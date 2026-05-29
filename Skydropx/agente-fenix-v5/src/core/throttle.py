"""
Auto-throttling adaptativo — gap #6.

Detecta cuando un dominio/fuente nos está bloqueando y reduce automáticamente
la tasa de requests para evitar bans permanentes.

Política:
- Cada dominio tiene un "estado de salud" (healthy | slowing | quarantine)
- Métricas: response_time, status_codes, anti-bot signals
- Si detecta degradación → backoff exponencial automático
- Si el domain queda quarantined → todas las futuras requests a ese dominio
  se saltan por N minutos
- Persiste estado en data/throttle_state.json para sobrevivir reinicios

Casos típicos:
1. Cloudflare bloqueó después de 50 requests → quarantine 30 min
2. DDG empezó a devolver captchas → slow mode 5s entre requests
3. Sitio devuelve 503/429 → backoff exponencial 1s → 2s → 4s → quarantine
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from threading import Lock
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

THROTTLE_STATE_PATH = Path("data/throttle_state.json")
_lock = Lock()

# Señales de bloqueo en respuestas HTML
BLOCK_SIGNALS = [
    "captcha", "cf-challenge", "challenge-platform", "cloudflare",
    "perimeterx", "datadome", "akamai-bm", "incapsula",
    "access denied", "rate limit", "too many requests",
    "human verification", "are you a robot",
]


@dataclass
class DomainState:
    domain: str
    requests_made: int = 0
    successful: int = 0
    failed: int = 0
    blocked_signals_count: int = 0
    last_request_at: float = 0.0
    last_blocked_at: float = 0.0
    avg_response_ms: float = 0.0
    quarantined_until: float = 0.0
    state: str = "healthy"          # healthy | slowing | quarantine
    current_delay_sec: float = 0.0

    def is_quarantined(self) -> bool:
        return time.time() < self.quarantined_until


@dataclass
class ThrottleConfig:
    # Threshold para slowing
    slowing_consecutive_fails: int = 3
    slowing_delay_sec: float = 5.0
    # Threshold para quarantine
    quarantine_consecutive_blocks: int = 5
    quarantine_duration_sec: int = 1800   # 30 min
    # Cap general
    max_delay_sec: float = 30.0


class AutoThrottle:
    def __init__(self, config: ThrottleConfig | None = None):
        self.config = config or ThrottleConfig()
        self.state: dict[str, DomainState] = {}
        self._load_state()

    # ---------- Pre-request ----------

    def before_request(self, url: str) -> tuple[bool, float, str]:
        """
        Antes de hacer un request, consulta el throttle.
        Devuelve (allowed, delay_to_apply, reason).

        Si allowed=False, el caller debe saltar la request.
        Si allowed=True y delay_to_apply > 0, debe hacer time.sleep(delay).
        """
        domain = self._extract_domain(url)
        if not domain:
            return True, 0.0, "no_domain"

        ds = self._get_or_create(domain)
        with _lock:
            if ds.is_quarantined():
                remaining = int(ds.quarantined_until - time.time())
                return False, 0.0, f"quarantine ({remaining}s left)"

            # Aplicar delay según estado
            delay = ds.current_delay_sec
            # Time since last request a este dominio
            elapsed = time.time() - ds.last_request_at
            if elapsed < delay:
                actual_delay = delay - elapsed
            else:
                actual_delay = 0.0

            ds.last_request_at = time.time()
            ds.requests_made += 1
            return True, min(actual_delay, self.config.max_delay_sec), ds.state

    # ---------- Post-request ----------

    def record_response(
        self, url: str,
        status_code: int,
        response_time_ms: int,
        html_snippet: str = "",
    ) -> None:
        """Registra el resultado de una request para ajustar throttle."""
        domain = self._extract_domain(url)
        if not domain:
            return
        ds = self._get_or_create(domain)
        with _lock:
            # Actualizar avg response time (moving average ligero)
            if ds.avg_response_ms == 0:
                ds.avg_response_ms = response_time_ms
            else:
                ds.avg_response_ms = 0.7 * ds.avg_response_ms + 0.3 * response_time_ms

            # ¿Es un bloqueo?
            is_blocked = self._is_blocked(status_code, html_snippet)
            if is_blocked:
                ds.failed += 1
                ds.blocked_signals_count += 1
                ds.last_blocked_at = time.time()
                self._escalate_throttle(ds)
            else:
                ds.successful += 1
                # Si lleva varios éxitos seguidos, relajar
                if ds.state == "slowing" and ds.successful % 10 == 0:
                    ds.state = "healthy"
                    ds.current_delay_sec = 0.0
                    logger.info("Throttle: %s vuelve a healthy", domain)

        self._persist_state()

    def record_exception(self, url: str, exc_type: str = "") -> None:
        """Registra una exception (no se pudo medir status code)."""
        domain = self._extract_domain(url)
        if not domain:
            return
        ds = self._get_or_create(domain)
        with _lock:
            ds.failed += 1
            if "timeout" in exc_type.lower() or "connection" in exc_type.lower():
                ds.blocked_signals_count += 1
                self._escalate_throttle(ds)
        self._persist_state()

    # ---------- Estado ----------

    def stats(self) -> dict:
        return {
            "tracked_domains": len(self.state),
            "quarantined": [
                {"domain": d, "remaining_sec": int(ds.quarantined_until - time.time())}
                for d, ds in self.state.items() if ds.is_quarantined()
            ],
            "slowing": [
                {"domain": d, "delay": ds.current_delay_sec,
                 "failed": ds.failed, "successful": ds.successful}
                for d, ds in self.state.items() if ds.state == "slowing"
            ],
            "top_blocked": sorted(
                [{"domain": d, "blocks": ds.blocked_signals_count,
                  "requests": ds.requests_made}
                 for d, ds in self.state.items() if ds.blocked_signals_count > 0],
                key=lambda x: -x["blocks"],
            )[:10],
        }

    def reset_domain(self, domain: str) -> None:
        """Para casos donde el usuario quiere forzar reset (debug)."""
        with _lock:
            if domain in self.state:
                self.state[domain] = DomainState(domain=domain)
        self._persist_state()

    # ---------- Internos ----------

    def _get_or_create(self, domain: str) -> DomainState:
        if domain not in self.state:
            self.state[domain] = DomainState(domain=domain)
        return self.state[domain]

    def _escalate_throttle(self, ds: DomainState) -> None:
        """Aumenta la severidad según fails consecutivos."""
        # Reset successful streak si falla
        ds.successful = 0
        if ds.failed >= self.config.quarantine_consecutive_blocks:
            ds.state = "quarantine"
            ds.quarantined_until = time.time() + self.config.quarantine_duration_sec
            ds.failed = 0  # reset para próxima ronda post-quarantine
            logger.warning("Throttle: %s QUARANTINE %ss",
                            ds.domain, self.config.quarantine_duration_sec)
        elif ds.failed >= self.config.slowing_consecutive_fails:
            ds.state = "slowing"
            ds.current_delay_sec = min(
                ds.current_delay_sec * 2 if ds.current_delay_sec > 0 else self.config.slowing_delay_sec,
                self.config.max_delay_sec,
            )
            logger.info("Throttle: %s SLOWING delay=%.1fs", ds.domain, ds.current_delay_sec)

    def _is_blocked(self, status_code: int, html: str) -> bool:
        if status_code in (403, 429, 503, 502, 504):
            return True
        if not html:
            return False
        low = html[:3000].lower()
        return any(sig in low for sig in BLOCK_SIGNALS)

    @staticmethod
    def _extract_domain(url: str) -> str | None:
        try:
            p = urlparse(url if url.startswith("http") else f"http://{url}")
            return p.netloc.lower().lstrip("www.")
        except Exception:  # noqa: BLE001
            return None

    def _persist_state(self) -> None:
        try:
            THROTTLE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {d: asdict(ds) for d, ds in self.state.items()
                    if ds.requests_made > 0}
            THROTTLE_STATE_PATH.write_text(
                json.dumps(data, indent=2, default=str), encoding="utf-8",
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("Persist throttle err: %s", e)

    def _load_state(self) -> None:
        if not THROTTLE_STATE_PATH.exists():
            return
        try:
            data = json.loads(THROTTLE_STATE_PATH.read_text())
            for d, raw in data.items():
                ds = DomainState(**{k: v for k, v in raw.items() if k in DomainState.__dataclass_fields__})
                self.state[d] = ds
        except Exception as e:  # noqa: BLE001
            logger.debug("Load throttle err: %s", e)


# Singleton
_default: AutoThrottle | None = None


def get_throttle() -> AutoThrottle:
    global _default
    if _default is None:
        _default = AutoThrottle()
    return _default


__all__ = ["AutoThrottle", "DomainState", "ThrottleConfig", "get_throttle"]
