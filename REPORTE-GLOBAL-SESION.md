# 📋 REPORTE GLOBAL DE SESIÓN — Agente Fénix v5 (handoff para otra IA)

> **Propósito de este archivo:** Si abres una sesión nueva con otra IA (o conmigo
> más adelante) para seguir trabajando en este proyecto, **lee este archivo primero**.
> Te da TODO el contexto: qué es el proyecto, qué encontramos, qué cambiamos y por qué,
> y cómo debe comportarse el asistente. Con esto el nuevo LLM se pone al día en 2 minutos.
>
> **Compañero de este archivo:** `CHANGELOG-FENIX.md` (control entrada-por-entrada de
> cada cambio: "qué teníamos" vs "qué arreglamos"). Actualízalo en CADA cambio.

**Última actualización:** 2026-05-30
**Estado del proyecto:** ✅ Funcional · 33 herramientas MCP · 164 tests pasando · $0/mes

---

## 0. Cómo debe comportarse el asistente (instrucciones para la IA que lea esto)

- **Idioma:** español (México). El usuario NO es programador técnico avanzado; explica claro.
- **Objetivo rector:** hacerle la vida MÁS FÁCIL al usuario final no técnico. Cada decisión
  se mide contra eso.
- **Filosofía del proyecto (no romper):**
  1. **$0/mes** — solo fuentes públicas y gratuitas. NUNCA proponer APIs de pago
     (Apollo/Clearbit/Hunter/ZoomInfo) ni servicios con costo por lead.
  2. **Datos REALES, nunca inventados** — si no hay dato, se marca como no verificable.
  3. **Mercado mexicano** — DENUE/INEGI, cámaras MX, Mercado Libre, tiendas en redes.
  4. **Cross-runtime** pero el foco es **opencode**.
  5. **A prueba de borrado + auto-instalable** — el usuario no corre comandos.
- **Forma de trabajo esperada:** investigar el código ANTES de cambiar, hacer cambios
  quirúrgicos, **correr los tests** después de cada cambio, y **actualizar
  `CHANGELOG-FENIX.md`**. Mantener todo en un solo MCP (no fragmentar en varios servers).
- **Antes de agregar dependencias externas o MCPs de terceros:** evaluar si choca con la
  filosofía. En esta sesión ya rechazamos varios (ver sección 6).

---

## 1. Qué es el proyecto

**Agente Fénix v5.3** — Skill + servidor MCP de **generación de leads B2B/B2C/D2C OSINT**
para **Skydropx** (logística/paquetería en México). Descubre, verifica y califica empresas
mexicanas que necesitan enviar paquetería, usando solo fuentes gratuitas.

- **Repo del usuario:** `https://github.com/Mrbzg/Skydropx.git`
  - ⚠️ El código real está en la rama **`master`**, carpeta `Skydropx/agente-fenix-v5/`.
  - La rama `main` está **vacía** (solo un commit que borró `.gitignore`). Confunde; el
    usuario debería cambiar la rama default a `master`.
- **Ruta de trabajo en este entorno:** `/home/user/Skydropx/Skydropx/agente-fenix-v5/`

### Arquitectura (3 piezas)
1. **SKILL.md** — el "cerebro"/persona del agente (lo que opencode carga como skill).
2. **MCP server** (`src/skill/mcp_server.py`) — 33 herramientas, JSON-RPC stdio, zero-deps
   para arrancar.
3. **CLI** (`python -m src.skill.cli fenix ...`) — mismo motor por línea de comandos.

### Pipeline de 10 agentes (en `src/agents/pipeline.py`)
```
TrendScout → Scout → Hunter → Verifier → Persist → Re-Enrich → Profiler →
DeepEnrich → Dispatcher → SelfImprover
```
- **Scout** usa fuentes: DENUE, cámaras MX, Mercado Libre, dorks, social_shops.
- **DB:** SQLite local (`data/fenix.sqlite`) con dedup persistente cross-corrida.
- **Export:** CSV v4.0 (26 columnas) + JSON, en `Dispatcher`.
- **Scoring:** DATA_SCORE, SKYDROPX_SCORE (1-5), SALES_PRIORITY, ICP dual + (NUEVO) BANT/MEDDIC.

---

## 2. Estado INICIAL (lo que el usuario tenía al empezar)

- Proyecto completo y funcional en `master`, con **28 tools MCP**, instalador `install.py`,
  `.bat`/`.command` para doble clic, y soporte opencode nativo.
