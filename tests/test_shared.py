"""Tests for the phyto_shared library (Step 2).

Local plane only: tiny fixtures, no network, no cloud calls.  The whole
file runs in well under 5 seconds on an M2 / 8 GB machine.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from phyto_shared import constants as C
from phyto_shared import schemas as S
from phyto_shared.exceptions import (
    AmbiguousResultError,
    CloudServiceError,
    InsufficientEvidenceError,
    InvalidInputError,
    PhytoCompanionError,
    ProvenanceError,
    SandboxError,
    ToolTierViolationError,
)
from phyto_shared.models import (
    Adduct,
    AnalysisRun,
    Candidate,
    CandidateRank,
    EvidenceGraph,
    EvidenceItem,
    FusedNetwork,
    IRPeak,
    MolecularFormula,
    MSPeak,
    NetworkEdge,
    NetworkNode,
    Peak1D,
    Peak2D,
    ProvenanceManifest,
    Spectrum,
)
from phyto_shared.provenance import (
    AuditLog,
    ProvenanceBuilder,
    sha256_bytes,
    sha256_dict,
    sha256_file,
    verify_manifest,
)
from phyto_shared.uncertainty import (
    MIN_MARGIN_FOR_CONFIRMED,
    MIN_MARGIN_FOR_TENTATIVE,
    EvidenceAssessment,
    coverage_confidence,
    is_unique_top1_higher_is_better,
    is_unique_top1_lower_is_better,
    margin_higher_is_better,
    margin_lower_is_better,
    rank_higher_is_better,
    rank_lower_is_better,
    verdict_from_evidence,
)

H64 = "a" * 64


# ===========================================================================
# constants
# ===========================================================================


class TestConstants:
    def test_solvent_references_match_specification(self):
        assert C.SOLVENT_REFERENCES["CDCl3"].h_ppm == (7.26,)
        assert C.SOLVENT_REFERENCES["CDCl3"].c_ppm == (77.16,)
        assert C.SOLVENT_REFERENCES["CD3OD"].h_ppm == (3.31,)
        assert C.SOLVENT_REFERENCES["CD3OD"].c_ppm == (49.00,)
        assert C.SOLVENT_REFERENCES["DMSO-d6"].h_ppm == (2.50,)
        assert C.SOLVENT_REFERENCES["DMSO-d6"].c_ppm == (39.52,)
        assert C.SOLVENT_REFERENCES["Acetone-d6"].h_ppm == (2.05,)
        assert 29.84 in C.SOLVENT_REFERENCES["Acetone-d6"].c_ppm
        pyr = C.SOLVENT_REFERENCES["C5D5N"]
        assert pyr.h_ppm == (8.74, 7.58, 7.22)
        assert pyr.c_ppm == (150.35, 135.91, 123.87)
        assert C.SOLVENT_REFERENCES["C6D6"].h_ppm == (7.16,)
        assert C.SOLVENT_REFERENCES["C6D6"].c_ppm == (128.06,)
        assert C.SOLVENT_REFERENCES["CD3CN"].h_ppm == (1.94,)
        assert 1.32 in C.SOLVENT_REFERENCES["CD3CN"].c_ppm
        d2o = C.SOLVENT_REFERENCES["D2O"]
        assert d2o.h_ppm == (4.79,)
        assert d2o.c_ppm == ()
        assert "pH" in d2o.notes and "temperature" in d2o.notes

    def test_solvent_lookup_is_forgiving(self):
        assert C.solvent_lookup("dmso-d6") is C.SOLVENT_REFERENCES["DMSO-d6"]
        assert C.solvent_lookup("  CD3OD ") is C.SOLVENT_REFERENCES["CD3OD"]
        with pytest.raises(KeyError):
            C.solvent_lookup("toluene-d8")

    def test_solvent_map_is_immutable(self):
        with pytest.raises(TypeError):
            C.SOLVENT_REFERENCES["THF-d8"] = C.SolventReference((3.58,), (67.57))

    def test_adduct_table(self):
        assert set(C.ADDUCT_MASSES) == {
            "[M+H]+", "[M-H]-", "[M+Na]+", "[M+K]+", "[M+NH4]+",
            "[M+HCOO]-", "[M+Cl]-",
        }
        assert C.ADDUCT_MASSES["[M+H]+"].mass_delta == pytest.approx(1.007276)
        assert C.ADDUCT_MASSES["[M-H]-"].mass_delta == pytest.approx(-1.007276)
        for info in C.ADDUCT_MASSES.values():
            assert abs(info.charge) == 1
            assert info.mass_delta == round(info.mass_delta, 6)  # exactly 6 dp
            assert (info.charge > 0) == (info.polarity == "positive")

    def test_element_table(self):
        expected = {"C", "H", "N", "O", "S", "P", "F", "Cl", "Br", "I", "Si", "B", "Se"}
        assert set(C.ELEMENT_MASSES) == expected
        assert C.ELEMENT_MASSES["C"].monoisotopic_mass == 12.0
        for symbol in expected:
            assert 0.0 < C.ELEMENT_MASSES[symbol].natural_abundance <= 1.0
            assert symbol in C.ELEMENT_VALENCES
        # Se: most abundant isotope is 80Se, not the lightest one
        assert C.ELEMENT_MASSES["Se"].isotope == "80Se"

    def test_msi_levels(self):
        assert [level.value for level in C.MSIConfidenceLevel] == [1, 2, 3, 4]
        assert all(level in C.MSI_LEVEL_REQUIREMENTS for level in C.MSIConfidenceLevel)

    def test_tool_tiers(self):
        assert C.ToolTier.TIER_1_DETERMINISTIC.requires_verification is False
        assert C.ToolTier.TIER_2_ML_VALIDATED.requires_verification is True
        assert C.ToolTier.TIER_3_LLM_GENERATED.requires_verification is True
        assert all(tier in C.TOOL_TIER_REQUIREMENTS for tier in C.ToolTier)

    def test_confidence_verdicts_are_pure_confidence(self):
        assert len(C.ConfidenceVerdict) == 5
        # Novelty was removed from the confidence axis (ADR-0006)
        assert not hasattr(C.ConfidenceVerdict, "NOVELTY_FLAGGED")
        assert all(v in C.CONFIDENCE_VERDICT_REQUIREMENTS for v in C.ConfidenceVerdict)

    def test_novelty_is_a_separate_axis(self):
        assert set(C.NOVELTY_STATUS_DEFINITIONS) == {"novel", "known", "uncertain"}
        # Novelty is flagged for humans; the platform never claims it
        assert "never claims" in C.NOVELTY_STATUS_DEFINITIONS["novel"]


# ===========================================================================
# exceptions
# ===========================================================================


_ALL_EXCEPTIONS = [
    InvalidInputError,
    InsufficientEvidenceError,
    AmbiguousResultError,
    ProvenanceError,
    CloudServiceError,
    ToolTierViolationError,
    SandboxError,
]


class TestExceptions:
    @pytest.mark.parametrize("exc_cls", _ALL_EXCEPTIONS)
    def test_raise_catch_and_context(self, exc_cls):
        with pytest.raises(exc_cls) as excinfo:
            raise exc_cls("something broke", {"file": "x.mzML", "mz": 303.05})
        assert isinstance(excinfo.value, PhytoCompanionError)
        assert excinfo.value.context == {"file": "x.mzML", "mz": 303.05}
        assert "something broke" in str(excinfo.value)

    @pytest.mark.parametrize("exc_cls", _ALL_EXCEPTIONS)
    def test_caught_as_base_class(self, exc_cls):
        with pytest.raises(PhytoCompanionError):
            raise exc_cls("boom")

    def test_default_context_is_empty_dict(self):
        err = ProvenanceError("no context given")
        assert err.context == {}

    def test_context_is_copied_from_caller(self):
        ctx = {"k": 1}
        err = InvalidInputError("msg", ctx)
        ctx["k"] = 999
        assert err.context == {"k": 1}


# ===========================================================================
# models
# ===========================================================================


def _candidate() -> Candidate:
    return Candidate(
        id="cand-001",
        name="quercetin",
        smiles="Oc1cc(O)c(c(c1)O)-c2oc3cc(O)cc(O)c3c(=O)c2",
        formula=MolecularFormula(counts={"C": 15, "H": 10, "O": 7}),
        exact_mass=302.042653,
        provenance="experimental",
        source="in-house reference standard",
    )


def _net_kwargs() -> dict:
    nodes = [
        NetworkNode(id="n1", candidate_or_feature="feat-1", modality="both"),
        NetworkNode(id="n2", candidate_or_feature="feat-2", modality="MS2"),
        NetworkNode(id="n3", candidate_or_feature="feat-3", modality="HSQC"),
    ]
    edges = [
        NetworkEdge(
            source="n1", target="n2", modality="MS2",
            score=0.78, evidence_type="ms2_cosine",
        ),
        NetworkEdge(
            source="n1", target="n3", modality="HSQC",
            score=0.85, evidence_type="hsqc_modified_hungarian",
        ),
        NetworkEdge(
            source="n2", target="n3", modality="both",
            score=0.66, evidence_type="fused",
        ),
    ]
    return {"nodes": nodes, "edges": edges}


def _manifest() -> ProvenanceManifest:
    return ProvenanceManifest(
        input_hashes={"sample.mzML": H64},
        model_versions={"shiftmpnn": "4.0.1"},
        database_versions={"mini_coconut": "v1 (downloaded 2026-10-01)"},
        parameters={"ppm_tolerance": 5.0},
        environment={"python_version": "3.12"},
        timestamp=datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc),
        manifest_hash="b" * 64,
    )


def _examples() -> list:
    evidence = EvidenceItem(
        source_spectrum=H64,
        spectral_feature="HSQC cross-peak 4.52/102.3 ppm",
        relation="anomeric carbon consistent with C-glycoside",
        strength=0.9,
    )
    return [
        Peak1D(ppm=7.26, intensity=100.0, snr=42.0),
        Peak2D(h_ppm=4.52, c_ppm=102.3, intensity=1.0e6, experiment_type="HSQC"),
        MSPeak(mz=303.0499, intensity=9.5e5, adduct="[M+H]+", ms_level=1),
        IRPeak(wavenumber=1712.5, intensity=0.84, functional_group_hint="C=O stretch"),
        Spectrum(
            kind="HSQC",
            data=[
                Peak2D(h_ppm=4.52, c_ppm=102.3, intensity=1.0e6, experiment_type="HSQC"),
                Peak2D(h_ppm=6.41, c_ppm=93.5, intensity=8.0e5, experiment_type="HSQC"),
            ],
            metadata={"solvent": "DMSO-d6", "field_MHz": 600},
            provenance_hash=H64,
        ),
        MolecularFormula(counts={"C": 15, "H": 10, "O": 7}),
        Adduct.from_name("[M+Na]+"),
        evidence,
        EvidenceGraph(
            node_id="node-1",
            claim="C-glycosylation at C-8",
            evidence=[evidence],
            confidence=0.82,
            gaps=["HMBC correlation to C-8 not observed"],
        ),
        _candidate(),
        CandidateRank(
            candidate=_candidate(),
            score=0.87,
            ranking_convention="higher_is_better",
            coverage=0.91,
            margin=0.12,
            ranking_interval=(1, 1),
            verdict=C.ConfidenceVerdict.TENTATIVE,
        ),
        NetworkNode(id="n1", candidate_or_feature="feat-1", modality="both"),
        NetworkEdge(
            source="n1", target="n2", modality="MS2",
            score=0.78, evidence_type="ms2_cosine",
        ),
        FusedNetwork(**_net_kwargs()),
        AnalysisRun(
            id="run-1",
            sample_id="sample-A",
            config={"workflow": "single_compound"},
            provenance_hash=H64,
        ),
        _manifest(),
    ]


@pytest.mark.parametrize(
    "example",
    _examples(),
    ids=lambda obj: type(obj).__name__,
)
def test_model_roundtrip(example):
    dumped = example.model_dump()
    rebuilt = type(example).model_validate(dumped)
    assert rebuilt == example
    assert rebuilt.model_dump() == dumped


_SIMPLE_INVALID_CASES = [
    (Peak1D, {"ppm": 500.0, "intensity": 1.0}),
    (Peak1D, {"ppm": 7.0, "intensity": -1.0}),
    (Peak1D, {"ppm": 7.0, "intensity": 1.0, "review_status": "nope"}),
    (Peak1D, {"ppm": 7.0, "intensity": 1.0, "unexpected_field": 1}),
    (Peak2D, {"h_ppm": 4.5, "c_ppm": 100.0, "intensity": 1.0, "experiment_type": "HSQCC"}),
    (Peak2D, {"h_ppm": 20.0, "c_ppm": 100.0, "intensity": 1.0, "experiment_type": "HSQC"}),
    (MSPeak, {"mz": 0.0, "intensity": 1.0}),
    (MSPeak, {"mz": 100.0, "intensity": 1.0, "adduct": "[M+Li]+"}),
    (MSPeak, {"mz": 100.0, "intensity": 1.0, "ms_level": 0}),
    (IRPeak, {"wavenumber": -5.0, "intensity": 0.5}),
    (Spectrum, {"kind": "NMR", "data": []}),
    (
        Spectrum,
        {
            "kind": "1H",
            "data": [{"peak_type": "ms", "mz": 100.0, "intensity": 1.0}],
        },
    ),
    (
        Spectrum,
        {
            "kind": "1H",
            "data": [{"peak_type": "1d", "ppm": 7.0, "intensity": 1.0}],
            "provenance_hash": "not-a-hash",
        },
    ),
    (MolecularFormula, {"counts": {}}),
    (MolecularFormula, {"counts": {"Xx": 1}}),
    (MolecularFormula, {"counts": {"C": 0}}),
    (MolecularFormula, {"counts": {"C": 1.5}}),
    (Adduct, {"name": "x", "mass_delta": 1.0, "charge": 1, "polarity": "negative"}),
    (Adduct, {"name": "x", "mass_delta": 1.0, "charge": 0, "polarity": "positive"}),
    (
        EvidenceItem,
        {"source_spectrum": "s", "spectral_feature": "f", "relation": "r", "strength": 2.0},
    ),
    (
        AnalysisRun,
        {"id": "r", "sample_id": "s", "provenance_hash": "short"},
    ),
    (
        ProvenanceManifest,
        {"input_hashes": {}, "timestamp": datetime.now(timezone.utc), "manifest_hash": "xyz"},
    ),
]


@pytest.mark.parametrize(
    ("model", "kwargs"),
    _SIMPLE_INVALID_CASES,
    ids=lambda value: value.__name__ if isinstance(value, type) else "",
)
def test_invalid_inputs_rejected(model, kwargs):
    with pytest.raises(ValidationError):
        model(**kwargs)


class TestModelBehaviour:
    def test_formula_exact_mass_and_rdbe(self):
        benzene = MolecularFormula(counts={"C": 6, "H": 6})
        assert benzene.exact_mass() == pytest.approx(78.046950, abs=2e-6)
        assert benzene.rdbe() == pytest.approx(4.0)

        caffeine = MolecularFormula(counts={"C": 8, "H": 10, "N": 4, "O": 2})
        assert caffeine.exact_mass() == pytest.approx(194.080376, abs=2e-6)
        assert caffeine.rdbe() == pytest.approx(6.0)

        glucose = MolecularFormula(counts={"C": 6, "H": 12, "O": 6})
        assert glucose.exact_mass() == pytest.approx(180.063388, abs=2e-6)

    def test_formula_hill_notation_and_normalization(self):
        assert str(MolecularFormula(counts={"C": 6, "H": 12, "O": 6})) == "C6H12O6"
        assert str(MolecularFormula(counts={"H": 2, "O": 1})) == "H2O"
        assert str(MolecularFormula(counts={"O": 7, "C": 15, "H": 10})) == "C15H10O7"
        normalized = MolecularFormula.model_validate({"counts": {"c": 6, "h": 6}})
        assert normalized.counts == {"C": 6, "H": 6}

    def test_adduct_helpers(self):
        protonated = Adduct.from_name("[M+H]+")
        assert protonated.ion_mz(180.063388) == pytest.approx(181.070664, abs=2e-6)
        deprotonated = Adduct.from_name("[M-H]-")
        assert deprotonated.ion_mz(180.063388) == pytest.approx(179.056112, abs=2e-6)
        with pytest.raises(InvalidInputError):
            Adduct.from_name("[M+Li]+")

    def test_spectrum_kind_must_match_peak_type(self):
        with pytest.raises(ValidationError):
            Spectrum(
                kind="MS2",
                data=[
                    Peak2D(
                        h_ppm=4.5, c_ppm=100.0, intensity=1.0, experiment_type="HSQC"
                    )
                ],
            )

    def test_spectrum_discriminator_preserves_types(self):
        spectrum = Spectrum(
            kind="1H",
            data=[Peak1D(ppm=6.18, intensity=10.0), Peak1D(ppm=6.41, intensity=8.0)],
        )
        rebuilt = Spectrum.model_validate(spectrum.model_dump())
        assert all(isinstance(p, Peak1D) for p in rebuilt.data)

    def test_provenance_manifest_is_frozen(self):
        manifest = _manifest()
        with pytest.raises(ValidationError):
            manifest.input_hashes = {}

    def test_candidate_rank_requires_ranking_convention(self):
        # ADR-0008: persisted ranks must never be convention-ambiguous
        with pytest.raises(ValidationError):
            CandidateRank(
                candidate=_candidate(),
                score=0.87,
                verdict=C.ConfidenceVerdict.TENTATIVE,
            )

    def test_candidate_rank_rejects_inverted_interval(self):
        with pytest.raises(ValidationError):
            CandidateRank(
                candidate=_candidate(),
                score=0.87,
                ranking_convention="higher_is_better",
                ranking_interval=(3, 2),
                verdict=C.ConfidenceVerdict.TENTATIVE,
            )

    def test_candidate_rank_rejects_coverage_above_one(self):
        with pytest.raises(ValidationError):
            CandidateRank(
                candidate=_candidate(),
                score=0.87,
                ranking_convention="higher_is_better",
                coverage=1.2,
                verdict=C.ConfidenceVerdict.TENTATIVE,
            )

    def test_network_edge_rejects_self_loop_and_bad_score(self):
        base = {
            "source": "a",
            "target": "b",
            "modality": "MS2",
            "evidence_type": "ms2_cosine",
        }
        with pytest.raises(ValidationError):
            NetworkEdge(**{**base, "target": "a", "score": 0.5})
        with pytest.raises(ValidationError):
            NetworkEdge(**{**base, "score": 1.5})

    def test_fused_network_rejects_unknown_endpoint(self):
        kwargs = _net_kwargs()
        kwargs["edges"] = kwargs["edges"] + [
            NetworkEdge(
                source="n1", target="ghost", modality="MS2",
                score=0.5, evidence_type="ms2_cosine",
            )
        ]
        with pytest.raises(ValidationError):
            FusedNetwork(**kwargs)

    def test_fused_network_rejects_duplicate_node_ids(self):
        kwargs = _net_kwargs()
        kwargs["nodes"] = kwargs["nodes"] + [
            NetworkNode(id="n1", candidate_or_feature="feat-9", modality="MS2")
        ]
        with pytest.raises(ValidationError):
            FusedNetwork(**kwargs)

    def test_fused_network_overlap_stats(self):
        network = FusedNetwork(**_net_kwargs())
        stats = network.compute_overlap_stats()
        assert stats == {
            "nodes_ms2_only": 1,
            "nodes_hsqc_only": 1,
            "nodes_both": 1,
            "edges_ms2": 1,
            "edges_hsqc": 1,
            "edges_both": 1,
            "edges_cross_modal": 3,
        }


# ===========================================================================
# provenance
# ===========================================================================


class TestHashing:
    def test_sha256_bytes_matches_hashlib(self):
        data = b"phyto companion provenance"
        assert sha256_bytes(data) == hashlib.sha256(data).hexdigest()

    def test_sha256_file_streams_large_files(self, tmp_path):
        payload = bytes(range(256)) * 12288  # 3 MiB, spans many 1 MiB chunks
        big = tmp_path / "big.bin"
        big.write_bytes(payload)
        assert sha256_file(big) == hashlib.sha256(payload).hexdigest()

    def test_sha256_dict_is_order_independent(self):
        d1 = {"a": 1, "b": [2, 3], "c": {"x": "y"}}
        d2 = {"c": {"x": "y"}, "b": [2, 3], "a": 1}
        assert sha256_dict(d1) == sha256_dict(d2)

    def test_sha256_dict_is_content_sensitive(self):
        assert sha256_dict({"a": 1}) != sha256_dict({"a": 2})

    def test_sha256_dict_known_vector(self):
        assert sha256_dict({}) == hashlib.sha256(b"{}").hexdigest()

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_sha256_dict_rejects_non_finite_floats(self, bad):
        with pytest.raises(ValueError):
            sha256_dict({"x": bad})


class TestProvenanceBuilder:
    def _build(self, tmp_path):
        source = tmp_path / "sample.mzML"
        source.write_bytes(b"mzML payload " * 500)
        manifest = (
            ProvenanceBuilder()
            .with_input("sample.mzML", source)
            .with_model_version("shiftmpnn", "4.0.1")
            .with_database_version("mini_coconut", "v1", downloaded="2026-10-01")
            .with_parameters({"ppm_tolerance": 5.0, "paths": {"lib": tmp_path}})
            .with_environment()
            .build()
        )
        return manifest, source

    def test_build_and_verify_roundtrip(self, tmp_path):
        manifest, source = self._build(tmp_path)
        assert len(manifest.manifest_hash) == 64
        assert manifest.input_hashes["sample.mzML"] == sha256_file(source)
        assert manifest.model_versions == {"shiftmpnn": "4.0.1"}
        assert manifest.database_versions == {
            "mini_coconut": "v1 (downloaded 2026-10-01)"
        }
        assert "python_version" in manifest.environment
        assert verify_manifest(manifest, {"sample.mzML": source}) is True

    def test_verify_detects_modified_file(self, tmp_path):
        manifest, source = self._build(tmp_path)
        source.write_bytes(b"tampered")  # raw data was mutated after manifest
        with pytest.raises(ProvenanceError) as excinfo:
            verify_manifest(manifest, {"sample.mzML": source})
        assert "mismatch" in str(excinfo.value).lower()
        assert "expected" in excinfo.value.context

    def test_verify_rejects_incomplete_file_set(self, tmp_path):
        manifest, source = self._build(tmp_path)
        with pytest.raises(ProvenanceError) as excinfo:
            verify_manifest(manifest, {})
        assert excinfo.value.context["missing_labels"] == ["sample.mzML"]
        with pytest.raises(ProvenanceError):
            verify_manifest(manifest, {"sample.mzML": source, "extra": source})

    def test_verify_detects_tampered_manifest(self, tmp_path):
        manifest, source = self._build(tmp_path)
        tampered = manifest.model_copy(
            update={"input_hashes": {"sample.mzML": "0" * 64}}
        )
        with pytest.raises(ProvenanceError) as excinfo:
            verify_manifest(tampered, {"sample.mzML": source})
        assert excinfo.value.context["check"] == "manifest_hash"

    def test_with_input_hash_validates_digest(self):
        with pytest.raises(ProvenanceError):
            ProvenanceBuilder().with_input_hash("label", "NOTHEX")

    def test_verify_detects_missing_file(self, tmp_path):
        manifest, source = self._build(tmp_path)
        source.unlink()
        with pytest.raises(ProvenanceError) as excinfo:
            verify_manifest(manifest, {"sample.mzML": source})
        assert "missing" in str(excinfo.value).lower()


class TestAuditLog:
    def test_jsonl_roundtrip_and_sequence_continuity(self, tmp_path):
        path = tmp_path / "audit" / "trail.jsonl"
        log = AuditLog(path)
        first = log.log_action(
            agent="planner",
            tool="rdkit.canonicalize",
            input_hash=H64,
            output_hash="c" * 64,
            metadata={"tier": 1},
        )
        log.log_action(agent="spectroscopist", tool="shiftmpnn", input_hash=H64, output_hash="d" * 64)
        assert first["seq"] == 1

        reopened = AuditLog(path)  # append-only: seq must continue
        third = reopened.log_action(
            agent="qa_critic", tool="llm_judge", input_hash=H64, output_hash="e" * 64
        )
        assert third["seq"] == 3

        rows = reopened.to_dataframe()
        assert len(rows) == 3
        assert [row["seq"] for row in rows] == [1, 2, 3]
        assert rows[0]["agent"] == "planner"
        assert rows[0]["metadata"] == {"tier": 1}
        assert rows[2]["tool"] == "llm_judge"

        # every line is valid JSON with a parseable timestamp
        with path.open() as handle:
            for line in handle:
                record = json.loads(line)
                datetime.fromisoformat(record["timestamp"])


# ===========================================================================
# uncertainty
# ===========================================================================


class TestRankingAPI:
    def test_mandated_disambiguation_case(self):
        scores = [0.9, 0.5, 0.3]
        assert rank_higher_is_better(scores, 0) == (1, 1)
        assert rank_lower_is_better(scores, 0) == (3, 3)

    def test_higher_is_better(self):
        assert rank_higher_is_better([0.9, 0.8, 0.7], 0) == (1, 1)
        assert rank_higher_is_better([0.9, 0.9, 0.8], 0) == (1, 2)
        assert rank_higher_is_better([0.9, 0.9, 0.8], 1) == (1, 2)
        assert rank_higher_is_better([0.9, 0.5, 0.8], 1) == (3, 3)
        assert rank_higher_is_better([0.42], 0) == (1, 1)

    def test_lower_is_better(self):
        assert rank_lower_is_better([0.3, 0.5, 0.9], 0) == (1, 1)
        assert rank_lower_is_better([0.3, 0.3, 0.9], 0) == (1, 2)
        assert rank_lower_is_better([0.3, 0.3, 0.9], 1) == (1, 2)
        assert rank_lower_is_better([0.9, 0.5, 0.8], 1) == (1, 1)  # lowest ranks first
        assert rank_lower_is_better([0.42], 0) == (1, 1)

    @pytest.mark.parametrize(
        "rank_fn", [rank_higher_is_better, rank_lower_is_better]
    )
    def test_empty_list_raises(self, rank_fn):
        with pytest.raises(InvalidInputError):
            rank_fn([], 0)

    @pytest.mark.parametrize(
        "rank_fn", [rank_higher_is_better, rank_lower_is_better]
    )
    def test_bad_index_raises(self, rank_fn):
        with pytest.raises(InvalidInputError):
            rank_fn([0.5, 0.6], 5)

    def test_atol_defines_ties_in_both_directions(self):
        assert rank_higher_is_better([0.9, 0.9 - 1e-12, 0.8], 0) == (1, 2)
        assert rank_lower_is_better([0.2, 0.2 + 1e-12, 0.8], 0) == (1, 2)

    @pytest.mark.parametrize(
        "rank_fn", [rank_higher_is_better, rank_lower_is_better]
    )
    def test_invariant_under_permutation(self, rank_fn):
        rng = random.Random(42)
        for _ in range(25):
            n = rng.randint(2, 12)
            scores = [round(rng.random(), 2) for _ in range(n)]  # exact ties common
            target = rng.randrange(n)
            expected = rank_fn(scores, target)
            others = [s for i, s in enumerate(scores) if i != target]
            rng.shuffle(others)
            permuted = others[:target] + [scores[target]] + others[target:]
            assert rank_fn(permuted, target) == expected

    def test_variants_are_exact_duals(self):
        # Negating every score swaps the conventions: a documented property
        # both implementations must satisfy (ADR-0007).
        rng = random.Random(7)
        for _ in range(25):
            n = rng.randint(1, 10)
            scores = [round(rng.random(), 2) for _ in range(n)]
            target = rng.randrange(n)
            negated = [-s for s in scores]
            assert rank_lower_is_better(scores, target) == rank_higher_is_better(
                negated, target
            )
            assert margin_lower_is_better(scores, target) == pytest.approx(
                margin_higher_is_better(negated, target)
            )


class TestUniqueTop1:
    def test_higher_is_better(self):
        assert is_unique_top1_higher_is_better([0.9, 0.8], 0) is True
        assert is_unique_top1_higher_is_better([0.9, 0.9], 0) is False
        assert is_unique_top1_higher_is_better([0.5], 0) is True
        assert is_unique_top1_higher_is_better([0.8, 0.9], 0) is False

    def test_lower_is_better(self):
        assert is_unique_top1_lower_is_better([0.2, 0.8], 0) is True
        assert is_unique_top1_lower_is_better([0.2, 0.2], 0) is False
        assert is_unique_top1_lower_is_better([0.5], 0) is True
        assert is_unique_top1_lower_is_better([0.8, 0.2], 0) is False


class TestScoreMargin:
    def test_higher_is_better(self):
        assert margin_higher_is_better([0.5], 0) == math.inf
        assert margin_higher_is_better([0.9, 0.9, 0.9], 1) == 0.0
        assert margin_higher_is_better([0.9, 0.8, 0.7], 0) == pytest.approx(0.1)
        assert margin_higher_is_better([0.9, 0.8], 1) == pytest.approx(-0.1)

    def test_lower_is_better(self):
        assert margin_lower_is_better([0.5], 0) == math.inf
        assert margin_lower_is_better([0.3, 0.3, 0.3], 1) == 0.0
        assert margin_lower_is_better([0.3, 0.5, 0.9], 0) == pytest.approx(0.2)
        # highest score is the worst candidate when lower wins: the best
        # competitor (0.3) undercuts it, giving a large negative margin
        assert margin_lower_is_better([0.9, 0.5, 0.3], 0) == pytest.approx(0.3 - 0.9)


class TestCoverageConfidence:
    def test_perfect_match_is_one(self):
        assert coverage_confidence(1.0, 1.0, 0.0) == pytest.approx(1.0)

    def test_symmetric_in_coverages(self):
        assert coverage_confidence(0.8, 0.2, 0.0) == coverage_confidence(0.2, 0.8, 0.0)

    def test_decays_with_residual(self):
        low = coverage_confidence(0.9, 0.9, 0.5)
        high = coverage_confidence(0.9, 0.9, 0.1)
        assert 0.0 <= low < high <= 1.0

    def test_stays_within_bounds(self):
        value = coverage_confidence(0.2, 0.3, 50.0)
        assert 0.0 <= value <= 1.0

    @pytest.mark.parametrize(
        ("args",),
        [
            ((1.2, 1.0, 0.0),),
            ((0.5, -0.1, 0.0),),
            ((0.5, 0.5, -0.01),),
        ],
        ids=["obs>1", "ref<0", "negative_residual"],
    )
    def test_invalid_inputs_raise(self, args):
        with pytest.raises(InvalidInputError):
            coverage_confidence(*args)


class TestVerdictFromEvidence:
    T1 = C.ToolTier.TIER_1_DETERMINISTIC
    T2 = C.ToolTier.TIER_2_ML_VALIDATED
    T3 = C.ToolTier.TIER_3_LLM_GENERATED
    L1 = C.MSIConfidenceLevel.LEVEL_1
    L2 = C.MSIConfidenceLevel.LEVEL_2
    L3 = C.MSIConfidenceLevel.LEVEL_3
    L4 = C.MSIConfidenceLevel.LEVEL_4

    def test_returns_two_axis_assessment(self):
        result = verdict_from_evidence(0.9, 0.2, 1, self.L1, self.T1, novelty="novel")
        assert isinstance(result, EvidenceAssessment)
        assert result.verdict is C.ConfidenceVerdict.CONFIRMED
        assert result.novelty == "novel"

    def test_default_novelty_is_uncertain(self):
        result = verdict_from_evidence(0.9, 0.2, 1, self.L1, self.T1)
        assert result.novelty == "uncertain"

    def test_assessment_is_frozen(self):
        result = verdict_from_evidence(0.9, 0.2, 1, self.L1, self.T1)
        with pytest.raises(FrozenInstanceError):
            result.verdict = C.ConfidenceVerdict.ABSTAIN

    @pytest.mark.parametrize(
        ("coverage", "margin", "n_tied", "msi", "tier", "expected_verdict"),
        [
            (0.90, 0.20, 1, L1, T1, C.ConfidenceVerdict.CONFIRMED),
            (0.90, math.inf, 1, L1, T1, C.ConfidenceVerdict.CONFIRMED),
            (0.90, 0.05, 1, L2, T2, C.ConfidenceVerdict.TENTATIVE),
            # Level 1 with a weak margin must NOT auto-confirm
            (0.90, 0.05, 1, L1, T1, C.ConfidenceVerdict.TENTATIVE),
            (0.90, 0.20, 3, L2, T2, C.ConfidenceVerdict.AMBIGUOUS),
            (0.30, 0.90, 1, L1, T1, C.ConfidenceVerdict.ABSTAIN),
            (0.90, 0.20, 1, L2, T3, C.ConfidenceVerdict.CANDIDATE),
            # Strong match with no library identity is still only tentative
            (0.90, 0.20, 1, L4, T2, C.ConfidenceVerdict.TENTATIVE),
            (0.60, 0.01, 1, L3, T2, C.ConfidenceVerdict.CANDIDATE),
        ],
        ids=[
            "confirmed",
            "confirmed_inf_margin",
            "tentative",
            "L1_weak_margin_falls_through",
            "ambiguous",
            "abstain_low_coverage",
            "tier3_never_above_candidate",
            "L4_strong_match_is_tentative",
            "partial_coverage_candidate",
        ],
    )
    def test_confidence_decision_table(
        self, coverage, margin, n_tied, msi, tier, expected_verdict
    ):
        result = verdict_from_evidence(coverage, margin, n_tied, msi, tier)
        assert result.verdict is expected_verdict
        assert result.novelty == "uncertain"

    @pytest.mark.parametrize(
        ("coverage", "margin", "n_tied", "msi", "tier", "novelty", "expected_verdict"),
        [
            (0.90, 0.20, 1, L1, T1, "known", C.ConfidenceVerdict.CONFIRMED),
            (0.90, 0.20, 1, L1, T1, "novel", C.ConfidenceVerdict.CONFIRMED),
            (0.90, 0.20, 1, L2, T3, "novel", C.ConfidenceVerdict.CANDIDATE),
            (0.30, 0.90, 1, L1, T1, "known", C.ConfidenceVerdict.ABSTAIN),
        ],
        ids=[
            "confirmed_known",
            "confirmed_novel",
            "candidate_novel",
            "abstain_known",
        ],
    )
    def test_novelty_is_independent_of_confidence(
        self, coverage, margin, n_tied, msi, tier, novelty, expected_verdict
    ):
        """The four mandated combinations: novelty must never move the verdict."""
        result = verdict_from_evidence(
            coverage, margin, n_tied, msi, tier, novelty=novelty
        )
        assert result.verdict is expected_verdict
        assert result.novelty == novelty

    def test_thresholds_are_sane(self):
        assert 0.0 <= MIN_MARGIN_FOR_TENTATIVE < MIN_MARGIN_FOR_CONFIRMED
        assert MIN_MARGIN_FOR_CONFIRMED <= 1.0

    @pytest.mark.parametrize(
        ("kwargs",),
        [
            ({"coverage": 1.5, "margin": 0.2, "n_tied": 1, "msi_level": L2, "tool_tier": T2},),
            ({"coverage": 0.9, "margin": 0.2, "n_tied": 0, "msi_level": L2, "tool_tier": T2},),
            (
                {
                    "coverage": 0.9, "margin": 0.2, "n_tied": 1,
                    "msi_level": L2, "tool_tier": T2, "novelty": "maybe",
                },
            ),
        ],
        ids=["coverage_out_of_range", "n_tied_zero", "invalid_novelty"],
    )
    def test_invalid_inputs_raise(self, kwargs):
        with pytest.raises(InvalidInputError):
            verdict_from_evidence(**kwargs)


# ===========================================================================
# schemas
# ===========================================================================


class TestSchemas:
    def test_every_response_carries_provenance_manifest_id(self):
        response_classes = [
            name
            for name in S.__all__
            if name.endswith("Response") and name != "ErrorResponse"
        ]
        assert len(response_classes) == 5
        for name in response_classes:
            cls = getattr(S, name)
            assert "provenance_manifest_id" in cls.model_fields, name

    def test_spectrum_upload_roundtrip(self):
        request = S.SpectrumUploadRequest(
            filename="sample.mzML",
            format="mzml",
            content_b64="PD94bWw=",
            metadata={"instrument": "QTOF"},
        )
        assert S.SpectrumUploadRequest.model_validate(request.model_dump()) == request
        response = S.SpectrumUploadResponse(
            spectrum_id="spec-1",
            provenance_manifest_id="man-1",
            warnings=["solvent peak flagged"],
            received_at=datetime.now(timezone.utc),
        )
        assert (
            S.SpectrumUploadResponse.model_validate(response.model_dump()) == response
        )

    def test_response_missing_provenance_is_rejected(self):
        with pytest.raises(ValidationError):
            S.NetworkResponse(
                network_id="net-1", n_nodes=3, n_edges=3  # no manifest id
            )

    def test_analysis_request_defaults_to_dry_run_off(self):
        request = S.AnalysisRequest(sample_id="s-1", workflow="single_compound")
        assert request.dry_run is False

    def test_elucidation_response_roundtrip(self):
        response = S.ElucidationResponse(
            run_id="run-9",
            candidates=[
                S.ElucidationCandidateSummary(
                    rank=1,
                    name="quercetin",
                    smiles="Oc1cc(O)c(c(c1)O)-c2oc3cc(O)cc(O)c3c(=O)c2",
                    score=0.87,
                    ranking_convention="lower_is_better",
                    verdict=C.ConfidenceVerdict.TENTATIVE,
                    coverage=0.91,
                    margin=0.12,
                    ranking_interval=(1, 2),
                    tied_candidate_count=2,
                )
            ],
            evidence_graph_refs=["eg-1"],
            provenance_manifest_id="man-9",
        )
        rebuilt = S.ElucidationResponse.model_validate(response.model_dump())
        assert rebuilt == response
        assert rebuilt.candidates[0].verdict is C.ConfidenceVerdict.TENTATIVE

    def test_pharmacology_request_needs_an_identifier(self):
        with pytest.raises(ValidationError):
            S.PharmacologyRequest()
        assert S.PharmacologyRequest(smiles=["CCO"]).smiles == ["CCO"]

    def test_pharmacology_disclaimer_is_honest(self):
        response = S.PharmacologyResponse(
            hypotheses=[{"target": "COX-2", "mechanism": "inhibition"}],
            provenance_manifest_id="man-10",
        )
        assert "hypothes" in response.disclaimer
        assert "not conclusions" in response.disclaimer

    def test_error_response(self):
        error = S.ErrorResponse(status=422, code="InvalidInputError", message="bad")
        assert S.ErrorResponse.model_validate(error.model_dump()) == error
        with pytest.raises(ValidationError):
            S.ErrorResponse(status=99, code="x", message="y")  # not an HTTP status
