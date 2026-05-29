"""
Google Dorks vía Search Backends Manager.

Esta fuente NO sabe qué backend usa — solo pide queries al manager y el
manager elige (Serper → SearXNG → OpenSERP → DDG) según disponibilidad,
costo y políticas configuradas.

Descubre:
- Shopify, Tiendanube, WooCommerce, VTEX, Magento, Wix, Jumpseller
- Sitios con intent "envíos a toda la república" (más alta calidad para Skydropx)
- Instagram Shops MX
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import requests

from src.core.config import settings
from src.core.models import RawRecord, ResearchPlan
from src.core.user_agents import random_ua
from src.sources.search_backends import (
    SearchBackendManager, get_default_manager,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15
DEFAULT_DELAY = 1.5

# ---------------- Dorks por categoría ----------------

DORKS = {
    "shopify": [
        '"cdn.shopify.com" site:.mx',
        '"powered by Shopify" "México"',
        '"Shopify" "envíos a todo México"',
        'inurl:/products/ "MXN" site:.mx',
    ],
    "tiendanube": [
        '"tiendanube.com" site:.mx',
        '"Mi tienda en línea con Tiendanube"',
        '"d22fxaf9e50qjs.cloudfront.net"',
    ],
    "woocommerce": [
        'inurl:/?add-to-cart= site:.mx',
        '"wp-content/plugins/woocommerce" site:.mx',
        '"WooCommerce" "envío a toda la república"',
    ],
    "vtex": [
        '"vtexassets.com" site:.mx',
        '"vtexcommercestable" site:.mx',
    ],
    "envios_mx": [
        '"envíos a toda la república" site:.mx',
        '"enviamos a todo México"',
        '"envío gratis en compras mayores"',
        '"hacemos envíos a toda la república"',
        '"cotiza tu envío" site:.mx',
        '"envíos foráneos" site:.mx',
    ],
    "instagram_shops_mx": [
        'site:instagram.com "tiendamx" "envíos" "México"',
        'site:instagram.com "hechoenmexico" "WhatsApp" "envíos"',
        'site:instagram.com "emprendedoramx" "pedidos" "México"',
    ],
}

# ---------------- Detectores de plataforma ----------------

PLATFORM_SIGNALS = {
    "shopify": [re.compile(r"cdn\.shopify\.com", re.I),
                re.compile(r"myshopify\.com", re.I),
                re.compile(r"window\.ShopifyAnalytics", re.I)],
    "tiendanube": [re.compile(r"tiendanube\.com", re.I),
                   re.compile(r"d22fxaf9e50qjs\.cloudfront\.net", re.I)],
    "woocommerce": [re.compile(r"wp-content/plugins/woocommerce", re.I),
                    re.compile(r"wc-block", re.I)],
    "vtex": [re.compile(r"vtexassets\.com", re.I)],
    "magento": [re.compile(r"Magento_", re.I)],
}

ENVIOS_SIGNALS = [
    re.compile(r"env[íi]os\s+(a\s+)?toda\s+la\s+rep[úu]blica", re.I),
    re.compile(r"enviamos\s+a\s+todo\s+m[ée]xico", re.I),
    re.compile(r"env[íi]os?\s+nacionales", re.I),
    re.compile(r"paqueter[íi]a", re.I),
]

PAQUETERIAS_COMPETENCIA = {
    "estafeta": r"\bestafeta\b",
    "dhl": r"\bdhl\b",
    "fedex": r"\bfedex\b",
    "99minutos": r"99\s*minutos",
    "paquetexpress": r"paquetexpress",
    "redpack": r"\bredpack\b",
    "ups": r"\bups\b",
    "correos_mexico": r"correos\s+de\s+m[ée]xico",
    "ivoy": r"\bivoy\b",
    "skydropx": r"\bskydropx\b",
}


@dataclass
class DorksConfig:
    categorias: list[str] = field(default_factory=lambda: list(DORKS.keys()))
    queries_extra: list[str] = field(default_factory=list)
    results_per_dork: int = 30
    delay_sec: float = DEFAULT_DELAY
    fetch_html: bool = True
    prefer_backends: list[str] | None = None    # ['serper'] para forzar uno
    avoid_paid: bool = True


def extract_domain(url: str) -> str | None:
    try:
        p = urlparse(url)
        return p.netloc.lower().lstrip("www.") or None
    except Exception:  # noqa: BLE001
        return None


def fetch_html(url: str, timeout: int = DEFAULT_TIMEOUT) -> str | None:
    try:
        r = requests.get(url, timeout=timeout, allow_redirects=True,
                         headers={"User-Agent": random_ua(),
                                  "Accept": "text/html"})
        if r.ok and "text/html" in r.headers.get("Content-Type", ""):
            return r.text[:200_000]
    except Exception as e:  # noqa: BLE001
        logger.debug("fetch %s err: %s", url, e)
    return None


def detect_platform(html: str) -> str | None:
    for plat, signals in PLATFORM_SIGNALS.items():
        if any(s.search(html) for s in signals):
            return plat
    return None


def has_envios_intent(html: str) -> bool:
    return any(s.search(html) for s in ENVIOS_SIGNALS)


def detect_paqueterias(html: str) -> list[str]:
    return [n for n, p in PAQUETERIAS_COMPETENCIA.items() if re.search(p, html, re.I)]


def build_rawrecord(domain: str, url: str, html: str | None, categoria: str,
                     backend_used: str = "") -> RawRecord:
    plat = detect_platform(html) if html else None
    envios = has_envios_intent(html) if html else (categoria == "envios_mx")
    paqueterias = detect_paqueterias(html) if html else []
    return RawRecord(
        source=f"dorks_{categoria}",
        empresa=domain,
        sitio_web=f"https://{domain}",
        metadata={
            "dork_categoria": categoria,
            "url_origen": url,
            "backend_used": backend_used,
            "plataforma_detectada": plat,
            "envios_intent": envios,
            "paqueterias_mencionadas": paqueterias,
            "ya_usa_competencia": bool(set(paqueterias) - {"skydropx"}),
        },
    )


# ---------------- Filtros anti-ruido ----------------

NOISE_DOMAINS = {
    "facebook.com", "youtube.com", "twitter.com", "x.com", "tiktok.com",
    "duckduckgo.com", "bing.com", "google.com", "google.com.mx", "tuscupones.com.mx",
    "amazon.com", "amazon.com.mx", "mercadolibre.com.mx",
    "reverso.net", "linguee.com", "wordreference.com",
    "wikipedia.org", "wiktionary.org",
}


def _is_noise(domain: str, categoria: str) -> bool:
    if categoria == "instagram_shops_mx" and domain == "instagram.com":
        return False  # los queremos en esa categoría
    return any(domain == d or domain.endswith("." + d) for d in NOISE_DOMAINS)


# ---------------- Entry point ----------------

def search(plan: ResearchPlan, manager: SearchBackendManager | None = None) -> list[RawRecord]:
    config = DorksConfig(
        categorias=plan.extras.get("dork_categorias") or list(DORKS.keys()),
        queries_extra=plan.extras.get("dork_queries_extra", []),
        results_per_dork=plan.extras.get("dork_limit", 30),
        fetch_html=plan.extras.get("dork_fetch_html", True),
        prefer_backends=plan.extras.get("dork_prefer_backends"),
        avoid_paid=plan.extras.get("dork_avoid_paid", settings.search_avoid_paid),
    )

    mgr = manager or get_default_manager()
    available = [b.name for b in mgr.available_backends()]
    logger.info("Dorks usando backends disponibles: %s", available)
    if not available:
        logger.error("Ningún search backend disponible. Configura SERPER_API_KEY o SEARXNG_URL.")
        return []

    seen_domains: set[str] = set()
    results: list[RawRecord] = []

    for categoria in config.categorias:
        dorks_list = DORKS.get(categoria, []) + config.queries_extra
        logger.info("Dorks cat=%s queries=%s", categoria, len(dorks_list))

        for dork in dorks_list:
            try:
                hits = mgr.search(
                    dork,
                    limit=config.results_per_dork,
                    country="mx",
                    prefer=config.prefer_backends,
                    avoid_paid=config.avoid_paid,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("Dork '%s' falló en todos los backends: %s", dork[:50], e)
                continue

            backend_used = hits[0].source if hits else "none"
            logger.info("  [%s] '%s' → %s resultados",
                        backend_used, dork[:50], len(hits))

            for h in hits:
                url = h.url
                if not url:
                    continue
                dom = extract_domain(url)
                if not dom or dom in seen_domains:
                    continue
                if _is_noise(dom, categoria):
                    continue
                seen_domains.add(dom)
                html = fetch_html(url) if config.fetch_html else None
                results.append(build_rawrecord(dom, url, html, categoria, backend_used))
                time.sleep(config.delay_sec)

            time.sleep(config.delay_sec * 2)

    logger.info("Dorks total únicos: %s dominios", len(results))
    return results


__all__ = ["search", "DORKS", "PLATFORM_SIGNALS", "DorksConfig"]
