"""Runtime plane selection for the pharmacology service (ADR-0078 rule).

The defect these tests pin: ``get_knowledge_graph()`` served the
in-memory networkx graph on every plane; a cloud deployment without a
real graph backend answered production requests with mock
traditional-use records ("LOTUS (mock)") while looking identical to a
real database. The tests assert the fail-closed contract instead: real
mode without ``NEO4J_URI`` refuses startup, and real mode stays
explicitly unavailable until the Step 8 Neo4j backend is wired.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from auth.config import AuthConfig
from fastapi.testclient import TestClient

from phyto_shared.exceptions import CloudServiceError
from pharmacology.knowledge_graph import NetworkxKnowledgeGraph
from pharmacology.main import create_app, get_runtime
from pharmacology.runtime_config import (
    PharmacologyConfig,
    PharmacologyConfigError,
)


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
    monkeypatch.delenv("NEO4J_URI", raising=False)


class TestConfigFromEnv:
    def test_local_defaults_to_dry_run(self, local_env):
        cfg = PharmacologyConfig.from_env()
        assert cfg.env == "local"
        assert cfg.dry_run is True
        assert cfg.mode == "local-mock"

    def test_cloud_defaults_to_real_mode(self, monkeypatch):
        monkeypatch.setenv("PHYTO_ENV", "cloud")
        monkeypatch.delenv("PHYTO_DRY_RUN", raising=False)
        assert PharmacologyConfig.from_env().dry_run is False

    def test_explicit_dry_run_wins(self, monkeypatch):
        monkeypatch.setenv("PHYTO_ENV", "cloud")
        monkeypatch.setenv("PHYTO_DRY_RUN", "1")
        assert PharmacologyConfig.from_env().dry_run is True


class TestValidation:
    def test_real_mode_without_neo4j_uri_refuses(self, monkeypatch):
        monkeypatch.setenv("PHYTO_ENV", "cloud")
        monkeypatch.delenv("PHYTO_DRY_RUN", raising=False)
        monkeypatch.delenv("NEO4J_URI", raising=False)
        with pytest.raises(PharmacologyConfigError) as excinfo:
            PharmacologyConfig.from_env().validate()
        assert "NEO4J_URI" in str(excinfo.value)
        assert "Refusing to start" in str(excinfo.value)

    def test_dry_run_skips_validation(self, monkeypatch):
        monkeypatch.setenv("PHYTO_ENV", "cloud")
        monkeypatch.setenv("PHYTO_DRY_RUN", "1")
        monkeypatch.delenv("NEO4J_URI", raising=False)
        assert PharmacologyConfig.from_env().validate() is not None


class TestFactoryFailClosed:
    def test_real_config_without_uri_raises_never_networkx(self):
        from pharmacology.knowledge_graph import get_knowledge_graph

        config = PharmacologyConfig(env="cloud", dry_run=False, neo4j_uri=None)
        with pytest.raises(CloudServiceError, match="NEO4J_URI"):
            get_knowledge_graph(config=config)

    def test_real_config_with_uri_is_explicitly_unavailable_until_step8(self):
        """Honest unavailability beats a mock: until the Neo4j backend is
        wired (Step 8), real mode answers with a named error, never the
        in-memory graph."""
        from pharmacology.knowledge_graph import get_knowledge_graph

        config = PharmacologyConfig(
            env="cloud", dry_run=False, neo4j_uri="bolt://neo4j:7687"
        )
        with pytest.raises(CloudServiceError, match="Step 8"):
            get_knowledge_graph(config=config)


class TestRuntime:
    def test_local_runtime_uses_networkx(self, local_env):
        runtime = get_runtime.__wrapped__()
        assert isinstance(runtime.kg, NetworkxKnowledgeGraph)
        assert runtime.config.mode == "local-mock"

    def test_real_runtime_missing_uri_raises_at_construction(self, monkeypatch):
        monkeypatch.setenv("PHYTO_ENV", "cloud")
        monkeypatch.delenv("PHYTO_DRY_RUN", raising=False)
        monkeypatch.delenv("NEO4J_URI", raising=False)
        with pytest.raises(PharmacologyConfigError):
            get_runtime.__wrapped__()

    def test_create_app_real_mode_missing_uri_fails_startup(self, monkeypatch):
        monkeypatch.setenv("PHYTO_ENV", "cloud")
        monkeypatch.delenv("PHYTO_DRY_RUN", raising=False)
        monkeypatch.delenv("NEO4J_URI", raising=False)
        # Keep auth collaborators offline while the scientific runtime still
        # reads the real-mode environment above. No cloud SDK/ADC is needed.
        auth_config = replace(AuthConfig.from_env(), dry_run=True, enabled=True)
        with pytest.raises(PharmacologyConfigError):
            create_app(auth_config)

    def test_create_app_local_reports_mock_mode(self, local_env):
        client = TestClient(create_app())
        body = client.get("/health").json()
        assert body["knowledge_graph"] == "NetworkxKnowledgeGraph"
        assert body["kg_mode"] == "local-mock"
        assert body["dry_run"] is True
