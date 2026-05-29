#!/usr/bin/env python3
# ============================================================================
# Agente Fénix v5 — Instalador universal (Windows / macOS / Linux)
# ============================================================================
# Hace TODO en un solo comando, sin bash. Funciona en PowerShell, CMD, Warp,
# Terminal de Mac y cualquier shell de Linux.
#
#   python install.py                 # instala todo (venv + deps + skill + MCP)
#   python install.py --no-venv       # usa el Python del sistema (sin venv)
#   python install.py --uninstall     # desinstala la skill de opencode y Claude
#
# Idempotente: se puede correr varias veces sin romper nada.
# ============================================================================
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# Windows usa cp1252 y crashea al imprimir → o acentos. Forzar UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO = Path(__file__).resolve().parent  # se puede reubicar a la carpeta de opencode
SKILL_NAME = "agente-fenix"
IS_WIN = os.name == "nt"

# --- Colores (se desactivan solos si la terminal no los soporta) ---
def _supports_color() -> bool:
    if IS_WIN:
        # Windows 10+ moderno y Warp/Terminal soportan ANSI
        return os.environ.get("WT_SESSION") or os.environ.get("TERM") or True
    return sys.stdout.isatty()

_C = _supports_color()
def c(code: str) -> str: return code if _C else ""
G, Y, R, B, BOLD, NC = (c("\033[0;32m"), c("\033[1;33m"), c("\033[0;31m"),
                         c("\033[0;34m"), c("\033[1m"), c("\033[0m"))
def ok(m):   print(f"{G}OK{NC}  {m}")
def warn(m): print(f"{Y}!!{NC}  {m}")
def err(m):  print(f"{R}XX{NC}  {m}", file=sys.stderr)
def step(m): print(f"\n{B}{BOLD}> {m}{NC}")


# ============================================================================
# Helpers
# ============================================================================
def venv_python(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts/python.exe" if IS_WIN else "bin/python")


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, **kw)


def link_or_copy(src: Path, dst: Path) -> None:
    """Crea symlink; si el SO no deja (Windows sin permisos), copia."""
    import shutil
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        if dst.is_dir() and not dst.is_symlink():
            shutil.rmtree(dst, ignore_errors=True)
        else:
            try: dst.unlink()
            except OSError: pass
    try:
        os.symlink(src, dst, target_is_directory=src.is_dir())
    except (OSError, NotImplementedError):
        # Fallback: copiar (Windows sin "modo desarrollador")
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)


def install_skill_to(dest_dir: Path) -> None:
    """Instala SKILL.md + references en dest_dir/<skill-name>/."""
    skill_dir = dest_dir / SKILL_NAME
    link_or_copy(REPO / "SKILL.md", skill_dir / "SKILL.md")
    link_or_copy(REPO / "references", skill_dir / "references")


def _strip_jsonc(text: str) -> str:
    """Quita comentarios // y /* */ y comas finales, para parsear JSONC."""
    import re
    # quita /* ... */
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    # quita // ... (pero no dentro de strings: heurística simple linea por linea)
    out = []
    for line in text.splitlines():
        in_str = False
        esc = False
        cut = None
        for i, ch in enumerate(line):
            if esc:
                esc = False; continue
            if ch == "\\":
                esc = True; continue
            if ch == '"':
                in_str = not in_str; continue
            if ch == "/" and i + 1 < len(line) and line[i+1] == "/" and not in_str:
                cut = i; break
        out.append(line[:cut] if cut is not None else line)
    text = "\n".join(out)
    # quita comas finales antes de } o ]
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return text


def _read_config_safe(cfg_path: Path):
    """Lee un opencode.json/.jsonc de forma tolerante. Devuelve dict o None si falla."""
    try:
        raw = cfg_path.read_text(encoding="utf-8")
    except Exception:
        return None
    try:
        return json.loads(raw)
    except Exception:
        try:
            return json.loads(_strip_jsonc(raw))
        except Exception:
            return None


