# Fuentes de datos — Fénix v5

Las 12 fuentes priorizadas, todas gratuitas, ordenadas por confiabilidad.

| # | Fuente | Tipo | Confiabilidad | Rate limit | Yield/mes |
|---|---|---|---|---|---|
| 1 | DENUE/INEGI | API oficial | 0.95 | ~100 req/min | 80-150K |
| 2 | Google Dorks vía SearXNG | Search | 0.85 | depende de engines | 30-50K |
| 3 | Mercado Libre API | API pública | 0.85 | 1K req/h sin auth | 30-50K |
| 4 | Cámaras MX (AMVO/Canacintra/...) | Scrapers | 0.90 | manual | 5-10K |
| 5 | theHarvester | OSINT | 0.80 | sin límite | enrichment |
| 6 | Photon / Scrapy | Crawler | 0.75 | config | enrichment |
| 7 | SpiderFoot | Framework | 0.85 | sin límite | enrichment |
| 8 | CrossLinked | LinkedIn (sin login) | 0.70 | 5 req/min | enrichment |
| 9 | EmailFinder | Email patterns | 0.75 | sin límite | enrichment |
| 10 | h8mail | Email existence | 0.60 | sin límite | verify |
| 11 | PhoneInfoga | Teléfono OSINT | 0.70 | sin límite | verify |
| 12 | Google Maps scraper | Maps | 0.80 | 10 req/min | 10-20K |

---

## 1. DENUE / INEGI — la fuente principal

**Status:** ✓ implementada en `src/sources/denue_source.py` y probada con token real.

**URL base:** `https://www.inegi.org.mx/app/api/denue/v1/consulta/`

**Token:** gratuito en https://www.inegi.org.mx/app/api/denue/v1/tutorial.html

**Endpoints disponibles:**

| Método | URL | Uso |
|---|---|---|
| `Cuantificar` | `/Cuantificar/{actividad}/{area}/{estrato}/{token}` | Conteo previo (sin gastar req) |
| `BuscarAreaActEstr` | `/BuscarAreaActEstr/{15 params}/{token}` | El más usado, máx flexibilidad |
| `BuscarEntidad` | `/BuscarEntidad/{condicion}/{entidad}/{ini}/{fin}/{token}` | Búsqueda simple por palabra |
| `Nombre` | `/Nombre/{nombre}/{entidad}/{ini}/{fin}/{token}` | Por razón social |
| `Ficha` | `/Ficha/{id}/{token}` | Detalle de un establecimiento |
| `Buscar` | `/Buscar/{condicion}/{lat,lon}/{radio_m}/{token}` | Geográfica (radio máx 5km) |

**Campos que regresa:**

```
CLEE, Id, Nombre, Razon_social, Clase_actividad, Estrato,
Tipo_vialidad, Calle, Num_Exterior, Num_Interior, Colonia, CP,
Ubicacion, Telefono, Correo_e, Sitio_internet, Tipo,
Longitud, Latitud, tipo_corredor_industrial, nom_corredor_industrial,
numero_local, AGEB, Manzana, CLASE_ACTIVIDAD_ID,
SECTOR_ACTIVIDAD_ID, SUBSECTOR_ACTIVIDAD_ID, RAMA_ACTIVIDAD_ID,
SUBRAMA_ACTIVIDAD_ID, EDIFICIO, Tipo_Asentamiento, Fecha_Alta, AreaGeo
```

**Volúmenes reales medidos (mayo 2026):**

| Sector | SCIAN | MX total | CDMX | Jalisco | NL |
|---|---|---|---|---|---|
| Comercio al menor | 46 | ~2.4M | 212,251 | 168,388 | 127,532 |
| Restaurantes | 722 | 778,198 | 57,168 | 51,531 | 34,784 |
| Ropa al menor | 4632 | ~120K | ~20K | ~15K | ~10K |
| Calzado al menor | 4633 | ~45K | ~5K | ~6K | ~3K |

**Mejores prácticas:**
- Usar `Cuantificar` antes de descargar masivo (te dice cuánto vas a traer)
- Iterar paginado de 100 en 100 con `BuscarAreaActEstr`
- Filtrar por `estrato` para targetear tamaño (1-2=Micro, 5-6=Mediana, 7=Grande)
- DENUE NO tiene WhatsApp ni redes sociales — eso requiere enrichment web

---

## 2. Mercado Libre API

**Status:** propuesto en `src/sources/mercadolibre_source.py` (v1 hecho, pendiente migrar a v5).

**URL base:** `https://api.mercadolibre.com` (sin auth para búsqueda)

