"""
Clasificador ICP dual para Skydropx Outbound.

Dos perfiles distintos de cliente ideal:

ICP-1: D2C / PyME / Emprendedores con 50-100 envíos/mes
    → Vendedor online típico con su Shopify/Tiendanube/WooCommerce
    → Plan PyME o Starter
    → Necesidad: cotizador + automatización + integración ecommerce
    → Señales: plataforma ecommerce, intent envíos, Meta Pixel, MercadoPago,
      tamaño Micro/Pequeña/Mediana, marketplace seller

ICP-2: Enterprise / 3PL / Agencias / B2B grande
    → Operador logístico, agencia digital, manufactura mediana con distribución B2B
    → Plan Enterprise
    → Necesidad: API + Webhooks + Convenios tarifarios + multi-cuenta
    → Señales: "3PL", "fulfillment", "logistica", "agencia digital", "marketing",
      tamaño Mediana/Grande, B2B en giro

Cada lead recibe:
    - icp_segment: ICP_1_PYME | ICP_2_ENTERPRISE | ICP_3_C2C | NO_ICP
    - icp_score: 0-100 confianza
    - envios_estimados: rango ("0-50" | "50-100" | "100-500" | "500-5000" | "5000+")
    - skydropx_plan: Starter | PyME | Enterprise
    - vertical: ecommerce | 3pl | agencia | marketplace_seller | fabricante | otro

Sin IA: heurísticas determinísticas basadas en SCIAN + tamaño + señales del Hunter.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------- Enums ----------------

class IcpSegment(str, Enum):
    ICP_1_PYME = "ICP_1_PYME"             # 50-100 envíos/mes
    ICP_2_ENTERPRISE = "ICP_2_ENTERPRISE" # 500+ envíos / B2B / 3PL / Agencia
    ICP_3_C2C = "ICP_3_C2C"               # vendedor esporádico (ML/MP)
    NO_ICP = "NO_ICP"                     # no califica


class Vertical(str, Enum):
    ECOMMERCE_D2C = "ecommerce_d2c"
    MARKETPLACE_SELLER = "marketplace_seller"
    PYME_RETAIL = "pyme_retail"
    PYME_MAYORISTA = "pyme_mayorista"
    FABRICANTE = "fabricante"
    LOGISTICA_3PL = "3pl_fulfillment"
    AGENCIA_MARKETING = "agencia_marketing"
    SERVICIOS_PROFESIONALES = "servicios_profesionales"
    OTRO = "otro"


# ---------------- Reglas de detección ----------------

# Palabras clave en razón social / nombre comercial / giro
KEYWORDS_3PL = [
    r"\b3pl\b", r"fulfillment", r"logistic[ao]s?", r"almacenaje",
    r"distribuci[óo]n", r"crossdock", r"cross\s*dock", r"warehous",
    r"centro\s+de\s+distribuci[óo]n", r"cedis", r"operador\s+log[íi]stico",
    r"\bWMS\b", r"\bTMS\b", r"order\s*management",
]
KEYWORDS_AGENCIA = [
    r"agencia\s+(de\s+)?(marketing|digital|publicidad|medios|creativa)",
    r"marketing\s+(digital|360|integral)", r"branding\s+agency",
    r"performance\s+marketing", r"growth\s+(agency|marketing)",
    r"social\s+media\s+(agency|marketing)", r"\bagency\b",
    r"agencia\s+de\s+influencers", r"PR\s+agency", r"relaciones\s+p[úu]blicas",
]
KEYWORDS_FABRICANTE = [
    r"fabricant[ae]s?", r"manufactura", r"manufacturing",
    r"productor[ae]s?\s+de", r"f[áa]brica\s+de", r"industrias?",
    r"maquila", r"taller\s+de", r"transformaci[óo]n",
]
KEYWORDS_MAYORISTA = [
    r"mayorista", r"mayoreo", r"al\s+por\s+mayor",
    r"wholesale", r"distribuidor[ae]s?", r"importador[ae]s?",
    r"comercializador[ae]s?", r"abasto",
]
KEYWORDS_ECOMMERCE = [
    r"tienda\s+(en\s+l[íi]nea|online|virtual|digital)",
    r"e-?commerce", r"venta\s+(en\s+l[íi]nea|por\s+internet|online)",
    r"shop", r"\bstore\b",
]

# SCIAN → vertical sugerido (mapeo grueso)
SCIAN_VERTICAL_MAP = {
    "31": Vertical.FABRICANTE,
    "32": Vertical.FABRICANTE,
    "33": Vertical.FABRICANTE,
    "43": Vertical.PYME_MAYORISTA,        # Comercio al por mayor
    "46": Vertical.PYME_RETAIL,            # Comercio al por menor
    "4541": Vertical.ECOMMERCE_D2C,        # Comercio por internet/catálogo
    "4931": Vertical.LOGISTICA_3PL,        # Almacenamiento
    "541": Vertical.SERVICIOS_PROFESIONALES,
    "5418": Vertical.AGENCIA_MARKETING,   # Servicios de publicidad
}

# Tech stack → señales ICP (con peso)
TECH_ICP_WEIGHTS = {
    # ICP-1 (PyME ecommerce)
    "shopify": ("ICP_1_PYME", 20),
    "tiendanube": ("ICP_1_PYME", 25),
    "woocommerce": ("ICP_1_PYME", 18),
    "jumpseller": ("ICP_1_PYME", 20),
    "wix_stores": ("ICP_1_PYME", 12),
    "klaviyo": ("ICP_1_PYME", 15),
    "meta_pixel": ("ICP_1_PYME", 10),
    "mercadopago": ("ICP_1_PYME", 12),
    "conekta": ("ICP_1_PYME", 10),
    "openpay": ("ICP_1_PYME", 10),
    "tiktok_pixel": ("ICP_1_PYME", 8),
    "whatsapp_business": ("ICP_1_PYME", 10),

    # ICP-2 (Enterprise / B2B)
    "vtex": ("ICP_2_ENTERPRISE", 25),
    "magento": ("ICP_2_ENTERPRISE", 20),
    "shopify_plus": ("ICP_2_ENTERPRISE", 30),
    "hubspot": ("ICP_2_ENTERPRISE", 15),
    "salesforce": ("ICP_2_ENTERPRISE", 20),
}

# Estrato DENUE → rango envíos estimado (heurística conservadora)
ESTRATO_ENVIOS_ESTIMADOS = {
    "1": "0-50",     # 0-5 empleados
    "2": "10-100",   # 6-10
    "3": "50-300",   # 11-30
    "4": "100-800",  # 31-50
    "5": "300-2000", # 51-100
    "6": "1000-5000",# 101-250
    "7": "5000+",    # 251+
}

# Estrato → plan sugerido por default
ESTRATO_PLAN_DEFAULT = {
    "1": "Starter",
    "2": "Starter",
    "3": "PyME",
    "4": "PyME",
    "5": "PyME",
    "6": "Enterprise",
    "7": "Enterprise",
}


# ---------------- Resultado ----------------

@dataclass
class IcpClassification:
    icp_segment: str = IcpSegment.NO_ICP.value
    icp_score: int = 0
    vertical: str = Vertical.OTRO.value
    envios_estimados: str = "0-50"
    skydropx_plan: str = "Starter"
    value_proposition: str = ""
    razones: list[str] = field(default_factory=list)
    signals: dict[str, int] = field(default_factory=dict)  # señal → peso aportado

    def to_dict(self) -> dict:
        return {
            "icp_segment": self.icp_segment,
            "icp_score": self.icp_score,
            "vertical": self.vertical,
            "envios_estimados": self.envios_estimados,
            "skydropx_plan": self.skydropx_plan,
            "value_proposition": self.value_proposition,
            "razones": self.razones,
            "signals": self.signals,
        }


# ---------------- Clasificador principal ----------------

def _match_any(text: str, patterns: list[str]) -> str | None:
    """Devuelve el primer patrón que matchea, o None."""
    text = (text or "").lower()
    for p in patterns:
        if re.search(p, text):
            return p
    return None


def _get_text_for_matching(lead: dict[str, Any]) -> str:
    """Concatena campos de texto para matching de keywords."""
    parts = [
        lead.get("empresa", ""),
        lead.get("nombre", ""),
        lead.get("nombre_comercial", ""),
        lead.get("giro", ""),
        lead.get("giro_descripcion", ""),
    ]
    return " | ".join(str(p) for p in parts if p).lower()


def classify_icp(lead: dict[str, Any]) -> IcpClassification:
    """
    Clasifica un lead en ICP-1, ICP-2, ICP-3 o NO_ICP.

    Args:
        lead: dict con campos del Lead/RawRecord (empresa, scian, tamano,
              source, metadata, etc.)
    """
    cls = IcpClassification()
    score_icp_1 = 0   # PyME 50-100 envíos
    score_icp_2 = 0   # Enterprise / 3PL / Agencia
    score_icp_3 = 0   # C2C esporádico

    meta = lead.get("metadata") or lead.get("_raw_records", [{}])[0].get("metadata", {}) if isinstance(lead.get("_raw_records"), list) else (lead.get("metadata") or {})

    text = _get_text_for_matching(lead)
    scian = str(lead.get("scian") or "")
    tamano = (lead.get("tamano") or "").lower()
    estrato = str(meta.get("estrato_id") or "")
    source = (lead.get("source") or lead.get("fuentes") or "").lower()

    # === Detección de vertical por keywords ===

    if _match_any(text, KEYWORDS_3PL):
        cls.vertical = Vertical.LOGISTICA_3PL.value
        score_icp_2 += 40
        cls.razones.append("keyword 3PL/logística detectado")
        cls.signals["keyword_3pl"] = 40

    elif _match_any(text, KEYWORDS_AGENCIA):
        cls.vertical = Vertical.AGENCIA_MARKETING.value
        score_icp_2 += 35
        cls.razones.append("agencia de marketing/digital detectada")
        cls.signals["keyword_agencia"] = 35

    elif _match_any(text, KEYWORDS_FABRICANTE):
        cls.vertical = Vertical.FABRICANTE.value
        score_icp_2 += 25
        cls.razones.append("fabricante/manufactura detectado")
        cls.signals["keyword_fabricante"] = 25

    elif _match_any(text, KEYWORDS_MAYORISTA):
        cls.vertical = Vertical.PYME_MAYORISTA.value
        score_icp_2 += 20
        score_icp_1 += 10
        cls.razones.append("mayorista/distribuidor detectado")
        cls.signals["keyword_mayorista"] = 20

    elif _match_any(text, KEYWORDS_ECOMMERCE):
        cls.vertical = Vertical.ECOMMERCE_D2C.value
        score_icp_1 += 20
        cls.razones.append("ecommerce/tienda online detectado")
        cls.signals["keyword_ecommerce"] = 20

    # === SCIAN → vertical y score base ===

    for prefix, vertical_default in SCIAN_VERTICAL_MAP.items():
        if scian.startswith(prefix):
            if cls.vertical == Vertical.OTRO.value:
                cls.vertical = vertical_default.value
            if vertical_default == Vertical.LOGISTICA_3PL:
                score_icp_2 += 30
                cls.signals["scian_3pl"] = 30
            elif vertical_default == Vertical.AGENCIA_MARKETING:
                score_icp_2 += 25
                cls.signals["scian_agencia"] = 25
            elif vertical_default == Vertical.FABRICANTE:
                score_icp_2 += 15
                score_icp_1 += 10
                cls.signals["scian_fabricante"] = 15
            elif vertical_default in (Vertical.PYME_RETAIL, Vertical.PYME_MAYORISTA):
                score_icp_1 += 25
                cls.signals["scian_retail"] = 25
            elif vertical_default == Vertical.ECOMMERCE_D2C:
                score_icp_1 += 35
                cls.signals["scian_ecommerce_puro"] = 35
            break

    # === Tech stack del Hunter ===

    tech_list = meta.get("tech_stack") or []
    for tech in tech_list:
        if tech in TECH_ICP_WEIGHTS:
            target_icp, weight = TECH_ICP_WEIGHTS[tech]
            if target_icp == "ICP_1_PYME":
                score_icp_1 += weight
            elif target_icp == "ICP_2_ENTERPRISE":
                score_icp_2 += weight
            cls.signals[f"tech_{tech}"] = weight
            cls.razones.append(f"tech {tech} → {target_icp} (+{weight})")

    # === Maturity score del Hunter ===

    maturity = int(meta.get("maturity_score", 0) or 0)
    if maturity >= 70:
        score_icp_1 += 10
        score_icp_2 += 5
        cls.signals["high_maturity_digital"] = 10
        cls.razones.append(f"maturity_score={maturity} (digital maduro)")
    elif maturity >= 40:
        score_icp_1 += 5
        cls.signals["medium_maturity_digital"] = 5

    # === Source Mercado Libre → ICP-3 default ===

    if "mercadolibre" in source:
        score_icp_3 += 40
        cls.vertical = Vertical.MARKETPLACE_SELLER.value
        cls.signals["source_ml"] = 40
        cls.razones.append("vendedor Mercado Libre detectado")
        # Si tiene volumen ML alto, sube a ICP_1
        ml_tx = int(meta.get("ml_tx_completed", 0) or 0)
        if ml_tx >= 500:
            score_icp_1 += 45     # ML high-volume = candidato PyME claro
            score_icp_3 -= 20
            cls.signals["ml_high_volume"] = 45
            cls.razones.append(f"ML con {ml_tx} transacciones → upgrade a PyME 50-100 envíos/mes")
        elif ml_tx >= 100:
            score_icp_1 += 25
            score_icp_3 -= 10
            cls.signals["ml_medium_volume"] = 25
            cls.razones.append(f"ML con {ml_tx} transacciones → señal PyME")

    # === Envíos intent ===

    if meta.get("envios_intent"):
        score_icp_1 += 15
        cls.signals["envios_intent"] = 15
        cls.razones.append("menciona 'envíos a toda la república'")

    # === Paqaqueterías mencionadas (ya usa competencia → oportunidad de migrar) ===

    paqs = meta.get("paqueterias_mencionadas") or []
    competencia = [p for p in paqs if p != "skydropx"]
    if competencia:
        score_icp_1 += 15
        score_icp_2 += 10  # también oportunidad enterprise
        cls.signals["ya_usa_competencia"] = 15
        cls.razones.append(f"ya usa: {','.join(competencia)} → oportunidad migración")

    # === Tamaño (estrato DENUE) ===

    if estrato in ("1", "2"):
        score_icp_1 += 8
        score_icp_3 += 15
        cls.signals[f"estrato_{estrato}_micro"] = 8
    elif estrato in ("3", "4"):
        score_icp_1 += 18      # SWEET SPOT 50-100 envíos
        cls.signals[f"estrato_{estrato}_pequena"] = 18
        cls.razones.append(f"estrato {estrato} = sweet spot PyME 50-100 envíos")
    elif estrato == "5":
        score_icp_1 += 12
        score_icp_2 += 10
        cls.signals["estrato_5_mediana"] = 12
    elif estrato == "6":
        score_icp_2 += 25
        cls.signals["estrato_6_mediana_grande"] = 25
        cls.razones.append("estrato 6 (101-250) → Enterprise candidate")
    elif estrato == "7":
        score_icp_2 += 35
        cls.signals["estrato_7_grande"] = 35
        cls.razones.append("estrato 7 (251+) → Enterprise puro")

    # ============================================================
    # Decisión final
    # ============================================================

    cls.signals["_score_icp_1"] = score_icp_1
    cls.signals["_score_icp_2"] = score_icp_2
    cls.signals["_score_icp_3"] = score_icp_3

    # === Decisión: el segmento gana por score relativo ===
    # Thresholds:
    #   ICP_1 (PyME):       ≥ 30 puntos
    #   ICP_2 (Enterprise): ≥ 40 puntos
    #   ICP_3 (C2C):        ≥ 25 puntos
    # Tiebreaker: el de mayor score absoluto

    candidates = []
    if score_icp_2 >= 40:
        candidates.append((IcpSegment.ICP_2_ENTERPRISE, score_icp_2))
    if score_icp_1 >= 30:
        candidates.append((IcpSegment.ICP_1_PYME, score_icp_1))
    if score_icp_3 >= 25:
        candidates.append((IcpSegment.ICP_3_C2C, score_icp_3))

    if not candidates:
        cls.icp_segment = IcpSegment.NO_ICP.value
        cls.icp_score = max(score_icp_1, score_icp_2, score_icp_3)
        cls.skydropx_plan = "Starter"
        cls.value_proposition = ""
        cls.envios_estimados = "0-50"
    else:
        # Si hay ICP-1 e ICP-2, preferir ICP-2 SOLO si supera por 15+ puntos
        # (los Enterprise están en menor cantidad pero valen más por deal)
        best_seg, best_score = max(candidates, key=lambda x: x[1])

        # Excepción: si ICP_1 y ICP_2 están dentro de 15 puntos, gana ICP_1 (más volumen)
        if len(candidates) >= 2:
            scores_dict = {seg: sc for seg, sc in candidates}
            s1 = scores_dict.get(IcpSegment.ICP_1_PYME, 0)
            s2 = scores_dict.get(IcpSegment.ICP_2_ENTERPRISE, 0)
            if s1 > 0 and s2 > 0 and abs(s2 - s1) < 15:
                best_seg = IcpSegment.ICP_1_PYME
                best_score = s1
                cls.razones.append(f"tie-break: ICP_1 preferido (s1={s1}, s2={s2}, dif<15)")

        cls.icp_segment = best_seg.value
        cls.icp_score = min(best_score, 100)

        if best_seg == IcpSegment.ICP_2_ENTERPRISE:
            cls.skydropx_plan = "Enterprise"
            cls.value_proposition = _vp_enterprise(cls.vertical)
            cls.envios_estimados = "500+" if estrato in ("5", "6", "7") else "100-500"
        elif best_seg == IcpSegment.ICP_1_PYME:
            cls.skydropx_plan = ESTRATO_PLAN_DEFAULT.get(estrato, "PyME")
            cls.value_proposition = _vp_pyme(cls.vertical)
            # Sweet spot 50-100 si es PyME pequeña
            if estrato in ("3", "4"):
                cls.envios_estimados = "50-100"
            else:
                cls.envios_estimados = ESTRATO_ENVIOS_ESTIMADOS.get(estrato, "50-100")
        elif best_seg == IcpSegment.ICP_3_C2C:
            cls.skydropx_plan = "Starter"
            cls.value_proposition = "Cotizador + guías sueltas sin contrato. Ideal para envíos esporádicos."
            cls.envios_estimados = "0-50"

    return cls


def _vp_pyme(vertical: str) -> str:
    """Value proposition específica por vertical para ICP-1 PyME."""
    vps = {
        Vertical.ECOMMERCE_D2C.value:
            "Integración nativa Shopify/Tiendanube + cotizador en checkout + tarifas preferentes para PyMEs",
        Vertical.MARKETPLACE_SELLER.value:
            "Cotizador desde Mercado Libre + impresión de guías masiva + reportes mensuales",
        Vertical.PYME_RETAIL.value:
            "Cobertura nacional + automatización + recolección programada en tu tienda",
        Vertical.PYME_MAYORISTA.value:
            "Multi-paquetería + tracking unificado + integración con tu ERP",
        Vertical.FABRICANTE.value:
            "Envíos B2B + 2B + Enterprise con SLA garantizado + reportes para tu ERP",
    }
    return vps.get(vertical, "Plataforma todo-en-uno de envíos con cobertura nacional MX")


def _vp_enterprise(vertical: str) -> str:
    """Value proposition para ICP-2 Enterprise."""
    vps = {
        Vertical.LOGISTICA_3PL.value:
            "API completa + Webhooks + multi-cuenta + convenios tarifarios para 3PL",
        Vertical.AGENCIA_MARKETING.value:
            "White-label + multi-cliente + reportes consolidados para agencias",
        Vertical.FABRICANTE.value:
            "API + Convenios tarifarios + crédito empresarial + KAM dedicado",
        Vertical.PYME_MAYORISTA.value:
            "Integración ERP/SAP + tarifas corporativas + cuenta empresarial",
    }
    return vps.get(vertical, "API + Webhooks + Convenios tarifarios Enterprise")


def is_skydropx_icp(classification: IcpClassification) -> bool:
    """¿El lead califica para outbound Skydropx?"""
    return classification.icp_segment != IcpSegment.NO_ICP.value


__all__ = [
    "IcpSegment", "Vertical", "IcpClassification",
    "classify_icp", "is_skydropx_icp",
    "ESTRATO_ENVIOS_ESTIMADOS", "ESTRATO_PLAN_DEFAULT",
    "SCIAN_VERTICAL_MAP", "TECH_ICP_WEIGHTS",
]
