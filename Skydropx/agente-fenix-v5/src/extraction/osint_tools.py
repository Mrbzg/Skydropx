"""
Wrappers para herramientas OSINT externas (CLI).

Cada wrapper:
- Detecta si la herramienta está instalada
- Si está, la usa
- Si no, devuelve [] silenciosamente y loguea cómo instalarla

Herramientas integradas:
- theHarvester  → emails, subdominios, IPs desde un dominio
- EmailHarvester → emails desde Google Dorks
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------- Helpers ----------------

def _which(cmd: str) -> str | None:
    return shutil.which(cmd)


# ---------------- theHarvester ----------------

@dataclass
class HarvestResult:
    domain: str
    emails: list[str] = field(default_factory=list)
    hosts: list[str] = field(default_factory=list)
    ips: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    error: str = ""


def theharvester_run(
    domain: str,
    sources: str = "bing,duckduckgo,certspotter,crtsh",
    limit: int = 500,
    timeout: int = 180,
) -> HarvestResult:
    """
    Ejecuta theHarvester contra un dominio y devuelve resultados normalizados.

    Instalación: pip install theHarvester  (requiere Python 3.12+)
    """
    cmd = _which("theHarvester") or _which("theharvester")
    if not cmd:
        logger.warning(
            "theHarvester no instalado. Instala con: pip install theHarvester"
        )
        return HarvestResult(domain=domain, error="not_installed")

    with tempfile.TemporaryDirectory() as tmpd:
        out_base = str(Path(tmpd) / f"th_{domain.replace('.', '_')}")
        try:
            proc = subprocess.run(
                [cmd, "-d", domain, "-b", sources, "-l", str(limit), "-f", out_base],
                capture_output=True, timeout=timeout, text=True,
            )
        except subprocess.TimeoutExpired:
            return HarvestResult(domain=domain, error="timeout")
        except Exception as e:  # noqa: BLE001
            return HarvestResult(domain=domain, error=str(e))

        # Intentar parsear el JSON output (theHarvester escribe .json)
        result = HarvestResult(domain=domain)
        json_path = Path(out_base + ".json")
        if json_path.exists():
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                result.emails = data.get("emails", [])
                result.hosts = data.get("hosts", [])
                result.ips = data.get("ips", [])
                result.urls = data.get("interesting_urls", [])
                return result
            except Exception as e:  # noqa: BLE001
                logger.debug("Error parseando JSON theHarvester: %s", e)

        # Fallback: parsear stdout (formato variable)
        result.emails = list(set(re.findall(
            r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
            proc.stdout,
        )))
        result.hosts = list(set(re.findall(
            r"(?:[a-zA-Z0-9-]+\.)+" + re.escape(domain),
            proc.stdout,
        )))
        return result


def theharvester_emails(domain: str, sources: str = "bing,duckduckgo,certspotter") -> list[str]:
    """API simplificada: solo emails."""
    return theharvester_run(domain, sources=sources).emails


# ---------------- EmailHarvester (Google Dorks) ----------------

def emailharvester_run(
    domain: str,
    pages: int = 5,
    timeout: int = 120,
    extra_args: list[str] | None = None,
) -> list[str]:
    """
    EmailHarvester usa Google Dorks para buscar emails.
    Repo: https://github.com/Towardscybersec/EmailHarvester

    Si no está instalado, retorna lista vacía silenciosamente.
    """
    cmd = _which("EmailHarvester") or _which("emailharvester")
    if not cmd:
        # Intentar como módulo Python
        try:
            __import__("EmailHarvester")
            cmd = "python3 -m EmailHarvester"
        except ImportError:
            logger.warning(
                "EmailHarvester no instalado. Instala con: "
                "git clone https://github.com/Towardscybersec/EmailHarvester && cd EmailHarvester && pip install -r requirements.txt"
            )
            return []

    cmd_list = cmd.split() + ["-d", domain, "-l", str(pages * 10)]
    if extra_args:
        cmd_list.extend(extra_args)

    try:
        proc = subprocess.run(
            cmd_list, capture_output=True, timeout=timeout, text=True
        )
    except (subprocess.TimeoutExpired, Exception) as e:  # noqa: BLE001
        logger.warning("EmailHarvester err: %s", e)
        return []

    emails = sorted(set(re.findall(
        r"[a-zA-Z0-9._%+\-]+@" + re.escape(domain),
        proc.stdout,
    )))
    return emails


# ---------------- Combinador inteligente ----------------

def harvest_all(domain: str) -> dict:
    """
    Corre todas las herramientas OSINT disponibles y unifica.
    Solo usa las que están instaladas; las demás se saltan en silencio.
    """
    result = {
        "domain": domain,
        "emails": set(),
        "hosts": set(),
        "ips": set(),
        "sources_used": [],
        "sources_skipped": [],
    }

    # theHarvester
    if _which("theHarvester") or _which("theharvester"):
        try:
            th = theharvester_run(domain)
            if not th.error:
                result["emails"].update(th.emails)
                result["hosts"].update(th.hosts)
                result["ips"].update(th.ips)
                result["sources_used"].append("theHarvester")
        except Exception:  # noqa: BLE001
            result["sources_skipped"].append("theHarvester")
    else:
        result["sources_skipped"].append("theHarvester (no instalado)")

    # EmailHarvester
    if _which("EmailHarvester") or _which("emailharvester"):
        try:
            emails = emailharvester_run(domain)
            result["emails"].update(emails)
            result["sources_used"].append("EmailHarvester")
        except Exception:  # noqa: BLE001
            result["sources_skipped"].append("EmailHarvester")
    else:
        result["sources_skipped"].append("EmailHarvester (no instalado)")

    result["emails"] = sorted(result["emails"])
    result["hosts"] = sorted(result["hosts"])
    result["ips"] = sorted(result["ips"])
    return result


__all__ = [
    "theharvester_run", "theharvester_emails",
    "emailharvester_run", "harvest_all",
    "HarvestResult",
]
