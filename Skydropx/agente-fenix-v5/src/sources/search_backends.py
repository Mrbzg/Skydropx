"""
Search Backends Manager — abstracción unificada para múltiples motores de búsqueda.

Backends soportados (en orden de preferencia configurable):
1. Serper.dev      → Google directo, 2,500 créditos gratis lifetime, luego $0.30/1K
2. SearXNG         → metabúsqueda self-hosted, 100% gratis perpetuo
3. OpenSERP        → Google directo self-hosted con rotación de proxies/UA
4. DuckDuckGo HTML → siempre disponible como red de seguridad

Política inteligente:
- TIER 1 (Serper): queries críticas/limitadas si hay créditos disponibles
- TIER 2 (SearXNG): default para volumen si está corriendo
- TIER 3 (OpenSERP): fallback con proxies si está disponible
- TIER 4 (DDG): siempre disponible como último recurso

Tracking de créditos Serper persistido en data/serper_credits.json.
Circuit-breaker por backend: 3 fallos seguidos → quarantine 5 min.
"""
from __future__ import annotations

import json
import logging
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal
from urllib.parse import parse_qs, unquote, urlparse

import requests

from src.core.config import settings
from src.core.user_agents import random_ua
from src.core.proxy_pool import ProxyPool, get_default_pool

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15

# ---------------- Modelo unificado ----------------

@dataclass
class SearchResult:
    url: str
    title: str = ""
    snippet: str = ""
    position: int = 0
    source: str = ""        # 'serper', 'searxng', 'openserp', 'ddg'
    raw: dict = field(default_factory=dict)


# ---------------- Backend abstracto ----------------

class SearchBackend(ABC):
    name: str = "abstract"
    cost_per_query_usd: float = 0.0
    priority: int = 100      # menor = más prioritario
    rate_limit_per_min: int = 60

    def __init__(self):
        self.fails = 0
        self.quarantined_until = 0.0
        self.queries_made = 0
        self.last_query_at = 0.0

    @abstractmethod
    def is_available(self) -> bool:
        """¿Este backend está configurado y disponible?"""

    @abstractmethod
    def _do_search(self, query: str, limit: int, country: str) -> list[SearchResult]:
        """Implementación específica."""

    def search(self, query: str, limit: int = 20, country: str = "mx") -> list[SearchResult]:
        if time.time() < self.quarantined_until:
            raise BackendQuarantined(f"{self.name} en cuarentena")
        # Rate limit suave
        delta = time.time() - self.last_query_at
        min_delta = 60.0 / max(1, self.rate_limit_per_min)
        if delta < min_delta:
            time.sleep(min_delta - delta)
        try:
            results = self._do_search(query, limit, country)
            self.last_query_at = time.time()
            self.queries_made += 1
            self.fails = 0
            return results
        except Exception as e:
            self.fails += 1
            if self.fails >= 3:
                self.quarantined_until = time.time() + 300
                logger.warning("%s quarantine 5min por %s fallos", self.name, self.fails)
                self.fails = 0
            raise


class BackendQuarantined(Exception):
    pass


# ---------------- Serper.dev ----------------

SERPER_CREDITS_PATH = Path("data/serper_credits.json")
SERPER_DEFAULT_CREDITS = 2500
SERPER_BASE = "https://google.serper.dev/search"


SERPER_STRATEGY_DEFAULT = "reserve"   # reserve|fallback|critical|priority|disabled


