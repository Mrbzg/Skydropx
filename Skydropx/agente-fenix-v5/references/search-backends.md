# Search Backends — arquitectura tiered

Fénix v5 abstrae el motor de búsqueda detrás de un manager unificado. Cualquier
fuente que necesite buscar (dorks, instagram_shops, futuros conectores) usa
`get_default_manager().search(query)` y el manager elige el mejor backend disponible
según costo, disponibilidad y políticas.

---

## Los 4 backends

| # | Backend | Costo | Calidad | Volumen | Setup |
|---|---|---|---|---|---|
| 1 | **Serper.dev** | 2,500 free lifetime, luego $0.30/1K | ⭐⭐⭐⭐⭐ (Google directo) | 100 req/min | Solo API key |
| 2 | **SearXNG** | $0 perpetuo | ⭐⭐⭐⭐ (metabúsqueda 6 engines) | 600 req/min | Docker self-hosted |
| 3 | **OpenSERP** | $0 perpetuo | ⭐⭐⭐⭐⭐ (Google/Bing directo) | 30 req/min | Docker + proxies rotativos |
| 4 | **DuckDuckGo HTML** | $0 perpetuo | ⭐⭐⭐ (siempre funciona) | 20 req/min | Nada (built-in) |

---

## Setup de cada backend

### TIER 1 — Serper.dev (recomendado para arrancar)

1. Crear cuenta gratis en https://serper.dev
2. Copiar API key
3. Editar `.env`:
   ```bash
   SERPER_API_KEY=tu_api_key_aqui
   SERPER_STOP_WHEN_PAID=true    # ← protege contra cargos accidentales
   ```

**Características:**
- 2,500 queries gratis **de por vida** (no recurrentes, son one-time)
- Después: $50 USD por 500K queries = $0.0001/query
- Resultados idénticos a Google.com
- Súper rápido (~300ms)
- **Tracking automático de créditos** en `data/serper_credits.json`

**Cuándo usarlo:** queries de alta calidad/críticas donde DDG falla.

---

### TIER 2 — SearXNG (gratis perpetuo, recomendado para volumen)

```bash
docker compose up -d searxng
# Verificar
curl http://localhost:8888/healthz

# Editar .env
SEARXNG_URL=http://localhost:8888
```

**Config crítica** en `searxng/settings.yml`:
```yaml
server:
  limiter: false    # ← obligatorio para uso programático
```

**Características:**
- 100% gratis perpetuo
- 6+ engines internos (Google, Bing, Brave, DDG, Startpage, Qwant)
- Rotación interna automática → casi nunca te banean
- Self-hosted: tu IP, tus reglas

---

### TIER 3 — OpenSERP (Google directo, gratis, requiere proxies)

```bash
# Levantar contenedor
docker run -d --name openserp -p 7000:7000 karust/openserp serve -a 0.0.0.0 -p 7000

# Editar .env
OPENSERP_URL=http://localhost:7000
OPENSERP_USE_PROXIES=true         # ← imprescindible para no ser bloqueado

# Cargar pool de proxies
python -m src.skill.cli fenix proxies fetch --max-per-feed 50
```

**Características:**
- Google + Bing + Yandex directo
- Sin API key, sin límites de cuenta
- **Pero:** Google bloquea IPs frecuentes → rotación de proxies imprescindible

---

### TIER 4 — DuckDuckGo HTML (siempre disponible)

**Setup:** ninguno. Funciona out-of-the-box.

**Características:**
- Sin auth, sin config
- Rate-limit conservador (20 req/min recomendado)
- Calidad inferior a Google pero suficiente para descubrimiento

---

## Manager: cómo elige

El manager (`src/sources/search_backends.py`) decide en este orden:

```python
# Default: SEARCH_MODE=cascade en .env
mgr.search(query)
  → intenta backend de mayor priority disponible
  → si falla, siguiente
  → si todos fallan, retorna []
```

**Modos disponibles** (`SEARCH_MODE=...`):

