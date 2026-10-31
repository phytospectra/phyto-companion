"""Runtime plane selection for the networking service (ADR-0078 rule).

The defect these tests pin: ``get_gnps_client()`` silently returned
``MockGNPSClient`` whenever no API key was set, on any plane -- a cloud
deployment returned fabricated job handles and fake network summaries
while looking identical to a real submission. The tests assert the
fail-closed contract instead: real mode without an authorized GNPS
access route refuses startup, and ``/health`` reports the actual mode.
"""

from __future__ import annotations

import pytest

from networking.gnps_client import MockGNPSClient
from networking.main import create_app, get_runtime
from networking.runtime_config import NetworkingConfig, NetworkingConfigError

from phyto_shared.exceptions import CloudServiceError


@pytest.fixture(autouse=True)
def fresh_runtime_cache():
    """The module-level lru_cache pins the first runtime forever; every
    test must see the plane its own env describes."""
    get_runtime.cache_clear()
    yield
    get_runtime.cache_clear()


@pytest.fixture()
def local_env(monkeypatch):
    monkeypatch.setenv("PHYTO_ENV", "local")
    monkeypatch.delenv("PHYTO_DRY_RUN", raising=False)
    monkeypatch.delenv("GNPS_API_KEY", raising=False)
    monkeypatch.delenv("PHYTO_ALLOW_PUBLIC_GNPS", raising=False)


class TestConfigFromEnv:
    def test_local_defaults_to_dry_run(self, local_env):
        cfg = NetworkingConfig.from_env()
        assert cfg.env == "local"
        assert cfg.dry_run is True
        assert cfg.mode == "local-mock"

    def test_cloud_defaults_to_real_mode(self, monkeypatch):
        monkeypatch.setenv("PHYTO_ENV", "cloud")
        monkeypatch.delenv("PHYTO_DRY_RUN", raising=False)
        assert NetworkingConfig.from_env().dry_run is False

    def test_explicit_dry_run_wins(self, monkeypatch):
        monkeypatch.setenv("PHYTO_ENV", "cloud")
        monkeypatch.setenv("PHYTO_DRY_RUN", "1")
        assert NetworkingConfig.from_env().dry_run is True


class TestValidation:
    def test_real_mode_without_route_refuses(self, monkeypatch):
        monkeypatch.setenv("PHYTO_ENV", "cloud")
        monkeypatch.delenv("PHYTO_DRY_RUN", raising=False)
        monkeypatch.delenv("GNPS_API_KEY", raising=False)
        monkeypatch.delenv("PHYTO_ALLOW_PUBLIC_GNPS", raising=False)
        with pytest.raises(NetworkingConfigError) as excinfo:
            NetworkingConfig.from_env().validate()
        assert "GNPS_API_KEY" in str(excinfo.value)
        assert "Refusing to start" in str(excinfo.value)

    def test_real_mode_with_api_key_passes(self, monkeypatch):
        monkeypatch.setenv("PHYTO_ENV", "cloud")
        monkeypatch.delenv("PHYTO_DRY_RUN", raising=False)
        monkeypatch.setenv("GNPS_API_KEY", "k")
        assert NetworkingConfig.from_env().validate() is not None

    def test_real_mode_with_explicit_public_optin_passes(self, monkeypatch):
        monkeypatch.setenv("PHYTO_ENV", "cloud")
        monkeypatch.delenv("PHYTO_DRY_RUN", raising=False)
        monkeypatch.setenv("PHYTO_ALLOW_PUBLIC_GNPS", "1")
        assert NetworkingConfig.from_env().validate() is not None


class TestFactoryFailClosed:
    def test_real_config_without_route_raises_never_mock(self):
        config = NetworkingConfig(
            env="cloud", dry_run=False, gnps_api_key=None,
            allow_public_gnps=False,
        )
        from networking.gnps_client import get_gnps_client

        with pytest.raises(CloudServiceError, match="GNPS_API_KEY"):
            get_gnps_client(config=config)

    def test_real_config_with_key_gives_real_client(self):
        from networking.gnps_client import GNPSClient, get_gnps_client

        config = NetworkingConfig(
            env="cloud", dry_run=False, gnps_api_key="k",
            allow_public_gnps=False,
        )
        assert isinstance(get_gnps_client(config=config), GNPSClient)


class TestRuntime:
    def test_local_runtime_uses_mock(self, local_env):
        runtime = get_runtime.__wrapped__()
        assert isinstance(runtime.client, MockGNPSClient)
        assert runtime.config.mode == "local-mock"

    def test_real_runtime_missing_route_raises_at_construction(self, monkeypatch):
        monkeypatch.setenv("PHYTO_ENV", "cloud")
        monkeypatch.delenv("PHYTO_DRY_RUN", raising=False)
        monkeypatch.delenv("GNPS_API_KEY", raising=False)
        monkeypatch.delenv("PHYTO_ALLOW_PUBLIC_GNPS", raising=False)
        with pytest.raises(NetworkingConfigError):
            get_runtime.__wrapped__()

    def test_create_app_real_mode_missing_route_fails_startup(self, monkeypatch):
        monkeypatch.setenv("PHYTO_ENV", "cloud")
        monkeypatch.delenv("PHYTO_DRY_RUN", raising=False)
        monkeypatch.delenv("GNPS_API_KEY", raising=False)
        monkeypatch.delenv("PHYTO_ALLOW_PUBLIC_GNPS", raising=False)
        with pytest.raises(NetworkingConfigError):
            create_app()