def merge_opencode_json(cfg_path: Path, py_for_mcp: str) -> bool:
    """Agrega el MCP a la config de opencode SIN destruir lo existente.

    - Detecta opencode.json y opencode.jsonc (usa el que exista).
    - Soporta comentarios (JSONC).
    - Si no puede leer la config existente, NO la toca (devuelve False) para
      no romper la configuración del usuario.
    Formato opencode: clave "mcp", type=local, command=[lista], environment, enabled.
    """
    # Si existe .jsonc, ese es el archivo real de opencode
    jsonc = cfg_path.with_suffix(".jsonc")
    target = jsonc if jsonc.exists() else cfg_path
    target.parent.mkdir(parents=True, exist_ok=True)

    data = {}
    if target.exists():
        parsed = _read_config_safe(target)
        if parsed is None:
            warn(f"No pude leer {target.name} sin riesgo -> NO lo modifico (déjalo como está).")
            return False
        data = parsed

    data.setdefault("$schema", "https://opencode.ai/config.json")
    data.setdefault("mcp", {})
    data["mcp"][SKILL_NAME] = {
        "type": "local",
        "command": [py_for_mcp, "-m", "src.skill.mcp_server"],
        "environment": {"PYTHONPATH": str(REPO)},
        "enabled": True,
        # El pipeline de leads puede tardar minutos; subimos el timeout para que
        # opencode no corte la herramienta (default suele ser ~30-60s).
        "timeout": 600000,
    }
    if isinstance(data.get("mcpServers"), dict):
        data["mcpServers"].pop(SKILL_NAME, None)
        if not data["mcpServers"]:
            data.pop("mcpServers", None)

    # Backup de seguridad antes de escribir (sin destruir el original)
    if target.exists():
        try:
            import shutil
            shutil.copy2(target, target.with_name(target.name + ".fenix-backup"))
        except Exception:
            pass
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return True


# ============================================================================
# Pasos
# ============================================================================
def detect_python() -> str:
    step("1/7 Detectando Python")
    v = sys.version_info
    if v < (3, 10):
        err(f"Python {v.major}.{v.minor} es muy viejo. Necesitas 3.10+")
        sys.exit(1)
    ok(f"Python {v.major}.{v.minor}.{v.micro} ({sys.executable})")
    return sys.executable


def setup_venv(system_python: str, use_venv: bool) -> str:
    if not use_venv:
        step("2/7 Entorno virtual (omitido por --no-venv)")
        return system_python
    step("2/7 Creando entorno virtual (.venv)")
    venv_dir = REPO / ".venv"
    if not venv_dir.exists():
        r = run([system_python, "-m", "venv", str(venv_dir)])
        if r.returncode != 0:
            warn("No pude crear venv; uso el Python del sistema.")
            return system_python
        ok("venv creado en .venv")
    else:
        ok("venv ya existía (.venv)")
    py = venv_python(venv_dir)
    return str(py) if py.exists() else system_python


# Paquetes que el agente DEBE poder importar para funcionar (nombre import : nombre pip)
REQUIRED_IMPORTS = {
    "requests": "requests",
    "bs4": "beautifulsoup4",
    "lxml": "lxml",
    "phonenumbers": "phonenumbers",
    "dns": "dnspython",
    "email_validator": "email-validator",
    "tenacity": "tenacity",
    "trafilatura": "trafilatura",
    "pytrends": "pytrends",
    "yaml": "pyyaml",
    "supabase": "supabase",
}


def _missing_imports(py: str) -> list[str]:
    """Devuelve la lista de nombres de import que NO se pueden importar con ese Python."""
    mods = list(REQUIRED_IMPORTS.keys())
    code = (
        "import importlib.util as u; "
        "mods=" + repr(mods) + "; "
        "print('\\n'.join([m for m in mods if u.find_spec(m) is None]))"
    )
    r = subprocess.run([py, "-c", code], capture_output=True, text=True)
    return [l.strip() for l in (r.stdout or "").splitlines() if l.strip()]


