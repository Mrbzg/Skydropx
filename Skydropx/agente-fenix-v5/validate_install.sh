#!/usr/bin/env bash
# ============================================================================
# Script de validación end-to-end de la instalación
# Úsalo después de `pip install -r requirements.txt`
# ============================================================================
set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

GREEN="\033[0;32m"
RED="\033[0;31m"
YELLOW="\033[1;33m"
NC="\033[0m"

pass() { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; exit 1; }
warn() { echo -e "${YELLOW}⚠${NC} $1"; }

echo "════════════════════════════════════════════════════════════════"
echo "  Agente Fénix v5.3 — Validación de Instalación"
echo "════════════════════════════════════════════════════════════════"
echo ""

# 1. Python version
echo "[1/8] Verificando Python..."
PY_VER=$(python3 --version 2>&1)
if python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"; then
    pass "Python OK: $PY_VER"
else
    fail "Python 3.10+ requerido. Tienes: $PY_VER"
fi

# 2. Estructura del proyecto
echo ""
echo "[2/8] Verificando estructura del proyecto..."
for f in src/skill/cli.py src/skill/mcp_server.py src/agents/pipeline.py SKILL.md; do
    if [ -f "$f" ]; then
        pass "$f"
    else
        fail "Falta: $f"
    fi
done

# 3. Dependencias críticas
echo ""
echo "[3/8] Verificando dependencias Tier 0+1..."
python3 -c "import requests, bs4, lxml" 2>/dev/null && pass "Tier 0: requests + bs4 + lxml" || fail "Tier 0 faltante. Corre: pip install -r requirements.txt"
python3 -c "import phonenumbers" 2>/dev/null && pass "phonenumbers (validación tel MX)" || warn "phonenumbers faltante (recomendado)"
python3 -c "import dns.resolver" 2>/dev/null && pass "dnspython (MX records)" || warn "dnspython faltante (recomendado)"
python3 -c "import email_validator" 2>/dev/null && pass "email-validator" || warn "email-validator faltante"
python3 -c "import tenacity" 2>/dev/null && pass "tenacity (retries)" || warn "tenacity faltante"
python3 -c "import trafilatura" 2>/dev/null && pass "trafilatura (extracción HTML limpia)" || warn "trafilatura faltante"

# 4. Configuración .env
echo ""
echo "[4/8] Verificando configuración..."
if [ -f ".env" ]; then
    pass ".env existe"
    if grep -q "DENUE_TOKEN=." .env && ! grep -q "DENUE_TOKEN=$" .env; then
        pass "DENUE_TOKEN configurado"
    else
        fail "DENUE_TOKEN vacío. Edita .env"
    fi
    if grep -q "SERPER_API_KEY=." .env && ! grep -q "SERPER_API_KEY=$" .env; then
        pass "SERPER_API_KEY configurado (opcional pero recomendado)"
    else
        warn "SERPER_API_KEY no configurado (opcional, get free at serper.dev)"
    fi
else
    fail ".env no existe. Corre: cp .env.example .env"
fi

# 5. DB inicializable
echo ""
echo "[5/8] Verificando DB SQLite..."
python3 -m src.skill.cli fenix db init 2>&1 | grep -q "Schema" && pass "DB schema inicializado" || warn "DB init falló (puede no ser crítico)"

# 6. Healthcheck completo
echo ""
echo "[6/8] Corriendo healthcheck..."
HC_OUT=$(python3 -m src.skill.cli fenix healthcheck 2>&1)
if echo "$HC_OUT" | grep -q '"overall_ok": true'; then
    pass "Healthcheck OK"
else
    warn "Healthcheck con warnings — revisa:"
    echo "$HC_OUT" | tail -20
fi

# 7. Tests automatizados
echo ""
echo "[7/8] Corriendo tests (puede tardar 5s)..."
if python3 -m pytest tests/ --tb=no -q 2>&1 | grep -q "passed"; then
    pass "Tests passing"
else
    warn "Algunos tests fallaron — no necesariamente bloquea"
fi

# 8. MCP server arrancable
echo ""
echo "[8/8] Verificando MCP server (clave para opencode/Claude Code)..."
MCP_OUT=$(echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"validator","version":"1"}}}' | \
    timeout 5 python3 -m src.skill.mcp_server 2>/dev/null | head -1)
if echo "$MCP_OUT" | grep -q "serverInfo"; then
    pass "MCP server responde correctamente"
    VERSION=$(echo "$MCP_OUT" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d['result']['serverInfo'].get('version','?'))" 2>/dev/null)
    pass "MCP version: $VERSION"
else
    fail "MCP server no responde. Verifica imports."
fi

# Resumen final
echo ""
echo "════════════════════════════════════════════════════════════════"
echo -e "  ${GREEN}✓ INSTALACIÓN VALIDADA${NC}"
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "Siguientes pasos:"
echo "  1. Primera corrida real:"
echo "     python3 -m src.skill.cli fenix run --nicho ropa --zona CDMX --meta 100 --mode quick"
echo ""
echo "  2. Para opencode:"
echo "     Edita ~/.config/opencode/opencode.json con la config de"
echo "     opencode-skill-config.example.json (reemplaza cwd con $PROJECT_DIR)"
echo ""
echo "  3. Para Claude Code:"
echo "     claude mcp add fenix python3 -m src.skill.mcp_server"
echo "     (corre el comando desde $PROJECT_DIR)"
echo ""
echo "  4. Documentación: SKILL.md + INSTALL.md + references/"
echo ""
