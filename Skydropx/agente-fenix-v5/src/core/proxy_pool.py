"""
Pool de proxies con rotación, health-check y circuit-breaker.

Soporta:
- Lista estática desde archivo (formato: host:port o user:pass@host:port)
- Tor (socks5://localhost:9050) — gratis si lo tienes corriendo
- Listas públicas (proxyscrape.com, free-proxy-list.net) — descarga automática
- Sin proxy (modo directo, default si no hay nada configurado)

Política: round-robin con quarantine de proxies que fallan 3 veces seguidas.
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Iterable

import requests

from src.core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_HEALTH = 5
DEFAULT_QUARANTINE_SEC = 600  # 10 min
DEFAULT_MAX_FAILS = 3

# Listas públicas de proxies gratis (cambian seguido)
FREE_PROXY_FEEDS = [
    "https://api.proxyscrape.com/v3/free-proxy-list/get?request=displayproxies&protocol=http&timeout=5000&country=all",
    "https://www.proxy-list.download/api/v1/get?type=http",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
]


@dataclass
class Proxy:
    url: str                   # "http://host:port" o "socks5://host:port"
    label: str = ""            # "tor", "static-3", "feed-proxyscrape", etc.
    fails: int = 0
    quarantined_until: float = 0.0
    successful_uses: int = 0

    def to_requests_dict(self) -> dict[str, str]:
        return {"http": self.url, "https": self.url}

    def is_available(self) -> bool:
        return time.time() >= self.quarantined_until


class ProxyPool:
    """Pool thread-safe con políticas configurables."""

    def __init__(
        self,
        proxies: Iterable[Proxy] | None = None,
        max_fails: int = DEFAULT_MAX_FAILS,
        quarantine_sec: int = DEFAULT_QUARANTINE_SEC,
        policy: str = "round_robin",  # round_robin | random
    ):
        self.proxies: list[Proxy] = list(proxies or [])
        self.max_fails = max_fails
        self.quarantine_sec = quarantine_sec
        self.policy = policy
        self._idx = 0
        self._lock = Lock()

    # ---------- Loading ----------

    @classmethod
    def from_env(cls) -> "ProxyPool":
        """Construye el pool según .env."""
        proxies: list[Proxy] = []

        # Tor (si está configurado HTTP_PROXY=socks5://...)
        if settings.http_proxy and "9050" in (settings.http_proxy or ""):
            proxies.append(Proxy(url=settings.http_proxy, label="tor"))

        # Lista estática desde archivo
        plist = Path("data/proxies.txt")
        if plist.exists():
            for i, line in enumerate(plist.read_text().splitlines()):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                url = line if line.startswith(("http://", "https://", "socks")) else f"http://{line}"
                proxies.append(Proxy(url=url, label=f"static-{i}"))

        if proxies:
            logger.info("ProxyPool: %s proxies cargados", len(proxies))
        return cls(proxies=proxies)

    def fetch_free_proxies(self, feeds: list[str] | None = None, max_per_feed: int = 50) -> int:
        """Descarga proxies públicos de listas gratis. Retorna cuántos agregó."""
        feeds = feeds or FREE_PROXY_FEEDS
        before = len(self.proxies)
        existing = {p.url for p in self.proxies}

        for feed in feeds:
            try:
                r = requests.get(feed, timeout=15)
                if not r.ok:
                    continue
                count = 0
                for line in r.text.splitlines():
                    line = line.strip()
                    if not line or ":" not in line:
                        continue
                    url = f"http://{line}"
                    if url in existing:
                        continue
                    self.proxies.append(Proxy(url=url, label=f"feed-{feed.split('//')[1][:20]}"))
                    existing.add(url)
                    count += 1
                    if count >= max_per_feed:
                        break
                logger.info("ProxyPool: %s nuevos de %s", count, feed[:60])
            except Exception as e:  # noqa: BLE001
                logger.debug("Feed err %s: %s", feed[:60], e)

        return len(self.proxies) - before

    # ---------- Selection ----------

    def get(self) -> Proxy | None:
        """Devuelve el siguiente proxy disponible (no en cuarentena)."""
        with self._lock:
            if not self.proxies:
                return None
            available = [p for p in self.proxies if p.is_available()]
            if not available:
                logger.warning("ProxyPool: todos en cuarentena, esperando %ss",
                               self.quarantine_sec)
                return None
            if self.policy == "random":
                return random.choice(available)
            # round_robin
            self._idx = (self._idx + 1) % len(available)
            return available[self._idx]

    def mark_success(self, proxy: Proxy) -> None:
        with self._lock:
            proxy.successful_uses += 1
            proxy.fails = 0

    def mark_failure(self, proxy: Proxy) -> None:
        with self._lock:
            proxy.fails += 1
            if proxy.fails >= self.max_fails:
                proxy.quarantined_until = time.time() + self.quarantine_sec
                logger.info("Proxy %s en cuarentena %ss", proxy.label, self.quarantine_sec)
                proxy.fails = 0

    # ---------- Health-check ----------

    def health_check(self, test_url: str = "https://httpbin.org/ip",
                    timeout: int = DEFAULT_TIMEOUT_HEALTH) -> dict:
        """Verifica cuáles proxies funcionan ahora mismo."""
        ok = []
        bad = []
        for p in self.proxies:
            try:
                r = requests.get(test_url, proxies=p.to_requests_dict(), timeout=timeout)
                if r.ok:
                    ok.append(p.label)
                else:
                    bad.append(p.label)
            except Exception:  # noqa: BLE001
                bad.append(p.label)
        return {"ok": len(ok), "bad": len(bad), "ok_labels": ok[:20], "bad_labels": bad[:20]}

    def stats(self) -> dict:
        return {
            "total": len(self.proxies),
            "available": sum(1 for p in self.proxies if p.is_available()),
            "quarantined": sum(1 for p in self.proxies if not p.is_available()),
            "top_users": sorted(
                [{"label": p.label, "uses": p.successful_uses} for p in self.proxies],
                key=lambda x: -x["uses"],
            )[:5],
        }


# Singleton global
_default_pool: ProxyPool | None = None


def get_default_pool() -> ProxyPool:
    global _default_pool
    if _default_pool is None:
        _default_pool = ProxyPool.from_env()
    return _default_pool


__all__ = ["Proxy", "ProxyPool", "get_default_pool", "FREE_PROXY_FEEDS"]
