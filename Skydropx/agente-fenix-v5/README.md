# 🔥 Agente Fénix v5.3 — Lead Generation OSINT para Skydropx

> Skill cross-runtime (opencode / Claude Code / cualquier MCP client) que genera
> leads B2B/B2C/D2C/C2C/C2B del mercado mexicano usando **100% fuentes públicas y gratuitas**.
> **Costo: $0 USD/mes**. **Datos REALES, nunca inventados.**

---

## 🟢 ¿Solo quieres USARLO? (instalación fácil)

**Forma recomendada (evita los bloqueos de seguridad de Windows):**
1. Descarga y extrae el ZIP donde quieras.
2. Abre **opencode** o **Claude Code** dentro de esa carpeta.
3. Escribe: **`instala el agente fenix`** → el asistente hace todo solo.
4. Cierra y reabre, escribe `/skills` → usa **agente-fenix**.

**Alternativa (doble clic):** ejecuta `INSTALAR-WINDOWS.bat` (Windows, pedirá
permisos de administrador → "Sí") o `INSTALAR-MAC-LINUX.command` (Mac/Linux).
> Si Windows muestra "Control inteligente de aplicaciones bloqueó...", es normal
> con cualquier script descargado. La mejor solución: **clic derecho en el ZIP
> ANTES de extraerlo → Propiedades → marca "Desbloquear" → Aceptar**, y luego
> extrae. Así salen todos los archivos desbloqueados. (O usa la forma recomendada.)

> Una sola vez. Después aparece en `/skills` en cualquier carpeta, para siempre.
> Más detalle en **`LEEME-PRIMERO.txt`**.

---

## ⚡ Quickstart (5 minutos)

```bash
# 1. Clonar + entrar
git clone <tu-repo-aqui> agente-fenix-v5
cd agente-fenix-v5

# 2. Instalar (Tier 0+1: lo mínimo viable)
pip install -r requirements.txt

# 3. Configurar token DENUE (gratis en inegi.org.mx)
cp .env.example .env
nano .env   # → llena DENUE_TOKEN

# 4. Validar todo
bash validate_install.sh

# 5. Primera corrida
python3 -m src.skill.cli fenix run --nicho ropa --zona CDMX --meta 100 --mode quick
```

**Si todos los pasos pasan ✓ → estás listo.** Continúa a "Integración con opencode/Claude Code" abajo.

---

## 🎯 ¿Qué hace el sistema?

Pipeline de **10 agentes** en cadena, todos opcionales y configurables:

```
1. TrendScout    — detecta tendencias (Google + Wikipedia + nichos sugeridos)
2. Scout         — 9 fuentes (DENUE + ML + Cámaras + Maps + Dorks + SocialShops)
3. Hunter        — crawling web (email/tel/wa/owner/tech_stack)
4. Verifier      — SMTP/MX + E.164 con Google libphonenumber
5. Persist       — DB SQLite + dedup persistente jerárquico
6. Re-Enrich     — DomainFinder + EmailInferencer (rescata huérfanos)
7. Profiler      — ICP dual + scoring + plan Skydropx
8. DeepEnrich    — Holehe/Maigret/PhoneInfoga (opcional, top leads)
9. Dispatcher    — CSV v4.0 + JSON + HubSpot-ready
10. SelfImprover — memoria + sugerencias para próxima corrida
```

**Características clave v5.3:**
- ✅ Sistema TIER de calidad (PREMIUM/GOLD/SILVER/BRONZE) para export selectivo
- ✅ ICP classifier dual (ICP_1_PYME 50-100 envíos | ICP_2_ENTERPRISE 3PL/agencias)
- ✅ Exclusiones 3 capas (técnica + MX + 284 empresas Skydropx outbound)
- ✅ Mapeo `canal→sources` (canal=social activa TikTok/IG/FB automáticamente)
- ✅ Eventos comerciales activos (17 catálogo) + agencias detrás de campañas
- ✅ Strategy de Serper: `reserve` (default) preserva 2,500 créditos free
- ✅ Healthcheck pre-run + Checkpoint+Resume + Retry queue + Auto-throttle
- ✅ Tests: **123 passing**

