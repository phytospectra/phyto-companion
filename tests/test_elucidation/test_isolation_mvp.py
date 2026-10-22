"""Isolation MVP tests (ADR-0055): model validation, priors, reranker,
identify-workflow integration.

Unit tests run on plain model/prior construction; the integration test
runs the real identify workflow through the orchestrator engine with
the isolation record seeded into the local-plane store.
"""

from __future__ import annotations

import logging

import pytest

from phyto_shared.constants import ConfidenceVerdict
from phyto_shared.isolation import (
    Fraction,
    IsolationCompound,
    IsolationRecord,
    TLCResponse,
    TLCObservation,
)
from phyto_shared.isolation_prior import (
    abundance_from_yield,
    class_from_tlc,
    polarity_from_fractions,
)
from phyto_shared.models import (
    Candidate,
    CandidateRank,
    EvidenceGraph,
    MolecularFormula,
)
from phyto_shared.schemas import SampleMetadata

from elucidation.isolation_reranker import (
    POLARITY_CONTRADICTION,
    TLC_CONTRADICTION,
    TLC_MATCH_BONUS,
    predicted_classes,
    predicted_polarity_tier,
    rerank_with_isolation,
)

QUERCETIN = "C1=CC(=C(C=C1C2=C(C(=O)C3=C(C=C(C=C3O2)O)O)O)O)O"
ISOQUERCITRIN = (
    "C1=CC(=C(C=C1C2=C(C(=O)C3=C(C=C(C=C3O2)O)O)O)OC4C(C(C(C(O4)CO)O)O)O)O"
)
CAFFEINE = "Cn1c(=O)c2c(ncn2C)n(C)c1=O"
BETA_SITOSTEROL = (
    "CCC(C)CCCC(C)C1CCC2C3CC=C4CC(O)CCC4(C)C3CCC12C"
)


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


def _registry(*tiers: str) -> dict[str, Fraction]:
    return {
        f"F{i + 1}": Fraction(
            fraction_id=f"F{i + 1}", polarity_tier=tier, solvent="EtOAc"
        )
        for i, tier in enumerate(tiers)
    }


def _compound(*, fractions=("F1",), tlc=None, uv=None, yield_mg=None) -> IsolationCompound:
    return IsolationCompound(
        compound_id="c1", present_in_fractions=list(fractions),
        tlc=tlc, flash_uv_lambda_max_nm=uv, yield_mg=yield_mg,
    )


# ---------------------------------------------------------------------------
# Model validation
# ---------------------------------------------------------------------------


class TestModelValidation:
    def test_rf_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError, match="rf"):
            TLCObservation(rf=1.3, solvent_system="EtOAc")

    def test_unknown_reagent_rejected(self) -> None:
        with pytest.raises(ValueError, match="reagent"):
            TLCResponse(reagent="kmno4", response="positive_strong")

    def test_unknown_response_kind_rejected(self) -> None:
        with pytest.raises(ValueError, match="response"):
            TLCResponse(reagent="Dragendorff", response="orange")

    def test_unknown_fraction_reference_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown fraction"):
            IsolationRecord(
                extract_id="E", species="S", mass_starting_g=10.0,
                extraction_solvent="EtOAc",
                fractions=[Fraction(fraction_id="F1", polarity_tier="mid", solvent="EtOAc")],
                compounds=[_compound(fractions=("F9",))],
                user_id="u",
            )

    def test_duplicate_fraction_ids_rejected(self) -> None:
        with pytest.raises(ValueError, match="duplicate fraction_id"):
            IsolationRecord(
                extract_id="E", species="S", mass_starting_g=10.0,
                extraction_solvent="EtOAc",
                fractions=[
                    Fraction(fraction_id="F1", polarity_tier="mid", solvent="EtOAc"),
                    Fraction(fraction_id="F1", polarity_tier="polar", solvent="MeOH"),
                ],
                user_id="u",
            )

    def test_purity_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError, match="purity"):
            IsolationCompound(
                compound_id="c1", present_in_fractions=["F1"], purity=1.5
            )


# ---------------------------------------------------------------------------
# polarity_from_fractions
# ---------------------------------------------------------------------------


