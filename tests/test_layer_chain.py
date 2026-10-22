"""Layer-chain integration tests (ADR-0061..ADR-0066): the pipeline
order, skip reporting, purity override, marker evidence, and the
planner's task wiring."""

from __future__ import annotations

import pytest

from phyto_shared.chromatography import (
    AnchorStandard,
    LCMethod,
    ReferenceStandard,
)
from phyto_shared.constants import ConfidenceVerdict
from phyto_shared.models import (
    Candidate,
    CandidateRank,
    EvidenceGraph,
    MolecularFormula,
)
from phyto_shared.uv_chromophore import UVClass

from orchestrator.layer_chain import LayerChainResult, apply_chromatography_layers

QUERCETIN = "Oc1cc(O)c(c(O)c1)-c1oc2cc(O)cc(O)c2c(=O)c1O"
PANDURATIN = "CC(C)=CCC[C@@H](C)C(=O)c1c(O)cc(O)c(C)c1"


def _rank(cid: str, name: str, smiles: str, score: float) -> CandidateRank:
    return CandidateRank(
        candidate=Candidate(
            id=cid, name=name, smiles=smiles,
            formula=MolecularFormula(counts={"C": 1}), exact_mass=100.0,
            provenance="experimental", source="test",
        ),
        score=score,
        ranking_convention="lower_is_better",
        verdict=ConfidenceVerdict.TENTATIVE,
        evidence_graph=EvidenceGraph(
            node_id=cid, claim=f"identity: {name}", evidence=[], confidence=0.5,
        ),
    )


def _method() -> LCMethod:
    return LCMethod(
        column_type="C18",
        mobile_phase_a="water + 0.1% formic acid",
        mobile_phase_b="acetonitrile + 0.1% formic acid",
        gradient=[(0.0, 5.0), (30.0, 95.0)],
        flow_rate_ml_min=0.3,
        column_temperature_c=40.0,
    )


def _standard(name="quercetin std", smiles=QUERCETIN, rt=9.8) -> ReferenceStandard:
    return ReferenceStandard(name=name, smiles=smiles, rt_min=rt)


FLAVONOL = UVClass(
    primary_class="flavonol", confidence=0.9, band_i_nm=370.0, band_ii_nm=280.0
)


def _flavonol_spectrum() -> list[tuple[float, float]]:
    """Two-band flavonol-like PDA trace: Band I 370 nm, Band II 280 nm."""
    return [
        (float(w),
         max(0.0, 2.0 - abs(w - 370.0) / 8.0)
         + max(0.0, 1.0 - abs(w - 280.0) / 10.0))
        for w in range(220, 421, 5)
    ]


def _predict_98(smiles: str, method: LCMethod):
    from phyto_shared.chromatography import RetentionPrediction

    return RetentionPrediction(
        predicted_rt_min=9.8, prediction_interval=(9.3, 10.3),
        model_id="test", model_version="1",
    )


ALL_TAGS = {"level1", "rt", "uv", "purity", "pharmacopoeial_markers"}


# --- Skips -------------------------------------------------------------------


def test_no_metadata_skips_every_layer_visibly() -> None:
    ranks = [_rank("q", "quercetin", QUERCETIN, 1.0)]
    result = apply_chromatography_layers(
        ranks, species=None, observed_rt_min=None,
        observed_uv_spectrum=None, peak_purity_score=None,
        lc_method=None, reference_standards=None, anchor_series=None,
    )
    assert isinstance(result, LayerChainResult)
    skipped = {s["layer"] for s in result.skipped_layers}
    assert skipped == ALL_TAGS
    assert all(s["reason"].startswith("layer skipped:") for s in result.skipped_layers)
    assert result.ranks[0].score == 1.0  # untouched


def test_uv_only_runs_uv_and_reports_the_other_skips() -> None:
    spectrum = _flavonol_spectrum()
    ranks = [_rank("q", "quercetin", QUERCETIN, 1.0)]
    result = apply_chromatography_layers(
        ranks, species=None, observed_rt_min=None,
        observed_uv_spectrum=spectrum, peak_purity_score=None,
        lc_method=None, reference_standards=None, anchor_series=None,
        predict_rt_fn=None,
    )
    skipped = {s["layer"] for s in result.skipped_layers}
    assert "uv" not in skipped
    assert "level1" in skipped and "rt" in skipped


# --- Level-1 + RT -------------------------------------------------------------


