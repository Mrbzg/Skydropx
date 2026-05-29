"""
Trends Detector — qué está en tendencia AHORA mismo en México.

Fuentes integradas (todas opcionales con graceful degradation):
1. Google Trends MX  vía pytrends (oficial-ish, no requiere API key)
2. Mercado Libre Trends  → https://www.mercadolibre.com.mx/trends/MLM (scraping HTML)
3. TikTok hashtags trending  → vía SearXNG/dorks
4. Amazon MX Best Sellers  → scraping de /gp/bestsellers
5. Catálogo local de eventos MX (en `data/eventos_mx.json`)

Cada fuente devuelve TrendItem(name, score, category, source, ts).
El manager unifica y rankea por relevancia (recencia + cross-source confirmation).

Para Skydropx: detectar nichos en crecimiento → enfocar campañas outbound ahí.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from src.core.config import settings
from src.core.user_agents import random_ua

logger = logging.getLogger(__name__)

# Detección de pytrends
try:
    from pytrends.request import TrendReq
    HAS_PYTRENDS = True
except ImportError:
    HAS_PYTRENDS = False
    TrendReq = None  # noqa: N816


CACHE_PATH = Path("data/trends_cache.json")
CACHE_TTL_HOURS = 6


# ---------------- Modelo ----------------

@dataclass
class TrendItem:
    name: str
    source: str               # 'google_trends' | 'mercadolibre' | 'tiktok' | 'amazon' | 'evento_mx'
    score: int = 50           # 0-100, normalizado
    category: str | None = None
    related_queries: list[str] = field(default_factory=list)
    related_categories: list[str] = field(default_factory=list)
    ts_detected: str = field(default_factory=lambda: datetime.now().isoformat())
    url: str | None = None
    extras: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------- Cache ----------------

def _cache_load() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _cache_save(data: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2,
                                       default=str), encoding="utf-8")


def _cache_get(source: str) -> list[TrendItem] | None:
    data = _cache_load()
    entry = data.get(source)
    if not entry:
        return None
    ts = datetime.fromisoformat(entry.get("ts", "1970-01-01"))
    if datetime.now() - ts > timedelta(hours=CACHE_TTL_HOURS):
        return None
    return [TrendItem(**item) for item in entry.get("items", [])]


def _cache_set(source: str, items: list[TrendItem]) -> None:
    data = _cache_load()
    data[source] = {
        "ts": datetime.now().isoformat(),
        "items": [it.to_dict() for it in items],
    }
    _cache_save(data)


# ---------------- 1. Google Trends ----------------

GOOGLE_TRENDS_RSS_MX = "https://trends.google.com/trending/rss?geo=MX"


def get_google_trends_mx(use_cache: bool = True) -> list[TrendItem]:
    """
    Trending searches diarias en México.
    Usa el RSS oficial (más estable que la API pytrends que cambia frecuente).
    """
    if use_cache:
        cached = _cache_get("google_trends")
        if cached:
            logger.info("Google Trends desde caché (%s items)", len(cached))
            return cached

    items: list[TrendItem] = []
    try:
        r = requests.get(GOOGLE_TRENDS_RSS_MX, timeout=15,
                          headers={"User-Agent": random_ua(),
                                   "Accept": "application/rss+xml,text/xml"})
        if not r.ok:
            logger.warning("Google Trends RSS HTTP %s", r.status_code)
            return _try_pytrends_fallback()

        # Parsear RSS con regex (zero deps)
        # Cada <item> tiene: <title>, <ht:approx_traffic>, <ht:news_item_title>, etc.
        xml = r.text
        # Cortar el header del canal
        items_xml = re.findall(r"<item>(.*?)</item>", xml, re.S)

        for rank, item_xml in enumerate(items_xml[:25], 1):
            title_m = re.search(r"<title>([^<]+)</title>", item_xml)
            traffic_m = re.search(r"<ht:approx_traffic>([^<]+)</ht:approx_traffic>", item_xml)
            news_items = re.findall(
                r"<ht:news_item_title>([^<]+)</ht:news_item_title>", item_xml,
            )
            picture_m = re.search(r"<ht:picture>([^<]+)</ht:picture>", item_xml)

            if not title_m:
                continue
            name = title_m.group(1).strip()

            # Score basado en rank + traffic
            base_score = max(100 - rank * 3, 25)
            if traffic_m:
                traffic_str = traffic_m.group(1).strip().lower()
                # ej "50K+", "100K+", "1M+"
                if "1m" in traffic_str or "2m" in traffic_str or "5m" in traffic_str:
                    base_score = min(base_score + 15, 100)
                elif "500k" in traffic_str or "200k" in traffic_str:
                    base_score = min(base_score + 10, 100)
                elif "100k" in traffic_str:
                    base_score = min(base_score + 5, 100)

            items.append(TrendItem(
                name=name, source="google_trends",
                score=base_score,
                category=None,
                related_queries=news_items[:5],
                url=f"https://www.google.com/search?q={requests.utils.quote(name)}",
                extras={
                    "rank": rank,
                    "approx_traffic": traffic_m.group(1).strip() if traffic_m else "",
                    "picture": picture_m.group(1).strip() if picture_m else "",
                    "fecha_rss": datetime.now().isoformat(),
                },
            ))
    except Exception as e:  # noqa: BLE001
        logger.warning("Google Trends RSS err: %s", e)
        return _try_pytrends_fallback()

    if items:
        _cache_set("google_trends", items)
    return items


def _try_pytrends_fallback() -> list[TrendItem]:
    """Fallback a pytrends si RSS falla."""
    if not HAS_PYTRENDS:
        return []
    items = []
    try:
        pytrends = TrendReq(hl="es-MX", tz=360, timeout=(5, 15))
        df = pytrends.trending_searches(pn="mexico")
        for i, row in enumerate(df.itertuples(index=False), 1):
            name = str(row[0])
            items.append(TrendItem(name=name, source="google_trends",
                                     score=max(100 - i * 4, 20)))
    except Exception:  # noqa: BLE001
        pass
    return items


def get_google_trends_related(keyword: str, geo: str = "MX",
                                timeframe: str = "today 3-m") -> dict:
    """Top + rising related queries para un keyword (útil para descubrir sub-nichos)."""
    if not HAS_PYTRENDS:
        return {"available": False}
    try:
        pytrends = TrendReq(hl="es-MX", tz=360, timeout=(5, 15))
        pytrends.build_payload([keyword], timeframe=timeframe, geo=geo)
        related = pytrends.related_queries()
        rq = related.get(keyword) or {}
        top = rq.get("top")
        rising = rq.get("rising")
        return {
            "available": True,
            "keyword": keyword,
            "top": top.to_dict("records") if top is not None else [],
            "rising": rising.to_dict("records") if rising is not None else [],
        }
    except Exception as e:  # noqa: BLE001
        return {"available": False, "error": str(e)}


# ---------------- 2. Mercado Libre Trends ----------------

ML_TRENDS_URL = "https://www.mercadolibre.com.mx/trends/MLM"


def get_mercadolibre_trends(use_cache: bool = True) -> list[TrendItem]:
    """Scraping de la página pública de trends ML México."""
    if use_cache:
        cached = _cache_get("mercadolibre")
        if cached:
            return cached
    items: list[TrendItem] = []
    try:
        r = requests.get(ML_TRENDS_URL, timeout=15,
                          headers={"User-Agent": random_ua(),
                                   "Accept-Language": "es-MX,es;q=0.9"})
        if not r.ok:
            logger.warning("ML Trends HTTP %s", r.status_code)
            return []
        # ML Trends HTML: lista de trends en <a class="trend_card"> o similar
        # Usamos varios selectores robustos via regex
        html = r.text
        seen: set[str] = set()
        # Capturar texto de elementos comunes
        # Patrón 1: items en lista con texto principal
        for m in re.finditer(
            r'<(?:a|div|span)[^>]+class="[^"]*trend[^"]*"[^>]*>([^<]{3,80})</',
            html, re.I,
        ):
            name = m.group(1).strip()
            if name and name not in seen and len(name) >= 3:
                seen.add(name)
                items.append(TrendItem(
                    name=name, source="mercadolibre",
                    score=max(95 - len(items) * 3, 25),
                    category="ecommerce",
                ))
            if len(items) >= 30:
                break

        # Patrón 2: si el HTML es SPA, intentar capturar de scripts JSON
        if not items:
            for m in re.finditer(r'"name":\s*"([^"]{3,80})"', html):
                name = m.group(1).strip()
                if name and name not in seen and "tendencia" not in name.lower():
                    seen.add(name)
                    items.append(TrendItem(
                        name=name, source="mercadolibre",
                        score=max(90 - len(items) * 3, 25),
                        category="ecommerce",
                    ))
                if len(items) >= 20:
                    break
    except Exception as e:  # noqa: BLE001
        logger.warning("ML Trends err: %s", e)
        return []
    if items:
        _cache_set("mercadolibre", items)
    return items


# ---------------- 3. TikTok Trends (vía SearXNG/dorks) ----------------

def get_tiktok_trends_mx(use_cache: bool = True, limit: int = 20) -> list[TrendItem]:
    """
    Detecta hashtags trending MX vía búsquedas con SearXNG.
    NOTA: TikTok no tiene API pública estable; usamos heurística vía SearXNG.
    """
    if use_cache:
        cached = _cache_get("tiktok")
        if cached:
            return cached
    items: list[TrendItem] = []
    try:
        from src.sources.search_backends import get_default_manager
        mgr = get_default_manager()
        queries = [
            'site:tiktok.com "trending" "México"',
            'site:tiktok.com mexico "viral"',
            'site:tiktok.com "#tendencia"',
            'tiktok mexico tendencias 2026',
        ]
        seen_hashtags: set[str] = set()
        for q in queries:
            try:
                results = mgr.search(q, limit=20, country="mx", avoid_paid=True)
            except Exception:  # noqa: BLE001
                continue
            for r in results:
                text = (r.title or "") + " " + (r.snippet or "")
                for tag in re.findall(r"#(\w{3,30})", text):
                    tag_low = tag.lower()
                    if tag_low not in seen_hashtags and tag_low not in (
                        "viral", "fyp", "parati", "trending", "tendencia",
                        "tiktok", "tiktokmexico",
                    ):
                        seen_hashtags.add(tag_low)
                        items.append(TrendItem(
                            name=f"#{tag}", source="tiktok",
                            score=max(80 - len(items) * 4, 20),
                            category="social",
                        ))
                if len(items) >= limit:
                    break
            if len(items) >= limit:
                break
    except Exception as e:  # noqa: BLE001
        logger.warning("TikTok trends err: %s", e)
        return []
    if items:
        _cache_set("tiktok", items)
    return items


# ---------------- 4. Amazon MX Best Sellers ----------------

AMAZON_BESTSELLERS = "https://www.amazon.com.mx/gp/bestsellers"


def get_amazon_bestsellers_mx(use_cache: bool = True,
                                limit: int = 20) -> list[TrendItem]:
    """Scraping de Amazon MX best sellers (top general)."""
    if use_cache:
        cached = _cache_get("amazon")
        if cached:
            return cached
    items: list[TrendItem] = []
    try:
        r = requests.get(AMAZON_BESTSELLERS, timeout=15,
                          headers={"User-Agent": random_ua(),
                                   "Accept-Language": "es-MX,es;q=0.9"})
        if not r.ok:
            return []
        # Amazon usa data-attributes en sus cards
        seen: set[str] = set()
        # Patrón: nombres de productos en aria-labels o headings
        for m in re.finditer(
            r'<div[^>]+class="[^"]*p13n-[^"]*"[^>]*>.*?<a[^>]*>([^<]{10,150})</a>',
            r.text, re.S | re.I,
        ):
            name = re.sub(r"\s+", " ", m.group(1)).strip()
            if name and name not in seen:
                seen.add(name)
                items.append(TrendItem(
                    name=name[:100], source="amazon",
                    score=max(95 - len(items) * 4, 20),
                    category="ecommerce",
                ))
            if len(items) >= limit:
                break
    except Exception as e:  # noqa: BLE001
        logger.warning("Amazon bestsellers err: %s", e)
        return []
    if items:
        _cache_set("amazon", items)
    return items




# ---------------- 5. Wikipedia Pageviews ES (proxy de interés cultural MX) ----------------

WIKI_API = "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/es.wikipedia/all-access"


def get_wikipedia_trends_es(use_cache: bool = True, limit: int = 30) -> list[TrendItem]:
    """
    Top artículos más vistos AYER en Wikipedia ES.
    Proxy fuerte de interés cultural MX (es la WP en español más consultada).
    Útil para detectar personas/eventos/marcas en tendencia que dispararon búsquedas.
    """
    if use_cache:
        cached = _cache_get("wikipedia_es")
        if cached:
            return cached
    items: list[TrendItem] = []
    try:
        from datetime import date, timedelta
        # Wikipedia tiene delay ~24h en stats; usar ayer
        ayer = date.today() - timedelta(days=1)
        url = f"{WIKI_API}/{ayer.year}/{ayer.month:02d}/{ayer.day:02d}"
        r = requests.get(url, timeout=15,
                          headers={"User-Agent": "AgenteFenix/5.0 (research)"})
        if not r.ok:
            return []
        data = r.json()
        articles = data.get("items", [{}])[0].get("articles", [])
        # Filtrar páginas especiales/portadas
        skip_prefixes = ("Especial:", "Wikipedia:", "Categoría:", "Plantilla:", "Anexo:")
        for a in articles:
            name = a.get("article", "").replace("_", " ")
            views = a.get("views", 0)
            if any(name.startswith(p) for p in skip_prefixes):
                continue
            if not name or len(name) < 3:
                continue
            # Score normalizado: top1 = 100, decae logaritmicamente
            rank = len(items) + 1
            score = max(int(100 - rank * 2.5), 25)
            items.append(TrendItem(
                name=name, source="wikipedia_es",
                score=score,
                category="cultural",
                url=f"https://es.wikipedia.org/wiki/{name.replace(' ', '_')}",
                extras={"views_ayer": views, "rank": rank,
                          "fecha_data": ayer.isoformat()},
            ))
            if len(items) >= limit:
                break
    except Exception as e:  # noqa: BLE001
        logger.warning("Wikipedia trends err: %s", e)
        return []
    if items:
        _cache_set("wikipedia_es", items)
    return items


# ---------------- Unificación + Ranking ----------------

@dataclass
class TrendsReport:
    fecha: str
    total_items: int
    by_source: dict[str, int]
    top_overall: list[TrendItem]
    top_ecommerce: list[TrendItem]
    top_recientes: list[TrendItem]
    cross_confirmed: list[TrendItem]  # aparecen en >1 source
    raw_by_source: dict[str, list[TrendItem]]

    def to_dict(self) -> dict:
        return {
            "fecha": self.fecha,
            "total_items": self.total_items,
            "by_source": self.by_source,
            "top_overall": [t.to_dict() for t in self.top_overall],
            "top_ecommerce": [t.to_dict() for t in self.top_ecommerce],
            "top_recientes": [t.to_dict() for t in self.top_recientes],
            "cross_confirmed": [t.to_dict() for t in self.cross_confirmed],
        }


def get_all_trends(use_cache: bool = True,
                    sources: list[str] | None = None) -> TrendsReport:
    """
    Detecta tendencias de TODAS las fuentes disponibles.

    Args:
        sources: lista opcional ['google','mercadolibre','tiktok','amazon'].
                 Default: las 4.
    """
    sources = sources or ["google", "mercadolibre", "tiktok", "amazon", "wikipedia"]
    raw: dict[str, list[TrendItem]] = {}

    if "google" in sources:
        raw["google_trends"] = get_google_trends_mx(use_cache=use_cache)
    if "mercadolibre" in sources:
        raw["mercadolibre"] = get_mercadolibre_trends(use_cache=use_cache)
    if "tiktok" in sources:
        raw["tiktok"] = get_tiktok_trends_mx(use_cache=use_cache)
    if "amazon" in sources:
        raw["amazon"] = get_amazon_bestsellers_mx(use_cache=use_cache)
    if "wikipedia" in sources or "wiki" in sources:
        raw["wikipedia_es"] = get_wikipedia_trends_es(use_cache=use_cache)

    all_items: list[TrendItem] = []
    for src, items in raw.items():
        all_items.extend(items)

    # Cross-source confirmation: trend que aparece (por nombre normalizado) en >1 source
    def _norm(s: str) -> str:
        return re.sub(r"[^\w]", "", s.lower().strip())[:25]

    name_to_sources: dict[str, set[str]] = {}
    for it in all_items:
        key = _norm(it.name)
        if key:
            name_to_sources.setdefault(key, set()).add(it.source)

    cross_confirmed = []
    seen_cross: set[str] = set()
    for it in all_items:
        k = _norm(it.name)
        if len(name_to_sources.get(k, set())) > 1 and k not in seen_cross:
            seen_cross.add(k)
            it.extras["cross_sources"] = list(name_to_sources[k])
            cross_confirmed.append(it)

    # Top overall: sort por score, eliminar duplicados de nombre
    seen_names: set[str] = set()
    top_overall = []
    for it in sorted(all_items, key=lambda x: -x.score):
        k = _norm(it.name)
        if k and k not in seen_names:
            seen_names.add(k)
            top_overall.append(it)
        if len(top_overall) >= 30:
            break

    top_ecommerce = [it for it in top_overall if it.category == "ecommerce"][:15]
    top_recientes = sorted(all_items,
                            key=lambda x: x.ts_detected, reverse=True)[:15]

    return TrendsReport(
        fecha=datetime.now().isoformat(),
        total_items=len(all_items),
        by_source={src: len(items) for src, items in raw.items()},
        top_overall=top_overall,
        top_ecommerce=top_ecommerce,
        top_recientes=top_recientes,
        cross_confirmed=cross_confirmed[:10],
        raw_by_source=raw,
    )


# ---------------- Mapeo Trend → Nicho conocido ----------------

# Mapeo semántico: si el trend contiene estas palabras, sugerir estos nichos
# Esto cubre casos donde el trend NO es un nicho directo (ej: "Mundial" → deportes)
SEMANTIC_TREND_TO_NICHOS = {
    # Eventos deportivos → deportes + ropa deportiva + bebidas
    "mundial": ["deportes", "ropa"],
    "fifa": ["deportes", "ropa"],
    "world cup": ["deportes", "ropa"],
    "futbol": ["deportes"],
    "football": ["deportes"],
    "champions": ["deportes"],
    "olimpiadas": ["deportes"],
    "super bowl": ["deportes"],
    "nfl": ["deportes"],
    "nba": ["deportes"],
    # Música / espectáculos → ropa, accesorios, joyería
    "concierto": ["ropa", "joyeria"],
    "festival": ["ropa", "joyeria"],
    "eticket": ["ropa", "joyeria"],  # boletos => evento => gente compra outfits
    "boletos": ["ropa"],
    # Belleza / influencers → belleza, ropa
    "maquillaje": ["belleza"],
    "skincare": ["belleza"],
    "cosmetic": ["belleza"],
    # Tecnología
    "iphone": ["electronica"],
    "smartphone": ["electronica"],
    "gaming": ["electronica", "juguetes"],
    "ps5": ["electronica"],
    "xbox": ["electronica"],
    # Fechas estacionales (cuando aparecen como trend)
    "navidad": ["juguetes", "ropa", "belleza", "electronica"],
    "valentin": ["joyeria", "belleza"],
    "madres": ["joyeria", "belleza", "ropa"],
    "buen fin": ["ropa", "calzado", "electronica", "muebles"],
    "black friday": ["electronica", "ropa"],
    "hot sale": ["ropa", "calzado", "electronica"],
}


def suggest_niches_from_trends(report: TrendsReport,
                                 max_niches: int = 8) -> list[dict]:
    """
    A partir de los trends, sugiere nichos del catálogo Fénix para correr pipeline.

    Estrategia jerárquica:
      1. Match exact alias del nicho en el trend (ej: "moda casual" → ropa)
      2. Match semántico (ej: "mundial 2026" → deportes + ropa via tabla)
      3. Si nada matchea, deja al usuario elegir libre del catálogo
    """
    nicho_path = Path(__file__).resolve().parents[2] / "data" / "nicho_scian.json"
    if not nicho_path.exists():
        return []
    catalog = json.loads(nicho_path.read_text(encoding="utf-8")).get("nichos", {})
    suggestions: list[dict] = []
    seen: set[str] = set()

    def _add(nicho_key, trend, match_type, match_reason):
        if nicho_key in seen or nicho_key == "_meta":
            return
        data = catalog.get(nicho_key, {})
        if not isinstance(data, dict):
            return
        seen.add(nicho_key)
        suggestions.append({
            "nicho": nicho_key,
            "trend_match": trend.name,
            "trend_source": trend.source,
            "trend_score": trend.score,
            "match_type": match_type,
            "match_reason": match_reason,
            "scianes": data.get("scianes", []),
            "modelo_default": data.get("modelo_default", ""),
        })

    # 1. Match directo por alias
    for trend in report.top_overall:
        trend_low = trend.name.lower()
        for nicho_key, data in catalog.items():
            if nicho_key in seen or nicho_key == "_meta":
                continue
            if not isinstance(data, dict):
                continue
            aliases = [nicho_key] + data.get("aliases", [])
            for alias in aliases:
                if not alias:
                    continue
                if alias.lower() in trend_low or trend_low in alias.lower():
                    _add(nicho_key, trend, "alias_direct",
                          f"alias '{alias}' aparece en '{trend.name}'")
                    break
        if len(suggestions) >= max_niches:
            return suggestions

    # 2. Match semántico (palabra clave → nichos relacionados)
    for trend in report.top_overall:
        trend_low = trend.name.lower()
        for keyword, nichos in SEMANTIC_TREND_TO_NICHOS.items():
            if keyword in trend_low:
                for nicho_key in nichos:
                    if nicho_key in catalog and nicho_key not in seen:
                        _add(nicho_key, trend, "semantic",
                              f"keyword '{keyword}' en '{trend.name}' → nicho relacionado")
                if len(suggestions) >= max_niches:
                    return suggestions

    return suggestions


__all__ = [
    "TrendItem", "TrendsReport",
    "get_all_trends", "get_google_trends_mx", "get_google_trends_related",
    "get_mercadolibre_trends", "get_tiktok_trends_mx",
    "get_amazon_bestsellers_mx", "get_wikipedia_trends_es",
    "suggest_niches_from_trends",
    "HAS_PYTRENDS",
]
