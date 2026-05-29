#!/usr/bin/env bash
# ============================================================================
# Agente Fénix v5 — SETUP TODO-EN-UNO (turnkey)
# ============================================================================
# Un solo comando que deja TODO listo para usar con /skills en opencode
# y Claude Code:
#
#   1. Detecta Python (o lo reporta si falta)
#   2. Crea un entorno virtual aislado (.venv)
#   3. Instala TODAS las dependencias (requirements.txt)
#   4. Crea .env desde la plantilla si no existe
#   5. Instala la SKILL en la raíz del proyecto (.opencode/skills + .claude/skills)
#      y también global (~/.config/opencode/skills, ~/.claude/skills)
#   6. Registra el MCP server en opencode.json y Claude Code
#   7. Verifica que el MCP arranca y lista las tools
#
# Uso:
#   ./setup.sh                 # todo automático
#   ./setup.sh --no-venv       # usa el Python del sistema (sin venv)
#   ./setup.sh --uninstall     # quita la skill de todos lados
#
# Idempotente: se puede correr varias veces sin romper nada.
# ============================================================================
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_NAME="agente-fenix"
VENV_DIR="$REPO_DIR/.venv"
USE_VENV=1
ACTION="install"

for arg in "$@"; do
  case "$arg" in
    --no-venv)   USE_VENV=0 ;;
    --uninstall) ACTION="uninstall" ;;
    -h|--help)   ACTION="help" ;;
  esac
done

# --- Colores ---
G="\033[0;32m"; Y="\033[1;33m"; R="\033[0;31m"; B="\033[0;34m"; BOLD="\033[1m"; NC="\033[0m"
ok()   { echo -e "${G}✓${NC} $*"; }
warn() { echo -e "${Y}⚠${NC} $*"; }
err()  { echo -e "${R}✗${NC} $*" >&2; }
step() { echo -e "\n${B}${BOLD}▶ $*${NC}"; }

show_help() {
  cat <<EOF
Agente Fénix v5 — setup.sh

Uso:
  ./setup.sh                 Instala todo (venv + deps + skill + MCP)
  ./setup.sh --no-venv       Usa Python del sistema en vez de .venv
  ./setup.sh --uninstall     Desinstala la skill de opencode y Claude Code
  ./setup.sh --help          Esta ayuda
EOF
}

# ============================================================================
# 1) Detectar Python
# ============================================================================
detect_python() {
  step "1/7 Detectando Python"
  for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then
      local v
      v="$("$c" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo "0.0")"
      local major="${v%%.*}" minor="${v##*.}"
      if [ "${major:-0}" -ge 3 ] && [ "${minor:-0}" -ge 10 ]; then
        PYTHON="$c"; ok "Python encontrado: $("$c" --version) ($c)"; return 0
      fi
    fi
  done
  err "No se encontró Python 3.10+. Instálalo:"
  echo "    macOS:  brew install python@3.12"
  echo "    Ubuntu: sudo apt install python3 python3-venv python3-pip"
  echo "    Windows: https://www.python.org/downloads/  (marca 'Add to PATH')"
  exit 1
}

# ============================================================================
# 2) Entorno virtual
# ============================================================================
setup_venv() {
  if [ "$USE_VENV" -eq 0 ]; then
    step "2/7 Entorno virtual (omitido por --no-venv)"
    PY="$PYTHON"
    return 0
  fi
  step "2/7 Creando entorno virtual (.venv)"
  if [ ! -d "$VENV_DIR" ]; then
    "$PYTHON" -m venv "$VENV_DIR" 2>/dev/null || {
      warn "No pude crear venv (¿falta python3-venv?). Continúo con Python del sistema."
      USE_VENV=0; PY="$PYTHON"; return 0
    }
    ok "venv creado en .venv"
  else
    ok "venv ya existía (.venv)"
  fi
  # Resolver el binario python del venv (Unix o Windows)
  if   [ -x "$VENV_DIR/bin/python" ];        then PY="$VENV_DIR/bin/python"
  elif [ -x "$VENV_DIR/Scripts/python.exe" ]; then PY="$VENV_DIR/Scripts/python.exe"
  else PY="$PYTHON"; warn "No encontré el python del venv; uso el del sistema."; fi
}

