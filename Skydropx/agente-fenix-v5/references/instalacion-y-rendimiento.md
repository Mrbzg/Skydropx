# Instalación + Rendimiento real (v5.3)

> Responde: cómo instalar todo en tu laptop, qué tan rápido es realmente,
> y cuál es la estrategia óptima (corridas chicas diarias vs mega-corridas).

---

## 📦 Instalación: 3 escenarios

### Escenario A — Setup mínimo (5 minutos)
**Cuándo:** primera prueba, validación rápida, demo.

```bash
git clone <repo> agente-fenix-v5
cd agente-fenix-v5
pip install -r requirements.txt     # Tier 0 + 1 (~20 MB)
```

**Qué obtienes:**
- ✅ DENUE/INEGI completo
- ✅ Validación tel MX (phonenumbers)
- ✅ Validación email (DNS MX + sintaxis)
- ✅ Hunter (crawling web básico)
- ✅ Re-enrich (DomainFinder + EmailInferencer)
- ✅ Social shops (dorks TikTok/IG/FB)
- ✅ HubSpot export + tier filter
- ✅ Pipeline completo de 10 agentes
- ❌ No Holehe / Maigret (deep OSINT)
- ❌ No Patchright (anti-bot pesado)

### Escenario B — Setup recomendado (15 min)
**Cuándo:** producción semanal estable.

```bash
pip install -r requirements.txt
pip install holehe maigret           # +2 OSINT esenciales
pip install patchright nodriver       # +anti-bot fuerte
patchright install chromium           # navegador para Patchright
```

**Adiciona:**
- ✅ Holehe: verifica email en 100+ servicios → identifica personas activas
- ✅ Maigret: encuentra LinkedIn/IG/Twitter del decisor
- ✅ Patchright: bypasea Cloudflare ligero
- ✅ Nodriver: anti-bot fuerte (Datadome, PerimeterX)

### Escenario C — Setup full (30 min, opcional)
**Cuándo:** mega-corridas, analytics avanzados.

```bash
pip install -r requirements-full.txt  # incluye Postgres, DuckDB, Celery
# + opcional: docker compose up -d searxng    # backend gratis perpetuo
```

---

## ⚡ Rendimiento REAL medido en este chat

| Setup | Leads | Duración | Leads/min |
|---|---|---|---|
| quick — solo DENUE | 30 | **1s** | 1800 |
| standard — DENUE solo | 1,533 | **60s** | 1,533 |
| standard — DENUE + Hunter 5% | 1,533 | **2.3 min** | 657 |
| standard — DENUE + Hunter 8% + re-enrich 10% | 1,533 | **6.3 min** | 243 |
| social_shops — TikTok/IG/FB | 93 | **97s** | 58 |

### El factor crítico: enrichment percentage

El **Hunter y Re-enrich son el cuello de botella** (HTTP requests externos).
A más enriquecimiento → más calidad de leads pero más tiempo.

| % enriquecimiento | Tiempo / 1K leads | Calidad |
|---|---|---|
| 0% (DENUE crudo) | 1 min | 0.3% PREMIUM |
| 5% (Hunter top) | 2 min | 5% PREMIUM |
| 10% (Hunter + re-enrich básico) | 4 min | 9% PREMIUM |
| 20% (Hunter + re-enrich + SearXNG) | 6-8 min | 15-25% PREMIUM |
| 30% (todo + deep_enrich) | 12-15 min | 25-35% PREMIUM |

---

## 🕐 Proyecciones a diferentes escalas

| Setup | 1K leads | 10K | 50K | 100K |
|---|---|---|---|---|
| **DENUE solo** | 40s | 7 min | 33 min | **1.1h** |
| DENUE + Hunter 5% | 2 min | 16 min | 1.3h | **2.6h** |
| DENUE + Hunter 8% + re-enrich 10% | 4 min | 42 min | 3.5h | **7h** |
| DENUE + Hunter 15% + re-enrich 20% | 6 min | 1.1h | 5.2h | **10.5h** |

---

## 🎯 La pregunta clave: ¿chicas diarias o mega-corridas?

### Mi respuesta honesta basada en tu setup (laptop L-V 8:30-18:00)

**RECOMENDADO: corridas medianas-pequeñas a demanda (200-2000 leads), NO mega-corridas.**

### Por qué NO mega-corridas (100K en una sesión):

| Razón | Detalle |
|---|---|
| **Tu laptop solo trabaja 9.5h/día** | Una corrida de 100K toma 7-10h. Si se interrumpe a las 6pm, queda incompleta |
| **El equipo de ventas no puede procesar 100K de golpe** | Un SDR cualifica/contacta ~50-100 leads/día. 100K = 1000+ días de trabajo |
| **Bloqueos** | Hunter + re-enrich a escala = miles de HTTP requests. Después de varios miles, los dominios empiezan a bloquearte (auto-throttle ayuda pero ralentiza) |
| **No puedes iterar** | Si la 1ra corrida no dio los leads que querías, perdiste 10 horas. Mejor 10 corridas de 1K con ajustes |
| **Te quedas sin dimensiones únicas** | Tu DB se llena de leads similares. Mejor diversificar nichos cada corrida |
| **Recursos compartidos** | DDG rate-limit, Serper créditos finitos, tu RAM compitiendo con Chrome/Slack |

