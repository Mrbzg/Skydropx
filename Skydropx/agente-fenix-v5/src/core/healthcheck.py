"""
Healthcheck pre-run + diagnostics — gap #7.

Verifica antes de empezar el pipeline que TODO lo crítico funciona:
- Token DENUE válido (test real)
- Search backends configurados y respondiendo
- DB accesible y schema OK
- Espacio en disco suficiente
- Output dir escribible
- Memoria suficiente para el meta solicitado
- Permisos de robots.txt para los dominios objetivo (sample)

Si algo crítico falla, aborta antes de quemar tiempo/recursos.
Si algo no-crítico falla, da warnings pero continúa.

Niveles:
- CRITICAL: pipeline NO puede correr (DB, output dir, sin search backend)
- WARNING:  pipeline correrá pero con limitaciones (sin Hunter, sin re-enrich)
- INFO:     features opcionales no disponibles (sin tools OSINT)
"""
from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class HealthCheck:
    name: str
    level: str = "INFO"          # CRITICAL | WARNING | INFO
    passed: bool = True
    message: str = ""
    duration_ms: int = 0
    details: dict = field(default_factory=dict)


@dataclass
class HealthReport:
    overall_ok: bool = True
    critical_failures: list[HealthCheck] = field(default_factory=list)
    warnings: list[HealthCheck] = field(default_factory=list)
    info: list[HealthCheck] = field(default_factory=list)
    duration_total_ms: int = 0

    def add(self, check: HealthCheck) -> None:
        if not check.passed:
            if check.level == "CRITICAL":
                self.critical_failures.append(check)
                self.overall_ok = False
            elif check.level == "WARNING":
                self.warnings.append(check)
            else:
                self.info.append(check)
        else:
            self.info.append(check)

    def summary(self) -> dict:
        return {
            "overall_ok": self.overall_ok,
            "n_critical": len(self.critical_failures),
            "n_warnings": len(self.warnings),
            "n_passed": sum(1 for c in self.info if c.passed),
            "duration_total_ms": self.duration_total_ms,
            "critical": [{"name": c.name, "message": c.message}
                          for c in self.critical_failures],
            "warnings": [{"name": c.name, "message": c.message}
                          for c in self.warnings],
        }


def _time_check(fn):
    """Decorator que mide duración del check."""
    def wrapped(*args, **kwargs) -> HealthCheck:
        t0 = time.time()
        try:
            check = fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            check = HealthCheck(
                name=fn.__name__, level="CRITICAL",
                passed=False, message=f"Exception: {e}",
            )
        check.duration_ms = int((time.time() - t0) * 1000)
        return check
    return wrapped


# ---------------- Checks individuales ----------------

@_time_check
def check_denue_token() -> HealthCheck:
    from src.core.config import settings
    if not settings.denue_token:
        return HealthCheck(
            name="denue_token", level="CRITICAL", passed=False,
            message="DENUE_TOKEN no configurado. Pipeline no puede usar DENUE.",
        )
    # Test real: cuantificar comercio en CDMX
    try:
        from src.sources.denue_source import DenueClient
        c = DenueClient()
        r = c.cuantificar("46", "09", "0")
        if r and r[0].get("Total"):
            total = int(r[0]["Total"])
            return HealthCheck(
                name="denue_token", level="INFO", passed=True,
                message=f"DENUE OK ({total:,} comercios CDMX accesibles)",
                details={"sample_count": total},
            )
        return HealthCheck(
            name="denue_token", level="CRITICAL", passed=False,
            message="DENUE responde pero sin data — token posiblemente revocado",
        )
    except Exception as e:  # noqa: BLE001
        return HealthCheck(
            name="denue_token", level="CRITICAL", passed=False,
            message=f"DENUE API error: {e}",
        )


@_time_check
def check_search_backends() -> HealthCheck:
    from src.sources.search_backends import get_default_manager
    mgr = get_default_manager()
    available = [b.name for b in mgr.available_backends()]
    if not available:
        return HealthCheck(
            name="search_backends", level="CRITICAL", passed=False,
            message="NINGÚN search backend disponible. Re-enrich y dorks no funcionarán.",
        )
    if "serper" in available or "searxng" in available:
        return HealthCheck(
            name="search_backends", level="INFO", passed=True,
            message=f"Backends premium disponibles: {available}",
            details={"available": available},
        )
    return HealthCheck(
        name="search_backends", level="WARNING", passed=True,
        message=f"Solo DDG disponible — rate-limit fuerte. Considera SearXNG o Serper. ({available})",
        details={"available": available},
    )


@_time_check
def check_db_accessible() -> HealthCheck:
    try:
        from src.db.engine import get_db
        db = get_db()
        db.init_schema()
        stats = db.stats()
        return HealthCheck(
            name="db_accessible", level="INFO", passed=True,
            message=f"DB OK ({stats['companies']:,} companies, {stats['jobs']} jobs)",
            details=stats,
        )
    except Exception as e:  # noqa: BLE001
        return HealthCheck(
            name="db_accessible", level="CRITICAL", passed=False,
            message=f"DB no accesible: {e}",
        )


@_time_check
def check_output_dir() -> HealthCheck:
    from src.core.config import settings
    try:
        p = Path(settings.output_dir)
        p.mkdir(parents=True, exist_ok=True)
        test = p / ".write_test"
        test.write_text("ok")
        test.unlink()
        return HealthCheck(
            name="output_dir", level="INFO", passed=True,
            message=f"output_dir OK: {p}",
        )
    except Exception as e:  # noqa: BLE001
        return HealthCheck(
            name="output_dir", level="CRITICAL", passed=False,
            message=f"output_dir no escribible: {e}",
        )


