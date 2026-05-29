"""
Google Maps source con wrapper opcional sobre google-maps-scraper (omkarcloud).

Estrategia tiered:
1. Si `google-maps-scraper` (omkarcloud) está instalado → usar (mejor calidad: emails, redes, etc.)
2. Si Botasaurus está instalado → usar versión propia con Botasaurus
3. Fallback: Playwright/Patchright si están instalados
4. Último recurso: documentar que el usuario debe instalar uno

GOOGLE MAPS CAP: Google limita a ~120 resultados por búsqueda.
Estrategia: dividir por colonia/municipio para multiplicar.
"""
from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from src.core.config import settings
from src.core.models import RawRecord, ResearchPlan

logger = logging.getLogger(__name__)


# ---------------- Detección de wrappers disponibles ----------------

def _has_module(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


HAS_GOOGLE_MAPS_SCRAPER = _has_module("google_maps_scraper")
HAS_BOTASAURUS = _has_module("botasaurus")
HAS_PATCHRIGHT = _has_module("patchright")
HAS_NODRIVER = _has_module("nodriver")
HAS_PLAYWRIGHT = _has_module("playwright")


@dataclass
class GMapsConfig:
    queries: list[str] = field(default_factory=list)
    # Ej: ["zapaterías en Coyoacán CDMX", "zapaterías en Iztapalapa CDMX"]
    max_per_query: int = 100
    headless: bool = True
    delay_sec: float = 2.0
    backend: str = "auto"   # auto|omkarcloud|botasaurus|patchright|nodriver|playwright|external


# ---------------- Wrapper omkarcloud/google-maps-scraper ----------------

def _scrape_via_omkarcloud(query: str, max_results: int) -> list[dict]:
    """
    Wrapper sobre https://github.com/omkarcloud/google-maps-scraper.

    Devuelve dicts con campos: name, address, phone, website, email, social_links, etc.
    """
    try:
        from google_maps_scraper import scrape  # type: ignore
    except ImportError:
        return []
    try:
        results = scrape(
            queries=[query],
            max=max_results,
            headless=True,
            lang="es",
            country="MX",
        )
        return results or []
    except Exception as e:  # noqa: BLE001
        logger.warning("omkarcloud err en '%s': %s", query[:50], e)
        return []


# ---------------- Wrapper Botasaurus (DIY si user lo tiene) ----------------

def _scrape_via_botasaurus(query: str, max_results: int) -> list[dict]:
    """Implementación mínima usando Botasaurus + selectors Google Maps."""
    try:
        from botasaurus.browser import browser  # type: ignore
    except ImportError:
        return []

    @browser(headless=True, block_images=True)
    def _do_scrape(driver, data):
        url = f"https://www.google.com/maps/search/{data['q'].replace(' ', '+')}/?hl=es&gl=mx"
        driver.get(url)
        time.sleep(3)
        # Scroll para cargar más
        for _ in range(min(10, data["max"] // 10)):
            driver.execute_script(
                "document.querySelector('div[role=\"feed\"]').scrollTop += 2000"
            )
            time.sleep(1.5)
        # Selectors aproximados - Google rota su DOM
        items = driver.find_elements_by_css_selector('div[role="article"]')
        out = []
        for item in items[: data["max"]]:
            try:
                out.append({
                    "name": item.find_element_by_css_selector("div.fontHeadlineSmall").text,
                    "_raw_text": item.text,
                })
            except Exception:  # noqa: BLE001
                pass
        return out

    try:
        return _do_scrape({"q": query, "max": max_results}) or []
    except Exception as e:  # noqa: BLE001
        logger.warning("botasaurus err: %s", e)
        return []


# ---------------- Wrapper CLI externo (gosom o omkarcloud binary) ----------------

def _scrape_via_external_cli(query: str, max_results: int) -> list[dict]:
    """
    Si el usuario tiene el binario CLI de algún scraper instalado en PATH.
    Ej: gosom/google-maps-scraper compilado.
    """
    # Buscar binarios conocidos
    for cmd_name in ("google-maps-scraper", "gmaps-scraper", "gosom"):
        try:
            subprocess.run([cmd_name, "--version"], capture_output=True, timeout=3)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        # Si lo encontramos, intentar usarlo
        try:
            out_file = Path(f"output/.tmp_gmaps_{int(time.time())}.json")
            out_file.parent.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(
                [cmd_name, "-input", query, "-results", str(max_results),
                 "-json", str(out_file)],
                capture_output=True, timeout=180,
            )
            if result.returncode == 0 and out_file.exists():
                import json
                data = json.loads(out_file.read_text())
                out_file.unlink()
                return data if isinstance(data, list) else []
        except Exception as e:  # noqa: BLE001
            logger.debug("CLI %s err: %s", cmd_name, e)
    return []


# ---------------- Backend selector ----------------

def _pick_backend(preference: str = "auto") -> str:
    if preference != "auto":
        return preference
    if HAS_GOOGLE_MAPS_SCRAPER:
        return "omkarcloud"
    if HAS_BOTASAURUS:
        return "botasaurus"
    if HAS_PATCHRIGHT:
        return "patchright"
    if HAS_PLAYWRIGHT:
        return "playwright"
    return "external"


def _execute_scrape(query: str, max_results: int, backend: str) -> list[dict]:
    if backend == "omkarcloud":
        return _scrape_via_omkarcloud(query, max_results)
    if backend == "botasaurus":
        return _scrape_via_botasaurus(query, max_results)
    if backend == "external":
        return _scrape_via_external_cli(query, max_results)
    # patchright/playwright/nodriver: stubs para implementación futura
    logger.warning(
        "Backend '%s' aún no implementado. Instala google-maps-scraper "
        "(`pip install google-maps-scraper`) o usa otro backend.",
        backend,
    )
    return []


# ---------------- Normalización a RawRecord ----------------

def _to_rawrecord(item: dict, query: str, backend: str) -> RawRecord:
    """Normaliza el dict de cualquier backend a RawRecord."""
    name = item.get("name") or item.get("title") or item.get("nombre", "")
    phone = (
        item.get("phone") or item.get("telefono")
        or item.get("phone_number") or ""
    ).strip()
    email = (item.get("email") or "").strip().lower() or None
    website = (item.get("website") or item.get("site") or "").strip() or None
    address = item.get("address") or item.get("direccion") or ""
    social = item.get("social_links") or item.get("social") or {}

    return RawRecord(
        source=f"maps_{backend}",
        empresa=name,
        nombre_comercial=name,
        telefono=phone or None,
        email=email,
        whatsapp=None,
        sitio_web=website,
        instagram=social.get("instagram") if isinstance(social, dict) else None,
        facebook=social.get("facebook") if isinstance(social, dict) else None,
        direccion=address,
        municipio=item.get("city") or item.get("municipio"),
        estado=item.get("state") or item.get("estado"),
        longitud=item.get("longitude") or item.get("lng"),
        latitud=item.get("latitude") or item.get("lat"),
        giro_descripcion=item.get("category") or item.get("type"),
        metadata={
            "gmaps_query": query,
            "gmaps_backend": backend,
            "gmaps_rating": item.get("rating"),
            "gmaps_reviews_count": item.get("reviews_count") or item.get("user_ratings_total"),
            "gmaps_place_id": item.get("place_id"),
            "gmaps_url": item.get("url") or item.get("link"),
        },
    )


# ---------------- Entry point ----------------

def search(plan: ResearchPlan) -> list[RawRecord]:
    config = GMapsConfig(
        queries=plan.extras.get("gmaps_queries") or _default_queries_from_plan(plan),
        max_per_query=plan.extras.get("gmaps_max_per_query", 100),
        backend=plan.extras.get("gmaps_backend", "auto"),
    )
    backend = _pick_backend(config.backend)
    logger.info("Google Maps backend seleccionado: %s", backend)

    if backend not in ("omkarcloud", "botasaurus", "external"):
        logger.warning(
            "No hay backend de Google Maps disponible.\n"
            "Instala uno de estos (todos gratis):\n"
            "  pip install google-maps-scraper            # ⭐ recomendado (omkarcloud)\n"
            "  pip install botasaurus                     # alternativa\n"
            "  go install github.com/gosom/google-maps-scraper@latest"
        )
        return []

    out: list[RawRecord] = []
    for q in config.queries:
        logger.info("GMaps query: '%s'", q)
        items = _execute_scrape(q, config.max_per_query, backend)
        logger.info("  → %s resultados", len(items))
        for it in items:
            out.append(_to_rawrecord(it, q, backend))
        time.sleep(config.delay_sec)

    return out


def _default_queries_from_plan(plan: ResearchPlan) -> list[str]:
    """Genera queries por defecto desde nicho + zona del plan."""
    nicho = plan.nicho or "tienda"
    zona = plan.zona if plan.zona != "nacional" else "México"
    return [f"{nicho} en {zona}"]


__all__ = [
    "search", "GMapsConfig",
    "HAS_GOOGLE_MAPS_SCRAPER", "HAS_BOTASAURUS",
    "HAS_PATCHRIGHT", "HAS_NODRIVER", "HAS_PLAYWRIGHT",
]
