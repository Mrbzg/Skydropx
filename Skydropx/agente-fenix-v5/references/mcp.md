# MCP Server — Integración nativa con Claude Code / opencode / Cursor

Fénix v5 expone su funcionalidad como **MCP server** (Model Context Protocol),
el estándar de Anthropic para conectar agentes de IA con herramientas externas.

Implementación: `src/skill/mcp_server.py` — **zero deps** (solo stdlib).

---

## 🧰 Tools expuestas (11)

| Tool | Descripción |
|---|---|
| `fenix_healthcheck` | Estado de infra: token DENUE, search backends, DB |
| `fenix_run` | Pipeline completo (8 agentes, schema v4.0 CSV) |
| `denue_cuantificar` | Cuenta establecimientos sin descargar |
| `denue_search` | Búsqueda DENUE por sector/subsector/clase + entidad |
| `verify_email` | Cascada sintaxis → MX → SMTP opcional |
| `verify_phone` | Google libphonenumber: E.164, tipo, región, can_whatsapp |
| `detect_tech_stack` | Shopify/Tiendanube/Klaviyo/MercadoPago/etc + maturity score |
| `search_dorks` | Búsqueda vía SearchBackendManager tiered |
| `db_stats` | Estadísticas de la DB persistente |
| `db_companies` | Lista filtrada de leads (bucket/score/estado) |
| `harvest_domain` | theHarvester + EmailHarvester unificados |

---

## 🚀 Instalación por runtime

### Claude Code

```bash
claude mcp add fenix -- python3 -m src.skill.mcp_server
```

Configuración explícita en `~/.claude.json`:

```json
{
  "mcpServers": {
    "fenix": {
      "command": "python3",
      "args": ["-m", "src.skill.mcp_server"],
      "cwd": "/ruta/absoluta/a/agente-fenix-v5"
    }
  }
}
```

### opencode

En `~/.config/opencode/opencode.json`:

```json
{
  "mcpServers": {
    "fenix": {
      "command": ["python3", "-m", "src.skill.mcp_server"],
      "cwd": "/ruta/absoluta/a/agente-fenix-v5"
    }
  }
}
```

### Cursor

En `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "fenix": {
      "command": "python3",
      "args": ["-m", "src.skill.mcp_server"],
      "cwd": "/ruta/absoluta/a/agente-fenix-v5"
    }
  }
}
```

### Test manual (sin cliente MCP)

```bash
cd /ruta/agente-fenix-v5
python3 -m src.skill.mcp_server
# pega esto en stdin:
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"manual","version":"1"}}}
{"jsonrpc":"2.0","method":"notifications/initialized"}
{"jsonrpc":"2.0","id":2,"method":"tools/list"}
```

---

## 💬 Ejemplos de uso desde Claude / opencode

Una vez instalado, dile al asistente:

> **"Usa fenix para verificar el teléfono +52 33 1234 5678"**
> → llama `verify_phone` → Claude ve `{e164: +523312345678, type: fijo_o_movil, region: Guadalajara, can_whatsapp: true}`

> **"¿Cuántas zapaterías hay en CDMX según DENUE?"**
> → llama `denue_cuantificar` con actividad=4633, entidad=09

> **"Corre Fénix para 500 leads de ropa en Jalisco modelo D2C"**
> → llama `fenix_run` con nicho=ropa, zona=Jalisco, meta=500, modelo=D2C
> → devuelve CSV v4.0 + stats

> **"Detecta qué tecnologías usa shopify.com.mx"**
> → llama `detect_tech_stack` → ve Shopify, GA, etc + maturity_score

---

## 🛡 Seguridad

- El server **respeta** `RESPECT_ROBOTS_TXT=true` (default)
- No expone credenciales (HUBSPOT_API_KEY, SERPER_API_KEY) en las responses
- Los `traceback` solo aparecen en `isError: true` (no en flujo normal)
- Stderr es para logs, stdout solo JSON-RPC (no contaminar)

---

## 🐛 Troubleshooting

| Problema | Solución |
|---|---|
| "DENUE_TOKEN no configurado" desde Claude Code | Verifica que el `cwd` apunte al directorio que contiene `.env` |
| Respuestas tardan mucho | Sube `enrich_max` por default o usa `mode: quick` |
| "Tool desconocida" | Pide `tools/list` para ver las 11 disponibles |
| Logs en stdout (rompe JSON-RPC) | Verifica que tu fork no haya cambiado `stream=sys.stderr` |

---

## 🔄 Diferencias vs CLI directo

| Caso | Usa CLI | Usa MCP |
|---|---|---|
| Script bash / cron | ✓ | ✗ |
| Conversación en Claude Code | ✗ | ✓ |
| CI/CD pipeline | ✓ | ✗ |
| Automatización agentic | ✗ | ✓ |
| Test ad-hoc rápido | ✓ | ✗ |

Ambos comparten el mismo `pipeline.py` por debajo.
