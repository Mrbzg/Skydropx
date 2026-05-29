"""
MCP (Model Context Protocol) server para Agente Fénix v5.

Expone el agente como skill nativa para:
- Claude Code     (claude mcp add fenix -- python -m src.skill.mcp_server)
- opencode        (mcpServers en opencode.json)
- Cursor          (vía MCP adapter)
- Cualquier cliente compatible con MCP stdio

Implementa el protocolo MCP estándar (JSON-RPC 2.0 sobre stdio) sin requerir
la librería oficial `mcp` (zero deps). Si está instalada, podemos migrar más
adelante a la SDK oficial para mejor compat con features avanzadas.

Tools expuestas:
- fenix_healthcheck      → estado de la infra
- fenix_run              → ejecutar pipeline completo
- denue_cuantificar      → conteo previo a descarga
- denue_search           → búsqueda DENUE
- verify_email           → email cascada
- verify_phone           → phone validate
- detect_tech_stack      → tech stack de un URL
- search_dorks           → dorks via SearchBackendManager
- db_stats               → estadísticas DB
- db_companies           → listar leads en DB
"""
from __future__ import annotations

import json
import logging
import sys
import traceback
from pathlib import Path
from typing import Any

# Windows usa cp1252 por defecto y crashea con caracteres como → o acentos.
# Forzamos UTF-8 en stdout/stderr para que el JSON-RPC y los logs no fallen.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001 (Python <3.7 o streams no reconfigurables)
    pass

# Setup paths + logging (logging va a stderr para no romper JSON-RPC en stdout)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Forzar carga de .env (importar config dispara _load_dotenv)
from src.core.config import settings  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="[mcp] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("fenix-mcp")


# ---------------- Catálogo de tools ----------------