---

## 📦 Niveles de instalación

| Tier | Tiempo | Tamaño | Funcionalidad |
|---|---|---|---|
| **Tier 0+1** (default) | 5 min | ~20 MB | Pipeline completo, validación MX, sin OSINT pesado |
| **Tier 2** | +10 min | +50 MB | + Holehe/Maigret + Patchright anti-bot |
| **Tier 3** | +15 min | +100 MB | + PostgreSQL + DuckDB + Tor |

Ver **`docs/INSTALL.md`** para guía paso a paso por tier + dependencias del SO + troubleshooting.

---

## ⚡ Instalación turnkey — un comando lo hace TODO

**La forma recomendada.** Copia el proyecto donde quieras (p. ej. tu Escritorio),
ábrelo en opencode y dile **"instala todo"** — o corre directamente:

```bash
cd agente-fenix-v5
python install.py
```

> **Windows (PowerShell/CMD/Warp):** usa `python install.py`.
> **macOS/Linux:** usa `python3 install.py` (o `bash ./setup.sh` si prefieres bash).

`install.py` deja TODO listo automáticamente (es idempotente y no rompe tu config):

1. Detecta Python 3.10+ (si falta, te dice cómo instalarlo).
2. Crea un entorno virtual aislado en `.venv`.
3. Instala **todas** las dependencias de `requirements.txt`.
4. Crea `.env` desde la plantilla si no existe.
5. Instala la **skill en la raíz del proyecto** (`.opencode/skills/agente-fenix`
   y `.claude/skills/agente-fenix`) **y** global (para usarla en cualquier carpeta).
6. Registra el **MCP server** en `opencode.json` y en Claude Code.
7. Verifica que el MCP arranca y lista las 28 tools.

Luego abre opencode en la carpeta y usa el comando:

```
/skills
```

Verás **`agente-fenix`** en la lista. ¡Listo! También puedes pedir en lenguaje natural:

```
> corre fenix para 50 leads de ropa en CDMX
> usa fenix para verificar el teléfono 5512345678
> qué eventos comerciales hay activos hoy
```

> **Tip:** como opencode lee `AGENTS.md`, basta con decirle *"instala todo"* dentro
> de opencode y él ejecutará `setup.sh` por ti.

Opciones:
```bash
python install.py --no-venv      # usa el Python del sistema (sin venv)
python install.py --uninstall    # desinstala la skill de opencode y Claude Code
```

> ¿Prefieres hacerlo manual? Sigue las secciones de abajo.

---

## 🔌 Integración con opencode

### Setup en 3 pasos

**Paso 1:** Copia la configuración de ejemplo
```bash
cat docs/opencode-skill-config.example.json
```

**Paso 2:** Edita tu `~/.config/opencode/opencode.json` y agrega:
```json
{
  "mcpServers": {
    "agente-fenix": {
      "command": "python3",
      "args": ["-m", "src.skill.mcp_server"],
      "cwd": "/RUTA/ABSOLUTA/A/agente-fenix-v5",
      "env": {
        "PYTHONPATH": "/RUTA/ABSOLUTA/A/agente-fenix-v5"
      }
    }
  }
}
```

**Paso 3:** Linkea la skill (para que opencode lea SKILL.md)
```bash
mkdir -p ~/.config/opencode/skills
ln -s "$(pwd)" ~/.config/opencode/skills/agente-fenix
```

**Paso 4:** Reinicia opencode y prueba:
```
> usa fenix para verificar el teléfono 5512345678
> corre fenix para 100 leads de ropa CDMX
> qué eventos hay activos esta semana
> encuentra leads de TikTok ropa D2C Monterrey
```

---

## 🔌 Integración con Claude Code

```bash
cd /ruta/agente-fenix-v5
claude mcp add fenix python3 -m src.skill.mcp_server

# Validar:
claude mcp list   # debe aparecer 'fenix (running)'
```

