# AGENTS.md — Agente Fénix v5

Instrucciones para agentes de código (opencode, Claude Code, Codex, etc.) que
abren este proyecto. Léelas antes de actuar.

## Qué es este proyecto

Agente Fénix es una **skill de inteligencia comercial y generación de leads OSINT
para Skydropx** (logística en México). Se distribuye como:

- Una **skill** (`SKILL.md` + `references/`) que el agente carga automáticamente.
- Un **MCP server** (`python3 -m src.skill.mcp_server`) con ~28 herramientas.
- Una **CLI** (`python3 -m src.skill.cli fenix ...`).

## ⚡ Instalación automática ("instala todo")

Si el usuario dice **"instala todo"**, **"instálalo"**, **"setup"**, **"déjalo listo"**
o equivalente, ejecuta **un solo comando** y deja todo funcionando:

**Usa el instalador universal en Python (funciona en Windows, macOS y Linux):**

```bash
python install.py
```

> En Windows usa `python install.py`. En macOS/Linux puede ser `python3 install.py`.
> (Existe también `setup.sh` para quienes prefieran bash en macOS/Linux, pero
> `install.py` es el recomendado porque funciona en PowerShell/CMD/Warp sin bash.)

`install.py` es idempotente y hace TODO automáticamente:

1. Detecta Python 3.10+ (si falta, indica cómo instalarlo).
2. Crea un entorno virtual aislado en `.venv`.
3. Instala **todas** las dependencias de `requirements.txt` (incluye el cliente
   de Supabase y pyyaml).
4. Crea `.env` desde `.env.example` si no existe.
5. Instala la **skill en la raíz del proyecto** (`.opencode/skills/agente-fenix`
   y `.claude/skills/agente-fenix`) **y** global (`~/.config/opencode/skills`,
   `~/.claude/skills`).
6. Registra el **MCP server** en `opencode.json` (proyecto y global) y en Claude Code.
7. Verifica que el MCP arranca y lista las tools.

Después de correrlo, **dile al usuario** que abra opencode en esta carpeta y use
`/skills` (verá `agente-fenix`), o que pida en lenguaje natural cosas como
"corre fenix para 50 leads de ropa en CDMX".

### Si el usuario abrió opencode/Claude en OTRA carpeta

Si el usuario dice "instala el agente fénix" pero NO estás dentro de la carpeta del
proyecto (no ves `install.py` en el directorio actual), entonces:

1. Búscalo en las ubicaciones típicas de Windows/Mac:
   - `~/Downloads/agente-fenix-v5`, `~/Descargas/agente-fenix-v5`
   - `~/Desktop/agente-fenix-v5`, `~/Escritorio/agente-fenix-v5`
   - subcarpetas del directorio actual (busca un archivo `install.py` junto a `SKILL.md`)
2. Cuando lo encuentres, entra a esa carpeta y ejecuta `python install.py` ahí.
3. Si no lo encuentras, pregúntale al usuario dónde extrajo el ZIP (pídele que arrastre
   la carpeta a la terminal para obtener la ruta), entra ahí y ejecuta el instalador.

La instalación es GLOBAL: tras correr `install.py` una vez, la skill aparece en
`/skills` desde cualquier carpeta, para siempre. No hay que reinstalar.

### Reglas para el agente al instalar

- Ejecuta `python install.py` (o `python3 install.py`) y **muestra el resultado**.
  No reimplementes los pasos a mano salvo que el script falle.
- Si `setup.sh` reporta que **falta Python**, comparte el comando de instalación
  correcto para el SO del usuario y detente (no inventes rutas).
- Si la instalación de dependencias falla por red o permisos, repórtalo textualmente
  y sugiere `python install.py --no-venv` como alternativa.
- **Nunca** edites `opencode.json` ni `~/.claude.json` manualmente: el script ya hace
  el merge respetando la configuración existente.
- **Nunca** subas secretos: `.env` está en `.gitignore`. No lo muestres ni lo comitees.

## Comandos útiles (post-instalación)

```bash
# Healthcheck de la infraestructura
.venv/bin/python -m src.skill.cli fenix healthcheck

# Correr el pipeline de leads
.venv/bin/python -m src.skill.cli fenix run --nicho ropa --zona CDMX --meta 100 --mode quick

# Sincronizar a Supabase (si está configurado en .env)
.venv/bin/python -m src.skill.cli fenix sync healthcheck
.venv/bin/python -m src.skill.cli fenix sync push
```

## Convenciones del proyecto

- Base de datos: **SQLite local** (source of truth) + **Supabase cloud** opcional
  (espejo, modo dual). Ver `docs/INSTALL.md`.
- Solo fuentes públicas y gratuitas. **Nunca inventar** emails, teléfonos ni RFCs.
- Cumplir LFPDPPP y respetar `robots.txt` (ya integrado).
- Para la lógica operativa completa de la skill, sigue `SKILL.md`.

## Desinstalar

```bash
python install.py --uninstall
```
