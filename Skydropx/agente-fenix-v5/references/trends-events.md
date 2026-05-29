# Trends + Events + Agency Detector — Casos avanzados

> Resuelve los casos complejos de outbound Skydropx: leads en tendencia,
> leads por evento/temporada, leads de campañas promocionales,
> identificar agencias detrás de campañas.

---

## 🔥 1. Trends Detector — qué está caliente HOY

### Fuentes integradas

| Fuente | Cómo funciona | Status |
|---|---|---|
| **Google Trends MX** | RSS oficial `/trending/rss?geo=MX` + fallback pytrends | ✅ Funcional |
| **Mercado Libre Trends** | Scraping de `/trends/MLM` | ✅ Funcional |
| **TikTok Hashtags** | Vía SearchBackendManager con dorks `site:tiktok.com` | ✅ Funcional |
| **Amazon MX Best Sellers** | Scraping de `/gp/bestsellers` | ✅ Funcional |

### Caché TTL 6h en `data/trends_cache.json` para no rebombardear las fuentes.

### Comandos CLI

```bash
# Solo Google Trends (rápido)
fenix trends google

# Todas las fuentes + sugerencia de nichos Fénix
fenix trends all --suggest-niches

# Solo algunas fuentes
fenix trends all --sources google,mercadolibre --no-cache
```

### Flujo conversacional típico (vía MCP)

```
Usuario: "¿Qué productos están en tendencia hoy?"
   ↓ Claude llama → trends_now(suggest_niches: true)
   ↓ Recibe:
      - top_overall: [eticket, mundial 2026, fifa boletos, ...]
      - nichos_sugeridos: [{nicho: deportes, trend_match: mundial 2026}]
Claude: "Hoy en México están en tendencia: fifa boletos (97), mundial 2026 (94)...
         ¿Quieres correr el pipeline de leads para alguno de estos nichos?"

Usuario: "Sí, para deportes"
   ↓ Claude llama → fenix_run(nicho: deportes, modelo: B2C, meta: 100, ...)
```

---

## 🎉 2. Events Catalog — campañas por temporada

### 17 eventos predefinidos en `data/eventos_mx.json`

**MX fechas fijas:**
- San Valentín, Día de la Mujer, Día de las Madres, Día del Padre,
  Reyes Magos, Fiestas Patrias, Halloween + Día de Muertos

**MX comerciales:**
- Hot Sale (AMVO), Buen Fin, Black Friday, Cyber Monday, Regreso a Clases,
  Navidad, Día de Reyes

**Globales con impacto MX:**
- **Mundial FIFA 2026** (11 jun - 19 jul) — alta prioridad ahora
- Super Bowl, Champions League Final, Juegos Olímpicos

### Cálculo automático de fechas móviles
- "tercer_viernes_noviembre" → Buen Fin calculado dinámicamente
- "primer_domingo_febrero" → Super Bowl
- "rango" → eventos multi-día con ventana antes/después configurables

### Comandos CLI

```bash
# Qué está activo HOY (en ventana antes/durante/después)
fenix events active

# Buscar un evento por keyword
fenix events find --query "leads del mundial"

# Plan completo de búsqueda para evento + campaña
fenix events suggest --query "mundial con compra y gana"
# → devuelve: evento detectado + dorks generados con keywords promocionales
```

### Caso de uso: "Leads del Mundial con compra y gana"

```python
# Input del usuario
suggest_event_campaign_search("leads del mundial con compra y gana")

# Output (real):
{
  "evento_detectado": {
    "id": "mundial_fifa_2026",
    "nombre": "Mundial FIFA 2026",
    "categorias_target": ["ropa deportiva", "deportes", "electronica",
                           "comida", "bebidas"]
  },
  "campaign_types_detected": ["compra_y_gana"],
  "dorks_sugeridos": [
    '"mundial 2026" "promocion" site:.mx',
    '"jersey seleccion" site:.mx',
    '"compra y gana" "mundial" site:.mx',
    '"registrate y gana" "mundial" site:.mx',
    ...
  ]
}
```

---

## 🏢 3. Agency Detector — quién está detrás de una campaña

### Detecta agencias en 2 modos:

#### Modo 1: análisis de texto directo
```bash
fenix agency --text "La presente promoción es organizada por MASSIVE EMOTIONS S DE RL DE CV con RFC MEM200115ABC"
```
→ detecta `"MASSIVE EMOTIONS"` (del catálogo de 42 agencias MX) + RFC + razón social

#### Modo 2: búsqueda en un dominio
```bash
fenix agency --dominio datumax.mx --campana "Compra y Gana Mundial"
```
→ aplica dorks `site:datumax.mx "bases" "sorteo" filetype:pdf` etc.
→ descarga PDFs/HTML legales
→ extrae agencias + RFCs + razones sociales

### Catálogo de 42 agencias MX conocidas
Incluye: BBDO, Ogilvy, McCann, JWT, Wunderman Thompson, Publicis, DDB, Grey,
Leo Burnett, Havas, Saatchi, TBWA, FCB, Carat, OMD, Mindshare, GroupM,
**MASSIVE EMOTIONS, Datumax** y 24+ más.

### Por qué es valioso para Skydropx
Las agencias suelen calificar como **ICP_2_ENTERPRISE** y manejan envíos masivos
de múltiples campañas → ticket de venta alto.

---

