# CLI Fénix v5 — Referencia completa

Todos los comandos usan `python -m src.skill.cli fenix <subcomando>`. Agregar `--json` siempre.

---

## 🟢 Comandos principales

### `fenix healthcheck`
Verifica que toda la infra esté lista.
```bash
python -m src.skill.cli fenix healthcheck --json
```

### `fenix run`
El comando todo-en-uno. Ejecuta los 7 agentes en secuencia.
```bash
python -m src.skill.cli fenix run \
  --nicho "moda femenina" \
  --modelo B2C \
  --canal web \
  --zona "Jalisco" \
  --meta 500 \
  --format both \
  --json
```

### `fenix ask`
Modo conversacional: parsea texto libre y arranca el Strategic Discovery Protocol.
```bash
python -m src.skill.cli fenix ask "necesito 100 leads de joyería en GDL" --json
```

### `fenix resume`
Reanuda pipeline interrumpido desde último checkpoint.
```bash
python -m src.skill.cli fenix resume --json
```

### `fenix status`
Estado del job en curso.
```bash
python -m src.skill.cli fenix status --json
```

---

## 🟦 Comandos por agente

### Trend Scout
```bash
python -m src.skill.cli fenix agent trend-scout \
  --nicho "moda femenina" --zona MX --json
```

### Scout
```bash
python -m src.skill.cli fenix agent scout \
  --nicho "joyería" --zona "Jalisco" --limit 1000 --json
```

### Hunter
```bash
python -m src.skill.cli fenix agent hunter \
  --input data/scout_results.json --json
```

### Verifier
```bash
python -m src.skill.cli fenix agent verifier \
  --input data/hunter_results.json --strict --json
```

### Profiler
```bash
python -m src.skill.cli fenix agent profiler \
  --input data/verified.json --enrich-linkedin --json
```

### Dispatcher
```bash
python -m src.skill.cli fenix agent dispatcher \
  --input data/enriched.json \
  --format csv,json \
  --destinations local,hubspot \
  --json
```

### Self-Improver
```bash
python -m src.skill.cli fenix agent self-improver --json
```

---

## 🟨 Comandos por fuente

### DENUE (la única implementada completa por ahora)
```bash
# Cuantificar (no descarga, solo cuenta)
python -m src.skill.cli fenix source denue cuantificar \
  --scian 46 --entidad 09 --estrato 0 --json

# BuscarAreaActEstr (paginado)
python -m src.skill.cli fenix source denue search \
  --entidad 09 --sector 46 --estrato 5,6 --limit 1000 --json

# Por nombre
python -m src.skill.cli fenix source denue nombre \
  --query "MARRIOTT" --entidad 00 --limit 50 --json

# Por coordenadas (radio máx 5km)
python -m src.skill.cli fenix source denue coords \
  --query "restaurante" --lat 19.4326 --lon -99.1332 --radio 1000 --json
```

### Mercado Libre
```bash
python -m src.skill.cli fenix source ml \
  --categoria MLM1430 \
  --limit 500 \
  --solo-oficiales false \
  --json
```

### Google Dorks vía SearXNG
```bash
python -m src.skill.cli fenix source dorks \
  --categoria envios_mx \
  --extra-queries "envíos a toda la república" \
  --limit-per-dork 50 \
  --json
```

### Cámaras MX
```bash
python -m src.skill.cli fenix source camaras \
  --camaras amvo,canacintra,canirac \
  --json
```

### Google Maps
```bash
python -m src.skill.cli fenix source maps \
  --query "joyerías en Guadalajara" \
  --limit 50 \
  --json
```

---

## 🟧 Comandos de base de datos

```bash
# Inicializar SQLite
python -m src.skill.cli fenix db init --json

# Backup completo
python -m src.skill.cli fenix db backup --output backups/fenix_$(date +%Y%m%d).json --json

# Restaurar backup
python -m src.skill.cli fenix db restore backups/fenix_20260527.json --json

# Migrar SQLite → Postgres (cuando crezca)
python -m src.skill.cli fenix db migrate --to postgres --json

# Limpiar leads viejos (LFPDPPP: 90 días sin contacto)
python -m src.skill.cli fenix db purge --older-than 90d --json
```

