"""Tests del Discovery Protocol + Events/Campaigns."""
import pytest
from src.skill.discovery import (
    parse_user_input, apply_answer, detect_nicho, detect_zona,
    detect_modelo, detect_canal, detect_meta,
)
from src.extraction.events_campaigns import (
    find_evento_by_keyword, detect_campaign_signals, detect_agency_in_text,
    get_eventos_activos, build_event_campaign_dorks,
)


# ===================== Discovery =====================

class TestDetectorsAtomic:
    def test_detect_nicho_ropa(self):
        nicho, scianes = detect_nicho("quiero leads de ropa")
        assert nicho == "ropa"
        assert scianes

    def test_detect_nicho_calzado_alias(self):
        nicho, _ = detect_nicho("vendo zapatos")
        assert nicho == "calzado"

    def test_detect_zona_cdmx(self):
        assert detect_zona("leads en CDMX") == "CDMX"

    def test_detect_zona_alias_gdl(self):
        assert detect_zona("en GDL") == "Jalisco"

    def test_detect_modelo_b2b_keyword(self):
        assert detect_modelo("para mayoristas") == "B2B"

    def test_detect_modelo_d2c_explicit(self):
        assert detect_modelo("D2C marcas propias") == "D2C"

    def test_detect_canal_marketplace(self):
        assert detect_canal("vendedores Mercado Libre") == "marketplace"

    def test_detect_meta_explicit_number(self):
        assert detect_meta("500 leads") == 500

    def test_detect_meta_with_mil(self):
        assert detect_meta("5 mil prospectos") == 5000

    def test_detect_meta_with_k(self):
        assert detect_meta("dame 10k contactos") == 10000


class TestDiscoverySession:
    def test_simple_query_needs_input(self):
        s = parse_user_input("quiero leads de ropa")
        assert s.status == "needs_input"
        assert s.nicho == "ropa"
        assert "modelo" in s.missing_fields() or "meta" in s.missing_fields()

    def test_complete_query_ready(self):
        s = parse_user_input("necesito 500 leads de calzado en CDMX para B2B")
        assert s.status == "ready"
        assert s.nicho == "calzado"
        assert s.zona == "CDMX"
        assert s.modelo == "B2B"
        assert s.meta == 500

    def test_apply_answer_completes_session(self):
        s = parse_user_input("quiero leads de ropa")
        s = apply_answer(s, "modelo", "B2C")
        s = apply_answer(s, "meta", "100")
        assert s.status == "ready"

    def test_to_research_plan(self):
        s = parse_user_input("500 leads de joyería en GDL para D2C")
        plan = s.to_research_plan()
        assert plan.meta == 500
        assert plan.nicho == "joyeria"
        assert plan.zona == "Jalisco"


# ===================== Events / Campaigns =====================

class TestEvents:
    def test_find_evento_mundial(self):
        ev = find_evento_by_keyword("quiero leads del mundial")
        assert ev is not None
        assert "mundial" in ev["id"]

    def test_find_evento_dia_madres(self):
        ev = find_evento_by_keyword("campanas del día de las madres")
        assert ev is not None
        assert ev["id"] == "dia_de_las_madres"

    def test_find_evento_returns_none_for_unknown(self):
        ev = find_evento_by_keyword("algo aleatorio sin evento")
        assert ev is None

    def test_eventos_activos_returns_list(self):
        activos = get_eventos_activos()
        assert isinstance(activos, list)
        # Pueden ser 0 o más según fecha actual

    def test_build_dorks_includes_promo_keywords(self):
        ev = find_evento_by_keyword("mundial")
        dorks = build_event_campaign_dorks(ev, incluir_campaign=True,
                                             incluir_exclusiones=False)
        # Algún dork debe combinar evento + keyword promocional
        combined = " ".join(dorks).lower()
        assert "compra y gana" in combined or "registrate" in combined


class TestCampaignDetection:
    def test_detect_compra_y_gana(self):
        text = "Esta es nuestra promocion: compra y gana boletos al mundial"
        signals = detect_campaign_signals(text)
        types = [s.tipo for s in signals]
        assert "compra_y_gana" in types

    def test_detect_registrate_y_gana(self):
        text = "Registra tu ticket para participar"
        signals = detect_campaign_signals(text)
        types = [s.tipo for s in signals]
        assert "registrate_gana" in types

    def test_no_signals_in_irrelevant_text(self):
        signals = detect_campaign_signals("Esto es un texto irrelevante")
        assert len(signals) == 0


class TestAgencyDetector:
    def test_detect_agency_known_catalog(self):
        text = ("Promoción operada por MASSIVE EMOTIONS S DE RL DE CV "
                "en colaboración con DATUMAX")
        r = detect_agency_in_text(text)
        # Detecta al menos una de las dos del catalog
        assert len(r["agencias"]) >= 1
        assert any("MASSIVE" in a.upper() or "DATUMAX" in a.upper()
                    for a in r["agencias"])

    def test_detect_rfc_pattern(self):
        text = "Responsable: MASSIVE EMOTIONS con RFC MEM200115ABC"
        r = detect_agency_in_text(text)
        assert "MEM200115ABC" in r["rfcs"]

    def test_detect_razon_social_with_legal_suffix(self):
        text = "Organizado por DATUMAX S.A. DE C.V., responsable del tratamiento"
        r = detect_agency_in_text(text)
        assert any("DATUMAX" in rs for rs in r["razones_sociales"])