## 📤 4. HubSpot CSV Exporter — para import manual

### Genera 3 archivos

```bash
fenix hubspot --only-bucket COMPLETO,PARCIAL --limit 1000 --run-id semana1
```

→ Crea en `output/`:
1. `fenix_hubspot_semana1_<ts>_contacts.csv`
2. `fenix_hubspot_semana1_<ts>_companies.csv`
3. `fenix_hubspot_semana1_<ts>_README.txt`

### Headers HubSpot-friendly (auto-mapping)

CSV de Contacts:
```
First Name, Last Name, Email, Phone Number, Mobile Phone Number,
Job Title, Company Name, Industry, City, State/Region,
Country/Region, Website URL, Lifecycle Stage, Lead Status,
HubSpot Score, Original Source, LinkedIn Bio, Facebook URL,
Instagram URL, Notes
```

HubSpot detecta estos headers automáticamente al importar — **sin necesidad
de mapeo manual columna por columna.**

### Validaciones aplicadas antes de exportar
- ✅ Email regex válido (`EMAIL_RE`)
- ✅ Teléfono normalizado E.164 (`+52XXXXXXXXXX`)
- ✅ Dedup por email (Contacts) y por dominio (Companies)
- ✅ Sin `DATO_NO_VERIFICABLE` en campos requeridos
- ✅ UTF-8 con BOM para Excel/HubSpot

### README incluye instrucciones paso a paso
1. Importar Companies primero (por dominio = clave de dedup)
2. Importar Contacts después (se enlazan auto a Company por nombre)
3. Crear vistas: "Skydropx Ready", "ICP PyME", "ICP Enterprise",
   "Ya usa competencia"
4. Notas LFPDPPP compliance

---

## 🧹 5. Dedup Audit — análisis del estado de duplicados

### Comandos

```bash
# Reporte completo
fenix dedup-audit report

# Sugerencias de campañas no usadas (anti-solapamiento)
fenix dedup-audit unused --nichos "ropa,calzado,joyeria"
```

### Qué reporta

| Métrica | Significado | Acción si malo |
|---|---|---|
| `overlap_ratio` | raw_findings / companies. >2 = mucho dedup | Cambiar nichos/estados |
| `avg_sources_per_company` | qué tan diverso es el aporte | Si <1.5, agregar fuentes |
| `leads_huerfanos` | sin email NI tel NI website | Subir `--enrich-max` |
| `leads_sin_enriquecer` | con website pero sin email | Re-correr solo Hunter |
| `top_companies_por_overlap` | empresas redescubiertas N veces | Validar dedup correcto |
| `cross_source_winners` | descubiertas por ≥2 fuentes (alta confianza) | Priorizar en outbound |

### Sugerencias automáticas

El audit genera sugerencias del tipo:
- "Fuente 'denue' domina (>70%). Diversifica con ML/dorks/cámaras"
- "19 leads huérfanos (>30%). Habilita Hunter con --enrich-max alto"
- "Overlap ratio 2.5 ALTO. Cambia nicho/estado en la próxima campaña"

### DuckDB opcional (para >100K rows)

Si tienes DuckDB instalado (`pip install duckdb`), las queries analíticas son
10-100x más rápidas. SQLite default funciona perfecto hasta 1M rows.

```bash
pip install duckdb  # opcional
# Automáticamente lo detecta y usa para queries de audit
```

---

## 🔌 Tools MCP nuevas (21 totales)

| Nueva tool | Para qué |
|---|---|
| `trends_now` | Tendencias actuales + sugerencia de nichos Fénix |
| `events_active` | Eventos activos hoy con ventana |
| `event_search_plan` | Plan completo evento + campaña → dorks listos |
| `find_agency` | Agencia detrás de una campaña |
| `export_hubspot_csv` | CSV listos para import manual |
| `dedup_audit` | Reporte de duplicados + sugerencias |

---

## 🎬 Flujo completo conversacional (ejemplo Mundial)

```
Usuario:  "¿Qué eventos hay esta semana?"
Claude:   (llama events_active)
          → Mundial FIFA en 14 días, Día del Padre en 24, Champions Final en 2
Claude:   "Tenemos 4 eventos activos. ¿Cuál te interesa?"

Usuario:  "Mundial. Quiero leads de empresas con campañas 'compra y gana'"
Claude:   (llama event_search_plan con query del usuario)
          → evento=mundial_fifa_2026, categorias=[deportes,ropa deportiva,comida,bebidas]
          → dorks=[mundial 2026 compra y gana site:.mx, ...]
Claude:   "Plan listo: 8 dorks generados para Mundial + compra y gana.
           Categorías target: deportes, ropa deportiva, comida, bebidas.
           ¿Ejecuto búsqueda? ¿Cuántos leads quieres?"

Usuario:  "500"
Claude:   (llama fenix_run con dork queries del plan + meta=500)
          → corre pipeline 9 agentes → exporta CSVs

Usuario:  "Para datumax.mx, ¿qué agencia está detrás?"
Claude:   (llama find_agency con dominio=datumax.mx)
          → busca bases del sorteo en PDFs
          → detecta MASSIVE EMOTIONS S DE RL DE CV + RFC
Claude:   "Agencia detectada: MASSIVE EMOTIONS S DE RL DE CV (RFC MEM200115ABC).
           ¿Quieres prospectarla? Es ICP_2_ENTERPRISE típico."
```
