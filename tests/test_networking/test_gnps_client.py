"""GNPS client tests: mock determinism, retry, rate limiting, factory."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import httpx
import pytest

from phyto_shared.exceptions import CloudServiceError, InvalidInputError
from networking.gnps_client import (
    FBMNParams,
    GNPSClient,
    IIMNParams,
    JobHandle,
    JobStatus,
    MNParams,
    MockGNPSClient,
    RateLimiter,
    get_gnps_client,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gnps"
MGF = (FIXTURES / "sample.mgf").read_bytes()


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def mock() -> MockGNPSClient:
    return MockGNPSClient()


class TestMockGNPSClient:
    def test_submit_returns_reproducible_handle(self, mock):
        handle = run(mock.submit_mn_job(MGF, MNParams()))
        assert handle.task_id.startswith("MOCKTASK-")
        assert len(handle.params_hash) == 64
        again = run(mock.submit_mn_job(MGF, MNParams()))
        assert again.task_id == handle.task_id  # deterministic

    def test_parameters_change_the_hash(self, mock):
        base = run(mock.submit_mn_job(MGF, MNParams()))
        tweaked = run(mock.submit_mn_job(MGF, MNParams(min_cosine=0.9)))
        assert tweaked.params_hash != base.params_hash

    def test_status_completes_immediately(self, mock):
        handle = run(mock.submit_mn_job(MGF, MNParams()))
        assert run(mock.get_job_status(handle)) is JobStatus.COMPLETED

    def test_network_shape_from_sample_mgf(self, mock):
        # sample.mgf precursor layout: scans (1,2) and (3,4) differ by
        # residue masses (128.095, 71.037); scan 5 stays isolated
        handle = run(mock.submit_mn_job(MGF, MNParams()))
        results = run(mock.fetch_results(handle))
        assert results.graphml.startswith("<?xml")
        assert len(results.clusters) == 3
        assert sorted(len(cluster.node_ids) for cluster in results.clusters) == [1, 2, 2]

    def test_annotations_exercise_confidence_range(self, mock):
        handle = run(mock.submit_mn_job(MGF, MNParams()))
        results = run(mock.fetch_results(handle))
        assert {a.node_id for a in results.annotations} == {"scan=1", "scan=4"}
        assert all(0.0 <= a.cosine <= 1.0 for a in results.annotations)
        assert all(1 <= a.msi_level <= 4 for a in results.annotations)

    def test_job_metadata_carries_provenance(self, mock):
        handle = run(mock.submit_mn_job(MGF, MNParams()))
        results = run(mock.fetch_results(handle))
        assert results.job_metadata["gnps_task_url"].endswith(handle.task_id)
        assert results.job_metadata["parameters_hash"] == handle.params_hash

    def test_cancel(self, mock):
        handle = run(mock.submit_mn_job(MGF, MNParams()))
        assert run(mock.cancel_job(handle)) is True

    def test_empty_mgf_rejected(self, mock):
        with pytest.raises(InvalidInputError):
            run(mock.submit_mn_job(b"no spectra here", MNParams()))

    def test_fbmn_and_iimn_submit(self, mock):
        fbmn = run(mock.submit_fbmn_job(MGF, b"a,b\n1,2", FBMNParams()))
        iimn = run(
            mock.submit_iimn_job(MGF, b"a,b\n1,2", b"x\ty", IIMNParams())
        )
        assert fbmn.task_id != iimn.task_id


class TestRealClient:
    def _client(self, handler: callable, **kwargs) -> GNPSClient:
        return GNPSClient(
            base_url="https://gnps2.test/api",
            api_key="test-key",
            backoff_base_seconds=0.0,
            transport=httpx.MockTransport(handler),
            **kwargs,
        )

    def test_submit_hits_expected_path_with_api_key(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = str(request.url.path)
            seen["key"] = request.headers.get("X-API-Key")
            return httpx.Response(200, json={"task_id": "TASK-1"})

        client = self._client(handler)
        handle = run(client.submit_mn_job(MGF, MNParams()))
        assert seen["path"] == "/api/molecularnetworking/submit"
        assert seen["key"] == "test-key"
        assert handle.task_id == "TASK-1"
        run(client.aclose())

    def test_retries_on_5xx_then_succeeds(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] <= 2:
                return httpx.Response(500)
            return httpx.Response(200, json={"status": "completed"})

        client = self._client(handler)
        handle = JobHandle(task_id="T", params_hash="0" * 64)
        status = run(client.get_job_status(handle))
        assert status is JobStatus.COMPLETED
        assert calls["n"] == 3
        run(client.aclose())

    def test_retries_exhausted_raises(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(503)

        client = self._client(handler)
        handle = JobHandle(task_id="T", params_hash="0" * 64)
        with pytest.raises(CloudServiceError) as excinfo:
            run(client.get_job_status(handle))
        assert "3 attempts" in str(excinfo.value)
        assert calls["n"] == 3
        run(client.aclose())

    def test_4xx_raises_without_retry(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(404)

        client = self._client(handler)
        handle = JobHandle(task_id="T", params_hash="0" * 64)
        with pytest.raises(CloudServiceError):
            run(client.get_job_status(handle))
        assert calls["n"] == 1
        run(client.aclose())

    def test_missing_api_key_warns(self, caplog, monkeypatch):
        monkeypatch.delenv("GNPS_API_KEY", raising=False)
        with caplog.at_level("WARNING"):
            GNPSClient(
                base_url="https://gnps2.test/api",
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json={})
                ),
            )
        assert "public submissions" in caplog.text


class TestRateLimiter:
    def test_burst_of_five_then_wait(self):
        async def scenario() -> float:
            limiter = RateLimiter(5.0)
            for _ in range(5):
                await limiter.acquire()
            start = time.monotonic()
            await limiter.acquire()  # 6th request inside the 1s window
            return time.monotonic() - start

        elapsed = asyncio.run(scenario())
        assert elapsed >= 0.15


class TestFactory:
    def test_dry_run_forces_mock(self, monkeypatch):
        monkeypatch.setenv("PHYTO_DRY_RUN", "1")
        monkeypatch.setenv("GNPS_API_KEY", "some-key")
        assert isinstance(get_gnps_client(), MockGNPSClient)

    def test_no_key_defaults_to_mock(self, monkeypatch):
        monkeypatch.delenv("PHYTO_DRY_RUN", raising=False)
        monkeypatch.delenv("GNPS_API_KEY", raising=False)
        monkeypatch.delenv("PHYTO_ALLOW_PUBLIC_GNPS", raising=False)
        assert isinstance(get_gnps_client(), MockGNPSClient)

    def test_api_key_gives_real_client(self, monkeypatch):
        # Real mode is an explicit choice: a key alone on the dry-run
        # local plane still gets the mock (ADR-0078 rule). Passing a
        # validated real-mode config selects the real client.
        from networking.runtime_config import NetworkingConfig

        config = NetworkingConfig(
            env="local", dry_run=False, gnps_api_key="secret",
            allow_public_gnps=False,
        )
        assert isinstance(get_gnps_client(config=config), GNPSClient)

    def test_explicit_public_allows_real_client(self):
        from networking.runtime_config import NetworkingConfig

        config = NetworkingConfig(
            env="local", dry_run=False, gnps_api_key=None,
            allow_public_gnps=True,
        )
        assert isinstance(get_gnps_client(config=config), GNPSClient)
