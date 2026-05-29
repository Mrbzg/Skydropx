"""Tests de motor de exclusiones y clasificador ICP."""
import pytest
from src.core.exclusions import (
    ExclusionEngine, normalize_name_for_match, normalize_domain_for_match,
)
from src.scoring.icp_classifier import classify_icp, IcpSegment
from src.core.models import RawRecord


# ===================== Normalizers =====================

class TestNormalizers:
    def test_strip_legal_suffix_sa_cv(self):
        assert "cemex" in normalize_name_for_match("CEMEX S.A.B. de C.V.")

    def test_strip_srl(self):
        n = normalize_name_for_match("ESTAFETA MEXICAN S DE RL")
        assert "estafeta" in n
        assert "rl" not in n

    def test_strip_sapi(self):
        n = normalize_name_for_match("GRUPO ICA SAPI DE CV")
        assert "grupo ica" == n.strip()

    def test_normalize_domain_strips_protocol_www(self):
        assert normalize_domain_for_match("https://www.empresa.com.mx/path") == "empresa.com.mx"

    def test_normalize_domain_empty_safe(self):
        assert normalize_domain_for_match("") == ""
        assert normalize_domain_for_match(None) == ""


# ===================== Exclusion Engine =====================

class TestExclusionEngine:
    def setup_method(self):
        self.eng = ExclusionEngine()

    def test_accepts_normal_pyme(self):
        r = RawRecord(source="denue", empresa="ANOMALISTYC",
                       scian="463311", metadata={"estrato_id": "3"})
        assert not self.eng.check_raw_record(r).excluded

    def test_excludes_marketplace_amazon(self):
        r = RawRecord(source="dorks", empresa="Amazon Mexico",
                       sitio_web="amazon.com.mx")
        result = self.eng.check_raw_record(r)
        assert result.excluded
        assert "MARKETPLACE" in result.signal

    def test_excludes_competencia_estafeta(self):
        r = RawRecord(source="dorks", empresa="ESTAFETA MEXICANA",
                       sitio_web="estafeta.com")
        result = self.eng.check_raw_record(r)
        assert result.excluded
        assert "COMPETENCIA" in result.signal

    def test_excludes_estafeta_with_legal_suffix_fuzzy(self):
        """Subset match debe detectar 'ESTAFETA' aun con sufijo."""
        r = RawRecord(source="dorks", empresa="ESTAFETA MEXICAN S DE RL")
        result = self.eng.check_raw_record(r)
        assert result.excluded
        assert "COMPETENCIA" in result.signal

    def test_excludes_financiero_by_scian(self):
        r = RawRecord(source="denue", empresa="BANCO X", scian="5221")
        result = self.eng.check_raw_record(r)
        assert result.excluded
        assert "FINANCIERO" in result.signal or "SCIAN" in result.signal

    def test_excludes_gobierno_by_domain_pattern(self):
        r = RawRecord(source="dorks", empresa="SAT",
                       sitio_web="https://www.sat.gob.mx/")
        result = self.eng.check_raw_record(r)
        assert result.excluded
        assert "PUBLICO" in result.signal

    def test_excludes_estrato_7_grande(self):
        r = RawRecord(source="denue", empresa="EMPRESA GRANDE",
                       scian="4631", metadata={"estrato_id": "7"})
        result = self.eng.check_raw_record(r)
        assert result.excluded
        assert "GRANDE" in result.signal or "TAMANO" in result.signal

    def test_override_include_large_allows_estrato_7(self):
        eng = ExclusionEngine(include_large=True)
        r = RawRecord(source="denue", empresa="EMPRESA GRANDE OK",
                       scian="4631", metadata={"estrato_id": "7"})
        # Empresa nueva sin keyword competencia, debería pasar
        assert not eng.check_raw_record(r).excluded

    def test_subset_match_walmart_substring(self):
        """Walmart aparece como sustring."""
        r = RawRecord(source="denue", empresa="WALMART SUPERCENTER 1234")
        result = self.eng.check_raw_record(r)
        assert result.excluded
        assert "MARKETPLACE" in result.signal

    def test_does_not_false_positive(self):
        """Nombre genérico NO debería matchear nada."""
        r = RawRecord(source="denue", empresa="LA CASITA DEL CALZADO",
                       scian="463311", metadata={"estrato_id": "2"})
        assert not self.eng.check_raw_record(r).excluded

    def test_build_dork_exclusions_non_empty(self):
        excl = self.eng.build_dork_exclusions()
        assert "-site:" in excl
        # No asumimos un dominio específico (set no determinístico);
        # solo verificamos que incluye al menos algo de cada capa
        assert "-filetype:" in excl                      # capa técnica
        assert "-site:wikipedia.org" in excl              # capa técnica fixed
        assert "-site:.gob.mx" in excl                    # capa MX fixed
        assert excl.count("-site:") >= 5                  # múltiples exclusiones

    def test_filter_records_separates(self):
        good = RawRecord(source="denue", empresa="BOUTIQUE FELIZ",
                          scian="463211", metadata={"estrato_id": "2"})
        bad = RawRecord(source="dorks", empresa="DHL EXPRESS")
        accepted, excluded = self.eng.filter_records([good, bad])
        assert len(accepted) == 1
        assert len(excluded) == 1
        assert accepted[0].empresa == "BOUTIQUE FELIZ"


# ===================== ICP Classifier =====================

class TestIcpClassifier:
    def test_pyme_ecommerce_shopify(self, sample_lead_dict):
        """Boutique con Shopify + Meta Pixel + MercadoPago → ICP_1_PYME alto."""
        r = classify_icp(sample_lead_dict)
        assert r.icp_segment == IcpSegment.ICP_1_PYME.value
        assert r.icp_score >= 60
        assert r.skydropx_plan in ("Starter", "PyME")

    def test_3pl_enterprise(self):
        lead = {
            "empresa": "LOGISTICA INTEGRAL MTY",
            "scian": "4931", "tamano": "Mediana",
            "metadata": {"estrato_id": "6"},
        }
        r = classify_icp(lead)
        assert r.icp_segment == IcpSegment.ICP_2_ENTERPRISE.value
        assert r.skydropx_plan == "Enterprise"
        assert r.vertical == "3pl_fulfillment"

    def test_agencia_marketing_enterprise(self):
        lead = {
            "empresa": "NEXTGEN DIGITAL AGENCY",
            "scian": "5418", "tamano": "Mediana",
            "metadata": {"estrato_id": "5"},
        }
        r = classify_icp(lead)
        assert r.icp_segment == IcpSegment.ICP_2_ENTERPRISE.value
        assert r.vertical == "agencia_marketing"

    def test_ml_high_volume_promotes_to_pyme(self):
        """Vendedor ML con >500 tx → upgrade a ICP_1_PYME (no C2C)."""
        lead = {"empresa": "X", "source": "mercadolibre",
                 "metadata": {"ml_tx_completed": 800}}
        r = classify_icp(lead)
        assert r.icp_segment == IcpSegment.ICP_1_PYME.value

    def test_ml_low_volume_stays_c2c(self):
        lead = {"empresa": "X", "source": "mercadolibre",
                 "metadata": {"ml_tx_completed": 25}}
        r = classify_icp(lead)
        assert r.icp_segment == IcpSegment.ICP_3_C2C.value

    def test_empty_lead_is_no_icp(self):
        r = classify_icp({"empresa": "X", "metadata": {}})
        assert r.icp_segment == IcpSegment.NO_ICP.value