- **Mecanismo a-prueba-de-borrado YA EXISTÍA:** `install.py` (PASO 0,
  `relocate_to_safe_home()`) copia el proyecto a `~/.config/opencode/agente-fenix-app/`
  y registra MCP+skill apuntando ahí. Si el usuario borra la carpeta descargada, sigue vivo.
- **Bugs/carencias que encontramos:** ver sección 3.

---

## 3. Qué CAMBIAMOS en esta sesión (resumen; detalle en CHANGELOG-FENIX.md)

### A) Lanzador auto-instalable `fenix_mcp.py` (NUEVO)
- **Problema:** el usuario inexperto tenía que correr `pip install`.
- **Solución:** `fenix_mcp.py` es el punto de entrada del MCP. La 1ª vez crea un venv
  aislado (`.fenix-venv`), instala dependencias solo, y arranca. Después es instantáneo.
  Reutiliza el `.venv` de `install.py` si existe. Crea `.env` desde la plantilla.
- `install.py` ahora registra el MCP con `fenix_mcp.py` → la instalación a-prueba-de-borrado
  quedó además **auto-reparable**.

### B) Persona/System Prompt fusionado (NUEVO `references/persona-system-prompt.md`)
- Se integró el System Prompt conversacional original (metasecurity, calibración por nivel,
  fases, anti-leak, tono consultor, máx 150 palabras, emoji ⚠) y se referencia desde SKILL.md.

### C) 3 mejoras de valor dentro del MCP (en vez de MCPs externos)
- **`qualify_lead`** — calificación BANT + MEDDIC heurística ($0, sobre datos reales).
- **`lead_provenance`** — atribución por campo (qué fuente aportó cada dato) + confianza.
- **`cache_stats`** + caché TTL (`src/core/cache.py`) — evita repetir búsquedas idénticas.
- BANT/MEDDIC + provenance se calculan AUTOMÁTICO para cada lead en el Profiler.

### D) Robustez de búsqueda + web search como ÚLTIMO recurso
- **Reintentos con backoff** por backend (antes saltaba al primer fallo).
- **5º backend gratis:** DuckDuckGo Lite (fallback robusto). Cascada:
  SearXNG → DuckDuckGo → OpenSERP → DuckDuckGo-Lite → Serper.
- **Filtro de anuncios** en DuckDuckGo (antes colaba `y.js?ad_domain=`).
- `search_dorks` devuelve `web_search_fallback_allowed: true` SOLO cuando TODOS los
  backends se agotan → recién ahí el agente puede usar su web search, avisando que es
  "sin verificar por fuentes Fénix" y verificando con verify_phone/email/tech.
- REGLA #0 suavizada + **REGLA #0.2** en SKILL.md con el orden estricto.

### E) Carpeta de export amigable (DÓNDE guardar los leads)
- **Problema:** los CSV caían en `output/` interno; el usuario no técnico nunca los hallaba.
- **Solución:** `src/core/user_paths.py` resuelve Escritorio/Descargas/Documentos
  (Windows/Mac/Linux, ES/EN, OneDrive). `fenix_run` acepta `destino`. Dos tools nuevas:
  `suggested_locations` (preguntar dónde) y `open_results_folder` (abrir el explorador).
- SKILL.md: **Fase 1.5** = el agente PREGUNTA dónde guardar antes de generar (default Escritorio).

### F) Fixes varios
- **Bug `.env`:** el parser no quitaba comentarios inline → `SQLITE_PATH` quedaba con
  `# default OK`. Corregido en `src/core/config.py`.
- **Bug tests:** `conftest.py` usaba `FenixDB(db_url=...)` en vez de `db_path=`. Corregido.
- **Token DENUE agregado** al `.env`: `55592824-a5c8-4bc4-b71e-5163583287d0` (verificado real).

---

## 4. Inventario de archivos tocados (vs repo original)

**Nuevos:**
- `fenix_mcp.py` — lanzador auto-instalable (punto de entrada MCP)
- `opencode.json` — config zero-install (apunta a fenix_mcp.py)
- `.opencode/skills/agente-fenix/` — skill preinstalada (copia de SKILL.md + references)
- `references/persona-system-prompt.md` — persona/system prompt
- `src/scoring/qualification.py` — BANT/MEDDIC
- `src/core/provenance.py` — atribución por campo
- `src/core/cache.py` — caché TTL
- `src/core/user_paths.py` — carpetas amigables de export
- `tests/test_qualification_provenance_cache.py` — 28 tests nuevos
- `EMPIEZA-AQUI.md`, `INSTRUCCIONES-USUARIO-NO-TECNICO.md` — guías de usuario
- `REPORTE-GLOBAL-SESION.md` (este archivo) + `CHANGELOG-FENIX.md`

