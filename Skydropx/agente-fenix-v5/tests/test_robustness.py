"""Tests de los gaps de robustez (5-8): healthcheck, retry, throttle, checkpoint."""
import json
import time
from pathlib import Path

import pytest


# ===================== Healthcheck =====================

class TestHealthcheck:
    def test_run_healthcheck_returns_report(self):
        from src.core.healthcheck import run_healthcheck, HealthReport
        report = run_healthcheck(meta=100)
        assert isinstance(report, HealthReport)
        # Al menos el output dir y db deberían pasar
        assert report.duration_total_ms >= 0

    def test_summary_has_expected_keys(self):
        from src.core.healthcheck import run_healthcheck
        report = run_healthcheck(meta=100)
        summary = report.summary()
        for k in ("overall_ok", "n_critical", "n_warnings", "duration_total_ms"):
            assert k in summary

    def test_check_output_dir(self):
        from src.core.healthcheck import check_output_dir
        check = check_output_dir()
        assert check.name == "output_dir"
        assert check.passed is True

    def test_check_optional_deps_returns_dict(self):
        from src.core.healthcheck import check_optional_deps
        check = check_optional_deps()
        assert isinstance(check.details, dict)
        for dep in ("phonenumbers", "trafilatura", "tenacity"):
            assert dep in check.details


# ===================== Throttle =====================

class TestThrottle:
    def setup_method(self):
        from src.core.throttle import AutoThrottle
        self.t = AutoThrottle()
        # Reset estado de prueba
        self.t.state.clear()

    def test_first_request_allowed(self):
        allowed, delay, reason = self.t.before_request("https://nuevo-dominio.com/")
        assert allowed is True
        assert delay == 0.0

    def test_failed_requests_escalate(self):
        url = "https://test-bloqueo.com/"
        # Simular 4 fallos seguidos
        for _ in range(4):
            self.t.before_request(url)
            self.t.record_response(url, status_code=429, response_time_ms=100)
        stats = self.t.stats()
        # Después de 4 fallos, debe estar en slowing o quarantine
        slowing_or_quar = (
            any(s["domain"] == "test-bloqueo.com" for s in stats["slowing"])
            or any(q["domain"] == "test-bloqueo.com" for q in stats["quarantined"])
        )
        assert slowing_or_quar

    def test_quarantine_blocks_requests(self):
        url = "https://muy-malo.com/"
        for _ in range(6):
            self.t.before_request(url)
            self.t.record_response(url, status_code=403, response_time_ms=50)
        allowed, _delay, reason = self.t.before_request(url)
        assert allowed is False
        assert "quarantine" in reason.lower()

    def test_block_signal_detection(self):
        url = "https://cloudflare-protected.com/"
        html = "<html>...cloudflare...captcha...</html>"
        self.t.before_request(url)
        self.t.record_response(url, status_code=200, response_time_ms=200,
                                 html_snippet=html)
        # blocked_signals_count debe haber incrementado
        ds = self.t.state.get("cloudflare-protected.com")
        assert ds is not None
        assert ds.blocked_signals_count >= 1


# ===================== Retry Queue =====================

class TestRetryQueue:
    def test_enqueue_and_get_due(self, isolated_db):
        from src.db.retry_queue import RetryQueue
        from src.db.repositories import JobRepository
        # Crear company + job para FK
        JobRepository(isolated_db).create("job_retry", nicho="x", zona="y")
        isolated_db.execute(
            "INSERT INTO companies (fingerprint, razon_social) VALUES (?, ?)",
            ("test_fp", "EMPRESA TEST"),
        )
        company_id = isolated_db.fetch_value("SELECT id FROM companies LIMIT 1")

        rq = RetryQueue(isolated_db)
        ok = rq.enqueue(company_id, target="find_domain",
                         reason="no_web_in_denue",
                         payload={"nombre": "EMPRESA TEST"})
        assert ok

        # No debe estar due todavía (backoff de 24h)
        due_now = rq.get_due(target="find_domain")
        assert len(due_now) == 0

    def test_mark_success_changes_status(self, isolated_db):
        from src.db.retry_queue import RetryQueue
        from src.db.repositories import JobRepository
        JobRepository(isolated_db).create("job_retry2", nicho="x", zona="y")
        isolated_db.execute(
            "INSERT INTO companies (fingerprint, razon_social) VALUES (?, ?)",
            ("test_fp2", "EMPRESA TEST 2"),
        )
        company_id = isolated_db.fetch_value("SELECT id FROM companies LIMIT 1")

        rq = RetryQueue(isolated_db)
        rq.enqueue(company_id, target="infer_email", reason="missing")
        entry_id = isolated_db.fetch_value("SELECT id FROM retry_queue WHERE company_id=?",
                                            (company_id,))
        rq.mark_success(entry_id)
        status = isolated_db.fetch_value(
            "SELECT status FROM retry_queue WHERE id=?", (entry_id,)
        )
        assert status == "succeeded"

    def test_stats_returns_counts(self, isolated_db):
        from src.db.retry_queue import RetryQueue
        rq = RetryQueue(isolated_db)
        stats = rq.stats()
        for k in ("total", "pending", "due_now", "succeeded", "exhausted"):
            assert k in stats


