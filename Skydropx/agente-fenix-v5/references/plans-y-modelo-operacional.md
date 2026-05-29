# Plans YAML + Modelo Operacional Skydropx

> **NO es scheduling automático.** Es atajo manual para campañas reusables.
> El usuario decide CUÁNDO correr cada plan, conforme su flujo de trabajo.

---

## 🎯 Por qué NO usamos cron automático

| Lo que asume el cron | Tu realidad |
|---|---|
| Laptop encendida 24/7 | Encendida L-V 8:30-18:00 (~47h/semana) |
| Pipeline corre solo de noche | Compu apagada de noche |
| Si arranca a las 22:00 no ejecuta | Cron silenciosamente fallido — invisible |
| Pipeline ciego repetitivo | Las campañas necesitan contexto humano |

**Conclusión:** un cron nocturno NUNCA correría en tu laptop, y aunque corriera durante el día, competiría por CPU/RAM con tu trabajo real.

---

## ✅ El modelo operacional correcto

```
Equipo de outbound, en cualquier momento del día:
   "Necesito 500 leads de calzado en CDMX"
        ↓
   Opción A (rápida):
        fenix ask "..." --run
   Opción B (con plan guardado):
        fenix plans run plans/mi_campana.yaml
        ↓
   Pipeline corre 2-5 min en background
        ↓
   CSV en output/ + DB actualizada
        ↓
   Subir CSV a HubSpot manualmente
```

**Ventajas:**
- ✅ Control humano: corres cuando hace sentido (lunes de planeación, jueves de refresh)
- ✅ Contextual: ajustas la campaña según resultados de la semana
- ✅ No interrumpe tu trabajo: corres cuando vas por café
- ✅ Trazable: cada corrida queda en `data/plans_history.json` con stats

---

## 📂 Estructura de un Plan YAML

```yaml
# === Metadata (no afecta pipeline) ===
name: "Nombre legible"
description: "Para qué sirve"

# === Obligatorios ===
nicho: "ropa"               # del catálogo (ver data/nicho_scian.json)
zona: "CDMX"                # estado, ciudad o "nacional"
meta: 500                   # cuántos leads

# === Opcionales ===
modelo: "B2C"               # B2B|B2C|C2C|D2C|C2B (auto-inferido si vacío)
canal: "web"                # web|social|marketplace|fisica|mixto
mode: "standard"            # quick|standard|deep|enterprise (auto por meta)

sources:
  - denue
  - dorks
  - camaras

scianes:                    # override del nicho catálogo
  - "4632"
  - "4633"

estratos:                   # filtrar tamaño DENUE (1-7)
  - "3"
  - "4"

enrich_max: 100             # webs a crawlear con Hunter
deep_enrich_max: 50         # OSINT profundo top-N (0 = skip)
deep_enrich_tools:
  - holehe
  - maigret

format: "csv,json"
include_large: false        # permitir Estrato 7 (251+ empleados)
include_medianas_grandes: false

extras:
  dork_categorias: ["envios_mx", "shopify"]
  dork_queries_extra:
    - '"compra y gana" "mundial" site:.mx'

tags:                       # para tracking
  - "skydropx"
  - "semana1"
```

Ver `plans/EJEMPLO.yaml` para la plantilla completa con todas las opciones documentadas.

---

## 🚀 Comandos disponibles

```bash
# Listar todos los plans en plans/
fenix plans list

# Ver detalle de un plan
fenix plans show plans/mi_campana.yaml

# Ejecutar un plan
fenix plans run plans/mi_campana.yaml

# Ejecutar con overrides (no modifica el archivo)
fenix plans run plans/mi_campana.yaml --meta 1000 --zona Jalisco

# Ver historial de corridas (cuándo y cuántos leads)
fenix plans history
fenix plans history --file plans/mi_campana.yaml --limit 50
```

---

## 💡 Workflow sugerido para el equipo Skydropx

### 1. Setup inicial (1 vez)
- Crear `plans/skydropx_pyme_ecommerce.yaml` con la configuración típica de campaña PyME
- Crear `plans/skydropx_enterprise.yaml` con configuración Enterprise (3PL/agencias)
- Crear `plans/skydropx_evento_temporada.yaml` con extras de dorks de eventos

### 2. Día a día (manual, on-demand)
```bash
# Lunes 9 am: planeación semanal
fenix plans history                       # ¿qué corrí la semana pasada?
fenix dedup-audit report                  # ¿hay solapamiento?
fenix dedup-audit unused --nichos "ropa,calzado,joyeria"   # ¿qué combos están frescos?
fenix events active                       # ¿hay evento que aprovechar?

# Decisión: corro plan A o B según contexto
fenix plans run plans/skydropx_pyme_ecommerce.yaml --meta 500

# Cuando termine (2-5 min):
fenix hubspot --only-bucket COMPLETO --limit 500
# → genera CSVs listos para subir a HubSpot

# Subir CSVs a HubSpot manualmente vía UI
```

### 3. Jueves: refresh
```bash
# Ver qué leads quedaron sin enriquecer la semana pasada
fenix dedup-audit report

# Si hay muchos huérfanos, corro solo Hunter
fenix run --nicho ropa --enrich-max 500 --sources denue --mode quick

# Si hay evento próximo, agregar plan temporada
fenix plans run plans/skydropx_evento_temporada.yaml
```

---

## 📊 Cuándo SÍ usaríamos cron (futuro hipotético)

Solo tendría sentido si:
1. Mueves Fénix a Oracle Cloud Always Free (corre 24/7 gratis)
2. El equipo decide "queremos batch garantizado cada noche"
3. Existe un proceso de revisión humana al día siguiente para los CSVs generados

Mientras eso no aplique, **mantener todo on-demand es la decisión correcta.**

Si en el futuro quieres migrarlo a cron, agregar a `crontab -e`:
```cron
0 22 * * 1 cd /opt/fenix && python -m src.skill.cli fenix plans run plans/lunes.yaml
```

Es 1 línea. Mejor no agregar complejidad hoy por necesidad futura hipotética.

---

## 🔌 Desde Claude Code / opencode (MCP)

Las 3 tools nuevas disponibles:

| Tool MCP | Ejemplo de uso conversacional |
|---|---|
| `plans_list` | "¿Qué campañas tengo guardadas?" |
| `plans_run` | "Corre la campaña PyME ecommerce con meta 1000" |
| `plans_history` | "¿Cuándo fue la última vez que corrí leads de ropa CDMX?" |

Total tools MCP: **24** (era 21).
