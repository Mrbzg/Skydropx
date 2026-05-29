"""
Engine de persistencia: SQLite (stdlib) + Supabase cloud opcional como sync layer.

Zero deps obligatorias: usa sqlite3 (stdlib) por default.
Para sync remoto a Supabase, usa src/db/supabase_client.py.
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from src.core.config import settings

logger = logging.getLogger(__name__)


SCHEMA_SQL = """
-- =================================================================
-- Fénix v5 — Schema de persistencia con dedup
-- =================================================================

CREATE TABLE IF NOT EXISTS companies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint     TEXT UNIQUE NOT NULL,           -- email|tel|dom|nombre+estado
    razon_social    TEXT,
    nombre_comercial TEXT,
    rfc             TEXT,
    estado          TEXT,
    municipio       TEXT,
    colonia         TEXT,
    cp              TEXT,
    direccion       TEXT,
    scian           TEXT,
    giro_descripcion TEXT,
    tamano          TEXT,                            -- Micro/Pequeña/Mediana/Grande
    modelo_negocio  TEXT,                            -- B2B/B2C/D2C/C2C/C2B
    skydropx_plan   TEXT,                            -- Starter/PyME/Enterprise
    longitud        REAL,
    latitud         REAL,
    score_data      INTEGER DEFAULT 0,
    score_skydropx  INTEGER DEFAULT 0,
    score_sales     INTEGER DEFAULT 0,
    score_contact   INTEGER DEFAULT 0,
    bucket          TEXT DEFAULT 'RAW',              -- COMPLETO/SOLO_EMAIL/SOLO_TEL/SIN_CONTACTO
    tipo_lead       TEXT DEFAULT 'frio',             -- caliente/frio
    first_seen_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    times_seen      INTEGER DEFAULT 1,
    metadata_json   TEXT
);

CREATE INDEX IF NOT EXISTS idx_companies_estado ON companies(estado);
CREATE INDEX IF NOT EXISTS idx_companies_scian ON companies(scian);
CREATE INDEX IF NOT EXISTS idx_companies_bucket ON companies(bucket);
CREATE INDEX IF NOT EXISTS idx_companies_score ON companies(score_data DESC);

-- Contactos múltiples por company (emails, tels, redes)
CREATE TABLE IF NOT EXISTS contacts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,                   -- email/phone/whatsapp/website/instagram/linkedin/facebook
    value           TEXT NOT NULL,
    value_norm      TEXT NOT NULL,                   -- forma normalizada para dedup
    is_primary      INTEGER DEFAULT 0,               -- 1 si es el principal
    is_personal     INTEGER DEFAULT 0,               -- 1 si email es de persona (no genérico)
    is_verified     INTEGER DEFAULT 0,               -- 1 si pasó MX+SMTP
    verification_status TEXT,                        -- 'syntax_ok','mx_ok','smtp_ok','invalid','disposable'
    verified_at     TIMESTAMP,
    fuente          TEXT,                            -- denue/dorks/maps/harvester/etc.
    found_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(company_id, kind, value_norm)
);

CREATE INDEX IF NOT EXISTS idx_contacts_value_norm ON contacts(value_norm);
CREATE INDEX IF NOT EXISTS idx_contacts_kind ON contacts(kind);
CREATE INDEX IF NOT EXISTS idx_contacts_company ON contacts(company_id);

-- Jobs (cada corrida del pipeline)
CREATE TABLE IF NOT EXISTS jobs (
    job_id          TEXT PRIMARY KEY,
    nicho           TEXT,
    zona            TEXT,
    modelo          TEXT,
    meta            INTEGER,
    estrategia      TEXT,
    started_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at     TIMESTAMP,
    n_candidatos    INTEGER DEFAULT 0,
    n_new           INTEGER DEFAULT 0,
    n_updated       INTEGER DEFAULT 0,
    n_duplicates    INTEGER DEFAULT 0,
    n_completo      INTEGER DEFAULT 0,
    duration_sec    REAL,
    errors_json     TEXT,
    stats_json      TEXT,
    exports_json    TEXT
);