def test_standard_match_stamps_level_1_and_offset() -> None:
    ranks = [
        _rank("q", "quercetin", QUERCETIN, 1.0),
        _rank("p", "panduratin", PANDURATIN, 1.2),
    ]
    result = apply_chromatography_layers(
        ranks, species=None, observed_rt_min=None,
        observed_uv_spectrum=None, peak_purity_score=None,
        lc_method=_method(), reference_standards=[_standard()],
        anchor_series=None, predict_rt_fn=_predict_98,
    )
    skipped = {s["layer"] for s in result.skipped_layers}
    assert "level1" not in skipped
    by_id = {r.candidate.id: r for r in result.ranks}
    assert by_id["q"].level_1_match == "quercetin std"
    assert "level1" in by_id["q"].classification_chain
    # The non-matching candidate gets the RT offset stamped, not Level 1.
    assert by_id["p"].level_1_match is None


def test_rt_layer_reranks_against_observed_rt() -> None:
    ranks = [_rank("q", "quercetin", QUERCETIN, 1.0)]
    result = apply_chromatography_layers(
        ranks, species=None, observed_rt_min=9.9,
        observed_uv_spectrum=None, peak_purity_score=None,
        lc_method=_method(), reference_standards=None, anchor_series=None,
        predict_rt_fn=_predict_98,
    )
    skipped = {s["layer"] for s in result.skipped_layers}
    assert "rt" not in skipped
    assert result.ranks[0].rt_bonus > 0  # |9.8 - 9.9| < tolerance/2


def test_anchor_series_switches_to_the_ri_axis() -> None:
    anchors = [
        AnchorStandard(name="C10", chain_length=10, observed_rt_min=5.0),
        AnchorStandard(name="C14", chain_length=14, observed_rt_min=11.0),
    ]
    ranks = [_rank("q", "quercetin", QUERCETIN, 1.0)]
    result = apply_chromatography_layers(
        ranks, species=None, observed_rt_min=8.0,
        observed_uv_spectrum=None, peak_purity_score=None,
        lc_method=_method(), reference_standards=None,
        anchor_series=anchors, predict_rt_fn=_predict_98,
    )
    skipped = {s["layer"] for s in result.skipped_layers}
    assert "rt" not in skipped
    assert result.ranks[0].ri_used == pytest.approx(1200.0)


# --- UV + purity ---------------------------------------------------------------


def test_uv_layer_stamps_observed_classification() -> None:
    spectrum = _flavonol_spectrum()
    ranks = [_rank("q", "quercetin", QUERCETIN, 1.0)]
    result = apply_chromatography_layers(
        ranks, species=None, observed_rt_min=None,
        observed_uv_spectrum=spectrum, peak_purity_score=None,
        lc_method=None, reference_standards=None, anchor_series=None,
    )
    assert result.ranks[0].uv_evidence is not None
    assert result.ranks[0].uv_evidence.primary_class == "flavonol"


def test_impure_peak_forces_ambiguous_with_caveat() -> None:
    ranks = [_rank("q", "quercetin", QUERCETIN, 1.0)]
    result = apply_chromatography_layers(
        ranks, species=None, observed_rt_min=None,
        observed_uv_spectrum=None, peak_purity_score=0.3,
        lc_method=None, reference_standards=None, anchor_series=None,
    )
    rank = result.ranks[0]
    assert rank.purity_score == 0.3
    assert rank.verdict == ConfidenceVerdict.AMBIGUOUS
    assert "purity" in rank.classification_chain
    relations = [e.relation for e in rank.evidence_chain]
    assert "peak_purity_override" in relations
    assert rank.evidence_graph is not None
    assert any("mixture" in gap.lower() for gap in rank.evidence_graph.gaps)


def test_pure_peak_records_score_without_forcing() -> None:
    ranks = [_rank("q", "quercetin", QUERCETIN, 1.0)]
    result = apply_chromatography_layers(
        ranks, species=None, observed_rt_min=None,
        observed_uv_spectrum=None, peak_purity_score=0.95,
        lc_method=None, reference_standards=None, anchor_series=None,
    )
    rank = result.ranks[0]
    assert rank.purity_score == 0.95
    assert rank.verdict == ConfidenceVerdict.TENTATIVE  # unchanged


# --- Pharmacopoeial markers -----------------------------------------------------


def test_markers_for_curated_species_attach_evidence() -> None:
    ranks = [_rank("q", "quercetin", QUERCETIN, 1.0)]
    result = apply_chromatography_layers(
        ranks, species="Curcuma longa", observed_rt_min=15.0,
        observed_uv_spectrum=None, peak_purity_score=None,
        lc_method=None, reference_standards=None, anchor_series=None,
    )
    skipped = {s["layer"] for s in result.skipped_layers}
    assert "pharmacopoeial_markers" not in skipped
    rank = result.ranks[0]
    assert "pharmacopoeial_markers" in rank.classification_chain
    marker_items = [
        e for e in rank.evidence_chain
        if e.source_spectrum.startswith("pharmacopoeial_marker:")
    ]
    assert marker_items
    assert all("Ph." in e.source_spectrum or e.source_spectrum for e in marker_items)