# ===================== Checkpoint =====================

class TestCheckpoint:
    def test_save_and_load(self, tmp_path, monkeypatch):
        # Redirigir CHECKPOINT_DIR a tmp_path
        from src.agents import checkpoint as cp_mod
        monkeypatch.setattr(cp_mod, "CHECKPOINT_DIR", tmp_path)

        from src.core.models import (
            PipelineState, ResearchPlan, RawRecord,
            ModeloNegocio, Canal, NivelUsuario, Estrategia,
        )
        plan = ResearchPlan(
            nicho="test_chkpt", meta=500, zona="CDMX",
            modelo=ModeloNegocio.B2C, canal=Canal.WEB,
            nivel_usuario=NivelUsuario.INTERMEDIO,
            estrategia=Estrategia.STANDARD,
        )
        state = PipelineState(plan=plan)
        state.fase_actual = "scout"
        state.candidatos = [
            RawRecord(source="denue", empresa="EMPRESA TEST",
                       email="test@empresa.com"),
        ]

        # Guardar
        cp_path = cp_mod.save_checkpoint(state, agent_completed="scout")
        assert cp_path.exists()

        # Cargar
        loaded = cp_mod.load_checkpoint(state.job_id)
        assert loaded is not None
        assert loaded.job_id == state.job_id
        assert loaded.plan.nicho == "test_chkpt"
        assert loaded.fase_actual == "scout"
        assert len(loaded.candidatos) == 1
        assert loaded.candidatos[0].empresa == "EMPRESA TEST"

    def test_list_pending(self, tmp_path, monkeypatch):
        from src.agents import checkpoint as cp_mod
        monkeypatch.setattr(cp_mod, "CHECKPOINT_DIR", tmp_path)

        from src.core.models import (
            PipelineState, ResearchPlan, ModeloNegocio,
            Canal, NivelUsuario, Estrategia,
        )
        plan = ResearchPlan(nicho="x", meta=500, zona="y",
                              modelo=ModeloNegocio.B2C, canal=Canal.WEB,
                              nivel_usuario=NivelUsuario.INTERMEDIO,
                              estrategia=Estrategia.STANDARD)
        state = PipelineState(plan=plan)
        state.fase_actual = "scout"
        cp_mod.save_checkpoint(state, agent_completed="scout")

        pending = cp_mod.list_pending()
        assert len(pending) >= 1
        assert pending[0]["agent_completed"] == "scout"

    def test_completed_pipeline_not_in_pending(self, tmp_path, monkeypatch):
        """Pipeline completo (llegó a self_improver) NO debe aparecer en pending."""
        from src.agents import checkpoint as cp_mod
        monkeypatch.setattr(cp_mod, "CHECKPOINT_DIR", tmp_path)

        from src.core.models import (
            PipelineState, ResearchPlan, ModeloNegocio,
            Canal, NivelUsuario, Estrategia,
        )
        plan = ResearchPlan(nicho="x", meta=500, zona="y",
                              modelo=ModeloNegocio.B2C, canal=Canal.WEB,
                              nivel_usuario=NivelUsuario.INTERMEDIO,
                              estrategia=Estrategia.STANDARD)
        state = PipelineState(plan=plan)
        state.fase_actual = "self_improver"
        cp_mod.save_checkpoint(state, agent_completed="self_improver")

        pending = cp_mod.list_pending()
        assert len(pending) == 0
