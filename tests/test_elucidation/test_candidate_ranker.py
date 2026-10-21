"""Candidate ranker tests: correctness, ties, missing modalities, conventions."""

from __future__ import annotations

import pytest

from phyto_shared.constants import ConfidenceVerdict
from phyto_shared.exceptions import InvalidInputError
from phyto_shared.models import Candidate, MolecularFormula

from elucidation.candidate_ranker import (
    ObservedEvidence,
    RankingConfig,
    parse_formula_string,
    rank_candidates,
    score_molecule,
)
from elucidation.shift_predictor import MockShiftPredictor


def _candidate(name: str, smiles: str, formula_text: str) -> Candidate:
    counts = parse_formula_string(formula_text)
    return Candidate(
        id=name.lower(),
        name=name,
        smiles=smiles,
        formula=MolecularFormula(counts=counts),
        exact_mass=MolecularFormula(counts=counts).exact_mass(),
        provenance="experimental",
        source="test",
    )


PHENOL = _candidate("phenol", "Oc1ccccc1", "C6H6O")
BENZENE = _candidate("benzene", "c1ccccc1", "C6H6")

# observed evidence constructed from the mock's phenol lookup table:
# 5 aromatic CH cross-peaks + OH proton + 13C list
PHENOL_HSQC = [
    (6.85, 116.0, "CH"),
    (7.20, 130.0, "CH"),
    (6.80, 121.0, "CH"),
    (7.20, 130.0, "CH"),
    (6.85, 116.0, "CH"),
]
PHENOL_OBSERVED = ObservedEvidence(
    h_1d=[5.30, 6.85, 7.20, 6.80, 7.20, 6.85],
    c_1d=[155.0, 116.0, 130.0, 121.0, 130.0, 116.0],
    hsqc=PHENOL_HSQC,
    formula="C6H6O",
    precursor_mz=95.0491,  # [M+H]+ of phenol (94.0419 + 1.007276)
    precursor_adduct="[M+H]+",
)


class TestParseFormula:
    def test_valid(self):
        assert parse_formula_string("C6H12O6") == {"C": 6, "H": 12, "O": 6}

    def test_single_count_implicit(self):
        assert parse_formula_string("CH4") == {"C": 1, "H": 4}

    @pytest.mark.parametrize("bad", ["", "Xx2", "C6H12O6!", "6C"])
    def test_invalid_raises(self, bad):
        with pytest.raises(InvalidInputError):
            parse_formula_string(bad)