class TestPolarityFromFractions:
    def test_single_fraction_full_confidence(self) -> None:
        est = polarity_from_fractions(["F1"], _registry("mid"))
        assert est.tier == "mid"
        assert est.confidence == 1.0

    def test_adjacent_tiers_middle_and_reduced_confidence(self) -> None:
        est = polarity_from_fractions(["F1", "F2"], _registry("nonpolar", "mid"))
        assert est.tier == "mid"  # span centre rounds toward polar
        assert est.confidence == 0.75

    def test_full_gradient_spread_low_confidence_floor(self) -> None:
        est = polarity_from_fractions(
            ["F1", "F2"], _registry("very_nonpolar", "very_polar")
        )
        assert est.tier in ("mid", "polar")
        assert est.confidence == pytest.approx(0.2)

    def test_no_fractions_is_no_data_not_a_guess(self) -> None:
        est = polarity_from_fractions([], _registry("mid"))
        assert est.tier is None
        assert est.confidence == 0.0

    def test_unknown_fraction_id_raises(self) -> None:
        with pytest.raises(Exception, match="registry"):
            polarity_from_fractions(["F9"], _registry("mid"))


# ---------------------------------------------------------------------------
# class_from_tlc
# ---------------------------------------------------------------------------


def _tlc(*responses: tuple[str, str, str | None]) -> TLCObservation:
    return TLCObservation(
        rf=0.42, solvent_system="EtOAc",
        visualization=[
            TLCResponse(reagent=r, response=res, color=c)  # type: ignore[arg-type]
            for r, res, c in responses
        ],
    )


class TestClassFromTLC:
    def test_naturstoff_strong_ranks_flavonols_first(self) -> None:
        classes = class_from_tlc(_tlc(("Naturstoff", "positive_strong", "yellow")))
        assert classes[:3] == ["flavonol", "flavone", "flavanone"]

    def test_dragendorff_implies_alkaloid(self) -> None:
        classes = class_from_tlc(_tlc(("Dragendorff", "positive_strong", None)))
        assert classes == ["alkaloid"]

    def test_liebermann_burchard_implies_sterol_triterpene(self) -> None:
        classes = class_from_tlc(
            _tlc(("Liebermann_Burchard", "positive_strong", None))
        )
        assert classes == ["sterol", "triterpene"]

    def test_vanillin_colors_split_terpene_vs_flavonoid(self) -> None:
        yellow = class_from_tlc(_tlc(("vanillin_H2SO4", "positive_strong", "yellow")))
        pink = class_from_tlc(_tlc(("vanillin_H2SO4", "positive_strong", "pink-red")))
        assert "flavonoid" in yellow and "terpene" not in yellow
        assert "terpene" in pink and "steroid" not in [c for c in pink if c == "steroid"]

    def test_untaught_vanillin_color_is_no_signal(self) -> None:
        classes = class_from_tlc(_tlc(("vanillin_H2SO4", "positive_strong", "blue-green")))
        assert classes == []

    def test_fecl3_phenolic_and_uv254_aromatic_appends(self) -> None:
        classes = class_from_tlc(
            _tlc(("FeCl3", "positive_strong", None), ("UV254", "absorbing", None))
        )
        assert classes[0] == "phenolic"
        assert classes[-1] == "aromatic"  # UV254 never leads

    def test_all_negative_is_empty_not_a_verdict(self) -> None:
        classes = class_from_tlc(
            _tlc(("Naturstoff", "negative", None), ("Dragendorff", "negative", None))
        )
        assert classes == []


# ---------------------------------------------------------------------------
# abundance_from_yield
# ---------------------------------------------------------------------------


class TestAbundanceFromYield:
    def test_twelve_point_five_mg_from_fifty_g(self) -> None:
        assert abundance_from_yield(50.0, 12.5) == pytest.approx(0.025)

    def test_yield_exceeding_starting_mass_raises(self) -> None:
        with pytest.raises(Exception, match="starting mass"):
            abundance_from_yield(0.01, 20.0)

    def test_non_positive_starting_mass_raises(self) -> None:
        with pytest.raises(Exception, match="positive"):
            abundance_from_yield(0.0, 1.0)


# ---------------------------------------------------------------------------
# Structure prediction + reranker
# ---------------------------------------------------------------------------


class TestStructurePrediction:
    def test_flavonol_glycoside_classes_and_polarity(self) -> None:
        classes = predicted_classes(ISOQUERCITRIN)
        assert "flavonol" in classes and "aromatic" in classes
        # one pyranose floors the tier at nonpolar-or-polarier; logP is
        # negative for a glycoside, so the sugar dominates -> polar
        assert predicted_polarity_tier(ISOQUERCITRIN) in ("polar", "very_polar")

    def test_sterol_and_alkaloid_and_aglycone(self) -> None:
        assert "triterpene" in predicted_classes(BETA_SITOSTEROL)
        assert predicted_polarity_tier(BETA_SITOSTEROL) == "very_nonpolar"
        assert "alkaloid" in predicted_classes(CAFFEINE)
        assert predicted_polarity_tier(CAFFEINE) == "very_polar"
        assert "flavonol" in predicted_classes(QUERCETIN)

    def test_unparseable_smiles_predicts_nothing(self) -> None:
        assert predicted_classes("not-a-smiles") == []
        assert predicted_polarity_tier("not-a-smiles") is None


