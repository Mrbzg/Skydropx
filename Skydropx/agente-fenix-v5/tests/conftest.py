"""Configuración compartida de pytest."""
import sys
from pathlib import Path

# Asegurar que src/ es importable desde los tests
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import os
import tempfile
import pytest


@pytest.fixture
def temp_db_path():
    """DB SQLite temporal, limpia al final del test."""
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def isolated_db(temp_db_path, monkeypatch):
    """FenixDB aislado en una DB temporal."""
    from src.db.engine import FenixDB
    db = FenixDB(db_url=f"sqlite:///{temp_db_path}")
    db.init_schema()
    # Monkeypatch el singleton para que get_db() devuelva esta instancia
    import src.db.engine as engine_mod
    monkeypatch.setattr(engine_mod, "_default_db", db)
    return db


@pytest.fixture
def sample_raw_record():
    """Un RawRecord típico de DENUE para tests."""
    from src.core.models import RawRecord
    return RawRecord(
        source="denue",
        empresa="ZAPATERIA DEMO",
        nombre_comercial="ZAPATERIA DEMO",
        email="contacto@zapateriademo.mx",
        telefono="5512345678",
        sitio_web="https://zapateriademo.mx",
        scian="463311",
        tamano="Micro",
        estado="CIUDAD DE MÉXICO",
        municipio="Coyoacán",
        metadata={"estrato_id": "1", "denue_id": "test-001"},
    )


@pytest.fixture
def sample_lead_dict():
    """Lead-style dict para tests de scoring/icp."""
    return {
        "empresa": "BOUTIQUE D2C",
        "nombre_comercial": "BOUTIQUE D2C",
        "scian": "463211",
        "tamano": "Pequeña",
        "source": "denue",
        "giro_descripcion": "Comercio al por menor de ropa",
        "metadata": {
            "estrato_id": "3",
            "tech_stack": ["shopify", "meta_pixel", "mercadopago"],
            "maturity_score": 75,
            "envios_intent": True,
        },
    }