-- Relación job ↔ company (qué jobs encontraron qué company)
CREATE TABLE IF NOT EXISTS job_companies (
    job_id          TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    company_id      INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    is_new          INTEGER DEFAULT 0,               -- 1 si esta company es nueva en este job
    seen_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (job_id, company_id)
);

-- Raw findings (trazabilidad de qué fuente trajo qué)
CREATE TABLE IF NOT EXISTS raw_findings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER REFERENCES companies(id) ON DELETE SET NULL,
    job_id          TEXT REFERENCES jobs(job_id) ON DELETE SET NULL,
    source          TEXT NOT NULL,                   -- denue/dorks_envios_mx/maps/etc.
    fingerprint     TEXT,
    payload_json    TEXT,
    found_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_findings_source ON raw_findings(source);
CREATE INDEX IF NOT EXISTS idx_findings_job ON raw_findings(job_id);

-- Opt-outs (LFPDPPP compliance)
CREATE TABLE IF NOT EXISTS opt_outs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    kind            TEXT NOT NULL,                   -- email|phone|company_id
    value_norm      TEXT NOT NULL,
    reason          TEXT,
    requested_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(kind, value_norm)
);

-- Source stats (para Self-Improver)
CREATE TABLE IF NOT EXISTS source_stats (
    source          TEXT PRIMARY KEY,
    runs            INTEGER DEFAULT 0,
    leads_total     INTEGER DEFAULT 0,
    leads_completo  INTEGER DEFAULT 0,
    last_run_at     TIMESTAMP,
    last_yield      INTEGER,
    avg_yield       REAL,
    success_rate    REAL DEFAULT 1.0
);
"""


class FenixDB:
    """Wrapper SQLite (stdlib). Sync a Supabase via src/db/supabase_client.py."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or settings.sqlite_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

    # ---------- Connections ----------

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Context manager: devuelve conexión SQLite."""
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
        finally:
            conn.close()

    # ---------- Init ----------

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)
            conn.commit()
        logger.info("✓ Schema inicializado en %s", self.db_path[:60])

    # ---------- Execute helpers ----------

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self.connect() as conn:
            conn.execute(sql, params)
            return conn

    def fetch_all(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def fetch_one(self, sql: str, params: tuple | dict = ()) -> dict | None:
        rows = self.fetch_all(sql, params)
        return rows[0] if rows else None

    def fetch_value(self, sql: str, params: tuple | dict = ()) -> Any:
        row = self.fetch_one(sql, params)
        return next(iter(row.values()), None) if row else None

    # ---------- Stats rápidas ----------

    def stats(self) -> dict:
        return {
            "companies": self.fetch_value("SELECT COUNT(*) AS n FROM companies"),
            "contacts": self.fetch_value("SELECT COUNT(*) AS n FROM contacts"),
            "jobs": self.fetch_value("SELECT COUNT(*) AS n FROM jobs"),
            "raw_findings": self.fetch_value("SELECT COUNT(*) AS n FROM raw_findings"),
            "by_bucket": self.fetch_all(
                "SELECT bucket, COUNT(*) AS n FROM companies GROUP BY bucket ORDER BY n DESC"
            ),
            "by_source": self.fetch_all(
                "SELECT source, COUNT(*) AS n FROM raw_findings GROUP BY source ORDER BY n DESC LIMIT 10"
            ),
            "top_estados": self.fetch_all(
                "SELECT estado, COUNT(*) AS n FROM companies WHERE estado IS NOT NULL "
                "GROUP BY estado ORDER BY n DESC LIMIT 10"
            ),
            "opt_outs": self.fetch_value("SELECT COUNT(*) AS n FROM opt_outs"),
            "verified_emails": self.fetch_value(
                "SELECT COUNT(*) AS n FROM contacts WHERE kind='email' AND is_verified=1"
            ),
        }


# Singleton
_default_db: FenixDB | None = None


def get_db() -> FenixDB:
    global _default_db
    if _default_db is None:
        _default_db = FenixDB()
    return _default_db


__all__ = ["FenixDB", "get_db", "HAS_SQLALCHEMY", "SCHEMA_SQL"]
