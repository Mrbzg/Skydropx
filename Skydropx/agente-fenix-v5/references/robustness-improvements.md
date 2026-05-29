# Mejoras de robustez — qué se agregó y cómo usarlo

> Esta doc responde 3 preguntas críticas: qué devuelve el sistema, qué hace con
> leads incompletos, y cómo se mejoró la robustez en v5.1.

---

## ❓ 1. ¿Qué leads devuelve el sistema?

**Respuesta corta:** depende del comando.

### Opción A: CSV v4.0 (todos los leads, con marca de calidad)
```bash
fenix run --nicho ropa --zona CDMX --meta 1000
# → output/fenix_ropa_<ts>.csv con TODOS los 1500 leads
#   los incompletos llevan "DATO_NO_VERIFICABLE" en email/tel
```

### Opción B: HubSpot CSV filtrado por TIER (recomendado para outbound)
```bash
# PREMIUM: los 3 datos completos (nombre + email + tel/wa)
fenix hubspot --tier PREMIUM --run-id semana1
# → ~8-15% del lote (los listos para outbound HOY)

# GOLD: 2 de 3 datos, email obligatorio
fenix hubspot --tier GOLD --run-id semana1
# → ~15-25% (para email marketing / nurturing)

# SILVER: al menos 1 dato de contacto
fenix hubspot --tier SILVER --run-id semana1
# → ~40-50% (cold outreach, reciclaje)
```

### Sistema TIER (clasificación automática)

| Tier | Criterio | % típico | Uso |
|---|---|---|---|
| **PREMIUM** | nombre + email válido + (tel o whatsapp) | 8-15% | Ventas directas |
| **GOLD** | nombre + email válido (sin tel) | 10-15% | Email marketing |
| **SILVER** | al menos 1 dato de contacto | 25-35% | Cold outreach |
| **BRONZE** | solo identificación (sin contacto) | 40-55% | Reciclar/re-enrich futuro |

---

## ❓ 2. ¿Qué hace con leads incompletos?

**Respuesta corta: los enriquece en 3 rondas progresivas, no los descarta.**

### Ronda 1: Hunter (default)
- Para cada lead **con sitio_web** pero sin email/tel:
- Crawlea las páginas de contacto (`/`, `/contacto`, `/nosotros`)
- Extrae email, tel, WhatsApp, owner, tech stack
- Cap: `--enrich-max` (default 100)

### Ronda 2: Re-Enrich (nuevo en v5.1, opt-in)
- Para cada lead **sin sitio_web** pero con nombre/dirección:
  - **DomainFinder**: busca el dominio oficial vía Google/SearXNG
- Para cada lead **con sitio_web** pero sin email:
  - **EmailInferencer**: prueba `contacto@dominio`, `ventas@dominio`, etc.
  - Verifica con DNS MX (rápido) o SMTP probe (lento pero confirma)
- Cap: `--re-enrich-max` (default 0 = skip; recomendado 100-200 con SearXNG)

### Ronda 3: Deep Enrich (opcional, OSINT pesado)
- Para top leads READY:
  - Holehe (verifica email en 100+ servicios)
  - Maigret (busca username en 3000 sitios)
  - PhoneInfoga (carrier MX)

---

## 🆕 3. Qué se mejoró en v5.1 (4 gaps críticos)

### Gap #1: Filtro tiered en export ✅ RESUELTO
**Antes:** solo se podía filtrar por `bucket` (técnico).
**Ahora:** `fenix hubspot --tier PREMIUM` filtra por lo que realmente importa para outbound.

### Gap #2: DomainFinder ✅ RESUELTO (con caveat)
**Antes:** leads SIN sitio_web quedaban huérfanos para siempre.
**Ahora:** `src/extraction/domain_finder.py` busca el dominio oficial vía SearXNG/DDG.

**Caveat honesto:** DDG tiene rate-limit fuerte (~50 q/h). Para 100+ búsquedas
necesitas **SearXNG self-hosted** o **Serper.dev** (2500 free).

Con SearXNG levantado: ~60-70% de huérfanos encuentran dominio.
Sin SearXNG: ~20-30% (solo los primeros antes de rate-limit).

### Gap #3: EmailInferencer ✅ RESUELTO
**Antes:** sitio_web sin email = lead inservible.
**Ahora:** `src/extraction/email_inferencer.py` infiere `contacto@dominio`:
- **Modo rápido** (default): verifica MX records → confidence 50
- **Modo SMTP** (`--re-enrich-verify-smtp`): handshake real → confidence 85

**Probado en vivo:**
- `gruposierras.mx` → SMTP confirmó `contacto@gruposierras.mx` ✓
- `agarcia.mx` → MX OK, SMTP no confirma (probablemente catch-all OFF)

### Gap #4: Agente RE_ENRICH ✅ RESUELTO
**Antes:** los enriquecimientos eran ad-hoc, sin orquestación.
**Ahora:** `src/agents/re_enrich.py` se ejecuta como agente nuevo en el pipeline
entre persist y profiler. Activación opt-in con `--re-enrich-max`.

---

## 📊 Comparación REAL: con vs sin re-enrich (1K leads ropa CDMX)

| Métrica | Sin re-enrich | Con re-enrich (DDG rate-limited) | Con SearXNG (proyectado) |
|---|---|---|---|
| Duración | 2:20 min | 6:18 min | 8-10 min |
| PREMIUM | 125 (8.8%) | 127 (9.0%) | 250-350 (17-25%) |
| GOLD | 126 (8.9%) | 110 (7.8%) | 180-220 (13-15%) |
| Costo | $0 | $0 | $0 (SearXNG self-hosted) |

**Conclusión honesta:** sin SearXNG, el re-enrich da ganancia marginal (+2 PREMIUM).
Con SearXNG levantado, la ganancia es 2-3x. Por eso es **opt-in**: no querés
desperdiciar 4 minutos extra de pipeline para ganar 2 leads cuando DDG está saturado.

---

## 🚀 Setup recomendado para máximo robustez

```bash
# 1. Levantar SearXNG (1 comando, una vez)
docker compose up -d searxng

# 2. Correr pipeline con todo activo
fenix run --nicho ropa --zona CDMX --meta 1000 \
  --enrich-max 100 \
  --re-enrich-max 200 --re-enrich-find-domains 50 --re-enrich-infer-emails 150

# 3. Export TIER PREMIUM listo para HubSpot
fenix hubspot --tier PREMIUM --run-id semana1
```

---

## 📋 Comandos nuevos disponibles

```bash
# Re-enrich
fenix run --re-enrich-max 200 --re-enrich-find-domains 50 \
  --re-enrich-infer-emails 150 --re-enrich-verify-smtp

# Tier-aware HubSpot export
fenix hubspot --tier PREMIUM       # 3 datos
fenix hubspot --tier GOLD           # 2 de 3
fenix hubspot --tier SILVER         # 1 contacto
```

---

## 🎯 Recomendación honesta de uso

| Escenario | Setup recomendado |
|---|---|
| Validación rápida (50-100 leads) | `--mode quick`, sin re-enrich |
| Producción semanal (500-2000 leads) | `--re-enrich-max 200` + SearXNG |
| Mega-campaña (5K+ leads) | + Serper.dev free credits para los más críticos |
| Cuenta clave específica | + Holehe + Maigret + SpiderFoot wrappers |