class TestRanking:
    def test_perfect_match_outranks_mismatch(self):
        # no precursor in the evidence: the precursor filter must not
        # exclude benzene (the formula gate handles it instead)
        observed = PHENOL_OBSERVED.model_copy(update={"precursor_mz": None})
        ranks = rank_candidates(observed, [PHENOL, BENZENE], predictor=MockShiftPredictor())
        assert ranks[0].candidate.name == "phenol"
        assert ranks[0].score < ranks[1].score
        # benzene fails the formula gate -> distance pinned at cap
        assert ranks[1].score == pytest.approx(RankingConfig().cap)

    def test_perfect_match_scores_near_zero(self):
        ranks = rank_candidates(
            PHENOL_OBSERVED, [PHENOL], predictor=MockShiftPredictor()
        )
        assert ranks[0].score == pytest.approx(0.0, abs=1e-6)
        assert ranks[0].coverage == pytest.approx(1.0)
        assert ranks[0].ranking_interval == (1, 1)

    def test_ranking_convention_always_lower_is_better(self):
        ranks = rank_candidates(
            PHENOL_OBSERVED, [PHENOL, BENZENE], predictor=MockShiftPredictor()
        )
        assert all(rank.ranking_convention == "lower_is_better" for rank in ranks)

    def test_tied_candidates_are_ambiguous(self):
        phenol_copy = PHENOL.model_copy(update={"id": "phenol-2"})
        ranks = rank_candidates(
            PHENOL_OBSERVED, [PHENOL, phenol_copy], predictor=MockShiftPredictor()
        )
        assert ranks[0].ranking_interval == (1, 2)
        assert ranks[0].tied_candidates == ["phenol", "phenol-2"]
        assert ranks[0].verdict is ConfidenceVerdict.AMBIGUOUS
        assert ranks[1].verdict is ConfidenceVerdict.AMBIGUOUS

    def test_unique_top_is_tentative(self):
        # MSI Level 2 (library match, no standard) -> TENTATIVE, not CONFIRMED
        ranks = rank_candidates(
            PHENOL_OBSERVED, [PHENOL, BENZENE], predictor=MockShiftPredictor()
        )
        assert ranks[0].verdict is ConfidenceVerdict.TENTATIVE

    def test_missing_modalities_handled(self):
        only_h = ObservedEvidence(h_1d=[7.26] * 6, formula="C6H6")
        ranks = rank_candidates(only_h, [BENZENE], predictor=MockShiftPredictor())
        assert ranks[0].coverage == pytest.approx(1.0)

    def test_no_modality_raises(self):
        empty = ObservedEvidence(formula="C6H6")
        with pytest.raises(InvalidInputError):
            rank_candidates(empty, [BENZENE], predictor=MockShiftPredictor())

    def test_ms2_cosine_scoring(self):
        from elucidation.candidate_ranker import ms2_cosine

        reference = [(95.0, 100.0), (77.0, 50.0), (51.0, 20.0)]
        same = [(95.0, 100.0), (77.0, 50.0), (51.0, 20.0)]
        shifted = [(95.01, 100.0), (77.01, 50.0)]  # within 0.02 Da
        disjoint = [(300.0, 100.0), (400.0, 80.0)]
        cosine_same, n_same = ms2_cosine(reference, same)
        cosine_shift, n_shift = ms2_cosine(reference, shifted)
        cosine_none, n_none = ms2_cosine(reference, disjoint)
        assert cosine_same == pytest.approx(1.0)
        assert n_same == 3
        assert cosine_shift == pytest.approx(1.0, abs=1e-6)  # tolerance
        assert n_shift == 2
        assert cosine_none == 0.0 and n_none == 0

    def test_ms2_scoring_disabled_without_reference(self):
        observed = ObservedEvidence(
            h_1d=[6.85, 7.20], formula="C6H6O", ms2=[(95.0, 100.0)]
        )
        ranks = rank_candidates(observed, [PHENOL], predictor=MockShiftPredictor())
        # ranked on 1H alone; the disabled modality is reported as a gap
        assert ranks
        assert any(
            item.relation == "ms2_scoring_disabled"
            for item in ranks[0].evidence_graph.evidence
        )

    def test_ms2_reference_scores_identical_candidate_best(self):
        observed = ObservedEvidence(
            h_1d=[6.85, 7.20],
            formula="C6H6O",
            ms2=[(95.0, 100.0), (77.0, 50.0)],
        )
        reference = {"phenol": [(95.0, 100.0), (77.0, 50.0)]}
        ranks = rank_candidates(
            observed, [PHENOL], predictor=MockShiftPredictor(),
            reference_ms2=reference,
        )
        assert any(
            "ms2" in item.source_spectrum
            for item in ranks[0].evidence_graph.evidence
        )

    def test_precursor_mz_is_a_filter_not_a_score(self):
        # a candidate 100 ppm off the precursor is EXCLUDED from ranking
        observed = ObservedEvidence(
            h_1d=[6.85, 7.20], formula="C6H6O", precursor_mz=95.0491,
            precursor_adduct="[M+H]+",
        )
        wrong_mass = PHENOL.model_copy(update={"exact_mass": 150.0})
        ranks = rank_candidates(
            observed, [PHENOL, wrong_mass], predictor=MockShiftPredictor()
        )
        assert [r.candidate.id for r in ranks] == ["phenol"]

    def test_evidence_graph_attached(self):
        ranks = rank_candidates(
            PHENOL_OBSERVED, [PHENOL], predictor=MockShiftPredictor()
        )
        graph = ranks[0].evidence_graph
        assert graph is not None
        assert any(item.relation.startswith("matched") for item in graph.evidence)

    def test_formula_mismatch_recorded_as_gap(self):
        observed = PHENOL_OBSERVED.model_copy(update={"precursor_mz": None})
        ranks = rank_candidates(observed, [BENZENE], predictor=MockShiftPredictor())
        assert any(
            item.relation == "formula_mismatch"
            for item in ranks[0].evidence_graph.evidence
        )


class TestScoreMolecule:
    def test_scores_smiles_directly(self):
        score, results = score_molecule(
            PHENOL_OBSERVED, PHENOL.smiles, RankingConfig(), MockShiftPredictor()
        )
        assert score == pytest.approx(0.0, abs=1e-6)
        assert "hsqc" in results

    def test_unparseable_smiles_raises(self):
        from phyto_shared.exceptions import CloudServiceError

        with pytest.raises(CloudServiceError):
            score_molecule(
                PHENOL_OBSERVED, "not-smiles", RankingConfig(), MockShiftPredictor()
            )