TOOLS_DEFINITION = [
    {
        "name": "fenix_healthcheck",
        "description": "Verifica el estado de la infraestructura del Agente Fénix: token DENUE, search backends disponibles, base de datos, output dir, etc.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "fenix_run",
        "description": "Ejecuta el pipeline completo de generación de leads B2B/B2C para Skydropx (México). Aplica metodología 4-D y 8 agentes en cadena (TrendScout→Scout→Hunter→Verifier→Persist→Profiler→Dispatcher→SelfImprover). Devuelve leads exportados en CSV v4.0.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "nicho": {"type": "string", "description": "ej: 'ropa femenina', 'calzado', 'joyería'"},
                "zona": {"type": "string", "description": "ej: 'CDMX', 'Jalisco', 'nacional'", "default": "nacional"},
                "modelo": {"type": "string", "enum": ["B2B", "B2C", "C2C", "D2C", "C2B"]},
                "meta": {"type": "integer", "minimum": 1, "maximum": 100000, "default": 100},
                "mode": {"type": "string", "enum": ["quick", "standard", "deep", "enterprise"]},
                "sources": {"type": "string", "description": "lista CSV: denue,mercadolibre,dorks,camaras"},
                "scianes": {"type": "string", "description": "lista CSV de códigos SCIAN, ej '4632,4633'"},
                "enrich_max": {"type": "integer", "default": 50},
            },
            "required": ["nicho"],
        },
    },
    {
        "name": "denue_cuantificar",
        "description": "Cuenta cuántos establecimientos DENUE existen para un código SCIAN + entidad + estrato, SIN descargarlos. Útil para estimar tamaño antes de correr el pipeline.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "actividad": {"type": "string", "description": "SCIAN 2-6 dígitos, ej '46' o '463311'"},
                "entidad": {"type": "string", "description": "clave 2 dígitos (01-32) o '00' nacional", "default": "0"},
                "estrato": {"type": "string", "description": "1-7 tamaño, 0 todos", "default": "0"},
            },
            "required": ["actividad"],
        },
    },
    {
        "name": "denue_search",
        "description": "Busca establecimientos DENUE/INEGI por sector/subsector/rama/clase + entidad. Devuelve datos reales (razón social, dirección, teléfono, email, sitio web).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entidad": {"type": "string", "default": "00"},
                "sector": {"type": "string"},
                "subsector": {"type": "string"},
                "rama": {"type": "string"},
                "clase": {"type": "string"},
                "estrato": {"type": "string", "default": "0"},
                "limit": {"type": "integer", "default": 20, "maximum": 200},
            },
        },
    },
    {
        "name": "verify_email",
        "description": "Verifica un email en cascada (sintaxis → disposable → MX records → SMTP opcional). Devuelve status, is_personal, MX records.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "email": {"type": "string"},
                "check_smtp": {"type": "boolean", "default": False},
            },
            "required": ["email"],
        },
    },
    {
        "name": "verify_phone",
        "description": "Valida y normaliza teléfono MX usando Google libphonenumber. Detecta tipo (móvil/fijo/800), región (CDMX/GDL/MTY...), carrier, y si puede usar WhatsApp.",
        "inputSchema": {
            "type": "object",
            "properties": {"phone": {"type": "string"}},
            "required": ["phone"],
        },
    },
    {
        "name": "detect_tech_stack",
        "description": "Detecta tecnologías usadas por un sitio web (Shopify, Tiendanube, WooCommerce, Klaviyo, MercadoPago, Meta Pixel, etc.) + maturity score 0-100. Útil para calificar leads.",
        "inputSchema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "search_dorks",
        "description": "Búsqueda Google Dorks vía SearchBackendManager (Serper→SearXNG→OpenSERP→DDG cascada). Devuelve URLs encontradas.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 10, "maximum": 100},
                "backend": {"type": "string", "description": "opcional: serper|searxng|openserp|ddg"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "db_stats",
        "description": "Estadísticas de la base de datos Fénix: companies, contacts, jobs, distribución por bucket y estado.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "db_companies",
        "description": "Lista empresas en DB filtradas por bucket (COMPLETO/SOLO_EMAIL/SOLO_TEL/SIN_CONTACTO/RAW) y/o estado.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "bucket": {"type": "string", "enum": ["COMPLETO", "SOLO_EMAIL", "SOLO_TEL", "SIN_CONTACTO", "RAW", "PARCIAL"]},
                "min_score": {"type": "integer", "default": 0},
                "estado": {"type": "string"},
                "limit": {"type": "integer", "default": 50, "maximum": 500},
                "only_with_contact": {"type": "boolean", "default": True},
            },
        },
    },
{
        "name": "osint_holehe",
        "description": "Verifica si un email está registrado en 100+ servicios (Twitter, LinkedIn, Instagram, etc.). Confirma que el email es de una persona REAL activa, no un buzón genérico. Requiere: pip install holehe",
        "inputSchema": {
            "type": "object",
            "properties": {"email": {"type": "string"}},
            "required": ["email"],
        },
    },
    {
        "name": "osint_maigret",
        "description": "Busca un username/nombre en 3000+ sitios públicos. Útil para encontrar perfiles LinkedIn/IG/Twitter de un decisor B2B. Requiere: pip install maigret",
        "inputSchema": {
            "type": "object",
            "properties": {"username": {"type": "string"}, "top_sites_only": {"type": "boolean", "default": True}},
            "required": ["username"],
        },
    },
    {
        "name": "osint_phoneinfoga",
        "description": "OSINT sobre número telefónico: carrier + tipo línea + presencia en redes. Complementa verify_phone. Requiere binario phoneinfoga.",
        "inputSchema": {
            "type": "object",
            "properties": {"phone": {"type": "string", "description": "E.164 ej +525512345678"}},
            "required": ["phone"],
        },
    },
    {
        "name": "osint_budget",
        "description": "Estado de las cuotas/presupuestos de las herramientas OSINT externas (Holehe, Maigret, pagodo, PhoneInfoga, SpiderFoot).",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
{
        "name": "trends_now",
        "description": "Detecta qué productos/nichos están en tendencia HOY en México (Google Trends + Mercado Libre + TikTok + Amazon). Devuelve top trends + nichos del catálogo Fénix que matchean para usar en pipeline.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sources": {"type": "string", "description": "csv: google,mercadolibre,tiktok,amazon"},
                "suggest_niches": {"type": "boolean", "default": True},
            },
        },
    },
    {
        "name": "events_active",
        "description": "Lista eventos comerciales activos AHORA (Día de las Madres, Mundial, Buen Fin, Black Friday, Navidad, etc.) con días restantes, keywords y categorías target.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "event_search_plan",
        "description": "Dado un input del usuario ('leads del mundial con compra y gana'), devuelve plan de búsqueda con evento detectado, tipo de campaña, dorks sugeridos y categorías target.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "find_agency",
        "description": "Detecta la agencia/empresa detrás de una campaña en un dominio. Busca 'bases del sorteo', 'términos y condiciones', 'aviso de privacidad' y extrae agencias conocidas + RFCs + razones sociales. Ej: datumax.mx → MASSIVE EMOTIONS S DE RL DE CV.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dominio": {"type": "string"},
                "campana": {"type": "string"},
            },
            "required": ["dominio"],
        },
    },
    {
        "name": "export_hubspot_csv",
        "description": "Exporta leads de la DB a 2 CSVs listos para import manual en HubSpot (contacts.csv + companies.csv + README con instrucciones). Headers HubSpot-friendly.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "bucket": {"type": "string", "enum": ["COMPLETO","PARCIAL","SOLO_EMAIL","SOLO_TEL"]},
                "only_bucket": {"type": "string", "description": "CSV de buckets a incluir"},
                "min_score": {"type": "integer", "default": 0},
                "limit": {"type": "integer", "default": 5000},
            },
        },
    },
    {
        "name": "dedup_audit",
        "description": "Reporte de deduplicación: overlap ratio, leads huérfanos, top empresas con más fuentes, sugerencias para próxima campaña.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
{
        "name": "plans_list",
        "description": "Lista los plans YAML disponibles en plans/. Cada plan es un atajo manual para no escribir 10 flags. NO son crons automáticos: el usuario los corre a voluntad.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "plans_run",
        "description": "Ejecuta un plan YAML (campaña reusable). El usuario decide cuándo correrlo. Acepta overrides como meta y zona.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "ej: plans/EJEMPLO.yaml"},
                "meta": {"type": "integer", "description": "override del meta del plan"},
                "zona": {"type": "string"},
            },
            "required": ["file"],
        },
    },
    {
        "name": "plans_history",
        "description": "Historial de corridas de plans: cuándo, cuántos leads, duración.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "opcional, filtra por plan"},
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
{
        "name": "supabase_healthcheck",
        "description": "Verifica conexión a Supabase y estado de tablas. Si no están creadas, indica cómo aplicarlas.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "supabase_status",
        "description": "Compara contadores entre SQLite local y Supabase cloud. Útil para ver pendientes de sync.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "supabase_push",
        "description": "Sube cambios de SQLite local a Supabase. target=all|companies|contacts|jobs. incremental=true por defecto (solo cambios nuevos).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "enum": ["all", "companies", "contacts", "jobs"], "default": "all"},
                "incremental": {"type": "boolean", "default": True},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "supabase_query_companies",
        "description": "Consulta companies directamente desde Supabase cloud (útil cuando la SQLite local no está disponible).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 50},
                "bucket": {"type": "string", "description": "COMPLETO | PARCIAL | SOLO_EMAIL | SOLO_TEL"},
                "estado": {"type": "string"},
            },
        },
    },
    {
        "name": "harvest_domain",
        "description": "OSINT completo sobre un dominio: theHarvester + EmailHarvester si están instalados. Devuelve emails, subdominios, IPs.",
        "inputSchema": {
            "type": "object",
            "properties": {"domain": {"type": "string"}},
            "required": ["domain"],
        },
    },
]


