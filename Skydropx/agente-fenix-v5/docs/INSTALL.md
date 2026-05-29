# 📦 Instalación Completa — Agente Fénix v5.3

> Guía paso a paso para tener el sistema 100% funcional en tu laptop.
> Tiempo total: **10-15 minutos**. Costo: **$0 USD**.

---

## 🎯 Requisitos del sistema operativo

### Windows / macOS / Linux

| Requisito | Mínimo | Recomendado | Cómo verificar |
|---|---|---|---|
| **Python** | 3.10 | 3.11 o 3.12 | `python3 --version` |
| **pip** | 23+ | actualizado | `pip --version` |
| **Git** | cualquiera | reciente | `git --version` |
| **RAM** | 4 GB | 8 GB | corridas hasta 5K leads OK con 4GB |
| **Disco** | 1 GB libre | 5 GB | DB SQLite crece ~1MB / 1K leads |
| **Internet** | requerido | banda ancha | DENUE + crawling web |

### Opcional (solo si activas Tier 2+)

| Componente | Para qué | Cómo instalar |
|---|---|---|
| **Docker** | SearXNG (search backend gratis) | docker.com/products/docker-desktop |
| **Go 1.21+** | Compilar PhoneInfoga (opcional) | golang.org/dl |
| **Chromium** | Anti-bot Patchright/Nodriver | se instala con patchright |
| **PostgreSQL** | Solo si pasas de 50K leads | postgresql.org/download |
| **Ollama** | LLM local para ScrapeGraphAI | ollama.com |

---

## ⚡ Instalación rápida (5 minutos) — Setup mínimo viable

Este setup te da el **80% de la funcionalidad** y arranca en 5 min.

### Paso 1: Clonar el repo
```bash
git clone <tu-repo-aqui> agente-fenix-v5
cd agente-fenix-v5
```

### Paso 2: Crear ambiente virtual (recomendado)
```bash
# Linux / macOS
python3 -m venv .venv
source .venv/bin/activate

# Windows (PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### Paso 3: Instalar dependencias Tier 0+1 (~20 MB)
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### Paso 4: Configurar `.env` (token DENUE)
```bash
cp .env.example .env
nano .env   # o tu editor favorito
```

**Variables MÍNIMAS para que funcione:**
```bash
DENUE_TOKEN=tu_token_inegi          # OBLIGATORIO
SQLITE_PATH=data/fenix.sqlite       # default OK
OUTPUT_DIR=output                   # default OK
```

**Cómo obtener tu DENUE_TOKEN (gratis, 2 minutos):**
1. Ve a https://www.inegi.org.mx/app/api/denue/v1/tutorial.html
2. Crear cuenta gratuita
3. "Mis tokens" → generar nuevo
4. Pegar en `.env` como `DENUE_TOKEN=xxx-xxx-xxx`

**Variables OPCIONALES (recomendadas):**
```bash
SERPER_API_KEY=tu_serper_key        # 2,500 búsquedas Google gratis (serper.dev/api-key)
SERPER_STRATEGY=reserve              # usa Serper solo cuando los gratis fallan (default)
SEARXNG_URL=http://localhost:8888    # si levantaste SearXNG en Docker
```

### Paso 5: Verificar instalación
```bash
python3 -m src.skill.cli fenix healthcheck
```

**Output esperado:**
```
✓ Healthcheck OK — sistema listo para correr pipeline
  ✓ denue_token: DENUE OK (212,251 comercios CDMX accesibles)
  ✓ search_backends: ['ddg']
  ✓ db_accessible: DB OK
  ✓ output_dir OK
  ✓ Disk OK 17 GB libres
```

### Paso 6: Primera corrida real (3 minutos)
```bash
python3 -m src.skill.cli fenix run \
  --nicho ropa --zona CDMX --meta 100 --mode quick
```

### Paso 7: Verificar tests
```bash
python3 -m pytest tests/ --tb=no -q
# Esperado: 123 passed
```

---

## 🚀 Setup completo (15 minutos) — Producción semanal

Adiciona deep OSINT (Holehe, Maigret) y anti-bot avanzado (Patchright).

### A: Tier 2 — Deep OSINT
```bash
pip install holehe maigret pagodo
```

**Verificación:**
```bash
python3 -m src.skill.cli fenix osint stats
# Debe mostrar: holehe=true, maigret=true, pagodo=true
```

### B: Tier 2 — Anti-bot avanzado (para sitios con Cloudflare)
```bash
pip install patchright nodriver botasaurus
patchright install chromium
```

**Verificación:**
```bash
python3 -m src.skill.cli fenix antibot stats
# Debe mostrar L1/L2/L3 = true
```

### C: PDF parsing (para detectar agencias en bases de sorteos)
```bash
pip install pypdf
```

### D: SearXNG (Search backend gratis perpetuo) — RECOMENDADO
```bash
# Requiere Docker instalado
docker compose up -d searxng

# Verificar
curl http://localhost:8888/healthz
# Esperado: OK

