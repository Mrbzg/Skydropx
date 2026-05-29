"""
Agente DEEP ENRICH — enrichment OSINT profundo selectivo.

Se ejecuta DESPUÉS del verifier/profiler, SOLO en leads que ya pasaron filtros:
- bucket=COMPLETO  (DATA_SCORE ≥ 70)
- bucket=PARCIAL   (al menos 1 contacto)
- O un subset explícito (top-N por priority_score)

Para cada lead seleccionado, aplica las herramientas OSINT externas (opcionales):
- Holehe en el email → confirma persona activa en otros servicios
- Maigret sobre nombre persona → busca perfiles sociales LinkedIn/IG/Twitter
- pagodo sobre dominio → descubre URLs/PDFs públicos que Google indexa
- PhoneInfoga sobre teléfono → carrier + tipo línea adicional

Cada herramienta respeta su Budget compartido (data/budgets.json).
Si una herramienta no está instalada → se omite silenciosamente.

POR QUÉ es un agente separado:
- Las herramientas OSINT son LENTAS (5-30s por lead) y consumen rate-limits externos
- Aplicarlas a 100K leads sería catastrófico (~833 horas + bans)
- Aplicarlas a top ~5K leads (READY) toma ~7-40 horas y enriquece justo a los que importan
"""
from __future__ import annotations

import logging
import time
from typing import Any

from src.core.models import Lead, PipelineState
from src.db.engine import get_db
from src.db.repositories import CompanyRepository

logger = logging.getLogger(__name__)