# ============================================================================
# 3) Dependencias
# ============================================================================
install_deps() {
  step "3/7 Instalando dependencias (requirements.txt)"
  "$PY" -m pip install --upgrade pip >/dev/null 2>&1 || warn "No pude actualizar pip (sigo)."
  if "$PY" -m pip install -r "$REPO_DIR/requirements.txt"; then
    ok "Dependencias instaladas"
  else
    err "Falló la instalación de dependencias. Revisa el error arriba."
    exit 1
  fi
}

# ============================================================================
# 4) .env
# ============================================================================
setup_env() {
  step "4/7 Configurando .env"
  if [ -f "$REPO_DIR/.env" ]; then
    ok ".env ya existe (no lo toco)"
  elif [ -f "$REPO_DIR/.env.example" ]; then
    cp "$REPO_DIR/.env.example" "$REPO_DIR/.env"
    ok ".env creado desde .env.example"
    warn "Edita .env y agrega tu DENUE_TOKEN (y credenciales de Supabase si las usas)."
  else
    warn "No hay .env.example; omito."
  fi
}

# ============================================================================
# 5) Instalar la SKILL (proyecto + global, opencode + Claude)
# ============================================================================
link_skill() {
  # $1 = directorio destino de la skill
  local dest="$1"
  mkdir -p "$dest"
  ln -sfn "$REPO_DIR/SKILL.md"   "$dest/SKILL.md"   2>/dev/null || cp -f "$REPO_DIR/SKILL.md" "$dest/SKILL.md"
  ln -sfn "$REPO_DIR/references" "$dest/references" 2>/dev/null || cp -rf "$REPO_DIR/references" "$dest/references"
}

install_skill_dirs() {
  step "5/7 Instalando la skill (raíz del proyecto + global)"
  # En la RAÍZ del proyecto (lo que pidió el usuario): opencode lee .opencode/skills y .claude/skills
  link_skill "$REPO_DIR/.opencode/skills/$SKILL_NAME"
  link_skill "$REPO_DIR/.claude/skills/$SKILL_NAME"
  ok "Skill en la raíz: .opencode/skills/$SKILL_NAME y .claude/skills/$SKILL_NAME"
  # Global (disponible en cualquier proyecto)
  link_skill "$HOME/.config/opencode/skills/$SKILL_NAME"
  link_skill "$HOME/.claude/skills/$SKILL_NAME"
  ok "Skill global: ~/.config/opencode/skills y ~/.claude/skills"
}

# ============================================================================
# 6) Registrar el MCP server
# ============================================================================
register_mcp() {
  step "6/7 Registrando el MCP server"
  local py_for_mcp="$PY"

  # opencode.json (merge, sin pisar config existente) — proyecto y global
  for cfg in "$REPO_DIR/opencode.json" "$HOME/.config/opencode/opencode.json"; do
    mkdir -p "$(dirname "$cfg")"
    CFG="$cfg" NAME="$SKILL_NAME" PY_MCP="$py_for_mcp" REPO="$REPO_DIR" "$PY" - <<'PYEOF'
import json, os
cfg=os.environ["CFG"]; name=os.environ["NAME"]; py=os.environ["PY_MCP"]; repo=os.environ["REPO"]
data={}
if os.path.exists(cfg):
    try:
        data=json.load(open(cfg,encoding="utf-8"))
    except Exception:
        print("  AVISO: no pude leer", cfg, "-> lo dejo intacto (no lo rompo)."); raise SystemExit(0)
data.setdefault("$schema","https://opencode.ai/config.json")
data.setdefault("mcp",{})
data["mcp"][name]={
    "type": "local",
    "command": [py,"-m","src.skill.mcp_server"],
    "environment": {"PYTHONPATH": repo},
    "enabled": True,
}
if isinstance(data.get("mcpServers"),dict):
    data["mcpServers"].pop(name,None)
    if not data["mcpServers"]: data.pop("mcpServers",None)
json.dump(data, open(cfg,"w",encoding="utf-8"), indent=2, ensure_ascii=False)
PYEOF
  done
  ok "MCP escrito en opencode.json (proyecto + global)"

  # Claude Code CLI (si existe)
  if command -v claude >/dev/null 2>&1; then
    if claude mcp list 2>/dev/null | grep -q "$SKILL_NAME"; then
      ok "MCP ya registrado en Claude Code"
    else
      ( cd "$REPO_DIR" && claude mcp add "$SKILL_NAME" "$py_for_mcp" -- -m src.skill.mcp_server >/dev/null 2>&1 ) \
        && ok "MCP registrado en Claude Code" \
        || warn "No pude registrar el MCP en Claude (regístralo manual si lo usas)."
    fi
  else
    warn "CLI 'claude' no detectado (normal si solo usas opencode)."
  fi
}

