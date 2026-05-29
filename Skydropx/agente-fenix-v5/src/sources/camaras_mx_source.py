"""
Padrones públicos de Cámaras Empresariales MX.

Cada cámara expone su directorio en su sitio. Este módulo trae scrapers básicos
para cada una. Los selectores CSS pueden requerir mantenimiento si las cámaras
rediseñan sus sitios (Pull Request bienvenidos).
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

from src.core.models import RawRecord, ResearchPlan
from src.core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 20
DEFAULT_DELAY = 1.0

CAMARAS_DISPONIBLES = {
    "amvo": "Asociación Mexicana de Venta Online (100% e-commerce)",
    "antad": "Asoc. Nacional de Tiendas de Autoservicio y Departamentales",
    "canacintra": "Cámara Nacional de la Industria de Transformación",
    "coparmex": "Confederación Patronal de la República Mexicana",
    "comce": "Consejo Empresarial Mexicano de Comercio Exterior",
    "canirac": "Cámara Nacional de la Industria de Restaurantes",
    "canieti": "Cámara Nacional de TI y Telecomunicaciones",
}


# ---------------- Base ----------------

class CamaraScraperBase:
    name = ""
    base_url = ""
    skydropx_priority = "MEDIUM"

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": settings.user_agent,
            "Accept": "text/html,application/xhtml+xml",
        })

    def fetch(self, url: str) -> BeautifulSoup | None:
        try:
            r = self.session.get(url, timeout=DEFAULT_TIMEOUT)
            if r.ok:
                return BeautifulSoup(r.text, "html.parser")
            logger.debug("%s fetch %s → HTTP %s", self.name, url, r.status_code)
        except Exception as e:  # noqa: BLE001
            logger.debug("%s fetch err %s: %s", self.name, url, e)
        return None

    def scrape(self) -> list[RawRecord]:
        raise NotImplementedError

    # Utilities
    @staticmethod
    def _text(node, sel: str) -> str:
        el = node.select_one(sel) if node else None
        return el.get_text(" ", strip=True) if el else ""

    @staticmethod
    def _href(node, sel: str) -> str | None:
        el = node.select_one(sel) if node else None
        return el.get("href") if el else None

    @staticmethod
    def _extract_phone(text: str) -> str | None:
        m = re.search(
            r"(?:\+?52[\s\-]?)?(?:1[\s\-]?)?(?:\(?\d{2,3}\)?[\s\-]?)?\d{3,4}[\s\-]?\d{4}",
            text,
        )
        if not m:
            return None
        digits = re.sub(r"\D", "", m.group(0))
        return digits[-10:] if len(digits) >= 10 else None

    @staticmethod
    def _extract_email(text: str) -> str | None:
        m = re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text)
        return m.group(0).lower() if m else None


# ---------------- AMVO ----------------

class AMVOScraper(CamaraScraperBase):
    name = "amvo"
    base_url = "https://www.amvo.org.mx"
    directorio_url = "https://www.amvo.org.mx/asociados/"
    skydropx_priority = "HIGH"   # 100% ecommerce

    def scrape(self) -> list[RawRecord]:
        records: list[RawRecord] = []
        soup = self.fetch(self.directorio_url)
        if not soup:
            return records
        # AMVO usa diferentes layouts; intentamos varios selectores
        cards = (soup.select(".asociado-item")
                 or soup.select(".partner-card")
                 or soup.select("article.asociado")
                 or soup.select(".elementor-column a[href*='http']"))
        for card in cards:
            nombre = self._text(card, ".nombre, h3, h4") or card.get_text(" ", strip=True)[:80]
            link = self._href(card, "a[href*='http']")
            descr = self._text(card, ".descripcion, p")
            if not nombre:
                continue
            records.append(RawRecord(
                source="camara_amvo",
                empresa=nombre,
                nombre_comercial=nombre,
                sitio_web=link,
                metadata={
                    "camara": "AMVO",
                    "categoria_camara": "ecommerce",
                    "descripcion": descr,
                    "perfil_camara_url": self.directorio_url,
                    "skydropx_priority": self.skydropx_priority,
                },
            ))
        logger.info("AMVO: %s asociados", len(records))
        return records


# ---------------- Canacintra ----------------

class CanacintraScraper(CamaraScraperBase):
    name = "canacintra"
    base_url = "https://canacintra.org.mx"
    directorio_url = "https://canacintra.org.mx/directorio-empresarial/"
    skydropx_priority = "HIGH"

    def scrape(self, max_pages: int = 50) -> list[RawRecord]:
        records: list[RawRecord] = []
        for page in range(1, max_pages + 1):
            url = f"{self.directorio_url}?page={page}"
            soup = self.fetch(url)
            if not soup:
                break
            empresas = (soup.select(".empresa-item")
                        or soup.select(".directory-card")
                        or soup.select(".resultado-empresa"))
            if not empresas:
                break
            for emp in empresas:
                txt = emp.get_text(" ", strip=True)
                nombre = self._text(emp, "h3, .nombre-empresa, h4") or txt[:80]
                records.append(RawRecord(
                    source="camara_canacintra",
                    empresa=nombre,
                    nombre_comercial=nombre,
                    telefono=self._extract_phone(txt),
                    email=self._extract_email(txt),
                    sitio_web=self._href(emp, "a[href*='http']"),
                    municipio=self._text(emp, ".ciudad, .estado"),
                    metadata={
                        "camara": "Canacintra",
                        "categoria_camara": "industrial",
                        "skydropx_priority": self.skydropx_priority,
                    },
                ))
            time.sleep(DEFAULT_DELAY)
        logger.info("Canacintra: %s empresas", len(records))
        return records


# ---------------- CANIRAC ----------------

class CaniracScraper(CamaraScraperBase):
    name = "canirac"
    base_url = "https://canirac.org.mx"
    directorio_url = "https://canirac.org.mx/directorio/"
    skydropx_priority = "MEDIUM"  # restaurantes con delivery

    def scrape(self) -> list[RawRecord]:
        records: list[RawRecord] = []
        soup = self.fetch(self.directorio_url)
        if not soup:
            return records
        for item in soup.select(".restaurante, .establecimiento, article"):
            txt = item.get_text(" ", strip=True)
            nombre = self._text(item, "h3, h4, .nombre") or txt[:80]
            if not nombre:
                continue
            records.append(RawRecord(
                source="camara_canirac",
                empresa=nombre,
                telefono=self._extract_phone(txt),
                email=self._extract_email(txt),
                sitio_web=self._href(item, "a[href*='http']"),
                metadata={
                    "camara": "CANIRAC",
                    "categoria_camara": "restaurantes",
                    "skydropx_priority": self.skydropx_priority,
                },
            ))
        logger.info("CANIRAC: %s establecimientos", len(records))
        return records


# ---------------- Coparmex (placeholder, paginado por estado) ----------------

class CoparmexScraper(CamaraScraperBase):
    name = "coparmex"
    base_url = "https://coparmex.org.mx"
    skydropx_priority = "HIGH"

    def scrape(self) -> list[RawRecord]:
        # Coparmex tiene 65+ centros empresariales por estado, cada uno con su sitio
        # Implementación inicial: solo trae listado de centros para validación manual
        logger.warning("Coparmex: scraper stub — requiere implementación por estado")
        return []


# ---------------- Registry ----------------

SCRAPERS = {
    "amvo": AMVOScraper,
    "canacintra": CanacintraScraper,
    "canirac": CaniracScraper,
    "coparmex": CoparmexScraper,
}


def search(plan: ResearchPlan) -> list[RawRecord]:
    camaras = plan.extras.get("camaras") or list(SCRAPERS.keys())
    results: list[RawRecord] = []
    for cam in camaras:
        ScrCls = SCRAPERS.get(cam)
        if not ScrCls:
            logger.warning("Cámara %s no implementada (válidas: %s)", cam, list(SCRAPERS.keys()))
            continue
        try:
            scraper = ScrCls()
            recs = scraper.scrape()
            results.extend(recs)
        except Exception as e:  # noqa: BLE001
            logger.exception("Error cámara %s: %s", cam, e)
        time.sleep(2.0)
    return results


__all__ = ["search", "SCRAPERS", "CAMARAS_DISPONIBLES",
           "AMVOScraper", "CanacintraScraper", "CaniracScraper", "CoparmexScraper"]