# Agregar a .env:
echo "SEARXNG_URL=http://localhost:8888" >> .env
```

**¿Por qué es útil?**
- Resuelve `site:tiktok.com`, `site:instagram.com`, etc.
- Sin rate-limit como DDG
- Gratis perpetuo
- Reemplaza llamadas a Serper.dev (preserva tus créditos)

---

## 🛠 Setup full (30 min) — Para mega-corridas o casos avanzados

Solo si vas a hacer corridas >10K leads o necesitas analytics avanzados.

### A: Supabase (cloud) — modo DUAL recomendado  ⭐

El sistema soporta **modo combinado**: SQLite local (rápido, offline, *source of
truth*) **+** Supabase en la nube (espejo para backup, dashboards y acceso remoto).
No reemplazas SQLite: lo complementas.

```bash
# 1) Instalar el cliente de Supabase
pip install "supabase>=2.4"

# 2) Crear proyecto gratis en https://supabase.com y obtener credenciales:
#    Dashboard → Settings → API
#      - Project URL       → SUPABASE_URL
#      - service_role key  → SUPABASE_KEY  (secreta; permite escrituras)

# 3) Agregar a .env:
echo "SUPABASE_URL=https://xxxxxxxx.supabase.co" >> .env
echo "SUPABASE_KEY=eyJhbGciOiJ..." >> .env
# (opcional) push automático al terminar cada corrida:
echo "SUPABASE_AUTO_SYNC=true" >> .env

# 4) Crear las tablas en Supabase: copia el contenido de
#    src/db/supabase_schema.sql al SQL Editor de Supabase y dale Run.
#    O imprime las instrucciones con:
python3 -m src.skill.cli fenix sync schema

# 5) Verificar conexión + tablas:
python3 -m src.skill.cli fenix sync healthcheck

# 6) Sincronizar SQLite local → Supabase
python3 -m src.skill.cli fenix sync push          # todo (companies, contacts, jobs)
python3 -m src.skill.cli fenix sync status        # comparar local vs cloud
```

> El push es **incremental** por defecto (solo lo nuevo). Usa `--full` para forzar todo.
> SQLite sigue siendo la base principal; Supabase es el espejo opcional.

### A-bis: PostgreSQL self-hosted (alternativa avanzada, normalmente NO necesaria)
```bash
pip install sqlalchemy psycopg2-binary

# Levantar Postgres con Docker
docker compose up -d postgres

# Agregar a .env:
echo "DATABASE_URL=postgresql://fenix:fenix_local@localhost:5432/fenix" >> .env

# Inicializar schema
python3 -m src.skill.cli fenix db init
```

### B: DuckDB (analytics OLAP rápidos)
```bash
pip install duckdb
# El sistema lo detecta y usa automáticamente para queries de audit
```

### C: Tor (proxies rotativos gratis)
```bash
docker compose --profile proxies up -d tor

# Agregar a .env:
echo "HTTP_PROXY=socks5://localhost:9050" >> .env
echo "HTTPS_PROXY=socks5://localhost:9050" >> .env
```

### D: Ollama + ScrapeGraphAI (LLM local opcional)
```bash
# Instalar Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Modelo ligero
ollama pull llama3.2:3b

# Python wrapper
pip install scrapegraphai

# Activar en .env
echo "AI_PARSER_ENABLED=true" >> .env
```

---

## 📱 Instalación para usar como Skill en opencode

### Paso 1: Tener el sistema funcional (pasos anteriores)

### Paso 2: Localizar tu carpeta de skills opencode
```bash
# Linux / macOS
mkdir -p ~/.config/opencode/skills

# Windows
mkdir %APPDATA%\opencode\skills
```

### Paso 3: Linkear o copiar la skill
```bash
# Opción A: symbolic link (recomendado, se actualiza con git pull)
ln -s "$(pwd)" ~/.config/opencode/skills/agente-fenix

# Opción B: copia (más estable, no se actualiza solo)
cp -r /ruta/agente-fenix-v5 ~/.config/opencode/skills/agente-fenix
```

### Paso 4: Registrar el MCP server en opencode
Edita `~/.config/opencode/opencode.json`:

```json
{
  "mcpServers": {
    "agente-fenix": {
      "command": "python3",
      "args": ["-m", "src.skill.mcp_server"],
      "cwd": "/ruta/absoluta/a/agente-fenix-v5",
      "env": {
        "PYTHONPATH": "/ruta/absoluta/a/agente-fenix-v5"
      }
    }
  }
}
```

### Paso 5: Validar desde opencode
```bash
# En opencode:
> ¿qué skills tengo disponibles?
# debe listar agente-fenix

