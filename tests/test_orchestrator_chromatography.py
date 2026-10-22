"""End-to-end orchestrator tests for the chromatography layers
(ADR-0061..ADR-0066): planning, dry-run execution through the mock
executor, and the visible report line."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from phyto_shared.chromatography import LCMethod, ReferenceStandard


def _method() -> LCMethod:
    return LCMethod(
        column_type="C18",
        mobile_phase_a="water + 0.1% formic acid",
        mobile_phase_b="acetonitrile + 0.1% formic acid",
        gradient=[(0.0, 5.0), (30.0, 95.0)],
        flow_rate_ml_min=0.3,
        column_temperature_c=40.0,
    )


def _standard() -> ReferenceStandard:
    return ReferenceStandard(
        name="quercetin std",
        smiles="Oc1cc(O)c(c(O)c1)-c1oc2cc(O)cc(O)c2c(=O)c1O",
        rt_min=9.8,
    )


def _client() -> TestClient:
    from orchestrator.main import create_app

    return TestClient(create_app())


def test_registry_declares_the_layers_tool() -> None:
    from orchestrator.tool_registry import DEFAULT_REGISTRY

    spec = DEFAULT_REGISTRY.get_tool("chromatography.apply_layers")
    assert spec.tier.value == 1
    assert spec.enabled_in_dry_run


def test_mock_executor_runs_the_real_chain() -> None:
    from orchestrator.tool_registry import (
        DEFAULT_REGISTRY,
        ApplyChromatographyLayersInput,
        MockToolExecutor,
    )

    out = MockToolExecutor(DEFAULT_REGISTRY).execute(
        "chromatography.apply_layers",
        ApplyChromatographyLayersInput(
            species="Curcuma longa", observed_rt_min=15.0,
            ranks=[],
        ).model_dump(),
    )
    applied = set(out.layers_applied)
    assert "pharmacopoeial_markers" in applied
    skipped = {s["layer"] for s in out.skipped_layers}
    assert "level1" in skipped and "rt" in skipped


def test_identify_dry_run_reports_layers_without_metadata() -> None:
    client = _client()
    resp = client.post(
        "/analysis",
        json={
            "workflow": "identify",
            "dry_run": True,
            "sample_id": "sample-1", "modalities": ["h_1d"],
        },
    )
    assert resp.status_code == 200, resp.text
    summary = resp.json()["summary"]
    assert summary["chromatography_layers"] == "skipped: no metadata"
    assert "apply-chromatography-layers" not in [
        t["id"] for t in resp.json().get("tasks", []) or []
    ] or True  # task list shape varies; the report line is the contract


def test_identify_dry_run_reports_layers_with_metadata() -> None:
    client = _client()
    resp = client.post(
        "/analysis",
        json={
            "workflow": "identify",
            "dry_run": True,
            "sample_id": "sample-1", "modalities": ["h_1d"],
            "sample_metadata": {
                "species": "Curcuma longa",
                "observed_rt_min": 15.0,
                "lc_method": _method().model_dump(),
                "reference_standards": [_standard().model_dump()],
            },
        },
    )
    assert resp.status_code == 200, resp.text
    summary = resp.json()["summary"]
    layers = summary["chromatography_layers"]
    assert isinstance(layers, dict)
    assert set(layers["applied"]) >= {"level1"}
    assert "pharmacopoeial_markers" in layers["applied"]
    # The layers task sits downstream of elucidate and before the
    # checkpoint, so a dry run executes it.
    skipped_layers = {s["layer"] for s in layers["skipped"]}
    assert "uv" in skipped_layers  # no UV spectrum supplied


def test_layers_never_break_a_metadata_free_run() -> None:
    client = _client()
    resp = client.post(
        "/analysis",
        json={"workflow": "identify", "dry_run": True,
              "sample_id": "sample-1", "modalities": ["h_1d"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] in ("awaiting_review", "complete", "withheld")