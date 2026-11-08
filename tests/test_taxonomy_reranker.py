"""Taxonomy reranker tests: bonuses, evidence, stability, skips (ADR-0052)."""

from __future__ import annotations

import pytest
from rdkit import Chem

from phyto_shared.constants import ConfidenceVerdict
from phyto_shared.literature_models import PaperHit
from phyto_shared.models import (
    Candidate,
    CandidateRank,
    EvidenceGraph,
    EvidenceItem,
    MolecularFormula,
)
from phyto_shared.taxonomy_models import ReportedCompound, TaxonPrior

from elucidation.taxonomy_reranker import (
    TAXON_BONUS,
    rerank_with_taxon_prior,
    taxon_bonus_for,
)

PANDURATIN = "CC(C)=CCC[C@@H](C)C(=O)c1c(O)cc(O)c(C)c1"  # logP ~ 4.0
PINOCEMBRIN = "O=c1cc(-c2ccccc2O)oc2cc(O)ccc12"  # logP ~ 2.4
QUERCETIN = "C1=CC(=C(C=C1C2=C(C(=O)C3=C(C=C(C=C3O2)O)O)O)O)O"


def _ik(smiles: str) -> str:
    return Chem.MolToInchiKey(Chem.MolFromSmiles(smiles))


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


def _prior(*levels: str) -> TaxonPrior:
    prior = TaxonPrior()
    for level in levels:
        smiles = {"species": PANDURATIN, "genus": PINOCEMBRIN}[level]
        organism = {"species": "Boesenbergia rotunda", "genus": "Boesenbergia"}[level]
        prior.add(
            ReportedCompound(
                compound_id=f"{level}-1", name="compound", smiles=smiles,
                inchikey=_ik(smiles), source_organism=organism,
                source_database={"species": "LOTUS", "genus": "KNApSAcK"}[level],
                source_reference="ref", taxon_level=level,  # type: ignore[arg-type]
            )
        )
    return prior


def _bonus(item: CandidateRank, before: float) -> float:
    return round(before - item.score, 6)


def _fixed_logp(value: float):
    def estimate(smiles: str) -> float:
        return value

    return estimate


class TestBonusApplication:
    def test_species_bonus_reorders_and_unmatched_untouched(self) -> None:
        ranks = [
            _rank("c1", "quercetin", QUERCETIN, 1.00),
            _rank("c2", "panduratin A", PANDURATIN, 1.20),
        ]
        out = rerank_with_taxon_prior(ranks, _prior("species"), None)
        assert out[0].candidate.name == "panduratin A"
        assert _bonus(out[0], 1.20) == pytest.approx(TAXON_BONUS["species"])
        assert out[1].score == 1.00  # no match -> no change
        assert out[1].taxon_prior_match == "none"

    def test_bonus_magnitudes_ordered_and_convention_respected(self) -> None:
        species = rerank_with_taxon_prior(
            [_rank("c", "panduratin A", PANDURATIN, 1.0)], _prior("species"), None
        )[0]
        genus = rerank_with_taxon_prior(
            [_rank("c", "pinocembrin", PINOCEMBRIN, 1.0)], _prior("genus"), None
        )[0]
        assert _bonus(species, 1.0) == pytest.approx(0.30)
        assert _bonus(genus, 1.0) == pytest.approx(0.15)
        # a similarity score gets the bonus ADDED, not subtracted
        similarity = _rank("c", "panduratin A", PANDURATIN, 0.80)
        similarity = similarity.model_copy(
            update={"ranking_convention": "higher_is_better"}
        )
        out = rerank_with_taxon_prior([similarity], _prior("species"), None)
        assert out[0].score == pytest.approx(0.80 + 0.30)

    def test_solvent_damping_reduces_bonus_and_gaps_leave_it_undamped(self) -> None:
        undamped = taxon_bonus_for("species", PANDURATIN, None, _fixed_logp(4.0))
        damped = taxon_bonus_for("species", PANDURATIN, "water", _fixed_logp(4.0))
        assert damped < undamped == TAXON_BONUS["species"]
        assert 0.0 < damped
        # unknown solvent: the filter is skipped, not invented
        assert (
            taxon_bonus_for("species", PANDURATIN, "vinegar", _fixed_logp(4.0))
            == TAXON_BONUS["species"]
        )

        def _unavailable(smiles: str) -> float:
            raise ValueError("no logP")

        # unavailable logP: bonus stays undamped
        assert (
            taxon_bonus_for("species", PANDURATIN, "water", _unavailable)
            == TAXON_BONUS["species"]
        )