class SerperBackend(SearchBackend):
    """
    Serper.dev — Google directo, súper rápido, 2.5K queries gratis lifetime.
    Después: $0.30 por 1K queries (~$0.0003/query).

    Tracking de créditos persistente en data/serper_credits.json para evitar
    sorpresas. El usuario configura el TOTAL inicial; restamos por cada query.
    """
    name = "serper"
    cost_per_query_usd = 0.0003
    priority = 99                         # ÚLTIMO por default (RESERVE) — preserva los 2,500 créditos
    rate_limit_per_min = 100

    def __init__(self):
        super().__init__()
        self.api_key = settings.serper_api_key if hasattr(settings, "serper_api_key") else None
        self.credits_remaining = self._load_credits()
        self.stop_when_paid = getattr(settings, "serper_stop_when_paid", True)
        # Strategy: reserve|fallback|critical|priority|disabled
        self.strategy = (getattr(settings, "serper_strategy", None)
                          or SERPER_STRATEGY_DEFAULT).lower()

    def is_available(self) -> bool:
        if not self.api_key:
            return False
        if self.stop_when_paid and self.credits_remaining <= 0:
            return False
        if self.strategy == "disabled":
            return False
        return True

    def is_available_for(self, context: str = "normal") -> bool:
        """
        Tabla strategy × context:
          strategy=priority  → siempre disponible
          strategy=reserve   → context ∈ {fallback,critical,forced} (no normal)
          strategy=fallback  → context ∈ {fallback,critical,forced} (igual que reserve)
          strategy=critical  → context ∈ {critical,forced} (más restrictivo)
          strategy=disabled  → nunca
        """
        if not self.is_available():
            return False
        if self.strategy == "priority":
            return True
        if self.strategy in ("reserve", "fallback"):
            return context in ("fallback", "critical", "forced")
        if self.strategy == "critical":
            return context in ("critical", "forced")
        return False

    def _load_credits(self) -> int:
        if not SERPER_CREDITS_PATH.exists():
            return SERPER_DEFAULT_CREDITS
        try:
            data = json.loads(SERPER_CREDITS_PATH.read_text())
            return int(data.get("remaining", SERPER_DEFAULT_CREDITS))
        except Exception:  # noqa: BLE001
            return SERPER_DEFAULT_CREDITS

    @staticmethod
    def _simplify_for_free_tier(query: str) -> str:
        """
        Simplifica un dork avanzado a query plana para Serper free.
        site:instagram.com "ropa" "mty" → "ropa mty instagram"
        site:tiktok.com "X" "envíos" → "X envíos tiktok"
        """
        import re
        site_match = re.search(r'site:(\S+)', query)
        site_brand = ""
        if site_match:
            domain = site_match.group(1).lower()
            # Extraer marca (instagram, tiktok, facebook, etc.)
            for brand in ("instagram", "tiktok", "facebook", "linkedin", "youtube"):
                if brand in domain:
                    site_brand = brand
                    break
        # Quitar operadores
        cleaned = re.sub(r'site:\S+', '', query)
        cleaned = re.sub(r'filetype:\S+', '', cleaned)
        cleaned = re.sub(r'intitle:\S+', '', cleaned)
        cleaned = re.sub(r'inurl:\S+', '', cleaned)
        cleaned = re.sub(r'intext:\S+', '', cleaned)
        # Quitar comillas (queda como query plano)
        cleaned = cleaned.replace('"', "")
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        if site_brand and site_brand not in cleaned.lower():
            cleaned = f"{cleaned} {site_brand}".strip()
        return cleaned

    def _save_credits(self) -> None:
        SERPER_CREDITS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SERPER_CREDITS_PATH.write_text(json.dumps({
            "remaining": self.credits_remaining,
            "last_query_at": self.last_query_at,
            "queries_made": self.queries_made,
        }, indent=2))

    def _do_search(self, query: str, limit: int, country: str) -> list[SearchResult]:
        if self.stop_when_paid and self.credits_remaining <= 0:
            raise BackendQuarantined("Serper: créditos free agotados (stop_when_paid=True)")

        headers = {"X-API-KEY": self.api_key, "Content-Type": "application/json"}

        def _post(q: str):
            payload = {"q": q, "num": min(limit, 100), "gl": country, "hl": "es"}
            return requests.post(SERPER_BASE, headers=headers, json=payload,
                                  timeout=DEFAULT_TIMEOUT)

        r = _post(query)

        # Serper FREE no permite operadores avanzados (site:, intitle:, filetype:).
        # Si falla con 400 "Query pattern not allowed", simplificar y reintentar.
        if r.status_code == 400 and b"not allowed for free" in r.content:
            simplified = self._simplify_for_free_tier(query)
            if simplified != query:
                logger.info("Serper free: simplificando dork '%s' → '%s'",
                            query[:60], simplified[:60])
                r = _post(simplified)

        if r.status_code == 400:
            logger.warning("Serper 400 — query=%r body=%s",
                            query[:80], r.text[:200])
        r.raise_for_status()
        data = r.json()

        self.credits_remaining -= 1
        self._save_credits()

        results: list[SearchResult] = []
        for i, item in enumerate(data.get("organic", [])[:limit]):
            results.append(SearchResult(
                url=item.get("link", ""),
                title=item.get("title", ""),
                snippet=item.get("snippet", ""),
                position=item.get("position", i + 1),
                source="serper",
                raw=item,
            ))
        return results


