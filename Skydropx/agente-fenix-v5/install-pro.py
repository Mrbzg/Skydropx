#!/usr/bin/env python3
# ============================================================================
# Agente Fénix v5 — Instalador PRO (capacidades pesadas / mega-corridas)
# ============================================================================
# Instala las dependencias avanzadas (requirements-full.txt) ENCIMA de la
# instalación básica: deep OSINT, anti-bot, analytics OLAP (DuckDB), colas
# (Celery/Redis), Postgres, etc.
#
# Pensado para sesiones de leads pesadas (>10,000 leads) o features avanzadas.
#
#   python install-pro.py            # auto-evalúa, instala lo que falta, auto-repara
#   python install-pro.py --recheck  # solo revisar qué hay y qué falta (no instala)
#
# - Auto-evaluación: detecta qué paquetes avanzados ya tienes y cuáles faltan.
# - Auto-reparación: reintenta los que fallen (varias estrategias).
# - "Mejor esfuerzo": si un paquete pesado no compila en Windows, NO rompe el
#   resto; lo reporta y sigue. La instalación básica sigue funcionando intacta.
# ============================================================================
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Windows usa cp1252 y crashea con → o acentos. Forzar UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO = Path(__file__).resolve().parent

# --- Colores ---
def _supports_color() -> bool:
    if os.name == "nt":
        return os.environ.get("WT_SESSION") or os.environ.get("TERM") or True
    return sys.stdout.isatty()
_C = _supports_color()
def c(code): return code if _C else ""
G, Y, R, B, BOLD, NC = (c("\033[0;32m"), c("\033[1;33m"), c("\033[0;31m"),
                         c("\033[0;34m"), c("\033[1m"), c("\033[0m"))
def ok(m):   print(f"{G}OK{NC}  {m}")
def warn(m): print(f"{Y}!!{NC}  {m}")
def err(m):  print(f"{R}XX{NC}  {m}", file=sys.stderr)
def step(m): print(f"\n{B}{BOLD}> {m}{NC}")


# ============================================================================
# Paquetes avanzados (import : pip). "core" = recomendado; "heavy" = puede
# fallar en algunos Windows (lo intentamos best-effort, sin romper nada).
# ============================================================================
PRO_PACKAGES = {
    # OLAP analytics rápido sobre la base (muy útil para mega-corridas)
    "duckdb":        {"pip": "duckdb>=0.10",        "tier": "core"},
    # PDF parsing (detectar agencias en bases de sorteos)
    "pypdf":         {"pip": "pypdf>=4.0",          "tier": "core"},
    # Colas para mega-corridas (>10K) distribuidas
    "redis":         {"pip": "redis>=5.0",          "tier": "core"},
    "celery":        {"pip": "celery[redis]>=5.3",  "tier": "core"},
    # Postgres directo (alternativa a SQLite para volúmenes enormes)
    "sqlalchemy":    {"pip": "sqlalchemy>=2.0",     "tier": "core"},
    "psycopg2":      {"pip": "psycopg2-binary>=2.9","tier": "heavy"},
    # Deep OSINT
    "holehe":        {"pip": "holehe>=1.61",        "tier": "heavy"},
    "maigret":       {"pip": "maigret>=0.4",        "tier": "heavy"},
    # Anti-bot avanzado (navegadores; pesados)
    "patchright":    {"pip": "patchright>=1.40",    "tier": "heavy"},
    "nodriver":      {"pip": "nodriver>=0.30",      "tier": "heavy"},
    "botasaurus":    {"pip": "botasaurus>=4.0",     "tier": "heavy"},
}


def _py() -> str:
    """Python a usar: el del venv del proyecto si existe, si no el actual."""
    venv = REPO / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return str(venv) if venv.exists() else sys.executable


def _installed(py: str) -> dict:
    """Devuelve {import_name: bool} de qué paquetes PRO están disponibles."""
    mods = list(PRO_PACKAGES.keys())
    code = (
        "import importlib.util as u; "
        "mods=" + repr(mods) + "; "
        "print('\\n'.join([m+'='+('1' if u.find_spec(m) else '0') for m in mods]))"
    )
    r = subprocess.run([py, "-c", code], capture_output=True, text=True)
    out = {}
    for line in (r.stdout or "").splitlines():
        if "=" in line:
            k, v = line.strip().split("=", 1)
            out[k] = (v == "1")
    return out