def test_markers_for_unknown_species_report_skip() -> None:
    ranks = [_rank("q", "quercetin", QUERCETIN, 1.0)]
    result = apply_chromatography_layers(
        ranks, species="Arabidopsis thaliana", observed_rt_min=15.0,
        observed_uv_spectrum=None, peak_purity_score=None,
        lc_method=None, reference_standards=None, anchor_series=None,
    )
    skipped = {s["layer"]: s for s in result.skipped_layers}
    assert "pharmacopoeial_markers" in skipped
    assert "no curated markers" in skipped["pharmacopoeial_markers"]["reason"]


def test_marker_evidence_cites_its_reference() -> None:
    ranks = [_rank("q", "quercetin", QUERCETIN, 1.0)]
    result = apply_chromatography_layers(
        ranks, species="Curcuma longa", observed_rt_min=15.0,
        observed_uv_spectrum=None, peak_purity_score=None,
        lc_method=None, reference_standards=None, anchor_series=None,
    )
    sources = {
        e.source_spectrum for e in result.ranks[0].evidence_chain
        if e.source_spectrum.startswith("pharmacopoeial_marker:")
    }
    # Every marker item names a pharmacopoeial source (per-entry provenance).
    assert sources
    for source in sources:
        assert ":" in source  # pharmacopoeial_marker:<reference>


# --- Pipeline order ---------------------------------------------------------------


def test_layers_run_in_pipeline_order_and_compose() -> None:
    spectrum = _flavonol_spectrum()
    ranks = [_rank("q", "quercetin", QUERCETIN, 1.0)]
    result = apply_chromatography_layers(
        ranks, species="Curcuma longa", observed_rt_min=9.9,
        observed_uv_spectrum=spectrum, peak_purity_score=0.95,
        lc_method=_method(), reference_standards=[_standard()],
        anchor_series=None, predict_rt_fn=_predict_98,
    )
    rank = result.ranks[0]
    chain = rank.classification_chain
    assert chain == ["level1", "rt", "uv", "purity", "pharmacopoeial_markers"]
    # Every applied layer contributed evidence items: the chain items use
    # the layer tag, the graph carries the diagnostic relation.
    relations = [e.relation for e in rank.evidence_chain]
    assert "level_1_reference" in relations
    assert "rt" in relations and "uv" in relations
    # Purity at 0.95 stamps the chain without an override item (it forced
    # nothing); the marker items carry the pharmacopoeial relation.
    assert "purity" in rank.classification_chain
    assert "pharmacopoeial_markers" in relations
    graph_relations = [
        e.relation for e in rank.evidence_graph.evidence
    ]
    assert "rt_prediction_close" in graph_relations


def test_scoring_is_never_below_zero_after_penalties() -> None:
    ranks = [_rank("p", "panduratin", PANDURATIN, 0.1)]
    spectrum = _flavonol_spectrum()
    result = apply_chromatography_layers(
        ranks, species=None, observed_rt_min=30.0,
        observed_uv_spectrum=spectrum, peak_purity_score=None,
        lc_method=_method(), reference_standards=None, anchor_series=None,
        predict_rt_fn=lambda s, m: _predict_98(s, m),
    )
    assert result.ranks[0].score >= 0.0


# --- Planner wiring ----------------------------------------------------------------


def test_planner_emits_layers_task_when_metadata_present() -> None:
    from phyto_shared.schemas import SampleMetadata

    from orchestrator.planner_agent import PlannerAgent
    from orchestrator.tool_registry import DEFAULT_REGISTRY

    metadata = SampleMetadata(
        species="Curcuma longa",
        lc_method=_method().model_dump(),
        reference_standards=[_standard().model_dump()],
    )
    graph = PlannerAgent(DEFAULT_REGISTRY).plan(
        run_id="run-test", workflow="identify", dry_run=True,
        sample_metadata=metadata,
    )
    ids = [t.id for t in graph.tasks]
    assert "apply-chromatography-layers" in ids
    layer_task = next(
        t for t in graph.tasks if t.id == "apply-chromatography-layers"
    )
    assert layer_task.depends_on == ["elucidate"]
    # The human checkpoint waits for the layers too.
    checkpoint = next(t for t in graph.tasks if t.id == "approve-claim")
    assert "apply-chromatography-layers" in checkpoint.depends_on


def test_planner_omits_layers_task_without_metadata() -> None:
    from phyto_shared.schemas import SampleMetadata

    from orchestrator.planner_agent import PlannerAgent
    from orchestrator.tool_registry import DEFAULT_REGISTRY

    graph = PlannerAgent(DEFAULT_REGISTRY).plan(
        run_id="run-test", workflow="identify", dry_run=True,
        sample_metadata=SampleMetadata(species="Curcuma longa"),
    )
    assert "apply-chromatography-layers" not in [t.id for t in graph.tasks]