def install_deps(py: str) -> str:
    """Instala dependencias y SE AUTO-REPARA hasta que todo sea importable.

    Devuelve el path de Python que terminó funcionando (puede recrear el venv).
    """
    step("3/7 Instalando dependencias (auto-reparable)")
    base = [py, "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check"]
    run(base + ["--upgrade", "pip"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Estrategia 1: instalar todo el requirements
    run(base + ["-r", str(REPO / "requirements.txt")])

    # Verificar imports REALES (no confiar en el "OK" de pip)
    missing = _missing_imports(py)
    if not missing:
        ok("Dependencias instaladas y verificadas (todo importable)")
        return py

    # Estrategia 2: reinstalar SOLO lo que falta, forzando
    warn(f"Faltan módulos: {', '.join(missing)}. Reparando automáticamente…")
    pkgs = sorted({REQUIRED_IMPORTS[m] for m in missing if m in REQUIRED_IMPORTS})
    run(base + ["--force-reinstall", "--no-deps", *pkgs])
    run(base + pkgs)  # otra pasada con deps
    missing = _missing_imports(py)
    if not missing:
        ok("Dependencias reparadas y verificadas")
        return py

    # Estrategia 3: el venv puede estar corrupto -> recrearlo desde cero
    venv_dir = REPO / ".venv"
    if str(venv_dir) in py:
        warn("El entorno virtual parece dañado. Recreándolo desde cero…")
        import shutil
        try:
            shutil.rmtree(venv_dir, ignore_errors=True)
        except Exception:
            pass
        sys_py = sys.executable
        if run([sys_py, "-m", "venv", str(venv_dir)]).returncode == 0:
            new_py = str(venv_python(venv_dir))
            base2 = [new_py, "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check"]
            run(base2 + ["--upgrade", "pip"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            run(base2 + ["-r", str(REPO / "requirements.txt")])
            if not _missing_imports(new_py):
                ok("Entorno recreado y dependencias verificadas")
                return new_py
            py = new_py

    # Estrategia 4: último intento con --user (sin venv)
    warn("Último intento: instalando con --user…")
    run([py, "-m", "pip", "install", "--no-cache-dir", "--user",
         "-r", str(REPO / "requirements.txt")])
    missing = _missing_imports(py)
    if not missing:
        ok("Dependencias instaladas (modo --user) y verificadas")
        return py

    err(f"No pude instalar automáticamente: {', '.join(missing)}")
    err("Posibles causas: sin internet, antivirus bloqueando, o disco lleno.")
    err("Reintenta con internet estable. El instalador se auto-repara al volver a correr.")
    sys.exit(1)


def setup_env() -> None:
    step("4/7 Configurando .env")
    env, example = REPO / ".env", REPO / ".env.example"
    if env.exists():
        ok(".env ya existe (no lo toco)")
    elif example.exists():
        import shutil
        shutil.copy2(example, env)
        ok(".env creado desde .env.example")
        warn("Edita .env y agrega tu DENUE_TOKEN (y Supabase si lo usas).")
    else:
        warn("No hay .env.example; omito.")


# Todas las ubicaciones conocidas donde opencode / Claude Code buscan skills.
# Incluye variantes "skill"/"skills" y rutas de proyecto + globales para máxima
# compatibilidad entre versiones y sistemas operativos.
def _skill_locations() -> list[Path]:
    home = Path.home()
    return [
        # --- En la RAÍZ del proyecto (cuando opencode se abre aquí) ---
        REPO / ".opencode" / "skill",
        REPO / ".opencode" / "skills",
        REPO / ".claude" / "skills",
        # --- GLOBALES (disponibles en CUALQUIER carpeta, para siempre) ---
        home / ".config" / "opencode" / "skill",
        home / ".config" / "opencode" / "skills",
        home / ".opencode" / "skill",
        home / ".opencode" / "skills",
        home / ".claude" / "skills",
    ]


def install_skill_dirs() -> None:
    step("5/7 Instalando la skill (raíz del proyecto + GLOBAL)")
    n = 0
    for loc in _skill_locations():
        try:
            install_skill_to(loc)
            n += 1
        except Exception as e:  # noqa: BLE001
            warn(f"No pude instalar en {loc}: {e}")
    ok(f"Skill instalada en {n} ubicaciones (incluye globales).")
    ok("Quedará disponible en /skills desde CUALQUIER carpeta.")


def register_mcp(py: str) -> None:
    step("6/7 Registrando el MCP server")
    home = Path.home()
    wrote = 0
    skipped = 0
    for cfg in (REPO / "opencode.json", home / ".config" / "opencode" / "opencode.json"):
        if merge_opencode_json(cfg, py):
            wrote += 1
        else:
            skipped += 1
    if wrote:
        ok(f"MCP escrito en opencode.json ({wrote} ubicación/es)")
    if skipped:
        warn("Alguna config de opencode no se pudo leer y se dejó intacta (para no romperla).")
        warn("Si el MCP no aparece, agrégalo manual con docs/opencode-skill-config.example.json")
    # Claude Code CLI si existe
    import shutil as _sh
    if _sh.which("claude"):
        try:
            listing = run(["claude", "mcp", "list"], capture_output=True, text=True)
            if SKILL_NAME in (listing.stdout or ""):
                ok("MCP ya registrado en Claude Code")
            else:
                run(["claude", "mcp", "add", SKILL_NAME, py, "--", "-m",
                     "src.skill.mcp_server"], cwd=str(REPO),
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                ok("MCP registrado en Claude Code")
        except Exception:
            warn("No pude registrar el MCP en Claude (regístralo manual si lo usas).")
    else:
        warn("CLI 'claude' no detectado (normal si solo usas opencode).")


def _probe_mcp(py: str):
    """Lanza el MCP y devuelve (n_tools, stderr). n_tools=0 si falló."""
    reqs = (
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n'
        '{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
        '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'
    )
    env = dict(os.environ, PYTHONPATH=str(REPO), PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    try:
        pr = subprocess.run([py, "-m", "src.skill.mcp_server"], input=reqs,
                            capture_output=True, text=True, cwd=str(REPO),
                            env=env, timeout=60)
    except subprocess.TimeoutExpired:
        return 0, "timeout"
    n = 0
    for line in (pr.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            if msg.get("id") == 2:
                n = len(msg["result"]["tools"])
        except Exception:
            pass
    return n, (pr.stderr or "")


def verify(py: str) -> None:
    step("7/7 Verificando el MCP server (auto-reparable)")
    n, stderr = _probe_mcp(py)
    if n > 0:
        ok(f"MCP server responde correctamente ({n} tools)")
        return

    # AUTO-REPARACIÓN: detectar módulo faltante en el stderr y reinstalarlo
    import re
    m = re.search(r"No module named ['\"]([\w\.]+)['\"]", stderr or "")
    if m:
        mod = m.group(1).split(".")[0]
        pkg = REQUIRED_IMPORTS.get(mod, mod)
        warn(f"El MCP no pudo importar '{mod}'. Instalándolo automáticamente…")
        run([py, "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check", pkg])
        n, stderr = _probe_mcp(py)
        if n > 0:
            ok(f"Reparado. MCP server responde correctamente ({n} tools)")
            return

    # Segundo intento: reinstalar TODO el requirements y reprobar
    warn("Reinstalando todas las dependencias y reintentando…")
    run([py, "-m", "pip", "install", "--no-cache-dir", "--force-reinstall",
         "-r", str(REPO / "requirements.txt")])
    n, stderr = _probe_mcp(py)
    if n > 0:
        ok(f"Reparado. MCP server responde correctamente ({n} tools)")
        return

    err("El MCP no arrancó tras varios intentos de auto-reparación.")
    if stderr:
        err("Detalle técnico (últimas líneas):")
        for ln in (stderr.strip().splitlines() or [])[-6:]:
            err("   " + ln)
    sys.exit(1)


def uninstall() -> None:
    step("Desinstalando skill 'agente-fenix'")
    import shutil
    for d in [loc / SKILL_NAME for loc in _skill_locations()]:
        shutil.rmtree(d, ignore_errors=True)
        if d.is_symlink():
            try: d.unlink()
            except OSError: pass
    ok("Skill removida (proyecto + global)")
    home = Path.home()
    candidates = []
    for base in (REPO / "opencode", home / ".config" / "opencode" / "opencode"):
        candidates += [base.with_suffix(".json"), base.with_suffix(".jsonc")]
    for cfg in candidates:
        if cfg.exists():
            data = _read_config_safe(cfg)
            if data is None:
                continue
            changed = False
            for key in ("mcp", "mcpServers"):
                if isinstance(data.get(key), dict) and data[key].pop(SKILL_NAME, None) is not None:
                    changed = True
            if changed:
                cfg.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                ok(f"MCP quitado de {cfg}")
    if __import__("shutil").which("claude"):
        run(["claude", "mcp", "remove", SKILL_NAME],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ok("Desinstalación completa.")


def _opencode_home() -> Path:
    """Carpeta de configuración de opencode (donde vivirá el proyecto, protegido)."""
    return Path.home() / ".config" / "opencode"


def relocate_to_safe_home() -> bool:
    """Copia el proyecto a ~/.config/opencode/agente-fenix-app/ y re-lanza desde ahí.

    Así el código queda 'pegado' a opencode: el usuario no lo borrará por error
    (tendría que borrar la carpeta de opencode entera). Devuelve True si re-lanzó.
    """
    global REPO
    dest = _opencode_home() / "agente-fenix-app"

    # Si ya estamos corriendo DESDE el destino, no reubicar (evita bucle)
    try:
        if REPO.resolve() == dest.resolve():
            return False
    except Exception:
        pass

    if os.environ.get("FENIX_RELOCATED") == "1":
        return False

    step("0/7 Copiando el proyecto a la carpeta de opencode (a prueba de borrado)")
    import shutil
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Copiar todo MENOS lo pesado/efímero Y los archivos de instalación.
        # IMPORTANTE: NO copiar AGENTS.md ni los instaladores a la copia "viva",
        # porque opencode leería AGENTS.md y se comportaría como "asistente
        # instalador" en vez de como Agente Fénix. La copia instalada solo debe
        # tener SKILL.md como instrucción del agente.
        # NO copiar AGENTS.md a la copia "viva": opencode lo leería y se
        # comportaría como "asistente instalador" en vez de como Agente Fénix.
        # (Sí copiamos install.py/install-pro.py para poder reinstalar/actualizar.)
        def _ignore(_dir, names):
            skip = {".venv", "__pycache__", ".git", ".opencode", ".claude",
                    "node_modules", ".cache", ".swarm", "tmp", "AGENTS.md"}
            return [n for n in names if n in skip or n.endswith(".pyc")]
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(REPO, dest, ignore=_ignore)
        ok(f"Proyecto copiado a: {dest}")
    except Exception as e:  # noqa: BLE001
        warn(f"No pude copiar a la carpeta de opencode ({e}). Instalo desde aquí mismo.")
        return False

    # Re-lanzar el instalador desde la copia segura
    new_installer = dest / "install.py"
    if not new_installer.exists():
        warn("No encontré install.py en el destino; instalo desde aquí.")
        return False
    ok("Continuando la instalación desde la copia segura…")
    env = dict(os.environ, FENIX_RELOCATED="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    r = subprocess.run([sys.executable, str(new_installer), *sys.argv[1:]], env=env)
    sys.exit(r.returncode)


# ============================================================================
# Main
# ============================================================================
def main() -> None:
    args = set(sys.argv[1:])
    print("=" * 60)
    print(f"  {BOLD}Agente Fénix v5 — Instalador universal{NC}")
    print(f"  Repo: {REPO}")
    print(f"  SO: {'Windows' if IS_WIN else os.uname().sysname if hasattr(os,'uname') else 'POSIX'}")
    print("=" * 60)

    if "--uninstall" in args:
        uninstall()
        return
    if "-h" in args or "--help" in args:
        print(__doc__ if __doc__ else "Uso: python install.py [--no-venv|--uninstall]")
        return

    # Reubicar el proyecto a la carpeta de opencode (protegido), salvo que se pida lo contrario
    if "--no-relocate" not in args and os.environ.get("FENIX_RELOCATED") != "1":
        relocate_to_safe_home()  # si reubica, re-lanza y termina aquí

    use_venv = "--no-venv" not in args
    system_python = detect_python()
    py = setup_venv(system_python, use_venv)
    py = install_deps(py)  # puede recrear el venv y devolver otro python
    setup_env()
    install_skill_dirs()
    register_mcp(py)
    verify(py)

    print("\n" + "=" * 60)
    print(f"  {G}{BOLD}TODO LISTO{NC}")
    print("=" * 60)
    print(f"\n{BOLD}Ahora, en opencode (abierto en esta carpeta):{NC}")
    print("    /skills            -> verás 'agente-fenix'")
    print(f"\n{BOLD}O pídele en lenguaje natural:{NC}")
    print("    > corre fenix para 50 leads de ropa en CDMX")
    print("    > usa fenix para verificar el teléfono 5512345678")
    print(f"\n{Y}Importante:{NC} cierra y vuelve a abrir opencode para que detecte la skill.")
    print("=" * 60)
    # Sugerir el instalador PRO para cargas pesadas
    print()
    print(f"{BOLD}¿Vas a generar MÁS de 10,000 leads por sesión, o usar OSINT profundo?{NC}")
    print("Instala las capacidades PRO (opcional, solo si lo necesitas):")
    print(f"   {BOLD}Windows:{NC}      doble clic en  INSTALAR-PRO-WINDOWS.bat")
    print(f"   {BOLD}Mac/Linux:{NC}    python install-pro.py")
    print("Con lo básico ya instalado, manejas sesiones de hasta ~10,000 leads sin problema.")
    print("=" * 60)


if __name__ == "__main__":
    main()
