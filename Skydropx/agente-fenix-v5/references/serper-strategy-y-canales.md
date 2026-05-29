# Serper Strategy + Canales Sociales (TikTok/IG/FB)

> Responde las 2 preguntas críticas: ¿qué hace si Serper falla? ¿cómo
> resuelve "10 leads de ropa Monterrey D2C de TikTok"?

---

## 🎯 Pregunta 1: Estrategia de Serper

### Default: `SERPER_STRATEGY=reserve`

Serper.dev **NO se usa en queries normales**. Solo cuando:
- Los backends gratis (SearXNG, DDG, OpenSERP) fallaron → `context=fallback` automático
- El usuario marca una query como crítica → `context=critical`
- Forzado explícitamente → `context=forced`

**Por qué:** preserva los **2,500 créditos free lifetime** para casos de alto valor
(no malgastarlos en queries que SearXNG/DDG pueden resolver).

### Tabla de prioridades actualizada

| Backend | Priority | Cuándo se usa |
|---|---|---|
| **SearXNG** | 1 | Primero si está corriendo (`docker compose up -d`) |
| **DDG HTML** | 2 | Si SearXNG no disponible — siempre funciona |
| **OpenSERP** | 3 | Google directo self-hosted (necesita proxies) |
| **Serper.dev** | 99 | **RESERVA** — solo si los anteriores fallaron o context=critical |

### 5 estrategias configurables

```bash
# En .env:
SERPER_STRATEGY=reserve     # default: solo crítical o fallback
SERPER_STRATEGY=fallback    # idem a reserve (alias semántico)
SERPER_STRATEGY=critical    # solo para queries marcadas, NO fallback automático
SERPER_STRATEGY=priority    # úsalo siempre primero (gasta créditos rápido)
SERPER_STRATEGY=disabled    # nunca
```

### Fallback automático

Cuando una query falla con SearXNG+DDG (todos los gratis), el sistema
**automáticamente** reintenta con `context=fallback` que activa Serper:

```python
# El flujo interno:
try:
    results = mgr.search(query, context="normal")  # gratis primero
except all_failed:
    results = mgr.search(query, context="fallback")  # ← activa Serper
```

### Caveat importante de Serper FREE

**Serper free NO permite operadores avanzados** (`site:`, `intitle:`, `filetype:`).
Devuelve `400 "Query pattern not allowed for free accounts"`.

**Solución implementada:** cuando Serper devuelve 400 por dorks, el sistema
**simplifica automáticamente** el dork removiendo operadores y los reintenta:

```
'site:tiktok.com "ropa" "mty" "envíos"'
    ↓ simplificación automática
'ropa mty envíos tiktok'
    → 200 OK, devuelve resultados
```

Esto significa que **incluso en free tier**, Serper sirve como fallback útil.

---

## 🎯 Pregunta 2: "10 leads de ropa Monterrey D2C de TikTok"

### Antes (no funcionaba):

```bash
fenix run --nicho ropa --modelo D2C --canal social --zona "Nuevo Leon" --meta 10
# → ignoraba canal=social, usaba DENUE, traía tiendas físicas
```

### Ahora (funciona):

```bash
fenix run --nicho ropa --modelo D2C --canal social --zona "Nuevo Leon" \
  --meta 10 --sources social_shops
# → 93 leads de TikTok/IG/FB en 97 segundos
```

### Cómo se resuelve

**1. Mapeo canal → sources automático:**
```python
CANAL_TO_SOURCES = {
    "social":      ["social_shops"],          # ← TikTok+IG+FB
    "marketplace": ["mercadolibre"],
    "web":         [],                          # default
    "mixto":       ["social_shops", "mercadolibre"],
}
```

Cuando el usuario pone `--canal social`, el Scout automáticamente activa
`social_shops` como source adicional.

**2. `social_shops` genera dorks por plataforma:**

| Plataforma | # dorks default | Categorías |
|---|---|---|
| TikTok | 9 | tiendas, intent_compra, hashtags_mx |
| Instagram | 10 | d2c_mx, intent_comercial, hashtags_emprendedores |
| Facebook | 6 | pages_tiendas, marketplace, grupos |

**3. Extracción inteligente de handles:**

El sistema captura el handle de:
- URL directa (`instagram.com/regio_boutique/` → `@regio_boutique`)
- Snippets de posts (`"regio_boutique on May 6"` → `@regio_boutique`)
- Menciones `@handle` en title/snippet

**4. Detección de contactos en bio/snippet:**

Cada lead descubierto incluye:
- WhatsApp (links `wa.me/...`)
- Teléfonos visibles en bio
- Emails en descripción
- URLs externas (link in bio)
- Hashtags comerciales MX (`#hechoenmexico`, `#emprendedoramx`, etc.)
- Flag `intent_envios` si menciona "envíos a toda la república"

### Resultados reales (probado en vivo)

```
93 leads de TikTok/IG/FB para "ropa Monterrey D2C":

  · @storemtyboutique     → +528115455792 + WA + 📦envíos
  · @alexa_arm            → +525549349760 + WA + 📦envíos
  · @roalymayoreo24       → mayoreo + tel + WA + envíos
  · @cocoastoremayoreo    → mayoreo + envíos
  · @regio_boutique       → envíos a toda la república
  · @befashionstore.mx    → +528112105271
  · @ciruela.mty          → WhatsApp link en bio
  + 86 más
```

### Comando con configuración fina

```bash
# Solo TikTok + Instagram (sin Facebook)
fenix run --nicho ropa --modelo D2C --canal social --zona "Nuevo Leon" \
  --meta 50 --sources social_shops
# (configurable vía plan YAML con social_platforms: ["tiktok", "instagram"])
```

```yaml
# plans/social_tiktok.yaml
name: "TikTok shops MTY"
nicho: ropa
zona: "Nuevo Leon"
modelo: D2C
canal: social
meta: 50
sources:
  - social_shops
extras:
  social_platforms: ["tiktok", "instagram"]
  social_dork_categories: ["tiendas_d2c_mx", "intent_compra"]
  social_limit_per_dork: 15
  social_use_serper_critical: true    # usa Serper si gratis fallan
```

---

## 📊 Estado del sistema (v5.3)

| Métrica | v5.2 | v5.3 |
|---|---|---|
| Archivos | 93 | **92** |
| Tests | 114 | **114** ✅ |
| Tools MCP | 24 | 24 |
| Comandos CLI | 28+ | 28+ |
| Serper.dev | priority=1 (gastaba rápido) | **priority=99 + strategy=reserve** |
| Canales D2C/social | ❌ no funcionaba | ✅ funciona end-to-end |
| Backends gratis primero | ❌ no | ✅ SearXNG → DDG → OpenSERP → Serper |
| Auto-simplify Serper free | ❌ no | ✅ dorks → queries simples |

---

## 🚀 Para Skydropx producción

```bash
# Caso típico "tiendas D2C en TikTok/IG":
fenix run --nicho ropa --canal social --zona "Nuevo Leon" --meta 50 --sources social_shops

# Caso "marketplaces":
fenix run --nicho ropa --canal marketplace --zona CDMX --meta 100 --sources mercadolibre

# Caso "todo combinado":
fenix run --nicho ropa --canal mixto --zona CDMX --meta 500 \
  --sources social_shops,mercadolibre,denue,camaras

# Caso "agencia detrás de campaña" (futuro: marca como critical):
fenix agency --dominio datumax.mx
# → usa Serper en critical mode automáticamente
```
