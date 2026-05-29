"""
Extractor para enriquecer un sitio web → email/tel/whatsapp/owner/platform.

Se invoca desde el Hunter cuando un RawRecord ya tiene `sitio_web` pero le faltan
contactos. Hace un crawling ligero (4-6 páginas típicas: /contacto, /nosotros, footer).
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from src.core.config import settings
from src.core.robots import can_fetch as robots_can_fetch

try:
    import trafilatura
    HAS_TRAFILATURA = True
except ImportError:
    HAS_TRAFILATURA = False

logger = logging.getLogger(__name__)

CONTACT_PATHS = [
    "/", "/contacto", "/contactanos", "/contact", "/contact-us",
    "/nosotros", "/about", "/about-us", "/quienes-somos",
    "/atencion-a-clientes", "/customer-service", "/ayuda",
    "/envios", "/shipping",
]

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
PHONE_MX_RE = re.compile(
    r"(?:(?:\+|00)?52[\s\-\.]?)?(?:1[\s\-\.]?)?"
    r"(?:\(?\d{2,3}\)?[\s\-\.]?)?\d{3,4}[\s\-\.]?\d{4}"
)
WHATSAPP_LINK_RE = re.compile(
    r"(?:wa\.me/|whatsapp\.com/send\?phone=|api\.whatsapp\.com/send\?phone=)([+\d]{10,15})",
    re.I,
)
WHATSAPP_TEXT_RE = re.compile(r"whats?app[\s:\-]*([+\d\s\-\(\)]{10,20})", re.I)

GENERIC_EMAIL_PREFIXES = {
    "info", "contacto", "contact", "hola", "hello", "ventas", "sales",
    "atencion", "soporte", "support", "ayuda", "help", "admin", "webmaster",
    "noreply", "no-reply", "newsletter",
}

PLATFORM_PATTERNS = {
    "shopify": [r"cdn\.shopify\.com", r"myshopify\.com"],
    "tiendanube": [r"tiendanube\.com", r"d22fxaf9e50qjs\.cloudfront\.net"],
    "woocommerce": [r"wp-content/plugins/woocommerce"],
    "vtex": [r"vtexassets\.com"],
    "magento": [r"Magento_"],
    "wix_stores": [r"wix\.com.*ecommerce"],
    "squarespace": [r"squarespace\.com.*Commerce"],
    "jumpseller": [r"jumpseller\.com"],
}

PAQUETERIAS_COMPETENCIA = {
    "estafeta": r"\bestafeta\b",
    "dhl": r"\bdhl\b",
    "fedex": r"\bfedex\b",
    "99minutos": r"99\s*minutos",
    "paquetexpress": r"paquetexpress",
    "redpack": r"\bredpack\b",
    "ups": r"\bups\b",
    "correos_mexico": r"correos\s+de\s+m[ée]xico",
    "ivoy": r"\bivoy\b",
    "skydropx": r"\bskydropx\b",
}

ENVIOS_INTENT = [
    r"env[íi]os\s+a\s+toda\s+la\s+rep[úu]blica",
    r"enviamos\s+a\s+todo\s+m[ée]xico",
    r"env[íi]os\s+nacionales",
    r"paqueter[íi]a",
]

OWNER_REGEX = re.compile(
    r"(fundadora?|CEO|CTO|director(?:a)?\s+general|propietari[oa]|due[ñn][oa]|founder)"
    r"[\s:,\-]+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){1,3})",
    re.I,
)


@dataclass
class ExtractedContact:
    emails: list[str] = field(default_factory=list)
    emails_personales: list[str] = field(default_factory=list)
    telefonos: list[str] = field(default_factory=list)
    whatsapps: list[str] = field(default_factory=list)
    nombres_personas: list[str] = field(default_factory=list)
    plataforma: str | None = None
    envios_intent: bool = False
    paqueterias_mencionadas: list[str] = field(default_factory=list)
    paginas_revisadas: list[str] = field(default_factory=list)

    @property
    def is_skydropx_ready(self) -> bool:
        return bool(self.emails) and bool(self.telefonos or self.whatsapps)


class SkydropxExtractor:
    def __init__(
        self,
        session: requests.Session | None = None,
        timeout: int = 15,
        max_pages_per_domain: int = 4,
        delay_sec: float = 0.6,
    ):
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": settings.user_agent,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "es-MX,es;q=0.9,en;q=0.7",
        })
        # Proxies si están configurados
        if settings.http_proxy:
            self.session.proxies["http"] = settings.http_proxy
        if settings.https_proxy:
            self.session.proxies["https"] = settings.https_proxy

        self.timeout = timeout
        self.max_pages = max_pages_per_domain
        self.delay = delay_sec

    def extract_from_domain(self, domain: str) -> ExtractedContact:
        base = self._normalize_base(domain)
        contact = ExtractedContact()
        all_html: list[str] = []
        for path in CONTACT_PATHS[: self.max_pages]:
            url = urljoin(base, path)
            html = self._fetch(url)
            if html:
                contact.paginas_revisadas.append(url)
                all_html.append(html)
                self._extract_into(html, contact)
                time.sleep(self.delay)
        if all_html:
            joined = "\n".join(all_html)
            self._detect_platform(joined, contact)
            self._detect_envios(joined, contact)
            self._detect_paqueterias(joined, contact)
        self._dedup_and_rank(contact)
        return contact

    def extract_from_html(self, html: str, base_url: str = "") -> ExtractedContact:
        contact = ExtractedContact()
        if base_url:
            contact.paginas_revisadas.append(base_url)
        self._extract_into(html, contact)
        self._detect_platform(html, contact)
        self._detect_envios(html, contact)
        self._detect_paqueterias(html, contact)
        self._dedup_and_rank(contact)
        return contact

    # ---------- internals ----------

    @staticmethod
    def _normalize_base(domain: str) -> str:
        if domain.startswith(("http://", "https://")):
            return domain.rstrip("/")
        return f"https://{domain.lstrip('/')}"

    def _fetch(self, url: str) -> str | None:
        if not robots_can_fetch(url):
            logger.debug("extract: robots.txt disallow %s", url)
            return None
        try:
            r = self.session.get(url, timeout=self.timeout, allow_redirects=True)
            if r.ok and "text/html" in r.headers.get("Content-Type", ""):
                return r.text[:250_000]
        except Exception as e:  # noqa: BLE001
            logger.debug("extract fetch %s err: %s", url, e)
        return None

    def extract_clean_text(self, html: str) -> str:
        """Texto limpio usando trafilatura si está, sino BS4. Útil para LLM analysis."""
        if HAS_TRAFILATURA:
            txt = trafilatura.extract(html, favor_recall=True,
                                       include_links=False, include_comments=False)
            if txt:
                return txt
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)

    def _extract_into(self, html: str, contact: ExtractedContact) -> None:
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True)

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("mailto:"):
                e = href.replace("mailto:", "").split("?")[0].strip().lower()
                if e and self._is_valid_email(e):
                    contact.emails.append(e)
            if href.startswith("tel:"):
                num = self._normalize_phone(href.replace("tel:", ""))
                if num:
                    contact.telefonos.append(num)

        for m in EMAIL_RE.finditer(text):
            e = m.group(0).lower()
            if self._is_valid_email(e):
                contact.emails.append(e)

        for m in WHATSAPP_LINK_RE.finditer(html):
            num = self._normalize_phone(m.group(1))
            if num:
                contact.whatsapps.append(num)

        for m in WHATSAPP_TEXT_RE.finditer(text):
            num = self._normalize_phone(m.group(1))
            if num:
                contact.whatsapps.append(num)

        for m in PHONE_MX_RE.finditer(text):
            num = self._normalize_phone(m.group(0))
            if num and len(num) == 10:
                contact.telefonos.append(num)

        for m in OWNER_REGEX.finditer(text):
            nombre = m.group(2).strip()
            if 4 < len(nombre) < 60:
                contact.nombres_personas.append(nombre)

    def _detect_platform(self, html: str, contact: ExtractedContact) -> None:
        for plat, patterns in PLATFORM_PATTERNS.items():
            if any(re.search(p, html, re.I) for p in patterns):
                contact.plataforma = plat
                return

    def _detect_envios(self, html: str, contact: ExtractedContact) -> None:
        if any(re.search(p, html, re.I) for p in ENVIOS_INTENT):
            contact.envios_intent = True

    def _detect_paqueterias(self, html: str, contact: ExtractedContact) -> None:
        for name, pat in PAQUETERIAS_COMPETENCIA.items():
            if re.search(pat, html, re.I):
                contact.paqueterias_mencionadas.append(name)

    @staticmethod
    def _is_valid_email(email: str) -> bool:
        if any(email.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")):
            return False
        if any(bd in email for bd in ("sentry.io", "wixpress.com", "example.com",
                                       "test.com", "sentry-next.")):
            return False
        return "@" in email and "." in email.split("@")[1]

    @staticmethod
    def _normalize_phone(raw: str) -> str | None:
        digits = re.sub(r"\D", "", raw)
        if digits.startswith("521") and len(digits) >= 13:
            digits = digits[2:]
        if digits.startswith("52") and len(digits) >= 12:
            digits = digits[2:]
        if len(digits) == 10:
            return digits
        if len(digits) > 10:
            return digits[-10:]
        return None

    @staticmethod
    def _dedup_and_rank(c: ExtractedContact) -> None:
        c.emails = sorted(set(c.emails))
        c.telefonos = sorted(set(c.telefonos))
        c.whatsapps = sorted(set(c.whatsapps))
        c.nombres_personas = list(dict.fromkeys(c.nombres_personas))
        c.paqueterias_mencionadas = sorted(set(c.paqueterias_mencionadas))
        c.emails_personales = [
            e for e in c.emails
            if e.split("@")[0].lower() not in GENERIC_EMAIL_PREFIXES
        ]


__all__ = ["SkydropxExtractor", "ExtractedContact"]
