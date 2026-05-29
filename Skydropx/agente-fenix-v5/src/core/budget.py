"""
Budget limiter compartido para herramientas OSINT externas.

Cada herramienta externa (Holehe, Maigret, PhoneInfoga, pagodo, etc.) puede:
- Generar muchos HTTP requests por uso (Holehe = ~100 sitios por email)
- Disparar rate-limits o bans si se abusa
- Consumir tiempo de cómputo significativo

Este módulo previene esos problemas con:
- Cuotas por herramienta (queries/hora, queries/día)
- Persistencia en data/budgets.json
- Circuit breaker: tras N fallos consecutivos, quarantine
- Auditoría para self-improver

Ejemplo:
    budget = get_budget("holehe", per_hour=50, per_day=200)
    if not budget.can_use():
        raise BudgetExceeded("holehe agotado, intenta en %s" % budget.next_window())
    # ... usar la herramienta
    budget.record_use(success=True)
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

BUDGET_FILE = Path("data/budgets.json")
_lock = Lock()


@dataclass
class BudgetState:
    name: str
    per_hour: int = 100
    per_day: int = 1000
    used_in_hour: int = 0
    used_in_day: int = 0
    hour_window_start: float = 0.0
    day_window_start: float = 0.0
    consecutive_fails: int = 0
    quarantined_until: float = 0.0
    quarantine_sec: int = 600
    max_consecutive_fails: int = 5
    total_uses: int = 0
    total_fails: int = 0


class Budget:
    """Cuota individual de una herramienta. Thread-safe."""

    def __init__(self, name: str, per_hour: int = 100, per_day: int = 1000,
                 quarantine_sec: int = 600, max_consecutive_fails: int = 5):
        self.name = name
        with _lock:
            state = _load_all().get(name)
            if state:
                self.state = BudgetState(**state)
                # Sync params (puede haber cambiado config)
                self.state.per_hour = per_hour
                self.state.per_day = per_day
                self.state.quarantine_sec = quarantine_sec
                self.state.max_consecutive_fails = max_consecutive_fails
            else:
                self.state = BudgetState(
                    name=name, per_hour=per_hour, per_day=per_day,
                    quarantine_sec=quarantine_sec,
                    max_consecutive_fails=max_consecutive_fails,
                    hour_window_start=time.time(),
                    day_window_start=time.time(),
                )

    def _rotate_windows(self) -> None:
        now = time.time()
        if now - self.state.hour_window_start >= 3600:
            self.state.used_in_hour = 0
            self.state.hour_window_start = now
        if now - self.state.day_window_start >= 86400:
            self.state.used_in_day = 0
            self.state.day_window_start = now

    def can_use(self) -> tuple[bool, str]:
        """Devuelve (puedo_usar, razón_si_no)."""
        with _lock:
            self._rotate_windows()
            if time.time() < self.state.quarantined_until:
                rem = int(self.state.quarantined_until - time.time())
                return False, f"{self.name} en cuarentena ({rem}s restantes)"
            if self.state.used_in_hour >= self.state.per_hour:
                rem = int(3600 - (time.time() - self.state.hour_window_start))
                return False, f"{self.name} agotó cuota horaria ({rem}s para reset)"
            if self.state.used_in_day >= self.state.per_day:
                rem = int(86400 - (time.time() - self.state.day_window_start))
                return False, f"{self.name} agotó cuota diaria ({rem // 3600}h para reset)"
            return True, ""

    def record_use(self, success: bool = True) -> None:
        with _lock:
            self._rotate_windows()
            self.state.used_in_hour += 1
            self.state.used_in_day += 1
            self.state.total_uses += 1
            if success:
                self.state.consecutive_fails = 0
            else:
                self.state.consecutive_fails += 1
                self.state.total_fails += 1
                if self.state.consecutive_fails >= self.state.max_consecutive_fails:
                    self.state.quarantined_until = time.time() + self.state.quarantine_sec
                    logger.warning(
                        "%s entró en cuarentena %ss tras %s fallos seguidos",
                        self.name, self.state.quarantine_sec, self.state.consecutive_fails,
                    )
                    self.state.consecutive_fails = 0
            self._persist()

    def remaining(self) -> dict:
        with _lock:
            self._rotate_windows()
            return {
                "per_hour": self.state.per_hour - self.state.used_in_hour,
                "per_day": self.state.per_day - self.state.used_in_day,
                "quarantined_for": max(0, int(self.state.quarantined_until - time.time())),
            }

    def _persist(self) -> None:
        all_states = _load_all()
        all_states[self.name] = asdict(self.state)
        BUDGET_FILE.parent.mkdir(parents=True, exist_ok=True)
        BUDGET_FILE.write_text(json.dumps(all_states, indent=2))


class BudgetExceeded(Exception):
    pass


def _load_all() -> dict[str, dict]:
    if not BUDGET_FILE.exists():
        return {}
    try:
        return json.loads(BUDGET_FILE.read_text())
    except Exception:  # noqa: BLE001
        return {}


# Singletons por herramienta (defaults conservadores)
_DEFAULTS = {
    "holehe":      {"per_hour": 100, "per_day": 500},     # ~100 sitios x 50 = 5000 reqs/hora
    "maigret":     {"per_hour": 30,  "per_day": 150},     # 3000 sitios x 30 = 90K reqs/hora
    "pagodo":      {"per_hour": 30,  "per_day": 200},     # google dorks, lento
    "phoneinfoga": {"per_hour": 100, "per_day": 500},
    "spiderfoot":  {"per_hour": 10,  "per_day": 50},      # tarda ~5 min/scan
    "sherlock":    {"per_hour": 50,  "per_day": 300},
    "h8mail":      {"per_hour": 30,  "per_day": 100},
}

_instances: dict[str, Budget] = {}


def get_budget(name: str, per_hour: int | None = None,
                per_day: int | None = None) -> Budget:
    """API de conveniencia: devuelve el Budget singleton para una herramienta."""
    if name not in _instances:
        defaults = _DEFAULTS.get(name, {"per_hour": 50, "per_day": 200})
        _instances[name] = Budget(
            name,
            per_hour=per_hour or defaults["per_hour"],
            per_day=per_day or defaults["per_day"],
        )
    return _instances[name]


def stats_all() -> dict:
    """Estado de todas las cuotas (para CLI / MCP)."""
    out = {}
    all_states = _load_all()
    for name, st in all_states.items():
        b = Budget(name, per_hour=st.get("per_hour", 50), per_day=st.get("per_day", 200))
        out[name] = {
            "remaining": b.remaining(),
            "total_uses": st.get("total_uses", 0),
            "total_fails": st.get("total_fails", 0),
            "quarantined": time.time() < st.get("quarantined_until", 0),
        }
    return out


__all__ = ["Budget", "BudgetExceeded", "get_budget", "stats_all"]