**Endpoints clave:**
```
GET /sites/MLM/search?category={cat_id}&offset={n}&limit=50
GET /users/{seller_id}
GET /users/{seller_id}/items/search
```

**Categorías top MX:**
- `MLM1430` Ropa, Bolsas y Calzado
- `MLM1276` Deportes y Fitness
- `MLM1574` Hogar, Muebles y Jardín
- `MLM1648` Computación
- `MLM1132` Juegos y Juguetes
- `MLM3937` Belleza y Cuidado Personal

**Rate limit:** 1K req/h sin auth, 10K/h con OAuth gratis.

---

## 3. Cámaras MX (padrones públicos)

**Status:** propuesto en `src/sources/camaras_mx_source.py` (v1 hecho).

| Cámara | URL directorio | Volumen | Categoría |
|---|---|---|---|
| AMVO | amvo.org.mx/asociados/ | ~600 | E-commerce 100% (PREMIUM) |
| ANTAD | antad.net/asociados/ | ~110 | Retail grande |
| Canacintra | canacintra.org.mx/directorio/ | ~12K | Industrial |
| Coparmex | coparmex.org.mx/ (por estado) | ~36K | Empresarios formales |
| COMCE | comce.org.mx/directorio/ | ~3.5K | Comercio exterior |
| CANIRAC | canirac.org.mx | ~25K | Restaurantes |
| CANIETI | canieti.org/ | ~700 | Tecnología |
| AMIPCI | amipci.org.mx/ | ~300 | Internet/digital |

**Total potencial:** ~78K leads cámara-validados (calidad altísima).

---

## 4. Google Dorks vía SearXNG

**Status:** propuesto en `src/sources/ecommerce_dorks_source.py` (v1 hecho).

**Por qué SearXNG:** Google directo te banea a los 50 dorks. SearXNG agrega 6+ engines (Google, Bing, Brave, DuckDuckGo, Startpage, Qwant) y rota internamente.

**Dorks completos:** ver `references/dorks.md`.

**Setup:**
```bash
docker compose up -d searxng
# verificar
curl 'http://localhost:8888/search?q=test&format=json'
```

---

## 5-11. Herramientas OSINT clásicas (para enrichment)

Estas no traen empresas — enriquecen las que ya descubrió DENUE/Dorks/ML.

| Herramienta | Instalación | Uso |
|---|---|---|
| `theHarvester` | `pip install theHarvester` | `theHarvester -d empresa.com -b all -l 500` |
| `Photon` | `git clone github.com/s0md3v/Photon` | `photon -u empresa.com.mx -o output/` |
| `SpiderFoot` | `pip install spiderfoot` | `sf.py -s empresa.com -t EMAILADDR,PHONE_NUMBER` |
| `CrossLinked` | `pip install crosslinked` | `crosslinked -f "{f}.{l}@empresa.com" "Empresa"` |
| `EmailFinder` | `pip install emailfinder` | `emailfinder -d empresa.com` |
| `h8mail` | `pip install h8mail` | `h8mail -t mail@empresa.com --local-breach` |
| `PhoneInfoga` | `go install github.com/sundowndev/phoneinfoga` | `phoneinfoga scan -n "+525512345678"` |

---

## 12. Google Maps scraper

**Status:** pendiente. Plan: usar `googlemaps-scraper` (npm) o Playwright.

**Volumen:** comparable a DENUE pero con WhatsApp/redes sociales más frecuente.

**Rate limit:** 10 req/min para no detección.

---

## Selección de fuentes por modo

| Modo | Fuentes activas |
|---|---|
| QUICK (≤50 leads) | DENUE solo |
| STANDARD (51-1K) | DENUE + Cámaras MX |
| DEEP (1K-10K) | DENUE + Cámaras + Mercado Libre + Dorks SearXNG |
| ENTERPRISE (10K+) | TODAS + enrichment paralelo |

---

## Compliance y ética

- ✓ **DENUE/INEGI**: 100% público, datos formales declarados al INEGI.
- ✓ **Mercado Libre API**: TOS permiten lectura programática para uso legítimo.
- ✓ **Cámaras MX**: directorios públicos, los socios consintieron al afiliarse.
- ✓ **Google Dorks**: solo páginas indexadas públicamente.
- ⚠ **LinkedIn**: máximo 3 req/min, sin login, sin scraping de perfiles privados.
- ❌ **NO usar**: directorios filtrados, leaks ilegales, BD vendidas.
- ✓ **LFPDPPP**: todos los datos retenidos son los mínimos necesarios y se purgan a los 90 días si no hay contacto comercial.
