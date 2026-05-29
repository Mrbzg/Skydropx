"""
Retry Queue — gap #5.

Mantiene leads que NO se pudieron enriquecer en la corrida actual para
reintentar en futuras corridas. Cada lead tiene:
- razón del intento fallido (no_email, no_phone, no_domain, network_error)
- intentos previos (con timestamps)
- próximo intento sugerido (backoff exponencial)
- max_attempts antes de "darse por vencido"

Casos de uso:
1. Lead con web pero EmailInferencer no encontró email → reintentar en 24h
   por si la web cambia o el catch-all se activa
2. Lead sin web → DomainFinder no encontró → reintentar en 7 días
3. Lead bloqueado por Cloudflare → reintentar en 3 días con Patchright

NO se duplica con `companies.bucket=SIN_CONTACTO`. La queue es para LO QUE
PROCESAR DESPUÉS, no para guardar el estado actual.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from src.db.engine import get_db

logger = logging.getLogger(__name__)


RETRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS retry_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    target          TEXT NOT NULL,         -- 'find_domain' | 'infer_email' | 'crawl_web' | etc.
    reason          TEXT,                  -- 'no_email_in_web' | 'cloudflare_blocked' | etc.
    attempts        INTEGER DEFAULT 0,
    max_attempts    INTEGER DEFAULT 5,
    last_attempt_at TIMESTAMP,
    next_retry_at   TIMESTAMP NOT NULL,
    last_error      TEXT,
    payload_json    TEXT,                  -- contexto para el retry
    status          TEXT DEFAULT 'pending',-- pending|succeeded|exhausted|abandoned
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(company_id, target)
);

CREATE INDEX IF NOT EXISTS idx_retry_next ON retry_queue(next_retry_at);
CREATE INDEX IF NOT EXISTS idx_retry_status ON retry_queue(status);
CREATE INDEX IF NOT EXISTS idx_retry_target ON retry_queue(target);
"""


# Backoff por target (en horas)
RETRY_BACKOFF_HOURS = {
    "find_domain": [24, 72, 168, 336],          # 1d, 3d, 7d, 14d
    "infer_email": [12, 48, 168, 336],          # 12h, 2d, 7d, 14d
    "crawl_web": [3, 24, 168],                  # 3h, 1d, 7d
    "verify_smtp": [1, 6, 24],                  # 1h, 6h, 1d
}


@dataclass
class RetryEntry:
    id: int
    company_id: int
    target: str
    reason: str
    attempts: int
    max_attempts: int
    next_retry_at: str
    payload: dict
    status: str


class RetryQueue:
    def __init__(self, db=None):
        self.db = db or get_db()
        self._init_schema()

    def _init_schema(self) -> None:
        with self.db.connect() as conn:
            try:
                conn.executescript(RETRY_SCHEMA)
                conn.commit()
            except Exception as e:  # noqa: BLE001
                logger.debug("Retry schema init: %s", e)

    def enqueue(
        self,
        company_id: int,
        target: str,
        reason: str,
        payload: dict | None = None,
        max_attempts: int = 5,
    ) -> bool:
        """Agrega una entrada a la cola. Si ya existe, no la duplica."""
        next_at = self._compute_next_retry(target, attempts=0)
        try:
            self.db.execute(
                "INSERT OR IGNORE INTO retry_queue "
                "(company_id, target, reason, max_attempts, "
                " next_retry_at, payload_json, status) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
                (company_id, target, reason, max_attempts,
                 next_at.isoformat(),
                 json.dumps(payload or {}, ensure_ascii=False)),
            )
            return True
        except Exception as e:  # noqa: BLE001
            logger.debug("Retry enqueue err: %s", e)
            return False

    def get_due(self, target: str | None = None,
                 limit: int = 100) -> list[RetryEntry]:
        """Devuelve entradas que ya vencieron (listas para reintentar)."""
        now_iso = datetime.now().isoformat()
        where = ["status='pending'", "next_retry_at <= ?"]
        params: list[Any] = [now_iso]
        if target:
            where.append("target = ?")
            params.append(target)
        params.append(limit)
        rows = self.db.fetch_all(
            f"SELECT * FROM retry_queue WHERE {' AND '.join(where)} "
            f"ORDER BY next_retry_at LIMIT ?",
            tuple(params),
        )
        return [self._row_to_entry(r) for r in rows]

    def mark_success(self, entry_id: int) -> None:
        self.db.execute(
            "UPDATE retry_queue SET status='succeeded', "
            "last_attempt_at=CURRENT_TIMESTAMP WHERE id=?",
            (entry_id,),
        )

    def mark_failure(self, entry_id: int, error: str = "") -> None:
        """Incrementa intentos y reprograma con backoff."""
        row = self.db.fetch_one(
            "SELECT target, attempts, max_attempts FROM retry_queue WHERE id=?",
            (entry_id,),
        )
        if not row:
            return
        new_attempts = (row["attempts"] or 0) + 1
        if new_attempts >= row["max_attempts"]:
            status = "exhausted"
            next_at = datetime.now() + timedelta(days=365)
        else:
            status = "pending"
            next_at = self._compute_next_retry(row["target"], new_attempts)

        self.db.execute(
            "UPDATE retry_queue SET attempts=?, last_attempt_at=CURRENT_TIMESTAMP, "
            "last_error=?, next_retry_at=?, status=? WHERE id=?",
            (new_attempts, error[:500], next_at.isoformat(), status, entry_id),
        )

    def stats(self) -> dict:
        return {
            "total":      self.db.fetch_value("SELECT COUNT(*) FROM retry_queue") or 0,
            "pending":    self.db.fetch_value("SELECT COUNT(*) FROM retry_queue WHERE status='pending'") or 0,
            "due_now":    self.db.fetch_value(
                "SELECT COUNT(*) FROM retry_queue WHERE status='pending' AND next_retry_at <= ?",
                (datetime.now().isoformat(),)) or 0,
            "succeeded":  self.db.fetch_value("SELECT COUNT(*) FROM retry_queue WHERE status='succeeded'") or 0,
            "exhausted":  self.db.fetch_value("SELECT COUNT(*) FROM retry_queue WHERE status='exhausted'") or 0,
            "by_target":  self.db.fetch_all(
                "SELECT target, status, COUNT(*) AS n FROM retry_queue "
                "GROUP BY target, status ORDER BY n DESC LIMIT 20"
            ) or [],
        }

    @staticmethod
    def _compute_next_retry(target: str, attempts: int) -> datetime:
        backoff = RETRY_BACKOFF_HOURS.get(target, [24, 72, 168])
        idx = min(attempts, len(backoff) - 1)
        return datetime.now() + timedelta(hours=backoff[idx])

    @staticmethod
    def _row_to_entry(row: dict) -> RetryEntry:
        try:
            payload = json.loads(row.get("payload_json") or "{}")
        except Exception:  # noqa: BLE001
            payload = {}
        return RetryEntry(
            id=row["id"], company_id=row["company_id"],
            target=row["target"], reason=row["reason"] or "",
            attempts=row["attempts"] or 0,
            max_attempts=row["max_attempts"] or 5,
            next_retry_at=row["next_retry_at"],
            payload=payload, status=row["status"],
        )


__all__ = ["RetryQueue", "RetryEntry", "RETRY_BACKOFF_HOURS"]