# ---------------- SearXNG ----------------

class SearXNGBackend(SearchBackend):
    name = "searxng"
    cost_per_query_usd = 0.0
    priority = 1                          # PRIMARY: gratis perpetuo + sin rate-limit
    rate_limit_per_min = 600      # con limiter:false en settings.yml

    def __init__(self):
        super().__init__()
        self.base_url = settings.searxng_url

    def is_available(self) -> bool:
        if not self.base_url:
            return False
        # Verificación rápida (sin gastar tiempo si está caído)
        try:
            r = requests.get(f"{self.base_url.rstrip('/')}/healthz", timeout=3)
            return r.ok
        except Exception:  # noqa: BLE001
            return False

    def _do_search(self, query: str, limit: int, country: str) -> list[SearchResult]:
        params = {
            "q": query, "format": "json",
            "engines": "google,bing,brave,duckduckgo,startpage,qwant",
            "safesearch": 0,
            "language": "es-MX",
        }
        r = requests.get(
            f"{self.base_url.rstrip('/')}/search",
            params=params, timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": random_ua()},
        )
        r.raise_for_status()
        data = r.json()
        results: list[SearchResult] = []
        for i, item in enumerate(data.get("results", [])[:limit]):
            results.append(SearchResult(
                url=item.get("url", ""),
                title=item.get("title", ""),
                snippet=item.get("content", ""),
                position=i + 1,
                source="searxng",
                raw=item,
            ))
        return results


# ---------------- OpenSERP ----------------

class OpenSERPBackend(SearchBackend):
    """
    OpenSERP self-hosted — Google/Bing/Yandex sin API key.
    Repo: https://github.com/karust/openserp

    Levantar: docker run -d -p 7000:7000 karust/openserp serve -a 0.0.0.0 -p 7000
    Endpoint: GET http://localhost:7000/google/search?text=...&lang=es&limit=10
    """
    name = "openserp"
    cost_per_query_usd = 0.0
    priority = 3                          # Google directo si SearXNG no basta
    rate_limit_per_min = 30      # Google directo es propenso a bloqueo

    def __init__(self):
        super().__init__()
        self.base_url = (
            getattr(settings, "openserp_url", None)
            or "http://localhost:7000"
        )
        self.proxy_pool: ProxyPool | None = None
        if getattr(settings, "openserp_use_proxies", False):
            self.proxy_pool = get_default_pool()

    def is_available(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/google/search",
                             params={"text": "test", "limit": 1},
                             timeout=3)
            return r.ok
        except Exception:  # noqa: BLE001
            return False

    def _do_search(self, query: str, limit: int, country: str) -> list[SearchResult]:
        params = {"text": query, "lang": "es", "limit": limit}
        kwargs = {
            "params": params, "timeout": DEFAULT_TIMEOUT,
            "headers": {"User-Agent": random_ua()},
        }
        if self.proxy_pool:
            proxy = self.proxy_pool.get()
            if proxy:
                kwargs["proxies"] = proxy.to_requests_dict()
                try:
                    r = requests.get(f"{self.base_url}/google/search", **kwargs)
                    r.raise_for_status()
                    self.proxy_pool.mark_success(proxy)
                except Exception:
                    self.proxy_pool.mark_failure(proxy)
                    raise
            else:
                r = requests.get(f"{self.base_url}/google/search", **kwargs)
                r.raise_for_status()
        else:
            r = requests.get(f"{self.base_url}/google/search", **kwargs)
            r.raise_for_status()

        data = r.json()
        results: list[SearchResult] = []
        items = data if isinstance(data, list) else data.get("results", [])
        for i, item in enumerate(items[:limit]):
            results.append(SearchResult(
                url=item.get("url", "") or item.get("link", ""),
                title=item.get("title", ""),
                snippet=item.get("description", "") or item.get("snippet", ""),
                position=i + 1,
                source="openserp",
                raw=item,
            ))
        return results


