# 🧾 CHANGELOG DE CONTROL — Agente Fénix v5

> **Para qué sirve:** llevar el control de CADA cambio/arreglo con el formato
> **"qué teníamos" → "qué cambiamos" → "por qué" → "cómo se verificó"**.
> Complementa a `REPORTE-GLOBAL-SESION.md` (que da el panorama general).
>
> **REGLA PARA LA IA:** cada vez que hagas un cambio o arreglo, **agrega una entrada
> nueva aquí ARRIBA** (orden cronológico inverso: lo más nuevo primero) y, si aplica,
> actualiza el resumen del reporte global. No borres entradas anteriores.

**Formato de cada entrada:**
```
## [N] Título corto — fecha
- Antes: ...
- Cambio: ...
- Archivos: ...
- Por qué: ...
- Verificado: ...
```

---

## [8] Reporte global + changelog de control — 2026-05-30
- **Antes:** no había un documento de handoff; abrir otra sesión implicaba re-explicar todo.
- **Cambio:** se crearon `REPORTE-GLOBAL-SESION.md` (contexto completo + cómo debe
  comportarse la IA) y este `CHANGELOG-FENIX.md` (control entrada-por-entrada).
- **Archivos:** `REPORTE-GLOBAL-SESION.md` (nuevo), `CHANGELOG-FENIX.md` (nuevo).
- **Por qué:** continuidad entre sesiones/LLMs; el usuario quiere pegar 1 archivo y que el
  nuevo asistente entienda el contexto y se comporte igual.
- **Verificado:** N/A (documentación). Tests siguen en 164 passed.

## [7] Carpeta de export amigable + el agente pregunta DÓNDE guardar — 2026-05-30
- **Antes:** los leads se exportaban a `output/` interno. Con la instalación a-prueba-de-
  borrado, ese `output/` vive en `~/.config/opencode/agente-fenix-app/output/`. Un usuario
  no técnico NUNCA lo encontraba. El reporte mostraba rutas tipo `output\fenix_....csv`.
- **Cambio:**
  - Nuevo `src/core/user_paths.py`: resuelve Escritorio/Descargas/Documentos
    (Windows/Mac/Linux, ES/EN, OneDrive). Subcarpeta `Leads-Fenix`.
  - `fenix_run` acepta `destino` ("escritorio"/"descargas"/"documentos"/ruta).
  - Dispatcher (`agent_dispatcher`) y `run_pipeline` aceptan `output_dir`.
  - 2 tools nuevas: `suggested_locations` (listar carpetas para preguntar) y
    `open_results_folder` (abrir el explorador con los leads).
  - SKILL.md: **Fase 1.5** = preguntar dónde guardar (default Escritorio); reporte muestra
    ubicación en palabras y ofrece abrir la carpeta. Tools 31 → 33.
- **Archivos:** `src/core/user_paths.py` (nuevo), `src/skill/mcp_server.py`,
  `src/agents/pipeline.py`, `SKILL.md`, `tests/test_qualification_provenance_cache.py`.
- **Por qué:** objetivo rector = facilitarle todo al usuario final no técnico.
- **Verificado:** 164 tests passed. fenix_run con `destino=/tmp/...` exporta ahí (5 leads
  reales DENUE). `suggested_locations` y `open_results_folder` responden OK por MCP.

## [6] Robustez de búsqueda + web search como ÚLTIMO recurso — 2026-05-29
- **Antes:** cuando `search_dorks` fallaba (rate-limit transitorio de DDG), el agente
  brincaba a su búsqueda web del LLM, violando la REGLA #0 (datos sin verificar). Además
  DDG colaba anuncios (`y.js?ad_domain=`) como si fueran leads.
- **Cambio:**
  - Reintentos con backoff por backend en `SearchBackend.search()`.
  - 5º backend gratis: **DuckDuckGoLiteBackend** (fallback robusto). Cascada:
    SearXNG → DDG → OpenSERP → DDG-Lite → Serper. (Se probó Bing pero pide cookies; se
    descartó.)
  - Filtro de anuncios en el parser de DDG.
  - `search_dorks` devuelve `web_search_fallback_allowed` (true solo si TODOS los backends
    se agotan) + no cachea respuestas vacías.
  - SKILL.md: REGLA #0 suavizada + **REGLA #0.2** (orden estricto; web search solo al final
    y siempre verificando, para no dejar al cliente sin leads).
- **Archivos:** `src/sources/search_backends.py`, `src/skill/mcp_server.py`, `SKILL.md`,
  `tests/test_qualification_provenance_cache.py`.
- **Por qué:** que el cliente nunca se quede sin resultados, pero priorizando datos verificados.
- **Verificado:** 158 tests passed. Caso normal → fallback=false; backends agotados →
  fallback=true. DDG ya no devuelve anuncios (cyamoda.com, oneluone.com, etc.).

## [5] Token DENUE + fix parser .env — 2026-05-29
- **Antes:** token DENUE revocado (en el chat del usuario el pipeline se bloqueaba). El
  parser de `.env` no quitaba comentarios inline → `SQLITE_PATH=data/fenix.sqlite # default OK`
  creaba un archivo basura con el comentario en el nombre.