# ---------------- Implementación de tools ----------------

def _tool_fenix_healthcheck(args: dict) -> dict:
    from src.core.config import settings
    from src.sources.search_backends import get_default_manager
    from src.sources.denue_source import DenueClient
    result = {
        "denue_token": bool(settings.denue_token),
        "search_backends": settings.search_backends_configured(),
        "sqlite_path": settings.sqlite_path,
        "fenix_mode": settings.fenix_mode,
    }
    try:
        avail = [b.name for b in get_default_manager().available_backends()]
        result["search_backends_live"] = avail
    except Exception as e:  # noqa: BLE001
        result["search_error"] = str(e)
    if settings.denue_token:
        try:
            r = DenueClient().cuantificar("46", "09", "0")
            if r:
                result["denue_api_reachable"] = True
                result["denue_sample_count"] = int(r[0].get("Total", 0))
        except Exception as e:  # noqa: BLE001
            result["denue_error"] = str(e)
    return result


def _tool_fenix_run(args: dict) -> dict:
    from src.core.models import ResearchPlan, ModeloNegocio, Canal, NivelUsuario, Estrategia
    from src.agents.pipeline import run_pipeline

    plan = ResearchPlan(
        nicho=args["nicho"],
        meta=args.get("meta", 100),
        zona=args.get("zona", "nacional"),
        modelo=ModeloNegocio(args["modelo"].upper()) if args.get("modelo") else ModeloNegocio.UNKNOWN,
        canal=Canal.WEB,
        nivel_usuario=NivelUsuario.INTERMEDIO,
        scianes=args["scianes"].split(",") if args.get("scianes") else [],
        sources_enabled=args["sources"].split(",") if args.get("sources") else [],
    )
    plan.estrategia = Estrategia(args["mode"]) if args.get("mode") else plan.auto_estrategia()
    state = run_pipeline(plan, enrich_max=args.get("enrich_max", 50),
                          formats=["csv", "json"])
    return {
        "job_id": state.job_id,
        "duration_sec": state.stats.get("pipeline_duration_sec"),
        "n_leads_exportados": len(state.leads_enriched),
        "stats": state.stats,
        "exports": state.exports,
        "errors_count": len(state.errors),
    }


