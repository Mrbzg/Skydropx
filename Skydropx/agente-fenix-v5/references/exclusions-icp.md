# Exclusiones + ICP Classifier — Filtros de calidad y segmentación

> Responde a las preguntas críticas: ¿qué leads descartar? ¿cómo distinguir
> PyME 50-100 envíos de Enterprise B2B/3PL/Agencia?

---

## 🛡 Capa 1 — Exclusiones (3 capas)

Cada lead pasa por 3 filtros antes de persistirse en DB. Si falla alguno → descartado
y registrado en `state.stats["exclusions"]` para auditoría.

### Capa Técnica
- Wikipedia, Wikidata, Wiktionary
- LinkedIn /company (no /in)
- Filetypes sin contenido: png/jpg/svg/mp4/zip
- Títulos "404", "Página no encontrada", "Maintenance"
- Texto "lorem ipsum", "sandbox", "demo site"
- URLs `/tmp/`, `/backup/`, `/old/`, `/staging/`, `/wp-admin/`

### Capa Contexto MX
- Dominios `.gob.mx`, `.edu.mx`, `.unam.mx`, etc.
- Empresas gubernamentales (SAT, IMSS, SEP, INE, ISSSTE)
- Universidades públicas (UNAM, IPN, UAM, UDG)

### Capa ICP Outbound Skydropx

| Categoría | Razón | Conteo |
|---|---|---|
| **Competencia logística** | DHL, FedEx, Estafeta, 99minutos, etc. | 33 empresas |
| **Marketplaces** | Amazon, ML, Walmart, Liverpool, Coppel | 36 empresas |
| **Financiero/Bancos/Seguros** | BBVA, Santander, Banamex, AXA, GNP | 50 empresas |
| **Transporte/Viajes/Hospedaje** | Uber, Aeromexico, Airbnb, Marriott | 45 empresas |
| **Construcción/Industrial pesado** | Cemex, Pemex, CFE, Ternium | 35 empresas |
| **Telecom/Media grandes** | Telmex, Telcel, Televisa, Netflix | 28 empresas |
| **Automotriz grandes** | Nissan, VW, Toyota, BMW | 30 empresas |
| **Estrato 7 DENUE** | 251+ empleados (corporativos) | automático |

**Total catálogo:** 284 empresas + 142 dominios + 13 SCIANs excluidos.

### Override por corrida

```bash
# Permitir empresas grandes esta vez
python3 -m src.skill.cli fenix run --nicho ropa --include-large

# Permitir medianas-grandes (101-250 empleados)
python3 -m src.skill.cli fenix run --nicho ropa --include-medianas-grandes
```

### Dorks con exclusiones integradas

```bash
python3 -m src.skill.cli fenix exclusions dork
# →  -filetype:gif -filetype:webp -site:wikipedia.org -site:linkedin.com/company
#    -site:dhl.com -site:fedex.com -site:amazon.com.mx -site:.gob.mx ...
```

### Test individual

```bash
# ¿Sería excluido CEMEX?
python3 -m src.skill.cli fenix exclusions check --empresa "CEMEX SAB de CV" --scian "3273"
# → excluded: true, signal: EXCLUIR_SCIAN, reason: SCIAN 3273 pertenece a sector excluido (23)
```

---

## 🎯 Capa 2 — ICP Classifier dual

Cada lead que sobrevive a exclusiones es clasificado en uno de 4 segmentos:

### ICP_1_PYME (50-100 envíos/mes) — el sweet spot
- **Perfil:** D2C / PyME / Emprendedores con tienda online + envíos recurrentes
- **Plan sugerido:** Starter o PyME
- **Señales:** Shopify/Tiendanube/WooCommerce + intent envíos + Meta Pixel + MercadoPago
- **Estrato DENUE típico:** 1-4 (Micro a Pequeña)
- **Value prop:** "Integración nativa Shopify + cotizador en checkout + tarifas preferentes"

### ICP_2_ENTERPRISE (500+ envíos / B2B) — los Enterprise
- **Perfil:** 3PL / Agencias digitales / Fabricantes medianos con distribución B2B
- **Plan sugerido:** Enterprise
- **Señales:** keyword "3PL"/"fulfillment"/"logística"/"agencia" + HubSpot/Salesforce + tamaño mediana/grande
- **Estrato DENUE típico:** 5-7 (Mediana a Grande)
- **Value prop:** "API + Webhooks + Convenios tarifarios + KAM dedicado"

### ICP_3_C2C (envíos esporádicos)
- **Perfil:** Vendedor Mercado Libre con bajo volumen, persona individual
- **Plan sugerido:** Starter
- **Señales:** source=mercadolibre + ml_tx_completed < 100
- **Value prop:** "Cotizador + guías sueltas sin contrato"

### NO_ICP (descarta)
- Score insuficiente en cualquier dimensión

### Verticales detectados

- `ecommerce_d2c` (tienda online propia)
- `marketplace_seller` (ML/Amazon vendor)
- `pyme_retail` (comercio minorista)
- `pyme_mayorista` (distribución B2B)
- `fabricante` (manufactura)
- `3pl_fulfillment` (operador logístico)
- `agencia_marketing` (agencia digital)
- `servicios_profesionales`
- `otro`

