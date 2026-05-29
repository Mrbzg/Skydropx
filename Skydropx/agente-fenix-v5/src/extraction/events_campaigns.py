"""
Events + Campaigns Engine — resuelve casos como:
  "leads del Mundial"
  "leads del Día de las Madres"
  "leads de empresas con campañas 'compra y gana' del Mundial"
  "qué agencia está detrás de datumax.mx"

3 capas:
1. Event Calendar       → catálogo de eventos MX/globales + ventanas + keywords
2. Campaign Detector    → keywords promocionales típicas (compra y gana, etc.)
3. Agency Detector      → encuentra la agencia detrás de una campaña en un dominio

Sin IA: catálogo + regex + scraping ligero.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from src.core.config import settings
from src.core.user_agents import random_ua

logger = logging.getLogger(__name__)

EVENTOS_PATH = Path(__file__).resolve().parents[2] / "data" / "eventos_mx.json"


def _load_catalog() -> dict:
    if not EVENTOS_PATH.exists():
        return {"eventos": [], "_keywords_promocionales": {}, "_dorks_descubrimiento_agencia": {}}
    return json.loads(EVENTOS_PATH.read_text(encoding="utf-8"))


CATALOG = _load_catalog()


# =====================================================================
# 1. EVENT CALENDAR — qué evento está activo
# =====================================================================

@dataclass
class EventoActivo:
    id: str
    nombre: str
    dias_restantes: int   # negativo si ya pasó
    fase: str             # 'pre_evento' | 'durante' | 'post_evento'
    keywords: list[str] = field(default_factory=list)
    categorias_target: list[str] = field(default_factory=list)
    dorks_intent: list[str] = field(default_factory=list)
    fecha_evento: str = ""


def _resolve_fecha_evento(ev: dict, year: int) -> date | None:
    """Calcula la fecha real del evento para un año dado."""
    tipo = ev.get("tipo", "")
    if tipo == "fecha_fija":
        try:
            mm, dd = ev["fecha"].split("-")
            return date(year, int(mm), int(dd))
        except Exception:  # noqa: BLE001
            return None

    if tipo == "rango":
        try:
            # puede ser "MM-DD" o "YYYY-MM-DD"
            inicio_str = ev["fecha_inicio"]
            if len(inicio_str) == 10:  # "YYYY-MM-DD"
                return date.fromisoformat(inicio_str)
            mm, dd = inicio_str.split("-")
            return date(year, int(mm), int(dd))
        except Exception:  # noqa: BLE001
            return None

    if tipo == "fecha_movil":
        try:
            mm, dd = ev["fecha_aprox"].split("-")
            return date(year, int(mm), int(dd))
        except Exception:  # noqa: BLE001
            return None

    if tipo == "tercer_domingo_junio":
        return _nth_weekday(year, 6, 6, 3)  # 6=domingo, 3era ocurrencia
    if tipo == "tercer_viernes_noviembre":
        return _nth_weekday(year, 11, 4, 3)  # 4=viernes
    if tipo == "cuarto_viernes_noviembre":
        return _nth_weekday(year, 11, 4, 4)
    if tipo == "primer_domingo_febrero":
        return _nth_weekday(year, 2, 6, 1)
    if tipo == "sabado_finales_mayo":
        return _last_weekday(year, 5, 5)
    if tipo == "lunes_despues_thanksgiving":
        # Thanksgiving = 4to jueves de noviembre + 4 días
        thanksgiving = _nth_weekday(year, 11, 3, 4)
        return thanksgiving + timedelta(days=4) if thanksgiving else None
    return None


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date | None:
    """weekday: 0=lunes...6=domingo. n=1..5"""
    try:
        d = date(year, month, 1)
        # Avanzar al primer weekday del mes
        offset = (weekday - d.weekday()) % 7
        d = d + timedelta(days=offset + 7 * (n - 1))
        if d.month != month:
            return None
        return d
    except Exception:  # noqa: BLE001
        return None


def _last_weekday(year: int, month: int, weekday: int) -> date | None:
    try:
        # último día del mes
        if month == 12:
            next_month = date(year + 1, 1, 1)
        else:
            next_month = date(year, month + 1, 1)
        last_day = next_month - timedelta(days=1)
        offset = (last_day.weekday() - weekday) % 7
        return last_day - timedelta(days=offset)
    except Exception:  # noqa: BLE001
        return None


def get_eventos_activos(reference_date: date | None = None,
                          include_proximos: bool = True) -> list[EventoActivo]:
    """
    Devuelve eventos que están EN VENTANA hoy (o en la fecha de referencia).
    "En ventana" = dentro de [fecha - ventana_dias_antes, fecha + ventana_dias_despues]

    Si include_proximos=False, solo devuelve los que están durante el evento.
    """
    ref = reference_date or date.today()
    year = ref.year
    activos: list[EventoActivo] = []

    for ev in CATALOG.get("eventos", []):
        fecha = _resolve_fecha_evento(ev, year)
        if fecha is None:
            # Si el año actual ya pasó, intentar año siguiente
            fecha = _resolve_fecha_evento(ev, year + 1)
            if fecha is None:
                continue

        antes = ev.get("ventana_dias_antes", 14)
        despues = ev.get("ventana_dias_despues", 3)
        ventana_inicio = fecha - timedelta(days=antes)
        ventana_fin = fecha + timedelta(days=despues)

        # Para rangos largos, también incluimos la fecha_fin
        if ev.get("tipo") == "rango" and ev.get("fecha_fin"):
            try:
                fin_str = ev["fecha_fin"]
                if len(fin_str) == 10:
                    fecha_fin_evento = date.fromisoformat(fin_str)
                else:
                    mm, dd = fin_str.split("-")
                    fecha_fin_evento = date(year, int(mm), int(dd))
                ventana_fin = max(ventana_fin, fecha_fin_evento + timedelta(days=despues))
            except Exception:  # noqa: BLE001
                pass

        if not (ventana_inicio <= ref <= ventana_fin):
            continue

        dias_restantes = (fecha - ref).days
        if dias_restantes > 0:
            fase = "pre_evento"
        elif dias_restantes == 0 or (ev.get("tipo") == "rango"
                                       and ev.get("fecha_fin") and
                                       ref <= fecha_fin_evento):
            fase = "durante"
        else:
            fase = "post_evento"

        if not include_proximos and fase == "pre_evento":
            continue

        activos.append(EventoActivo(
            id=ev["id"], nombre=ev["nombre"],
            dias_restantes=dias_restantes,
            fase=fase,
            keywords=ev.get("keywords", []),
            categorias_target=ev.get("categorias_target", []),
            dorks_intent=ev.get("dorks_intent", []),
            fecha_evento=fecha.isoformat(),
        ))

    activos.sort(key=lambda x: abs(x.dias_restantes))
    return activos


def find_evento_by_keyword(query: str) -> dict | None:
    """
    Busca un evento por keyword del usuario.
    'quiero leads del mundial' → mundial_fifa_2026
    'campañas día de las madres' → dia_de_las_madres
    Match jerárquico: word-boundary > substring > token-overlap
    """
    import unicodedata
    def _norm(s):
        s = unicodedata.normalize("NFD", s)
        s = "".join(c for c in s if unicodedata.category(c) != "Mn")
        return s.lower().strip()

    q = _norm(query)
    q_words = set(q.split())
    best: tuple[int, dict] = (0, None)

    for ev in CATALOG.get("eventos", []):
        candidates = [ev["id"], ev["nombre"]] + ev.get("keywords", [])
        score = 0
        for c in candidates:
            if not c:
                continue
            c_norm = _norm(c)
            c_words = set(c_norm.split())

            # Word-boundary match (mejor: el keyword del evento aparece como palabra completa en query)
            if c_words and c_words.issubset(q_words):
                score = max(score, 100 + len(c_norm))
                continue
            # Substring match
            if c_norm in q or q in c_norm:
                score = max(score, 50 + len(c_norm))
                continue
            # Token overlap parcial (al menos 1 palabra común con la keyword)
            overlap = c_words & q_words
            if overlap and len(overlap) >= max(1, len(c_words) // 2):
                score = max(score, 20 + len("".join(overlap)))

        if score > best[0]:
            best = (score, ev)

    return best[1] if best[0] >= 25 else None


# =====================================================================
# 2. CAMPAIGN DETECTOR — keywords "compra y gana", "regístrate y gana"
# =====================================================================

@dataclass
class CampaignSignal:
    tipo: str       # 'compra_y_gana' | 'registrate_gana' | 'sorteo' | 'premio' | 'descuento_codigo'
    keyword_matched: str
    contexto: str   # snippet de 100-200 chars alrededor


def detect_campaign_signals(text: str, max_per_type: int = 3) -> list[CampaignSignal]:
    """Detecta si un texto/HTML contiene señales de campaña promocional activa."""
    if not text:
        return []
    text_low = text.lower()
    keywords = CATALOG.get("_keywords_promocionales", {})
    signals: list[CampaignSignal] = []

    for tipo, kws in keywords.items():
        if tipo == "descripcion":
            continue
        if not isinstance(kws, list):
            continue
        found = 0
        for kw in kws:
            kw_low = kw.lower()
            idx = text_low.find(kw_low)
            if idx >= 0:
                start = max(0, idx - 60)
                end = min(len(text), idx + len(kw_low) + 60)
                contexto = text[start:end].replace("\n", " ").strip()
                signals.append(CampaignSignal(
                    tipo=tipo, keyword_matched=kw, contexto=contexto,
                ))
                found += 1
                if found >= max_per_type:
                    break
    return signals


def build_event_campaign_dorks(evento: dict | EventoActivo,
                                 incluir_campaign: bool = True,
                                 incluir_exclusiones: bool = True) -> list[str]:
    """
    Genera dorks específicos para encontrar empresas con campañas activas en un evento.

    Ej: 'mundial' + 'compra y gana' → '"mundial" "compra y gana" site:.mx'
    """
    ev_data = evento if isinstance(evento, dict) else {
        "keywords": evento.keywords, "dorks_intent": evento.dorks_intent,
    }
    dorks = list(ev_data.get("dorks_intent", []))

    if incluir_campaign:
        kw_promos = CATALOG.get("_keywords_promocionales", {})
        event_kws = ev_data.get("keywords", [])[:3]
        for tipo, kws in kw_promos.items():
            if tipo == "descripcion":
                continue
            if not isinstance(kws, list):
                continue
            promo_kw = kws[0]  # usar el más común de cada tipo
            for ev_kw in event_kws[:2]:
                dorks.append(f'"{ev_kw}" "{promo_kw}" site:.mx')

    if incluir_exclusiones:
        try:
            from src.core.exclusions import get_default_engine
            excl = get_default_engine().build_dork_exclusions()
            dorks = [f"{d} {excl}" for d in dorks]
        except Exception:  # noqa: BLE001
            pass

    return dorks


# =====================================================================
# 3. AGENCY DETECTOR — qué agencia está detrás de una campaña
# =====================================================================

@dataclass
class AgencyFinding:
    dominio_analizado: str
    agencias_detectadas: list[str] = field(default_factory=list)
    rfcs_detectados: list[str] = field(default_factory=list)
    razones_sociales_detectadas: list[str] = field(default_factory=list)
    documentos_analizados: list[str] = field(default_factory=list)
    fuente_metodo: str = ""           # 'pdf_bases' | 'terminos_condiciones' | 'aviso_privacidad'
    error: str = ""


# Regex MX
RFC_RE = re.compile(r"\b([A-Z&Ñ]{3,4}\d{6}[A-Z0-9]{3})\b")
RAZON_SOCIAL_RE = re.compile(
    r"\b([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ &\.,]{4,80}?)\s+(?:S\.?\s*A\.?(?:\s+(?:DE\s+)?C\.?\s*V\.?)?|"
    r"S\.?\s*(?:DE\s+)?R\.?\s*L\.?(?:\s+DE\s+C\.?\s*V\.?)?|SAPI(?:\s+DE\s+C\.?\s*V\.?)?|S\.?\s*C\.?)\b",
)


def detect_agency_in_text(text: str) -> dict:
    """
    Detecta posibles agencias mencionadas en texto de bases/términos/aviso de privacidad.
    Returns dict con: agencias (de catalog), rfcs, razones_sociales.
    """
    out = {"agencias": [], "rfcs": [], "razones_sociales": []}
    if not text:
        return out

    text_norm = text.upper()

    # Agencias conocidas del catálogo
    agencias_catalog = CATALOG.get("_dorks_descubrimiento_agencia", {}).get(
        "agencias_conocidas_mx", [])
    for ag in agencias_catalog:
        if ag.upper() in text_norm:
            out["agencias"].append(ag)

    # RFCs
    for m in RFC_RE.finditer(text):
        rfc = m.group(1).upper()
        if rfc not in out["rfcs"]:
            out["rfcs"].append(rfc)

    # Razones sociales con sufijo legal
    for m in RAZON_SOCIAL_RE.finditer(text):
        rs = m.group(0).strip()
        if 8 < len(rs) < 100 and rs not in out["razones_sociales"]:
            out["razones_sociales"].append(rs)

    # Dedup
    out["agencias"] = sorted(set(out["agencias"]))
    out["rfcs"] = sorted(set(out["rfcs"]))[:5]
    out["razones_sociales"] = sorted(set(out["razones_sociales"]))[:8]
    return out


def find_agency_behind_campaign(
    dominio: str,
    nombre_campana: str = "",
    max_docs: int = 5,
    timeout: int = 20,
) -> AgencyFinding:
    """
    Busca la agencia detrás de una campaña haciendo:
    1. Dorks site:dominio "bases" / "terminos" / "aviso de privacidad"
    2. Descarga PDFs y páginas legales
    3. Aplica detect_agency_in_text() a cada doc
    """
    finding = AgencyFinding(dominio_analizado=dominio)
    dominio_clean = dominio.replace("https://", "").replace("http://", "").lstrip("www.").rstrip("/")

    try:
        from src.sources.search_backends import get_default_manager
        mgr = get_default_manager()
    except Exception as e:  # noqa: BLE001
        finding.error = f"search_backend_unavailable: {e}"
        return finding

    # 1. Generar dorks
    dork_patterns = CATALOG.get("_dorks_descubrimiento_agencia", {}).get(
        "patterns_por_dominio", [])
    urls_a_revisar: set[str] = set()

    for pattern in dork_patterns:
        dork = pattern.replace("{dominio}", dominio_clean)
        try:
            results = mgr.search(dork, limit=10, country="mx", avoid_paid=True)
        except Exception:  # noqa: BLE001
            continue
        for r in results[:max_docs]:
            if r.url and dominio_clean in r.url:
                urls_a_revisar.add(r.url)

    # 2. Descargar y analizar cada doc
    agencias_acum: set[str] = set()
    rfcs_acum: set[str] = set()
    rs_acum: set[str] = set()
    docs_revisados: list[str] = []

    for url in list(urls_a_revisar)[:max_docs]:
        try:
            r = requests.get(url, timeout=timeout,
                              headers={"User-Agent": random_ua()},
                              allow_redirects=True)
            if not r.ok:
                continue
            content_type = r.headers.get("Content-Type", "").lower()

            text = ""
            if "pdf" in content_type or url.lower().endswith(".pdf"):
                # PDF: requiere pypdf opcional
                try:
                    import pypdf
                    from io import BytesIO
                    reader = pypdf.PdfReader(BytesIO(r.content))
                    text = "\n".join(p.extract_text() or "" for p in reader.pages[:10])
                except Exception:  # noqa: BLE001
                    text = r.text  # fallback con bytes crudos
            else:
                text = r.text[:300_000]

            detected = detect_agency_in_text(text)
            agencias_acum.update(detected["agencias"])
            rfcs_acum.update(detected["rfcs"])
            rs_acum.update(detected["razones_sociales"])
            docs_revisados.append(url)
        except Exception as e:  # noqa: BLE001
            logger.debug("AgencyDetector err %s: %s", url, e)

    finding.agencias_detectadas = sorted(agencias_acum)
    finding.rfcs_detectados = sorted(rfcs_acum)[:5]
    finding.razones_sociales_detectadas = sorted(rs_acum)[:8]
    finding.documentos_analizados = docs_revisados
    return finding


# =====================================================================
# 4. Helpers para Discovery
# =====================================================================

def suggest_event_campaign_search(user_query: str) -> dict:
    """
    Dado el input del usuario ('leads del mundial con compra y gana'),
    devuelve un plan estructurado con:
      - evento detectado
      - señales de campaña detectadas
      - dorks sugeridos
      - categorías target sugeridas
    """
    evento = find_evento_by_keyword(user_query)
    if not evento:
        return {
            "evento_detectado": None,
            "sugerencia": "No se reconoció ningún evento. Eventos disponibles: " +
                          ", ".join(e["id"] for e in CATALOG.get("eventos", [])[:10]),
        }

    # ¿el usuario menciona keywords promocionales?
    query_low = user_query.lower()
    campaign_types_detected = []
    for tipo, kws in CATALOG.get("_keywords_promocionales", {}).items():
        if tipo == "descripcion" or not isinstance(kws, list):
            continue
        for kw in kws:
            if kw.lower() in query_low:
                campaign_types_detected.append(tipo)
                break

    # Generar dorks
    dorks = build_event_campaign_dorks(evento, incluir_campaign=bool(campaign_types_detected))

    return {
        "evento_detectado": {
            "id": evento["id"],
            "nombre": evento["nombre"],
            "categorias_target": evento.get("categorias_target", []),
        },
        "campaign_types_detected": campaign_types_detected,
        "dorks_sugeridos": dorks[:10],
        "siguiente_paso": "ejecutar dorks vía search_backends + crawl + detect_campaign_signals",
    }


__all__ = [
    "EventoActivo", "CampaignSignal", "AgencyFinding",
    "get_eventos_activos", "find_evento_by_keyword",
    "detect_campaign_signals", "build_event_campaign_dorks",
    "detect_agency_in_text", "find_agency_behind_campaign",
    "suggest_event_campaign_search",
]
