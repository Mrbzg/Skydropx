#!/usr/bin/env bash
# ============================================================================
#  Agente Fénix v5 — Instalador para macOS / Linux (doble clic)
# ============================================================================
#  Haz DOBLE CLIC en este archivo (macOS) o ejecútalo, y espera a que termine.
#  No necesitas saber nada técnico.
# ============================================================================
cd "$(dirname "$0")" || exit 1

echo "============================================================"
echo "   AGENTE FÉNIX v5 — Instalación automática"
echo "============================================================"
echo ""
echo " Esto instalará todo lo necesario. Tarda 1-3 minutos."
echo ""

# Buscar Python 3
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done

if [ -z "$PY" ]; then
  echo "============================================================"
  echo " [!] FALTA PYTHON"
  echo "============================================================"
  echo ""
  echo " Instala Python una sola vez:"
  echo "   macOS:  abre la App Store o https://www.python.org/downloads/"
  echo "   Linux:  sudo apt install python3 python3-venv python3-pip"
  echo ""
  read -r -p "Presiona Enter para salir..."
  exit 1
fi

echo " Python encontrado. Instalando el Agente Fénix..."
echo ""

"$PY" install.py
RESULT=$?

echo ""
if [ "$RESULT" -eq 0 ]; then
  echo "============================================================"
  echo "   ✓ TODO LISTO  |  El Agente Fénix ya está instalado"
  echo "============================================================"
  echo ""
  echo " Ahora abre opencode o Claude Code y escribe:"
  echo ""
  echo "     /skills"
  echo ""
  echo " Verás 'agente-fenix' en la lista. Listo para usar."
  echo " (Si opencode ya estaba abierto, ciérralo y ábrelo de nuevo.)"
else
  echo "============================================================"
  echo "   Hubo un problema durante la instalación"
  echo "============================================================"
  echo " Revisa los mensajes de arriba."
fi
echo ""
read -r -p "Presiona Enter para cerrar..."