class TestEvidenceAndProvenance:
    def test_taxon_evidence_provenance_and_spectral_evidence(self) -> None:
        rank = _rank("c", "panduratin A", PANDURATIN, 1.0)
        rank.evidence_graph.evidence.append(
            EvidenceItem(
                source_spectrum="observed:hsqc",
                spectral_feature="4.52/102.3 ppm",
                relation="matched:hsqc", strength=0.9,
            )
        )
        out = rerank_with_taxon_prior([rank], _prior("species"), None)
        graph = out[0].evidence_graph
        item = next(e for e in graph.evidence if e.relation == "taxon_prior")
        assert item.source_spectrum == "LOTUS"  # the database that supplied it
        assert item.strength == pytest.approx(0.30, abs=1e-3)
        assert "Boesenbergia rotunda" in item.spectral_feature
        # the original spectral evidence survives the rerank
        assert any(e.relation == "matched:hsqc" for e in graph.evidence)
        # and the provenance list carries the database reports
        assert [p.taxon_level for p in out[0].taxon_prior_provenance] == ["species"]

    def test_reported_in_species_paper_evidence_attached(self) -> None:
        paper = PaperHit(
            pmid="12345678", title="Panduratin A from Boesenbergia rotunda",
            source="PubMed",
        )
        out = rerank_with_taxon_prior(
            [_rank("c", "panduratin A", PANDURATIN, 1.0)],
            _prior("species"),
            None,
            species_papers={_ik(PANDURATIN): [paper]},
        )
        item = next(
            e for e in out[0].evidence_graph.evidence
            if e.relation == "reported_in_species_paper"
        )
        assert item.source_spectrum == "PubMed:12345678"


class TestSortingAndSkips:
    def test_equal_adjusted_scores_keep_original_order(self) -> None:
        ranks = [
            _rank("b", "panduratin A", PANDURATIN, 1.00),
            _rank("a", "panduratin A isomer", PANDURATIN, 0.95),
        ]
        out = rerank_with_taxon_prior(ranks, _prior("species"), None)
        # same bonus for both -> relative order unchanged
        assert [r.candidate.id for r in out] == ["a", "b"]
        exact_ties = [
            _rank("first", "panduratin A", PANDURATIN, 1.00),
            _rank("second", "panduratin A again", PANDURATIN, 1.00),
        ]
        out = rerank_with_taxon_prior(exact_ties, _prior("species"), None)
        assert [r.candidate.id for r in out] == ["first", "second"]

    def test_empty_or_missing_prior_returns_ranks_unchanged(self) -> None:
        ranks = [
            _rank("c1", "quercetin", QUERCETIN, 1.00),
            _rank("c2", "panduratin A", PANDURATIN, 1.20),
        ]
        out = rerank_with_taxon_prior(ranks, TaxonPrior(), None)
        assert out is not ranks
        assert [r.score for r in out] == [1.00, 1.20]
        assert all(r.taxon_prior_match == "none" for r in out)
        # explicit None prior (reranker never ran) behaves identically
        out = rerank_with_taxon_prior(
            [_rank("c1", "quercetin", QUERCETIN, 1.00)], None, "methanol"
        )
        assert out[0].score == 1.00

    def test_no_match_candidate_gets_no_taxon_evidence(self) -> None:
        out = rerank_with_taxon_prior(
            [_rank("c", "quercetin", QUERCETIN, 1.0)], _prior("species"), None
        )
        assert out[0].taxon_prior_match == "none"
        assert out[0].taxon_prior_provenance == []
        assert not [
            e for e in out[0].evidence_graph.evidence if e.relation == "taxon_prior"
        ]
