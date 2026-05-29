# Gaps de robustez 5-8: implementados ✅

> Resumen de los 4 gaps de robustez agregados en esta iteración, con código
> funcional, tests y comandos CLI.

---

## ✅ Gap #5: Retry Queue (`src/db/retry_queue.py`)

Mantiene leads que NO se pudieron enriquecer para reintentar en futuras corridas
con backoff exponencial.

**Cómo funciona:**
- Cuando un agente falla en enriquecer un lead (sin dominio, sin email después de probar),
  lo agrega a `retry_queue`
- Cada entrada tiene: `target`, `attempts`, `next_retry_at` (con backoff: 1d → 3d → 7d → 14d)
- `max_attempts` (default 5) antes de marcarse `exhausted`

**Tabla SQLite:**
```sql
CREATE TABLE retry_queue (
    id INTEGER PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id),
    target TEXT,                -- 'find_domain'|'infer_email'|'crawl_web'|'verify_smtp'
    attempts INTEGER DEFAULT 0,
    next_retry_at TIMESTAMP,
    status TEXT DEFAULT 'pending'
);
```

**Comandos CLI:**
```bash
fenix retry stats              # cuántos pendientes, exhausted, por target
fenix retry due --limit 50     # ver los que ya vencieron
```

---

## ✅ Gap #6: Auto-throttling (`src/core/throttle.py`)

Detecta cuando un dominio nos bloquea y reduce automáticamente la tasa.

**3 estados por dominio:**
- `healthy`: normal, sin delay
- `slowing`: 3+ fallos → delay creciente (5s → 10s → ... → 30s max)
- `quarantine`: 5+ fallos → quarantine 30 min, skip todas las requests

**Detecta como "bloqueo":**
- HTTP 403, 429, 503, 502, 504
- HTML con: `cloudflare`, `captcha`, `perimeterx`, `datadome`, `access denied`,
  `rate limit`, `too many requests`, `human verification`

**Persistencia:** `data/throttle_state.json` (sobrevive reinicios).

**Comandos CLI:**
```bash
fenix throttle stats            # ver dominios en quarantine/slowing
fenix throttle reset --domain X # reset manual (debug)
```

---

## ✅ Gap #7: Healthcheck pre-run (`src/core/healthcheck.py`)

Verifica TODO antes de empezar el pipeline. Aborta si hay CRITICAL.

**Checks implementados:**
1. `denue_token` (CRITICAL): hace test real contra DENUE API
2. `search_backends` (CRITICAL/WARNING): verifica que al menos 1 funcione
3. `db_accessible` (CRITICAL): schema OK, escritura OK
4. `output_dir` (CRITICAL): escribible
5. `disk_space` (CRITICAL <1GB, WARNING <5GB): espacio libre
6. `optional_deps` (WARNING): phonenumbers, trafilatura, etc.
7. `serper_credits` (WARNING <100): créditos Serper restantes
8. `memory_for_meta` (WARNING): RAM vs meta solicitada

**Comando CLI:**
```bash
fenix healthcheck                       # default meta=1000
fenix healthcheck --meta 10000          # validar contra meta grande
fenix healthcheck --fail-fast --json    # JSON para CI
```

**Integrado al run automáticamente:**
```bash
fenix run --nicho ropa --zona CDMX --meta 1000
# Si CRITICAL falla → aborta sin gastar tiempo
# Si WARNING → continúa con advertencia
```

Para desactivar: `--no-healthcheck`.

---

## ✅ Gap #8: Checkpoint + Resume (`src/agents/checkpoint.py`)

Sobrevive interrupciones: Ctrl+C, crash, apagón, timeout.

**Cómo funciona:**
- Cada agente, al terminar, guarda el `state` completo en `data/checkpoints/<job_id>.json`
- Si llamas `fenix run --resume <job_id>`, carga el state y reanuda desde el agente SIGUIENTE
- `fenix run --resume-last` busca automáticamente el último pendiente
- Al terminar exitosamente, borra el checkpoint

**Activación:**
- **Automática** si `meta >= 500` (ya que esos toman varios minutos)
- **Manual** con `--force-checkpoint` para metas chicas

**Comandos CLI:**
```bash
fenix checkpoint list                    # ver pendientes (job que NO terminó)
fenix checkpoint show --job-id fnx_xxx   # detalle del state guardado
fenix checkpoint delete --job-id fnx_xxx # borrar manualmente
fenix checkpoint cleanup --older-than 30 # purgar viejos
fenix checkpoint last-pending            # el más reciente
```

**Ejemplo de uso real:**
```bash
# Lunes 5 pm: corres mega corrida
fenix run --nicho ropa --zona nacional --meta 10000

# A las 6 pm cierras laptop sin querer
# Martes 9 am:
fenix run --nicho ropa --zona nacional --meta 10000 --resume-last
# → carga checkpoint, salta agents ya completados, continúa desde donde quedó
```

---

## 📊 Resumen ejecutivo

| Gap | Status | Comando CLI | Tests |
|---|---|---|---|
| #5 Retry queue | ✅ | `fenix retry stats/due` | 3 |
| #6 Auto-throttling | ✅ | `fenix throttle stats/reset` | 4 |
| #7 Healthcheck pre-run | ✅ | `fenix healthcheck` (auto en run) | 4 |
| #8 Checkpoint + Resume | ✅ | `fenix checkpoint list/show/delete` | 3 |

**Total tests robustez: 14/14 passing.**
**Suite completa: 114 passed, 9 skipped en 3 segundos.**

---

## 🎯 Estado final del sistema (v5.2)

| Métrica | Valor |
|---|---|
| Archivos totales | **93** (+5 esta vuelta) |
| Tests automatizados | **114 passing** |
| Tools MCP | 24 (sin cambios) |
| Comandos CLI | **28+** (3 nuevos: checkpoint, retry, throttle) |
| Costo operativo | **$0 USD/mes** |
| Serper.dev | ✅ Configurado (2,470/2,500 créditos restantes) |

---

## 🚀 Comando recomendado para Skydropx producción

```bash
# Corrida típica semanal con todo activo
fenix run \
  --nicho ropa --zona CDMX --meta 1000 --mode standard \
  --sources denue \
  --enrich-max 100 \
  --re-enrich-max 200 --re-enrich-find-domains 50 --re-enrich-infer-emails 150 \
  --force-checkpoint
# Healthcheck pre-run automático
# Checkpoint cada agente (puede reanudarse si se interrumpe)
# Throttle y retry queue trabajando silenciosamente

# Si la laptop se apaga a mitad:
fenix run --nicho ropa --zona CDMX --meta 1000 --resume-last

# Al terminar:
fenix hubspot --tier PREMIUM --run-id semana1
```
