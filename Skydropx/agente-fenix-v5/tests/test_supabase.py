"""Tests de la integración Supabase + sync layer."""
import json
from unittest.mock import MagicMock, patch

import pytest


class TestSupabaseClient:
    def test_is_configured_false_without_env(self, monkeypatch):
        from src.db.supabase_client import is_configured
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_KEY", raising=False)
        # Reimport para que recoja env limpio
        import importlib
        import src.core.config
        importlib.reload(src.core.config)
        import src.db.supabase_client
        importlib.reload(src.db.supabase_client)
        from src.db.supabase_client import is_configured as is_conf
        assert is_conf() is False

    def test_healthcheck_no_lib(self, monkeypatch):
        """Si supabase-py no instalado, devuelve mensaje claro."""
        import src.db.supabase_client as m
        monkeypatch.setattr(m, "HAS_SUPABASE", False)
        result = m.healthcheck()
        assert result["available"] is False
        assert "no instalado" in result["reason"]

    def test_healthcheck_no_config(self, monkeypatch):
        import src.db.supabase_client as m
        monkeypatch.setattr(m, "HAS_SUPABASE", True)
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_KEY", raising=False)
        # Reload config
        import importlib
        import src.core.config
        importlib.reload(src.core.config)
        importlib.reload(m)
        result = m.healthcheck()
        assert result["available"] is False


class TestSyncTransformers:
    def test_company_to_supabase_basic(self):
        from src.db.sync import _company_to_supabase
        row = {
            "id": 42,
            "fingerprint": "email:test@x.com",
            "razon_social": "EMPRESA TEST",
            "estado": "CDMX",
            "scian": "4632",
            "metadata_json": '{"icp_segment": "ICP_1_PYME"}',
        }
        payload = _company_to_supabase(row)
        assert payload["fingerprint"] == "email:test@x.com"
        assert payload["razon_social"] == "EMPRESA TEST"
        assert payload["local_id"] == 42
        assert payload["metadata"]["icp_segment"] == "ICP_1_PYME"
        assert payload["source_system"] == "fenix_local"

    def test_company_to_supabase_handles_bad_metadata(self):
        from src.db.sync import _company_to_supabase
        row = {
            "id": 1,
            "fingerprint": "x",
            "razon_social": "TEST",
            "metadata_json": "not json",
        }
        payload = _company_to_supabase(row)
        assert payload["metadata"] == {}  # fallback OK

    def test_company_to_supabase_skips_none_values(self):
        from src.db.sync import _company_to_supabase
        row = {
            "id": 1,
            "fingerprint": "x",
            "razon_social": "TEST",
            "rfc": None,
            "longitud": None,
            "metadata_json": "{}",
        }
        payload = _company_to_supabase(row)
        assert "rfc" not in payload
        assert "longitud" not in payload

    def test_contact_to_supabase_with_mapping(self):
        from src.db.sync import _contact_to_supabase
        row = {
            "company_id": 5,
            "kind": "email",
            "value": "j@empresa.com",
            "value_norm": "j@empresa.com",
        }
        # local_id=5 → remote_id=100
        mapping = {5: 100}
        payload = _contact_to_supabase(row, mapping)
        assert payload is not None
        assert payload["company_id"] == 100
        assert payload["kind"] == "email"

    def test_contact_to_supabase_skips_if_no_mapping(self):
        from src.db.sync import _contact_to_supabase
        row = {"company_id": 99, "kind": "email", "value": "x", "value_norm": "x"}
        # 99 no está en el mapping
        payload = _contact_to_supabase(row, {1: 100})
        assert payload is None

    def test_job_to_supabase(self):
        from src.db.sync import _job_to_supabase
        row = {
            "job_id": "fnx_abc123",
            "nicho": "ropa",
            "zona": "CDMX",
            "meta": 100,
            "errors_json": '[]',
            "stats_json": '{"scout": {"n_post_dedup": 80}}',
            "exports_json": '{}',
        }
        payload = _job_to_supabase(row)
        assert payload["job_id"] == "fnx_abc123"
        assert payload["stats"]["scout"]["n_post_dedup"] == 80
        assert payload["errors"] == []


class TestSupabaseSetup:
    def test_required_tables_list(self):
        from src.db.supabase_setup import REQUIRED_TABLES
        assert "fenix_companies" in REQUIRED_TABLES
        assert "fenix_contacts" in REQUIRED_TABLES
        assert "fenix_jobs" in REQUIRED_TABLES

    def test_check_tables_with_mock(self):
        from src.db.supabase_setup import check_tables, REQUIRED_TABLES

        mock_client = MagicMock()
        # Simular respuesta exitosa
        mock_response = MagicMock()
        mock_response.count = 42
        mock_client.table.return_value.select.return_value.limit.return_value.execute.return_value = mock_response

        result = check_tables(mock_client)
        for table in REQUIRED_TABLES:
            assert table in result
            assert result[table]["exists"] is True

    def test_schema_file_exists_and_creates_expected_tables(self):
        from src.db.supabase_setup import SCHEMA_FILE
        assert SCHEMA_FILE.exists()
        sql = SCHEMA_FILE.read_text()
        for table in ("fenix_companies", "fenix_contacts", "fenix_jobs",
                        "fenix_sync_log", "fenix_opt_outs"):
            assert table in sql


class TestSyncLastSync:
    def test_load_save_last_sync(self, tmp_path, monkeypatch):
        import src.db.sync as sync_mod
        target = tmp_path / "last_sync.json"
        monkeypatch.setattr(sync_mod, "LAST_SYNC_FILE", target)

        # Vacío al inicio
        assert sync_mod._load_last_sync() == {}

        # Guardar y leer
        sync_mod._save_last_sync({"companies_pushed_at": "2026-01-01T00:00:00"})
        loaded = sync_mod._load_last_sync()
        assert loaded["companies_pushed_at"] == "2026-01-01T00:00:00"
