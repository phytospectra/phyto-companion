"""Runtime plane selection for the metabolomics service (ADR-0078 rule).

The defect these tests pin: ``get_detector()`` silently returned
``MockDetector`` whenever ``MZMINE_HOME`` was unset, on any plane, and
``_Runtime`` was built lazily at first request -- so a cloud deployment
without an MZmine installation served mock feature detection while
looking identical to a real one. The tests assert the fail-closed
contract instead: real mode with missing configuration refuses startup,
and ``/health`` reports the actual detector mode.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from auth.config import AuthConfig
from fastapi.testclient import TestClient

from metabolomics.feature_detection import MockDetector
from metabolomics.main import create_app, get_runtime
from metabolomics.runtime_config import (
    MetabolomicsConfig,
    MetabolomicsConfigError,
)


@pytest.fixture(autouse=True)
def fresh_runtime_cache():
    """The module-level lru_cache pins the first runtime forever; every
    test must see the plane its own env describes (found by test: the
    cloud startup-failure test otherwise received the local runtime the
    module import had cached)."""
    get_runtime.cache_clear()
    yield
    get_runtime.cache_clear()


@pytest.fixture()
def local_env(monkeypatch):
    monkeypatch.setenv("PHYTO_ENV", "local")
    monkeypatch.delenv("PHYTO_DRY_RUN", raising=False)
    monkeypatch.delenv("MZMINE_HOME", raising=False)


class TestConfigFromEnv:
    def test_local_defaults_to_dry_run(self, local_env):
        cfg = MetabolomicsConfig.from_env()
        assert cfg.env == "local"
        assert cfg.dry_run is True
        assert cfg.mode == "local-mock"

    def test_cloud_defaults_to_real_mode(self, monkeypatch):
        monkeypatch.setenv("PHYTO_ENV", "cloud")
        monkeypatch.delenv("PHYTO_DRY_RUN", raising=False)
        cfg = MetabolomicsConfig.from_env()
        assert cfg.dry_run is False
        assert cfg.mode == "cloud-real"

    def test_explicit_dry_run_wins(self, monkeypatch):
        monkeypatch.setenv("PHYTO_ENV", "cloud")
        monkeypatch.setenv("PHYTO_DRY_RUN", "1")
        assert MetabolomicsConfig.from_env().dry_run is True


class TestValidation:
    def test_real_mode_without_mzmine_home_refuses(self, monkeypatch):
        monkeypatch.setenv("PHYTO_ENV", "cloud")
        monkeypatch.delenv("PHYTO_DRY_RUN", raising=False)
        monkeypatch.delenv("MZMINE_HOME", raising=False)
        with pytest.raises(MetabolomicsConfigError) as excinfo:
            MetabolomicsConfig.from_env().validate()
        assert "MZMINE_HOME" in str(excinfo.value)
        assert "Refusing to start" in str(excinfo.value)

    def test_dry_run_skips_validation(self, monkeypatch):
        monkeypatch.setenv("PHYTO_ENV", "cloud")
        monkeypatch.setenv("PHYTO_DRY_RUN", "1")
        monkeypatch.delenv("MZMINE_HOME", raising=False)
        assert MetabolomicsConfig.from_env().validate() is not None

    def test_real_mode_with_mzmine_home_passes(self, monkeypatch):
        monkeypatch.setenv("PHYTO_ENV", "cloud")
        monkeypatch.delenv("PHYTO_DRY_RUN", raising=False)
        monkeypatch.setenv("MZMINE_HOME", "/opt/mzmine")
        assert MetabolomicsConfig.from_env().validate().mzmine_home == "/opt/mzmine"


class TestRuntime:
    def test_local_runtime_uses_mock(self, local_env):
        runtime = get_runtime.__wrapped__()  # bypass lru_cache
        assert isinstance(runtime.detector, MockDetector)
        assert runtime.config.mode == "local-mock"

    def test_real_runtime_missing_home_raises_at_construction(self, monkeypatch):
        monkeypatch.setenv("PHYTO_ENV", "cloud")
        monkeypatch.delenv("PHYTO_DRY_RUN", raising=False)
        monkeypatch.delenv("MZMINE_HOME", raising=False)
        with pytest.raises(MetabolomicsConfigError):
            get_runtime.__wrapped__()

    def test_create_app_local_reports_mock_mode(self, local_env):
        client = TestClient(create_app())
        body = client.get("/health").json()
        assert body["detector"] == "MockDetector"
        assert body["detector_mode"] == "local-mock"
        assert body["dry_run"] is True

    def test_create_app_real_mode_missing_home_fails_startup(self, monkeypatch):
        # The container entrypoint builds the app at import time; a cloud
        # deployment without MZMINE_HOME must fail THERE, not on the
        # first /metabolomics/detect request.
        monkeypatch.setenv("PHYTO_ENV", "cloud")
        monkeypatch.delenv("PHYTO_DRY_RUN", raising=False)
        monkeypatch.delenv("MZMINE_HOME", raising=False)
        # Keep auth collaborators offline while the scientific runtime still
        # reads the real-mode environment above. No cloud SDK/ADC is needed.
        auth_config = replace(AuthConfig.from_env(), dry_run=True, enabled=True)
        with pytest.raises(MetabolomicsConfigError):
            create_app(auth_config)
