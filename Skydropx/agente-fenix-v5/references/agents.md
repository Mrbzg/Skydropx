# Los 7 Agentes del pipeline Fénix v5 — detalle completo

Cada agente vive en `src/agents/<nombre>.py` y expone una función `run(state) -> state` con un contrato uniforme.

---

## Agente 1 — TREND SCOUT

**Objetivo:** Detectar tendencias activas en el nicho antes de prospectar para evitar perseguir nichos en declive.

**Fuentes (timeout 15s c/u):**
- `pytrends` → Google Trends México (región MX)
- `tendencias.mercadolibre.com.mx` → Trends de ML
- TikTok hashtags públicos MX (sin auth)

**Input:**
```json
{"nicho_input": "moda femenina", "zona": "CDMX"}
```

**Output:**
```json
{
  "nicho": "moda femenina",
  "zona": "CDMX",
  "modelo_negocio_sugerido": "B2C",
  "tendencia_score": 0.87,
  "fuente_tendencia": "mercadolibre",
  "subtendencias_relacionadas": ["ropa interior moldeadora", "vestidos casuales"],
  "temporada_actual": "primavera_2026"
}
```

**Decisión:** si `tendencia_score < 0.3`, sugerir al usuario un nicho alternativo antes de continuar.

---

## Agente 2 — SCOUT

**Objetivo:** Descubrir empresas en el nicho con dominio web identificable.

**Mapeo nicho → SCIAN (parcial, ver `data/nicho_scian.json` completo):**

| Nicho input | SCIAN | Descripción |
|---|---|---|
| ropa femenina | 463211 | Comercio al por menor de ropa, excepto bebé |
| calzado | 463311 | Comercio al por menor de calzado |
| joyería | 465311 | Comercio al por menor de joyería |
| belleza | 465211 | Cosméticos |
| restaurantes | 722 | Servicios de alimentación |
| ecommerce puro | 4541 | Comercio por catálogo/internet (rama) |
| manufactura | 31-33 | Industria manufacturera (sectores) |

**Estrategia por tamaño de empresa:**

| Tamaño DENUE (estrato) | Fuentes prioritarias |
|---|---|
| Micro (1-2: 0-10 empleados) | DENUE + Google Maps + Facebook + Mercado Libre |
| Pequeña (3-4: 11-50) | DENUE + Google Maps + theHarvester + Photon |
| Mediana (5-6: 51-250) | DENUE + LinkedIn no-login + SpiderFoot + CrossLinked |
| Grande (7: 251+) | DENUE + LinkedIn no-login + SpiderFoot + Dorks + cámaras |

**Comandos clave:**
```bash
# DENUE - YA IMPLEMENTADO en src/sources/denue_source.py
python -m src.skill.cli fenix source denue \
  --scian 463211 --entidad 09 --estrato 0 --limit 1000 --json

# theHarvester por dominio
theHarvester -d "{empresa}.com.mx" -b all -l 500 -f output/scout_{empresa}

# Google Maps scraping
python -m src.skill.cli fenix source maps \
  --query "{nicho} en {ciudad}" --limit 50
```

---

## Agente 3 — HUNTER

**Objetivo:** Extraer emails corporativos + teléfonos de las empresas descubiertas.

**Reglas duras de extracción:**
- Solo emails corporativos: descartar `@gmail.com`, `@hotmail.com`, `@yahoo`, `@outlook`
- Teléfonos: exactamente 10 dígitos MX, formato E.164 `+52XXXXXXXXXX`
- Validación CoVe en 4 pasos: `Draft → Verify → Cross-check → Final`
- Mínimo 2 fuentes para cualquier dato

**Herramientas:**

```python
# Emails — patrón corporativo
emailfinder --company "{empresa}" --domain "{dominio}"

# Emails — verificación de brechas (solo existence, no contenido)
h8mail -t "{email}" --local-breach

# Teléfonos — OSINT
phoneinfoga scan -n "+52{telefono}" -o json

# Empleados LinkedIn — sin auth
python -m src.skill.cli fenix source crosslinked \
  --company "{empresa}" --location "México"
```

**Buckets de salida del Hunter:**

| Bucket | Criterio | Acción siguiente |
|---|---|---|
| `COMPLETO` | email + teléfono | → Verifier |
| `SOLO_EMAIL` | solo email | retry tel (máx 2) |
| `SOLO_TEL` | solo teléfono | retry email (máx 2) |
| `SIN_CONTACTO` | nada | export terciario (Google Maps manual) |

---

## Agente 4 — VERIFIER

**Objetivo:** Validar todos los datos antes de calcular DATA_SCORE.

**Validaciones:**

```python
# Email - SMTP check
from verify_email import verify_email
result = verify_email("{email}", check_smtp=True, debug=False)

# Email - DNS MX check
import dns.resolver
dns.resolver.resolve("{domain}", "MX")

# Email - dominios desechables (lista local de 1500+)
from utils.disposable_emails import is_disposable

# Teléfono - formato E.164 MX
import phonenumbers
num = phonenumbers.parse("+52{tel}", "MX")
assert phonenumbers.is_valid_number(num)

# RFC - regex MX
import re
assert re.match(r'^[A-Z&Ñ]{3,4}\d{6}[A-Z0-9]{3}$', rfc)
```