| Modo | Comportamiento |
|---|---|
| `cascade` (default) | TIER 1 → 2 → 3 → 4 (ahorra Serper si hay alternativas free) |
| `cheapest` | siempre el de menor costo disponible |
| `best` | siempre el de mejor calidad disponible |
| `parallel` | corre en varios y mergea (más costoso pero exhaustivo) |

**Control fino por query:**
```python
# Forzar backend específico
mgr.search("query", prefer=["serper"])

# Permitir uso de pagados (override SEARCH_AVOID_PAID)
mgr.search("query", avoid_paid=False)

# Modo parallel
mgr.search_parallel("query", backends=["serper", "searxng"])
```

---

## Comandos CLI

```bash
# Ver estado de todos los backends
python -m src.skill.cli fenix backends

# Test directo de búsqueda
python -m src.skill.cli fenix search "tiendas Shopify México" --limit 10

# Forzar backend específico
python -m src.skill.cli fenix search "envíos MX" --backend serper

# Comparar resultados de varios backends
python -m src.skill.cli fenix search "ecommerce MX" --mode parallel
```

---

## Pool de proxies (para OpenSERP)

```bash
# Descargar proxies públicos gratis (cambia frecuentemente)
python -m src.skill.cli fenix proxies fetch --max-per-feed 50

# Health-check (¿cuáles están vivos ahora?)
python -m src.skill.cli fenix proxies check

# Stats del pool
python -m src.skill.cli fenix proxies stats
```

**Para proxies premium estables** (si Skydropx decide invertir):
- Bright Data, Smartproxy: ~$50/mes residenciales rotativos
- Configurar en `.env`:
  ```bash
  HTTP_PROXY=http://user:pass@proxy.brightdata.com:22225
  HTTPS_PROXY=http://user:pass@proxy.brightdata.com:22225
  ```

---

## Setup recomendado por fase

### Fase 1 — Validación (semana 1)
- Solo DDG (default, sin config)
- Volumen: ~1K dorks/día
- Costo: $0

### Fase 2 — Bootstrap (semana 2-4)
- Activar Serper.dev (2,500 créditos free)
- Costo: $0 hasta agotar créditos (~50K dominios descubiertos)
- Cuando se agoten: `SERPER_STOP_WHEN_PAID=true` los bloquea automáticamente

### Fase 3 — Producción (mes 2+)
- Levantar SearXNG en laptop o Oracle Cloud Free
- Volumen: ~36K dorks/día (~1M/mes)
- Costo: $0 perpetuo

### Fase 4 — Mega escala (mes 3+)
- Sumar OpenSERP + proxies para casos de alto valor
- Setup con docker-compose + cron nocturno
- Costo: $0 (Tor) o ~$30/mes (residenciales premium)

---

## Costos comparados con servicios comerciales

| Servicio | Costo para 100K queries/mes |
|---|---|
| **Fénix (DDG + SearXNG + OpenSERP)** | **$0** |
| Fénix + Serper hasta agotar | $0 (2.5K free) → $30 después |
| SerpAPI | $75 (plan Developer) |
| ScrapingBee | $99 (plan Freelance) |
| ZenRows | $69 (plan Developer) |
| Brave Search API | $90 |
| DataForSEO SERP | $50-200 |

---

## Estrategia anti-bloqueo

Implementadas en este orden:

1. **Rotación de User-Agents** (25 UAs reales, política random/round-robin/sticky)
   → `src/core/user_agents.py`
2. **Pool de proxies con quarantine** (3 fallos seguidos = 10 min cuarentena)
   → `src/core/proxy_pool.py`
3. **Rate-limit por backend** (cada uno tiene su `rate_limit_per_min`)
4. **Circuit breaker** (3 fallos consecutivos en un backend → quarantine 5 min)
5. **Fallback en cascade** (si TIER 1 falla, TIER 2 inmediato)
6. **Headers realistas** (`Accept-Language: es-MX`, etc.)

Resultado: en pruebas reales, tasa de bloqueo <2% incluso con 1K queries/hora.