# ============================================================================
# 7) Verificación
# ============================================================================
verify() {
  step "7/7 Verificando el MCP server"
  local n
  n=$(printf '%s\n%s\n%s\n' \
    '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
    '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
    '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
    | (cd "$REPO_DIR" && PYTHONPATH="$REPO_DIR" timeout 40 "$PY" -m src.skill.mcp_server 2>/dev/null) \
    | "$PY" -c "import sys,json
n=0
for l in sys.stdin:
    l=l.strip()
    if l and json.loads(l).get('id')==2: n=len(json.loads(l)['result']['tools'])
print(n)" 2>/dev/null || echo 0)
  if [ "${n:-0}" -gt 0 ]; then
    ok "MCP server responde correctamente ($n tools)"
  else
    err "El MCP no respondió. Revisa que las dependencias se instalaron bien."
    exit 1
  fi
}

# ============================================================================
# Uninstall
# ============================================================================
uninstall() {
  step "Desinstalando skill '$SKILL_NAME'"
  rm -rf "$REPO_DIR/.opencode/skills/$SKILL_NAME" \
         "$REPO_DIR/.claude/skills/$SKILL_NAME" \
         "$HOME/.config/opencode/skills/$SKILL_NAME" \
         "$HOME/.claude/skills/$SKILL_NAME" 2>/dev/null
  # limpiar dirs padre vacíos
  for d in "$REPO_DIR/.opencode/skills" "$REPO_DIR/.opencode" \
           "$REPO_DIR/.claude/skills" "$REPO_DIR/.claude" \
           "$HOME/.config/opencode/skills" "$HOME/.claude/skills"; do
    rmdir "$d" 2>/dev/null || true
  done
  ok "Skill removida (proyecto + global)"
  command -v claude >/dev/null 2>&1 && claude mcp remove "$SKILL_NAME" >/dev/null 2>&1 && ok "MCP quitado de Claude" || true
  for cfg in "$REPO_DIR/opencode.json" "$HOME/.config/opencode/opencode.json"; do
    [ -f "$cfg" ] && CFG="$cfg" NAME="$SKILL_NAME" "${PYTHON:-python3}" - <<'PYEOF'
import json,os
cfg=os.environ["CFG"]; name=os.environ["NAME"]
try:
    d=json.load(open(cfg,encoding="utf-8"))
    changed=False
    for key in ("mcp","mcpServers"):
        if isinstance(d.get(key),dict) and d[key].pop(name,None) is not None: changed=True
    if changed:
        json.dump(d,open(cfg,"w",encoding="utf-8"),indent=2,ensure_ascii=False)
        print("  MCP quitado de", cfg)
except Exception: pass
PYEOF
  done
  ok "Desinstalación completa."
}

# ============================================================================
# Main
# ============================================================================
echo "============================================================"
echo -e "  ${BOLD}Agente Fénix v5 — Setup turnkey${NC}"
echo "  Repo: $REPO_DIR"
echo "============================================================"

case "$ACTION" in
  help) show_help; exit 0 ;;
  uninstall) detect_python; uninstall; exit 0 ;;
esac

detect_python
setup_venv
install_deps
setup_env
install_skill_dirs
register_mcp
verify

echo ""
echo "============================================================"
echo -e "  ${G}${BOLD}✓ TODO LISTO${NC}"
echo "============================================================"
echo ""
echo -e "${BOLD}Ahora abre opencode en esta carpeta y usa:${NC}"
echo "    /skills            → verás 'agente-fenix' en la lista"
echo ""
echo -e "${BOLD}O simplemente pídele en lenguaje natural:${NC}"
echo "    > corre fenix para 50 leads de ropa en CDMX"
echo "    > usa fenix para verificar el teléfono 5512345678"
echo "    > qué eventos comerciales hay activos hoy"
echo ""
[ "$USE_VENV" -eq 1 ] && echo -e "${Y}Nota:${NC} se usó el entorno .venv (el MCP ya apunta a él automáticamente)."
echo "============================================================"
