"""
Repositorios de acceso a datos. Patrón thin: queries SQL + mapping a dicts.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from src.db.engine import FenixDB, get_db

logger = logging.getLogger(__name__)


# =================================================================
# JobRepository
# =================================================================

class JobRepository:
    def __init__(self, db: FenixDB | None = None):
        self.db = db or get_db()

    def create(self, job_id: str, nicho: str, zona: str = "",
               modelo: str = "", meta: int = 0, estrategia: str = "") -> str:
        try:
            self.db.execute(
                "INSERT INTO jobs (job_id, nicho, zona, modelo, meta, estrategia) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (job_id, nicho, zona, modelo, meta, estrategia),
            )
        except Exception:
            self.db.execute(
                "INSERT INTO jobs (job_id, nicho, zona, modelo, meta, estrategia) "
                "VALUES (:j, :n, :z, :m, :me, :e) ON CONFLICT DO NOTHING",
                {"j": job_id, "n": nicho, "z": zona, "m": modelo,
                 "me": meta, "e": estrategia},
            )
        return job_id

    def finish(self, job_id: str, stats: dict, errors: list,
               exports: dict, duration_sec: float | None = None) -> None:
        self.db.execute(
            "UPDATE jobs SET finished_at = CURRENT_TIMESTAMP, "
            "n_candidatos = ?, n_new = ?, n_updated = ?, n_duplicates = ?, "
            "n_completo = ?, duration_sec = ?, "
            "errors_json = ?, stats_json = ?, exports_json = ? "
            "WHERE job_id = ?",
            (
                stats.get("scout", {}).get("n_post_dedup", 0),
                stats.get("dedup", {}).get("n_new", 0),
                stats.get("dedup", {}).get("n_updated", 0),
                stats.get("scout", {}).get("n_pre_dedup", 0)
                  - stats.get("scout", {}).get("n_post_dedup", 0),
                stats.get("profiler", {}).get("n_completo", 0),
                duration_sec,
                json.dumps(errors, ensure_ascii=False, default=str),
                json.dumps(stats, ensure_ascii=False, default=str),
                json.dumps(exports, ensure_ascii=False, default=str),
                job_id,
            ),
        )

    def get(self, job_id: str) -> dict | None:
        return self.db.fetch_one("SELECT * FROM jobs WHERE job_id = ?", (job_id,))

    def list_recent(self, limit: int = 20) -> list[dict]:
        return self.db.fetch_all(
            "SELECT job_id, nicho, zona, started_at, finished_at, n_new, n_completo, duration_sec "
            "FROM jobs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        )


# =================================================================
# CompanyRepository
# =================================================================

class CompanyRepository:
    def __init__(self, db: FenixDB | None = None):
        self.db = db or get_db()

    def get(self, company_id: int) -> dict | None:
        co = self.db.fetch_one("SELECT * FROM companies WHERE id = ?", (company_id,))
        if not co:
            return None
        co["contacts"] = self.db.fetch_all(
            "SELECT kind, value, is_personal, is_verified, verification_status, fuente "
            "FROM contacts WHERE company_id = ?",
            (company_id,),
        )
        return co

    def list(self, bucket: str | None = None, min_score: int = 0,
             estado: str | None = None, limit: int = 100,
             offset: int = 0, only_with_contact: bool = False) -> list[dict]:
        where = ["1=1"]
        params: list[Any] = []
        if bucket:
            where.append("bucket = ?")
            params.append(bucket)
        if min_score:
            where.append("score_data >= ?")
            params.append(min_score)
        if estado:
            where.append("UPPER(estado) = ?")
            params.append(estado.upper())
        if only_with_contact:
            where.append("id IN (SELECT company_id FROM contacts "
                         "WHERE kind IN ('email','phone','whatsapp'))")
        sql = (
            "SELECT id, razon_social, nombre_comercial, estado, municipio, "
            "  giro_descripcion, tamano, bucket, score_data, tipo_lead, times_seen, "
            "  first_seen_at, last_seen_at "
            f"FROM companies WHERE {' AND '.join(where)} "
            "ORDER BY score_data DESC, last_seen_at DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])
        return self.db.fetch_all(sql, tuple(params))

    def search(self, query: str, limit: int = 50) -> list[dict]:
        q = f"%{query.lower()}%"
        return self.db.fetch_all(
            "SELECT id, razon_social, nombre_comercial, estado, bucket, score_data "
            "FROM companies WHERE LOWER(razon_social) LIKE ? OR LOWER(nombre_comercial) LIKE ? "
            "ORDER BY score_data DESC LIMIT ?",
            (q, q, limit),
        )

    def update_scoring(self, company_id: int, score_data: int,
                       score_skydropx: int, score_sales: int, score_contact: int,
                       bucket: str, tipo_lead: str,
                       modelo_negocio: str = None, skydropx_plan: str = None) -> None:
        cols = ["score_data = ?", "score_skydropx = ?",
                "score_sales = ?", "score_contact = ?",
                "bucket = ?", "tipo_lead = ?"]
        params: list[Any] = [score_data, score_skydropx,
                             score_sales, score_contact, bucket, tipo_lead]
        if modelo_negocio:
            cols.append("modelo_negocio = ?")
            params.append(modelo_negocio)
        if skydropx_plan:
            cols.append("skydropx_plan = ?")
            params.append(skydropx_plan)
        params.append(company_id)
        self.db.execute(
            f"UPDATE companies SET {', '.join(cols)} WHERE id = ?",
            tuple(params),
        )

    def pending_enrichment(self, limit: int = 200,
                           require_website: bool = True) -> list[dict]:
        """Companies que tienen sitio web pero les falta email O teléfono."""
        sql = """
        SELECT c.id, c.razon_social, c.nombre_comercial,
               (SELECT value FROM contacts WHERE company_id=c.id AND kind='website' LIMIT 1) AS website,
               EXISTS(SELECT 1 FROM contacts WHERE company_id=c.id AND kind='email')  AS has_email,
               EXISTS(SELECT 1 FROM contacts WHERE company_id=c.id AND kind IN ('phone','whatsapp')) AS has_phone
        FROM companies c
        WHERE c.bucket != 'COMPLETO'
        """
        if require_website:
            sql += " AND EXISTS(SELECT 1 FROM contacts WHERE company_id=c.id AND kind='website')"
        sql += " ORDER BY c.score_data DESC LIMIT ?"
        return self.db.fetch_all(sql, (limit,))


# =================================================================
# ContactRepository
# =================================================================

class ContactRepository:
    def __init__(self, db: FenixDB | None = None):
        self.db = db or get_db()

    def mark_verified(self, contact_id: int, status: str) -> None:
        self.db.execute(
            "UPDATE contacts SET is_verified = ?, verification_status = ?, "
            "verified_at = CURRENT_TIMESTAMP WHERE id = ?",
            (1 if status in ("smtp_ok", "mx_ok") else 0, status, contact_id),
        )

    def unverified_emails(self, limit: int = 500) -> list[dict]:
        return self.db.fetch_all(
            "SELECT id, value, value_norm FROM contacts "
            "WHERE kind='email' AND is_verified=0 AND verification_status IS NULL "
            "LIMIT ?",
            (limit,),
        )


__all__ = ["JobRepository", "CompanyRepository", "ContactRepository"]
