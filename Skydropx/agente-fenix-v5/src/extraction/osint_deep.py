"""
Wrappers OSINT de "enrichment profundo".

Estas herramientas se aplican SOLO a leads que ya pasaron el filtro grueso
(bucket COMPLETO o WARM), no a los 100K iniciales — eso sería 1000x más lento.

Herramientas:
- Holehe       → verifica si un email está registrado en 100+ sitios
- Maigret      → busca username/nombre en 3000+ sitios (LinkedIn, IG, Twitter...)
- pagodo       → automatiza dorks de la Google Hacking Database
- PhoneInfoga  → OSINT sobre números (carrier + presencia en redes)

Todas las herramientas son OPCIONALES: si no están instaladas, el wrapper devuelve
{"available": false} silenciosamente y no rompe nada.

Cada llamada respeta el Budget compartido (data/budgets.json).
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.core.budget import BudgetExceeded, get_budget

logger = logging.getLogger(__name__)


def _which(cmd: str) -> str | None:
    return shutil.which(cmd)


def _has_module(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


# =====================================================================
# HOLEHE — ¿este email está registrado en X servicios?
# =====================================================================

HAS_HOLEHE = _has_module("holehe") or _which("holehe") is not None


@dataclass
class HoleheResult:
    email: str
    available: bool = False
    sites_registered: list[str] = field(default_factory=list)
    sites_checked: int = 0
    is_active_persona: bool = False  # True si tiene presencia en >= 2 sitios
    error: str = ""


def holehe_check(email: str, timeout: int = 60,
                 only_used: bool = True) -> HoleheResult:
    """
    Verifica en qué servicios está registrado un email.
    Si está en LinkedIn + Twitter, casi seguro es una persona real activa.
    """
    result = HoleheResult(email=email, available=HAS_HOLEHE)
    if not HAS_HOLEHE:
        result.error = "holehe_not_installed (pip install holehe)"
        return result

    budget = get_budget("holehe")
    ok, reason = budget.can_use()
    if not ok:
        result.error = f"budget_exceeded: {reason}"
        return result

    cmd = _which("holehe")
    if not cmd:
        # Usar como módulo Python si no está en PATH
        cmd_list = ["python3", "-m", "holehe", email]
    else:
        cmd_list = [cmd, email]
    if only_used:
        cmd_list.append("--only-used")

    try:
        proc = subprocess.run(
            cmd_list, capture_output=True, timeout=timeout, text=True,
        )
        # Holehe imprime con colores ANSI; los limpiamos
        out = re.sub(r"\x1b\[[0-9;]*m", "", proc.stdout)

        sites = []
        for line in out.splitlines():
            # Líneas tipo "[+] Twitter" indican "encontrado"
            m = re.match(r"\s*\[\+\]\s+(\S+)", line)
            if m:
                sites.append(m.group(1).lower())
            # Contador "X / Y" al final
            tot = re.search(r"(\d+)\s*/\s*(\d+)", line)
            if tot:
                result.sites_checked = int(tot.group(2))

        result.sites_registered = sorted(set(sites))
        result.is_active_persona = len(result.sites_registered) >= 2
        budget.record_use(success=True)
    except subprocess.TimeoutExpired:
        result.error = "timeout"
        budget.record_use(success=False)
    except Exception as e:  # noqa: BLE001
        result.error = str(e)
        budget.record_use(success=False)

    return result


# =====================================================================
# MAIGRET — busca username/nombre en 3000+ sitios
# =====================================================================

HAS_MAIGRET = _has_module("maigret") or _which("maigret") is not None


@dataclass
class MaigretResult:
    username: str
    available: bool = False
    profiles: dict[str, str] = field(default_factory=dict)  # sitio → url
    top_profiles: dict[str, str] = field(default_factory=dict)
    error: str = ""


# Sitios prioritarios para Skydropx (B2B / e-commerce / contacto comercial)
PRIORITY_SITES = {
    "linkedin", "twitter", "x", "instagram", "facebook", "tiktok",
    "github", "behance", "dribbble",  # creativos / D2C
    "mercadolibre", "amazon",          # marketplace
    "whatsapp", "telegram",            # contacto directo
    "youtube",                          # marca / content
}


def maigret_search(username: str, timeout: int = 180,
                   top_sites_only: bool = True) -> MaigretResult:
    """
    Busca un username/nombre en sitios públicos.
    Para no tardar 10 minutos por persona, top_sites_only=True usa solo los 50 más populares.
    """
    result = MaigretResult(username=username, available=HAS_MAIGRET)
    if not HAS_MAIGRET:
        result.error = "maigret_not_installed (pip install maigret)"
        return result

    budget = get_budget("maigret")
    ok, reason = budget.can_use()
    if not ok:
        result.error = f"budget_exceeded: {reason}"
        return result

    cmd = _which("maigret") or "python3 -m maigret"
    cmd_list = (cmd.split() if isinstance(cmd, str) else [cmd]) + [
        username, "--json", "ndjson",
    ]
    if top_sites_only:
        cmd_list.extend(["--top-sites", "50"])

    with tempfile.TemporaryDirectory() as tmpd:
        cmd_list.extend(["--folderoutput", tmpd])
        try:
            proc = subprocess.run(
                cmd_list, capture_output=True, timeout=timeout, text=True,
            )
            # Parsear los .json/.ndjson generados
            for json_file in Path(tmpd).glob("*.json"):
                try:
                    data = json.loads(json_file.read_text())
                    for site, info in data.items():
                        status = (info or {}).get("status", {}).get("status", "")
                        url = (info or {}).get("url_user", "")
                        if status == "Claimed" and url:
                            result.profiles[site.lower()] = url
                except Exception as e:  # noqa: BLE001
                    logger.debug("Maigret parse err: %s", e)

            # Sitios top relevantes para Skydropx
            result.top_profiles = {
                s: u for s, u in result.profiles.items()
                if any(p in s.lower() for p in PRIORITY_SITES)
            }
            budget.record_use(success=True)
        except subprocess.TimeoutExpired:
            result.error = "timeout"
            budget.record_use(success=False)
        except Exception as e:  # noqa: BLE001
            result.error = str(e)
            budget.record_use(success=False)

    return result


# =====================================================================
# PAGODO — Google Hacking Database (GHDB) dorks automatizados
# =====================================================================

HAS_PAGODO = _has_module("pagodo") or _which("pagodo") is not None


@dataclass
class PagodoResult:
    domain: str
    available: bool = False
    urls_found: list[str] = field(default_factory=list)
    dorks_used: list[str] = field(default_factory=list)
    error: str = ""


def pagodo_search(domain: str, max_dorks: int = 20,
                  categories: list[str] | None = None,
                  timeout: int = 300) -> PagodoResult:
    """
    Aplica dorks de GHDB filtrados por categoría sobre un dominio target.
    NOTA: Google rate-limita agresivamente, úsalo con cuidado.
    """
    result = PagodoResult(domain=domain, available=HAS_PAGODO)
    if not HAS_PAGODO:
        result.error = "pagodo_not_installed (pip install pagodo)"
        return result

    budget = get_budget("pagodo")
    ok, reason = budget.can_use()
    if not ok:
        result.error = f"budget_exceeded: {reason}"
        return result

    cmd = _which("pagodo") or "python3 -m pagodo"
    with tempfile.TemporaryDirectory() as tmpd:
        out_file = Path(tmpd) / "pagodo.json"
        # pagodo args dependen de versión; intentamos algo conservador
        cmd_list = (cmd.split() if isinstance(cmd, str) else [cmd]) + [
            "-d", domain,
            "-g", str(max_dorks),
            "-j", str(out_file),
            "-l",  # log
        ]
        try:
            proc = subprocess.run(
                cmd_list, capture_output=True, timeout=timeout, text=True,
            )
            if out_file.exists():
                try:
                    data = json.loads(out_file.read_text())
                    result.urls_found = data.get("urls", [])[:200]
                    result.dorks_used = data.get("dorks", [])[:50]
                except Exception:  # noqa: BLE001
                    # parsing fallback con regex
                    result.urls_found = list(set(re.findall(
                        r"https?://[^\s]+" + re.escape(domain) + r"[^\s]*",
                        proc.stdout,
                    )))[:200]
            else:
                # Fallback: parsear stdout
                result.urls_found = list(set(re.findall(
                    r"https?://[^\s\"<>]+",
                    proc.stdout,
                )))[:200]
            budget.record_use(success=True)
        except subprocess.TimeoutExpired:
            result.error = "timeout"
            budget.record_use(success=False)
        except Exception as e:  # noqa: BLE001
            result.error = str(e)
            budget.record_use(success=False)

    return result


# =====================================================================
# PHONEINFOGA — OSINT sobre números
# =====================================================================

HAS_PHONEINFOGA = _which("phoneinfoga") is not None


@dataclass
class PhoneInfogaResult:
    phone: str
    available: bool = False
    country: str = ""
    carrier: str = ""
    line_type: str = ""
    raw_scan: dict = field(default_factory=dict)
    error: str = ""


def phoneinfoga_scan(phone_e164: str, timeout: int = 60) -> PhoneInfogaResult:
    """
    OSINT sobre un número (carrier, presencia en redes).
    Requiere binario instalado: https://github.com/sundowndev/phoneinfoga
    """
    result = PhoneInfogaResult(phone=phone_e164, available=HAS_PHONEINFOGA)
    if not HAS_PHONEINFOGA:
        result.error = "phoneinfoga_not_installed"
        return result

    budget = get_budget("phoneinfoga")
    ok, reason = budget.can_use()
    if not ok:
        result.error = f"budget_exceeded: {reason}"
        return result

    try:
        proc = subprocess.run(
            ["phoneinfoga", "scan", "-n", phone_e164, "--format", "json"],
            capture_output=True, timeout=timeout, text=True,
        )
        try:
            data = json.loads(proc.stdout)
            result.raw_scan = data
            # PhoneInfoga estructura varía por versión; extraemos lo común
            num = data.get("number") or data
            result.country = num.get("country") or ""
            result.carrier = num.get("carrier") or ""
            result.line_type = num.get("type") or ""
            budget.record_use(success=True)
        except json.JSONDecodeError:
            result.error = "no_json_output"
            budget.record_use(success=False)
    except subprocess.TimeoutExpired:
        result.error = "timeout"
        budget.record_use(success=False)
    except Exception as e:  # noqa: BLE001
        result.error = str(e)
        budget.record_use(success=False)

    return result


# =====================================================================
# SPIDERFOOT — wrapper mínimo (modo CLI single-target)
# =====================================================================

HAS_SPIDERFOOT = _which("sf.py") is not None or _which("spiderfoot") is not None


@dataclass
class SpiderFootResult:
    target: str
    available: bool = False
    emails: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    ips: list[str] = field(default_factory=list)
    breaches: list[str] = field(default_factory=list)
    social: list[str] = field(default_factory=list)
    scan_id: str = ""
    error: str = ""


def spiderfoot_scan(target: str, modules: str = "sfp_dnsresolve,sfp_email,sfp_company",
                    timeout: int = 600) -> SpiderFootResult:
    """
    Wrapper mínimo de SpiderFoot CLI. Pensado para investigación PROFUNDA
    de un solo target (ej: una empresa B2B Enterprise grande).
    NO usar masivamente.
    """
    result = SpiderFootResult(target=target, available=HAS_SPIDERFOOT)
    if not HAS_SPIDERFOOT:
        result.error = "spiderfoot_not_installed (git clone smicallef/spiderfoot)"
        return result

    budget = get_budget("spiderfoot")
    ok, reason = budget.can_use()
    if not ok:
        result.error = f"budget_exceeded: {reason}"
        return result

    cmd = _which("sf.py") or _which("spiderfoot")
    try:
        proc = subprocess.run(
            [cmd, "-s", target, "-m", modules, "-q", "-o", "json"],
            capture_output=True, timeout=timeout, text=True,
        )
        try:
            data = json.loads(proc.stdout)
            for event in (data if isinstance(data, list) else data.get("events", [])):
                event_type = event.get("type", "")
                value = event.get("data", "")
                if "EMAIL" in event_type and value:
                    result.emails.append(value)
                elif "DOMAIN" in event_type and value:
                    result.domains.append(value)
                elif "IP_ADDRESS" in event_type and value:
                    result.ips.append(value)
                elif "BREACH" in event_type and value:
                    result.breaches.append(value)
                elif "SOCIAL" in event_type and value:
                    result.social.append(value)
            result.scan_id = data.get("scan_id", "") if isinstance(data, dict) else ""
            for attr in ("emails", "domains", "ips", "breaches", "social"):
                setattr(result, attr, sorted(set(getattr(result, attr))))
            budget.record_use(success=True)
        except json.JSONDecodeError:
            # SpiderFoot a veces escribe a archivo, no a stdout
            result.error = "parse_error_no_json"
            budget.record_use(success=False)
    except subprocess.TimeoutExpired:
        result.error = "timeout"
        budget.record_use(success=False)
    except Exception as e:  # noqa: BLE001
        result.error = str(e)
        budget.record_use(success=False)

    return result


# =====================================================================
# UTILS
# =====================================================================

def availability() -> dict[str, bool]:
    """Estado de cada herramienta opcional."""
    return {
        "holehe": HAS_HOLEHE,
        "maigret": HAS_MAIGRET,
        "pagodo": HAS_PAGODO,
        "phoneinfoga": HAS_PHONEINFOGA,
        "spiderfoot": HAS_SPIDERFOOT,
    }


__all__ = [
    "holehe_check", "HoleheResult", "HAS_HOLEHE",
    "maigret_search", "MaigretResult", "HAS_MAIGRET",
    "pagodo_search", "PagodoResult", "HAS_PAGODO",
    "phoneinfoga_scan", "PhoneInfogaResult", "HAS_PHONEINFOGA",
    "spiderfoot_scan", "SpiderFootResult", "HAS_SPIDERFOOT",
    "availability", "PRIORITY_SITES",
]