def agent_deep_enrich(
    state: PipelineState,
    max_leads: int = 200,
    target_buckets: tuple[str, ...] = ("COMPLETO", "PARCIAL"),
    tools: tuple[str, ...] = ("holehe", "maigret", "phoneinfoga"),
    pagodo_per_domain_limit: int = 5,
) -> PipelineState:
    """
    Enriquece leads ya filtrados con OSINT profundo.

    Args:
        max_leads:   máximo de leads a procesar (default 200, evita corridas eternas)
        target_buckets: solo procesa leads con bucket en esta lista
        tools:       cuáles de las 4 herramientas usar
        pagodo_per_domain_limit: 5 dorks por dominio (Google rate-limita)
    """
    state.fase_actual = "deep_enrich"

    # Selección: solo leads de target_buckets, ordenados por score
    targets = [l for l in state.leads_enriched if l._bucket in target_buckets][:max_leads]
    if not targets:
        logger.info("DeepEnrich: 0 leads en buckets %s, saltando", target_buckets)
        state.stats["deep_enrich"] = {"n_targets": 0, "skipped": True}
        return state

    logger.info("DeepEnrich: procesando %s leads (de %s totales) con herramientas: %s",
                len(targets), len(state.leads_enriched), tools)

    co_repo = CompanyRepository()
    n_enriched = 0
    n_holehe_hits = 0
    n_maigret_hits = 0
    n_pagodo_hits = 0
    n_phoneinfoga_hits = 0
    n_budget_skipped = 0
    domains_done: set[str] = set()  # no repetir pagodo por dominio

    # Imports defensivos (las tools pueden no estar instaladas)
    holehe_fn = maigret_fn = pagodo_fn = phoneinfoga_fn = None
    if "holehe" in tools:
        from src.extraction.osint_deep import holehe_check as holehe_fn
    if "maigret" in tools:
        from src.extraction.osint_deep import maigret_search as maigret_fn
    if "pagodo" in tools:
        from src.extraction.osint_deep import pagodo_search as pagodo_fn
    if "phoneinfoga" in tools:
        from src.extraction.osint_deep import phoneinfoga_scan as phoneinfoga_fn

    t0 = time.time()
    for i, lead in enumerate(targets, 1):
        lead_enrich_data: dict[str, Any] = {}

        # 1. Holehe: ¿este email es persona activa?
        if holehe_fn and lead.email and lead.email != "DATO_NO_VERIFICABLE":
            try:
                r = holehe_fn(lead.email, only_used=True)
                if r.available and not r.error:
                    lead_enrich_data["holehe_sites"] = r.sites_registered
                    lead_enrich_data["holehe_is_active_persona"] = r.is_active_persona
                    if r.sites_registered:
                        n_holehe_hits += 1
                elif "budget" in (r.error or ""):
                    n_budget_skipped += 1
            except Exception as e:  # noqa: BLE001
                logger.debug("Holehe err en lead %s: %s", lead.lead_id, e)

        # 2. Maigret: buscar perfiles del nombre/persona
        if maigret_fn and lead.nombre and lead.nombre != "(sin nombre)":
            # Limpiar nombre: solo primera persona/marca para búsqueda
            search_name = lead.nombre.lower().split(",")[0].split(" ")[0]
            if len(search_name) >= 4:
                try:
                    r = maigret_fn(search_name, top_sites_only=True)
                    if r.available and r.top_profiles:
                        lead_enrich_data["maigret_top_profiles"] = r.top_profiles
                        n_maigret_hits += 1
                        # Si encontramos LinkedIn, lo guardamos directo en el Lead
                        for site, url in r.top_profiles.items():
                            if "linkedin" in site and not lead.linkedin:
                                lead.linkedin = url
                            elif "instagram" in site and not lead.instagram:
                                lead.instagram = url
                            elif "facebook" in site and not lead.facebook:
                                lead.facebook = url
                    elif r.error and "budget" in r.error:
                        n_budget_skipped += 1
                except Exception as e:  # noqa: BLE001
                    logger.debug("Maigret err en lead %s: %s", lead.lead_id, e)

        # 3. PhoneInfoga: enriquece carrier/línea (complementa phonenumbers)
        if phoneinfoga_fn and lead.telefono and lead.telefono != "DATO_NO_VERIFICABLE":
            try:
                r = phoneinfoga_fn(lead.telefono)
                if r.available and not r.error:
                    if r.carrier:
                        lead_enrich_data["phoneinfoga_carrier"] = r.carrier
                        n_phoneinfoga_hits += 1
                elif "budget" in (r.error or ""):
                    n_budget_skipped += 1
            except Exception as e:  # noqa: BLE001
                logger.debug("PhoneInfoga err en lead %s: %s", lead.lead_id, e)

        # 4. Pagodo: solo si tenemos dominio Y no lo hicimos antes
        if pagodo_fn:
            domain = None
            for raw in lead._raw_records:
                w = (raw.get("sitio_web") or "")
                if w:
                    domain = w.replace("https://", "").replace("http://", "").rstrip("/").split("/")[0]
                    break
            if domain and domain not in domains_done:
                domains_done.add(domain)
                try:
                    r = pagodo_fn(domain, max_dorks=pagodo_per_domain_limit)
                    if r.available and r.urls_found:
                        lead_enrich_data["pagodo_urls"] = r.urls_found[:20]
                        n_pagodo_hits += 1
                    elif "budget" in (r.error or ""):
                        n_budget_skipped += 1
                except Exception as e:  # noqa: BLE001
                    logger.debug("Pagodo err en lead %s: %s", lead.lead_id, e)

        # Persistir enrichment en metadata + DB
        if lead_enrich_data:
            for raw in lead._raw_records:
                raw.setdefault("metadata", {}).update(lead_enrich_data)
            try:
                row = get_db().fetch_one(
                    "SELECT id, metadata_json FROM companies WHERE fingerprint = ? LIMIT 1",
                    (lead._raw_records[0].get("source", "") + ":" + (lead.email or lead.telefono or ""),)
                )
                # En caso de no encontrar por fingerprint, intentar por email/tel
                if not row and lead.email:
                    row = get_db().fetch_one(
                        "SELECT c.id, c.metadata_json FROM companies c "
                        "JOIN contacts ct ON c.id = ct.company_id "
                        "WHERE ct.kind='email' AND ct.value_norm = ? LIMIT 1",
                        (lead.email.lower(),)
                    )
                if row:
                    import json
                    existing = {}
                    try:
                        existing = json.loads(row["metadata_json"] or "{}")
                    except Exception:  # noqa: BLE001
                        pass
                    existing.update(lead_enrich_data)
                    get_db().execute(
                        "UPDATE companies SET metadata_json = ? WHERE id = ?",
                        (json.dumps(existing, ensure_ascii=False, default=str), row["id"]),
                    )
            except Exception as e:  # noqa: BLE001
                logger.debug("DeepEnrich DB update err: %s", e)
            n_enriched += 1

        if i % 10 == 0:
            elapsed = time.time() - t0
            logger.info("  [%s/%s] elapsed %.0fs, enriched=%s (holehe=%s maigret=%s pagodo=%s ph=%s) budget_skip=%s",
                        i, len(targets), elapsed, n_enriched,
                        n_holehe_hits, n_maigret_hits, n_pagodo_hits, n_phoneinfoga_hits,
                        n_budget_skipped)

    state.stats["deep_enrich"] = {
        "n_targets": len(targets),
        "n_enriched": n_enriched,
        "holehe_hits": n_holehe_hits,
        "maigret_hits": n_maigret_hits,
        "pagodo_hits": n_pagodo_hits,
        "phoneinfoga_hits": n_phoneinfoga_hits,
        "budget_skipped": n_budget_skipped,
        "duration_sec": round(time.time() - t0, 1),
        "tools_used": list(tools),
    }
    logger.info("DeepEnrich completado: %s leads enriquecidos en %.1fs",
                n_enriched, time.time() - t0)
    return state


__all__ = ["agent_deep_enrich"]