**Fórmula DATA_SCORE (umbral 70):**
```
email_verificado_SMTP_corporativo  → 40
cargo_confirmado_≥1_fuente         → 30
telefono_10dig_MX_valido           → 20
LinkedIn_URL_activa                → 10
```

**Buckets por DATA_SCORE:**

| Bucket | Score | Significado |
|---|---|---|
| `COMPLETO` | ≥70 | → pasa al Profiler |
| `SOLO_EMAIL` | 50-69 | retry tel |
| `SOLO_TEL` | 50-69 | retry email |
| `SIN_CONTACTO` | <50 | export terciario |
| `DESCARTADO` | <50 + datos falsos | log + descartar |

---

## Agente 5 — PROFILER

**Objetivo:** Enriquecer el lead con cargo, redes sociales, pain points, propuesta de valor.

**Herramientas:**

```bash
# Perfiles sociales por username
sherlock "{nombre_contacto}" --site LinkedIn --site Twitter --site Instagram

# Metadata de PDFs/Office públicos de la empresa (revela cargos)
metagoofil -d "{dominio}" -t pdf,docx,xlsx -l 10 -o output/meta_{empresa}/

# LinkedIn OSINT directo (sin login)
python -m src.skill.cli fenix source linkedint \
  --company "{empresa}" --location "Mexico"
```

**Mapeo giro → modelo de negocio:**

```
Fabricante / Manufactura   → B2B (volumen alto)
Mayorista / Distribuidor   → B2B + D2C
Retail físico              → B2C + B2B
E-commerce propio          → D2C + B2C
Marketplace (vendedor ML)  → C2C → C2B
```

**Fórmula SALES_PRIORITY:**
```
match_modelo_negocio_Skydropx     → 40
volumen_estimado_envios           → 30
presencia_digital                 → 20
complejidad_logistica             → 10
```

---

## Agente 6 — DISPATCHER

**Objetivo:** Exportar los leads calificados al destino final.

**Formatos de salida:**

```bash
# CSV v4.0 (26 columnas)
python -m src.skill.cli fenix export csv \
  --input data/leads_verified.json \
  --output output/leads_{nicho}_{fecha}.csv

# JSON con metadata completa
python -m src.skill.cli fenix export json \
  --input data/leads_verified.json \
  --output output/leads_{nicho}_{fecha}.json

# Google Sheets (requiere credentials.json)
python -m src.skill.cli fenix export sheets \
  --input output/leads_{nicho}_{fecha}.csv \
  --sheet "Leads {nicho} {fecha}"

# HubSpot CRM (requiere HUBSPOT_API_KEY)
python -m src.skill.cli fenix export hubspot \
  --input output/leads_{nicho}_{fecha}.csv \
  --pipeline "Outbound MX"
```

**Deduplicación antes de exportar:**

```bash
python -m src.skill.cli fenix dedup \
  --input output/leads_{nicho}_{fecha}.csv \
  --keys "empresa,email,telefono" \
  --fuzzy-threshold 85
```

---

## Agente 7 — SELF-IMPROVER

**Objetivo:** Aprender de cada sesión y mejorar las siguientes.

**Memoria persistente (SQLite `data/memory.db`):**

| Tabla | Qué guarda |
|---|---|
| `fallbacks_known` | fuentes alternativas que funcionaron en cada error |
| `source_stats` | tasa de éxito por fuente (rolling 30 días) |
| `queries_history` | historial de búsquedas con CTR efectivo |
| `improvements_applied` | mejoras activadas entre sesiones |
| `pending_retry_leads` | SIN_CONTACTO pendientes de reintentar |
| `trend_history` | nichos/tendencias detectadas históricamente |
| `seasonal_niches` | nichos por temporada (Navidad, Buen Fin, etc.) |
| `installed_tools` | herramientas instaladas dinámicamente |
| `audit_trail` | registro completo de ejecuciones (LFPDPPP) |

**Reporte al finalizar (9 secciones):**

```
1. Resiliencia     → % fuentes que respondieron
2. Reclamación     → leads recuperados de retry
3. Dedup           → duplicados eliminados
4. Estacional      → ajuste por temporada
5. Optimización    → mejoras aplicadas esta sesión
6. Audit Trail     → log completo de acciones
7. Shutdown        → estado del checkpoint
8. Quality         → distribución de scoring
9. Pool            → estado del pool de workers
```

---

## Contrato de estado entre agentes

Cada agente recibe y retorna un objeto `state` con esta estructura mínima:

```python
@dataclass
class PipelineState:
    # Input original
    nicho: str
    zona: str
    modelo: str           # B2B/B2C/C2C/D2C/C2B
    canal: str            # web/social/marketplace
    meta: int

    # Trazabilidad
    job_id: str
    fase_actual: str       # trend|scout|hunter|verify|profile|dispatch|improve
    started_at: datetime
    checkpoint_at: datetime | None

    # Datos en pipeline
    candidatos: list[dict]     # del Scout
    leads_hunted: list[dict]   # del Hunter
    leads_verified: list[dict] # del Verifier
    leads_enriched: list[dict] # del Profiler
    exports: dict              # del Dispatcher

    # Métricas
    stats: dict
    errors: list[dict]

    def checkpoint(self) -> None:
        """Persiste state a pipeline_checkpoint.json."""
        ...
```

Esto garantiza que el pipeline es **reanudable** desde cualquier agente.
