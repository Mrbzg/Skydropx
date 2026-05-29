"""
Las 4 fórmulas de scoring Fénix v5.

- DATA_SCORE        : calidad del dato (0-100, umbral 70)
- SKYDROPX_SCORE    : calor del lead (1-5)
- SALES_PRIORITY    : prioridad comercial (0-100)
- CONTACT_SCORE     : calidad del contacto (0-100, umbral 50)

Convención: cada función recibe un dict (Lead.to_full_dict()) y retorna ScoreBreakdown.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------- DATA_SCORE ----------------

DATA_WEIGHTS = {
    "email_smtp_corporativo": 40,
    "cargo_confirmado": 30,
    "telefono_mx_valido": 20,
    "linkedin_activo": 10,
}


@dataclass
class ScoreBreakdown:
    total: int = 0
    bucket: str = "RAW"
    detail: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def calc_data_score(lead: dict[str, Any]) -> ScoreBreakdown:
    sb = ScoreBreakdown()
    email = (lead.get("email") or "").strip().lower()
    if email and "@" in email and "." in email.split("@", 1)[1]:
        # Considerar corporativo si no es @gmail/hotmail/yahoo/outlook
        dominio = email.split("@", 1)[1]
        if not any(d in dominio for d in ("gmail.", "hotmail.", "yahoo.", "outlook.",
                                            "live.com", "icloud.com")):
            sb.detail["email_smtp_corporativo"] = DATA_WEIGHTS["email_smtp_corporativo"]
            sb.total += DATA_WEIGHTS["email_smtp_corporativo"]
            sb.notes.append("email corporativo")
        else:
            # email personal vale la mitad
            sb.detail["email_smtp_corporativo"] = 20
            sb.total += 20
            sb.notes.append("email personal")
    if lead.get("cargo") or lead.get("nombre_persona"):
        sb.detail["cargo_confirmado"] = DATA_WEIGHTS["cargo_confirmado"]
        sb.total += DATA_WEIGHTS["cargo_confirmado"]
    tel = (lead.get("telefono") or "").strip()
    digits = "".join(c for c in tel if c.isdigit())
    if len(digits) >= 10:
        sb.detail["telefono_mx_valido"] = DATA_WEIGHTS["telefono_mx_valido"]
        sb.total += DATA_WEIGHTS["telefono_mx_valido"]
    if lead.get("linkedin"):
        sb.detail["linkedin_activo"] = DATA_WEIGHTS["linkedin_activo"]
        sb.total += DATA_WEIGHTS["linkedin_activo"]
    sb.total = min(sb.total, 100)
    sb.bucket = _bucket_data(sb.total)
    return sb


def _bucket_data(score: int) -> str:
    if score >= 70:
        return "COMPLETO"
    if score >= 50:
        return "PARCIAL"
    return "SIN_CONTACTO"


# ---------------- SKYDROPX_SCORE (1-5) ----------------

def calc_skydropx_score(lead: dict[str, Any]) -> ScoreBreakdown:
    """
    1 = MUY FRÍO, 5 = MUY CALIENTE.
    Heurísticas conservadoras: si hay señales claras de envíos, ya es 3+.
    """
    sb = ScoreBreakdown()
    s = 1

    has_email = bool(lead.get("email"))
    has_phone = bool(lead.get("telefono") or lead.get("whatsapp"))
    has_web = bool(lead.get("sitio_web") or lead.get("instagram") or lead.get("facebook"))
    has_size = lead.get("tamano") in ("Mediana", "Grande")
    has_envios = (lead.get("envios_intent")
                  or lead.get("_envios_intent")
                  or (lead.get("metadata") or {}).get("envios_intent"))
    has_competencia = bool(
        set((lead.get("metadata") or {}).get("paqueterias_mencionadas") or [])
        - {"skydropx"}
    )
    has_platform = bool(
        lead.get("plataforma") or (lead.get("metadata") or {}).get("plataforma_detectada")
    )

    if has_email and has_phone:
        s = max(s, 3)
    if has_envios:
        s = max(s, 4)
    if has_competencia:
        # ya está enviando con competencia = lead muy caliente
        s = max(s, 4)
    if has_envios and has_competencia and has_platform:
        s = 5
    if has_size and has_email and has_phone:
        s = max(s, 4)
    if not has_email and not has_phone:
        s = min(s, 2)
    if not has_web and not has_email and not has_phone:
        s = 1

    sb.total = s
    sb.bucket = "caliente" if s >= 3 else "frio"
    sb.notes.append(f"señales: env={has_envios} comp={has_competencia} plat={has_platform}")
    return sb


# ---------------- SALES_PRIORITY ----------------

TOP_CITIES = {
    "ciudad de mexico", "ciudad de méxico", "cdmx", "mexico df",
    "guadalajara", "monterrey", "puebla", "queretaro", "querétaro",
    "leon", "león", "merida", "mérida", "tijuana", "saltillo", "toluca",
}


def calc_sales_priority(lead: dict[str, Any]) -> ScoreBreakdown:
    sb = ScoreBreakdown()
    # match modelo
    modelo = (lead.get("modelo") or "").upper()
    if modelo in ("B2B", "D2C", "B2C"):
        sb.detail["modelo_match"] = 40
        sb.total += 40
    elif modelo in ("C2C", "C2B"):
        sb.detail["modelo_match"] = 20
        sb.total += 20

    # volumen estimado de envíos (proxy: tamaño + ML transactions)
    tamano = lead.get("tamano", "")
    ml_tx = (lead.get("metadata") or {}).get("ml_tx_completed", 0)
    vol = 0
    if tamano == "Grande": vol = 30
    elif tamano == "Mediana": vol = 22
    elif tamano == "Pequeña": vol = 12
    elif tamano == "Micro": vol = 5
    if ml_tx >= 500: vol = max(vol, 28)
    elif ml_tx >= 100: vol = max(vol, 20)
    elif ml_tx >= 20: vol = max(vol, 10)
    if vol:
        sb.detail["volumen_envios"] = vol
        sb.total += vol

    # presencia digital
    pres = 0
    if lead.get("sitio_web"): pres += 6
    if lead.get("instagram"): pres += 5
    if lead.get("facebook"): pres += 4
    if lead.get("linkedin"): pres += 5
    pres = min(pres, 20)
    if pres:
        sb.detail["presencia_digital"] = pres
        sb.total += pres

    # complejidad logística (proxy: top city = mayor demanda envíos)
    ciudad = (lead.get("ubicacion") or "").lower().strip()
    if any(c in ciudad for c in TOP_CITIES):
        sb.detail["complejidad_logistica"] = 10
        sb.total += 10

    sb.total = min(sb.total, 100)
    return sb


# ---------------- CONTACT_SCORE ----------------

def calc_contact_score(lead: dict[str, Any]) -> ScoreBreakdown:
    sb = ScoreBreakdown()
    tels = []
    if lead.get("telefono"): tels.append(lead["telefono"])
    if lead.get("whatsapp"): tels.append(lead["whatsapp"])
    emails = []
    if lead.get("email"): emails.append(lead["email"])
    extras_emails = (lead.get("metadata") or {}).get("emails_personales") or []
    emails.extend(extras_emails)

    n_tels = len(set(tels))
    n_emails = len(set(emails))

    if n_tels >= 2:
        sb.detail["telefonos"] = 40; sb.total += 40
    elif n_tels == 1:
        sb.detail["telefonos"] = 20; sb.total += 20
    if n_emails >= 2:
        sb.detail["emails"] = 30; sb.total += 30
    elif n_emails == 1:
        sb.detail["emails"] = 15; sb.total += 15
    if lead.get("linkedin"):
        sb.detail["linkedin"] = 15; sb.total += 15
    if lead.get("instagram") or lead.get("facebook"):
        sb.detail["otras_redes"] = 15; sb.total += 15
    sb.total = min(sb.total, 100)
    sb.bucket = "contactable" if sb.total >= 50 else "no_contactable"
    return sb


# ---------------- Scoring unificado ----------------

def score_lead(lead: dict[str, Any]) -> dict[str, ScoreBreakdown]:
    return {
        "DATA_SCORE": calc_data_score(lead),
        "SKYDROPX_SCORE": calc_skydropx_score(lead),
        "SALES_PRIORITY": calc_sales_priority(lead),
        "CONTACT_SCORE": calc_contact_score(lead),
    }


def is_skydropx_ready(lead: dict[str, Any]) -> bool:
    """Lead READY si tiene email + (tel o whatsapp) + nombre."""
    has_email = bool(lead.get("email"))
    has_phone = bool(lead.get("telefono") or lead.get("whatsapp"))
    has_name = bool(lead.get("empresa") or lead.get("nombre") or lead.get("razon_social"))
    return has_email and has_phone and has_name


__all__ = [
    "ScoreBreakdown", "score_lead", "is_skydropx_ready",
    "calc_data_score", "calc_skydropx_score", "calc_sales_priority", "calc_contact_score",
    "DATA_WEIGHTS", "TOP_CITIES",
]
