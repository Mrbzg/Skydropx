"""
Setup helper para Supabase.

Verifica si las tablas existen y guía al usuario para crearlas si no.

Hay 2 formas de aplicar el schema:
  A. Manual (recomendado): copiar src/db/supabase_schema.sql al SQL Editor de Supabase Studio
  B. Programática: si tienes la postgres connection string, instalar psycopg2 y ejecutar directo
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_FILE = Path(__file__).parent / "supabase_schema.sql"

REQUIRED_TABLES = [
    "fenix_companies",
    "fenix_contacts",
    "fenix_jobs",
    "fenix_sync_log",
    "fenix_opt_outs",
]


def check_tables(supabase_client) -> dict:
    """Verifica qué tablas Fénix existen ya en Supabase."""
    status = {}
    for table in REQUIRED_TABLES:
        try:
            r = supabase_client.table(table).select("*", count="exact").limit(0).execute()
            status[table] = {
                "exists": True,
                "count": getattr(r, "count", 0) or 0,
            }
        except Exception as e:  # noqa: BLE001
            status[table] = {
                "exists": False,
                "error": str(e)[:120],
            }
    return status


def print_setup_instructions() -> None:
    """Imprime instrucciones manuales si las tablas no existen."""
    print("\n" + "=" * 72)
    print("  📋 SETUP MANUAL DE SUPABASE — 30 segundos")
    print("=" * 72)
    print(f"""
1. Abre el SQL Editor de Supabase:
   https://supabase.com/dashboard/project/_/sql/new
   (selecciona tu project)

2. Copia el contenido completo de:
   {SCHEMA_FILE}

3. Pega en el editor y haz click en "Run" (o Cmd/Ctrl+Enter)

4. Verifica con:
   python3 -c "from src.db.supabase_client import get_sync_client; \\
               from src.db.supabase_setup import check_tables; \\
               import json; \\
               print(json.dumps(check_tables(get_sync_client()), indent=2))"
""")
    print("=" * 72)


def try_apply_via_psycopg(connection_string: str) -> bool:
    """
    Opcional: aplica el schema directo via psycopg2.
    Requiere la connection_string de Supabase (Settings > Database > Connection string).
    """
    try:
        import psycopg2
    except ImportError:
        logger.warning("psycopg2 no instalado. Instala con: pip install psycopg2-binary")
        return False

    if not SCHEMA_FILE.exists():
        logger.error("Schema no encontrado: %s", SCHEMA_FILE)
        return False

    sql = SCHEMA_FILE.read_text(encoding="utf-8")
    try:
        conn = psycopg2.connect(connection_string)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.close()
        logger.info("✓ Schema aplicado exitosamente via psycopg2")
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("Error aplicando schema: %s", e)
        return False


__all__ = ["check_tables", "print_setup_instructions",
            "try_apply_via_psycopg", "REQUIRED_TABLES"]
