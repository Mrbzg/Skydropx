"""
Audit + reportes avanzados de deduplicación.

Complementa a `src/db/deduper.py` con:
- Reporte de duplicados detectados y consolidados
- Análisis cross-corrida (qué fuentes están dando los mismos leads)
- Stats por fuente: efectividad, overlap, fingerprints únicos aportados
- Detección de leads "huérfanos" sin enriquecimiento
- Sugerencias automáticas para la próxima campaña (evitar solapamiento)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from src.db.engine import get_db, FenixDB

logger = logging.getLogger(__name__)


# ---------------- Audit reports ----------------

@dataclass
class DedupAuditReport:
    fecha_reporte: str
    total_companies: int = 0
    total_raw_findings: int = 0
    overlap_ratio: float = 0.0      # raw_findings / companies — más alto = más dedup
    avg_sources_per_company: float = 0.0
    sources_breakdown: list[dict] = field(default_factory=list)
    leads_huerfanos: int = 0        # companies sin email NI teléfono NI website
    leads_sin_enriquecer: int = 0   # con website pero sin email
    top_companies_por_overlap: list[dict] = field(default_factory=list)
    cross_source_winners: list[dict] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


def audit_dedup(db: FenixDB | None = None) -> DedupAuditReport:
    """Reporte completo del estado de dedup en la DB."""
    db = db or get_db()
    rpt = DedupAuditReport(fecha_reporte=datetime.now().isoformat())

    rpt.total_companies = db.fetch_value("SELECT COUNT(*) FROM companies") or 0
    rpt.total_raw_findings = db.fetch_value("SELECT COUNT(*) FROM raw_findings") or 0

    if rpt.total_companies > 0:
        rpt.overlap_ratio = round(rpt.total_raw_findings / rpt.total_companies, 2)

    # Avg sources por company
    avg = db.fetch_value("""
        SELECT AVG(n_sources) FROM (
            SELECT company_id, COUNT(DISTINCT source) AS n_sources
            FROM raw_findings
            GROUP BY company_id
        )
    """)
    rpt.avg_sources_per_company = round(float(avg or 0), 2)

    # Breakdown por fuente
    rpt.sources_breakdown = db.fetch_all("""
        SELECT
            source,
            COUNT(*) AS n_findings,
            COUNT(DISTINCT company_id) AS n_companies,
            ROUND(COUNT(DISTINCT company_id) * 1.0 / COUNT(*), 2) AS unique_ratio
        FROM raw_findings
        GROUP BY source
        ORDER BY n_findings DESC
    """) or []

    # Huérfanos (sin ningún contacto útil)
    rpt.leads_huerfanos = db.fetch_value("""
        SELECT COUNT(*) FROM companies
        WHERE id NOT IN (
            SELECT DISTINCT company_id FROM contacts
            WHERE kind IN ('email','phone','whatsapp','website')
        )
    """) or 0

    # Sin enriquecer (website sí pero sin email)
    rpt.leads_sin_enriquecer = db.fetch_value("""
        SELECT COUNT(*) FROM companies c
        WHERE EXISTS(SELECT 1 FROM contacts WHERE company_id=c.id AND kind='website')
          AND NOT EXISTS(SELECT 1 FROM contacts WHERE company_id=c.id AND kind='email')
    """) or 0

    # Top companies por overlap (las que más veces se han redescubierto)
    rpt.top_companies_por_overlap = db.fetch_all("""
        SELECT
            c.id, c.razon_social, c.estado, c.times_seen,
            (SELECT COUNT(DISTINCT source) FROM raw_findings WHERE company_id=c.id) AS n_sources
        FROM companies c
        ORDER BY c.times_seen DESC, n_sources DESC
        LIMIT 10
    """) or []

    # Cross-source winners (companies descubiertas por >2 sources distintos)
    rpt.cross_source_winners = db.fetch_all("""
        SELECT
            c.id, c.razon_social, c.estado, c.bucket,
            COUNT(DISTINCT rf.source) AS n_sources,
            GROUP_CONCAT(DISTINCT rf.source) AS sources_list
        FROM companies c
        JOIN raw_findings rf ON rf.company_id = c.id
        GROUP BY c.id
        HAVING n_sources >= 2
        ORDER BY n_sources DESC
        LIMIT 15
    """) or []

    # Sugerencias automáticas
    if rpt.overlap_ratio > 2.0:
        rpt.suggestions.append(
            f"Overlap ratio {rpt.overlap_ratio} ALTO → muchas corridas redundantes. "
            "Cambia nicho/estado/SCIAN en la próxima campaña."
        )
    if rpt.leads_huerfanos / max(1, rpt.total_companies) > 0.3:
        rpt.suggestions.append(
            f"{rpt.leads_huerfanos} leads huérfanos (>30%). "
            "Habilita Hunter con --enrich-max alto en próximo run."
        )
    if rpt.leads_sin_enriquecer > 100:
        rpt.suggestions.append(
            f"{rpt.leads_sin_enriquecer} leads tienen web pero NO email. "
            "Corre `fenix run --enrich-max 500` (solo Hunter, sin Scout)."
        )
    top_source = rpt.sources_breakdown[0] if rpt.sources_breakdown else None
    if top_source and top_source["n_findings"] / max(1, rpt.total_raw_findings) > 0.7:
        rpt.suggestions.append(
            f"Fuente '{top_source['source']}' domina (>70%). "
            "Diversifica con ML/dorks/cámaras para reducir sesgo."
        )

    return rpt


# ---------------- Scheduler de dimensiones (evita solapamiento) ----------------

@dataclass
class CampaignDimension:
    """Una dimensión de búsqueda. Combinables ortogonalmente."""
    nicho: str = ""
    estado: str = ""
    scian_prefix: str = ""
    estrato: str = ""
    fuente: str = ""

    def signature(self) -> str:
        parts = [self.nicho, self.estado, self.scian_prefix, self.estrato, self.fuente]
        return "|".join(p or "_" for p in parts)


def get_unused_dimensions(
    nichos_disponibles: list[str],
    estados_disponibles: list[str] | None = None,
    estratos_disponibles: list[str] | None = None,
    fuentes_disponibles: list[str] | None = None,
    days_lookback: int = 30,
    max_suggestions: int = 10,
    db: FenixDB | None = None,
) -> list[CampaignDimension]:
    """
    Sugiere combinaciones nicho × estado × fuente que NO se han corrido en los últimos N días.
    Útil para diversificar campañas y evitar redundancia.
    """
    db = db or get_db()
    estados_disponibles = estados_disponibles or [
        "09", "14", "15", "19", "11", "21", "22", "26", "08", "07", "30",
    ]
    estratos_disponibles = estratos_disponibles or ["1", "2", "3", "4", "5"]
    fuentes_disponibles = fuentes_disponibles or [
        "denue", "mercadolibre", "dorks_envios_mx", "camara_amvo",
    ]

    # Obtener combos YA corridos recientemente
    since = (datetime.now() - timedelta(days=days_lookback)).isoformat()
    recent_jobs = db.fetch_all(
        "SELECT nicho, zona FROM jobs WHERE started_at >= ?",
        (since,),
    )
    recent_sigs: set[str] = set()
    for j in recent_jobs:
        for fuente in fuentes_disponibles:
            for estrato in estratos_disponibles:
                sig = CampaignDimension(
                    nicho=(j["nicho"] or "").lower(),
                    estado=(j["zona"] or "").upper(),
                    estrato=estrato, fuente=fuente,
                ).signature()
                recent_sigs.add(sig)

    # Generar candidatos no usados
    suggestions: list[CampaignDimension] = []
    for nicho in nichos_disponibles:
        for estado in estados_disponibles:
            for estrato in estratos_disponibles:
                for fuente in fuentes_disponibles:
                    dim = CampaignDimension(
                        nicho=nicho, estado=estado,
                        estrato=estrato, fuente=fuente,
                    )
                    if dim.signature() not in recent_sigs:
                        suggestions.append(dim)
                        if len(suggestions) >= max_suggestions:
                            return suggestions

    return suggestions


# ---------------- DuckDB opcional ----------------

def _has_duckdb() -> bool:
    try:
        import duckdb  # noqa: F401
        return True
    except ImportError:
        return False


HAS_DUCKDB = _has_duckdb()


def analytics_query(sql: str, sqlite_path: str | None = None) -> list[dict]:
    """
    Ejecuta una query analítica usando DuckDB si está disponible (queries OLAP rápidas),
    sino fallback a SQLite estándar.

    DuckDB es 10-100x más rápido para agregaciones masivas sobre la DB Fénix.
    """
    if HAS_DUCKDB and sqlite_path:
        try:
            import duckdb
            con = duckdb.connect(":memory:")
            con.execute(f"ATTACH '{sqlite_path}' AS fenix (TYPE SQLITE)")
            # En DuckDB usar fenix.companies, fenix.contacts, etc.
            adapted_sql = sql.replace("FROM companies", "FROM fenix.companies").replace(
                "FROM contacts", "FROM fenix.contacts").replace(
                "FROM raw_findings", "FROM fenix.raw_findings").replace(
                "FROM jobs", "FROM fenix.jobs")
            result = con.execute(adapted_sql).fetchall()
            cols = [d[0] for d in con.description]
            return [dict(zip(cols, row)) for row in result]
        except Exception as e:  # noqa: BLE001
            logger.warning("DuckDB query falló, fallback a SQLite: %s", e)
    # Fallback a SQLite estándar
    return get_db().fetch_all(sql)


__all__ = [
    "DedupAuditReport", "audit_dedup",
    "CampaignDimension", "get_unused_dimensions",
    "analytics_query", "HAS_DUCKDB",
]