---

## 🟪 Comandos de empresas y consulta

```bash
# Listar leads
python -m src.skill.cli fenix company list \
  --limit 100 --min-score 70 --bucket COMPLETO --json

# Buscar empresa
python -m src.skill.cli fenix company search "Suburbia" --json

# Detalle de un lead
python -m src.skill.cli fenix company show <lead_id> --json

# Estadísticas globales
python -m src.skill.cli fenix stats db --json

# Stats por fuente
python -m src.skill.cli fenix stats sources --json

# Ranking de fuentes por efectividad
python -m src.skill.cli fenix stats rank --json

# KPIs últimos 30 días
python -m src.skill.cli fenix kpis --period 30d --json
```

---

## 🟫 Comandos de verificación

```bash
# Email - SMTP + dominio + disposable check
python -m src.skill.cli fenix verify email contacto@empresa.com --json

# Teléfono - E.164 + carrier MX
python -m src.skill.cli fenix verify phone "+525512345678" --json

# Dominio - WHOIS + DNS + SSL
python -m src.skill.cli fenix verify domain empresa.com.mx --json

# RFC - regex + SAT (opcional, requiere captcha)
python -m src.skill.cli fenix verify rfc XAXX010101000 --json
```

---

## 🔴 Comandos de export e integración

```bash
# Export CSV v4.0 (26 cols)
python -m src.skill.cli fenix export csv \
  --bucket COMPLETO,SOLO_EMAIL \
  --output output/leads_$(date +%Y%m%d).csv \
  --json

# Export JSON con metadata
python -m src.skill.cli fenix export json \
  --bucket all \
  --output output/leads_$(date +%Y%m%d).json \
  --json

# Export a Google Sheets
python -m src.skill.cli fenix export sheets \
  --input output/leads.csv \
  --sheet "Leads Fénix $(date +%Y-%m-%d)" \
  --json

# Export a HubSpot
python -m src.skill.cli fenix export hubspot \
  --input output/leads.csv \
  --owner "ventas@skydropx.com" \
  --pipeline "Outbound MX" \
  --json
```

---

## ⚫ Comandos batch / enterprise

```bash
# Mega-run (campaña grande con cola Redis)
python -m src.skill.cli fenix mega-run \
  --plan-file plans/semanal_ropa.yaml \
  --concurrency 8 \
  --resume \
  --json

# Batch por archivo de nichos
python -m src.skill.cli fenix batch \
  --nichos-file nichos.txt \
  --meta-por-nicho 100 \
  --json

# Programar campaña en cron
python -m src.skill.cli fenix schedule add \
  --name "ropa-semanal" \
  --cron "0 22 * * 1" \
  --plan plans/semanal_ropa.yaml \
  --json
```

---

## ⚪ Comandos de configuración

```bash
# Ver config actual
python -m src.skill.cli fenix config --json

# Validar políticas de fuentes
python -m src.skill.cli fenix policy check --nicho "X" --sources "denue,dorks" --json

# Ver memoria del Self-Improver
python -m src.skill.cli fenix memory show --table source_stats --json

# Reset memoria (¡cuidado!)
python -m src.skill.cli fenix memory reset --confirm --json
```

---

## Notas de uso

1. **SIEMPRE usar `--json`** para parsing programático.
2. **`--dry-run`** disponible en `run`, `mega-run`, `batch` para validar sin gastar.
3. **`--resume`** funciona en cualquier comando largo gracias a los checkpoints.
4. **Logs** en `logs/fenix.log` (rotación diaria, retención 30 días).
5. **Output** siempre en `output/<nicho>_<fecha>/` con CSV + JSON + reporte HTML.