> usa fenix para verificar el teléfono +52 33 1234 5678
# debe llamar verify_phone y devolver E.164 + región + can_whatsapp
```

---

## 🔌 Instalación para usar como MCP en Claude Code

### Paso 1: Comando one-liner
```bash
cd /ruta/agente-fenix-v5
claude mcp add fenix python3 -m src.skill.mcp_server
```

### Paso 2: Verificar
```bash
claude mcp list
# Debe aparecer: fenix (running)
```

### Paso 3: Usar desde Claude Code
```
> Corre fenix para 100 leads de ropa CDMX
> Verifica si juan@empresa.mx es persona activa
> ¿Qué eventos comerciales hay activos hoy?
> Encuentra leads de TikTok de ropa en Monterrey D2C
```

---

## ✅ Checklist de instalación completa

Marca lo que tienes:

### Mínimo (5 min) — sistema funcional
- [ ] Python 3.10+ instalado
- [ ] Repo clonado
- [ ] Virtual environment activo
- [ ] `pip install -r requirements.txt` ejecutado sin errores
- [ ] `.env` configurado con `DENUE_TOKEN`
- [ ] `fenix healthcheck` muestra "Healthcheck OK"
- [ ] `pytest tests/` muestra "123 passed"

### Recomendado (15 min) — producción
- [ ] Holehe + Maigret instalados (`pip install holehe maigret`)
- [ ] Patchright + Nodriver instalados (anti-bot)
- [ ] `patchright install chromium` ejecutado
- [ ] Docker instalado
- [ ] SearXNG corriendo (`docker compose up -d searxng`)
- [ ] `.env` con `SEARXNG_URL=http://localhost:8888`
- [ ] (Opcional) `SERPER_API_KEY` configurado

### Avanzado (30 min) — mega-corridas
- [ ] PostgreSQL corriendo
- [ ] `DATABASE_URL` en `.env`
- [ ] Tor corriendo (proxies)
- [ ] Ollama instalado (si usas ScrapeGraphAI)

### Integración con asistentes
- [ ] MCP server registrado en Claude Code O opencode
- [ ] Skill linkeada/copiada a `~/.config/opencode/skills/` o `~/.claude/skills/`
- [ ] Probado desde el asistente con un comando real

---

## 🐛 Troubleshooting común

### "ModuleNotFoundError: No module named 'src'"
**Causa:** corriendo desde directorio equivocado.
**Fix:**
```bash
cd /ruta/a/agente-fenix-v5
python3 -m src.skill.cli ...  # usa -m, no python src/skill/cli.py
```

### "DENUE_TOKEN no configurado"
**Causa:** falta variable de entorno.
**Fix:**
```bash
cat .env | grep DENUE
# Si vacío:
echo "DENUE_TOKEN=tu_token_aqui" >> .env
```

### `phonenumbers` no se instala en Windows
**Causa:** falta Microsoft C++ Build Tools.
**Fix:** descarga desde https://visualstudio.microsoft.com/visual-cpp-build-tools/

### Tests skipped en lugar de pasar
**Causa:** falta `phonenumbers` instalado.
**Fix:**
```bash
pip install phonenumbers
pytest tests/  # ahora deberían pasar todos
```

### `patchright install chromium` falla
**Causa:** falta espacio en disco o permisos.
**Fix:**
```bash
# Verificar espacio
df -h
# Patchright necesita ~500 MB para chromium
```

### El MCP server no aparece en opencode
**Causa:** opencode no leyó el archivo o cwd está mal.
**Fix:**
```bash
# Verificar JSON válido:
cat ~/.config/opencode/opencode.json | python3 -m json.tool
# Verificar cwd absoluto:
ls /ruta/absoluta/a/agente-fenix-v5/src/skill/mcp_server.py
```

### Serper devuelve 400 "Query pattern not allowed"
**Causa:** plan free no permite operadores avanzados (`site:`, etc.).
**Fix:** ya está implementado en v5.3 — el sistema auto-simplifica los dorks.
Si sigues viendo el error, verifica que tu Serper key está bien.

---

## 📊 Ejemplo de instalación completa probada

```bash
# 1. Sistema
python3 --version       # 3.13.13 ✓
git --version           # 2.50 ✓
docker --version        # opcional ✓

# 2. Repo + venv
git clone <repo> agente-fenix-v5
cd agente-fenix-v5
python3 -m venv .venv && source .venv/bin/activate

# 3. Deps mínimas
pip install -r requirements.txt
# 8 packages instalados en ~30 segundos:
#  requests, beautifulsoup4, lxml, phonenumbers, dnspython,
#  email-validator, tenacity, trafilatura, pytrends, pytest

# 4. Config
cp .env.example .env
echo "DENUE_TOKEN=tu_token" >> .env

# 5. Verificar
python3 -m src.skill.cli fenix healthcheck
# ✓ Healthcheck OK

# 6. Tests
pytest tests/ -q
# 123 passed in 4s ✓

# 7. Primera corrida
python3 -m src.skill.cli fenix run \
  --nicho ropa --zona CDMX --meta 100 --mode quick
# ⏱ Duración total: 4.2s
# 📁 output/fenix_ropa_*.csv
# 💰 Costo: $0.00

# 8. Export para HubSpot
python3 -m src.skill.cli fenix hubspot --tier PREMIUM

echo "✓ Sistema funcionando"
```

---

## 🆘 Soporte

- Documentación completa: `references/`
- Comandos CLI: `python3 -m src.skill.cli fenix --help`
- Tests: `pytest tests/ -v`
- Logs: `logs/fenix.log`