Después en cualquier conversación de Claude Code:
```
> corre fenix para 500 leads de calzado D2C en Jalisco
> verifica si juan@gruposierras.mx es persona activa
> qué agencia está detrás de datumax.mx
```

---

## 🛠 Tools MCP expuestas (28 totales)

| Categoría | Tools |
|---|---|
| Pipeline | `fenix_healthcheck`, `fenix_run`, `fenix_ask` |
| DENUE | `denue_cuantificar`, `denue_search` |
| Búsqueda | `search_dorks`, `trends_now`, `events_active`, `event_search_plan` |
| Validación | `verify_email`, `verify_phone`, `detect_tech_stack` |
| OSINT | `osint_holehe`, `osint_maigret`, `osint_phoneinfoga`, `osint_budget`, `harvest_domain` |
| Campañas | `find_agency` |
| DB | `db_stats`, `db_companies`, `dedup_audit` |
| Export | `export_hubspot_csv` |
| Plans | `plans_list`, `plans_run`, `plans_history` |

---

## 📂 Estructura del proyecto

```
agente-fenix-v5/
├── LEEME-PRIMERO.txt                 ← EMPIEZA AQUÍ (instrucciones simples)
├── INSTALAR-WINDOWS.bat              ← doble clic para instalar (Windows)
├── INSTALAR-MAC-LINUX.command        ← doble clic para instalar (Mac/Linux)
├── install.py                        ← instalador universal (lo usan los .bat/.command)
├── setup.sh                          ← instalador alternativo (bash)
│
├── SKILL.md                          ← skill principal (lee Claude/opencode)
├── AGENTS.md                         ← instrucciones para el asistente
├── README.md                         ← este archivo
├── requirements.txt                  ← dependencias (mínimo viable)
├── requirements-full.txt             ← dependencias (features avanzadas)
├── .env / .env.example               ← configuración (tokens)
├── docker-compose.yml                ← servicios opcionales (SearXNG, Redis, etc.)
│
├── docs/                             ← documentación técnica
│   ├── INSTALL.md                    ← guía instalación manual paso a paso
│   ├── opencode-skill-config.example.json
│   └── claude-code-mcp-config.example.json
│
├── src/
│   ├── agents/        ← pipeline de 10 agentes
│   ├── core/          ← modelos, config, throttle, healthcheck, etc.
│   ├── db/            ← SQLite, dedup persistente, retry queue
│   ├── sources/       ← DENUE, ML, dorks, cámaras, social_shops, etc.
│   ├── extraction/    ← Hunter, EmailInferencer, DomainFinder, OSINT tools
│   ├── scoring/       ← ICP classifier, tiered filter
│   ├── export/        ← HubSpot CSV exporter
│   └── skill/         ← CLI + MCP server + Discovery Protocol
│
├── data/              ← catálogos (eventos_mx, exclusions_skydropx, nicho_scian)
├── plans/             ← plantilla EJEMPLO.yaml + tus campañas
├── tests/             ← 123 tests pytest
├── references/        ← 18 docs detalladas (cli, fuentes, instalación, etc.)
└── output/            ← CSVs/JSONs generados
```

---

## 🚀 Comandos más útiles

```bash
# Pipeline completo (auto-elige strategy según meta)
fenix run --nicho ropa --zona CDMX --meta 500 --modelo B2C

# Con canal social (TikTok/IG/FB)
fenix run --nicho ropa --canal social --zona "Nuevo Leon" --meta 100 --sources social_shops

# Con re-enrich activo (rescata huérfanos)
fenix run --nicho calzado --zona Jalisco --meta 1000 \
  --enrich-max 100 --re-enrich-max 200

# Discovery con texto libre
fenix ask "necesito 200 leads de joyería en GDL para D2C" --run

# Tendencias actuales
fenix trends all --suggest-niches

# Eventos comerciales activos
fenix events active

# Detector de agencia
fenix agency --dominio datumax.mx

# Export tier=PREMIUM para HubSpot
fenix hubspot --tier PREMIUM --run-id "semana1"

# DB y dedup
fenix db stats
fenix dedup-audit report

# Healthcheck + checkpoint + retry + throttle
fenix healthcheck
fenix checkpoint list
fenix retry stats
fenix throttle stats
```

