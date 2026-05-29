"""
Validador y normalizador de teléfonos MX usando phonenumbers (Google libphonenumber).

Reemplaza la normalización regex casera por la librería oficial de Google,
que conoce:
- Lada MX correcta (2-3 dígitos según región)
- Validación de existencia (no solo formato)
- Tipo de línea: FIXED_LINE, MOBILE, FIXED_OR_MOBILE
- Carrier (Telcel, AT&T, Movistar, etc.)
- Región dentro de MX (CDMX = lada 55, GDL = 33, MTY = 81, etc.)

Esto es crítico para Skydropx porque:
- WhatsApp solo funciona en móviles
- Los buckets de scoring distinguen tel fijo vs móvil
- E.164 estricto es lo que HubSpot/CRMs requieren
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal

try:
    import phonenumbers
    from phonenumbers import (
        PhoneNumberFormat, PhoneNumberType, NumberParseException,
        carrier as ph_carrier, geocoder as ph_geocoder,
    )
    HAS_PHONENUMBERS = True
except ImportError:
    HAS_PHONENUMBERS = False
    PhoneNumberType = None  # noqa: N816

logger = logging.getLogger(__name__)


# Mapeo PhoneNumberType → string legible
_TYPE_NAMES = {}
if HAS_PHONENUMBERS:
    _TYPE_NAMES = {
        PhoneNumberType.FIXED_LINE: "fijo",
        PhoneNumberType.MOBILE: "movil",
        PhoneNumberType.FIXED_LINE_OR_MOBILE: "fijo_o_movil",
        PhoneNumberType.TOLL_FREE: "800",
        PhoneNumberType.PREMIUM_RATE: "premium",
        PhoneNumberType.SHARED_COST: "shared",
        PhoneNumberType.VOIP: "voip",
        PhoneNumberType.PERSONAL_NUMBER: "personal",
        PhoneNumberType.PAGER: "pager",
        PhoneNumberType.UAN: "uan",
        PhoneNumberType.VOICEMAIL: "voicemail",
        PhoneNumberType.UNKNOWN: "desconocido",
    }


@dataclass
class PhoneValidation:
    original: str
    is_valid: bool = False
    e164: str = ""              # +52XXXXXXXXXX
    national: str = ""          # 55 XXXX XXXX
    rfc3966: str = ""           # tel:+52-XXXXXXXXXX
    country: str = "MX"
    region: str = ""            # estado/ciudad inferida
    line_type: str = "unknown"  # movil/fijo/fijo_o_movil/...
    carrier: str = ""           # Telcel/AT&T/Movistar
    can_whatsapp: bool = False  # móvil o fijo_o_móvil
    error: str = ""


def validate_phone_mx(raw: str, default_region: str = "MX") -> PhoneValidation:
    """
    Valida y normaliza un teléfono. Si phonenumbers no está instalado,
    cae a regex básica (compatible con la implementación anterior).
    """
    result = PhoneValidation(original=raw or "")
    if not raw:
        result.error = "empty"
        return result

    # Fallback sin phonenumbers: solo regex
    if not HAS_PHONENUMBERS:
        return _fallback_regex(raw)

    # Limpiar entrada
    cleaned = raw.strip()
    if not cleaned.startswith("+"):
        # Heurística: si tiene 10 dígitos, asumir MX
        digits = re.sub(r"\D", "", cleaned)
        if len(digits) == 10:
            cleaned = "+52" + digits
        elif len(digits) == 12 and digits.startswith("52"):
            cleaned = "+" + digits
        elif len(digits) == 13 and digits.startswith("521"):
            # +521 (móvil legacy) → +52 (nuevo)
            cleaned = "+52" + digits[3:]
        else:
            cleaned = digits

    try:
        num = phonenumbers.parse(cleaned, default_region)
    except NumberParseException as e:
        result.error = f"parse_error: {e}"
        return result

    if not phonenumbers.is_valid_number(num):
        result.error = "invalid_number"
        return result

    result.is_valid = True
    result.e164 = phonenumbers.format_number(num, PhoneNumberFormat.E164)
    result.national = phonenumbers.format_number(num, PhoneNumberFormat.NATIONAL)
    result.rfc3966 = phonenumbers.format_number(num, PhoneNumberFormat.RFC3966)
    result.country = phonenumbers.region_code_for_number(num) or "MX"

    line_type = phonenumbers.number_type(num)
    result.line_type = _TYPE_NAMES.get(line_type, "desconocido")
    result.can_whatsapp = line_type in (
        PhoneNumberType.MOBILE, PhoneNumberType.FIXED_LINE_OR_MOBILE,
    )

    try:
        result.region = ph_geocoder.description_for_number(num, "es") or ""
    except Exception:  # noqa: BLE001
        pass
    try:
        result.carrier = ph_carrier.name_for_number(num, "es") or ""
    except Exception:  # noqa: BLE001
        pass

    return result


def _fallback_regex(raw: str) -> PhoneValidation:
    """Compatibilidad si phonenumbers no instalado: misma lógica que el pipeline original."""
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("521") and len(digits) >= 13:
        digits = digits[2:]
    if digits.startswith("52") and len(digits) >= 12:
        digits = digits[2:]
    if len(digits) >= 10:
        digits = digits[-10:]
        return PhoneValidation(
            original=raw, is_valid=True,
            e164=f"+52{digits}",
            national=f"{digits[:2]} {digits[2:6]} {digits[6:]}",
            can_whatsapp=True,  # asumir móvil como default conservador
            error="fallback_regex (sin phonenumbers)",
        )
    return PhoneValidation(original=raw, error="too_short")


def validate_many(numbers: list[str]) -> list[PhoneValidation]:
    return [validate_phone_mx(n) for n in numbers]


__all__ = ["PhoneValidation", "validate_phone_mx", "validate_many", "HAS_PHONENUMBERS"]
