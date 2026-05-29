"""
Fuente Mercado Libre MX - vendedores activos vía API pública.

Sin auth: 1K req/h. Con OAuth gratis: 10K req/h.
API: https://api.mercadolibre.com

Cómo descubre leads:
1. Buscar productos por categoría → sacar seller_id
2. GET /users/{seller_id} → datos públicos del vendedor
3. Detectar tiendas oficiales (tienen .com.mx propio para crawl posterior)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Iterator

import requests

from src.core.models import RawRecord, ResearchPlan
from src.core.config import settings

logger = logging.getLogger(__name__)

ML_API = "https://api.mercadolibre.com"
SITE_ID = "MLM"
DEFAULT_TIMEOUT = 15
DEFAULT_DELAY = 0.7  # 1K/h = ~3.6s, 0.7 es OK con bursts

# Categorías top con vendedores que necesitan envíos
ECOMMERCE_CATEGORIES = {
    "MLM1430": "Ropa, Bolsas y Calzado",
    "MLM1276": "Deportes y Fitness",
    "MLM1574": "Hogar, Muebles y Jardín",
    "MLM1648": "Computación",
    "MLM1132": "Juegos y Juguetes",
    "MLM1182": "Instrumentos Musicales",
    "MLM3937": "Belleza y Cuidado Personal",
    "MLM1499": "Industrias y Oficinas",
    "MLM1051": "Celulares y Teléfonos",
    "MLM1000": "Electrónica, Audio y Video",
    "MLM1196": "Música, Películas y Series",
    "MLM3025": "Libros, Revistas y Comics",
    "MLM5726": "Electrodomésticos",
    "MLM1540": "Bebés",
    "MLM1071": "Animales y Mascotas",
}


@dataclass
class MLConfig:
    categorias: list[str] | None = None
    estados_filter: list[str] | None = None
    limit_per_categoria: int = 500
    solo_tiendas_oficiales: bool = False
    delay_sec: float = DEFAULT_DELAY
    enriquecer_user: bool = True   # llamar /users/{id} para cada seller


class MercadoLibreClient:
    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": settings.user_agent,
            "Accept": "application/json",
        })
        # OAuth opcional para 10x rate-limit
        if settings.ml_app_id and settings.ml_client_secret:
            self._refresh_token()

    def _refresh_token(self) -> None:
        """OAuth client_credentials para 10K req/h."""
        try:
            r = self.session.post(
                f"{ML_API}/oauth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": settings.ml_app_id,
                    "client_secret": settings.ml_client_secret,
                },
                timeout=10,
            )
            if r.ok:
                token = r.json().get("access_token")
                if token:
                    self.session.headers["Authorization"] = f"Bearer {token}"
                    logger.info("ML OAuth ok, rate-limit 10K/h")
        except Exception as e:  # noqa: BLE001
            logger.warning("ML OAuth failed, usando sin auth: %s", e)

    def search_products(self, category: str, offset: int = 0, limit: int = 50) -> dict:
        url = f"{ML_API}/sites/{SITE_ID}/search"
        params = {"category": category, "limit": limit, "offset": offset}
        r = self.session.get(url, params=params, timeout=DEFAULT_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def get_user(self, user_id: int) -> dict:
        url = f"{ML_API}/users/{user_id}"
        r = self.session.get(url, timeout=DEFAULT_TIMEOUT)
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        return r.json()


def iter_sellers_from_category(
    client: MercadoLibreClient,
    category: str,
    max_sellers: int,
    delay: float = DEFAULT_DELAY,
) -> Iterator[int]:
    """Itera seller_ids únicos paginando."""
    seen: set[int] = set()
    offset = 0
    while len(seen) < max_sellers:
        try:
            data = client.search_products(category, offset=offset, limit=50)
        except requests.HTTPError as e:
            logger.warning("ML search err cat=%s offset=%s: %s", category, offset, e)
            break
        results = data.get("results", [])
        if not results:
            break
        for item in results:
            sid = (item.get("seller") or {}).get("id")
            if sid and sid not in seen:
                seen.add(sid)
                yield sid
                if len(seen) >= max_sellers:
                    return
        offset += 50
        total = data.get("paging", {}).get("total", 0)
        if offset >= total:
            break
        time.sleep(delay)


def _to_rawrecord(user_data: dict, category_label: str = "") -> RawRecord:
    address = user_data.get("address", {}) or {}
    rep = user_data.get("seller_reputation", {}) or {}
    txs = rep.get("transactions") or {}
    is_official = (
        user_data.get("user_type") == "brand"
        or "brand" in (user_data.get("tags") or [])
    )
    nickname = user_data.get("nickname", "")
    sid = user_data.get("id")

    return RawRecord(
        source="mercadolibre",
        empresa=nickname,
        nombre_comercial=nickname,
        telefono=None,  # ML oculta tel; se enriquece luego
        email=None,
        whatsapp=None,
        sitio_web=None,  # solo tiendas oficiales lo exponen; lo llenamos vía crawl
        estado=address.get("state", ""),
        municipio=address.get("city", ""),
        scian=None,
        giro_descripcion=f"Vendedor ML — {category_label}",
        metadata={
            "ml_user_id": sid,
            "ml_nickname": nickname,
            "ml_perfil_url": f"https://www.mercadolibre.com.mx/perfil/{nickname}",
            "ml_reputation_level": rep.get("level_id"),
            "ml_power_seller": rep.get("power_seller_status"),
            "ml_tx_completed": txs.get("completed", 0),
            "ml_tx_canceled": txs.get("canceled", 0),
            "ml_tienda_oficial": is_official,
            "ml_pais": user_data.get("country_id", "MX"),
            "ml_categoria": category_label,
        },
    )


def search(plan: ResearchPlan) -> list[RawRecord]:
    """Entry point para el orquestador (Scout)."""
    config = MLConfig(
        categorias=plan.extras.get("ml_categorias") or list(ECOMMERCE_CATEGORIES.keys())[:5],
        estados_filter=plan.estados or None,
        limit_per_categoria=plan.extras.get("ml_limit_per_cat", 300),
        solo_tiendas_oficiales=plan.extras.get("ml_solo_oficiales", False),
    )
    client = MercadoLibreClient()
    results: list[RawRecord] = []
    seen_users: set[int] = set()

    for category in config.categorias:
        cat_label = ECOMMERCE_CATEGORIES.get(category, category)
        logger.info("ML scrapeando categoría %s (%s)", category, cat_label)

        for sid in iter_sellers_from_category(client, category, config.limit_per_categoria):
            if sid in seen_users:
                continue
            seen_users.add(sid)
            try:
                if config.enriquecer_user:
                    user = client.get_user(sid)
                else:
                    user = {"id": sid, "nickname": ""}
                if not user:
                    continue
                if user.get("country_id", "MX") != "MX":
                    continue
                is_official = (
                    user.get("user_type") == "brand"
                    or "brand" in (user.get("tags") or [])
                )
                if config.solo_tiendas_oficiales and not is_official:
                    continue
                if config.estados_filter:
                    estado = (user.get("address") or {}).get("state", "").lower()
                    if not any(e.lower() in estado for e in config.estados_filter):
                        continue
                results.append(_to_rawrecord(user, cat_label))
                time.sleep(config.delay_sec)
            except requests.HTTPError as e:
                logger.debug("ML user %s err: %s", sid, e)
                continue

    logger.info("ML total únicos: %s", len(results))
    return results


__all__ = ["search", "MercadoLibreClient", "ECOMMERCE_CATEGORIES", "MLConfig"]