- **Cambio:** token `55592824-a5c8-4bc4-b71e-5163583287d0` agregado al `.env` (incluido en el
  ZIP para testers). Fix en `_load_dotenv` de `src/core/config.py` para ignorar `# ...`
  cuando el valor no está entre comillas.
- **Archivos:** `.env`, `src/core/config.py`.
- **Por qué:** el token estaba revocado y el bug del .env ensuciaba el proyecto.
- **Verificado:** healthcheck 8/8 OK, overall_ok=true. Pipeline real con DENUE: 6
  establecimientos descubiertos en Nuevo León. SQLITE_PATH limpio.

## [4] 3 mejoras de valor dentro del MCP (BANT/MEDDIC, provenance, caché) — 2026-05-29
- **Antes:** el usuario consideraba MCPs externos (leadenrich, OSINT, etc.). Se rechazaron
  (rompían $0 o no aplicaban). Faltaban: calificación de ventas, atribución de fuente, caché.
- **Cambio:**
  - `src/scoring/qualification.py`: BANT + MEDDIC heurístico ($0, sobre datos reales).
  - `src/core/provenance.py`: atribución por campo (qué fuente aportó cada dato) + confianza.
  - `src/core/cache.py`: caché TTL persistente; enganchado a `search_dorks`.
  - 3 tools MCP: `qualify_lead`, `lead_provenance`, `cache_stats`. Tools 28 → 31.
  - Profiler calcula BANT/MEDDIC/provenance automático para cada lead.
- **Archivos:** `src/scoring/qualification.py`, `src/core/provenance.py`, `src/core/cache.py`,
  `src/skill/mcp_server.py`, `src/agents/pipeline.py`, `SKILL.md`,
  `tests/test_qualification_provenance_cache.py` (nuevo, 18 tests).
- **Por qué:** dar valor real sin romper el modelo $0 ni depender de terceros.
- **Verificado:** 154 tests passed. Lead caliente → BANT 75/MEDDIC 72; frío → bajo + preguntas.

## [3] Lanzador auto-instalable fenix_mcp.py + unificación a-prueba-de-borrado — 2026-05-29
- **Antes:** "abrir la carpeta" dependía de la carpeta descargada (se rompía si la borraban).
  El usuario debía correr pip/install.py a mano en algún flujo.
- **Cambio:**
  - `fenix_mcp.py`: punto de entrada del MCP que auto-instala dependencias la 1ª vez
    (venv aislado `.fenix-venv`), reutiliza `.venv` de install.py, crea `.env`. Después es
    instantáneo y auto-reparable.
  - `install.py` registra el MCP con `fenix_mcp.py` → la instalación relocalizada
    (`~/.config/opencode/agente-fenix-app/`) quedó auto-reparable.
  - `opencode.json` + `.opencode/skills/` versionados para zero-config.
- **Archivos:** `fenix_mcp.py` (nuevo), `opencode.json` (nuevo), `.opencode/` (nuevo),
  `install.py`, `.gitignore`, `EMPIEZA-AQUI.md`, `INSTRUCCIONES-USUARIO-NO-TECNICO.md`.
- **Por qué:** que el usuario inexperto no instale nada y que sobreviva al borrado.
- **Verificado:** bootstrap desde Python pelón → 28→31→33 tools según avance. Borrar carpeta
  descargada → MCP sigue vivo desde la copia protegida.

## [2] Persona / System Prompt fusionado — 2026-05-29
- **Antes:** el System Prompt conversacional del PDF (metasecurity, calibración, fases,
  anti-leak, tono) no estaba formalizado en la skill.
- **Cambio:** `references/persona-system-prompt.md` (nuevo) + referencia desde SKILL.md
  (identidad operativa, jerarquía inmutable, calibración NOVICIO por defecto).
- **Archivos:** `references/persona-system-prompt.md`, `SKILL.md`.
- **Por qué:** el usuario lo pidió; da consistencia de personalidad y seguridad.
- **Verificado:** SKILL.md válido, MCP sigue arrancando.

## [1] Diagnóstico inicial + fix de tests — 2026-05-29
- **Antes:** se creía que el repo estaba vacío (la rama `main` lo está). El proyecto real
  estaba en `master`. 11 tests fallaban por `conftest.py` usando `FenixDB(db_url=...)`.
- **Cambio:** se identificó la rama `master` con el proyecto v5.3 (28 tools). Fix en
  `tests/conftest.py`: `db_url=` → `db_path=`.
- **Archivos:** `tests/conftest.py`.
- **Por qué:** dejar la base verde antes de construir sobre ella.
- **Verificado:** de 125 passed/11 errors → 136 passed.

---

### Estado acumulado actual
- **Tools MCP:** 33
- **Tests:** 164 passed
- **Costo:** $0/mes · **Token DENUE:** configurado y verificado
- **Sin commitear aún** a `master` (pendiente push).
