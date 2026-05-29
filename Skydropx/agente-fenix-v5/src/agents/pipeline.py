"""
Los 7 agentes del pipeline Fénix v5 + orquestador.

Diseño minimalista, sin LangGraph (queremos zero deps externas opcionales).
Cada agente es una función que recibe PipelineState y la muta.

Flow: trend_scout → scout → hunter → verifier → profiler → dispatcher → self_improver
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

from src.core.config import settings
from src.core.models import (
    Bucket, Canal, Estrategia, Lead, ModeloNegocio, PipelineState,
    RawRecord, ResearchPlan, SkydropxPlan,
)
from src.extraction.skydropx_extractor import SkydropxExtractor
from src.db.engine import get_db
from src.db.deduper import Deduper
from src.db.repositories import JobRepository, CompanyRepository, ContactRepository
from src.extraction.email_verifier import EmailVerifier
from src.extraction.phone_validator import validate_phone_mx
from src.extraction.tech_stack import detect_tech_stack
from src.agents.deep_enrich import agent_deep_enrich
from src.agents.re_enrich import agent_re_enrich
from src.agents.checkpoint import save_checkpoint, load_checkpoint, delete_checkpoint
from src.core.exclusions import get_default_engine as get_exclusion_engine
from src.scoring.icp_classifier import classify_icp

from src.scoring.skydropx_scoring import (
    calc_contact_score, calc_data_score, calc_sales_priority, calc_skydropx_score,
    is_skydropx_ready,
)

logger = logging.getLogger(__name__)

# ---------------- Catálogo de nichos ----------------

NICHO_SCIAN_PATH = Path(__file__).resolve().parents[2] / "data" / "nicho_scian.json"

def _load_nicho_catalog() -> dict:
    if NICHO_SCIAN_PATH.exists():
        return json.loads(NICHO_SCIAN_PATH.read_text(encoding="utf-8"))
    return {"nichos": {}}

NICHO_CATALOG = _load_nicho_catalog()


# =====================================================================
# AGENTE 1 — TREND SCOUT
# =====================================================================

def agent_trend_scout(state: PipelineState) -> PipelineState:
    """
    Detecta tendencias activas en el nicho. Versión mínima: usa el catálogo local
    para inferir modelo de negocio y SCIAN si no se especificó.

    Versión completa (futura): integrar pytrends + ML Trends + TikTok hashtags.
    """
    state.fase_actual = "trend_scout"
    plan = state.plan
    nicho_norm = (plan.nicho or "").lower().strip()

    # Resolver SCIAN desde catálogo si no vienen explícitos
    if not plan.scianes:
        for key, entry in NICHO_CATALOG.get("nichos", {}).items():
            if (key == nicho_norm or
                nicho_norm in entry.get("aliases", []) or
                any(a in nicho_norm for a in entry.get("aliases", []))):
                plan.scianes = entry.get("scianes", [])
                if plan.modelo == ModeloNegocio.UNKNOWN:
                    modelo_str = entry.get("modelo_default", "UNKNOWN").split("/")[0]
                    try:
                        plan.modelo = ModeloNegocio(modelo_str)
                    except ValueError:
                        pass
                state.stats.setdefault("trend_scout", {})["catalog_match"] = key
                logger.info("Trend Scout: nicho '%s' → catalog='%s' scian=%s modelo=%s",
                            plan.nicho, key, plan.scianes, plan.modelo.value)
                break

    if not plan.scianes:
        state.add_error("NO_DATA", "trend_scout",
                        f"Nicho '{plan.nicho}' no encontrado en catálogo. "
                        f"Agregar a data/nicho_scian.json o usar --scianes")
        state.stats.setdefault("trend_scout", {})["catalog_match"] = None
    return state


# =====================================================================
# AGENTE 2 — SCOUT
# =====================================================================

def agent_scout(state: PipelineState) -> PipelineState:
    """Descubre empresas en múltiples fuentes paralelas (sequential aquí)."""
    state.fase_actual = "scout"
    plan = state.plan
    n_pre = len(state.candidatos)
    sources_run: dict[str, int] = {}

    # Si el usuario pidió canal social/marketplace, agregar las sources correspondientes
    canal_str = plan.canal.value if hasattr(plan.canal, "value") else str(plan.canal)
    enabled = plan.sources_enabled or _default_sources_for(plan.estrategia, canal=canal_str)

    if "denue" in enabled and settings.has_denue() and plan.scianes:
        try:
            from src.sources.denue_source import search_for_skydropx
            recs = search_for_skydropx(
                nicho_scian=plan.scianes,
                zona=plan.zona,
                estrato="0" if not plan.estratos else ",".join(plan.estratos),
                max_per_zone=min(plan.meta * 2, 5000),
            )
            for r in recs:
                state.candidatos.append(_denue_to_raw(r))
            sources_run["denue"] = len(recs)
        except Exception as e:  # noqa: BLE001
            logger.exception("Scout DENUE err: %s", e)
            state.add_error("AUTH_ERROR" if "token" in str(e).lower() else "TIMEOUT",
                            "denue", str(e))

    if "mercadolibre" in enabled:
        try:
            from src.sources.mercadolibre_source import search as ml_search
            recs = ml_search(plan)
            state.candidatos.extend(recs)
            sources_run["mercadolibre"] = len(recs)
        except Exception as e:  # noqa: BLE001
            logger.exception("Scout ML err: %s", e)
            state.add_error("TIMEOUT", "mercadolibre", str(e))

    if "dorks" in enabled:
        try:
            from src.sources.ecommerce_dorks_source import search as dork_search
            recs = dork_search(plan)
            state.candidatos.extend(recs)
            sources_run["dorks"] = len(recs)
        except Exception as e:  # noqa: BLE001
            logger.exception("Scout Dorks err: %s", e)
            state.add_error("TIMEOUT", "dorks", str(e))

    if "camaras" in enabled:
        try:
            from src.sources.camaras_mx_source import search as cam_search
            recs = cam_search(plan)
            state.candidatos.extend(recs)
            sources_run["camaras"] = len(recs)
        except Exception as e:  # noqa: BLE001
            logger.exception("Scout Cámaras err: %s", e)
            state.add_error("TIMEOUT", "camaras", str(e))

    if "social_shops" in enabled:
        try:
            from src.sources.social_shops_source import search as social_search
            recs = social_search(plan)
            state.candidatos.extend(recs)
            sources_run["social_shops"] = len(recs)
        except Exception as e:  # noqa: BLE001
            logger.exception("Scout social_shops err: %s", e)
            state.add_error("TIMEOUT", "social_shops", str(e))

    # Filtro de exclusiones 3 capas (técnica + MX + ICP Skydropx outbound)
    excl_engine = get_exclusion_engine()
    pre_excl = len(state.candidatos)
    accepted, excluded = excl_engine.filter_records(state.candidatos)
    state.candidatos = accepted
    if excluded:
        logger.info("Scout: %s excluidos de %s por filtros ICP/ruido",
                    len(excluded), pre_excl)
        by_cat: dict = {}
        for ex in excluded:
            by_cat[ex.get("category", "?")] = by_cat.get(ex.get("category", "?"), 0) + 1
        state.stats["exclusions"] = {
            "total_excluded": len(excluded),
            "by_category": by_cat,
            "sample": excluded[:5],
        }

    # Dedup in-memory por fingerprint
    seen: set[str] = set()
    deduped: list[RawRecord] = []
    for r in state.candidatos:
        fp = r.fingerprint()
        if fp not in seen:
            seen.add(fp)
            deduped.append(r)
    state.candidatos = deduped

    state.stats["scout"] = {
        "sources_run": sources_run,
        "n_pre_dedup": n_pre + sum(sources_run.values()),
        "n_post_dedup": len(state.candidatos),
        "dedup_rate_pct": round(
            100 * (1 - len(state.candidatos) / max(1, n_pre + sum(sources_run.values()))),
            2,
        ),
    }
    logger.info("Scout: %s candidatos únicos (de %s) por fuentes=%s",
                len(state.candidatos), n_pre + sum(sources_run.values()), sources_run)
    return state


def _denue_to_raw(d_rec) -> RawRecord:
    return RawRecord(
        source="denue",
        empresa=d_rec.razon_social or d_rec.nombre_establecimiento or "",
        nombre_comercial=d_rec.nombre_establecimiento or d_rec.razon_social,
        email=d_rec.email or None,
        telefono=d_rec.telefono or None,
        sitio_web=d_rec.sitio_web or None,
        direccion=f"{d_rec.tipo_vialidad} {d_rec.calle} {d_rec.num_exterior}".strip(),
        colonia=d_rec.colonia,
        cp=d_rec.cp,
        municipio=d_rec.municipio,
        estado=d_rec.estado,
        scian=d_rec.clase_actividad_id,
        giro_descripcion=d_rec.clase_actividad,
        tamano=d_rec.tamano,
        longitud=d_rec.longitud,
        latitud=d_rec.latitud,
        metadata={
            "denue_id": d_rec.id,
            "denue_clee": d_rec.clee,
            "estrato": d_rec.estrato,
            "estrato_id": _estrato_text_to_id(d_rec.estrato),
            "fecha_alta": d_rec.fecha_alta,
            "tipo_establecimiento": d_rec.tipo_establecimiento,
        },
    )


def _estrato_text_to_id(text: str | None) -> str:
    """Convierte '0 a 5 personas' → '1', '6 a 10 personas' → '2', etc."""
    if not text:
        return ""
    text = text.lower()
    mapping = {
        "0 a 5 personas": "1", "6 a 10 personas": "2",
        "11 a 30 personas": "3", "31 a 50 personas": "4",
        "51 a 100 personas": "5", "101 a 250 personas": "6",
        "251 y más personas": "7", "251 y mas personas": "7",
    }
    return mapping.get(text, "")


# Mapeo canal -> sources adicionales (se SUMAN a las del modo)
CANAL_TO_SOURCES = {
    "web":         [],                            # default (ya cubierto por denue + camaras + dorks)
    "social":      ["social_shops"],              # ← TikTok + IG + FB via dorks
    "marketplace": ["mercadolibre"],              # ← ML API + dorks marketplace
    "fisica":      [],                            # DENUE ya domina retail físico
    "mixto":       ["social_shops", "mercadolibre"],
}


def _default_sources_for(estrategia: Estrategia, canal: str = "web") -> list[str]:
    """Devuelve sources según modo + canal solicitado."""
    if estrategia == Estrategia.QUICK:
        base = ["denue"]
    elif estrategia == Estrategia.STANDARD:
        base = ["denue", "camaras"]
    elif estrategia == Estrategia.DEEP:
        base = ["denue", "camaras", "mercadolibre", "dorks"]
    else:  # ENTERPRISE
        base = ["denue", "camaras", "mercadolibre", "dorks", "social_shops"]

    # Agregar sources por canal
    canal_norm = canal.lower() if canal else "web"
    extras = CANAL_TO_SOURCES.get(canal_norm, [])
    for s in extras:
        if s not in base:
            base.append(s)
    return base


# =====================================================================
# AGENTE 3 — HUNTER (enriquecimiento web)
# =====================================================================

def _hunter_enrich_one(r, extractor):
    """Enriquece UN candidato (crawl + tech stack). Pensado para ThreadPool."""
    import requests as _rq
    res = {"email": False, "phone": False, "wa": False}
    try:
        contact = extractor.extract_from_domain(r.sitio_web)
        if contact.emails and not r.email:
            r.email = contact.emails[0]; res["email"] = True
        if contact.telefonos and not r.telefono:
            r.telefono = contact.telefonos[0]; res["phone"] = True
        if contact.whatsapps and not r.whatsapp:
            r.whatsapp = contact.whatsapps[0]; res["wa"] = True
        if contact.plataforma:
            r.metadata["plataforma_detectada"] = contact.plataforma
        if contact.envios_intent:
            r.metadata["envios_intent"] = True
        if contact.paqueterias_mencionadas:
            r.metadata["paqueterias_mencionadas"] = contact.paqueterias_mencionadas
        if contact.nombres_personas and not r.nombre_persona:
            r.nombre_persona = contact.nombres_personas[0]
        # Tech stack (un fetch ligero de la home)
        try:
            hr = _rq.get(
                f"https://{r.sitio_web.replace('https://','').replace('http://','').rstrip('/')}",
                timeout=10,
                headers={"User-Agent": extractor.session.headers["User-Agent"]},
            )
            if hr.ok:
                tech = detect_tech_stack(hr.text, url=hr.url, headers=dict(hr.headers))
                if tech.detected:
                    r.metadata["tech_stack"] = list(tech.detected.keys())
                    r.metadata["tech_categories"] = list(tech.by_category.keys())
                    r.metadata["maturity_score"] = tech.maturity_score
                    if tech.skydropx_signals:
                        r.metadata["skydropx_signals_tech"] = tech.skydropx_signals
        except Exception as _te:
            logger.debug("tech stack detect err %s: %s", r.sitio_web, _te)
        res["ok"] = True
    except Exception as e:  # noqa: BLE001
        logger.debug("Hunter err %s: %s", r.sitio_web, e)
        res["ok"] = False
    return res


def agent_hunter(state: PipelineState, max_enrich: int = 200) -> PipelineState:
    """
    Enriquece los candidatos que tienen sitio_web pero les faltan contactos.
    Cap a max_enrich. Crawling EN PARALELO (ThreadPool) para máxima velocidad.
    """
    state.fase_actual = "hunter"
    extractor = SkydropxExtractor()
    enriched = 0
    found_email = 0
    found_phone = 0
    found_wa = 0

    # Solo procesa los que tienen web pero les falta email O tel
    to_enrich = []
    for r in state.candidatos:
        if not r.sitio_web:
            continue
        if r.email and (r.telefono or r.whatsapp):
            continue
        to_enrich.append(r)
        if len(to_enrich) >= max_enrich:
            break

    # --- Paralelización: muchos sitios a la vez (cada uno es I/O de red) ---
    import os as _os
    from concurrent.futures import ThreadPoolExecutor, as_completed
    # workers configurables vía .env (FENIX_HUNTER_WORKERS), default 16
    try:
        workers = int(_os.environ.get("FENIX_HUNTER_WORKERS", "16"))
    except ValueError:
        workers = 16
    workers = max(1, min(workers, 64))

    if to_enrich:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_hunter_enrich_one, r, extractor): r for r in to_enrich}
            for fut in as_completed(futures):
                res = fut.result() or {}
                if res.get("ok"):
                    enriched += 1
                if res.get("email"): found_email += 1
                if res.get("phone"): found_phone += 1
                if res.get("wa"):    found_wa += 1

    state.leads_hunted = state.candidatos  # ahora con datos enriquecidos
    state.stats["hunter"] = {
        "intentados": len(to_enrich),
        "enriquecidos": enriched,
        "emails_nuevos": found_email,
        "telefonos_nuevos": found_phone,
        "whatsapps_nuevos": found_wa,
        "workers": workers,
    }
    logger.info("Hunter: enriqueció %s/%s sitios en paralelo (x%s) (emails=%s, tels=%s, wa=%s)",
                enriched, len(to_enrich), workers, found_email, found_phone, found_wa)
    return state


# =====================================================================

def agent_persist(state: PipelineState) -> PipelineState:
    """Persiste candidatos en SQLite/Postgres con dedup cross-corrida."""
    state.fase_actual = "persist"
    try:
        db = get_db()
        db.init_schema()  # idempotent
        jobs = JobRepository(db)
        jobs.create(
            state.job_id,
            nicho=state.plan.nicho,
            zona=state.plan.zona,
            modelo=state.plan.modelo.value if hasattr(state.plan.modelo, "value") else str(state.plan.modelo),
            meta=state.plan.meta,
            estrategia=state.plan.estrategia.value if hasattr(state.plan.estrategia, "value") else str(state.plan.estrategia),
        )
        dd = Deduper(db)
        result = dd.upsert_many(state.candidatos, job_id=state.job_id)
        state.stats["persist"] = result
        state.stats["dedup"] = result
        logger.info("Persist: %s nuevos, %s actualizados (dedup cross-corrida)",
                    result["n_new"], result["n_updated"])
    except Exception as e:
        logger.exception("Persist err: %s", e)
        state.add_error("DB_ERROR", "persist", str(e))
    return state


# =====================================================================
# AGENTE 4 — VERIFIER
# =====================================================================

EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "tempmail.com", "10minutemail.com",
    "throwawaymail.com", "yopmail.com", "trashmail.com",
}


def _valid_email(email: str) -> bool:
    if not email or not EMAIL_RE.match(email):
        return False
    dom = email.rsplit("@", 1)[1].lower()
    return dom not in DISPOSABLE_DOMAINS


def _valid_phone_mx(phone: str) -> tuple[bool, str]:
    """Usa phonenumbers (Google libphonenumber) si está, fallback a regex."""
    v = validate_phone_mx(phone)
    return v.is_valid, v.e164

def _phone_metadata(phone: str) -> dict:
    """Metadata extra: tipo (movil/fijo), región, carrier, can_whatsapp."""
    v = validate_phone_mx(phone)
    if not v.is_valid:
        return {}
    return {
        "phone_e164": v.e164,
        "phone_type": v.line_type,
        "phone_region": v.region,
        "phone_carrier": v.carrier,
        "phone_can_whatsapp": v.can_whatsapp,
    }


def _verify_one(r, verifier):
    """Verifica email + teléfonos de UN registro. Para ThreadPool. Devuelve flags."""
    flags = {"invalid_email": 0, "disposable": 0, "mx_missing": 0,
             "personal": 0, "invalid_phone": 0}
    if r.email:
        ev = verifier.verify(r.email)
        if not ev.is_valid:
            if ev.status == "disposable": flags["disposable"] = 1
            elif ev.status == "mx_missing": flags["mx_missing"] = 1
            else: flags["invalid_email"] = 1
            r.email = None
        elif ev.is_personal:
            flags["personal"] = 1
            r.metadata["email_verification"] = ev.status
            r.metadata["email_is_personal"] = True
        else:
            r.metadata["email_verification"] = ev.status

    if r.telefono:
        meta = _phone_metadata(r.telefono)
        if meta:
            r.telefono = meta["phone_e164"]
            r.metadata.update(meta)
            if meta["phone_can_whatsapp"] and not r.whatsapp:
                r.whatsapp = meta["phone_e164"]
        else:
            r.telefono = None
            flags["invalid_phone"] = 1

    if r.whatsapp:
        meta = _phone_metadata(r.whatsapp)
        if meta and meta["phone_can_whatsapp"]:
            r.whatsapp = meta["phone_e164"]
            if "whatsapp_region" not in r.metadata:
                r.metadata["whatsapp_region"] = meta["phone_region"]
        else:
            r.whatsapp = None
    return flags


def agent_verifier(state: PipelineState, check_mx: bool = True, check_smtp: bool = False) -> PipelineState:
    """Valida emails (sintaxis → MX → SMTP) + teléfonos (E.164 MX). EN PARALELO."""
    state.fase_actual = "verifier"
    verifier = EmailVerifier(check_mx=check_mx, check_smtp=check_smtp)

    import os as _os
    from concurrent.futures import ThreadPoolExecutor, as_completed
    try:
        workers = int(_os.environ.get("FENIX_VERIFY_WORKERS", "16"))
    except ValueError:
        workers = 16
    workers = max(1, min(workers, 64))

    totals = {"invalid_email": 0, "disposable": 0, "mx_missing": 0,
              "personal": 0, "invalid_phone": 0}
    leads = list(state.leads_hunted)
    if leads:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_verify_one, r, verifier) for r in leads]
            for fut in as_completed(futures):
                f = fut.result() or {}
                for k in totals:
                    totals[k] += f.get(k, 0)

    state.candidatos = leads
    state.stats["verifier"] = {
        "n_validados": len(leads),
        "n_email_invalid": totals["invalid_email"],
        "n_disposable_blocked": totals["disposable"],
        "n_mx_missing": totals["mx_missing"],
        "n_email_personal": totals["personal"],
        "n_phone_invalid": totals["invalid_phone"],
        "check_mx": check_mx,
        "check_smtp": check_smtp,
        "workers": workers,
    }
    return state


# =====================================================================
# AGENTE 5 — PROFILER
# =====================================================================

def _infer_modelo_y_plan(r: RawRecord, default_modelo: ModeloNegocio) -> tuple[str, str, str]:
    """Devuelve (modelo, plan, propuesta_valor)."""
    tam = (r.tamano or "").lower()
    has_envios = (r.metadata or {}).get("envios_intent")
    has_competencia = bool(set((r.metadata or {}).get("paqueterias_mencionadas") or []) - {"skydropx"})
    is_ml = r.source == "mercadolibre"

    if is_ml:
        return "C2C", "Starter", "Cotizador + Guías sueltas sin contrato"
    if tam == "grande" or has_envios and "wholesale" in (r.giro_descripcion or "").lower():
        return "B2B", "Enterprise", "API + Webhooks + Convenios tarifarios"
    if tam == "mediana":
        return ("D2C" if has_envios else "B2C"), "PyME", "Fulfillment + Integraciones ecommerce"
    if tam in ("pequeña", "pequena", "micro"):
        return ("B2C" if has_envios else default_modelo.value or "B2C"), "Starter", "SOS + Rastreo + Automatizaciones"
    return default_modelo.value or "B2C", "Starter", "Cotizador + Rastreo"


def agent_profiler(state: PipelineState) -> PipelineState:
    """Convierte RawRecord en Lead final con scoring + mapa Skydropx."""
    state.fase_actual = "profiler"
    leads: list[Lead] = []

    for r in state.candidatos:
        # Clasificación ICP dual (PyME / Enterprise / C2C)
        lead_input_dict = {
            "empresa": r.empresa, "nombre_comercial": r.nombre_comercial,
            "scian": r.scian, "tamano": r.tamano,
            "source": r.source, "giro_descripcion": r.giro_descripcion,
            "metadata": r.metadata or {},
        }
        icp = classify_icp(lead_input_dict)
        # Guardar SIEMPRE el resultado en metadata del raw
        r.metadata["icp_segment"] = icp.icp_segment
        r.metadata["icp_score"] = icp.icp_score
        r.metadata["icp_vertical"] = icp.vertical
        r.metadata["envios_estimados"] = icp.envios_estimados

        if icp.icp_segment != "NO_ICP":
            # Mapear ICP a ModeloNegocio si no se especificó uno explícito
            seg_to_modelo = {"ICP_1_PYME": "B2C", "ICP_2_ENTERPRISE": "B2B", "ICP_3_C2C": "C2C"}
            user_modelo = state.plan.modelo.value if hasattr(state.plan.modelo, "value") else str(state.plan.modelo)
            if user_modelo and user_modelo not in ("UNKNOWN", ""):
                modelo = user_modelo
            else:
                modelo = seg_to_modelo.get(icp.icp_segment, "B2C")
            plan_sd = icp.skydropx_plan
            vp = icp.value_proposition
        else:
            modelo, plan_sd, vp = _infer_modelo_y_plan(r, state.plan.modelo)
        ml_perfil = (r.metadata or {}).get("ml_perfil_url", "")
        lead_dict = {
            "email": r.email or "",
            "telefono": r.telefono or "",
            "whatsapp": r.whatsapp or "",
            "empresa": r.empresa or "",
            "nombre_persona": r.nombre_persona or "",
            "tamano": r.tamano or "",
            "modelo": modelo,
            "sitio_web": r.sitio_web or "",
            "instagram": r.instagram or ml_perfil if "instagram" in str(ml_perfil) else r.instagram or "",
            "facebook": r.facebook or "",
            "linkedin": r.linkedin or "",
            "ubicacion": r.municipio or r.colonia or "",
            "metadata": r.metadata or {},
        }

        ds = calc_data_score(lead_dict)
        ss = calc_skydropx_score(lead_dict)
        sp = calc_sales_priority(lead_dict)
        cs = calc_contact_score(lead_dict)

        # Bucket general según DATA_SCORE
        if ds.total >= 70:
            bucket = "COMPLETO"
        elif r.email and not r.telefono:
            bucket = "SOLO_EMAIL"
        elif r.telefono and not r.email:
            bucket = "SOLO_TEL"
        elif r.email or r.telefono or r.whatsapp:
            bucket = "PARCIAL"
        else:
            bucket = "SIN_CONTACTO"

        lead = Lead(
            lead_id=str(uuid.uuid4()),
            modelo=modelo,
            tipo="empresa" if r.empresa else "persona" if r.nombre_persona else "empresa",
            nombre=r.empresa or r.nombre_persona or r.nombre_comercial or "(sin nombre)",
            empresa=r.nombre_comercial or r.empresa or "",
            rfc=r.rfc or "",
            email=r.email or "DATO_NO_VERIFICABLE",
            email_score=80 if r.email else 0,
            telefono=r.telefono or "DATO_NO_VERIFICABLE",
            whatsapp="yes" if r.whatsapp else "no",
            instagram=r.instagram or "",
            linkedin=r.linkedin or "",
            facebook=r.facebook or "",
            ubicacion=r.municipio or r.colonia or "",
            estado=r.estado or "",
            giro=r.giro_descripcion or "",
            tamano=r.tamano or "",
            skydropx_plan=plan_sd,
            soluciones="",
            value_proposition=vp,
            priority_score=sp.total,
            scoring=ds.total,
            tipo_lead=ss.bucket,  # caliente|frio
            fuentes=r.source,
            first_seen=r.fecha_descubierto.isoformat() if r.fecha_descubierto else datetime.now().isoformat(),
            version="4.0",
        )
        lead._bucket = bucket
        lead._scoring_breakdown = {
            "DATA_SCORE": ds.detail,
            "SKYDROPX_SCORE": ss.total,
            "SALES_PRIORITY": sp.detail,
            "CONTACT_SCORE": cs.detail,
        }
        lead._raw_records = [r.to_dict()]
        leads.append(lead)

        # Propagar scoring a la DB (si el lead vino de la persistencia)
        try:
            company_id = (r.metadata or {}).get("_company_id")
            if not company_id:
                # Buscar por fingerprint
                from src.db.engine import get_db
                row = get_db().fetch_one(
                    "SELECT id FROM companies WHERE fingerprint = ? LIMIT 1",
                    (r.fingerprint(),),
                )
                company_id = row["id"] if row else None
            if company_id:
                CompanyRepository().update_scoring(
                    company_id=company_id,
                    score_data=ds.total, score_skydropx=ss.total,
                    score_sales=sp.total, score_contact=cs.total,
                    bucket=bucket, tipo_lead=ss.bucket,
                    modelo_negocio=modelo, skydropx_plan=plan_sd,
                )
                # También sincronizar metadata_json (ICP, tech_stack, etc.)
                try:
                    import json as _json
                    db_inst = get_db()
                    row = db_inst.fetch_one(
                        "SELECT metadata_json FROM companies WHERE id = ?", (company_id,)
                    )
                    existing_meta = {}
                    if row and row.get("metadata_json"):
                        try:
                            existing_meta = _json.loads(row["metadata_json"])
                        except Exception:
                            pass
                    existing_meta.update(r.metadata or {})
                    db_inst.execute(
                        "UPDATE companies SET metadata_json = ? WHERE id = ?",
                        (_json.dumps(existing_meta, ensure_ascii=False, default=str),
                         company_id),
                    )
                except Exception as _e:
                    logger.debug("metadata_json sync err: %s", _e)
        except Exception as _e:
            logger.debug("Profiler scoring → DB err: %s", _e)

    state.leads_enriched = leads
    state.stats["profiler"] = {
        "n_leads": len(leads),
        "n_completo": sum(1 for l in leads if l._bucket == "COMPLETO"),
        "n_solo_email": sum(1 for l in leads if l._bucket == "SOLO_EMAIL"),
        "n_solo_tel": sum(1 for l in leads if l._bucket == "SOLO_TEL"),
        "n_sin_contacto": sum(1 for l in leads if l._bucket == "SIN_CONTACTO"),
        "n_caliente": sum(1 for l in leads if l.tipo_lead == "caliente"),
    }
    return state


# =====================================================================
# AGENTE 6 — DISPATCHER (export CSV/JSON)
# =====================================================================

def agent_dispatcher(state: PipelineState, formats: list[str] | None = None) -> PipelineState:
    """Exporta a output/. CSV v4.0 (26 cols) + JSON con metadata."""
    state.fase_actual = "dispatcher"
    out_dir = Path(settings.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fecha = datetime.now().strftime("%Y%m%d_%H%M%S")
    nicho_slug = re.sub(r"\W+", "_", state.plan.nicho.lower())[:30]
    base = f"fenix_{nicho_slug}_{fecha}_{state.job_id}"

    formats = formats or ["csv", "json"]

    if "csv" in formats:
        import csv
        csv_path = out_dir / f"{base}.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=Lead.csv_columns())
            w.writeheader()
            for lead in state.leads_enriched:
                w.writerow(lead.to_csv_row())
        state.exports["csv"] = str(csv_path)

    if "json" in formats:
        json_path = out_dir / f"{base}.json"
        with json_path.open("w", encoding="utf-8") as f:
            json.dump([lead.to_full_dict() for lead in state.leads_enriched],
                      f, ensure_ascii=False, indent=2, default=str)
        state.exports["json"] = str(json_path)

    state.stats["dispatcher"] = {
        "exports": state.exports,
        "n_exportados": len(state.leads_enriched),
    }
    return state


# =====================================================================
# AGENTE 7 — SELF-IMPROVER (memoria persistente)
# =====================================================================

def agent_self_improver(state: PipelineState) -> PipelineState:
    """Persiste stats de fuentes y notas de la corrida en data/memory.json."""
    state.fase_actual = "self_improver"
    mem_path = Path("data/memory.json")
    mem_path.parent.mkdir(parents=True, exist_ok=True)

    mem = {}
    if mem_path.exists():
        try:
            mem = json.loads(mem_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            mem = {}

    # Source stats acumulado
    src_stats = mem.setdefault("source_stats", {})
    for source, n in (state.stats.get("scout", {}).get("sources_run") or {}).items():
        s = src_stats.setdefault(source, {"runs": 0, "leads_total": 0})
        s["runs"] += 1
        s["leads_total"] += n
        s["last_run"] = datetime.now().isoformat()

    # Audit trail
    audit = mem.setdefault("audit_trail", [])
    audit.append({
        "job_id": state.job_id,
        "nicho": state.plan.nicho,
        "meta": state.plan.meta,
        "started_at": state.started_at.isoformat(),
        "finished_at": datetime.now().isoformat(),
        "n_leads_exportados": len(state.leads_enriched),
        "n_completo": state.stats.get("profiler", {}).get("n_completo", 0),
        "errors": len(state.errors),
    })
    # Truncar audit a últimas 1000 entradas
    mem["audit_trail"] = audit[-1000:]

    mem_path.write_text(json.dumps(mem, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    state.stats["self_improver"] = {"memory_path": str(mem_path)}
    return state


# =====================================================================
# ORQUESTADOR
# =====================================================================

PIPELINE_ORDER: list[tuple[str, Callable]] = [
    ("trend_scout", agent_trend_scout),
    ("scout", agent_scout),
    ("hunter", agent_hunter),
    ("verifier", agent_verifier),
    ("persist", agent_persist),              # ← DB + dedup persistente
    ("re_enrich", agent_re_enrich),          # ← NEW: DomainFinder + EmailInferencer
    ("profiler", agent_profiler),
    ("deep_enrich", agent_deep_enrich),      # ← OSINT profundo (opcional, skip si max=0)
    ("dispatcher", agent_dispatcher),
    ("self_improver", agent_self_improver),
]


def run_pipeline(
    plan: ResearchPlan,
    skip_agents: list[str] | None = None,
    enrich_max: int = 200,
    re_enrich_max: int = 0,
    re_enrich_find_domains: int = 50,
    re_enrich_infer_emails: int = 100,
    re_enrich_verify_smtp: bool = False,
    deep_enrich_max: int = 0,
    deep_enrich_tools: tuple[str, ...] = ("holehe", "maigret", "phoneinfoga"),
    formats: list[str] | None = None,
    enable_checkpoint: bool | None = None,    # auto si meta>=500
    resume_job_id: str | None = None,         # reanudar desde checkpoint
    run_healthcheck: bool = True,
) -> PipelineState:
    """Punto de entrada principal."""
    skip = set(skip_agents or [])

    # === RESUME: cargar state desde checkpoint si se pidió ===
    if resume_job_id:
        loaded = load_checkpoint(resume_job_id)
        if loaded:
            state = loaded
            logger.info("=== RESUMING job=%s desde %s ===", state.job_id, state.fase_actual)
            # Agregar los agentes YA completados a skip
            completed_idx = None
            for i, (name, _) in enumerate(PIPELINE_ORDER):
                if name == state.fase_actual:
                    completed_idx = i
                    break
            if completed_idx is not None:
                for name, _ in PIPELINE_ORDER[:completed_idx + 1]:
                    skip.add(name)
                logger.info("Saltando agentes ya completados: %s", sorted(skip))
        else:
            logger.warning("Checkpoint %s no encontrado, corriendo de cero", resume_job_id)

    if not resume_job_id or "state" not in dir():
        if plan.estrategia == Estrategia.STANDARD and not plan.sources_enabled:
            plan.estrategia = plan.auto_estrategia()
        state = PipelineState(plan=plan)

    # === HEALTHCHECK pre-run ===
    if run_healthcheck and not resume_job_id:
        try:
            from src.core.healthcheck import run_healthcheck as hc_fn
            report = hc_fn(meta=plan.meta)
            state.stats["healthcheck"] = report.summary()
            if not report.overall_ok:
                logger.error("HEALTHCHECK FAILED — abortando")
                for c in report.critical_failures:
                    logger.error("  ❌ %s: %s", c.name, c.message)
                    state.add_error("HEALTHCHECK_CRITICAL", c.name, c.message)
                state.stats["pipeline_duration_sec"] = 0
                return state
            for w in report.warnings:
                logger.warning("  ⚠ %s: %s", w.name, w.message)
        except Exception as e:
            logger.warning("No se pudo correr healthcheck: %s", e)

    # === Decidir si activar checkpoint ===
    if enable_checkpoint is None:
        enable_checkpoint = plan.meta >= 500
    if enable_checkpoint:
        logger.info("Checkpoints ACTIVADOS (meta=%s, archivo data/checkpoints/%s.json)",
                    plan.meta, state.job_id)

    logger.info("=== Pipeline Fénix v5 %s job=%s ===",
                "REANUDADO" if resume_job_id else "iniciado", state.job_id)
    logger.info("Plan: nicho=%s zona=%s meta=%s estrategia=%s",
                plan.nicho, plan.zona, plan.meta, plan.estrategia.value)

    t0 = time.time()
    for name, agent in PIPELINE_ORDER:
        if name in skip:
            logger.info("⏭  skip agent=%s", name)
            continue
        ta = time.time()
        try:
            if name == "hunter":
                state = agent(state, max_enrich=enrich_max)
            elif name == "re_enrich":
                if re_enrich_max > 0:
                    state = agent(state, max_total=re_enrich_max,
                                    find_domains_max=re_enrich_find_domains,
                                    infer_emails_max=re_enrich_infer_emails,
                                    verify_smtp=re_enrich_verify_smtp)
                else:
                    logger.info("⏭  skip re_enrich (re_enrich_max=0)")
                    state.stats["re_enrich"] = {"skipped_by_default": True}
            elif name == "deep_enrich":
                if deep_enrich_max > 0:
                    state = agent(state, max_leads=deep_enrich_max, tools=deep_enrich_tools)
                else:
                    logger.info("⏭  skip deep_enrich (deep_enrich_max=0, default)")
                    state.stats["deep_enrich"] = {"skipped_by_default": True}
            elif name == "dispatcher":
                state = agent(state, formats=formats)
            else:
                state = agent(state)
            logger.info("✓ agent=%s en %.1fs", name, time.time() - ta)
            state.checkpoint_at = datetime.now()
            if enable_checkpoint:
                try:
                    save_checkpoint(state, agent_completed=name)
                except Exception as _e:
                    logger.debug("Checkpoint err en %s: %s", name, _e)
        except Exception as e:  # noqa: BLE001
            logger.exception("✗ agent=%s falló: %s", name, e)
            state.add_error("AGENT_FAIL", name, str(e))

    state.stats["pipeline_duration_sec"] = round(time.time() - t0, 1)

    # === AUTO-SYNC a Supabase (si está configurado) ===
    try:
        from src.core.config import settings
        if getattr(settings, "supabase_auto_sync", False):
            from src.db.supabase_client import is_configured
            if is_configured():
                from src.db.sync import push_all
                logger.info("Auto-sync a Supabase (SUPABASE_AUTO_SYNC=true)...")
                sync_result = push_all(incremental=True)
                state.stats["supabase_auto_sync"] = sync_result
                logger.info("Auto-sync OK: companies=%s contacts=%s jobs=%s",
                            sync_result.get("companies", {}).get("pushed", 0),
                            sync_result.get("contacts", {}).get("pushed", 0),
                            sync_result.get("jobs", {}).get("pushed", 0))
    except Exception as _e:
        logger.warning("Auto-sync a Supabase falló: %s", _e)

    # Borrar checkpoint si el pipeline terminó completo
    if enable_checkpoint and not state.errors:
        try:
            delete_checkpoint(state.job_id)
        except Exception:
            pass

    # Cerrar job en DB
    try:
        from src.db.repositories import JobRepository
        JobRepository().finish(
            state.job_id, state.stats, state.errors, state.exports,
            duration_sec=state.stats["pipeline_duration_sec"],
        )
    except Exception as e:
        logger.debug("No se pudo cerrar job en DB: %s", e)

    return state


__all__ = [
    "run_pipeline",
    "agent_trend_scout", "agent_scout", "agent_hunter",
    "agent_verifier", "agent_profiler", "agent_dispatcher", "agent_self_improver",
]
