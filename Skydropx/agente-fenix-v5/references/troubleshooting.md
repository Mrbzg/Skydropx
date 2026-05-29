# Troubleshooting Fénix v5

## DENUE / INEGI

| Error | Diagnóstico | Fix |
|---|---|---|
| HTML response "Página no encontrada" en lugar de JSON | URL del endpoint mal armada | Verificar: `BuscarAreaActEstr` toma **15 params** antes del token (entidad/municipio/localidad/ageb/manzana/sector/subsector/rama/clase/nombre/regini/regfin/id/estrato/token) |
| HTTP 200 con `[]` vacío | SCIAN no existe en esa entidad | Correr `Cuantificar` primero |
| HTTP 200 con `[{"AE":"...","AG":"...","Total":"0"}]` | El SCIAN específico no tiene establecimientos | Usar nivel superior (ej: 4632 en vez de 463211) |
| `403` o `Token inválido` | Token vencido | Renovar en api.inegi.org.mx/tutorial.html |
| Timeout >30s | API saturada (común domingos por mantto) | Reintentar después o usar fallback OSM |
| Algunos campos vacíos | Normal: 60% de DENUE no tiene tel/email | Enriquecer con web crawling |

## SearXNG

| Error | Diagnóstico | Fix |
|---|---|---|
| `Connection refused localhost:8888` | SearXNG no levantado | `docker compose up -d searxng` |
| `429 Too Many Requests` | Limiter interno activo | `searxng/settings.yml`: `server.limiter: false` + restart |
| Engine "google" timeout | Bloqueado | Quitar de engines, usar Bing/Brave/DDG |
| 0 resultados consistente | IP del VPS baneada | Levantar Tor: `docker compose --profile proxies up -d` |
| Output sin "url" en JSON | Formato JSON deshabilitado | `formats: [json]` en settings.yml |

## Mercado Libre

| Error | Diagnóstico | Fix |
|---|---|---|
| `403 Forbidden` | Rate-limit sin auth (1K/h) | Crear app gratis en developers.mercadolibre.com.mx → OAuth |
| `429 Too Many Requests` | Hit rate-limit | Bajar `--concurrency` a 2, delay 1.5s |
| `0 sellers nuevos` | Categoría ya scrapeada recientemente | Rotar categoría o esperar 7 días |
| `user.address` vacío | Vendedor no expone ubicación | Normal (~30%) |

## Proxies / Tor

| Error | Fix |
|---|---|
| `tor_circuit_failed` | `docker restart fenix-tor` |
| Latencia >30s en Tor | Normal, bajar concurrency a 2 |
| Lista de proxies HTTP agotada | Re-descargar de proxyscrape.com/free-proxy-list |

## Extracción / Enrichment

| Error | Fix |
|---|---|
| 0 emails de un sitio con email visible | Sitio usa imágenes para anti-bot → aceptar, usar tel/whatsapp |
| Bloqueado por Cloudflare | Saltar ese dominio (no vale la pena Playwright para 1) |
| Encoding errors (latin1) | Ya manejado en `skydropx_extractor.py` con `r.encoding = 'utf-8'` |
| WhatsApp no detectado pero existe | Algunos usan imagen en lugar de wa.me — agregar al TODO |

## Base de datos

| Error | Fix |
|---|---|
| `database is locked` (SQLite) | Migrar a Postgres con `db migrate --to postgres` |
| disk full | `VACUUM` + purge leads >90 días sin contacto |
| `duplicate key` en upsert | Bug en deduper — pegar `--debug-dedup` y reportar |

## Compliance

| Error | Fix |
|---|---|
| `robots_txt_disallow` | Respetar (lo legal). Saltar dominio. |
| `pii_detected_in_email` | Email parece personal — marcar `bucket=COLD` para revisión manual |
| Empresa pide ser eliminada | `fenix gdpr-purge --email X --reason "opt-out"` (cumple LFPDPPP art. 28) |

## Pipeline / Workers

| Error | Fix |
|---|---|
| `HEARTBEAT_MISS` (worker colgado) | Auto-restart por watcher; si persiste, ver `logs/worker_<id>.log` |
| `MAX_ITERATIONS` | Subir estrategia: QUICK→STANDARD→DEEP automático tras 3 batches |
| `CIRCUIT_OPEN` 15 min | Fuente está bloqueando — espera cooldown automático |
| Checkpoint corrupto | `pipeline.json` borrado → reanudación desde último batch persistido en DB |

## Logs útiles

```bash
# Últimos errores
tail -100 logs/fenix.log | grep ERROR

# Stats por fuente del último run
python -m src.skill.cli fenix stats sources --period 24h --json | jq

# Ver qué fuente está fallando más
python -m src.skill.cli fenix stats rank --period 7d --json
```