def _tool_denue_cuantificar(args: dict) -> dict:
    from src.sources.denue_source import DenueClient
    c = DenueClient()
    r = c.cuantificar(args["actividad"], args.get("entidad", "0"),
                      args.get("estrato", "0"))
    total = sum(int(d.get("Total", 0)) for d in r) if r else 0
    return {"actividad": args["actividad"], "entidad": args.get("entidad"),
            "total": total, "by_entidad": r}


def _tool_denue_search(args: dict) -> dict:
    from src.sources.denue_source import DenueClient, resolve_entidad
    c = DenueClient()
    entidad = resolve_entidad(args.get("entidad", "00"))
    kwargs = {k: args[k] for k in ("sector", "subsector", "rama", "clase") if args.get(k)}
    recs = c.buscar_area_act_estr(
        entidad=entidad, registro_inicial=1, registro_final=args.get("limit", 20),
        estrato=args.get("estrato", "0"), **kwargs,
    )
    return {
        "count": len(recs),
        "results": [{
            "empresa": r.razon_social or r.nombre_establecimiento,
            "telefono": r.telefono, "email": r.email, "sitio_web": r.sitio_web,
            "direccion": f"{r.calle} {r.num_exterior}", "municipio": r.municipio,
            "estado": r.estado, "giro": r.clase_actividad, "tamano": r.tamano,
        } for r in recs],
    }


def _tool_verify_email(args: dict) -> dict:
    from src.extraction.email_verifier import EmailVerifier
    v = EmailVerifier(check_mx=True, check_smtp=args.get("check_smtp", False))
    r = v.verify(args["email"])
    return {
        "email": r.email, "status": r.status, "is_valid": r.is_valid,
        "is_personal": r.is_personal, "is_disposable": r.is_disposable,
        "mx_records": r.mx_records[:3] if r.mx_records else [],
        "smtp_message": r.smtp_message,
    }


def _tool_verify_phone(args: dict) -> dict:
    from src.extraction.phone_validator import validate_phone_mx
    v = validate_phone_mx(args["phone"])
    return {
        "is_valid": v.is_valid, "e164": v.e164, "national": v.national,
        "type": v.line_type, "region": v.region, "carrier": v.carrier,
        "can_whatsapp": v.can_whatsapp, "error": v.error or None,
    }


