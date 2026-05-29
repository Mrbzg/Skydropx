"""Tests de validación de teléfono (phonenumbers) y email (cascada)."""
import pytest
from src.extraction.phone_validator import validate_phone_mx, HAS_PHONENUMBERS
from src.extraction.email_verifier import EmailVerifier


@pytest.mark.skipif(not HAS_PHONENUMBERS, reason="phonenumbers no instalado")
class TestPhoneValidator:
    def test_cdmx_10_digits(self):
        v = validate_phone_mx("5556059049")
        assert v.is_valid
        assert v.e164 == "+525556059049"
        assert "CDMX" in v.region or "Ciudad de México" in v.region

    def test_gdl_formatted(self):
        v = validate_phone_mx("+52 33 1234 5678")
        assert v.is_valid
        assert v.e164 == "+523312345678"
        assert "Guadalajara" in v.region or "JAL" in v.region

    def test_mty_short(self):
        v = validate_phone_mx("8112345678")
        assert v.is_valid
        assert v.e164 == "+528112345678"

    def test_legacy_521_prefix(self):
        """521 (móvil legacy) → +52 (nuevo formato)."""
        v = validate_phone_mx("521 5556059049")
        assert v.is_valid
        assert v.e164 == "+525556059049"

    def test_invalid_too_short(self):
        v = validate_phone_mx("12345")
        assert not v.is_valid

    def test_invalid_not_a_phone(self):
        v = validate_phone_mx("abc-no-tel")
        assert not v.is_valid

    def test_usa_number_invalid_for_mx(self):
        """Número USA no es MX válido."""
        v = validate_phone_mx("+1 555 1234567")
        assert not v.is_valid

    def test_800_toll_free_cannot_whatsapp(self):
        v = validate_phone_mx("8005551234")
        assert v.is_valid
        assert v.can_whatsapp is False  # 800 no es WA

    def test_mobile_can_whatsapp(self):
        v = validate_phone_mx("5512345678")
        assert v.is_valid
        # fijo_o_movil → can_whatsapp=True conservador
        assert v.can_whatsapp is True


class TestEmailVerifier:
    """Tests offline (sin SMTP) — los que requieren red usan mark.network."""

    def setup_method(self):
        # check_mx=False → 100% offline para tests rápidos
        self.v = EmailVerifier(check_mx=False, check_smtp=False)

    def test_valid_syntax(self):
        r = self.v.verify("contacto@empresa.com.mx")
        assert r.is_valid
        assert r.domain == "empresa.com.mx"

    def test_invalid_syntax(self):
        r = self.v.verify("no_es_un_email")
        assert not r.is_valid
        assert r.status == "invalid_syntax"

    def test_disposable_blocked(self):
        r = self.v.verify("test@mailinator.com")
        assert not r.is_valid
        assert r.status == "disposable"
        assert r.is_disposable

    def test_personal_email_detected(self):
        r = self.v.verify("juan@gmail.com")
        assert r.is_valid
        assert r.is_personal

    def test_corporate_email_not_personal(self):
        r = self.v.verify("contacto@miempresa.com.mx")
        assert r.is_valid
        assert not r.is_personal

    def test_cache_returns_same_object(self):
        """Llamadas repetidas usan caché."""
        r1 = self.v.verify("test@empresa.com")
        r2 = self.v.verify("test@empresa.com")
        # mismo objeto en memoria (cache hit)
        assert r1 is r2