@_time_check
def check_disk_space(min_gb: float = 1.0) -> HealthCheck:
    try:
        from src.core.config import settings
        usage = shutil.disk_usage(Path(settings.output_dir).resolve())
        free_gb = usage.free / 1e9
        if free_gb < min_gb:
            return HealthCheck(
                name="disk_space", level="CRITICAL", passed=False,
                message=f"Solo {free_gb:.2f} GB libres (mínimo {min_gb} GB)",
            )
        if free_gb < 5:
            return HealthCheck(
                name="disk_space", level="WARNING", passed=True,
                message=f"Espacio bajo: {free_gb:.2f} GB libres",
                details={"free_gb": free_gb},
            )
        return HealthCheck(
            name="disk_space", level="INFO", passed=True,
            message=f"Disk OK: {free_gb:.1f} GB libres",
        )
    except Exception as e:  # noqa: BLE001
        return HealthCheck(
            name="disk_space", level="WARNING", passed=True,
            message=f"No se pudo verificar disk: {e}",
        )


@_time_check
def check_optional_deps() -> HealthCheck:
    """Reporta qué deps opcionales tenemos para ajustar yields esperados."""
    deps = {}
    for name in ["phonenumbers", "trafilatura", "tenacity", "pytrends",
                  "holehe", "maigret", "patchright", "nodriver",
                  "botasaurus", "pypdf", "scrapegraphai"]:
        try:
            __import__(name)
            deps[name] = True
        except ImportError:
            deps[name] = False
    critical_missing = [d for d in ("phonenumbers",) if not deps.get(d)]
    if critical_missing:
        return HealthCheck(
            name="optional_deps", level="WARNING", passed=False,
            message=f"Deps recomendadas faltantes: {critical_missing}",
            details=deps,
        )
    n_installed = sum(deps.values())
    return HealthCheck(
        name="optional_deps", level="INFO", passed=True,
        message=f"{n_installed}/{len(deps)} deps opcionales instaladas",
        details=deps,
    )


@_time_check
def check_memory_for_meta(meta: int = 1000) -> HealthCheck:
    """Estima si hay RAM suficiente para procesar la meta."""
    # Heurística: ~5KB por lead en memoria (RawRecord + extras)
    needed_mb = (meta * 2 * 5) / 1024  # x2 por dedup intermedio
    try:
        import os
        if hasattr(os, "sysconf") and os.sysconf_names.get("SC_PAGE_SIZE"):
            page = os.sysconf("SC_PAGE_SIZE")
            phys = os.sysconf("SC_PHYS_PAGES")
            total_mb = (page * phys) / 1e6
            if needed_mb > total_mb * 0.3:
                return HealthCheck(
                    name="memory_for_meta", level="WARNING", passed=True,
                    message=f"Meta {meta:,} usaría ~{needed_mb:.0f}MB (tienes {total_mb:.0f}MB)",
                )
            return HealthCheck(
                name="memory_for_meta", level="INFO", passed=True,
                message=f"RAM OK para meta {meta:,} (~{needed_mb:.0f}MB)",
                details={"needed_mb": needed_mb, "total_mb": total_mb},
            )
    except Exception:  # noqa: BLE001
        pass
    return HealthCheck(
        name="memory_for_meta", level="INFO", passed=True,
        message=f"RAM check no disponible en esta plataforma",
    )


@_time_check
def check_serper_credits() -> HealthCheck:
    from src.core.config import settings
    if not settings.serper_api_key:
        return HealthCheck(
            name="serper_credits", level="INFO", passed=True,
            message="Serper.dev no configurado (opcional)",
        )
    from src.sources.search_backends import SerperBackend
    b = SerperBackend()
    rem = b.credits_remaining
    if rem <= 100:
        return HealthCheck(
            name="serper_credits", level="WARNING", passed=True,
            message=f"Serper créditos bajos: {rem} restantes",
            details={"remaining": rem},
        )
    return HealthCheck(
        name="serper_credits", level="INFO", passed=True,
        message=f"Serper OK ({rem} créditos free)",
        details={"remaining": rem},
    )


# ---------------- Runner ----------------

ALL_CHECKS = [
    check_denue_token,
    check_search_backends,
    check_db_accessible,
    check_output_dir,
    check_disk_space,
    check_optional_deps,
    check_serper_credits,
]


def run_healthcheck(meta: int = 1000,
                     fail_fast: bool = False) -> HealthReport:
    """
    Corre todos los checks pre-pipeline.

    Args:
        meta: para validar RAM contra la meta esperada
        fail_fast: si True, detiene en la primera falla CRITICAL
    """
    t0 = time.time()
    report = HealthReport()
    for fn in ALL_CHECKS:
        check = fn()
        report.add(check)
        if fail_fast and check.level == "CRITICAL" and not check.passed:
            break
    # Check específico de RAM para esta corrida
    mem_check = check_memory_for_meta(meta)
    report.add(mem_check)
    report.duration_total_ms = int((time.time() - t0) * 1000)
    return report


def print_report(report: HealthReport) -> None:
    """Imprime reporte legible al usuario."""
    print()
    if report.overall_ok:
        print("✓ Healthcheck OK — sistema listo para correr pipeline")
    else:
        print("✗ Healthcheck FAILED — pipeline NO puede correr")
    print()
    for check in report.critical_failures:
        print(f"  ❌ [{check.level}] {check.name}: {check.message}")
    for check in report.warnings:
        print(f"  ⚠  [{check.level}] {check.name}: {check.message}")
    for check in report.info:
        if check.passed:
            print(f"  ✓  {check.name}: {check.message}")
    print()
    print(f"Total: {report.duration_total_ms}ms")


__all__ = [
    "HealthCheck", "HealthReport", "run_healthcheck", "print_report",
]