### Lógica de decisión (tiebreaker)

```
1. Calcula score_ICP_1, score_ICP_2, score_ICP_3
2. Thresholds: ICP_1 ≥ 30, ICP_2 ≥ 40, ICP_3 ≥ 25
3. Si hay tie ICP_1 ↔ ICP_2 con diff < 15 → gana ICP_1 (más volumen de leads)
4. Si nada llega a threshold → NO_ICP
```

### Test individual

```bash
python3 -m src.skill.cli fenix icp \
  --empresa "LOGISTICA INTEGRAL MTY" \
  --scian "4931" \
  --tamano "Mediana" \
  --metadata '{"estrato_id":"6"}'
# → ICP_2_ENTERPRISE, vertical=3pl_fulfillment, plan=Enterprise, envios=500+
```

### Resultado en el CSV v4.0

Cada lead final tiene en `metadata_json`:
```json
{
  "icp_segment": "ICP_1_PYME",
  "icp_score": 75,
  "icp_vertical": "ecommerce_d2c",
  "envios_estimados": "50-100",
  "phone_can_whatsapp": true,
  "tech_stack": ["shopify", "meta_pixel", "mercadopago"],
  "maturity_score": 75
}
```

Y en columnas del CSV: `modelo` y `skydropx_plan` reflejan la clasificación ICP.

---

## 🧭 Strategic Discovery Protocol

Cuando el usuario dice **"quiero leads de ropa"** sin más contexto, el sistema:

### Modo CLI interactivo
```bash
python3 -m src.skill.cli fenix ask "quiero leads de ropa" --interactive
# → Detecta nicho="ropa", pregunta modelo, zona, meta
```

### Modo MCP (Claude Code / opencode)
El asistente llama a `fenix_ask` (próximo tool a agregar al MCP), recibe `next_question`,
pregunta al usuario en lenguaje natural, llama de nuevo con `field`+`value` hasta completar.

### Modo one-shot (texto completo)
```bash
python3 -m src.skill.cli fenix ask "necesito 500 leads de calzado en CDMX para B2B" --run
# → Status: ready, ejecuta pipeline directo
```

### Campos que detecta automáticamente

| Campo | Patterns que reconoce |
|---|---|
| **nicho** | catálogo de 17 nichos + aliases (ropa, calzado, joyería, belleza, etc.) |
| **zona** | 32 estados + ciudades top + "nacional"/"toda la república" |
| **modelo** | B2B/B2C/C2C/D2C/C2B + keywords ("mayorista", "marca propia", "emprendedor") |
| **canal** | web/social/marketplace/fisica/mixto + plataformas (Shopify, Instagram, ML) |
| **meta** | "500 leads", "5 mil", "10k", números 10-1M |

---

## 🔄 Flujo completo end-to-end

```
Usuario: "quiero leads de ropa"
   │
   ▼
[fenix ask]
   │
   ├─ Detecta: nicho="ropa", zona="nacional"
   ├─ Falta: modelo, meta
   ▼
[next_question="¿modelo?"]
   │ ↓ usuario dice "B2C, 500 leads"
   ▼
[status=ready, ResearchPlan listo]
   │
   ▼
[Pipeline 9 agentes]
   ├─ TrendScout
   ├─ Scout (DENUE/ML/dorks/cámaras)
   │   └─ filter_records()  ← Exclusiones 3 capas
   │       └─ descarta competencia, marketplaces, financiero, etc.
   ├─ Hunter (extracción email/tel/wa + tech_stack)
   ├─ Verifier (email cascada + phone E.164)
   ├─ Persist (DB + dedup persistente)
   ├─ Profiler
   │   └─ classify_icp()  ← ICP dual
   │       ├─ Asigna ICP_1/ICP_2/ICP_3/NO_ICP
   │       ├─ Detecta vertical (3pl, agencia, ecommerce, etc.)
   │       └─ Asigna plan Skydropx + value proposition
   ├─ DeepEnrich (opcional, holehe+maigret en top READY)
   ├─ Dispatcher (CSV v4.0 + JSON)
   └─ SelfImprover (memoria + stats)
```

---

## 📊 Comandos completos disponibles

```bash
# Discovery
fenix ask "texto libre"                       # parse + pregunta faltantes
fenix ask "..." --interactive                 # con prompts en terminal
fenix ask "..." --run                         # si ready, ejecuta pipeline
fenix ask --session-id X --field modelo --value B2B   # responder

# Exclusiones
fenix exclusions list                         # ver catálogo
fenix exclusions check --empresa "X" --scian "Y"   # ¿se excluiría?
fenix exclusions dork                         # genera string -site:... para dorks

# ICP
fenix icp --empresa "X" --scian "Y" --tamano Z --metadata '{}'   # clasificar lead

# Pipeline con overrides
fenix run --nicho ropa --include-large                # permite Estrato 7
fenix run --nicho ropa --include-medianas-grandes     # permite Estrato 6
```

Todo expuesto también vía MCP server para Claude Code/opencode.
