"""Runtime plane selection for the elucidation service (ADR-0078).

The defect these tests pin: ``_Runtime`` used to construct
``MockStructureDB()`` and call ``get_shift_predictor()`` with no arguments,
so switching ``PHYTO_ENV`` to ``cloud`` did NOT activate real prediction —
it silently served mock science. The tests below assert the fail-closed
contract instead: real mode with missing configuration refuses to start,
and an unconfigured capability answers 503 rather than pretending.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from phyto_shared.exceptions import CloudServiceError
from elucidation.runtime_config import ElucidationConfig, ElucidationConfigError
from elucidation.shift_predictor import MockShiftPredictor


@pytest.fixture()
def local_env(monkeypatch):
    monkeypatch.setenv("PHYTO_ENV", "local")
    monkeypatch.delenv("PHYTO_DRY_RUN", raising=False)
    monkeypatch.delenv("VERTEX_ENDPOINT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)


class TestConfigFromEnv:
    def test_local_defaults_to_dry_run(self, local_env):
        cfg = ElucidationConfig.from_env()
        assert cfg.env == "local"
        assert cfg.dry_run is True
        assert cfg.mode == "local-mock"

    def test_cloud_defaults_to_real_mode(self, monkeypatch):
        monkeypatch.setenv("PHYTO_ENV", "cloud")
        monkeypatch.delenv("PHYTO_DRY_RUN", raising=False)
        cfg = ElucidationConfig.from_env()
        assert cfg.dry_run is False
        assert cfg.mode == "cloud-real"

    def test_explicit_dry_run_wins(self, monkeypatch):
        monkeypatch.setenv("PHYTO_ENV", "cloud")
        monkeypatch.setenv("PHYTO_DRY_RUN", "1")
        assert ElucidationConfig.from_env().dry_run is True


class TestValidation:
    def test_real_mode_without_endpoint_refuses(self, monkeypatch):
        monkeypatch.setenv("PHYTO_ENV", "cloud")
        monkeypatch.delenv("PHYTO_DRY_RUN", raising=False)
        monkeypatch.delenv("VERTEX_ENDPOINT_ID", raising=False)
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
        with pytest.raises(ElucidationConfigError) as excinfo:
            ElucidationConfig.from_env().validate()
        assert "VERTEX_ENDPOINT_ID" in str(excinfo.value)
        assert "Refusing to start" in str(excinfo.value)

    def test_real_mode_without_project_refuses(self, monkeypatch):
        monkeypatch.setenv("PHYTO_ENV", "cloud")
        monkeypatch.delenv("PHYTO_DRY_RUN", raising=False)
        monkeypatch.setenv("VERTEX_ENDPOINT_ID", "1")
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        with pytest.raises(ElucidationConfigError) as excinfo:
            ElucidationConfig.from_env().validate()
        assert "GOOGLE_CLOUD_PROJECT" in str(excinfo.value)

    def test_dry_run_skips_validation(self, local_env):
        assert ElucidationConfig.from_env().validate().dry_run is True


class TestRuntime:
    def test_local_runtime_is_mock(self, local_env):
        from elucidation import main

        runtime = main._Runtime()
        assert isinstance(runtime.predictor, MockShiftPredictor)
        assert runtime.config.mode == "local-mock"
        assert runtime._db is not None

    def test_real_runtime_never_constructs_the_mock_db(self, monkeypatch):
        cfg = ElucidationConfig(
            env="cloud",
            dry_run=False,
            vertex_endpoint_id="endpoint-1",
            gcp_project="proj",
            vertex_location="us-central1",
        )
        from elucidation import main

        runtime = main._Runtime(cfg)
        # The runtime carries the REAL client, never the mock, and its
        # database slot stays empty (explicit unavailability).
        from elucidation.shift_predictor import VertexAIShiftPredictor

        assert isinstance(runtime.predictor, VertexAIShiftPredictor)
        assert runtime._db is None
        # And the real client fails closed without the SDK: prediction is
        # a loud CloudServiceError, never mock shift values.
        with pytest.raises(CloudServiceError):
            runtime.predictor.predict("CCO")

    def test_unconfigured_db_raises_cloud_service_error(self, local_env):
        from elucidation import main

        runtime = main._Runtime()
        runtime._db = None  # simulate "real mode, no provider wired"
        with pytest.raises(CloudServiceError) as excinfo:
            runtime.db
        assert excinfo.value.context["capability"] == "structure_db"


class TestHealth:
    def test_health_reports_mode_and_db_availability(self, local_env):
        from elucidation import main

        main.get_runtime.cache_clear()
        with TestClient(main.app) as client:
            body = client.get("/health").json()
        main.get_runtime.cache_clear()
        assert body["predictor"] == "MockShiftPredictor"
        assert body["predictor_mode"] == "local-mock"
        assert body["structure_db_available"] is True
        assert body["db_size"] >= 8

    def test_missing_db_answers_503_not_mock_data(self, local_env):
        from elucidation import main

        main.get_runtime.cache_clear()
        runtime = main.get_runtime()
        runtime._db = None
        try:
            with TestClient(main.app) as client:
                response = client.post(
                    "/elucidate/candidates",
                    json={"observed": {"h_1d": [7.0]}, "formula": "C6H6O"},
                )
        finally:
            main.get_runtime.cache_clear()
        assert response.status_code == 503
        assert response.json()["context"]["capability"] == "structure_db"
        assert response.json()["detail"].startswith("no structure database")
