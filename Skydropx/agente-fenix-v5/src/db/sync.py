"""
Sync layer: SQLite local ↔ Supabase remoto.

Estrategia DUAL-MODE:
  - SQLite local sigue siendo el "source of truth" durante corridas del pipeline
    (rápido, sin latencia de red, funciona offline)
  - Supabase es el "espejo remoto" para acceso desde múltiples lugares,
    backups, colaboración, dashboards Studio

Operaciones:
  push  : envía cambios locales → Supabase
  pull  : trae cambios remotos → SQLite local
  status: muestra diferencias entre ambas DBs

Política de sync:
  - PUSH es incremental por defecto: solo lo modificado desde último push
  - Usa `synced_at` para tracking
  - Upsert por fingerprint (companies), (company_id, kind, value_norm) para contacts
  - NO borra registros en remoto (sync aditivo)
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from src.db.engine import get_db
from src.db.supabase_client import (
    upsert_companies, upsert_contacts, upsert_jobs,
    log_sync, is_configured, healthcheck,
)

logger = logging.getLogger(__name__)

LAST_SYNC_FILE = Path("data/last_sync.json")


def _load_last_sync() -> dict:
    if not LAST_SYNC_FILE.exists():
        return {}
    try:
        return json.loads(LAST_SYNC_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_last_sync(data: dict) -> None:
    LAST_SYNC_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_SYNC_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ============================================================================
# Transformadores SQLite row → Supabase row
# ============================================================================

def _company_to_supabase(row: dict) -> dict:
    """Convierte fila de companies (SQLite) a payload Supabase."""
    metadata = row.get("metadata_json") or "{}"
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except Exception:  # noqa: BLE001
            metadata = {}
    out = {
        "fingerprint": row["fingerprint"],
        "razon_social": row.get("razon_social"),
        "nombre_comercial": row.get("nombre_comercial"),
        "rfc": row.get("rfc"),
        "estado": row.get("estado"),
        "municipio": row.get("municipio"),
        "colonia": row.get("colonia"),
        "cp": row.get("cp"),
        "direccion": row.get("direccion"),
        "scian": row.get("scian"),
        "giro_descripcion": row.get("giro_descripcion"),
        "tamano": row.get("tamano"),
        "modelo_negocio": row.get("modelo_negocio"),
        "skydropx_plan": row.get("skydropx_plan"),
        "longitud": row.get("longitud"),
        "latitud": row.get("latitud"),
        "score_data": row.get("score_data", 0),
        "score_skydropx": row.get("score_skydropx", 0),
        "score_sales": row.get("score_sales", 0),
        "score_contact": row.get("score_contact", 0),
        "bucket": row.get("bucket", "RAW"),
        "tipo_lead": row.get("tipo_lead", "frio"),
        "times_seen": row.get("times_seen", 1),
        "local_id": row["id"],
        "first_seen_at": row.get("first_seen_at"),
        "last_seen_at": row.get("last_seen_at"),
        "metadata": metadata,
        "source_system": "fenix_local",
    }
    # Remover None para que Supabase use defaults
    return {k: v for k, v in out.items() if v is not None}


def _contact_to_supabase(row: dict, local_to_remote_company_id: dict) -> dict | None:
    """Convierte fila de contacts. Necesita el mapa local_id → remote_id de companies."""
    remote_company_id = local_to_remote_company_id.get(row["company_id"])
    if not remote_company_id:
        return None  # company no sincronizada aún
    return {
        "company_id": remote_company_id,
        "kind": row["kind"],
        "value": row["value"],
        "value_norm": row["value_norm"],
        "is_primary": row.get("is_primary", 0),
        "is_personal": row.get("is_personal", 0),
        "is_verified": row.get("is_verified", 0),
        "verification_status": row.get("verification_status"),
        "fuente": row.get("fuente"),
    }


def _job_to_supabase(row: dict) -> dict:
    """Convierte fila de jobs."""
    return {
        "job_id": row["job_id"],
        "nicho": row.get("nicho"),
        "zona": row.get("zona"),
        "modelo": row.get("modelo"),
        "meta": row.get("meta"),
        "estrategia": row.get("estrategia"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "n_candidatos": row.get("n_candidatos", 0),
        "n_new": row.get("n_new", 0),
        "n_updated": row.get("n_updated", 0),
        "n_duplicates": row.get("n_duplicates", 0),
        "n_completo": row.get("n_completo", 0),
        "duration_sec": row.get("duration_sec"),
        "errors": json.loads(row.get("errors_json") or "[]"),
        "stats": json.loads(row.get("stats_json") or "{}"),
        "exports": json.loads(row.get("exports_json") or "{}"),
        "source_system": "fenix_local",
    }


# ============================================================================
# PUSH: SQLite local → Supabase
# ============================================================================

def push_companies(incremental: bool = True, limit: int | None = None) -> dict:
    """Sube companies de SQLite a Supabase."""
    db = get_db()
    last_sync = _load_last_sync()
    where = "1=1"
    params: tuple = ()
    if incremental and last_sync.get("companies_pushed_at"):
        where = "last_seen_at >= ?"
        params = (last_sync["companies_pushed_at"],)
    sql = f"SELECT * FROM companies WHERE {where} ORDER BY id"
    if limit:
        sql += f" LIMIT {limit}"
    rows = db.fetch_all(sql, params)
    if not rows:
        return {"pushed": 0, "total_local": 0, "incremental": incremental}

    t0 = time.time()
    payloads = [_company_to_supabase(r) for r in rows]
    result = upsert_companies(payloads)
    duration = round(time.time() - t0, 2)

    log_sync("push", "fenix_companies", result, duration)
    last_sync["companies_pushed_at"] = datetime.utcnow().isoformat()
    last_sync["companies_last_count"] = result["ok"]
    _save_last_sync(last_sync)

    return {
        "pushed": result["ok"],
        "errors": result["errors"],
        "total_local": len(rows),
        "duration_sec": duration,
        "incremental": incremental,
        "last_error": result.get("last_error"),
    }


def push_contacts(incremental: bool = True, limit: int | None = None) -> dict:
    """
    Sube contacts. Requiere que companies ya estén sincronizadas
    (necesita el mapping local_id → remote_id).
    """
    from src.db.supabase_client import get_sync_client
    db = get_db()
    last_sync = _load_last_sync()

    # 1) Mapping local_id ↔ remote_id (vía fingerprint)
    fingerprints = [r["fingerprint"] for r in db.fetch_all(
        "SELECT fingerprint FROM companies"
    )]
    if not fingerprints:
        return {"pushed": 0, "reason": "no companies en SQLite"}

    sb = get_sync_client()
    remote = sb.table("fenix_companies").select("id,local_id,fingerprint").execute()
    local_to_remote = {r["local_id"]: r["id"] for r in (remote.data or []) if r.get("local_id")}

    if not local_to_remote:
        return {
            "pushed": 0,
            "reason": "Companies no sincronizadas a Supabase. Corre primero: fenix sync push companies",
        }

    # 2) Cargar contacts
    where = "1=1"
    params: tuple = ()
    if incremental and last_sync.get("contacts_pushed_at"):
        where = "found_at >= ?"
        params = (last_sync["contacts_pushed_at"],)
    sql = f"SELECT * FROM contacts WHERE {where} ORDER BY id"
    if limit:
        sql += f" LIMIT {limit}"
    rows = db.fetch_all(sql, params)

    payloads = []
    skipped = 0
    for r in rows:
        p = _contact_to_supabase(r, local_to_remote)
        if p is None:
            skipped += 1
        else:
            payloads.append(p)

    if not payloads:
        return {"pushed": 0, "skipped": skipped, "total_local": len(rows)}

    t0 = time.time()
    result = upsert_contacts(payloads)
    duration = round(time.time() - t0, 2)

    log_sync("push", "fenix_contacts", result, duration)
    last_sync["contacts_pushed_at"] = datetime.utcnow().isoformat()
    _save_last_sync(last_sync)

    return {
        "pushed": result["ok"],
        "errors": result["errors"],
        "skipped": skipped,
        "total_local": len(rows),
        "duration_sec": duration,
        "incremental": incremental,
        "last_error": result.get("last_error"),
    }


def push_jobs(incremental: bool = True, limit: int | None = None) -> dict:
    """Sube jobs de SQLite a Supabase."""
    db = get_db()
    last_sync = _load_last_sync()
    where = "1=1"
    params: tuple = ()
    if incremental and last_sync.get("jobs_pushed_at"):
        where = "started_at >= ?"
        params = (last_sync["jobs_pushed_at"],)
    sql = f"SELECT * FROM jobs WHERE {where} ORDER BY started_at"
    if limit:
        sql += f" LIMIT {limit}"
    rows = db.fetch_all(sql, params)
    if not rows:
        return {"pushed": 0, "total_local": 0}

    t0 = time.time()
    payloads = [_job_to_supabase(r) for r in rows]
    result = upsert_jobs(payloads)
    duration = round(time.time() - t0, 2)

    log_sync("push", "fenix_jobs", result, duration)
    last_sync["jobs_pushed_at"] = datetime.utcnow().isoformat()
    _save_last_sync(last_sync)

    return {
        "pushed": result["ok"],
        "errors": result["errors"],
        "total_local": len(rows),
        "duration_sec": duration,
        "last_error": result.get("last_error"),
    }


def push_all(incremental: bool = True) -> dict:
    """Push todo en orden correcto: companies → contacts → jobs."""
    out = {
        "companies": push_companies(incremental=incremental),
        "contacts": push_contacts(incremental=incremental),
        "jobs": push_jobs(incremental=incremental),
    }
    return out


# ============================================================================
# STATUS: comparación local vs remoto
# ============================================================================

def status() -> dict:
    """Compara contadores de SQLite vs Supabase."""
    db = get_db()
    local = {
        "companies": db.fetch_value("SELECT COUNT(*) FROM companies") or 0,
        "contacts": db.fetch_value("SELECT COUNT(*) FROM contacts") or 0,
        "jobs": db.fetch_value("SELECT COUNT(*) FROM jobs") or 0,
    }

    try:
        from src.db.supabase_client import stats as sb_stats
        remote = sb_stats()
    except Exception as e:  # noqa: BLE001
        return {
            "local": local,
            "remote": {"error": str(e)[:120]},
            "supabase_ok": False,
        }

    last_sync = _load_last_sync()
    return {
        "local": local,
        "remote": {
            "fenix_companies": remote.get("fenix_companies", 0),
            "fenix_contacts": remote.get("fenix_contacts", 0),
            "fenix_jobs": remote.get("fenix_jobs", 0),
        },
        "diff": {
            "companies_to_push": max(0, local["companies"] - (remote.get("fenix_companies") or 0)
                                       if isinstance(remote.get("fenix_companies"), int) else 0),
            "contacts_to_push": max(0, local["contacts"] - (remote.get("fenix_contacts") or 0)
                                       if isinstance(remote.get("fenix_contacts"), int) else 0),
            "jobs_to_push": max(0, local["jobs"] - (remote.get("fenix_jobs") or 0)
                                   if isinstance(remote.get("fenix_jobs"), int) else 0),
        },
        "last_sync": last_sync,
        "supabase_ok": True,
    }


__all__ = [
    "push_companies", "push_contacts", "push_jobs", "push_all", "status",
]