---

## 📊 Rendimiento real (medido)

| Setup | 1K leads | Velocidad |
|---|---|---|
| DENUE solo | 60s | 1,500 leads/min |
| DENUE + Hunter 5% | 2.3 min | 650/min |
| DENUE + Hunter + Re-enrich 10% | 6.3 min | 245/min |
| Social shops (TikTok/IG/FB) | 97s | 58/min (más profundo) |

**Recomendación:** corridas chicas a demanda (500-2K leads, 5-15 min) en vez
de mega-corridas. Ver `references/instalacion-y-rendimiento.md` para análisis completo.

---

## 💰 Costo: $0 USD/mes

| Componente | Costo |
|---|---|
| Token DENUE/INEGI | $0 (gratis registro) |
| Mercado Libre API pública | $0 |
| SearXNG self-hosted | $0 (Docker, sin límites) |
| DuckDuckGo HTML | $0 |
| Cámaras MX scraping | $0 (datos públicos) |
| Tu laptop o Oracle Cloud Free | $0 |
| Serper.dev (2,500 free lifetime) | $0 inicial |
| **Total** | **$0 USD/mes** |

---

## 🛡 Compliance

- ✅ **LFPDPPP** (Ley Federal MX de Protección de Datos Personales)
- ✅ Solo datos públicos (DENUE, dorks indexados, padrones cámaras)
- ✅ Respeta `robots.txt` automáticamente
- ✅ Opt-out registrable: `fenix db opt-out --value email@cliente.com`
- ✅ Retención 90 días para leads sin contacto
- ✅ Sin scraping agresivo de LinkedIn

---

## 📚 Documentación

| Doc | Para qué |
|---|---|
| `SKILL.md` | Skill principal (Claude/opencode lo lee) |
| `docs/INSTALL.md` | Instalación paso a paso con todos los escenarios |
| `references/cli.md` | Todos los comandos CLI |
| `references/instalacion-y-rendimiento.md` | Benchmarks reales |
| `references/serper-strategy-y-canales.md` | Backends + canal social |
| `references/exclusions-icp.md` | Sistema de exclusiones + ICP dual |
| `references/trends-events.md` | Tendencias + eventos + agencias |
| `references/gaps-resueltos.md` | Healthcheck/Checkpoint/Retry/Throttle |
| `references/plans-y-modelo-operacional.md` | Plans YAML manuales |
| `references/integracion-crm.md` | HubSpot mapping |
| `references/troubleshooting.md` | Errores comunes |

---

## ✅ Estado actual

| Métrica | v5.3 |
|---|---|
| Tests passing | **123 / 123 ✓** |
| Tools MCP | **24** |
| Comandos CLI | **28+** |
| Sources de leads | **9** |
| Search backends tiered | **4** (SearXNG → DDG → OpenSERP → Serper) |
| Agentes en pipeline | **10** |
| Empresas en exclusions | **284** + 142 dominios |
| Eventos comerciales catalog | **17** |
| Agencias MX conocidas | **42** |
| **Costo operativo** | **$0 USD/mes** |

---

## 🆘 Troubleshooting

Ver `docs/INSTALL.md` sección "Troubleshooting común" o `references/troubleshooting.md`.

Quick fixes:
- `ModuleNotFoundError: No module named 'src'` → corre desde la raíz del proyecto
- `DENUE_TOKEN no configurado` → edita `.env` con tu token de inegi.org.mx
- Tests skipped → `pip install phonenumbers`
- MCP no detectado en opencode → usar rutas ABSOLUTAS en `cwd`

---

**¿Listo para arrancar?**

```bash
bash validate_install.sh    # te dice exactamente qué falta
```
