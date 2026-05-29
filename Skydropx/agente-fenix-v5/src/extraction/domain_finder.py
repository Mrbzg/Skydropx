"""
Domain Finder — busca el sitio web de una empresa cuando DENUE no lo trae.

Estrategia jerárquica:
1. Búsqueda Google/Bing/DDG vía SearchBackendManager con razón_social + ciudad
2. Filtra resultados: solo dominios MX (.mx, .com.mx, .org.mx) o .com con relevancia
3. Excluye marketplaces, directorios genéricos (yelp, facebook, etc.)
4. Verifica que el dominio existe (MX records)
5. Opcionalmente: cross-validation (verifica que el nombre aparece en la home page)

Sin auth, sin pago. Usa los search backends ya configurados.

Para Skydropx: convierte huérfanos SIN_CONTACTO (53% de DENUE) en candidatos
para Hunter → enriquecimiento real.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

import requests

from src.core.config import settings
from src.core.user_agents import random_ua

logger = logging.getLogger(__name__)


# Dominios que NO son sitios oficiales de empresa
NON_OFFICIAL_DOMAINS = {
    # Marketplaces (que ya están en exclusions pero los repetimos por seguridad)
    "amazon.com.mx", "mercadolibre.com.mx", "walmart.com.mx",
    "liverpool.com.mx", "coppel.com", "shein.com.mx", "temu.com",
    # Redes sociales (links a perfiles, no sitios oficiales)
    "facebook.com", "instagram.com", "twitter.com", "x.com",
    "tiktok.com", "linkedin.com", "youtube.com", "pinterest.com",
    # Directorios genéricos
    "yelp.com", "yelp.com.mx", "foursquare.com", "yellowpages.com",
    "paginasamarillas.com.mx", "encuentra24.com",
    # Wikipedia / Wiki / referencias
    "wikipedia.org", "wikidata.org",
    # Reviews / comparadores
    "trustpilot.com", "doctoralia.com.mx", "doctoralia.com",
    # Mapas
    "google.com", "maps.google.com", "duckduckgo.com", "bing.com",
    # Cámaras (si encontró el directorio de la cámara y no el sitio de la empresa)
    "canacintra.org.mx", "coparmex.org.mx", "amvo.org.mx",
}


@dataclass
class DomainCandidate:
    domain: str
    url_found: str
    title: str = ""
    snippet: str = ""
    score: int = 50         # 0-100 confianza de que ES el sitio oficial
    backend_used: str = ""
    verified_mx: bool = False
    notes: str = ""


@dataclass
class DomainSearchResult:
    company_name: str
    candidates: list[DomainCandidate] = field(default_factory=list)
    best: DomainCandidate | None = None
    queries_used: list[str] = field(default_factory=list)
    error: str = ""

    def has_result(self) -> bool:
        return self.best is not None


def _normalize_company_for_search(name: str) -> str:
    """Limpia razón social para búsqueda: quita SA de CV, lower, etc."""
    s = re.sub(r"\b(s\.?\s*a\.?\s*b?\.?(\s+de\s+c\.?\s*v\.?)?|sapi(\s+de\s+c\.?\s*v\.?)?|"
               r"s\.?\s*c\.?|s\.?\s*(de\s+)?r\.?\s*l\.?(\s+de\s+c\.?\s*v\.?)?)\b",
               "", name, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _extract_domain(url: str) -> str | None:
    try:
        p = urlparse(url if url.startswith("http") else f"http://{url}")
        d = p.netloc.lower().lstrip("www.")
        return d.split("/")[0] if d else None
    except Exception:  # noqa: BLE001
        return None


def _is_acceptable_domain(domain: str) -> bool:
    """¿Vale la pena considerar este dominio como sitio oficial?"""
    if not domain or "." not in domain:
        return False
    if domain in NON_OFFICIAL_DOMAINS:
        return False
    if any(domain.endswith("." + n) or domain == n for n in NON_OFFICIAL_DOMAINS):
        return False
    # gob.mx y edu.mx también se descartan
    if domain.endswith(".gob.mx") or domain.endswith(".edu.mx"):
        return False
    return True


def _score_candidate(
    domain: str, company_norm: str,
    title: str, snippet: str,
) -> int:
    """Score 0-100 de qué tan probable es que este dominio sea el oficial."""
    score = 30  # base
    company_words = set(w for w in company_norm.lower().split() if len(w) > 2)
    if not company_words:
        return score

    # 1. Dominio MX: +20
    if domain.endswith(".mx") or domain.endswith(".com.mx"):
        score += 20

    # 2. Palabra del nombre aparece en el dominio: +25
    dom_no_tld = domain.split(".")[0].lower()
    for w in company_words:
        if w in dom_no_tld and len(w) >= 4:
            score += 25
            break

    # 3. Nombre aparece en el title del resultado: +15
    title_low = title.lower()
    matches_in_title = sum(1 for w in company_words if w in title_low)
    if matches_in_title >= 2:
        score += 15
    elif matches_in_title == 1 and len(company_words) <= 3:
        score += 10

    # 4. Snippet menciona "oficial", "sitio web", "contacto"
    snip_low = snippet.lower()
    if any(k in snip_low for k in ("sitio oficial", "página oficial", "official",
                                     "contáctanos", "acerca de nosotros")):
        score += 10

    return min(score, 100)


class DomainFinder:
    def __init__(
        self,
        verify_mx: bool = True,
        min_score_to_accept: int = 60,
        max_candidates_per_query: int = 10,
    ):
        self.verify_mx = verify_mx
        self.min_score = min_score_to_accept
        self.max_candidates_per_query = max_candidates_per_query

        # Lazy imports
        self._manager = None
        self._verifier = None

    def _get_manager(self):
        if self._manager is None:
            from src.sources.search_backends import get_default_manager
            self._manager = get_default_manager()
        return self._manager

    def _get_verifier(self):
        if self._verifier is None:
            from src.extraction.email_verifier import EmailVerifier
            self._verifier = EmailVerifier(check_mx=True, check_smtp=False)
        return self._verifier

    def find(
        self,
        company_name: str,
        ciudad: str = "",
        giro: str = "",
        cap_results: int = 5,
        use_serper_for_critical: bool = False,    # marca esta búsqueda como critical
    ) -> DomainSearchResult:
        """
        Busca el dominio oficial de una empresa.

        Si use_serper_for_critical=True, marca la búsqueda como 'critical' →
        Serper se usará aunque su strategy sea 'reserve'. Recomendado para:
          - Leads ICP_2 Enterprise (3PL, agencias, fabricantes grandes)
          - Empresas con alta priority_score en pipeline
        """
        result = DomainSearchResult(company_name=company_name)
        norm = _normalize_company_for_search(company_name)
        if not norm or len(norm) < 4:
            result.error = "company_name_too_short"
            return result

        # Generar queries en orden de especificidad
        queries = []
        if ciudad:
            queries.append(f'"{norm}" {ciudad} site:.mx')
            queries.append(f'"{norm}" {ciudad}')
        queries.append(f'"{norm}" sitio oficial site:.mx')
        queries.append(f'"{norm}" contacto site:.mx')
        if giro:
            # Tomar solo el primer término del giro
            giro_first = giro.split(",")[0].split(" ")[0:3]
            queries.append(f'"{norm}" {" ".join(giro_first)}')

        result.queries_used = queries

        # Ejecutar búsquedas (cap a 2-3 queries para no agotar budget)
        seen_domains: set[str] = set()
        mgr = self._get_manager()

        for query in queries[:3]:
            try:
                ctx = "critical" if use_serper_for_critical else "normal"
                hits = mgr.search(query, limit=self.max_candidates_per_query,
                                    country="mx", avoid_paid=False,
                                    context=ctx)
            except Exception as e:  # noqa: BLE001
                logger.debug("DomainFinder search err: %s", e)
                continue

            for hit in hits:
                dom = _extract_domain(hit.url)
                if not dom or dom in seen_domains:
                    continue
                if not _is_acceptable_domain(dom):
                    continue
                seen_domains.add(dom)

                score = _score_candidate(dom, norm, hit.title or "",
                                          hit.snippet or "")
                cand = DomainCandidate(
                    domain=dom, url_found=hit.url,
                    title=hit.title or "", snippet=(hit.snippet or "")[:200],
                    score=score, backend_used=hit.source,
                )
                result.candidates.append(cand)

            # Si el mejor candidato hasta ahora ya está sobre el threshold, parar
            if result.candidates:
                top = max(result.candidates, key=lambda c: c.score)
                if top.score >= 90:
                    break

        if not result.candidates:
            result.error = "no_candidates_found"
            return result

        # Ordenar candidatos por score
        result.candidates.sort(key=lambda c: -c.score)
        result.candidates = result.candidates[:cap_results]

        # Verificar MX del top candidato
        if self.verify_mx:
            for cand in result.candidates[:3]:
                try:
                    mx = self._get_verifier()._check_mx_records(cand.domain)  # noqa: SLF001
                    cand.verified_mx = bool(mx)
                    if mx:
                        cand.score = min(cand.score + 10, 100)
                        cand.notes = f"MX OK ({len(mx)} records)"
                    else:
                        cand.score = max(cand.score - 20, 0)
                        cand.notes = "Sin MX records"
                except Exception:  # noqa: BLE001
                    pass

            # Re-ordenar tras verificación
            result.candidates.sort(key=lambda c: -c.score)

        # Best = top siempre que pase threshold
        if result.candidates and result.candidates[0].score >= self.min_score:
            result.best = result.candidates[0]

        return result


__all__ = ["DomainFinder", "DomainSearchResult", "DomainCandidate"]