# ---------------- DuckDuckGo HTML (siempre disponible) ----------------

class DuckDuckGoBackend(SearchBackend):
    name = "ddg"
    cost_per_query_usd = 0.0
    priority = 2                          # Siempre disponible — segundo después de SearXNG
    rate_limit_per_min = 20        # DDG no le gusta volumen alto

    def is_available(self) -> bool:
        return True

    def _do_search(self, query: str, limit: int, country: str) -> list[SearchResult]:
        import re as _re
        r = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query, "kl": f"{country}-es" if country else "wt-wt"},
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": random_ua()},
        )
        r.raise_for_status()
        results: list[SearchResult] = []
        # Parser ligero del HTML de DDG
        for i, m in enumerate(
            _re.finditer(
                r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>([^<]+)</a>',
                r.text,
            )
        ):
            href, title = m.group(1), m.group(2)
            if "duckduckgo.com/l/?" in href and "uddg=" in href:
                qs = parse_qs(urlparse(href).query)
                href = unquote((qs.get("uddg") or [href])[0])
            results.append(SearchResult(
                url=href, title=title, snippet="",
                position=i + 1, source="ddg",
            ))
            if len(results) >= limit:
                break
        return results


# ---------------- Manager (orquestador inteligente) ----------------

@dataclass
class BackendStats:
    name: str
    available: bool
    priority: int
    queries_made: int
    fails: int
    quarantined: bool
    cost_per_query: float
    extras: dict = field(default_factory=dict)