def _tool_detect_tech_stack(args: dict) -> dict:
    from src.extraction.tech_stack import detect_from_url
    return detect_from_url(args["url"]).to_dict()


def _tool_search_dorks(args: dict) -> dict:
    from src.sources.search_backends import get_default_manager
    mgr = get_default_manager()
    prefer = [args["backend"]] if args.get("backend") else None
    results = mgr.search(args["query"], limit=args.get("limit", 10), prefer=prefer)
    return {
        "query": args["query"],
        "backend_used": results[0].source if results else None,
        "count": len(results),
        "results": [{"url": r.url, "title": r.title, "snippet": r.snippet[:200]}
                    for r in results],
    }


def _tool_db_stats(args: dict) -> dict:
    from src.db.engine import get_db
    return get_db().stats()


def _tool_db_companies(args: dict) -> dict:
    from src.db.repositories import CompanyRepository
    co = CompanyRepository().list(
        bucket=args.get("bucket"), min_score=args.get("min_score", 0),
        estado=args.get("estado"), limit=args.get("limit", 50),
        only_with_contact=args.get("only_with_contact", True),
    )
    return {"count": len(co), "companies": co}


def _tool_harvest_domain(args: dict) -> dict:
    from src.extraction.osint_tools import harvest_all
    r = harvest_all(args["domain"])
    return r



def _tool_osint_holehe(args: dict) -> dict:
    from src.extraction.osint_deep import holehe_check
    r = holehe_check(args["email"])
    return {"available": r.available, "sites_registered": r.sites_registered,
            "is_active_persona": r.is_active_persona, "error": r.error or None}


def _tool_osint_maigret(args: dict) -> dict:
    from src.extraction.osint_deep import maigret_search
    r = maigret_search(args["username"], top_sites_only=args.get("top_sites_only", True))
    return {"available": r.available, "profiles": r.profiles,
            "top_profiles": r.top_profiles, "error": r.error or None}


def _tool_osint_phoneinfoga(args: dict) -> dict:
    from src.extraction.osint_deep import phoneinfoga_scan
    r = phoneinfoga_scan(args["phone"])
    return {"available": r.available, "country": r.country,
            "carrier": r.carrier, "line_type": r.line_type, "error": r.error or None}


def _tool_osint_budget(args: dict) -> dict:
    from src.core.budget import stats_all
    from src.extraction.osint_deep import availability
    return {"tools_available": availability(), "budgets": stats_all()}



def _tool_trends_now(args: dict) -> dict:
    from src.extraction.trends_detector import get_all_trends, suggest_niches_from_trends
    sources = args["sources"].split(",") if args.get("sources") else None
    report = get_all_trends(sources=sources)
    out = report.to_dict()
    if args.get("suggest_niches", True):
        out["nichos_sugeridos"] = suggest_niches_from_trends(report)
    return out


def _tool_events_active(args: dict) -> dict:
    from src.extraction.events_campaigns import get_eventos_activos
    activos = get_eventos_activos()
    return {"count": len(activos),
            "eventos": [{"id":e.id,"nombre":e.nombre,"fase":e.fase,
                          "dias_restantes":e.dias_restantes,
                          "fecha":e.fecha_evento,
                          "categorias_target":e.categorias_target,
                          "keywords":e.keywords[:5]} for e in activos]}


def _tool_event_search_plan(args: dict) -> dict:
    from src.extraction.events_campaigns import suggest_event_campaign_search
    return suggest_event_campaign_search(args["query"])


def _tool_find_agency(args: dict) -> dict:
    from src.extraction.events_campaigns import find_agency_behind_campaign
    r = find_agency_behind_campaign(args["dominio"],
                                       nombre_campana=args.get("campana", ""))
    return r.__dict__


