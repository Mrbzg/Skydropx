"""
Enforcement real de robots.txt usando urllib.robotparser (stdlib).

Cumple LFPDPPP + buenas prácticas de scraping:
- Antes de hacer fetch a un dominio, verifica si el path está permitido
- Cachea robots.txt por dominio (no re-descargar para cada URL)
- Respeta `Crawl-delay` si está definido
- Opción `RESPECT_ROBOTS_TXT=false` en .env para casos donde el usuario tiene
  permiso explícito (ej: scraping de su propio sitio)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from threading import Lock
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from src.core.config import settings
from src.core.user_agents import random_ua

logger = logging.getLogger(__name__)


@dataclass
class RobotsDecision:
    allowed: bool
    crawl_delay: float = 0.0
    reason: str = ""


class RobotsChecker:
    """Cache de robots.txt por dominio + decisión por URL."""

    def __init__(self, respect: bool | None = None,
                 user_agent_token: str = "FenixBot"):
        self.respect = settings.respect_robots_txt if respect is None else respect
        self.ua_token = user_agent_token
        self._cache: dict[str, RobotFileParser] = {}
        self._lock = Lock()

    def _get_parser(self, domain: str) -> RobotFileParser | None:
        with self._lock:
            if domain in self._cache:
                return self._cache[domain]
            rp = RobotFileParser()
            rp.set_url(f"https://{domain}/robots.txt")
            try:
                rp.read()
            except Exception as e:  # noqa: BLE001
                logger.debug("robots.txt unreachable for %s: %s", domain, e)
                # Si no hay robots.txt, asumir todo permitido (RFC9309)
                rp = None
            self._cache[domain] = rp
            return rp

    def can_fetch(self, url: str, user_agent: str | None = None) -> RobotsDecision:
        if not self.respect:
            return RobotsDecision(allowed=True, reason="robots_disabled_in_config")
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            if not domain:
                return RobotsDecision(allowed=True, reason="no_domain")

            rp = self._get_parser(domain)
            if rp is None:
                return RobotsDecision(allowed=True, reason="no_robots_txt")

            ua = user_agent or self.ua_token
            allowed = rp.can_fetch(ua, url)

            # crawl-delay si está definido
            delay = 0.0
            try:
                d = rp.crawl_delay(ua)
                if d:
                    delay = float(d)
            except Exception:  # noqa: BLE001
                pass

            return RobotsDecision(
                allowed=allowed,
                crawl_delay=delay,
                reason="allowed_by_robots" if allowed else "disallowed_by_robots",
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("robots check err %s: %s", url, e)
            return RobotsDecision(allowed=True, reason=f"check_error: {e}")

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()


# Singleton
_default = RobotsChecker()


def can_fetch(url: str, user_agent: str | None = None) -> bool:
    """API simple: True si puedo fetchear este URL según robots.txt."""
    return _default.can_fetch(url, user_agent).allowed


def get_default_checker() -> RobotsChecker:
    return _default


__all__ = ["RobotsChecker", "RobotsDecision", "can_fetch", "get_default_checker"]