class SearchBackendManager:
    """
    Decide qué backend usar para cada query según prioridad + disponibilidad.

    Modos:
    - 'cascade'   : intenta TIER 1 → si falla, TIER 2, etc. (default)
    - 'parallel'  : llama varios y mergea (más costoso)
    - 'cheapest'  : elige el de menor costo disponible (default ahorra Serper)
    - 'best'      : elige el de mayor priority/calidad disponible
    """

    def __init__(self, mode: Literal["cascade", "cheapest", "best", "parallel"] = "cascade"):
        self.mode = mode
        self.backends: list[SearchBackend] = [
            SerperBackend(),
            SearXNGBackend(),
            OpenSERPBackend(),
            DuckDuckGoBackend(),
        ]
        # Cache de availability (no checar cada query)
        self._availability_cache: dict[str, tuple[float, bool]] = {}
        self._availability_ttl = 60.0  # segundos

    def _is_available(self, b: SearchBackend) -> bool:
        cached = self._availability_cache.get(b.name)
        if cached and time.time() - cached[0] < self._availability_ttl:
            return cached[1]
        ok = b.is_available()
        self._availability_cache[b.name] = (time.time(), ok)
        return ok

    def available_backends(self) -> list[SearchBackend]:
        return [b for b in self.backends if self._is_available(b)
                and time.time() >= b.quarantined_until]

    def search(
        self,
        query: str,
        limit: int = 20,
        country: str = "mx",
        prefer: list[str] | None = None,
        avoid_paid: bool = True,
        context: str = "normal",          # normal|critical|forced|fallback
    ) -> list[SearchResult]:
        """
        Búsqueda inteligente con fallback automático.

        context:
          - normal:   uso típico (Serper se salta si strategy='reserve')
          - critical: marca esta query como prioritaria (Serper SÍ se considera)
          - forced:   forzar uso de Serper aunque strategy lo bloquee
          - fallback: usar como último recurso después de los gratis
        """
        candidates = self.available_backends()
        # Filtrar Serper según strategy + context
        candidates = [
            b for b in candidates
            if not isinstance(b, SerperBackend) or b.is_available_for(context)
        ]

        if not candidates:
            logger.error("Ningún backend disponible. Verifica config.")
            return []

        # Filtrar pagados si avoid_paid (Serper sin créditos = no disponible ya;
        # esto es por si SerperBackend está en modo "paga después")
        if avoid_paid:
            candidates = [b for b in candidates if b.cost_per_query_usd == 0
                          or (b.name == "serper" and b.credits_remaining > 0)]

        # Aplicar preferencia explícita
        if prefer:
            ordered = ([b for b in candidates if b.name in prefer]
                       + [b for b in candidates if b.name not in prefer])
        elif self.mode == "cheapest":
            ordered = sorted(candidates, key=lambda b: (b.cost_per_query_usd, b.priority))
        elif self.mode == "best":
            ordered = sorted(candidates, key=lambda b: b.priority)
        else:  # cascade
            ordered = sorted(candidates, key=lambda b: b.priority)

        last_error = None
        for backend in ordered:
            try:
                results = backend.search(query, limit=limit, country=country)
                if results:
                    logger.debug("✓ %s → %s resultados para '%s'",
                                 backend.name, len(results), query[:50])
                    return results
                logger.debug("%s devolvió 0 resultados, probando siguiente", backend.name)
            except BackendQuarantined as e:
                logger.debug("%s quarantined: %s", backend.name, e)
                last_error = e
            except Exception as e:  # noqa: BLE001
                logger.warning("%s falló: %s", backend.name, e)
                last_error = e

        if last_error:
            logger.error("Todos los backends fallaron. Último error: %s", last_error)
            # Auto-fallback: si era normal, reintenta con context='fallback' (activa Serper si strategy='fallback')
            if context == "normal":
                logger.info("Auto-escalando a context=fallback (puede activar Serper)")
                return self.search(query, limit=limit, country=country,
                                     prefer=prefer, avoid_paid=avoid_paid,
                                     context="fallback")
        return []

    def search_parallel(
        self, query: str, limit: int = 20, country: str = "mx",
        backends: list[str] | None = None,
    ) -> dict[str, list[SearchResult]]:
        """Ejecuta en varios backends y devuelve dict por backend (para comparar)."""
        target_names = backends or [b.name for b in self.available_backends()]
        out: dict[str, list[SearchResult]] = {}
        for b in self.available_backends():
            if b.name not in target_names:
                continue
            try:
                out[b.name] = b.search(query, limit, country)
            except Exception as e:  # noqa: BLE001
                out[b.name] = []
                logger.debug("%s parallel err: %s", b.name, e)
        return out

    def stats(self) -> list[dict]:
        out = []
        for b in self.backends:
            extras = {}
            if isinstance(b, SerperBackend):
                extras["credits_remaining"] = b.credits_remaining
                extras["estimated_cost_extra_usd"] = round(b.queries_made * b.cost_per_query_usd, 4)
            out.append({
                "name": b.name,
                "available": self._is_available(b),
                "priority": b.priority,
                "queries_made": b.queries_made,
                "fails": b.fails,
                "quarantined": time.time() < b.quarantined_until,
                "cost_per_query_usd": b.cost_per_query_usd,
                **extras,
            })
        return out


# Singleton de conveniencia
_default_manager: SearchBackendManager | None = None


def get_default_manager() -> SearchBackendManager:
    global _default_manager
    if _default_manager is None:
        _default_manager = SearchBackendManager()
    return _default_manager


__all__ = [
    "SearchResult", "SearchBackend", "BackendQuarantined",
    "SerperBackend", "SearXNGBackend", "OpenSERPBackend", "DuckDuckGoBackend",
    "SearchBackendManager", "get_default_manager",
]
