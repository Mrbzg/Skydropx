"""
Tiered Filter — clasifica leads en niveles de calidad para outbound:

  PREMIUM:  los 3 datos (nombre + email + tel/whatsapp)
  GOLD:     2 de 3 datos, incluyendo email VÁLIDO
  SILVER:   al menos 1 dato de contacto (email O tel O wa)
  BRONZE:   solo identificación (sin contacto) — para reciclar

Este filtro es ortogonal al bucket (COMPLETO/PARCIAL/SIN_CONTACTO) que se basa
en DATA_SCORE. El bucket mide calidad técnica del scoring; el tier mide
"con esto puedo arrancar outbound HOY".

Uso típico:
    Equipo ventas pide CSV "lo mejor de lo mejor" → tier=PREMIUM (~5% del lote)
    Equipo growth pide para nurturing → tier=GOLD+ (~20-30%)
    Reciclaje futuro / cold outreach → tier=SILVER+ (~50%)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


class Tier(str, Enum):
    PREMIUM = "PREMIUM"   # los 3 datos completos
    GOLD = "GOLD"         # 2 de 3 datos, email obligatorio
    SILVER = "SILVER"     # al menos 1 dato de contacto
    BRONZE = "BRONZE"     # sin contacto (solo identificación)


def _is_real_email(email: str | None) -> bool:
    if not email or email == "DATO_NO_VERIFICABLE":
        return False
    return bool(EMAIL_RE.match(email.strip()))


def _is_real_phone(phone: str | None) -> bool:
    if not phone or phone == "DATO_NO_VERIFICABLE":
        return False
    digits = re.sub(r"\D", "", phone)
    return len(digits) >= 10


def _has_name(lead: dict) -> bool:
    name_fields = ["nombre", "empresa", "razon_social", "nombre_comercial",
                    "nombre_persona"]
    for f in name_fields:
        v = lead.get(f, "")
        if v and v not in ("(sin nombre)", "DATO_NO_VERIFICABLE", ""):
            return True
    return False


def classify_tier(lead: dict) -> tuple[Tier, str]:
    """
    Clasifica un lead en uno de los 4 tiers.
    Devuelve (Tier, descripción_breve).
    """
    has_name = _has_name(lead)
    has_email = _is_real_email(lead.get("email"))
    has_phone = _is_real_phone(lead.get("telefono")) or _is_real_phone(lead.get("whatsapp"))

    # Construir descripción
    pieces = []
    if has_name: pieces.append("nombre")
    if has_email: pieces.append("email")
    if has_phone: pieces.append("phone/wa")
    desc = "+".join(pieces) if pieces else "ninguno"

    # Reglas tier
    if has_name and has_email and has_phone:
        return Tier.PREMIUM, f"{desc} (los 3)"
    if has_email and has_name:
        return Tier.GOLD, f"{desc} (faltan tel)"
    if has_email or has_phone:
        # SILVER si tiene contacto + nombre o solo contacto
        return Tier.SILVER, f"{desc} (parcial)"
    return Tier.BRONZE, "solo identificación"


def filter_by_tier(leads: list[dict],
                    min_tier: Tier = Tier.SILVER) -> tuple[list[dict], dict[str, int]]:
    """
    Filtra leads que cumplen al menos `min_tier`.
    Devuelve (leads_aceptados, conteo_por_tier).
    """
    tier_order = [Tier.PREMIUM, Tier.GOLD, Tier.SILVER, Tier.BRONZE]
    min_idx = tier_order.index(min_tier)
    accepted_tiers = set(t.value for t in tier_order[: min_idx + 1])

    accepted = []
    counts = {t.value: 0 for t in Tier}

    for lead in leads:
        tier, _desc = classify_tier(lead)
        counts[tier.value] += 1
        if tier.value in accepted_tiers:
            # Anotar el tier en el lead para que el export lo use
            lead["_tier"] = tier.value
            accepted.append(lead)

    return accepted, counts


def get_tier_summary(leads: list[dict]) -> dict:
    """Reporte de distribución por tier."""
    counts = {t.value: 0 for t in Tier}
    for lead in leads:
        tier, _ = classify_tier(lead)
        counts[tier.value] += 1
    total = sum(counts.values())
    return {
        "total": total,
        "counts": counts,
        "pct": {k: round(100 * v / max(1, total), 1) for k, v in counts.items()},
    }


__all__ = ["Tier", "classify_tier", "filter_by_tier", "get_tier_summary"]