### Por qué SÍ corridas medianas a demanda:

✅ **Cabe en tu jornada laboral** (200-2000 leads = 2-8 min, las haces en pausas)
✅ **Ventas tiene cola manejable** (200-500 leads/semana = ~40-100/día, óptimo para 1 SDR)
✅ **Puedes ajustar entre corridas** (lunes ropa, martes calzado, miércoles agencias)
✅ **Evitas bloqueos** (corridas chicas no triggerean rate-limits)
✅ **Mejor uso de Serper** (2,500 free durarían meses con uso prudente)
✅ **Puedes correr en background** mientras trabajas en otras cosas

---

## 📅 Plan operativo recomendado para Skydropx

### Semana típica (45-50 horas de laptop encendida)

| Día | Acción | Leads | Tiempo | Cuándo |
|---|---|---|---|---|
| **Lunes** | Planeación + corrida nicho A | 500 | 5 min | 10am |
| **Martes** | Corrida nicho B + revisar A | 500 | 5 min | 11am |
| **Miércoles** | Re-enrich los WARM de lun-mar | — | 10 min | 11am |
| **Jueves** | Corrida canal social (TikTok/IG) | 300 | 8 min | 10am |
| **Viernes** | Export tier=PREMIUM + audit | — | 5 min | 9am |
| **TOTAL semana** | | **~1,300** | **~33 min** | 5 sesiones cortas |

**= 5,200 leads/mes** sin afectar productividad del equipo.

### ¿Y si necesitas 100K leads?

**Distribuirlos en 4-6 semanas**, no 1 sesión:

- Semana 1: 25K leads (corridas de 5K × 5 días, 25min/día)
- Semana 2: 25K (otros nichos/estados)
- Semana 3: re-enrich masivo de huérfanos previos
- Semana 4: 25K (sociales/marketplaces)
- Semana 5-6: limpieza + nuevos focos

**Total real factible:** 100K leads/mes con **~30 min/día de tu laptop**.

---

## 💡 Comparación: chicas vs mega

| Métrica | Chicas (1K-2K diarios) | Mega (50K-100K una vez) |
|---|---|---|
| Tiempo total | 30-60 min/día × 5 días | 7-15 horas seguidas |
| Riesgo interrupción | Bajo (5 min cada una) | Alto (laptop se duerme, internet falla) |
| Bloqueos | Casi cero | Frecuentes después de ~10K |
| Ajustes entre corridas | ✅ Sí | ❌ No, todo de un golpe |
| Diversificación nicho/zona | ✅ Fácil rotar | ❌ Una sola dimensión |
| Carga para ventas | Manejable | Ahoga (1K/día por SDR) |
| Dedup intra-DB | Funciona perfecto | Funciona perfecto |
| Necesita checkpoint/resume | Opcional | Imprescindible |
| Uso de Serper credits | Mínimo (cada query individual) | Alto (potencia consumo rápido) |
| **Recomendado para Skydropx** | ✅✅✅ | ⚠ solo si tienes Oracle Cloud 24/7 |

---

## 🚀 Comandos modelo

### Corrida diaria típica (5 minutos)
```bash
fenix run \
  --nicho ropa --modelo B2C --zona CDMX --meta 500 \
  --mode standard --sources denue \
  --enrich-max 50

# Export inmediato para ventas:
fenix hubspot --tier PREMIUM --run-id "lunes-ropa-cdmx"
```

### Corrida temática semanal (15-20 min)
```bash
fenix run \
  --nicho calzado --modelo D2C --canal mixto --zona "Jalisco" --meta 2000 \
  --mode standard \
  --sources denue,social_shops \
  --enrich-max 100 \
  --re-enrich-max 150 --re-enrich-find-domains 30 --re-enrich-infer-emails 120

fenix hubspot --tier PREMIUM --tier GOLD --run-id "semana1-calzado-jalisco"
```

### Mega-corrida (solo si tienes Oracle Cloud 24/7)
```bash
# CON checkpoint OBLIGATORIO (puede interrumpirse)
fenix run \
  --nicho ropa --zona nacional --meta 50000 \
  --mode enterprise \
  --enrich-max 5000 --re-enrich-max 10000 \
  --force-checkpoint

# Si se interrumpe:
fenix run --nicho ropa --zona nacional --meta 50000 --resume-last
```

---

## 📊 Resumen ejecutivo

| Métrica clave | Valor |
|---|---|
| **Velocidad base (DENUE solo)** | 1,500 leads/minuto |
| **Velocidad realista con enrichment** | 200-400 leads/minuto |
| **Sweet spot por sesión** | 500-2,000 leads (5-15 min) |
| **Para 100K/mes** | 5K/día × 20 días hábiles = 30 min/día |
| **PREMIUM rate típico** | 8-15% con enrichment ligero |
| **PREMIUM rate maximizado** | 25-35% con re-enrich + SearXNG + Holehe |
| **Costo de instalación Tier 0+1** | $0, ~20 MB, 5 minutos |
| **Costo operativo mensual** | $0 USD |
