"""
Social Shops source — descubre tiendas/marcas D2C en TikTok, Instagram y Facebook.

Estrategia 100% via dorks (sin scraping frágil de las plataformas):
1. Genera dorks específicos por red social + nicho + zona
2. Usa SearchBackendManager (SearXNG/DDG primero, Serper como reserve)
3. Extrae handles, URLs de bio, info pública visible en los snippets
4. Devuelve RawRecords con `source='social_<plataforma>'` y metadata enriquecida

NO usa TikTokApi/Instagram-scraper (frágiles, alto mantenimiento).
SÍ usa dorks `site:tiktok.com`, `site:instagram.com`, `site:facebook.com`.

Para Skydropx: resuelve el caso "leads de D2C en TikTok/IG Monterrey",
porque esos vendedores son EXACTAMENTE el ICP_1_PYME / D2C.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import urlparse

from src.core.models import RawRecord, ResearchPlan
from src.core.config import settings

logger = logging.getLogger(__name__)


# ============================================================
# DORKS por plataforma + intent comercial MX
# ============================================================

TIKTOK_DORKS = {
    "tiendas_general": [
        'site:tiktok.com "{nicho}" "{zona}" "envíos"',
        'site:tiktok.com "{nicho}" "{zona}" "pedidos"',
        'site:tiktok.com "{nicho}" "México" "envíos a toda la república"',
    ],
    "intent_compra": [
        'site:tiktok.com "{nicho}" "compra aquí"',
        'site:tiktok.com "{nicho}" "link in bio"',
        'site:tiktok.com "{nicho}" "tiendamx"',
    ],
    "hashtags_comerciales_mx": [
        'site:tiktok.com "{nicho}" "hechoenmexico"',
        'site:tiktok.com "{nicho}" "emprendedoramx"',
        'site:tiktok.com "tienda{nicho}"',
    ],
}

INSTAGRAM_DORKS = {
    "tiendas_d2c_mx": [
        'site:instagram.com "{nicho}" "{zona}" "envíos a toda la república"',
        'site:instagram.com "{nicho}" "{zona}" "WhatsApp"',
        'site:instagram.com "{nicho}" "México" "pedidos"',
        'site:instagram.com "{nicho}" "Mx" "link in bio"',
    ],
    "intent_comercial": [
        'site:instagram.com "{nicho}" "MXN"',
        'site:instagram.com "{nicho}" "checkout"',
        'site:instagram.com "{nicho}" "tienda online"',
    ],
    "hashtags_emprendedores_mx": [
        'site:instagram.com "{nicho}" "hechoenmexico"',
        'site:instagram.com "{nicho}" "emprendedoramexicana"',
        'site:instagram.com "{nicho}" "{zona}" "mxn"',
    ],
}

FACEBOOK_DORKS = {
    "pages_tiendas_mx": [
        'site:facebook.com/pages "{nicho}" "{zona}" "envíos"',
        'site:facebook.com "{nicho}" "{zona}" "página de tienda"',
        'site:facebook.com "{nicho}" "Mexico" "envíos a toda la república"',
    ],
    "marketplaces": [
        # Vendedores en Facebook Marketplace por nicho
        'site:facebook.com/marketplace "{nicho}" "{zona}"',
    ],
    "grupos_emprendedores": [
        'site:facebook.com/groups "vendo {nicho}" "{zona}"',
        'site:facebook.com/groups "tienda {nicho}" "México"',
    ],
}


# ============================================================
# Regex de extracción de info de snippets/URLs
# ============================================================

# Handles
TIKTOK_HANDLE_RE = re.compile(r"tiktok\.com/@([a-zA-Z0-9_\.]{2,30})", re.I)
INSTAGRAM_HANDLE_RE = re.compile(r"instagram\.com/([a-zA-Z0-9_\.]{2,30})/?(?:\?|$|\")", re.I)
FACEBOOK_PAGE_RE = re.compile(r"facebook\.com/(?:pg/)?([a-zA-Z0-9\.\-]{3,60})/?", re.I)

# Contactos en bio/snippet
PHONE_MX_INLINE_RE = re.compile(
    r"(?:\+?52[\s\-\.]?)?(?:1[\s\-\.]?)?\d{2,3}[\s\-\.]?\d{3,4}[\s\-\.]?\d{4}"
)
WHATSAPP_LINK_RE = re.compile(r"(?:wa\.me|api\.whatsapp\.com/send.*phone=)/?([+\d]{10,15})", re.I)
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
URL_IN_BIO_RE = re.compile(r"https?://[a-zA-Z0-9.\-]+\.[a-z]{2,}(?:/[^\s\"']*)?", re.I)

# Detección de hashtags comerciales MX
COMMERCIAL_HASHTAGS_MX = [
    "tiendamx", "tiendamexico", "hechoenmexico", "emprendedoramexicana",
    "emprendedoramx", "modamexicana", "d2c", "directoalconsumidor",
    "ventaonline", "comprasenlinea", "tiendaonline", "shopmx",
]


# ============================================================
# Configuración
# ============================================================

@dataclass
class SocialShopsConfig:
    platforms: list[str] = field(default_factory=lambda: ["tiktok", "instagram", "facebook"])
    dork_categories: list[str] | None = None    # None = todas
    limit_per_dork: int = 15
    fetch_html: bool = False                     # las redes bloquean fetch directo, mejor solo snippets
    use_serper_for_critical: bool = False


# ============================================================
# Generación de dorks
# ============================================================

def _normalize_zona(zona: str) -> tuple[str, list[str]]:
    """'Monterrey' → ('Monterrey', ['mty', 'nuevo leon', 'nl'])"""
    aliases_map = {
        "monterrey": ["mty", "nuevo leon", "nl", "monterrey"],
        "cdmx": ["cdmx", "ciudad de mexico", "df", "mexico city"],
        "guadalajara": ["gdl", "guadalajara", "jalisco", "jal", "zmg"],
        "puebla": ["puebla", "pue"],
        "queretaro": ["queretaro", "qro"],
        "merida": ["merida", "yucatan"],
        "tijuana": ["tijuana", "tj", "baja california"],
    }
    z_norm = zona.lower().strip()
    for canonical, aliases in aliases_map.items():
        if z_norm in aliases:
            return canonical, aliases
    return zona, [zona]


def build_dorks_for_query(
    platform: str,
    nicho: str,
    zona: str,
    categories: list[str] | None = None,
) -> list[str]:
    """Genera dorks específicos para una plataforma."""
    catalog = {
        "tiktok": TIKTOK_DORKS,
        "instagram": INSTAGRAM_DORKS,
        "facebook": FACEBOOK_DORKS,
    }
    if platform not in catalog:
        return []
    dorks_dict = catalog[platform]
    cats = categories or list(dorks_dict.keys())

    _, zona_aliases = _normalize_zona(zona)
    # Usa la primera alias y opcionalmente la segunda
    primary_zona = zona_aliases[0]

    out = []
    for cat in cats:
        patterns = dorks_dict.get(cat, [])
        for pat in patterns:
            dork = pat.replace("{nicho}", nicho).replace("{zona}", primary_zona)
            out.append(dork)
    return out


# ============================================================
# Extracción desde resultados de búsqueda
# ============================================================

# Regex adicional: capturar handle desde URLs /p/, /reel/, /tv/ no es trivial
# (la URL no contiene el handle, está en el snippet/title)
INSTAGRAM_HANDLE_PROFILE_RE = re.compile(
    r'instagram\.com/([a-zA-Z0-9_\.]{2,30})/?(?:\?|$|/$)', re.I
)
# Handle visible en snippet ("xxx_xxx on May 6", "@regio_boutique...")
INSTAGRAM_HANDLE_SNIPPET_RE = re.compile(
    r"(?:^|\s)@?([a-z0-9_\.]{3,30})\s+(?:on|posted|shared)", re.I
)
HANDLE_IN_TEXT_RE = re.compile(r"@([a-zA-Z0-9_\.]{3,30})")


def _extract_handle_from_url(url: str, platform: str) -> str | None:
    """Captura handle PRIMERO desde URL si es perfil directo."""
    if platform == "tiktok":
        m = TIKTOK_HANDLE_RE.search(url)
        return f"@{m.group(1)}" if m else None
    if platform == "instagram":
        # /p/ y /reel/ no tienen el handle en la URL, lo extraemos del snippet aparte
        if "/p/" in url or "/reel/" in url or "/tv/" in url:
            return None
        m = INSTAGRAM_HANDLE_PROFILE_RE.search(url)
        if m:
            handle = m.group(1)
            # Filtrar palabras genéricas que no son handles
            if handle.lower() in ("p", "reel", "tv", "explore", "tags",
                                    "stories", "accounts", "directory"):
                return None
            return f"@{handle}"
        return None
    if platform == "facebook":
        m = FACEBOOK_PAGE_RE.search(url)
        if m:
            handle = m.group(1)
            if handle.lower() in ("pages", "groups", "marketplace", "p",
                                    "watch", "events"):
                return None
            return handle
        return None
    return None


def _extract_handle_from_snippet(snippet: str, title: str, platform: str) -> str | None:
    """Si la URL no tiene handle (post directo), busca en snippet/title."""
    full = (title or "") + " " + (snippet or "")
    # Pattern 1: "username on May 6"
    m = INSTAGRAM_HANDLE_SNIPPET_RE.search(full)
    if m:
        handle = m.group(1)
        if handle.lower() not in ("the", "this", "and", "for", "you", "more"):
            return f"@{handle}"
    # Pattern 2: @username en el texto
    m = HANDLE_IN_TEXT_RE.search(full)
    if m:
        handle = m.group(1)
        if handle.lower() not in ("instagram", "tiktok", "facebook"):
            return f"@{handle}"
    return None


def _extract_contacts_from_snippet(snippet: str) -> dict:
    """Extrae email/tel/wa/url visibles en el snippet de la búsqueda."""
    out = {"emails": [], "phones": [], "whatsapps": [], "urls_in_bio": []}
    if not snippet:
        return out
    out["emails"] = sorted(set(EMAIL_RE.findall(snippet)))[:3]
    out["phones"] = sorted(set(
        re.sub(r"\D", "", m.group(0))[-10:]
        for m in PHONE_MX_INLINE_RE.finditer(snippet)
        if len(re.sub(r"\D", "", m.group(0))) >= 10
    ))[:3]
    out["whatsapps"] = sorted(set(WHATSAPP_LINK_RE.findall(snippet)))[:2]
    out["urls_in_bio"] = sorted(set(
        u for u in URL_IN_BIO_RE.findall(snippet)
        if not any(noise in u.lower() for noise in (
            "tiktok.com", "instagram.com", "facebook.com",
            "google.com", "duckduckgo.com",
        ))
    ))[:3]
    return out


def _detect_hashtags(text: str) -> list[str]:
    """Detecta hashtags comerciales MX en el texto."""
    found = []
    for h in COMMERCIAL_HASHTAGS_MX:
        if h in text.lower():
            found.append(h)
    return found


def _build_raw_record(
    platform: str,
    handle: str,
    url: str,
    title: str,
    snippet: str,
    nicho: str,
    zona: str,
    backend_used: str,
) -> RawRecord:
    contacts = _extract_contacts_from_snippet(snippet + " " + title)
    hashtags = _detect_hashtags(snippet + " " + title)

    # Email (prioridad: del snippet)
    email = contacts["emails"][0] if contacts["emails"] else None

    # Phone (priorizar WhatsApp ya que es lo más usado en redes)
    phone = None
    whatsapp = None
    if contacts["whatsapps"]:
        whatsapp = contacts["whatsapps"][0]
        phone = whatsapp  # el WA es número válido también
    elif contacts["phones"]:
        phone = contacts["phones"][0]

    # Sitio web (link in bio)
    sitio_web = contacts["urls_in_bio"][0] if contacts["urls_in_bio"] else None

    # Nombre comercial: el handle limpio
    empresa = handle.lstrip("@") if handle else (title[:60] if title else "")

    return RawRecord(
        source=f"social_{platform}",
        empresa=empresa,
        nombre_comercial=empresa,
        email=email,
        telefono=phone,
        whatsapp=whatsapp,
        sitio_web=sitio_web,
        instagram=url if platform == "instagram" else None,
        facebook=url if platform == "facebook" else None,
        metadata={
            "social_platform": platform,
            "social_handle": handle,
            "social_url": url,
            "social_title": title[:200],
            "social_snippet_preview": snippet[:300],
            "hashtags_commercial_mx": hashtags,
            "backend_used": backend_used,
            "nicho_buscado": nicho,
            "zona_buscada": zona,
            "intent_envios": "envío" in snippet.lower() or "envios" in snippet.lower(),
            "has_wa_link": bool(contacts["whatsapps"]),
            "has_email_in_bio": bool(contacts["emails"]),
        },
    )


# ============================================================
# Entry point
# ============================================================

def search(plan: ResearchPlan) -> list[RawRecord]:
    """
    Descubre tiendas/marcas en TikTok, Instagram y Facebook según nicho+zona.

    Usa el SearchBackendManager (SearXNG/DDG primero, Serper como reserve).
    """
    config = SocialShopsConfig(
        platforms=plan.extras.get("social_platforms") or ["tiktok", "instagram", "facebook"],
        dork_categories=plan.extras.get("social_dork_categories"),
        limit_per_dork=plan.extras.get("social_limit_per_dork", 15),
        use_serper_for_critical=plan.extras.get("social_use_serper_critical", False),
    )

    nicho = plan.nicho or ""
    zona = plan.zona if plan.zona != "nacional" else "México"

    if not nicho:
        logger.error("social_shops requiere plan.nicho")
        return []

    try:
        from src.sources.search_backends import get_default_manager
        mgr = get_default_manager()
    except Exception as e:  # noqa: BLE001
        logger.error("SearchBackendManager no disponible: %s", e)
        return []

    seen_handles: set[str] = set()
    results: list[RawRecord] = []
    # IMPORTANTE: social_shops sin search backend = 0 resultados.
    # Marcamos context='fallback' por default (activa Serper si gratis fallan)
    # o 'critical' si el usuario lo pidió explícito.
    context = "critical" if config.use_serper_for_critical else "fallback"

    for platform in config.platforms:
        dorks = build_dorks_for_query(platform, nicho, zona, config.dork_categories)
        logger.info("social_shops/%s: %s dorks generados", platform, len(dorks))

        for dork in dorks:
            try:
                hits = mgr.search(dork, limit=config.limit_per_dork,
                                    country="mx", context=context)
            except Exception as e:  # noqa: BLE001
                logger.debug("social_shops dork err: %s", e)
                continue

            backend_used = hits[0].source if hits else "none"

            for hit in hits:
                handle = _extract_handle_from_url(hit.url, platform)
                if not handle:
                    # Fallback: extraer desde snippet (caso /p/, /reel/, etc.)
                    handle = _extract_handle_from_snippet(
                        hit.snippet or "", hit.title or "", platform,
                    )
                if not handle:
                    continue
                # Dedup por handle dentro de la misma plataforma
                key = f"{platform}:{handle.lower()}"
                if key in seen_handles:
                    continue
                seen_handles.add(key)

                rec = _build_raw_record(
                    platform=platform, handle=handle,
                    url=hit.url, title=hit.title or "",
                    snippet=hit.snippet or "",
                    nicho=nicho, zona=zona,
                    backend_used=backend_used,
                )
                results.append(rec)

    logger.info("social_shops total: %s handles únicos en %s plataformas",
                len(results), len(config.platforms))
    return results


__all__ = [
    "search", "SocialShopsConfig",
    "TIKTOK_DORKS", "INSTAGRAM_DORKS", "FACEBOOK_DORKS",
    "build_dorks_for_query", "COMMERCIAL_HASHTAGS_MX",
]