**Modificados:**
- `SKILL.md` — persona, REGLA #0/#0.2, Fase 1.5 (dónde guardar), +100k leads, 33 tools
- `src/skill/mcp_server.py` — 5 tools nuevas (qualify_lead, lead_provenance, cache_stats,
  open_results_folder, suggested_locations) + destino en fenix_run + señal de fallback
- `src/agents/pipeline.py` — Profiler con BANT/MEDDIC/provenance + dispatcher con output_dir
- `src/sources/search_backends.py` — retry/backoff, DDG Lite, filtro anuncios, señal fallback
- `src/core/config.py` — fix comentarios inline en .env
- `install.py` — registra MCP con fenix_mcp.py
- `tests/conftest.py` — fix db_path
- `.gitignore` — versiona opencode.json y .opencode/; ignora .fenix-venv
- `AGENTS.md` — instrucciones zero-install

---

## 5. Cómo se instala y usa (flujo final para el usuario no técnico)

1. **Requisito único:** Python 3.10+ (en Windows marcar "Add Python to PATH").
2. Descomprimir `AgenteFenix-OpenCode.zip` → carpeta `agente-fenix-v5`.
3. **Doble clic** en `INSTALAR-WINDOWS.bat` (o `INSTALAR-MAC-LINUX.command`). Instala todo
   y copia el agente DENTRO de opencode (a prueba de borrado). Espera "TODO LISTO".
4. Abrir opencode (cerrar/reabrir si estaba abierto) → `/skills` → `agente-fenix`, o escribir
   "corre fenix para 5 leads de ropa en CDMX".
5. El agente pregunta DÓNDE guardar (Escritorio recomendado) y al final puede ABRIR la carpeta.

- Guías incluidas: `INSTRUCCIONES-USUARIO-NO-TECNICO.md`, `EMPIEZA-AQUI.md`.
- **ZIP distribuible:** se genera en `/home/user/AgenteFenix-OpenCode.zip` (incluye el token
  para que los testers no configuren nada).

---

## 6. Decisiones tomadas (qué se RECHAZÓ y por qué)

El usuario consultó a otros LLMs que propusieron integraciones. Se evaluaron y rechazaron:

| Propuesta | Veredicto | Razón |
|---|---|---|
| OSINT MCP Server (@superadnim, badchars, etc.) | ❌ | Son de ciberseguridad (WHOIS/Nmap/Shodan), no de leads comerciales |
| leadenrich-mcp (Apollo/Clearbit/Hunter) | ❌ | Rompe el $0/mes ($29/mes + $0.05-0.15/lead) y es US-céntrico |
| Memory MCP | ❌ | Ya hay memoria propia (SelfImprover + memory.json + SQLite dedup) |
| Filesystem MCP | ❌ | Redundante: opencode ya da Read/Write/Bash |
| ralph-wiggum-mcp / ralph-workflow | ❌ | Es para CODEAR en loop, no para leads; ya hay bloques+checkpoint |
| autopoiesis-mcp | ❌ | Vago/experimental, riesgo sin valor para usuarios inexpertos |
| Rediseño "Phoenix MCP v2" (5 servers + Docker + Redis) | ❌ | Sobre-ingeniería; ya existe todo en 1 MCP funcional |

**SÍ tendrían valor a futuro (no implementados aún):** HubSpot MCP oficial (push directo a
CRM), Playwright/browser MCP (solo como fuente para sitios SPA), Google Sheets MCP (export
alternativo). Integrarlos COMO fuentes/destinos dentro del pipeline, no como motores paralelos.

---

## 7. Verificación (cómo comprobar que todo sigue bien)

```bash
cd /home/user/Skydropx/Skydropx/agente-fenix-v5
python3 -m pytest -q                       # debe dar 164 passed
python3 -m src.skill.cli fenix healthcheck # con token DENUE: 8/8 OK
# arrancar MCP y listar tools (debe decir 33):
printf '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}\n' | python3 -m src.skill.mcp_server
```

---

## 8. Pendientes / próximos pasos sugeridos

- [ ] **Commit + push a `master`** de todo lo acumulado (aún sin commitear).
- [ ] (Opcional) Cambiar rama default del repo a `master` en GitHub.
- [ ] (Opcional) Integrar HubSpot MCP para push directo a CRM.
- [ ] Seguir registrando cada cambio en `CHANGELOG-FENIX.md`.
