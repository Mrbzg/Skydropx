"""
Fetcher con escalado anti-bot:
  Nivel 0  →  requests + UA rotativo + proxies      (default, rápido)
  Nivel 1  →  Patchright (Playwright parchado)      (Cloudflare ligero)
  Nivel 2  →  Nodriver (CDP nativo, sin Selenium)   (anti-bot fuerte)
  Nivel 3  →  Botasaurus                            (cf_clearance persistente)

Cada nivel se intenta solo si el anterior falla con bloqueo detectado.
Las librerías son opcionales; si no están instaladas, se omite el nivel.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Literal

import requests

from src.core.config import settings
from src.core.proxy_pool import get_default_pool
from src.core.robots import can_fetch as robots_can_fetch
from src.core.user_agents import random_ua

logger = logging.getLogger(__name__)

# Detección de bloqueos comunes
BLOCK_SIGNALS = [
    "captcha", "cf-challenge", "challenge-platform", "cloudflare",
    "perimeterx", "datadome", "akamai-bm", "incapsula", "blocked",
    "access denied", "are you a robot", "human verification",
]


@dataclass
class FetchResult:
    url: str
    status_code: int = 0
    html: str = ""
    final_url: str = ""
    blocked: bool = False
    level_used: int = 0
    duration_ms: int = 0
    error: str = ""


def _detect_block(html: str, status_code: int) -> bool:
    if status_code in (403, 429, 503):
        return True
    if not html or len(html) < 500:
        return True
    low = html[:5000].lower()
    return any(sig in low for sig in BLOCK_SIGNALS)


# ---------------- Detección de librerías opcionales ----------------

def _try_import(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


HAS_PATCHRIGHT = _try_import("patchright")
HAS_NODRIVER = _try_import("nodriver")
HAS_BOTASAURUS = _try_import("botasaurus")
HAS_PLAYWRIGHT = _try_import("playwright")


# ---------------- Niveles ----------------

def _fetch_l0_requests(url: str, timeout: int = 15) -> FetchResult:
    """Nivel 0: requests con UA rotativo + (opcional) proxy del pool."""
    t0 = time.time()
    result = FetchResult(url=url, level_used=0)
    headers = {
        "User-Agent": random_ua(),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "es-MX,es;q=0.9,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    proxies = None
    pool = get_default_pool() if settings.use_free_proxies or settings.has_proxies() else None
    proxy = pool.get() if pool else None
    if proxy:
        proxies = proxy.to_requests_dict()

    try:
        r = requests.get(url, headers=headers, proxies=proxies,
                         timeout=timeout, allow_redirects=True)
        result.status_code = r.status_code
        result.html = r.text[:300_000]
        result.final_url = r.url
        result.blocked = _detect_block(result.html, r.status_code)
        if proxy and pool:
            (pool.mark_success if not result.blocked else pool.mark_failure)(proxy)
    except Exception as e:  # noqa: BLE001
        result.error = str(e)
        if proxy and pool:
            pool.mark_failure(proxy)
    result.duration_ms = int((time.time() - t0) * 1000)
    return result


def _fetch_l1_patchright(url: str, timeout: int = 30) -> FetchResult:
    """Nivel 1: Patchright (Playwright parchado contra detección)."""
    t0 = time.time()
    result = FetchResult(url=url, level_used=1)
    if not HAS_PATCHRIGHT:
        result.error = "patchright_not_installed"
        return result
    try:
        from patchright.sync_api import sync_playwright  # type: ignore
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=random_ua(),
                locale="es-MX",
            )
            page = context.new_page()
            page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
            result.html = page.content()[:300_000]
            result.status_code = 200
            result.final_url = page.url
            result.blocked = _detect_block(result.html, 200)
            browser.close()
    except Exception as e:  # noqa: BLE001
        result.error = str(e)
    result.duration_ms = int((time.time() - t0) * 1000)
    return result


def _fetch_l2_nodriver(url: str, timeout: int = 30) -> FetchResult:
    """Nivel 2: Nodriver (CDP nativo, sin Selenium/webdrivers)."""
    t0 = time.time()
    result = FetchResult(url=url, level_used=2)
    if not HAS_NODRIVER:
        result.error = "nodriver_not_installed"
        return result
    try:
        import asyncio
        import nodriver as uc  # type: ignore

        async def _run():
            browser = await uc.start(headless=True)
            page = await browser.get(url)
            await page.wait_for_ready_state("complete", timeout=timeout)
            html = await page.get_content()
            await browser.stop()
            return html, page.url

        html, final = asyncio.run(_run())
        result.html = (html or "")[:300_000]
        result.status_code = 200
        result.final_url = final
        result.blocked = _detect_block(result.html, 200)
    except Exception as e:  # noqa: BLE001
        result.error = str(e)
    result.duration_ms = int((time.time() - t0) * 1000)
    return result


def _fetch_l3_botasaurus(url: str, timeout: int = 30) -> FetchResult:
    """Nivel 3: Botasaurus (cf_clearance persistente entre sesiones)."""
    t0 = time.time()
    result = FetchResult(url=url, level_used=3)
    if not HAS_BOTASAURUS:
        result.error = "botasaurus_not_installed"
        return result
    try:
        from botasaurus.request import request as bs_request  # type: ignore

        @bs_request(use_stealth=True)
        def _do(req, data):
            return req.get(data["url"], timeout=data["timeout"])

        resp = _do({"url": url, "timeout": timeout})
        result.html = (resp.text if hasattr(resp, "text") else "")[:300_000]
        result.status_code = getattr(resp, "status_code", 200)
        result.final_url = getattr(resp, "url", url)
        result.blocked = _detect_block(result.html, result.status_code)
    except Exception as e:  # noqa: BLE001
        result.error = str(e)
    result.duration_ms = int((time.time() - t0) * 1000)
    return result


# ---------------- Fetch escalado ----------------

def fetch(
    url: str,
    max_level: int = 3,
    auto_escalate: bool = True,
    timeout: int = 15,
    respect_robots: bool = True,
) -> FetchResult:
    """
    Intenta fetchear `url` escalando niveles solo si el anterior fue bloqueado.

    Si respect_robots=True (default), verifica robots.txt antes de cualquier fetch.
    """
    if respect_robots and not robots_can_fetch(url):
        logger.info("Robots.txt disallow: %s", url)
        return FetchResult(url=url, blocked=True, error="robots_disallow")

    # Nivel 0 siempre
    r = _fetch_l0_requests(url, timeout=timeout)
    if not r.blocked or not auto_escalate or max_level == 0:
        return r

    logger.info("Bloqueo detectado en L0 para %s, escalando", url[:60])

    if max_level >= 1 and HAS_PATCHRIGHT:
        r1 = _fetch_l1_patchright(url, timeout=timeout * 2)
        if not r1.blocked and r1.html:
            return r1

    if max_level >= 2 and HAS_NODRIVER:
        r2 = _fetch_l2_nodriver(url, timeout=timeout * 2)
        if not r2.blocked and r2.html:
            return r2

    if max_level >= 3 and HAS_BOTASAURUS:
        r3 = _fetch_l3_botasaurus(url, timeout=timeout * 2)
        if not r3.blocked and r3.html:
            return r3

    # Si nada funcionó, devolver el último intento
    logger.warning("Todos los niveles bloqueados para %s", url[:60])
    return r


def fetch_stats() -> dict:
    """Devuelve qué backends anti-bot están disponibles."""
    return {
        "l0_requests": True,
        "l1_patchright": HAS_PATCHRIGHT,
        "l2_nodriver": HAS_NODRIVER,
        "l3_botasaurus": HAS_BOTASAURUS,
        "l_alt_playwright": HAS_PLAYWRIGHT,
    }


__all__ = [
    "fetch", "fetch_stats", "FetchResult",
    "HAS_PATCHRIGHT", "HAS_NODRIVER", "HAS_BOTASAURUS", "HAS_PLAYWRIGHT",
]