class TestReranker:
    def test_bonus_applied_and_evidence_attached(self) -> None:
        reg = _registry("polar")
        comp = _compound(
            fractions=("F1",),
            tlc=_tlc(("Naturstoff", "positive_strong", "yellow"),
                     ("UV254", "absorbing", None)),
            uv=370.0,
        )
        out = rerank_with_isolation([_rank("q", "quercetin", QUERCETIN, 1.10)], comp, reg)
        assert out[0].isolation_bonus > 0
        relations = {e.relation for e in out[0].isolation_evidence}
        assert {"isolation_polarity", "isolation_tlc_class", "isolation_flash_uv"} <= relations

    def test_class_contradiction_penalises_non_alkaloid(self) -> None:
        # Dragendorff-positive plate: quercetin (no ring N) contradicts.
        # very_nonpolar is >= 2 tiers from quercetin's predicted polarity,
        # so the polarity signal also penalises: net -0.30 - 0.25.
        reg = _registry("very_nonpolar")
        comp = _compound(
            fractions=("F1",),
            tlc=_tlc(("Dragendorff", "positive_strong", None)),
        )
        out = rerank_with_isolation(
            [_rank("q", "quercetin", QUERCETIN, 1.00)], comp, reg
        )
        # penalty subtracts from a lower_is_better distance: -(-0.30) + (-0.25)
        assert out[0].isolation_bonus == pytest.approx(TLC_CONTRADICTION + POLARITY_CONTRADICTION)
        assert out[0].score == pytest.approx(1.00 + 0.55)

    def test_dragendorff_matches_real_alkaloid(self) -> None:
        # "polar" tier: adjacent to caffeine's predicted very_polar, so
        # the only signal is the TLC class match
        reg = _registry("polar")
        comp = _compound(
            fractions=("F1",),
            tlc=_tlc(("Dragendorff", "positive_strong", None)),
        )
        out = rerank_with_isolation(
            [_rank("caf", "caffeine", CAFFEINE, 1.00)], comp, reg
        )
        assert out[0].isolation_bonus > 0

    def test_polarity_contradiction_penalises_sterol_in_polar(self) -> None:
        reg = _registry("very_polar")
        comp = _compound(fractions=("F1",))
        out = rerank_with_isolation(
            [_rank("s", "beta-sitosterol", BETA_SITOSTEROL, 1.00)], comp, reg
        )
        assert out[0].isolation_bonus == pytest.approx(POLARITY_CONTRADICTION)

    def test_no_record_is_identity_no_op(self) -> None:
        ranks = [_rank("a", "quercetin", QUERCETIN, 1.0)]
        out = rerank_with_isolation(ranks, None, {})
        assert out == ranks

    def test_stable_sort_preservation(self) -> None:
        reg = _registry("mid")
        comp = _compound(fractions=("F1",))
        ranks = [
            _rank("first", "quercetin", QUERCETIN, 1.00),
            _rank("second", "quercetin again", QUERCETIN, 1.00),
            _rank("third", "isoquercitrin", ISOQUERCITRIN, 1.00),
        ]
        out = rerank_with_isolation(ranks, comp, reg)
        # all three receive identical bonuses -> input order preserved
        assert [r.candidate.id for r in out] == ["first", "second", "third"]

    def test_higher_is_better_convention_respected(self) -> None:
        reg = _registry("mid")
        comp = _compound(fractions=("F1",))
        rank = _rank("q", "quercetin", QUERCETIN, 0.80).model_copy(
            update={"ranking_convention": "higher_is_better"}
        )
        out = rerank_with_isolation([rank], comp, reg)
        assert out[0].score == pytest.approx(0.80 + out[0].isolation_bonus)

    def test_unknown_fraction_skips_polarity_but_not_other_signals(self) -> None:
        reg = _registry("mid")
        comp = _compound(
            fractions=("F9",),
            tlc=_tlc(("Naturstoff", "positive_strong", "yellow")),
        )
        out = rerank_with_isolation(
            [_rank("q", "quercetin", QUERCETIN, 1.00)], comp, reg
        )
        relations = {e.relation for e in out[0].isolation_evidence}
        assert "isolation_polarity" not in relations
        assert "isolation_tlc_class" in relations
        assert out[0].isolation_bonus == pytest.approx(TLC_MATCH_BONUS)


