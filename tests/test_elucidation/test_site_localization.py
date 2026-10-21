"""Site localization tests: benchmarking mode, ambiguity, production mode."""

from __future__ import annotations

import pytest
from rdkit import Chem
from rdkit.Chem import rdMolDescriptors

from phyto_shared.models import Candidate, MolecularFormula

from elucidation.candidate_ranker import (
    ObservedEvidence,
    parse_formula_string,
    score_molecule,
)
from elucidation.hungarian_matcher import MatchResult
from elucidation.novel_structure_generator import NovelCandidate
from elucidation.shift_predictor import MockShiftPredictor
from elucidation.site_localization import localize_site


def _candidate(name: str, smiles: str) -> Candidate:
    counts = parse_formula_string(
        rdMolDescriptors.CalcMolFormula(Chem.MolFromSmiles(smiles))
    )
    return Candidate(
        id=name.lower(),
        name=name,
        smiles=smiles,
        formula=MolecularFormula(counts=counts),
        exact_mass=MolecularFormula(counts=counts).exact_mass(),
        provenance="experimental",
        source="test",
    )


ETHANOL = _candidate("ethanol", "CCO")
BENZENE = _candidate("benzene", "c1ccccc1")
METHANOL = _candidate("methanol", "CO")

PREDICTOR = MockShiftPredictor()


def _baseline_match(observed: ObservedEvidence, parent: Candidate) -> MatchResult:
    score, _ = score_molecule(observed, parent.smiles, predictor=PREDICTOR,
                              exact_mass=parent.exact_mass)
    return MatchResult(
        score=score, coverage=0.0, n_observed=0, n_predicted=0,
        ranking_convention="lower_is_better",
    )


def _ethylene_glycol_observed() -> ObservedEvidence:
    # taken from the mock predictor's ethylene glycol lookup:
    # CH2 at (3.70, 62.0) x2, OH protons at 4.80
    return ObservedEvidence(
        h_1d=[3.70, 3.70, 4.80, 4.80],
        c_1d=[62.0, 62.0],
        hsqc=[(3.70, 62.0, "CH2"), (3.70, 62.0, "CH2")],
        formula="C2H6O2",
    )


def _novel(parent: Candidate, edit: str, smiles: str) -> NovelCandidate:
    counts = parse_formula_string(
        rdMolDescriptors.CalcMolFormula(Chem.MolFromSmiles(smiles))
    )
    return NovelCandidate(
        id="novel-test",
        name="test novel",
        smiles=smiles,
        formula=MolecularFormula(counts=counts),
        exact_mass=MolecularFormula(counts=counts).exact_mass(),
        provenance="predicted",
        source="test",
        parent_ik=Chem.MolToInchiKey(Chem.MolFromSmiles(parent.smiles)),
        edit_type=edit,
        edit_site=None,
        generated_smiles=smiles,
    )


class TestBenchmarkMode:
    def test_true_site_ranks_first(self):
        observed = _ethylene_glycol_observed()
        novel = _novel(ETHANOL, "hydroxylation", "OCCO")
        localization = localize_site(
            observed,
            ETHANOL,
            _baseline_match(observed, ETHANOL),
            novel,
            predictor=PREDICTOR,
            true_edit_site=0,  # ethanol atom 0 (the CH3 carbon)
        )
        assert localization.edit_atom_index == 0
        assert localization.rank_of_true_site == 1
        assert localization.n_possible_sites == 2  # both carbons host an H
        assert localization.localization_confidence == 1.0

    def test_rank_of_true_site_none_in_production(self):
        observed = _ethylene_glycol_observed()
        novel = _novel(ETHANOL, "hydroxylation", "OCCO")
        localization = localize_site(
            observed,
            ETHANOL,
            _baseline_match(observed, ETHANOL),
            novel,
            predictor=PREDICTOR,
        )
        assert localization.rank_of_true_site is None
        assert localization.edit_atom_index == 0  # answer still correct


class TestAmbiguity:
    def test_symmetric_parent_reports_competing_sites(self):
        # benzene: all six CH sites give the SAME product (phenol)
        observed = ObservedEvidence(
            h_1d=[5.30, 6.85, 7.20, 6.80, 7.20, 6.85],
            hsqc=[(6.85, 116.0, "CH")] * 5,
            formula="C6H6O",
        )
        novel = _novel(BENZENE, "hydroxylation", "Oc1ccccc1")
        localization = localize_site(
            observed,
            BENZENE,
            _baseline_match(observed, BENZENE),
            novel,
            predictor=PREDICTOR,
        )
        assert localization.n_possible_sites == 6
        assert len(localization.competing_sites) == 6
        assert localization.localization_confidence == pytest.approx(1.0 / 6.0, abs=1e-3)
        assert any("not site-resolved" in note for note in localization.notes)


class TestTrivialCases:
    def test_single_possible_site_is_flagged_trivial(self):
        # methanol has exactly one carbon -> one hydroxylation site
        observed = ObservedEvidence(
            h_1d=[3.30, 4.50], formula="CH4O2"
        )
        novel = _novel(METHANOL, "hydroxylation", "OCO")
        localization = localize_site(
            observed,
            METHANOL,
            _baseline_match(observed, METHANOL),
            novel,
            predictor=PREDICTOR,
        )
        assert localization.n_possible_sites == 1
        assert any("trivially uninformative" in note for note in localization.notes)

    def test_supporting_evidence_present(self):
        observed = _ethylene_glycol_observed()
        novel = _novel(ETHANOL, "hydroxylation", "OCCO")
        localization = localize_site(
            observed, ETHANOL, _baseline_match(observed, ETHANOL),
            novel, predictor=PREDICTOR,
        )
        assert localization.supporting_evidence
        assert localization.supporting_evidence[0].relation == "site_improvement"
