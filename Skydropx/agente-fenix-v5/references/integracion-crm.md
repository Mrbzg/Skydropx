# Integración con CRMs y destinos (Dispatcher)

## HubSpot

### Setup

1. Crear Private App en HubSpot Settings → Integrations → Private Apps
2. Permisos requeridos: `crm.objects.contacts.write`, `crm.objects.companies.write`, `crm.objects.deals.write`
3. Copiar API key a `.env`:
```bash
HUBSPOT_API_KEY=pat-na1-xxxx-xxxx
HUBSPOT_PIPELINE_ID=<id_pipeline>
HUBSPOT_STAGE_ID=<id_stage_nuevo_lead>
```

### Mapeo de campos Fénix v4.0 → HubSpot

| Campo Fénix | Campo HubSpot | Object |
|---|---|---|
| `nombre` | `firstname` + `lastname` (split) | contact |
| `email` | `email` | contact |
| `telefono` | `phone` | contact |
| `whatsapp` | `mobilephone` | contact |
| `empresa` | `company` (string) + `name` | company |
| `rfc` | `rfc` (property custom) | company |
| `giro` | `industry` | company |
| `estado` | `state` | company |
| `ubicacion` | `city` | company |
| `tamano` | `numberofemployees` (derivado) | company |
| `skydropx_plan` | `description` | deal |
| `value_proposition` | nota adjunta al contacto | engagement |
| `scoring` | `hs_lead_score` (custom) | contact |
| `tipo_lead` | `hs_lead_status` (Hot/Cold) | contact |
| `priority_score` | `hubspot_owner_assignedteam_id` (routing) | contact |
| `fuentes` | `hs_analytics_source_data_1` | contact |

### Comando

```bash
python -m src.skill.cli fenix export hubspot \
  --input output/leads_<nicho>_<fecha>.csv \
  --owner "ventas@skydropx.com" \
  --pipeline-name "Outbound MX" \
  --stage-name "Nuevo Lead Outbound" \
  --source-tag "OSINT-Fénix-v5" \
  --create-companies \
  --create-deals \
  --dry-run    # quitar para ejecutar real
  --json
```

### Comportamiento por bucket

| Bucket | HubSpot stage |
|---|---|
| `COMPLETO` (≥70) + `tipo_lead=caliente` | "Nuevo Lead Caliente" + asignación automática |
| `COMPLETO` + `tipo_lead=frio` | "Nurturing" |
| `SOLO_EMAIL` o `SOLO_TEL` | "Por enriquecer" |
| `SIN_CONTACTO` | NO exportar (queda en DB local) |

---

## Google Sheets

### Setup

1. Crear Service Account en Google Cloud Console
2. Habilitar Google Sheets API
3. Descargar `credentials.json` y colocar en raíz del proyecto
4. Compartir la hoja destino con el email del service account

```bash
GOOGLE_SHEETS_CREDS=credentials.json
GOOGLE_SHEETS_DEFAULT_FOLDER=<folder_id>   # opcional
```

### Comando

```bash
python -m src.skill.cli fenix export sheets \
  --input output/leads.csv \
  --sheet "Leads Fénix $(date +%Y-%m-%d)" \
  --folder "Skydropx > Outbound > Leads OSINT" \
  --json
```

La hoja tendrá 26 columnas (schema v4.0) + 1 columna extra `_fenix_sync_at` con timestamp.

---

## CSV / JSON local

### CSV v4.0 — formato estándar

```bash
python -m src.skill.cli fenix export csv \
  --bucket COMPLETO,SOLO_EMAIL \
  --min-score 70 \
  --output output/leads_$(date +%Y%m%d).csv \
  --encoding utf-8 \
  --delimiter , \
  --json
```

### JSON con metadata completa

```bash
python -m src.skill.cli fenix export json \
  --bucket all \
  --include-raw-sources \
  --include-scoring-breakdown \
  --output output/leads_$(date +%Y%m%d).json \
  --json
```

---

## Pipedrive (opcional)

```bash
PIPEDRIVE_API_TOKEN=xxx
PIPEDRIVE_COMPANY_DOMAIN=skydropx
```

Mapeo similar a HubSpot pero usando Persons + Organizations + Deals.

---

## Mautic (open-source, autohospedado)

Si no se quiere depender de SaaS:

```bash
MAUTIC_BASE_URL=https://mautic.skydropx.internal
MAUTIC_USERNAME=fenix-bot
MAUTIC_PASSWORD=xxx
```

---

## Webhooks genéricos

```bash
python -m src.skill.cli fenix export webhook \
  --url https://api.skydropx.com/internal/leads-ingest \
  --auth-header "Bearer $INTERNAL_TOKEN" \
  --batch-size 100 \
  --json
```

Útil para feed directo a sistemas internos de Skydropx.

---

## Reglas de compliance al exportar

1. **NUNCA exportar leads `SIN_CONTACTO`** a CRM — solo a DB interna para reciclar.
2. **NUNCA exportar leads con `DATO_NO_VERIFICABLE`** en email/teléfono.
3. **Incluir siempre el campo `fuentes`** para trazabilidad LFPDPPP.
4. **Tag obligatorio** `source=OSINT-Fenix-v5` para que el equipo de ventas sepa el origen.
5. **Si el lead pide opt-out**, propagar a Fénix con `gdpr-purge` para que no vuelva a aparecer.
