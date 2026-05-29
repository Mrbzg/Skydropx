"""Tests de modelos core (RawRecord, Lead, ResearchPlan)."""
import pytest
from src.core.models import (
    RawRecord, Lead, ResearchPlan, ModeloNegocio, Canal,
    Estrategia, NivelUsuario,
)


class TestRawRecord:
    def test_fingerprint_email_priority(self):
        """Email tiene prioridad sobre tel/dominio en fingerprint."""
        r = RawRecord(source="x", email="A@b.com", telefono="5512345678",
                       sitio_web="otro.com")
        assert r.fingerprint() == "email:a@b.com"

    def test_fingerprint_phone_fallback(self):
        """Si no hay email, usa últimos 10 dígitos del teléfono."""
        r = RawRecord(source="x", telefono="+52 55 1234 5678")
        assert r.fingerprint() == "tel:5512345678"

    def test_fingerprint_domain_fallback(self):
        r = RawRecord(source="x", sitio_web="https://www.miempresa.com.mx/path")
        # normaliza: quita https://, www., path
        assert r.fingerprint() == "dom:miempresa.com.mx"

    def test_fingerprint_name_estado_fallback(self):
        r = RawRecord(source="x", empresa="Mi Negocio", estado="CDMX")
        assert r.fingerprint() == "nombre:mi negocio|cdmx"

    def test_fingerprint_uuid_when_nothing(self):
        r = RawRecord(source="x")
        assert r.fingerprint().startswith("id:")


class TestResearchPlan:
    def test_auto_estrategia_quick(self):
        p = ResearchPlan(nicho="x", meta=30)
        assert p.auto_estrategia() == Estrategia.QUICK

    def test_auto_estrategia_standard(self):
        p = ResearchPlan(nicho="x", meta=500)
        assert p.auto_estrategia() == Estrategia.STANDARD

    def test_auto_estrategia_deep(self):
        p = ResearchPlan(nicho="x", meta=5000)
        assert p.auto_estrategia() == Estrategia.DEEP

    def test_auto_estrategia_enterprise(self):
        p = ResearchPlan(nicho="x", meta=50000)
        assert p.auto_estrategia() == Estrategia.ENTERPRISE


class TestLead:
    def test_csv_columns_exactly_26(self):
        """Schema v4.0 = exactamente 26 columnas."""
        assert len(Lead.csv_columns()) == 26

    def test_csv_columns_have_required(self):
        cols = Lead.csv_columns()
        required = ["lead_id", "modelo", "tipo", "nombre", "email",
                    "telefono", "scoring", "version"]
        for r in required:
            assert r in cols, f"Falta columna requerida: {r}"

    def test_to_csv_row_returns_dict_with_26_keys(self):
        lead = Lead(nombre="Demo")
        row = lead.to_csv_row()
        assert len(row) == 26
