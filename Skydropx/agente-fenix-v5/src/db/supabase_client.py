"""
Cliente Supabase para Agente Fénix.

Singleton + helpers para upserts en lotes.
NO reemplaza a SQLite — coexiste como capa de sync remoto.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from src.core.config import settings

logger = logging.getLogger(__name__)

# Detección de la lib
try:
    from supabase import create_client, Client
    HAS_SUPABASE = True
except ImportError:
    HAS_SUPABASE = False
    Client = None  # type: ignore


class SupabaseError(Exception):
    """Errores propios de la integración."""


_default_client: "Client | None" = None


def get_sync_client() -> "Client":
    """
    Singleton del cliente Supabase.
    Lee SUPABASE_URL + SUPABASE_KEY (service_role) de .env
    """
    global _default_client
    if not HAS_SUPABASE:
        raise SupabaseError(
            "supabase-py no instalado. Corre: pip install supabase"
        )

    url = getattr(settings, "supabase_url", None) or os.environ.get("SUPABASE_URL")
    key = getattr(settings, "supabase_key", None) or os.environ.get("SUPABASE_KEY")

    if not url or not key:
        raise SupabaseError(
            "Falta SUPABASE_URL o SUPABASE_KEY en .env. "
            "Get them at: https://supabase.com/dashboard/project/_/settings/api"
        )

    if _default_client is None:
        _default_client = create_client(url, key)
        logger.info("Supabase client inicializado: %s", url[:50])
    return _default_client


def is_configured() -> bool:
    """¿Hay credenciales de Supabase en el ambiente?"""
    url = getattr(settings, "supabase_url", None) or os.environ.get("SUPABASE_URL")
    key = getattr(settings, "supabase_key", None) or os.environ.get("SUPABASE_KEY")
    return bool(url and key and HAS_SUPABASE)


def healthcheck() -> dict:
    """Verifica conexión + tablas. Devuelve dict legible."""
    if not HAS_SUPABASE:
        return {
            "available": False,
            "reason": "supabase-py no instalado",
            "fix": "pip install supabase",
        }
    if not is_configured():
        return {
            "available": False,
            "reason": "SUPABASE_URL o SUPABASE_KEY no configurados en .env",
            "fix": "Agregar al .env: SUPABASE_URL=https://... y SUPABASE_KEY=eyJ...",
        }
    try:
        client = get_sync_client()
        from src.db.supabase_setup import check_tables
        tables = check_tables(client)
        all_exist = all(t["exists"] for t in tables.values())
        return {
            "available": True,
            "tables_ok": all_exist,
            "tables": tables,
            "fix": (
                None if all_exist
                else "Aplicar schema: copiar src/db/supabase_schema.sql a Supabase Studio SQL Editor"
            ),
        }
    except Exception as e:  # noqa: BLE001
        return {
            "available": False,
            "reason": f"Error conectando: {e}",
            "fix": "Verifica que SUPABASE_URL y SUPABASE_KEY son correctos",
        }


# ============================================================================
# Helpers de upsert/select
# ============================================================================

def upsert_companies(rows: list[dict], chunk_size: int = 100) -> dict:
    """
    Inserta o actualiza companies en Supabase usando upsert por fingerprint.
    Procesa en chunks para no exceder límites del API.

    Cada row debe tener al menos: fingerprint, razon_social.
    """
    client = get_sync_client()
    n_total = len(rows)
    n_ok = 0
    n_err = 0
    last_err = None
    for i in range(0, n_total, chunk_size):
        chunk = rows[i:i + chunk_size]
        try:
            client.table("fenix_companies").upsert(
                chunk, on_conflict="fingerprint",
            ).execute()
            n_ok += len(chunk)
        except Exception as e:  # noqa: BLE001
            n_err += len(chunk)
            last_err = str(e)[:200]
            logger.warning("upsert companies chunk %d falló: %s", i, e)
    return {"total": n_total, "ok": n_ok, "errors": n_err, "last_error": last_err}


def upsert_contacts(rows: list[dict], chunk_size: int = 200) -> dict:
    """Upsert de contactos. Conflict key = (company_id, kind, value_norm)."""
    client = get_sync_client()
    n_total = len(rows)
    n_ok = 0
    n_err = 0
    last_err = None
    for i in range(0, n_total, chunk_size):
        chunk = rows[i:i + chunk_size]
        try:
            client.table("fenix_contacts").upsert(
                chunk, on_conflict="company_id,kind,value_norm",
            ).execute()
            n_ok += len(chunk)
        except Exception as e:  # noqa: BLE001
            n_err += len(chunk)
            last_err = str(e)[:200]
            logger.warning("upsert contacts chunk %d falló: %s", i, e)
    return {"total": n_total, "ok": n_ok, "errors": n_err, "last_error": last_err}


def upsert_jobs(rows: list[dict], chunk_size: int = 100) -> dict:
    """Upsert de jobs. Conflict key = job_id (PK)."""
    client = get_sync_client()
    n_total = len(rows)
    n_ok = 0
    n_err = 0
    last_err = None
    for i in range(0, n_total, chunk_size):
        chunk = rows[i:i + chunk_size]
        try:
            client.table("fenix_jobs").upsert(
                chunk, on_conflict="job_id",
            ).execute()
            n_ok += len(chunk)
        except Exception as e:  # noqa: BLE001
            n_err += len(chunk)
            last_err = str(e)[:200]
    return {"total": n_total, "ok": n_ok, "errors": n_err, "last_error": last_err}


def log_sync(direction: str, table_name: str, stats: dict,
              duration_sec: float = 0.0) -> None:
    """Registra una operación de sync en fenix_sync_log."""
    try:
        client = get_sync_client()
        client.table("fenix_sync_log").insert({
            "direction": direction,
            "table_name": table_name,
            "n_inserted": stats.get("ok", 0),
            "n_errors": stats.get("errors", 0),
            "duration_sec": duration_sec,
            "error_sample": stats.get("last_error", ""),
            "source_system": "fenix_local",
        }).execute()
    except Exception as e:  # noqa: BLE001
        logger.debug("log_sync err: %s", e)


def query_companies(
    limit: int = 100,
    bucket: str | None = None,
    estado: str | None = None,
) -> list[dict]:
    """Query de companies desde Supabase (útil para uso desde otros sistemas)."""
    client = get_sync_client()
    q = client.table("fenix_companies").select("*")
    if bucket:
        q = q.eq("bucket", bucket)
    if estado:
        q = q.ilike("estado", f"%{estado}%")
    q = q.order("score_data", desc=True).limit(limit)
    r = q.execute()
    return r.data or []


def stats() -> dict:
    """Stats globales desde Supabase."""
    client = get_sync_client()
    out = {}
    for table in ("fenix_companies", "fenix_contacts", "fenix_jobs"):
        try:
            r = client.table(table).select("*", count="exact").limit(0).execute()
            out[table] = getattr(r, "count", 0) or 0
        except Exception as e:  # noqa: BLE001
            out[table] = f"err: {str(e)[:80]}"
    return out


__all__ = [
    "get_sync_client", "is_configured", "healthcheck",
    "upsert_companies", "upsert_contacts", "upsert_jobs",
    "log_sync", "query_companies", "stats",
    "HAS_SUPABASE", "SupabaseError",
]