def _tool_export_hubspot_csv(args: dict) -> dict:
    from src.db.engine import get_db
    from src.export.hubspot_csv import export_hubspot_csvs
    db = get_db()
    where, params = ["1=1"], []
    if args.get("bucket"):
        where.append("bucket = ?"); params.append(args["bucket"])
    if args.get("min_score"):
        where.append("score_data >= ?"); params.append(args["min_score"])
    rows = db.fetch_all(f"""
        SELECT c.*,
               (SELECT value FROM contacts WHERE company_id=c.id AND kind='email' LIMIT 1) AS email,
               (SELECT value FROM contacts WHERE company_id=c.id AND kind='phone' LIMIT 1) AS telefono,
               (SELECT value FROM contacts WHERE company_id=c.id AND kind='whatsapp' LIMIT 1) AS whatsapp,
               (SELECT value FROM contacts WHERE company_id=c.id AND kind='website' LIMIT 1) AS sitio_web
        FROM companies c WHERE {' AND '.join(where)} LIMIT ?
    """, tuple(params) + (args.get("limit", 5000),))
    leads = []
    for r in rows:
        leads.append({
            "lead_id": r["id"], "nombre": r["razon_social"] or "",
            "empresa": r["nombre_comercial"] or r["razon_social"] or "",
            "email": r.get("email") or "", "telefono": r.get("telefono") or "",
            "whatsapp": r.get("whatsapp") or "", "sitio_web": r.get("sitio_web") or "",
            "giro": r["giro_descripcion"] or "", "tamano": r["tamano"] or "",
            "ubicacion": r["municipio"] or "", "estado": r["estado"] or "",
            "modelo": r["modelo_negocio"] or "", "skydropx_plan": r["skydropx_plan"] or "",
            "tipo_lead": r["tipo_lead"] or "", "scoring": r["score_data"] or 0,
            "_bucket": r["bucket"] or "", "fuentes": "",
            "value_proposition": "",
            "metadata": json.loads(r["metadata_json"] or "{}"),
        })
    only_bucket = tuple((args.get("only_bucket") or "COMPLETO,PARCIAL,SOLO_EMAIL").split(","))
    result = export_hubspot_csvs(leads, only_bucket=only_bucket)
    return result.to_dict()


def _tool_dedup_audit(args: dict) -> dict:
    from src.db.dedup_audit import audit_dedup
    return audit_dedup().to_dict()



def _tool_plans_list(args: dict) -> dict:
    from src.skill.plans import list_plans
    return {"plans": list_plans()}


def _tool_plans_run(args: dict) -> dict:
    from src.skill.plans import run_plan
    overrides = {}
    if args.get("meta"): overrides["meta"] = args["meta"]
    if args.get("zona"): overrides["zona"] = args["zona"]
    return run_plan(args["file"], **overrides)


def _tool_plans_history(args: dict) -> dict:
    from src.skill.plans import history
    h = history(plan_file=args.get("file"), limit=args.get("limit", 20))
    return {"count": len(h), "entries": h}



def _tool_supabase_healthcheck(args: dict) -> dict:
    from src.db.supabase_client import healthcheck
    return healthcheck()


def _tool_supabase_status(args: dict) -> dict:
    from src.db.sync import status
    return status()


def _tool_supabase_push(args: dict) -> dict:
    from src.db.sync import push_all, push_companies, push_contacts, push_jobs
    target = args.get("target", "all")
    incremental = args.get("incremental", True)
    limit = args.get("limit")
    if target == "companies":
        return push_companies(incremental=incremental, limit=limit)
    if target == "contacts":
        return push_contacts(incremental=incremental, limit=limit)
    if target == "jobs":
        return push_jobs(incremental=incremental, limit=limit)
    return push_all(incremental=incremental)


def _tool_supabase_query_companies(args: dict) -> dict:
    from src.db.supabase_client import query_companies
    rows = query_companies(
        limit=args.get("limit", 50),
        bucket=args.get("bucket"),
        estado=args.get("estado"),
    )
    return {"count": len(rows), "rows": rows}