def _pip_install(py: str, pkg: str) -> bool:
    base = [py, "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check"]
    r = subprocess.run(base + [pkg])
    if r.returncode == 0:
        return True
    # Reintento con timeout mayor
    r = subprocess.run(base + ["--default-timeout=180", pkg])
    return r.returncode == 0


def recheck(py: str) -> dict:
    step("Auto-evaluación: qué hay instalado y qué falta")
    state = _installed(py)
    for mod, meta in PRO_PACKAGES.items():
        tag = "(recomendado)" if meta["tier"] == "core" else "(opcional/pesado)"
        if state.get(mod):
            ok(f"{mod} {tag} — ya instalado")
        else:
            warn(f"{mod} {tag} — FALTA")
    return state


def main() -> None:
    args = set(sys.argv[1:])
    print("=" * 60)
    print(f"  {BOLD}Agente Fénix v5 — Instalador PRO (cargas pesadas){NC}")
    print(f"  Carpeta: {REPO}")
    print("=" * 60)

    py = _py()
    if not (REPO / "requirements-full.txt").exists():
        err("No encuentro requirements-full.txt. ¿Estás en la carpeta del proyecto?")
        sys.exit(1)

    # Verificar que la instalación BÁSICA exista primero
    base_check = subprocess.run(
        [py, "-c", "import importlib.util as u,sys; sys.exit(0 if u.find_spec('supabase') else 1)"]
    )
    if base_check.returncode != 0:
        warn("La instalación básica no está completa. Corre primero el instalador básico")
        warn("(INSTALAR-WINDOWS.bat o 'python install.py'). Continúo igual con lo PRO…")

    state = recheck(py)

    if "--recheck" in args:
        print("\nSolo revisión (no instalé nada). Quita --recheck para instalar lo que falta.")
        return

    missing = [m for m in PRO_PACKAGES if not state.get(m)]
    if not missing:
        print()
        ok("Ya tienes TODAS las capacidades PRO instaladas. Nada que hacer.")
        return

    step(f"Instalando {len(missing)} paquete(s) PRO que faltan (auto-reparable)")
    py_base = [py, "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check"]
    subprocess.run(py_base + ["--upgrade", "pip"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    failed_core, failed_heavy = [], []
    for mod in missing:
        meta = PRO_PACKAGES[mod]
        print(f"\n  → Instalando {mod} ({meta['pip']}) …")
        if _pip_install(py, meta["pip"]):
            # verificar que de verdad importe
            chk = subprocess.run(
                [py, "-c", f"import importlib.util as u,sys; sys.exit(0 if u.find_spec('{mod}') else 1)"]
            )
            if chk.returncode == 0:
                ok(f"{mod} instalado y verificado")
                continue
        # Falló
        if meta["tier"] == "core":
            failed_core.append(mod)
            warn(f"{mod} (recomendado) falló. Lo reintentaré al final.")
        else:
            failed_heavy.append(mod)
            warn(f"{mod} (opcional/pesado) no se pudo instalar. Se omite sin afectar el resto.")

    # Reintento final solo para los "core" que fallaron
    if failed_core:
        step("Reintento de paquetes recomendados que fallaron")
        still = []
        for mod in failed_core:
            print(f"\n  → Reintentando {mod} …")
            if _pip_install(py, PRO_PACKAGES[mod]["pip"]):
                ok(f"{mod} instalado en el reintento")
            else:
                still.append(mod)
        failed_core = still

    # Resumen
    print("\n" + "=" * 60)
    final = _installed(py)
    n_ok = sum(1 for m in PRO_PACKAGES if final.get(m))
    print(f"  {G}{BOLD}INSTALACIÓN PRO TERMINADA{NC}  ({n_ok}/{len(PRO_PACKAGES)} capacidades activas)")
    print("=" * 60)
    if failed_core:
        warn(f"Recomendados que NO se instalaron: {', '.join(failed_core)}")
        warn("Causa común: falta un compilador o internet inestable. Reintenta más tarde.")
    if failed_heavy:
        warn(f"Opcionales pesados omitidos (no afectan el uso normal): {', '.join(failed_heavy)}")
        print("   Estos solo se usan para OSINT muy profundo o anti-bot extremo.")
    if not failed_core and not failed_heavy:
        ok("Todas las capacidades PRO quedaron activas.")
    print()
    print("Tu Agente Fénix ahora soporta sesiones de leads MÁS PESADAS.")
    print("Reinicia opencode/Claude Code si estaba abierto.")
    print("=" * 60)


if __name__ == "__main__":
    main()
