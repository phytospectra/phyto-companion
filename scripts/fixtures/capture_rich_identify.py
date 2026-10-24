"""Capture script for the rich identify fixture
(frontend/src/__fixtures__/rich_identify_response.json).

Regeneration (from the repo root, with the project venv):

  PYTHONPATH="shared:build/auth-path:services/orchestrator/app" \
    .venv/bin/python scripts/fixtures/capture_rich_identify.py \
    frontend/src/__fixtures__/rich_identify_response.json

The ``services/orchestrator/app`` entry on PYTHONPATH is REQUIRED: without
it ``_mock_apply_chromatography_layers`` cannot import ``layer_chain`` and
silently degrades to "layer chain not staged in this plane" (all 5 layers
skipped), which does NOT match the checked-in fixture (4 layers applied).
Verify a regeneration with the deterministic-diff check in
HANDOFF_frontend_followup_pass1.md.

The output is deterministic except for inherently nondeterministic fields:
run_id, provenance_manifest_id, citation/manifest hashes, task created_at
timestamps, and summary wall_seconds. Everything else — including the full
layer_chain_over_ranks CandidateRank surface — must reproduce exactly.

Runs a rich identify workflow through the orchestrator TestClient with
every layer's metadata supplied (Level-1 standards, LC method, observed
RT, flavonol UV spectrum, purity, species + extract hint + isolation
record), then dumps:

  * the full /analysis response (summary carries chromatography_layers,
    isolation_layer, top_candidate_msi_level, pruned_tasks, evidence_gap)
  * the /runs/{run_id} status response
  * the ranks from the apply-chromatography-layers task output (evidence
    chains, isolation evidence, classification_chain) and the elucidate
    task output (taxon reranker items)
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, "shared")
sys.path.insert(0, "build/auth-path")
sys.path.insert(0, "tests")


from fastapi.testclient import TestClient  # noqa: E402

from phyto_shared.isolation import (  # noqa: E402
    Fraction,
    IsolationCompound,
    IsolationRecord,
    TLCObservation,
    TLCResponse,
)
from phyto_shared.chromatography import LCMethod, ReferenceStandard  # noqa: E402

QUERCETIN = "Oc1cc(O)c(c(O)c1)-c1oc2cc(O)cc(O)c2c(=O)c1O"
ISOQUERCITRIN = (
    "C1=CC(=C(C=C1C2=C(C(=O)C3=C(C=C(C=C3O2)O)O)O)OC4C(C(C(C(O4)CO)O)O)O)O"
)


def main() -> None:
    from orchestrator.main import create_app
    from orchestrator import tool_registry

    client = TestClient(create_app())

    method = LCMethod(
        column_type="C18",
        mobile_phase_a="water + 0.1% formic acid",
        mobile_phase_b="acetonitrile + 0.1% formic acid",
        gradient=[(0.0, 5.0), (30.0, 95.0)],
        flow_rate_ml_min=0.3,
        column_temperature_c=40.0,
    ).model_dump()

    standard = ReferenceStandard(
        name="quercetin std", smiles=QUERCETIN, rt_min=9.8
    ).model_dump()

    # flavonol-like two-band PDA trace
    uv = [
        (float(w), max(0.0, 2.0 - abs(w - 370.0) / 8.0)
         + max(0.0, 1.0 - abs(w - 280.0) / 10.0))
        for w in range(220, 421, 5)
    ]

    # Seed a bench isolation record for the extract (isolation MVP path)
    record = IsolationRecord(
        extract_id="EXT-2024-042",
        species="Curcuma longa",
        plant_part="rhizome",
        mass_starting_g=120.5,
        extraction_solvent="EtOH/H2O 7:3",
        fractions=[
            Fraction(fraction_id="F1", polarity_tier="very_nonpolar",
                     solvent="hexane"),
            Fraction(fraction_id="F2", polarity_tier="mid",
                     solvent="EtOAc"),
            Fraction(fraction_id="F3", polarity_tier="polar",
                     solvent="MeOH/H2O 1:1"),
        ],
        compounds=[
            IsolationCompound(
                compound_id="CMP-1",
                present_in_fractions=["F2", "F3"],
                tlc=TLCObservation(
                    rf=0.42,
                    solvent_system="EtOAc:MeOH:H2O 8:2:0.5",
                    visualization=[
                        TLCResponse(reagent="UV365", response="fluorescent"),
                        TLCResponse(reagent="Naturstoff",
                                    response="positive_strong", color="yellow"),
                    ],
                ),
                flash_uv_lambda_max_nm=370.0,
                yield_mg=48.2,
                purity=0.91,
                isolation_chain=["F2", "F2.3"],
            ),
        ],
        collection_country="IN",
        collection_permit="PIC-2019-ABC123",
        abs_status="clear",
        user_id="gap-audit",
    )
    tool_registry._MOCK_ISOLATION_RECORDS["EXT-2024-042"] = record

    request = {
        "workflow": "identify",
        "dry_run": True,
        "sample_id": "sample-rich-1",
        "modalities": ["h_1d", "c_1d", "hsqc", "ms2"],
        "sample_metadata": {
            "species": "Curcuma longa",
            "genus": "Curcuma",
            "family": "Zingiberaceae",
            "extraction_solvent": "EtOH/H2O 7:3",
            "extract_id": "EXT-2024-042",
            "observed_rt_min": 9.9,
            "observed_uv_spectrum": uv,
            "lc_method": method,
            "reference_standards": [standard],
        },
    }

    resp = client.post("/analysis", json=request)
    assert resp.status_code == 200, resp.text
    analysis = resp.json()
    run_id = analysis["run_id"]

    status = client.get(f"/runs/{run_id}")
    assert status.status_code == 200, status.text
    status_json = status.json()

    citation = client.get(f"/runs/{run_id}/citation")
    citation_json = citation.json() if citation.status_code == 200 else None

    # Pull the rich task outputs (ranks with evidence chains) from the
    # runtime graph — this is the data a result panel must render.
    from orchestrator.main import get_runtime
    graph = get_runtime().runs[run_id]["graph"]
    task_outputs = {}
    for t in graph.tasks:
        if isinstance(t.output, dict) and t.output:
            try:
                task_outputs[t.id] = json.loads(json.dumps(t.output, default=str))
            except Exception:
                task_outputs[t.id] = {"_unserialisable": True}

    payload = {
        "_capture_note": (
            "Gap-audit fixture (2026-10-22): rich identify dry-run through "
            "the orchestrator local plane with every chromatography layer's "
            "metadata, a seeded isolation record, taxon metadata and UV/RT/"
            "purity inputs. Sections: analysis_response (POST /analysis), "
            "run_status (GET /runs/{id}), citation, task_outputs (the ranks "
            "each layer's task produced — the data a result panel renders). "
            "NOTE: in the dry-run the apply-chromatography-layers task "
            "received zero ranks (it ran before elucidate), so the section "
            "layer_chain_over_ranks additionally runs the REAL backend "
            "chain (layer_chain.apply_chromatography_layers — the exact "
            "function the layers tool calls) over the elucidate ranks with "
            "the same metadata, to expose the full CandidateRank field "
            "surface a user-facing report must render."
        ),
        "analysis_response": analysis,
        "run_status": status_json,
        "citation": citation_json,
        "task_outputs": task_outputs,
    }

    # Real layer chain over the elucidate ranks (same metadata as above).
    try:
        from orchestrator.layer_chain import apply_chromatography_layers
        from phyto_shared.uv_chromophore import UVClass  # noqa: F401

        el_ranks_raw = task_outputs.get("elucidate", {}).get("ranks", [])
        from phyto_shared.models import Candidate, CandidateRank, EvidenceGraph

        el_ranks = []
        for r in el_ranks_raw:
            c = r["candidate"]
            candidate = Candidate(
                id=c["id"], name=c["name"], smiles=c["smiles"],
                formula=c["formula"], exact_mass=c["exact_mass"],
                provenance=c["provenance"], source=c["source"],
            )
            eg = r.get("evidence_graph")
            graph = None
            if eg:
                graph = EvidenceGraph(
                    node_id=eg["node_id"], claim=eg["claim"],
                    evidence=eg["evidence"], confidence=eg["confidence"],
                    gaps=eg.get("gaps", []),
                )
            el_ranks.append(CandidateRank(
                candidate=candidate, score=r["score"],
                ranking_convention=r["ranking_convention"],
                coverage=r.get("coverage"), margin=r.get("margin"),
                verdict=r["verdict"],
                evidence_graph=graph,
            ))
        chain_result = apply_chromatography_layers(
            el_ranks, species="Curcuma longa", observed_rt_min=9.9,
            observed_uv_spectrum=uv, peak_purity_score=0.95,
            lc_method=LCMethod(**method),
            reference_standards=[ReferenceStandard(**standard)],
            anchor_series=None,
        )
        payload["layer_chain_over_ranks"] = {
            "layers_applied": [
                tag for tag in ("level1", "rt", "uv", "purity",
                                "pharmacopoeial_markers")
                if tag not in
                {s["layer"] for s in chain_result.skipped_layers}
            ],
            "skipped_layers": chain_result.skipped_layers,
            "ranks": json.loads(
                json.dumps(
                    [r.model_dump(mode="json") for r in chain_result.ranks],
                    default=str,
                )
            ),
        }
    except Exception as exc:  # keep the capture robust
        payload["layer_chain_over_ranks"] = {"_error": repr(exc)}

    out = sys.argv[1] if len(sys.argv) > 1 else "rich_identify_response.json"
    with open(out, "w") as fh:
        json.dump(payload, fh, indent=2)
    print(f"wrote {out}: {len(json.dumps(payload))} bytes")


if __name__ == "__main__":
    main()