# 🛠 Arsenal Fénix v5 — librerías OSINT integradas

> Cada herramienta es **opcional**: si no está instalada, el pipeline cae al fallback.
> Cero error 100% del tiempo. Instala solo las que necesites.

---

## 🕷 CAPA 1 — Scraping / Crawling

| Tool | Status | Cuándo instalarlo | Install |
|---|---|---|---|
| **Scrapy** | wrapper futuro | Crawling masivo de directorios MX (Páginas Amarillas, Canaco) | `pip install scrapy scrapy-splash` |
| **Botasaurus** | ✓ detectado en `antibot_fetcher.py` y `google_maps_source.py` | Cloudflare/PerimeterX, paralelo masivo | `pip install botasaurus` |
| **ScrapeGraphAI** | ✓ wrapper en `scrapegraph_extractor.py` | Extracción guiada por NLP con Ollama | `pip install scrapegraphai` |
| **Nodriver** | ✓ nivel L2 en `antibot_fetcher.py` | Anti-bot fuerte, sin webdrivers | `pip install nodriver` |
| **Patchright** | ✓ nivel L1 en `antibot_fetcher.py` | Cloudflare ligero, Playwright parchado | `pip install patchright && patchright install chromium` |

---

## 🗺 CAPA 2 — Fuentes de leads (Google Maps + directorios)

| Tool | Status | Yield esperado | Install |
|---|---|---|---|
| **google-maps-scraper (omkarcloud)** | ✓ wrapper en `google_maps_source.py` | 50+ data points/negocio (email, redes, etc.) | `pip install google-maps-scraper` |
| **gosom/google-maps-scraper** (CLI Go) | ✓ wrapper external CLI | Más ligero, masivo via CLI | `go install github.com/gosom/google-maps-scraper@latest` |

**Tip crítico:** Google Maps tiene cap de ~120 resultados/búsqueda.
Estrategia: dividir por colonia o municipio.

```bash
# Ejemplo: en lugar de "zapaterías CDMX" (120 max), usa:
python3 -m src.skill.cli fenix source maps --query "zapaterías Coyoacán CDMX"
python3 -m src.skill.cli fenix source maps --query "zapaterías Iztapalapa CDMX"
python3 -m src.skill.cli fenix source maps --query "zapaterías Cuauhtémoc CDMX"
# → 120 × 16 alcaldías = ~2K zapaterías CDMX sin pagar nada
```

---

## 🔍 CAPA 3 — OSINT / Reconocimiento de dominio

| Tool | Status | Para qué | Install |
|---|---|---|---|
| **theHarvester** | ✓ wrapper en `osint_tools.py` | emails, subdominios, IPs de un dominio | `pip install theHarvester` (Python 3.12+) |
| **EmailHarvester** | ✓ wrapper en `osint_tools.py` | Google Dorks → emails de un dominio | `git clone Towardscybersec/EmailHarvester` |

**Uso unificado:**

```bash
python3 -m src.skill.cli fenix harvest empresa.com.mx
# → corre theHarvester + EmailHarvester si están instalados
# → unifica emails/hosts/IPs encontrados
```

---

## ✅ CAPA 4 — Verificación de emails (la más crítica)

| Tool | Status | Tier | Install |
|---|---|---|---|
| **python-email-validator** | ✓ integrado | Sintaxis + IDNA | `pip install email-validator` |
| **dnspython** | ✓ integrado | MX lookup | `pip install dnspython` |
| **SMTP handshake nativo** | ✓ integrado (stdlib `smtplib`) | Verificación SMTP real | nada |

**Cascada implementada en `src/extraction/email_verifier.py`:**

```
1. Regex sintaxis        → 0ms si falla
2. Disposable check      → 0ms (set en memoria, 30+ dominios)
3. email_validator       → 1-5ms (IDNA + sintaxis robusta) — opcional
4. dnspython MX          → 50-200ms primera vez por dominio, después cache
5. SMTP handshake        → 1-5s (HELO + MAIL FROM + RCPT TO, sin enviar DATA)
```

**Uso:**

```bash
# Sin SMTP (rápido, ~100ms/email)
python3 -m src.skill.cli fenix verify contacto@empresa.mx ventas@otra.mx

# Con SMTP (lento, verificación real del mailbox)
python3 -m src.skill.cli fenix verify contacto@empresa.mx --smtp
```

