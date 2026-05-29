# Catálogo de Google Dorks MX para Skydropx

> Fuente: extraído de los docs "Google Hacking 101", "GoogleDorking" y "Marco 4-D"
> Uso recomendado: vía SearXNG (no Google directo, te banean a las 50 queries).

---

## Operadores base

| Operador | Función | Ejemplo |
|---|---|---|
| `site:` | Dominio específico | `site:linkedin.com/in` |
| `inurl:` | Texto en URL | `inurl:/contacto` |
| `intext:` | Texto en cuerpo | `intext:"envíos a toda la república"` |
| `intitle:` | Texto en `<title>` | `intitle:"tienda en línea"` |
| `filetype:` | Tipo de archivo | `filetype:pdf` |
| `ext:` | Equivalente a filetype | `ext:xlsx` |
| `inanchor:` | Texto en links que apuntan a la página | `inanchor:"comprar"` |
| `related:` | Sitios similares | `related:mercadolibre.com.mx` |
| `cache:` | Versión cacheada de Google | `cache:empresa.com.mx` |
| `before:` / `after:` | Rango de fechas | `after:2025-01-01 before:2026-05-27` |
| `numrange:` | Números en rango | `numrange:500-1000` |
| `OR` / `\|` | OR lógico | `(shopify OR tiendanube)` |
| `-` | Excluir | `-job -empleo` |
| `*` | Comodín | `"mejores * de México"` |
| `~` | Sinónimos | `~comprar` |
| `""` | Frase exacta | `"envíos foráneos"` |

---

## Dorks por industria (LISTOS PARA COPIAR)

### Manufactura
```python
DORKS_MANUFACTURA = [
    'site:linkedin.com/company "manufactura" "México" "director"',
    'filetype:xlsx "proveedores" "Monterrey" OR "CDMX" "RFC"',
    '"empresa manufacturera" "cotización" site:.com.mx',
    'intitle:"directorio" "manufactura" site:.gob.mx',
    'filetype:pdf "padrón" "industria" site:.gob.mx',
]
```

### Logística y 3PL
```python
DORKS_LOGISTICA = [
    'site:linkedin.com/company "logística" "México" "operaciones"',
    '"almacén" "fulfillment" "envíos" site:.com.mx -job -empleo',
    'filetype:pdf "tarifas" "envíos" "paquetería" "México"',
    '"servicios logísticos" "B2B" site:.com.mx',
    '"3PL" OR "fulfillment" "México" site:.com.mx',
]
```

### Moda y textil
```python
DORKS_MODA = [
    'site:linkedin.com "directora comercial" "moda" "México"',
    '"tienda de ropa" "mayoreo" site:.com.mx',
    '"boutique" "colecciones" "catálogo" site:.mx -instagram',
    'inurl:/shop "ropa" "MXN" site:.mx',
    '"diseñadora mexicana" "tienda online" site:.com.mx',
]
```

### Electrónica retail
```python
DORKS_ELECTRONICA = [
    '"distribuidor" "electrónicos" "mayorista" site:.com.mx',
    'site:linkedin.com/company "electrónica" "ventas" "México"',
    'filetype:xlsx "lista de precios" "electrónicos" "2025" OR "2026"',
    '"importador" "componentes electrónicos" site:.com.mx',
]
```

### Salud y farmacia
```python
DORKS_SALUD = [
    '"farmacia" "distribuidora" "mayoreo" site:.com.mx',
    'site:linkedin.com/company "salud" "director médico" "México"',
    '"laboratorio" "distribución" "médico" site:.mx -blog',
    'filetype:pdf "directorio médico" site:.gob.mx',
]
```

### E-commerce (todas las plataformas)
```python
DORKS_ECOMMERCE = [
    # Shopify MX
    '"cdn.shopify.com" site:.mx',
    '"powered by Shopify" "México"',
    'inurl:/products/ "MXN" site:.mx',

    # Tienda Nube
    '"tiendanube.com" site:.mx',
    '"Mi tienda en línea con Tiendanube"',

    # WooCommerce
    'inurl:/?add-to-cart= site:.mx',
    '"wp-content/plugins/woocommerce" site:.mx',

    # Jumpseller
    '"jumpseller.com" site:.mx',

    # VTEX
    '"vtexcommercestable.com.br" site:.mx',
]
```