# ---------------------------------------------------------------------------
# Identify-workflow integration (ADR-0055)
# ---------------------------------------------------------------------------


class TestIdentifyIntegration:
    def _plan_and_run(self, metadata: SampleMetadata):
        import conftest as _bootstrap  # noqa: F401  (registers aliases)

        from orchestrator.planner_agent import (
            ExecutionContext,
            HumanCheckpoint,
            ObservationModality,
            PlannerAgent,
            TaskStatus,
            WorkflowEngine,
        )
        from orchestrator.tool_registry import (
            DEFAULT_REGISTRY,
            MockToolExecutor,
            _MOCK_ISOLATION_RECORDS,
        )
        from orchestrator.audit_logger import ChainedAuditLogger
        from orchestrator.qa_critic_agent import QACriticAgent

        registry = _registry("polar")
        compound = _compound(
            fractions=("F1",),
            tlc=_tlc(("Naturstoff", "positive_strong", "yellow"),
                     ("UV254", "absorbing", None)),
            uv=370.0,
            yield_mg=12.5,
        )
        record = IsolationRecord(
            record_id="iso-test", extract_id="EXT-001", species="Boesenbergia rotunda",
            mass_starting_g=50.0, extraction_solvent="ethyl acetate",
            fractions=list(registry.values()), compounds=[compound], user_id="alice",
        )
        _MOCK_ISOLATION_RECORDS["EXT-001"] = record

        planner = PlannerAgent(DEFAULT_REGISTRY)
        engine = WorkflowEngine(
            registry=DEFAULT_REGISTRY,
            executor=MockToolExecutor(DEFAULT_REGISTRY),
            audit=ChainedAuditLogger("/tmp/phyto_isolation_test_audit.jsonl"),
            qa_critic=QACriticAgent(),
        )
        graph = planner.plan(
            run_id="run-iso", workflow="identify", dry_run=True,
            modalities={ObservationModality.HSQC}, sample_metadata=metadata,
        )
        context = ExecutionContext(
            run_id="run-iso", dry_run=True, verification_available=True,
            human_in_the_loop_approved=True,
            approved_checkpoints={HumanCheckpoint.BEFORE_CONFIRMED_CLAIM},
            actor="test",
        )
        result = engine.run(graph, context)
        return graph, result, TaskStatus

    def test_isolation_record_flips_top_candidate_to_flavonol_glycoside(
        self, caplog
    ) -> None:
        metadata = SampleMetadata(
            species="Boesenbergia rotunda", extract_id="EXT-001"
        )
        with caplog.at_level(logging.INFO):
            graph, result, TaskStatus = self._plan_and_run(metadata)
        assert result.status == "complete", [
            (t.id, t.error) for t in graph.tasks if t.error
        ]
        # the isolation task ran and the record was consumed
        load = graph.task_by_id("load-isolation-record")
        assert load is not None and load.status == TaskStatus.COMPLETE
        elucidate = graph.task_by_id("elucidate")
        assert elucidate.output["isolation_applied"] is True
        # the bench evidence (Naturstoff+, EtOAc/polar, UV 370 nm) promotes
        # the flavonol glycoside past the aglycone and sinks the alkaloid
        names = [r["candidate"]["name"] for r in elucidate.output["ranks"]]
        assert names[0] == "isoquercitrin"
        assert "caffeine" in names
        top = elucidate.output["ranks"][0]
        assert top["isolation_bonus"] > 0
        caffeine_rank = next(
            r for r in elucidate.output["ranks"] if r["candidate"]["name"] == "caffeine"
        )
        assert caffeine_rank["isolation_bonus"] < 0
        relations = {
            e["relation"] for e in top["evidence_graph"]["evidence"]
        }
        assert "isolation_tlc_class" in relations

    def test_no_extract_id_skips_layer_with_visible_note(self, caplog) -> None:
        metadata = SampleMetadata(species="Boesenbergia rotunda")
        with caplog.at_level(logging.INFO, logger="orchestrator.planner_agent"):
            graph, result, TaskStatus = self._plan_and_run(metadata)
        assert result.status == "complete"
        assert "load-isolation-record" not in [t.id for t in graph.tasks]
        assert any(
            "isolation layer skipped: no record" in record.message
            for record in caplog.records
        )
        output = graph.task_by_id("elucidate").output
        assert output["isolation_applied"] is False