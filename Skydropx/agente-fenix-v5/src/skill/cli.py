"""
CLI principal de Fénix v5.

Uso:
    python -m src.skill.cli fenix healthcheck
    python -m src.skill.cli fenix backends          ← ver estado search backends
    python -m src.skill.cli fenix search "ropa CDMX" --limit 10
    python -m src.skill.cli fenix proxies fetch     ← descargar proxies free
    python -m src.skill.cli fenix proxies check     ← health check de proxies
    python -m src.skill.cli fenix run --nicho "ropa" --meta 100
    python -m src.skill.cli fenix source denue cuantificar --actividad 46 --entidad 09
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from src.core.config import settings
from src.core.models import (
    Canal, Estrategia, ModeloNegocio, NivelUsuario, ResearchPlan,
)
from src.agents.pipeline import run_pipeline
from src.agents.checkpoint import get_last_pending


# ---------------- healthcheck ----------------

def cmd_healthcheck(args) -> int:
    result = {
        "ok": True,
        "checks": {
            "denue_token": bool(settings.denue_token),
            "search_backends": settings.search_backends_configured(),
            "search_mode": settings.search_mode,
            "sqlite_path": settings.sqlite_path,
            "output_dir_writable": _check_writable(settings.output_dir),
            "data_dir": Path("data").exists(),
            "fenix_mode": settings.fenix_mode,
            "rotate_user_agents": settings.rotate_user_agents,
            "proxies_configured": settings.has_proxies(),
            "hubspot_configured": bool(settings.hubspot_api_key),
        },
    }
    result["ok"] = all([
        result["checks"]["denue_token"],
        result["checks"]["output_dir_writable"],
    ])

    if settings.denue_token:
        try:
            from src.sources.denue_source import DenueClient
            c = DenueClient()
            r = c.cuantificar("46", "09", "0")
            if r and r[0].get("Total"):
                result["checks"]["denue_api_reachable"] = True
                result["checks"]["denue_sample"] = f"{int(r[0]['Total']):,} establecimientos sector 46 CDMX"
        except Exception as e:  # noqa: BLE001
            result["checks"]["denue_api_reachable"] = False
            result["checks"]["denue_error"] = str(e)

    # Verificar al menos 1 search backend disponible
    try:
        from src.sources.search_backends import get_default_manager
        mgr = get_default_manager()
        avail = [b.name for b in mgr.available_backends()]
        result["checks"]["search_backends_live"] = avail
        if not avail:
            result["ok"] = False
            result["checks"]["search_warning"] = "Ningún search backend disponible — DDG debería estar siempre"
    except Exception as e:  # noqa: BLE001
        result["checks"]["search_backends_error"] = str(e)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


def _check_writable(p: str) -> bool:
    try:
        Path(p).mkdir(parents=True, exist_ok=True)
        test = Path(p) / ".write_test"
        test.write_text("ok")
        test.unlink()
        return True
    except Exception:  # noqa: BLE001
        return False


# ---------------- backends (estado y stats) ----------------

def cmd_backends(args) -> int:
    """Muestra estado y estadísticas de todos los search backends."""
    from src.sources.search_backends import get_default_manager
    mgr = get_default_manager()
    stats = mgr.stats()
    print(json.dumps({
        "ok": True,
        "mode": mgr.mode,
        "available_now": [b.name for b in mgr.available_backends()],
        "backends": stats,
    }, ensure_ascii=False, indent=2))
    return 0


# ---------------- search (probar manager directo) ----------------

def cmd_search(args) -> int:
    """Test directo del search manager."""
    from src.sources.search_backends import get_default_manager
    mgr = get_default_manager()
    prefer = args.backend.split(",") if args.backend else None

    if args.mode == "parallel":
        out = mgr.search_parallel(args.query, limit=args.limit,
                                    backends=prefer)
        result = {
            "ok": True, "mode": "parallel", "query": args.query,
            "by_backend": {
                name: {"count": len(rs),
                       "results": [{"url": r.url, "title": r.title[:80]} for r in rs[:5]]}
                for name, rs in out.items()
            }
        }
    else:
        results = mgr.search(args.query, limit=args.limit,
                              prefer=prefer, avoid_paid=not args.allow_paid)
        result = {
            "ok": True, "query": args.query,
            "count": len(results),
            "backend_used": results[0].source if results else None,
            "results": [
                {"url": r.url, "title": r.title, "snippet": r.snippet[:120]}
                for r in results[:args.limit]
            ]
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


# ---------------- proxies ----------------

def cmd_proxies(args) -> int:
    from src.core.proxy_pool import get_default_pool, FREE_PROXY_FEEDS
    pool = get_default_pool()

    if args.subcommand == "stats":
        print(json.dumps(pool.stats(), ensure_ascii=False, indent=2))
    elif args.subcommand == "fetch":
        added = pool.fetch_free_proxies(max_per_feed=args.max_per_feed)
        print(json.dumps({"ok": True, "added": added,
                           "total": len(pool.proxies),
                           "feeds": FREE_PROXY_FEEDS},
                         ensure_ascii=False, indent=2))
    elif args.subcommand == "check":
        if not pool.proxies:
            print(json.dumps({"ok": False, "msg": "Pool vacío. Corre 'proxies fetch' primero."}))
            return 1
        result = pool.health_check()
        print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({"ok": False, "error": f"subcomando '{args.subcommand}' desconocido"}))
        return 2
    return 0


# ---------------- run pipeline ----------------

def cmd_run(args) -> int:
    plan = ResearchPlan(
        nicho=args.nicho,
        meta=args.meta,
        zona=args.zona or "nacional",
        modelo=_parse_modelo(args.modelo),
        canal=_parse_canal(args.canal),
        nivel_usuario=NivelUsuario.INTERMEDIO,
        scianes=args.scianes.split(",") if args.scianes else [],
        estratos=args.estratos.split(",") if args.estratos else [],
        sources_enabled=args.sources.split(",") if args.sources else [],
    )
    plan.estrategia = plan.auto_estrategia() if not args.mode else Estrategia(args.mode)

    state = run_pipeline(
        plan,
        enrich_max=args.enrich_max,
        re_enrich_max=args.re_enrich_max,
        re_enrich_find_domains=args.re_enrich_find_domains,
        re_enrich_infer_emails=args.re_enrich_infer_emails,
        re_enrich_verify_smtp=args.re_enrich_verify_smtp,
        deep_enrich_max=args.deep_enrich_max,
        deep_enrich_tools=tuple(args.deep_enrich_tools.split(",")) if args.deep_enrich_tools else (),
        formats=(args.format.split(",") if args.format else ["csv", "json"]),
        enable_checkpoint=True if args.force_checkpoint else None,
        resume_job_id=(args.resume or (get_last_pending() if args.resume_last else None)),
        run_healthcheck=not args.no_healthcheck,
    )

    summary = {
        "ok": True,
        "job_id": state.job_id,
        "fase_final": state.fase_actual,
        "duration_sec": state.stats.get("pipeline_duration_sec"),
        "n_leads_exportados": len(state.leads_enriched),
        "stats": state.stats,
        "exports": state.exports,
        "errors": state.errors,
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    else:
        _print_human_report(state)
    return 0


def _print_human_report(state) -> None:
    profile = state.stats.get("profiler", {})
    scout = state.stats.get("scout", {})
    hunter = state.stats.get("hunter", {})
    print(f"\n✓ Pipeline Fénix completado (job_id: {state.job_id} | "
          f"mode: {state.plan.estrategia.value.upper()})\n")
    print("📊 Resultados:")
    print(f"   · Leads totales:       {len(state.leads_enriched):,}")
    print(f"   · DATA_SCORE ≥70:      {profile.get('n_completo', 0):,}  (caliente)")
    print(f"   · SOLO_EMAIL:          {profile.get('n_solo_email', 0):,}")
    print(f"   · SOLO_TEL:            {profile.get('n_solo_tel', 0):,}")
    print(f"   · SIN_CONTACTO:        {profile.get('n_sin_contacto', 0):,}")
    print()
    if scout.get("sources_run"):
        print("🏆 Por fuente:")
        for src, n in scout["sources_run"].items():
            print(f"   · {src:18s} {n:,}")
        print()
    if hunter:
        print("🔍 Enriquecimiento Hunter:")
        print(f"   · Sitios crawleados: {hunter.get('enriquecidos', 0)}/{hunter.get('intentados', 0)}")
        print(f"   · Emails nuevos:     {hunter.get('emails_nuevos', 0)}")
        print(f"   · Teléfonos nuevos:  {hunter.get('telefonos_nuevos', 0)}")
        print(f"   · WhatsApps nuevos:  {hunter.get('whatsapps_nuevos', 0)}")
        print()
    if state.exports:
        print("📁 Archivos:")
        for fmt, path in state.exports.items():
            print(f"   · {fmt.upper():4s}  {path}")
        print()
    print(f"⏱  Duración total: {state.stats.get('pipeline_duration_sec', 0)}s")
    print("💰 Costo de esta corrida: $0.00 USD")
    if state.errors:
        print(f"\n⚠  {len(state.errors)} errores (ver --json para detalle)")


# ---------------- source individual ----------------

def cmd_source(args) -> int:
    src_name = args.source_name
    if src_name == "denue":
        return _cmd_source_denue(args)
    if src_name == "ml":
        return _cmd_source_ml(args)
    if src_name == "dorks":
        return _cmd_source_dorks(args)
    if src_name == "camaras":
        return _cmd_source_camaras(args)
    print(json.dumps({"ok": False, "error": f"fuente '{src_name}' desconocida"}))
    return 2


def _cmd_source_denue(args) -> int:
    from src.sources.denue_source import DenueClient, resolve_entidad
    c = DenueClient()
    if args.subcommand == "cuantificar":
        r = c.cuantificar(args.actividad or "46", args.entidad or "0",
                          args.estrato or "0")
        print(json.dumps({"ok": True, "result": r}, ensure_ascii=False, indent=2))
    elif args.subcommand == "search":
        entidad = resolve_entidad(args.entidad or "00")
        kwargs = {}
        for level in ("sector", "subsector", "rama", "clase"):
            v = getattr(args, level, None)
            if v: kwargs[level] = v
        recs = c.buscar_area_act_estr(
            entidad=entidad, registro_inicial=1, registro_final=args.limit or 50,
            estrato=args.estrato or "0", **kwargs,
        )
        print(json.dumps({"ok": True, "count": len(recs),
                          "sample": [{"empresa": r.razon_social or r.nombre_establecimiento,
                                      "tel": r.telefono, "email": r.email,
                                      "estado": r.estado} for r in recs[:10]]},
                         ensure_ascii=False, indent=2))
    elif args.subcommand == "nombre":
        recs = c.buscar_por_nombre(args.query, args.entidad or "00", 1, args.limit or 50)
        print(json.dumps({"ok": True, "count": len(recs),
                          "sample": [r.__dict__ for r in recs[:5]]},
                         ensure_ascii=False, indent=2, default=str))
    return 0


def _cmd_source_ml(args) -> int:
    from src.sources.mercadolibre_source import (
        MercadoLibreClient, iter_sellers_from_category, ECOMMERCE_CATEGORIES,
    )
    client = MercadoLibreClient()
    cat = args.categoria or "MLM1430"
    label = ECOMMERCE_CATEGORIES.get(cat, cat)
    out = []
    for sid in iter_sellers_from_category(client, cat, args.limit or 20):
        user = client.get_user(sid)
        if user:
            out.append({
                "id": user.get("id"), "nickname": user.get("nickname"),
                "tipo": user.get("user_type"),
                "ubicacion": (user.get("address") or {}).get("city"),
            })
    print(json.dumps({"ok": True, "categoria": cat, "label": label,
                      "count": len(out), "sellers": out},
                     ensure_ascii=False, indent=2))
    return 0


def _cmd_source_dorks(args) -> int:
    from src.sources.ecommerce_dorks_source import search as dork_search
    plan = ResearchPlan(nicho="dorks", meta=0)
    plan.extras["dork_categorias"] = (args.categoria or "envios_mx").split(",")
    plan.extras["dork_limit"] = args.limit or 20
    plan.extras["dork_fetch_html"] = bool(args.fetch_html)
    if args.backend:
        plan.extras["dork_prefer_backends"] = args.backend.split(",")
    recs = dork_search(plan)
    print(json.dumps({"ok": True, "count": len(recs),
                      "sample": [{"dominio": r.empresa,
                                  "url": r.metadata.get("url_origen"),
                                  "backend": r.metadata.get("backend_used"),
                                  "plataforma": r.metadata.get("plataforma_detectada"),
                                  "envios": r.metadata.get("envios_intent")}
                                 for r in recs[:10]]},
                     ensure_ascii=False, indent=2))
    return 0


def _cmd_source_camaras(args) -> int:
    from src.sources.camaras_mx_source import search as cam_search, CAMARAS_DISPONIBLES
    plan = ResearchPlan(nicho="camaras", meta=0)
    plan.extras["camaras"] = (args.camaras or "amvo,canacintra,canirac").split(",")
    recs = cam_search(plan)
    print(json.dumps({"ok": True, "camaras_disponibles": CAMARAS_DISPONIBLES,
                      "count": len(recs),
                      "sample": [{"empresa": r.empresa,
                                  "camara": r.metadata.get("camara"),
                                  "tel": r.telefono, "email": r.email}
                                 for r in recs[:10]]},
                     ensure_ascii=False, indent=2))
    return 0


# ---------------- helpers ----------------

def _parse_modelo(s):
    if not s: return ModeloNegocio.UNKNOWN
    try: return ModeloNegocio(s.upper())
    except ValueError: return ModeloNegocio.UNKNOWN

def _parse_canal(s):
    if not s: return Canal.WEB
    try: return Canal(s.lower())
    except ValueError: return Canal.WEB



# ---------------- db (estadísticas y mantenimiento) ----------------

def cmd_db(args) -> int:
    from src.db.engine import get_db
    from src.db.repositories import JobRepository, CompanyRepository

    db = get_db()
    if args.subcommand == "init":
        db.init_schema()
        print(json.dumps({"ok": True, "msg": f"Schema inicializado en {db.db_url}"}))
    elif args.subcommand == "stats":
        print(json.dumps({"ok": True, "stats": db.stats()},
                         ensure_ascii=False, indent=2, default=str))
    elif args.subcommand == "jobs":
        jobs = JobRepository(db).list_recent(limit=args.limit or 20)
        print(json.dumps({"ok": True, "jobs": jobs}, ensure_ascii=False, indent=2, default=str))
    elif args.subcommand == "companies":
        co = CompanyRepository(db).list(
            bucket=args.bucket, min_score=args.min_score or 0,
            estado=args.entidad, limit=args.limit or 20,
            only_with_contact=args.with_contact,
        )
        print(json.dumps({"ok": True, "count": len(co), "companies": co},
                         ensure_ascii=False, indent=2, default=str))
    elif args.subcommand == "opt-out":
        from src.db.deduper import Deduper
        dd = Deduper(db)
        dd.add_opt_out(args.kind or "email", args.value, args.reason or "user_request")
        print(json.dumps({"ok": True, "msg": f"Opt-out registrado para {args.value}"}))
    return 0


# ---------------- verify email ----------------

def cmd_verify(args) -> int:
    from src.extraction.email_verifier import EmailVerifier
    v = EmailVerifier(check_mx=True, check_smtp=args.smtp)
    out = []
    for email in args.emails:
        r = v.verify(email)
        out.append({
            "email": r.email, "status": r.status, "is_valid": r.is_valid,
            "is_personal": r.is_personal, "is_disposable": r.is_disposable,
            "mx_records": r.mx_records[:3] if r.mx_records else [],
            "smtp_message": r.smtp_message if r.checked_smtp else None,
            "duration_ms": r.duration_ms,
        })
    print(json.dumps({"ok": True, "results": out}, ensure_ascii=False, indent=2))
    return 0


# ---------------- harvest (OSINT en un dominio) ----------------

def cmd_harvest(args) -> int:
    from src.extraction.osint_tools import harvest_all
    result = harvest_all(args.domain)
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    return 0


# ---------------- antibot fetch ----------------

def cmd_antibot(args) -> int:
    from src.extraction.antibot_fetcher import fetch, fetch_stats
    if args.subcommand == "stats":
        print(json.dumps({"ok": True, "backends": fetch_stats()},
                         ensure_ascii=False, indent=2))
    elif args.subcommand == "fetch":
        r = fetch(args.url, max_level=args.max_level)
        print(json.dumps({
            "ok": not r.blocked and bool(r.html),
            "url": r.url, "final_url": r.final_url,
            "status_code": r.status_code, "level_used": r.level_used,
            "blocked": r.blocked, "html_size": len(r.html),
            "duration_ms": r.duration_ms, "error": r.error,
            "html_preview": r.html[:300] if r.html else "",
        }, ensure_ascii=False, indent=2))
    return 0




# ---------------- phone validate ----------------

def cmd_phone(args) -> int:
    from src.extraction.phone_validator import validate_phone_mx, HAS_PHONENUMBERS
    out = []
    for n in args.numbers:
        v = validate_phone_mx(n)
        out.append({
            "original": v.original, "valid": v.is_valid,
            "e164": v.e164, "national": v.national,
            "type": v.line_type, "region": v.region, "carrier": v.carrier,
            "can_whatsapp": v.can_whatsapp, "error": v.error or None,
        })
    print(json.dumps({"ok": True, "has_phonenumbers": HAS_PHONENUMBERS,
                       "results": out}, ensure_ascii=False, indent=2))
    return 0


# ---------------- tech (detect stack) ----------------

def cmd_tech(args) -> int:
    from src.extraction.tech_stack import detect_from_url
    t = detect_from_url(args.url)
    print(json.dumps({"ok": True, **t.to_dict()},
                     ensure_ascii=False, indent=2))
    return 0


# ---------------- robots ----------------

def cmd_robots(args) -> int:
    from src.core.robots import get_default_checker
    rc = get_default_checker()
    decision = rc.can_fetch(args.url)
    print(json.dumps({
        "ok": True, "url": args.url,
        "allowed": decision.allowed,
        "crawl_delay_sec": decision.crawl_delay,
        "reason": decision.reason,
    }, ensure_ascii=False, indent=2))
    return 0



# ---------------- osint (wrappers para deep enrichment) ----------------

def cmd_osint(args) -> int:
    """Wrappers individuales para herramientas OSINT externas."""
    from src.extraction.osint_deep import (
        availability, holehe_check, maigret_search,
        pagodo_search, phoneinfoga_scan, spiderfoot_scan,
    )

    if args.subcommand == "stats":
        from src.core.budget import stats_all
        print(json.dumps({
            "ok": True,
            "tools_available": availability(),
            "budgets": stats_all(),
        }, ensure_ascii=False, indent=2))
        return 0

    if args.subcommand == "holehe":
        if not args.target: return _err_target()
        r = holehe_check(args.target)
        print(json.dumps({"ok": True, "result": r.__dict__}, ensure_ascii=False, indent=2))
    elif args.subcommand == "maigret":
        if not args.target: return _err_target()
        r = maigret_search(args.target)
        print(json.dumps({"ok": True, "result": r.__dict__}, ensure_ascii=False, indent=2))
    elif args.subcommand == "pagodo":
        if not args.target: return _err_target()
        r = pagodo_search(args.target, max_dorks=args.max_dorks or 10)
        print(json.dumps({"ok": True, "result": r.__dict__}, ensure_ascii=False, indent=2))
    elif args.subcommand == "phoneinfoga":
        if not args.target: return _err_target()
        r = phoneinfoga_scan(args.target)
        print(json.dumps({"ok": True, "result": r.__dict__}, ensure_ascii=False, indent=2))
    elif args.subcommand == "spiderfoot":
        if not args.target: return _err_target()
        r = spiderfoot_scan(args.target)
        print(json.dumps({"ok": True, "result": r.__dict__}, ensure_ascii=False, indent=2))
    else:
        return _err_target()
    return 0


def _err_target():
    print(json.dumps({"ok": False, "error": "missing --target"}))
    return 2


# ---------------- budget ----------------

def cmd_budget(args) -> int:
    from src.core.budget import stats_all
    print(json.dumps({"ok": True, "budgets": stats_all()}, ensure_ascii=False, indent=2))
    return 0



# ---------------- ask (Discovery interactivo) ----------------

def cmd_ask(args) -> int:
    from src.skill.discovery import (
        parse_user_input, apply_answer, run_cli_discovery, DiscoverySession,
    )
    if args.session_id:
        session = DiscoverySession.load(args.session_id)
        if not session:
            print(json.dumps({"ok": False, "error": f"session {args.session_id} not found"}))
            return 2
        if args.field and args.value is not None:
            apply_answer(session, args.field, args.value)
            session.save()
            print(json.dumps({"ok": True, "session": session.__dict__},
                             ensure_ascii=False, indent=2, default=str))
            return 0

    if args.interactive:
        session = run_cli_discovery(args.text or "")
    else:
        session = parse_user_input(args.text or "")
        session.save()

    out = {"ok": True, "session_id": session.session_id, "status": session.status,
            "session": session.__dict__}
    if session.status == "ready" and args.run:
        from src.agents.pipeline import run_pipeline
        from src.agents.checkpoint import get_last_pending
        plan = session.to_research_plan()
        state = run_pipeline(plan,
                              enrich_max=args.enrich_max or 50,
                              deep_enrich_max=args.deep_enrich_max or 0)
        out["pipeline"] = {
            "job_id": state.job_id,
            "stats": state.stats,
            "exports": state.exports,
            "n_leads": len(state.leads_enriched),
        }
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


# ---------------- exclusions ----------------

def cmd_exclusions(args) -> int:
    from src.core.exclusions import ExclusionEngine
    from src.core.models import RawRecord
    eng = ExclusionEngine(
        include_large=args.include_large,
        include_medianas_grandes=args.include_medianas_grandes,
    )
    if args.subcommand == "list":
        cats = []
        for cat_key, entry in eng.by_category.items():
            cats.append({
                "category": cat_key,
                "empresas_count": len(entry.get("empresas", set())),
                "dominios_count": len(entry.get("dominios", set())),
                "scianes": list(entry.get("scianes", set())),
            })
        print(json.dumps({"ok": True, "categories": cats},
                          ensure_ascii=False, indent=2))
    elif args.subcommand == "check":
        rec = RawRecord(
            source="cli_test", empresa=args.empresa or "",
            sitio_web=args.dominio or "", scian=args.scian or "",
            metadata={"estrato_id": args.estrato or ""},
        )
        r = eng.check_raw_record(rec)
        print(json.dumps({"ok": True, **r.to_dict()},
                          ensure_ascii=False, indent=2))
    elif args.subcommand == "dork":
        print(json.dumps({"ok": True, "exclusions_dork": eng.build_dork_exclusions()},
                          ensure_ascii=False, indent=2))
    return 0


# ---------------- icp classifier ----------------

def cmd_icp(args) -> int:
    from src.scoring.icp_classifier import classify_icp
    lead = {
        "empresa": args.empresa, "scian": args.scian,
        "tamano": args.tamano, "source": args.source or "",
        "giro_descripcion": args.giro or "",
        "metadata": json.loads(args.metadata) if args.metadata else {},
    }
    r = classify_icp(lead)
    print(json.dumps({"ok": True, **r.to_dict()},
                      ensure_ascii=False, indent=2))
    return 0



# ---------------- trends ----------------

def cmd_trends(args) -> int:
    from src.extraction.trends_detector import (
        get_all_trends, get_google_trends_mx, suggest_niches_from_trends,
    )
    if args.subcommand == "google":
        items = get_google_trends_mx(use_cache=not args.no_cache)
        print(json.dumps({"ok": True, "count": len(items),
                           "trends": [i.to_dict() for i in items]},
                          ensure_ascii=False, indent=2, default=str))
    elif args.subcommand == "all":
        sources = args.sources.split(",") if args.sources else None
        report = get_all_trends(use_cache=not args.no_cache, sources=sources)
        out = report.to_dict()
        if args.suggest_niches:
            out["nichos_sugeridos"] = suggest_niches_from_trends(report)
        print(json.dumps({"ok": True, **out},
                          ensure_ascii=False, indent=2, default=str))
    return 0


# ---------------- events ----------------

def cmd_events(args) -> int:
    from src.extraction.events_campaigns import (
        get_eventos_activos, find_evento_by_keyword,
        suggest_event_campaign_search, build_event_campaign_dorks,
    )
    if args.subcommand == "active":
        activos = get_eventos_activos()
        print(json.dumps({"ok": True, "count": len(activos),
                           "eventos": [{"id":e.id,"nombre":e.nombre,
                                         "fase":e.fase,"dias_restantes":e.dias_restantes,
                                         "fecha":e.fecha_evento,
                                         "categorias_target":e.categorias_target,
                                         "keywords":e.keywords[:5]}
                                        for e in activos]},
                          ensure_ascii=False, indent=2))
    elif args.subcommand == "find":
        ev = find_evento_by_keyword(args.query or "")
        if ev:
            dorks = build_event_campaign_dorks(ev, incluir_campaign=True, incluir_exclusiones=False)
            print(json.dumps({"ok": True, "evento": ev, "dorks_generados": dorks[:8]},
                              ensure_ascii=False, indent=2))
        else:
            print(json.dumps({"ok": False, "error": "evento no encontrado"}))
    elif args.subcommand == "suggest":
        r = suggest_event_campaign_search(args.query or "")
        print(json.dumps({"ok": True, **r}, ensure_ascii=False, indent=2))
    return 0


# ---------------- agency ----------------

def cmd_agency(args) -> int:
    from src.extraction.events_campaigns import (
        find_agency_behind_campaign, detect_agency_in_text,
    )
    if args.text:
        r = detect_agency_in_text(args.text)
        print(json.dumps({"ok": True, "from": "text", **r},
                          ensure_ascii=False, indent=2))
    elif args.dominio:
        r = find_agency_behind_campaign(args.dominio,
                                          nombre_campana=args.campana or "",
                                          max_docs=args.max_docs or 3)
        print(json.dumps({"ok": True, "from": "domain", **r.__dict__},
                          ensure_ascii=False, indent=2))
    else:
        print(json.dumps({"ok": False, "error": "usa --dominio o --text"}))
        return 2
    return 0


# ---------------- hubspot export ----------------

def cmd_hubspot(args) -> int:
    from src.db.engine import get_db
    from src.export.hubspot_csv import export_hubspot_csvs
    db = get_db()
    where = ["1=1"]
    params = []
    if args.bucket:
        where.append("bucket = ?")
        params.append(args.bucket)
    if args.min_score:
        where.append("score_data >= ?")
        params.append(args.min_score)

    rows = db.fetch_all(f"""
        SELECT c.*,
               (SELECT value FROM contacts WHERE company_id=c.id AND kind='email' LIMIT 1) AS email,
               (SELECT value FROM contacts WHERE company_id=c.id AND kind='phone' LIMIT 1) AS telefono,
               (SELECT value FROM contacts WHERE company_id=c.id AND kind='whatsapp' LIMIT 1) AS whatsapp,
               (SELECT value FROM contacts WHERE company_id=c.id AND kind='website' LIMIT 1) AS sitio_web
        FROM companies c
        WHERE {' AND '.join(where)}
        LIMIT ?
    """, tuple(params) + (args.limit or 5000,))

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

    only_bucket = tuple((args.only_bucket or "COMPLETO,PARCIAL,SOLO_EMAIL").split(","))
    result = export_hubspot_csvs(leads, run_id=args.run_id or "manual",
                                   only_bucket=only_bucket,
                                   min_tier=args.tier)
    print(json.dumps({"ok": True, **result.to_dict()},
                      ensure_ascii=False, indent=2))
    return 0


# ---------------- dedup audit ----------------

def cmd_dedup_audit(args) -> int:
    from src.db.dedup_audit import audit_dedup, get_unused_dimensions
    if args.subcommand == "report":
        rpt = audit_dedup()
        print(json.dumps({"ok": True, **rpt.to_dict()},
                          ensure_ascii=False, indent=2, default=str))
    elif args.subcommand == "unused":
        nichos = (args.nichos or "ropa,calzado,joyeria,belleza,muebles").split(",")
        dims = get_unused_dimensions(
            nichos_disponibles=nichos,
            max_suggestions=args.limit or 10,
        )
        print(json.dumps({"ok": True, "count": len(dims),
                           "sugerencias": [{"signature": d.signature(),
                                              **d.__dict__} for d in dims]},
                          ensure_ascii=False, indent=2))
    return 0


# ---------------- plans (campañas reusables manuales) ----------------

def cmd_plans(args) -> int:
    from src.skill.plans import (
        Plan, list_plans, run_plan, history, PLANS_DIR,
    )

    if args.subcommand == "list":
        plans = list_plans(args.plans_dir or PLANS_DIR)
        print(json.dumps({"ok": True, "count": len(plans),
                           "plans_dir": str(args.plans_dir or PLANS_DIR),
                           "plans": plans},
                          ensure_ascii=False, indent=2, default=str))
    elif args.subcommand == "show":
        if not args.file:
            print(json.dumps({"ok": False, "error": "falta --file"}))
            return 2
        try:
            plan = Plan.load(args.file)
            print(json.dumps({"ok": True, "plan": plan.__dict__},
                              ensure_ascii=False, indent=2, default=str))
        except Exception as e:
            print(json.dumps({"ok": False, "error": str(e)}))
            return 1
    elif args.subcommand == "run":
        if not args.file:
            print(json.dumps({"ok": False, "error": "falta --file"}))
            return 2
        try:
            # Overrides desde CLI
            overrides = {}
            if args.meta: overrides["meta"] = args.meta
            if args.zona: overrides["zona"] = args.zona
            result = run_plan(args.file, **overrides)
            print(json.dumps({"ok": True, **result},
                              ensure_ascii=False, indent=2, default=str))
        except Exception as e:
            print(json.dumps({"ok": False, "error": str(e)}))
            return 1
    elif args.subcommand == "history":
        h = history(plan_file=args.file, limit=args.limit or 20)
        print(json.dumps({"ok": True, "count": len(h), "entries": h},
                          ensure_ascii=False, indent=2, default=str))
    return 0


# ---------------- healthcheck v2 (mejorado) ----------------

def cmd_healthcheck_v2(args) -> int:
    from src.core.healthcheck import run_healthcheck, print_report
    report = run_healthcheck(meta=args.meta or 1000, fail_fast=args.fail_fast)
    if args.json:
        print(json.dumps(report.summary(), ensure_ascii=False, indent=2))
    else:
        print_report(report)
    return 0 if report.overall_ok else 1


# ---------------- checkpoint commands ----------------

def cmd_checkpoint(args) -> int:
    from src.agents.checkpoint import (
        list_pending, get_last_pending, cleanup_old, delete_checkpoint, load_checkpoint,
    )
    if args.subcommand == "list":
        pending = list_pending()
        print(json.dumps({"ok": True, "count": len(pending),
                           "pending": pending},
                          ensure_ascii=False, indent=2, default=str))
    elif args.subcommand == "show":
        if not args.job_id:
            print(json.dumps({"ok": False, "error": "falta --job-id"}))
            return 2
        state = load_checkpoint(args.job_id)
        if not state:
            print(json.dumps({"ok": False, "error": f"checkpoint {args.job_id} no encontrado"}))
            return 1
        print(json.dumps({
            "ok": True,
            "job_id": state.job_id,
            "fase_actual": state.fase_actual,
            "n_candidatos": len(state.candidatos),
            "n_leads_hunted": len(state.leads_hunted),
            "stats": state.stats,
            "errors_count": len(state.errors),
        }, ensure_ascii=False, indent=2, default=str))
    elif args.subcommand == "cleanup":
        n = cleanup_old(days=args.older_than or 30)
        print(json.dumps({"ok": True, "deleted": n}))
    elif args.subcommand == "delete":
        if not args.job_id:
            print(json.dumps({"ok": False, "error": "falta --job-id"}))
            return 2
        ok = delete_checkpoint(args.job_id)
        print(json.dumps({"ok": ok, "deleted": args.job_id}))
    elif args.subcommand == "last-pending":
        last = get_last_pending()
        print(json.dumps({"ok": True, "last_pending_job_id": last}))
    return 0


# ---------------- retry queue commands ----------------

def cmd_retry(args) -> int:
    from src.db.retry_queue import RetryQueue
    rq = RetryQueue()
    if args.subcommand == "stats":
        print(json.dumps({"ok": True, "stats": rq.stats()},
                          ensure_ascii=False, indent=2, default=str))
    elif args.subcommand == "due":
        due = rq.get_due(target=args.target, limit=args.limit or 50)
        print(json.dumps({"ok": True, "count": len(due),
                           "entries": [e.__dict__ for e in due]},
                          ensure_ascii=False, indent=2, default=str))
    return 0


# ---------------- throttle stats ----------------

def cmd_throttle(args) -> int:
    from src.core.throttle import get_throttle
    t = get_throttle()
    if args.subcommand == "stats":
        print(json.dumps({"ok": True, "stats": t.stats()},
                          ensure_ascii=False, indent=2, default=str))
    elif args.subcommand == "reset":
        if not args.domain:
            print(json.dumps({"ok": False, "error": "falta --domain"}))
            return 2
        t.reset_domain(args.domain)
        print(json.dumps({"ok": True, "msg": f"reset {args.domain}"}))
    return 0


# ---------------- supabase sync ----------------

def cmd_sync(args) -> int:
    """Sync local SQLite ↔ Supabase cloud."""
    from src.db.supabase_client import healthcheck, is_configured
    from src.db.sync import (
        push_companies, push_contacts, push_jobs, push_all, status,
    )

    if args.subcommand == "healthcheck":
        hc = healthcheck()
        print(json.dumps({"ok": hc.get("available", False), **hc},
                          ensure_ascii=False, indent=2))
        return 0 if hc.get("available") else 1

    if not is_configured():
        print(json.dumps({"ok": False,
                           "error": "Supabase no configurado. Agrega SUPABASE_URL y SUPABASE_KEY al .env"},
                          ensure_ascii=False, indent=2))
        return 2

    if args.subcommand == "status":
        st = status()
        print(json.dumps({"ok": True, **st},
                          ensure_ascii=False, indent=2, default=str))
    elif args.subcommand == "push":
        target = args.target or "all"
        incremental = not args.full
        if target == "companies":
            r = push_companies(incremental=incremental, limit=args.limit)
        elif target == "contacts":
            r = push_contacts(incremental=incremental, limit=args.limit)
        elif target == "jobs":
            r = push_jobs(incremental=incremental, limit=args.limit)
        else:
            r = push_all(incremental=incremental)
        print(json.dumps({"ok": True, "target": target, "incremental": incremental, "result": r},
                          ensure_ascii=False, indent=2, default=str))
    elif args.subcommand == "schema":
        from src.db.supabase_setup import print_setup_instructions
        print_setup_instructions()
    return 0

# ---------------- argparse ----------------

def build_parser():
    import argparse
    p = argparse.ArgumentParser(prog="fenix-cli", description="Agente Fénix v5")
    sub = p.add_subparsers(dest="command", required=True)
    fenix = sub.add_parser("fenix", help="Subcomandos Fénix")
    fsub = fenix.add_subparsers(dest="subcommand", required=True)

    # healthcheck
    p_hc = fsub.add_parser("healthcheck")
    p_hc.add_argument("--json", action="store_true", default=True)
    p_hc.add_argument("--meta", type=int, default=1000)
    p_hc.add_argument("--fail-fast", action="store_true", default=False)
    p_hc.set_defaults(func=cmd_healthcheck_v2)

    # backends
    p_bk = fsub.add_parser("backends", help="Estado de search backends")
    p_bk.set_defaults(func=cmd_backends)

    # search (test del manager)
    p_se = fsub.add_parser("search", help="Test directo del search manager")
    p_se.add_argument("query")
    p_se.add_argument("--limit", type=int, default=10)
    p_se.add_argument("--backend", default=None, help="forzar backend(s) específicos: serper,searxng,openserp,ddg")
    p_se.add_argument("--mode", default="cascade", choices=["cascade", "parallel"])
    p_se.add_argument("--allow-paid", action="store_true", default=False)
    p_se.set_defaults(func=cmd_search)

    # proxies
    p_px = fsub.add_parser("proxies", help="Gestionar pool de proxies")
    p_px.add_argument("subcommand", choices=["stats", "fetch", "check"])
    p_px.add_argument("--max-per-feed", type=int, default=50)
    p_px.set_defaults(func=cmd_proxies)

    # run
    p_run = fsub.add_parser("run", help="Ejecutar pipeline completo")
    p_run.add_argument("--nicho", required=True)
    p_run.add_argument("--meta", type=int, default=100)
    p_run.add_argument("--zona", default="nacional")
    p_run.add_argument("--modelo", choices=["B2B", "B2C", "C2C", "D2C", "C2B"])
    p_run.add_argument("--canal", default="web",
                       choices=["web", "social", "marketplace", "fisica", "mixto"])
    p_run.add_argument("--mode", choices=["quick", "standard", "deep", "enterprise"])
    p_run.add_argument("--scianes")
    p_run.add_argument("--estratos")
    p_run.add_argument("--sources")
    p_run.add_argument("--enrich-max", type=int, default=200)
    p_run.add_argument("--re-enrich-max", type=int, default=0,
                       help="# leads a rescatar via DomainFinder+EmailInferencer (0=skip)")
    p_run.add_argument("--re-enrich-find-domains", type=int, default=50)
    p_run.add_argument("--re-enrich-infer-emails", type=int, default=100)
    p_run.add_argument("--re-enrich-verify-smtp", action="store_true", default=False)
    p_run.add_argument("--deep-enrich-max", type=int, default=0,
                       help="# de leads READY/WARM a enriquecer con OSINT profundo (0=skip)")
    p_run.add_argument("--deep-enrich-tools", default="holehe,maigret,phoneinfoga",
                       help="tools: holehe,maigret,pagodo,phoneinfoga")
    p_run.add_argument("--format", default="csv,json")
    p_run.add_argument("--resume", default=None, metavar="JOB_ID",
                       help="Reanudar desde checkpoint")
    p_run.add_argument("--resume-last", action="store_true", default=False,
                       help="Reanudar el último checkpoint pendiente")
    p_run.add_argument("--no-healthcheck", action="store_true", default=False)
    p_run.add_argument("--force-checkpoint", action="store_true", default=False)
    p_run.add_argument("--json", action="store_true", default=False)
    p_run.set_defaults(func=cmd_run)

    # source
    p_src = fsub.add_parser("source")
    p_src.add_argument("source_name", choices=["denue", "ml", "dorks", "camaras"])
    p_src.add_argument("subcommand", nargs="?", default=None)
    p_src.add_argument("--actividad", default=None)
    p_src.add_argument("--entidad", default=None)
    p_src.add_argument("--estrato", default=None)
    p_src.add_argument("--sector", default=None)
    p_src.add_argument("--subsector", default=None)
    p_src.add_argument("--rama", default=None)
    p_src.add_argument("--clase", default=None)
    p_src.add_argument("--query", default="")
    p_src.add_argument("--limit", type=int, default=20)
    p_src.add_argument("--categoria", default=None)
    p_src.add_argument("--camaras", default=None)
    p_src.add_argument("--backend", default=None, help="forzar backend(s)")
    p_src.add_argument("--fetch-html", action="store_true", default=False)
    p_src.add_argument("--json", action="store_true", default=True)
    p_src.set_defaults(func=cmd_source)


    # db
    p_db = fsub.add_parser("db", help="Base de datos (stats, jobs, companies)")
    p_db.add_argument("subcommand", choices=["init", "stats", "jobs", "companies", "opt-out"])
    p_db.add_argument("--limit", type=int, default=20)
    p_db.add_argument("--bucket", choices=["COMPLETO", "SOLO_EMAIL", "SOLO_TEL", "SIN_CONTACTO", "RAW"])
    p_db.add_argument("--min-score", type=int, default=0)
    p_db.add_argument("--entidad")
    p_db.add_argument("--with-contact", action="store_true")
    p_db.add_argument("--kind", default="email")
    p_db.add_argument("--value", default="")
    p_db.add_argument("--reason", default="")
    p_db.set_defaults(func=cmd_db)

    # verify
    p_ve = fsub.add_parser("verify", help="Verificar emails (sintaxis → MX → SMTP)")
    p_ve.add_argument("emails", nargs="+")
    p_ve.add_argument("--smtp", action="store_true", default=False,
                       help="hacer handshake SMTP (lento, puede ser bloqueado)")
    p_ve.set_defaults(func=cmd_verify)

    # harvest
    p_ha = fsub.add_parser("harvest", help="OSINT: theHarvester + EmailHarvester sobre un dominio")
    p_ha.add_argument("domain")
    p_ha.set_defaults(func=cmd_harvest)

    # antibot
    p_ab = fsub.add_parser("antibot", help="Fetcher con escalado anti-bot")
    p_ab.add_argument("subcommand", choices=["stats", "fetch"])
    p_ab.add_argument("url", nargs="?", default="")
    p_ab.add_argument("--max-level", type=int, default=3)
    p_ab.set_defaults(func=cmd_antibot)


    # phone
    p_ph = fsub.add_parser("phone", help="Validar teléfonos MX (Google libphonenumber)")
    p_ph.add_argument("numbers", nargs="+")
    p_ph.set_defaults(func=cmd_phone)

    # tech
    p_tc = fsub.add_parser("tech", help="Detectar tech stack de un sitio (Shopify, Klaviyo, MercadoPago, etc.)")
    p_tc.add_argument("url")
    p_tc.set_defaults(func=cmd_tech)

    # robots
    p_rb = fsub.add_parser("robots", help="Verificar si robots.txt permite fetchear un URL")
    p_rb.add_argument("url")
    p_rb.set_defaults(func=cmd_robots)


    # osint
    p_os = fsub.add_parser("osint", help="Wrappers OSINT externos (holehe/maigret/pagodo/phoneinfoga/spiderfoot)")
    p_os.add_argument("subcommand", choices=["stats", "holehe", "maigret", "pagodo", "phoneinfoga", "spiderfoot"])
    p_os.add_argument("--target", default=None, help="email/username/domain/phone según el comando")
    p_os.add_argument("--max-dorks", type=int, default=10)
    p_os.set_defaults(func=cmd_osint)

    # budget
    p_bg = fsub.add_parser("budget", help="Cuotas de herramientas externas")
    p_bg.set_defaults(func=cmd_budget)


    # ask (Discovery)
    p_ask = fsub.add_parser("ask", help="Discovery Protocol (texto libre -> plan)")
    p_ask.add_argument("text", nargs="?", default="")
    p_ask.add_argument("--session-id", default=None)
    p_ask.add_argument("--field", default=None)
    p_ask.add_argument("--value", default=None)
    p_ask.add_argument("--interactive", action="store_true", default=False)
    p_ask.add_argument("--run", action="store_true", default=False)
    p_ask.add_argument("--enrich-max", type=int, default=None)
    p_ask.add_argument("--deep-enrich-max", type=int, default=None)
    p_ask.set_defaults(func=cmd_ask)

    # exclusions
    p_ex = fsub.add_parser("exclusions", help="Cat\u00e1logo de exclusiones Skydropx")
    p_ex.add_argument("subcommand", choices=["list", "check", "dork"])
    p_ex.add_argument("--empresa", default=None)
    p_ex.add_argument("--dominio", default=None)
    p_ex.add_argument("--scian", default=None)
    p_ex.add_argument("--estrato", default=None)
    p_ex.add_argument("--include-large", action="store_true", default=False)
    p_ex.add_argument("--include-medianas-grandes", action="store_true", default=False)
    p_ex.set_defaults(func=cmd_exclusions)

    # icp
    p_ic = fsub.add_parser("icp", help="Clasificar un lead (ICP_1/ICP_2/ICP_3/NO_ICP)")
    p_ic.add_argument("--empresa", required=True)
    p_ic.add_argument("--scian", default="")
    p_ic.add_argument("--tamano", default="")
    p_ic.add_argument("--source", default="")
    p_ic.add_argument("--giro", default="")
    p_ic.add_argument("--metadata", default="")
    p_ic.set_defaults(func=cmd_icp)


    # trends
    p_tr = fsub.add_parser("trends", help="Detectar tendencias actuales (Google/ML/TikTok/Amazon)")
    p_tr.add_argument("subcommand", choices=["google", "all"])
    p_tr.add_argument("--sources", default=None, help="csv: google,mercadolibre,tiktok,amazon")
    p_tr.add_argument("--suggest-niches", action="store_true", default=False)
    p_tr.add_argument("--no-cache", action="store_true", default=False)
    p_tr.set_defaults(func=cmd_trends)

    # events
    p_ev = fsub.add_parser("events", help="Eventos activos + campanas")
    p_ev.add_argument("subcommand", choices=["active", "find", "suggest"])
    p_ev.add_argument("--query", default="")
    p_ev.set_defaults(func=cmd_events)

    # agency
    p_ag = fsub.add_parser("agency", help="Detecta agencia detras de campana")
    p_ag.add_argument("--dominio", default=None)
    p_ag.add_argument("--text", default=None)
    p_ag.add_argument("--campana", default=None)
    p_ag.add_argument("--max-docs", type=int, default=3)
    p_ag.set_defaults(func=cmd_agency)

    # hubspot
    p_hs = fsub.add_parser("hubspot", help="Export CSV listos para import manual a HubSpot")
    p_hs.add_argument("--bucket", default=None,
                       choices=["COMPLETO","PARCIAL","SOLO_EMAIL","SOLO_TEL","SIN_CONTACTO","RAW"])
    p_hs.add_argument("--only-bucket", default=None,
                       help="CSV de buckets a incluir, ej: COMPLETO,PARCIAL")
    p_hs.add_argument("--min-score", type=int, default=0)
    p_hs.add_argument("--limit", type=int, default=5000)
    p_hs.add_argument("--tier", choices=["PREMIUM","GOLD","SILVER"], default=None,
                       help="Filtro tiered (sobreescribe --bucket si se especifica)")
    p_hs.add_argument("--run-id", default=None)
    p_hs.set_defaults(func=cmd_hubspot)

    # dedup-audit
    p_da = fsub.add_parser("dedup-audit", help="Reporte de dedup + sugerencias de campana")
    p_da.add_argument("subcommand", choices=["report", "unused"])
    p_da.add_argument("--nichos", default=None, help="CSV de nichos disponibles")
    p_da.add_argument("--limit", type=int, default=10)
    p_da.set_defaults(func=cmd_dedup_audit)


    # plans
    p_pl = fsub.add_parser("plans", help="Plans YAML (campanas reusables, manual)")
    p_pl.add_argument("subcommand", choices=["list", "show", "run", "history"])
    p_pl.add_argument("file", nargs="?", default=None, help="path al .yaml")
    p_pl.add_argument("--plans-dir", default=None)
    p_pl.add_argument("--meta", type=int, default=None, help="override meta")
    p_pl.add_argument("--zona", default=None, help="override zona")
    p_pl.add_argument("--limit", type=int, default=20)
    p_pl.set_defaults(func=cmd_plans)


    # checkpoint
    p_cp = fsub.add_parser("checkpoint", help="Checkpoints y resume del pipeline")
    p_cp.add_argument("subcommand", choices=["list","show","delete","cleanup","last-pending"])
    p_cp.add_argument("--job-id", default=None)
    p_cp.add_argument("--older-than", type=int, default=30, help="dias")
    p_cp.set_defaults(func=cmd_checkpoint)

    # retry
    p_rt = fsub.add_parser("retry", help="Cola de retries para leads incompletos")
    p_rt.add_argument("subcommand", choices=["stats","due"])
    p_rt.add_argument("--target", default=None)
    p_rt.add_argument("--limit", type=int, default=50)
    p_rt.set_defaults(func=cmd_retry)

    # throttle
    p_th = fsub.add_parser("throttle", help="Auto-throttling adaptativo")
    p_th.add_argument("subcommand", choices=["stats","reset"])
    p_th.add_argument("--domain", default=None)
    p_th.set_defaults(func=cmd_throttle)


    # sync (Supabase)
    p_sy = fsub.add_parser("sync", help="Sync local SQLite ↔ Supabase cloud")
    p_sy.add_argument("subcommand", choices=["healthcheck", "status", "push", "schema"])
    p_sy.add_argument("--target", default=None, choices=["companies", "contacts", "jobs", "all"],
                       help="qué tabla sincronizar (default: all)")
    p_sy.add_argument("--full", action="store_true", default=False,
                       help="forzar sync completo (no incremental)")
    p_sy.add_argument("--limit", type=int, default=None)
    p_sy.set_defaults(func=cmd_sync)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