TOOL_HANDLERS = {
    "fenix_healthcheck": _tool_fenix_healthcheck,
    "fenix_run": _tool_fenix_run,
    "denue_cuantificar": _tool_denue_cuantificar,
    "denue_search": _tool_denue_search,
    "verify_email": _tool_verify_email,
    "verify_phone": _tool_verify_phone,
    "detect_tech_stack": _tool_detect_tech_stack,
    "search_dorks": _tool_search_dorks,
    "db_stats": _tool_db_stats,
    "db_companies": _tool_db_companies,
    "harvest_domain": _tool_harvest_domain,
    "osint_holehe": _tool_osint_holehe,
    "osint_maigret": _tool_osint_maigret,
    "osint_phoneinfoga": _tool_osint_phoneinfoga,
    "osint_budget": _tool_osint_budget,
    "trends_now": _tool_trends_now,
    "events_active": _tool_events_active,
    "event_search_plan": _tool_event_search_plan,
    "find_agency": _tool_find_agency,
    "export_hubspot_csv": _tool_export_hubspot_csv,
    "dedup_audit": _tool_dedup_audit,
    "plans_list": _tool_plans_list,
    "plans_run": _tool_plans_run,
    "plans_history": _tool_plans_history,
    "supabase_healthcheck": _tool_supabase_healthcheck,
    "supabase_status": _tool_supabase_status,
    "supabase_push": _tool_supabase_push,
    "supabase_query_companies": _tool_supabase_query_companies,
}


# ---------------- JSON-RPC 2.0 server (stdio) ----------------

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "agente-fenix", "version": "5.3.0"}


def _send(msg: dict) -> None:
    """Envía un mensaje JSON-RPC por stdout."""
    s = json.dumps(msg, ensure_ascii=False, default=str)
    sys.stdout.write(s + "\n")
    sys.stdout.flush()


def _ok(req_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id: Any, code: int, message: str, data: Any = None) -> dict:
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def handle_request(req: dict) -> dict | None:
    """Procesa un mensaje JSON-RPC. Devuelve la respuesta o None para notifications."""
    method = req.get("method", "")
    req_id = req.get("id")
    params = req.get("params", {}) or {}

    # === Lifecycle ===
    if method == "initialize":
        return _ok(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {
                "tools": {"listChanged": False},
                "logging": {},
            },
            "serverInfo": SERVER_INFO,
        })

    if method == "notifications/initialized":
        return None  # notification, no response

    if method == "ping":
        return _ok(req_id, {})

    # === Tools ===
    if method == "tools/list":
        return _ok(req_id, {"tools": TOOLS_DEFINITION})

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {}) or {}
        handler = TOOL_HANDLERS.get(tool_name)
        if not handler:
            return _err(req_id, -32601, f"Tool desconocida: {tool_name}")
        try:
            result = handler(tool_args)
            content_text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
            return _ok(req_id, {
                "content": [{"type": "text", "text": content_text}],
                "isError": False,
            })
        except Exception as e:  # noqa: BLE001
            logger.exception("Tool '%s' falló: %s", tool_name, e)
            return _ok(req_id, {
                "content": [{"type": "text",
                              "text": f"Error ejecutando {tool_name}: {e}\n\n{traceback.format_exc()}"}],
                "isError": True,
            })

    # === Otros (resources, prompts) — no implementados aún ===
    if method == "resources/list":
        return _ok(req_id, {"resources": []})
    if method == "prompts/list":
        return _ok(req_id, {"prompts": []})

    return _err(req_id, -32601, f"Método no soportado: {method}")


def serve() -> int:
    """Loop principal: lee JSON-RPC de stdin, escribe respuestas a stdout."""
    logger.info("MCP server fenix v%s listo (stdio)", SERVER_INFO["version"])
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            _send(_err(None, -32700, f"Parse error: {e}"))
            continue
        try:
            resp = handle_request(req)
            if resp is not None:
                _send(resp)
        except Exception as e:  # noqa: BLE001
            logger.exception("Error procesando request: %s", e)
            _send(_err(req.get("id"), -32603, f"Internal error: {e}"))
    return 0


if __name__ == "__main__":
    sys.exit(serve())
