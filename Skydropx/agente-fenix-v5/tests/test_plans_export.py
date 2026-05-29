"""Tests de plans YAML + HubSpot exporter."""
import json
import tempfile
from pathlib import Path

import pytest

from src.skill.plans import Plan, _minimal_yaml_parse, _coerce_scalar


# ===================== Plans =====================

class TestYamlParser:
    """Parser YAML mínimo zero-deps."""

    def test_coerce_int(self):
        assert _coerce_scalar("42") == 42

    def test_coerce_float(self):
        assert _coerce_scalar("3.14") == 3.14

    def test_coerce_bool_true(self):
        assert _coerce_scalar("true") is True
        assert _coerce_scalar("yes") is True

    def test_coerce_bool_false(self):
        assert _coerce_scalar("false") is False

    def test_coerce_strip_quotes(self):
        assert _coerce_scalar('"hello"') == "hello"

    def test_parse_simple_keys(self):
        text = "nicho: ropa\nmeta: 100\n"
        d = _minimal_yaml_parse(text)
        assert d["nicho"] == "ropa"
        assert d["meta"] == 100

    def test_parse_list(self):
        text = "sources:\n  - denue\n  - dorks\n"
        d = _minimal_yaml_parse(text)
        assert d["sources"] == ["denue", "dorks"]

    def test_parse_skip_comments(self):
        text = "# este es un comentario\nnicho: ropa  # inline\n"
        d = _minimal_yaml_parse(text)
        assert d["nicho"] == "ropa"


class TestPlan:
    def test_load_valid_plan(self, tmp_path):
        yaml_text = """name: "Test Plan"
nicho: ropa
zona: CDMX
meta: 100
modelo: B2C
"""
        p = tmp_path / "test.yaml"
        p.write_text(yaml_text)
        plan = Plan.load(p)
        assert plan.nicho == "ropa"
        assert plan.zona == "CDMX"
        assert plan.meta == 100
        assert plan.modelo == "B2C"

    def test_validate_requires_nicho(self, tmp_path):
        yaml_text = "zona: CDMX\nmeta: 100\n"
        p = tmp_path / "bad.yaml"
        p.write_text(yaml_text)
        with pytest.raises(ValueError, match="nicho"):
            Plan.load(p)

    def test_validate_rejects_invalid_mode(self, tmp_path):
        yaml_text = "nicho: ropa\nzona: CDMX\nmeta: 100\nmode: invalid\n"
        p = tmp_path / "bad.yaml"
        p.write_text(yaml_text)
        with pytest.raises(ValueError, match="mode"):
            Plan.load(p)

    def test_to_research_plan(self, tmp_path):
        yaml_text = "nicho: calzado\nzona: GDL\nmeta: 50\n"
        p = tmp_path / "ok.yaml"
        p.write_text(yaml_text)
        plan = Plan.load(p)
        rp = plan.to_research_plan()
        assert rp.nicho == "calzado"
        assert rp.meta == 50
        # Estrategia auto = quick para 50
        assert rp.estrategia.value == "quick"

    def test_example_plan_loads_ok(self):
        """El plan ejemplo del proyecto debe ser válido."""
        example = Path("plans/EJEMPLO.yaml")
        if not example.exists():
            pytest.skip("plans/EJEMPLO.yaml no existe en este entorno")
        plan = Plan.load(example)
        assert plan.nicho == "ropa"
        assert plan.zona == "CDMX"


# ===================== HubSpot Exporter =====================

class TestHubSpotExporter:
    def test_lead_to_contact_valid(self):
        from src.export.hubspot_csv import lead_to_hubspot_contact
        lead = {
            "empresa": "ZAPATERIA DEMO",
            "nombre": "Juan García",
            "email": "juan@zapateriademo.mx",
            "telefono": "+525556059049",
            "_bucket": "COMPLETO",
            "metadata": {"icp_segment": "ICP_1_PYME"},
        }
        row = lead_to_hubspot_contact(lead, run_id="t1")
        assert row is not None
        assert row["Email"] == "juan@zapateriademo.mx"
        assert "Notes" in row
        assert "ICP_1_PYME" in row["Notes"]

    def test_lead_without_email_rejected(self):
        from src.export.hubspot_csv import lead_to_hubspot_contact
        lead = {"empresa": "X", "email": "DATO_NO_VERIFICABLE"}
        assert lead_to_hubspot_contact(lead) is None

    def test_lead_invalid_email_rejected(self):
        from src.export.hubspot_csv import lead_to_hubspot_contact
        lead = {"empresa": "X", "email": "no_es_email"}
        assert lead_to_hubspot_contact(lead) is None

    def test_lead_to_company(self):
        from src.export.hubspot_csv import lead_to_hubspot_company
        lead = {
            "empresa": "ZAPATERIA DEMO",
            "sitio_web": "https://zapateriademo.mx/",
            "telefono": "5556059049",
            "tamano": "Pequeña",
            "giro": "Comercio al por menor",
        }
        row = lead_to_hubspot_company(lead, run_id="t1")
        assert row is not None
        assert row["Company Name"] == "ZAPATERIA DEMO"
        assert row["Company Domain Name"] == "zapateriademo.mx"
        assert row["Number of Employees"] == "11-50"

    def test_export_full_flow(self, tmp_path):
        from src.export.hubspot_csv import export_hubspot_csvs
        leads = [
            {
                "empresa": "Empresa A", "nombre": "Juan",
                "email": "a@empresa-a.mx", "telefono": "5512345678",
                "_bucket": "COMPLETO",
                "metadata": {"icp_segment": "ICP_1_PYME"},
            },
            {
                "empresa": "Empresa B", "nombre": "Maria",
                "email": "b@empresa-b.mx", "telefono": "8112345678",
                "_bucket": "PARCIAL",
                "metadata": {},
            },
        ]
        result = export_hubspot_csvs(leads, output_dir=str(tmp_path),
                                       run_id="test")
        assert Path(result.contacts_csv).exists()
        assert Path(result.companies_csv).exists()
        assert Path(result.readme).exists()
        assert result.n_contacts == 2
        assert result.n_companies == 2

    def test_dedup_by_email(self, tmp_path):
        from src.export.hubspot_csv import export_hubspot_csvs
        leads = [
            {"empresa": "A", "email": "x@same.com", "_bucket": "COMPLETO"},
            {"empresa": "B", "email": "x@same.com", "_bucket": "COMPLETO"},
        ]
        result = export_hubspot_csvs(leads, output_dir=str(tmp_path), run_id="t")
        assert result.n_contacts == 1   # uno deduped
        assert result.n_contacts_skipped == 1
