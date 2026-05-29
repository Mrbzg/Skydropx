# Deep Enrich — OSINT profundo selectivo

> **Filosofía:** las herramientas OSINT externas (Holehe, Maigret, pagodo, PhoneInfoga,
> SpiderFoot) son **lentas y propensas a bloqueos**. Aplicarlas a 100K leads sería
> catastrófico (~833 horas + bans masivos). Por eso Fénix las aplica **solo a leads
> ya filtrados** (bucket=COMPLETO/PARCIAL), después del scoring.

---

## 🎯 Cuándo se ejecuta

El `agent_deep_enrich` se ejecuta DESPUÉS del `agent_profiler`, SOLO si pasas
`--deep-enrich-max > 0` al CLI o `deep_enrich_max` al MCP `fenix_run`.

```
trend_scout → scout → hunter → verifier → persist → profiler
                                                       ↓
                                              deep_enrich (opcional)
                                                       ↓
                                              dispatcher → self_improver
```

**Default:** `deep_enrich_max=0` → se omite (es opcional para no ralentizar runs normales).

---

## 🛠 Las 4 herramientas integradas

| Tool | Hace | Tiempo por lead | Útil para Skydropx |
|---|---|---|---|
| **Holehe** | Verifica email en 100+ sitios | ~5-15s | Confirma email es persona ACTIVA (vs buzón muerto) |
| **Maigret** | Busca username en 3000+ sitios | ~30-120s | Encuentra LinkedIn/IG/Twitter del decisor B2B |
| **pagodo** | Aplica dorks de GHDB sobre dominio | ~30-60s | Descubre PDFs/Excel públicos con leads adicionales |
| **PhoneInfoga** | OSINT sobre número | ~5-10s | Complementa carrier/operador a `phonenumbers` |

**Plus opcional:** SpiderFoot wrapper para casos de "investiga A FONDO esta empresa Enterprise" (ej: cuenta clave que vale 1 hora de cómputo).

---

## 💰 Budget compartido (anti-ban)

Cada herramienta tiene cuotas configuradas en `src/core/budget.py`:

| Tool | Por hora | Por día | Quarantine |
|---|---|---|---|
| holehe | 100 | 500 | 10 min tras 5 fails seguidos |
| maigret | 30 | 150 | igual |
| pagodo | 30 | 200 | igual |
| phoneinfoga | 100 | 500 | igual |
| spiderfoot | 10 | 50 | igual |
| sherlock | 50 | 300 | igual |
| h8mail | 30 | 100 | igual |

**Persistencia:** estado en `data/budgets.json`, sobrevive reinicios.

```bash
# Ver presupuesto actual
python3 -m src.skill.cli fenix budget
```

---

## ⚡ Instalación (todo opcional)

```bash
# Holehe (recomendado, el más útil para Skydropx)
pip install holehe

# Maigret (perfila a personas en 3000 sitios)
pip install maigret

# pagodo (Google Hacking Database automatizado)
pip install pagodo

# PhoneInfoga (binario Go, descarga release de GitHub)
# https://github.com/sundowndev/phoneinfoga/releases

# SpiderFoot (clonar repo)
git clone https://github.com/smicallef/spiderfoot
cd spiderfoot && pip install -r requirements.txt
```

Si NO instalas alguna, el agente la omite silenciosamente:

```json
{
  "tools_available": {
    "holehe": true,
    "maigret": false,
    "pagodo": false,
    ...
  }
}
```

---

## 🚀 Uso

### CLI

```bash
# Ver herramientas instaladas
python3 -m src.skill.cli fenix osint stats

# Probar una herramienta individual
python3 -m src.skill.cli fenix osint holehe --target persona@empresa.com.mx
python3 -m src.skill.cli fenix osint maigret --target juangarcia
python3 -m src.skill.cli fenix osint phoneinfoga --target +525512345678
python3 -m src.skill.cli fenix osint pagodo --target empresa.com.mx --max-dorks 10
python3 -m src.skill.cli fenix osint spiderfoot --target empresa.com.mx

# Pipeline completo con deep enrich activado (top 100 leads READY)
python3 -m src.skill.cli fenix run \
  --nicho "ropa" --zona CDMX --meta 1000 \
  --deep-enrich-max 100 \
  --deep-enrich-tools "holehe,maigret,phoneinfoga"
```

### MCP

Desde Claude Code / opencode:

> *"Verifica si juan@empresa.mx es persona activa"* → llama `osint_holehe`
> *"Busca a Juan García en redes"* → llama `osint_maigret`
> *"OSINT sobre +525512345678"* → llama `osint_phoneinfoga`
> *"Estado de cuotas OSINT"* → llama `osint_budget`

---

## 📊 Qué metadata se persiste por lead

Tras deep_enrich, cada `Company` en la DB recibe en `metadata_json`:

```json
{
  "holehe_sites": ["amazon.com", "twitter.com", "spotify.com"],
  "holehe_is_active_persona": true,
  "maigret_top_profiles": {
    "linkedin": "https://linkedin.com/in/juangarcia",
    "instagram": "https://instagram.com/juangarcia"
  },
  "phoneinfoga_carrier": "Telcel",
  "pagodo_urls": ["https://empresa.com.mx/pdf/catalogo.pdf", "..."]
}
```

Si Maigret encuentra LinkedIn, se copia directamente al campo `linkedin` del Lead
(para que aparezca en el CSV v4.0).

---

## 🚦 Cuándo NO usar deep_enrich

- Cuando el universo es >10K leads (toma horas → considera mover a un cron nocturno separado)
- Cuando no necesitas confirmar persona activa (ej: solo te interesan empresas como tal)
- Cuando los presupuestos están agotados (`fenix budget` mostrará `quarantined`)
- Cuando un cliente pidió opt-out (LFPDPPP: respetar)

---

## 🔄 Alternativas que descartamos (a propósito)

| Tool | Por qué NO la integramos |
|---|---|
| **SpiderFoot completo** | Es un framework standalone con su propia DB/UI/scheduler — duplicaría funcionalidad. Solo lo exponemos como wrapper mínimo CLI |
| **Recon-ng** | Workspace model interactivo (REPL), no apto para automatización agentic |
| **sn0int** | Binario Rust con DB SQLite propia — 3ra DB en el stack (overkill) |
| **h8mail** | Verificación de brechas (HIBP) — éticamente delicado bajo LFPDPPP MX. Disponible como wrapper pero NO se usa por default |
| **Sherlock** | Maigret lo reemplaza (es su evolución con 6x más sitios) |