### Intent de envíos (LA MÁS ALTA CALIDAD para Skydropx)
```python
DORKS_ENVIOS_MX = [
    '"envíos a toda la república" site:.mx',
    '"enviamos a todo México"',
    '"envío gratis en compras mayores"',
    '"paquetería" "todo México" -site:dhl.com -site:fedex.com -site:estafeta.com',
    '"hacemos envíos a toda la república"',
    '"cotiza tu envío" site:.mx',
    '"envíos foráneos" site:.mx',
    '"envíos nacionales" "México" -site:correosdemexico.gob.mx',
]
```

---

## Dorks para CONTACTOS directos

```python
DORKS_CONTACTOS = {
    "emails": [
        '"{empresa}" "@{dominio}" "director" OR "gerente" OR "ventas"',
        'site:{dominio} "contacto" OR "ventas" email',
        '"{empresa}" filetype:pdf "@{dominio}"',
    ],
    "linkedin_empresa": [
        'site:linkedin.com/company "{empresa}"',
        'site:linkedin.com/in "{nombre}" "{empresa}" "México"',
    ],
    "gobierno_datos": [
        'site:datos.gob.mx "{nicho}" padrón directorio',
        'site:inegi.org.mx "{nicho}" estadísticas directorio',
    ],
    "archivos_publicos": [
        'filetype:xlsx "{nicho}" "RFC" "teléfono" "email" site:.gob.mx',
        'filetype:pdf "directorio" "{nicho}" "México" "correo"',
    ],
    "instagram_shops": [
        'site:instagram.com "{nicho}" "envíos" "México" "WhatsApp"',
        'site:instagram.com "tiendamx" "{nicho}"',
    ],
}
```

---

## Dorks para detectar TECNOLOGÍA usada

```python
DORKS_TECH = {
    "shopify_only": '"cdn.shopify.com" site:.mx',
    "tiendanube_only": '"d22fxaf9e50qjs.cloudfront.net" site:.mx',
    "woocommerce_only": '"wp-content/plugins/woocommerce" site:.mx',
    "magento": '"Magento_Catalog" site:.mx',
    "wix_stores": '"wixstores" site:.mx',
    "squarespace": '"squarespace-cdn.com" site:.mx',
    "klaviyo": '"klaviyo.com" site:.mx',           # señal de email mkt activo
    "google_analytics": '"UA-" site:.mx',          # señal de marketing maduro
    "facebook_pixel": '"fbq(\'init\'" site:.mx',   # señal de ads activos
    "tiktok_pixel": '"ttq.load" site:.mx',         # ads en TikTok
}
```

> Empresas con Klaviyo / FB Pixel / TikTok Pixel = ya invierten en mkt → tienen volumen → Skydropx-ready alto.

---

## Combinatorias avanzadas (para usuario EXPERTO)

```python
# Tiendas Shopify MX con envíos a toda la república y pixel de Meta
ADVANCED_DORK_1 = (
    '"cdn.shopify.com" site:.mx '
    '"envíos a toda la república" '
    '"fbq(\'init\'"'
)

# PyMEs medianas con CRM HubSpot (señal de proceso de ventas formal)
ADVANCED_DORK_2 = (
    'site:.mx "hubspot" "ventas B2B" '
    '("PyME" OR "mediana empresa") '
    '"México"'
)

# Empresas con presupuestos publicados (señal: comercio exterior)
ADVANCED_DORK_3 = (
    'filetype:pdf "presupuesto" "exportación" "México" "2026"'
)

# Marketplaces de nicho con vendedores activos
ADVANCED_DORK_4 = (
    '(site:mercadolibre.com.mx OR site:amazon.com.mx) '
    '"vendedor" "{nicho}" "envío gratis"'
)
```

---

## Reglas de uso (importantes)

1. **No usar Google directo** > 50 queries/hora desde la misma IP — te banean. Usar SearXNG con 6+ engines.
2. **Respetar `robots.txt`** del sitio destino antes de scrapear lo encontrado.
3. **Pausa mínima 3s** entre queries (RATE_LIMIT del Hunter).
4. **CoVe obligatorio**: si un dork da un email, validarlo con SMTP antes de exportar.
5. **Dorks de archivos confidenciales** (filetype:xlsx "confidencial" salary) → PROHIBIDO usar para leads. Solo para ejemplos didácticos.
6. **LFPDPPP**: si encuentras datos personales en un PDF público, validar que el documento sea efectivamente público (no leak interno por error).
