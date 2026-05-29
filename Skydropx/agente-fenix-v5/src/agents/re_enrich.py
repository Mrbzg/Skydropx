"""
Agente RE_ENRICH — segunda ronda inteligente de enriquecimiento.

Se ejecuta DESPUÉS del Hunter+Verifier+Persist, ANTES del Profiler.
Su job: rescatar leads BRONZE/SILVER y promoverlos a GOLD/PREMIUM.

Estrategia:
1. Para leads SIN sitio_web → DomainFinder (busca el dominio oficial)
2. Para leads CON sitio_web pero SIN email → EmailInferencer (contacto@dominio + SMTP)
3. Re-clasificar tier después de enrichment

Configuración via plan/CLI:
    --re-enrich-max 200          # cuántos rescatar (default 0 = skip)
    --re-enrich-find-domains 50  # cuántos sin web buscar dominio (lento)
    --re-enrich-infer-emails 100 # cuántos con web inferir email
    --re-enrich-verify-smtp false # SMTP probe (más lento, más preciso)

IMPACTO ESPERADO en una corrida de 1000 DENUE:
    Sin re_enrich: ~5% PREMIUM
    Con re_enrich: ~15-25% PREMIUM (3-5x mejora)
"""
from __future__ import annotations

import logging
import time
from typing import Any

from src.core.models import PipelineState, RawRecord
from src.db.engine import get_db
from src.db.deduper import Deduper

logger = logging.getLogger(__name__)


def agent_re_enrich(
    state: PipelineState,
    max_total: int = 200,
    find_domains_max: int = 50,
    infer_emails_max: int = 100,
    verify_smtp: bool = False,
) -> PipelineState:
    """
    Segunda ronda de enriquecimiento.

    Args:
        max_total:        cap absoluto de leads a procesar
        find_domains_max: # de leads sin web a buscar dominio (más lento)
        infer_emails_max: # de leads con web pero sin email a inferir
        verify_smtp:      hacer SMTP probe (más lento, +preciso)
    """
    state.fase_actual = "re_enrich"

    if max_total <= 0:
        logger.info("⏭  Re-enrich skip (max_total=0)")
        state.stats["re_enrich"] = {"skipped": True}
        return state

    # Lazy imports
    try:
        from src.extraction.domain_finder import DomainFinder
        from src.extraction.email_inferencer import EmailInferencer
    except ImportError as e:
        logger.error("Re-enrich requiere domain_finder + email_inferencer: %s", e)
        state.stats["re_enrich"] = {"skipped": True, "error": str(e)}
        return state

    finder = DomainFinder(verify_mx=True, min_score_to_accept=60)
    inferencer = EmailInferencer(check_smtp=verify_smtp, max_candidates=3)

    # Separar candidatos por estrategia
    sin_web = []         # ningún sitio_web → DomainFinder
    con_web_sin_email = []  # tiene web pero no email → EmailInferencer

    for r in state.candidatos:
        if r.email:
            continue  # ya tiene email, no hacer nada
        if r.sitio_web:
            con_web_sin_email.append(r)
        elif r.empresa or r.nombre_comercial:
            sin_web.append(r)

    sin_web = sin_web[:find_domains_max]
    con_web_sin_email = con_web_sin_email[:infer_emails_max]
    total = len(sin_web) + len(con_web_sin_email)
    if total > max_total:
        # Recortar proporcionalmente
        ratio = max_total / total
        sin_web = sin_web[:int(len(sin_web) * ratio)]
        con_web_sin_email = con_web_sin_email[:int(len(con_web_sin_email) * ratio)]

    logger.info("Re-enrich: %s sin web (DomainFinder) + %s con web (EmailInferencer)",
                len(sin_web), len(con_web_sin_email))

    stats = {
        "n_targets_sin_web": len(sin_web),
        "n_targets_con_web": len(con_web_sin_email),
        "domains_found": 0,
        "emails_inferred": 0,
        "emails_smtp_verified": 0,
        "errors": 0,
    }

    t0 = time.time()

    # ---- Fase 1: domain finder para sin_web ----
    for i, rec in enumerate(sin_web, 1):
        try:
            empresa = rec.empresa or rec.nombre_comercial or ""
            ciudad = rec.municipio or ""
            giro = rec.giro_descripcion or ""
            r = finder.find(empresa, ciudad=ciudad, giro=giro)
            if r.has_result():
                rec.sitio_web = f"https://{r.best.domain}"
                rec.metadata["domain_found_by"] = "DomainFinder"
                rec.metadata["domain_score"] = r.best.score
                rec.metadata["domain_query_used"] = r.queries_used[0] if r.queries_used else ""
                stats["domains_found"] += 1
                # Ahora que tiene dominio, también intentar inferir email
                inf = inferencer.infer_from_domain(r.best.domain)
                if inf.has_result() and inf.best_email.confidence >= 50:
                    rec.email = inf.best_email.email
                    rec.metadata["email_inferred"] = True
                    rec.metadata["email_confidence"] = inf.best_email.confidence
                    rec.metadata["email_inference_status"] = inf.best_email.status
                    stats["emails_inferred"] += 1
                    if inf.best_email.is_verified_smtp:
                        stats["emails_smtp_verified"] += 1
        except Exception as e:  # noqa: BLE001
            logger.debug("Re-enrich domain err %s: %s", rec.empresa, e)
            stats["errors"] += 1

        if i % 10 == 0:
            logger.info("  Re-enrich domain: %s/%s (encontrados=%s)",
                        i, len(sin_web), stats["domains_found"])

    # ---- Fase 2: email inferencer para con_web_sin_email ----
    for i, rec in enumerate(con_web_sin_email, 1):
        try:
            dom = (rec.sitio_web or "").replace("https://", "").replace("http://", "")
            dom = dom.lstrip("www.").rstrip("/").split("/")[0]
            if not dom:
                continue
            inf = inferencer.infer_from_domain(dom)
            if inf.has_result() and inf.best_email.confidence >= 50:
                rec.email = inf.best_email.email
                rec.metadata["email_inferred"] = True
                rec.metadata["email_confidence"] = inf.best_email.confidence
                rec.metadata["email_inference_status"] = inf.best_email.status
                stats["emails_inferred"] += 1
                if inf.best_email.is_verified_smtp:
                    stats["emails_smtp_verified"] += 1
        except Exception as e:  # noqa: BLE001
            logger.debug("Re-enrich email err %s: %s", rec.empresa, e)
            stats["errors"] += 1

        if i % 20 == 0:
            logger.info("  Re-enrich email: %s/%s (inferidos=%s)",
                        i, len(con_web_sin_email), stats["emails_inferred"])

    # Persistir cambios en DB
    try:
        dd = Deduper()
        for rec in sin_web + con_web_sin_email:
            if rec.metadata.get("email_inferred") or rec.metadata.get("domain_found_by"):
                dd.upsert(rec, job_id=state.job_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("Re-enrich persist err: %s", e)

    stats["duration_sec"] = round(time.time() - t0, 1)
    state.stats["re_enrich"] = stats

    logger.info(
        "Re-enrich completado: domains=%s emails_inferred=%s smtp_verified=%s en %.0fs",
        stats["domains_found"], stats["emails_inferred"],
        stats["emails_smtp_verified"], stats["duration_sec"],
    )
    return state


__all__ = ["agent_re_enrich"]