---

## 📊 Cómo se usa en el pipeline

El **agent_verifier** (uno de los 7 agentes) automáticamente:

1. Para cada lead con email, llama a `EmailVerifier.verify()` (sintaxis + MX por default)
2. Si `--check-smtp` activo, hace handshake completo
3. Descarta `disposable`, `mx_missing`, `invalid_syntax`
4. Marca como `is_personal=True` los emails de gmail/hotmail/yahoo/etc.
5. Guarda status en `metadata["email_verification"]` y `contacts.verification_status`

**El bucket `COMPLETO` solo se asigna si el email pasó la verificación.**

---

## 🤖 Anti-bot escalado (en `src/extraction/antibot_fetcher.py`)

4 niveles, escalado automático si detecta bloqueo:

```
L0 requests + UA rotativo + proxy           ⚡ default, rápido
  ↓ (si Cloudflare detectado)
L1 Patchright (Playwright parchado)         Cloudflare ligero
  ↓ (si sigue bloqueado)
L2 Nodriver (CDP nativo, sin Selenium)      Anti-bot fuerte
  ↓ (si aún bloqueado)
L3 Botasaurus (cf_clearance persistente)    Último recurso
```

**Uso:**

```bash
# Ver qué niveles tienes disponibles
python3 -m src.skill.cli fenix antibot stats

# Fetch con escalado automático
python3 -m src.skill.cli fenix antibot fetch https://sitio-con-cloudflare.com
```

---

## 🗄 DB con dedup persistente (la pieza clave)

`src/db/engine.py` + `src/db/deduper.py` + `src/db/repositories.py`

**Tablas creadas automáticamente:**
- `companies` — empresas únicas con fingerprint
- `contacts` — emails/tels/redes con flag `is_verified`
- `jobs` — historial de corridas
- `job_companies` — relación M:N (qué jobs encontraron qué empresas)
- `raw_findings` — trazabilidad completa de qué fuente trajo qué dato
- `opt_outs` — LFPDPPP compliance
- `source_stats` — para el Self-Improver

**Dedup jerárquico:**

```
1. exact_match(email_norm)              → MISMO LEAD
2. exact_match(phone_norm last 10)      → MISMO LEAD
3. exact_match(dominio_norm)            → MISMA EMPRESA
4. fuzzy_match(nombre, 85%) + estado    → MISMA EMPRESA
```

**Cross-corrida garantizado:** corres el mismo nicho 10 veces, sigues teniendo
las mismas N empresas en DB (con `times_seen` incrementado).

**Uso:**

```bash
python3 -m src.skill.cli fenix db init                 # crea schema
python3 -m src.skill.cli fenix db stats                # contadores
python3 -m src.skill.cli fenix db jobs --limit 20      # historial
python3 -m src.skill.cli fenix db companies --bucket COMPLETO
python3 -m src.skill.cli fenix db opt-out --value cliente@noquiero.com --reason "user_request"
```

**Migración a Postgres** (cuando pases de 100K rows):

```bash
# 1. Levantar Postgres (ya viene en docker-compose.yml)
docker compose up -d postgres

# 2. Editar .env:
DATABASE_URL=postgresql://fenix:fenix_local@localhost:5432/fenix

# 3. Instalar driver
pip install sqlalchemy psycopg2-binary

# 4. Migrar
python3 -m src.skill.cli fenix db init
# ... y exportar/importar con db backup/restore (a implementar)
```

---

## 💯 Matriz de "qué instalar según tu caso"

| Caso | Instalar |
|---|---|
| Solo arrancar y probar | nada (todo built-in funciona) |
| Quiero verificar emails seriamente | `pip install email-validator dnspython` |
| Quiero Google Maps a escala | `pip install google-maps-scraper` |
| Quiero scrapear sitios con Cloudflare | `pip install patchright && patchright install chromium` |
| Quiero scrapear sitios anti-bot fuerte | `pip install nodriver` o `pip install botasaurus` |
| Quiero OSINT completo de un dominio | `pip install theHarvester` |
| Quiero extracción NLP-guiada con LLM local | `pip install scrapegraphai` + `ollama pull llama3.2:3b` |
| Quiero pasar de 100K leads | `pip install sqlalchemy psycopg2-binary` + Postgres |

Todo es 100% gratis y opcional.
