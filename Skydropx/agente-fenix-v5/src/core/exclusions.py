"""
Motor de exclusiones en 3 capas para Skydropx Outbound:

CAPA 1 — Técnica (Calidad del Lead)
  Filtra ruido: wikipedia, imágenes, 404, demos, páginas legales.

CAPA 2 — Contexto MX
  Respeta políticas: .gob.mx, directorios expuestos, terms/privacy.

CAPA 3 — ICP Outbound Skydropx
  Excluye empresas que no cierran outbound:
  - Competencia logística directa (DHL, Estafeta, 99minutos, etc.)
  - Marketplaces (Amazon, ML, Walmart, Liverpool)
  - Sectores fuera del ICP (financiero, transporte/viajes, construcción pesada,
    telecom/media grandes, automotriz, gobierno/educación pública)
  - Tamaño Estrato 7 (251+ empleados) — opcionalmente Estrato 6

Cada exclusión devuelve un motivo legible para auditoría/trazabilidad.

REGLA DE ORO: cualquier RawRecord que dispare una exclusión se descarta ANTES
de persistirse en la DB. Quedan en raw_findings para auditoría.

Override: el usuario puede pasar `--include-excluded-categories X,Y` o
`--include-large` para sobreescribir reglas específicas.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

EXCLUSIONS_PATH = Path(__file__).resolve().parents[2] / "data" / "exclusions_skydropx.json"


# ---------------- Carga del catálogo ----------------

def _load_catalog() -> dict:
    if not EXCLUSIONS_PATH.exists():
        logger.warning("exclusions_skydropx.json no encontrado en %s", EXCLUSIONS_PATH)
        return {}
    return json.loads(EXCLUSIONS_PATH.read_text(encoding="utf-8"))


# ---------------- Normalizadores ----------------

def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def normalize_name_for_match(s: str | None) -> str:
    if not s:
        return ""
    s = _strip_accents(s).lower().strip()
    # quitar sufijos legales mexicanos
    # Sufijos legales MX: SA, SA de CV, SAB de CV, S de RL, SAPI, SC, etc.
    s = re.sub(r"\b(s\.?\s*a\.?\s*b?\.?(\s+de\s+c\.?\s*v\.?)?|sapi(\s+de\s+c\.?\s*v\.?)?|s\.?\s*c\.?|s\.?\s*en\s+c\.?|s\.?\s*(de\s+)?r\.?\s*l\.?(\s+de\s+c\.?\s*v\.?)?)\b", "", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_domain_for_match(s: str | None) -> str:
    if not s:
        return ""
    s = s.strip().lower()
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^www\.", "", s)
    s = s.rstrip("/").split("/")[0]
    return s


# ---------------- Fuzzy match opcional ----------------

try:
    from difflib import SequenceMatcher
    def _fuzzy(a: str, b: str) -> int:
        if not a or not b:
            return 0
        return int(SequenceMatcher(None, a, b).ratio() * 100)
except ImportError:
    def _fuzzy(a: str, b: str) -> int:
        return 100 if a == b else 0


# ---------------- Resultado ----------------

@dataclass
class ExclusionResult:
    excluded: bool = False
    layer: str = ""              # 'tecnica' | 'mx' | 'icp_skydropx'
    category: str = ""           # 'competencia_logistica' | 'marketplace' | etc.
    signal: str = ""             # 'EXCLUIR_COMPETENCIA' etc.
    reason: str = ""             # mensaje legible
    matched_value: str = ""      # qué valor disparó la exclusión

    def to_dict(self) -> dict:
        return {
            "excluded": self.excluded, "layer": self.layer,
            "category": self.category, "signal": self.signal,
            "reason": self.reason, "matched_value": self.matched_value,
        }


# ---------------- Motor ----------------

class ExclusionEngine:
    """Aplica las 3 capas de exclusiones. Configurable por instancia."""

    def __init__(
        self,
        catalog: dict | None = None,
        include_categories: list[str] | None = None,
        include_large: bool = False,
        include_medianas_grandes: bool = False,
        fuzzy_threshold: int = 88,
        custom_excludes: list[str] | None = None,
    ):
        """
        Args:
            include_categories: lista de categorías a NO excluir (override)
                                ej: ["financiero_bancos_seguros"] permite leads de bancos
            include_large: si True, permite empresas Estrato 7 (251+ empleados)
            include_medianas_grandes: si True, permite Estrato 6 (101-250)
            fuzzy_threshold: umbral fuzzy match nombre (default 88)
            custom_excludes: lista adicional de nombres/dominios a excluir
        """
        self.catalog = catalog if catalog is not None else _load_catalog()
        self.include_categories = set(include_categories or [])
        self.include_large = include_large
        self.include_medianas_grandes = include_medianas_grandes
        self.fuzzy_threshold = fuzzy_threshold
        self.custom_excludes_names = set(
            normalize_name_for_match(x) for x in (custom_excludes or [])
        )

        # Precomputar sets normalizados para lookup O(1)
        self._build_lookups()

    def _build_lookups(self) -> None:
        """Construye índices normalizados de empresas/dominios por categoría."""
        self.by_category: dict[str, dict[str, set[str]]] = {}
        for cat_key, cat_data in self.catalog.items():
            if cat_key.startswith("_") or not isinstance(cat_data, dict):
                continue
            if cat_key in self.include_categories:
                continue  # override: el usuario quiere esta categoría
            entry = {"empresas": set(), "dominios": set(),
                     "dominios_patron": set(), "scianes": set(),
                     "url_patterns": set(), "title_patterns": set(),
                     "filetype_excludes": set(), "estratos": set()}

            for emp in cat_data.get("empresas", []):
                entry["empresas"].add(normalize_name_for_match(emp))
            for dom in cat_data.get("dominios", []):
                entry["dominios"].add(normalize_domain_for_match(dom))
            for p in cat_data.get("dominios_patron", []):
                entry["dominios_patron"].add(p.lower())
            for s in (cat_data.get("scianes") or {}):
                entry["scianes"].add(str(s))
            for u in cat_data.get("url_patterns", []):
                entry["url_patterns"].add(u.lower())
            for t in cat_data.get("title_patterns", []):
                entry["title_patterns"].add(t.lower())
            for ft in cat_data.get("filetype_excludes", []):
                entry["filetype_excludes"].add(ft.lower())
            for e in cat_data.get("denue_estrato_excluir", []):
                entry["estratos"].add(str(e))

            self.by_category[cat_key] = entry

    # ============================================================
    # CAPA 1: Técnica (calidad del lead)
    # ============================================================

    def check_layer_tecnica(self, *, url: str = "", title: str = "",
                            text: str = "", filetype: str = "") -> ExclusionResult:
        ruido = self.by_category.get("patrones_ruido_url", {})
        if not ruido:
            return ExclusionResult()

        # filetype excluido
        if filetype:
            ft = filetype.lower().lstrip(".")
            if ft in ruido.get("filetype_excludes", set()):
                return ExclusionResult(
                    excluded=True, layer="tecnica", category="patrones_ruido_url",
                    signal="RUIDO_FILETYPE",
                    reason=f"filetype '{ft}' está en exclusiones (imagen/video sin contacto)",
                    matched_value=ft,
                )

        # URL patterns
        url_low = url.lower()
        for pat in ruido.get("url_patterns", set()):
            if pat in url_low:
                return ExclusionResult(
                    excluded=True, layer="tecnica", category="patrones_ruido_url",
                    signal="RUIDO_URL",
                    reason=f"URL contiene patrón ruido '{pat}'",
                    matched_value=pat,
                )

        # Title patterns
        title_low = title.lower()
        for pat in ruido.get("title_patterns", set()):
            if pat in title_low:
                return ExclusionResult(
                    excluded=True, layer="tecnica", category="patrones_ruido_url",
                    signal="RUIDO_TITLE",
                    reason=f"title indica error/maintenance: '{pat}'",
                    matched_value=pat,
                )

        # Text patterns (texto del sitio)
        if text:
            text_low = text[:5000].lower()
            for pat in ruido.get("text_patterns", set()):
                if pat in text_low:
                    return ExclusionResult(
                        excluded=True, layer="tecnica", category="patrones_ruido_url",
                        signal="RUIDO_TEXTO",
                        reason=f"texto indica sitio de prueba: '{pat}'",
                        matched_value=pat,
                    )
        return ExclusionResult()

    # ============================================================
    # CAPA 2: Contexto MX (gobierno, directorios expuestos)
    # ============================================================

    def check_layer_mx(self, *, url: str = "", domain: str = "") -> ExclusionResult:
        gob = self.by_category.get("gobierno_educacion_publicas", {})
        if gob:
            dom = normalize_domain_for_match(domain or urlparse(url).netloc)
            for pat in gob.get("dominios_patron", set()):
                if dom.endswith(pat) or pat in dom:
                    return ExclusionResult(
                        excluded=True, layer="mx", category="gobierno_educacion_publicas",
                        signal="EXCLUIR_PUBLICO",
                        reason=f"dominio gubernamental/educacional: {dom}",
                        matched_value=dom,
                    )
        return ExclusionResult()

    # ============================================================
    # CAPA 3: ICP Outbound Skydropx
    # ============================================================

    def check_layer_icp(self, *, empresa: str = "", dominio: str = "",
                        scian: str = "", estrato: str = "") -> ExclusionResult:
        emp_norm = normalize_name_for_match(empresa)
        dom_norm = normalize_domain_for_match(dominio)

        # Custom excludes del usuario
        if emp_norm and emp_norm in self.custom_excludes_names:
            return ExclusionResult(
                excluded=True, layer="icp_skydropx", category="custom",
                signal="EXCLUIR_CUSTOM",
                reason=f"empresa en lista custom del usuario: {empresa}",
                matched_value=empresa,
            )

        # SCIAN excluidos (validar primero — más rápido)
        if scian:
            scian_str = str(scian)
            for cat_key, entry in self.by_category.items():
                scianes_excl = entry.get("scianes", set())
                if not scianes_excl:
                    continue
                # Match por prefijo (ej: scian '4641' contiene '46' → excluido si '46' está)
                for excl in scianes_excl:
                    if scian_str.startswith(excl):
                        cat_data = self.catalog.get(cat_key, {})
                        return ExclusionResult(
                            excluded=True, layer="icp_skydropx", category=cat_key,
                            signal=cat_data.get("signal", "EXCLUIR_SCIAN"),
                            reason=f"SCIAN {scian} pertenece a sector excluido ({excl}: {cat_data.get('scianes',{}).get(excl,'')})",
                            matched_value=scian,
                        )

        # Tamaño (estrato DENUE)
        if estrato:
            estrato_str = str(estrato)
            tam_cat = self.by_category.get("tamano_maximo_outbound", {})
            if estrato_str in tam_cat.get("estratos", set()):
                if estrato_str == "7" and not self.include_large:
                    return ExclusionResult(
                        excluded=True, layer="icp_skydropx",
                        category="tamano_maximo_outbound",
                        signal="EXCLUIR_TAMANO_GRANDE",
                        reason=f"Estrato {estrato} (251+ empleados) fuera de ICP outbound",
                        matched_value=estrato_str,
                    )
                if estrato_str == "6" and not self.include_medianas_grandes:
                    return ExclusionResult(
                        excluded=True, layer="icp_skydropx",
                        category="tamano_maximo_outbound",
                        signal="EXCLUIR_TAMANO_MEDIANA_GRANDE",
                        reason=f"Estrato {estrato} (101-250) requiere --include-medianas-grandes",
                        matched_value=estrato_str,
                    )

        # Dominios exactos
        if dom_norm:
            for cat_key, entry in self.by_category.items():
                if dom_norm in entry.get("dominios", set()):
                    cat_data = self.catalog.get(cat_key, {})
                    return ExclusionResult(
                        excluded=True, layer="icp_skydropx", category=cat_key,
                        signal=cat_data.get("signal", "EXCLUIR_DOMINIO"),
                        reason=f"dominio {dom_norm} en lista de exclusión '{cat_key}'",
                        matched_value=dom_norm,
                    )

        # Empresas: exact match primero
        if emp_norm:
            for cat_key, entry in self.by_category.items():
                if emp_norm in entry.get("empresas", set()):
                    cat_data = self.catalog.get(cat_key, {})
                    return ExclusionResult(
                        excluded=True, layer="icp_skydropx", category=cat_key,
                        signal=cat_data.get("signal", "EXCLUIR_NOMBRE"),
                        reason=f"empresa '{empresa}' en lista de exclusión '{cat_key}'",
                        matched_value=empresa,
                    )

            # Substring word-boundary + Fuzzy match
            # Si "estafeta" del catalog aparece como palabra en "estafeta mexican" → match
            if len(emp_norm) >= 3:
                emp_words = set(emp_norm.split())
                for cat_key, entry in self.by_category.items():
                    for excl_emp in entry.get("empresas", set()):
                        if len(excl_emp) < 3:
                            continue
                        # 1. Subset match: nombre del catálogo contenido como palabras en el real
                        excl_words = set(excl_emp.split())
                        if excl_words and excl_words.issubset(emp_words):
                            cat_data = self.catalog.get(cat_key, {})
                            return ExclusionResult(
                                excluded=True, layer="icp_skydropx", category=cat_key,
                                signal=cat_data.get("signal", "EXCLUIR_NOMBRE_SUBSET"),
                                reason=f"'{empresa}' contiene '{excl_emp}' (subset) en '{cat_key}'",
                                matched_value=excl_emp,
                            )
                        # 2. Fuzzy completo solo si longitud comparable (evita falsos positivos)
                        if abs(len(emp_norm) - len(excl_emp)) < 10:
                            score = _fuzzy(emp_norm, excl_emp)
                            if score >= self.fuzzy_threshold:
                                cat_data = self.catalog.get(cat_key, {})
                                return ExclusionResult(
                                    excluded=True, layer="icp_skydropx", category=cat_key,
                                    signal=cat_data.get("signal", "EXCLUIR_NOMBRE_FUZZY"),
                                    reason=f"'{empresa}' ~ '{excl_emp}' ({score}%) en '{cat_key}'",
                                    matched_value=excl_emp,
                                )
        return ExclusionResult()

    # ============================================================
    # API principal: chequea las 3 capas
    # ============================================================

    def check_all(self, **kwargs) -> ExclusionResult:
        """
        Aplica las 3 capas en orden. Devuelve el primer hit.
        kwargs aceptados: empresa, dominio, url, title, text, filetype, scian, estrato.
        """
        # CAPA 1: Técnica
        r = self.check_layer_tecnica(
            url=kwargs.get("url", ""),
            title=kwargs.get("title", ""),
            text=kwargs.get("text", ""),
            filetype=kwargs.get("filetype", ""),
        )
        if r.excluded:
            return r

        # CAPA 2: MX
        r = self.check_layer_mx(
            url=kwargs.get("url", ""),
            domain=kwargs.get("dominio", ""),
        )
        if r.excluded:
            return r

        # CAPA 3: ICP Skydropx
        return self.check_layer_icp(
            empresa=kwargs.get("empresa", ""),
            dominio=kwargs.get("dominio", ""),
            scian=kwargs.get("scian", ""),
            estrato=kwargs.get("estrato", ""),
        )

    def check_raw_record(self, record) -> ExclusionResult:
        """Atajo: aplica check_all sobre un RawRecord directo."""
        meta = record.metadata or {}
        return self.check_all(
            empresa=record.empresa or record.nombre_comercial or "",
            dominio=record.sitio_web or "",
            url=record.sitio_web or meta.get("url_origen") or "",
            scian=record.scian or "",
            estrato=str(meta.get("estrato_id") or meta.get("estrato") or ""),
        )

    # ============================================================
    # Generación de dorks con exclusión integrada
    # ============================================================

    def build_dork_exclusions(self) -> str:
        """
        Construye el sufijo de exclusiones para Google Dorks.
        Ej: '-site:wikipedia.org -site:amazon.com.mx -filetype:png ...'
        """
        excludes = []
        ruido = self.by_category.get("patrones_ruido_url", {})

        # filetypes
        for ft in list(ruido.get("filetype_excludes", set()))[:4]:  # cap
            excludes.append(f"-filetype:{ft}")

        # URL patterns críticos
        url_critical = ["wikipedia.org", "linkedin.com/company"]
        for pat in url_critical:
            if pat in ruido.get("url_patterns", set()):
                excludes.append(f"-site:{pat}" if "." in pat else f"-inurl:{pat}")

        # Top dominios competencia
        comp = self.by_category.get("competencia_logistica", {})
        for dom in list(comp.get("dominios", set()))[:5]:
            excludes.append(f"-site:{dom}")

        # Top marketplaces
        mp = self.by_category.get("marketplaces", {})
        for dom in list(mp.get("dominios", set()))[:5]:
            excludes.append(f"-site:{dom}")

        # Gobierno
        for pat in [".gob.mx", ".edu.mx"]:
            excludes.append(f"-site:{pat}")

        return " ".join(excludes)

    # ============================================================
    # Bulk filtering
    # ============================================================

    def filter_records(self, records: list) -> tuple[list, list[dict]]:
        """
        Devuelve (records_aceptados, registros_excluidos_con_motivo).
        """
        accepted = []
        excluded = []
        for r in records:
            res = self.check_raw_record(r)
            if res.excluded:
                excluded.append({
                    "fingerprint": r.fingerprint(),
                    "empresa": r.empresa,
                    "source": r.source,
                    **res.to_dict(),
                })
            else:
                accepted.append(r)
        return accepted, excluded


# ---------------- Singleton de conveniencia ----------------

_default: ExclusionEngine | None = None


def get_default_engine() -> ExclusionEngine:
    global _default
    if _default is None:
        _default = ExclusionEngine()
    return _default


def reset_default_engine() -> None:
    """Útil para tests o cuando cambian flags."""
    global _default
    _default = None


__all__ = [
    "ExclusionEngine", "ExclusionResult",
    "get_default_engine", "reset_default_engine",
    "normalize_name_for_match", "normalize_domain_for_match",
]
