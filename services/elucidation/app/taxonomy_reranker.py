"""Taxonomy-aware post-ranking reranker (ADR-0052).

Additive by contract: this module NEVER touches ``candidate_ranker``'s
scoring logic.  It consumes the ranked list the ranker produced, looks
each candidate's InChIKey up in the taxon prior, and adjusts the
combined score by a bonus that reflects how strongly the compound is
reported from the sample's taxon:

    species match:  +0.30
    genus match:    +0.15
    family match:   +0.05
    no match:        0.00

The bonus is scaled DOWN (never up) by extraction-solvent compatibility
when the user supplied a solvent and the structure yields a logP: a
cardamonin report is weaker evidence in a hexane extract than in a
methanol one.  This is a soft multiplier with no hard exclusion --
``candidate_ranker`` already excluded what needed excluding; the reranker
only re-weights prior evidence (ADR-0052).

Scores keep their ``ranking_convention``: a bonus SUBTRACTS from a
``lower_is_better`` distance and ADDS to a ``higher_is_better``
similarity.  ``margin``, ``ranking_interval`` and ``tied_candidates``
are left untouched -- they describe the spectral ranking, and the
attached ``taxon_prior`` evidence item states the bonus separately so a
reader can always undo it.
"""

from __future__ import annotations

import logging
from typing import Callable, Literal

from phyto_shared.literature_models import PaperHit
from phyto_shared.models import CandidateRank, EvidenceGraph, EvidenceItem
from phyto_shared.solvent_prior import normalise_solvent, solvent_compatibility
from phyto_shared.taxonomy_models import TaxonLevel, TaxonPrior

__all__ = [
    "TAXON_BONUS",
    "PAPER_EVIDENCE_STRENGTH",
    "taxon_bonus_for",
    "rerank_with_taxon_prior",
]

logger = logging.getLogger(__name__)

TAXON_BONUS: dict[str, float] = {
    "species": 0.30,
    "genus": 0.15,
    "family": 0.05,
}

#: Fixed evidence strength for one confirmed compound-in-species paper.
#: Provenance, not score: papers never change the bonus itself.
PAPER_EVIDENCE_STRENGTH: float = 0.2

LogPEstimator = Callable[[str], float]


def taxon_bonus_for(
    level: TaxonLevel | None,
    smiles: str,
    solvent: str | None,
    estimate_logp: LogPEstimator,
) -> float:
    """The bonus for one candidate after optional solvent damping.

    Unknown/unnormalisable solvents return the UNDAMPED bonus: the
    solvent filter is skipped, not invented (ADR-0052).  So does a
    structure whose logP cannot be computed.
    """
    if level is None:
        return 0.0
    bonus = TAXON_BONUS[level]
    if not solvent:
        return bonus
    try:
        normalised = normalise_solvent(solvent)
    except Exception:
        logger.warning(
            "solvent %r has no compatibility profile; taxon bonus left undamped",
            solvent,
        )
        return bonus
    try:
        logp = estimate_logp(smiles)
    except Exception:
        logger.warning("logP unavailable for %r; taxon bonus left undamped", smiles)
        return bonus
    return bonus * solvent_compatibility(logp, normalised)


def _inchikey_of(smiles: str) -> str | None:
    from rdkit import Chem

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    return Chem.MolToInchiKey(mol)


def _paper_evidence(papers: list[PaperHit]) -> list[EvidenceItem]:
    return [
        EvidenceItem(
            source_spectrum=f"PubMed:{paper.pmid}" if paper.pmid else (paper.doi or "literature"),
            spectral_feature=paper.title,
            relation="reported_in_species_paper",
            strength=PAPER_EVIDENCE_STRENGTH,
        )
        for paper in papers[:5]
    ]


def rerank_with_taxon_prior(
    ranked: list[CandidateRank],
    prior: TaxonPrior | None,
    solvent: str | None,
    species_papers: dict[str, list[PaperHit]] | None = None,
    estimate_logp: LogPEstimator | None = None,
) -> list[CandidateRank]:
    """Rerank a ranked list against the taxon prior, with provenance.

    Each candidate gets ``taxon_prior_match`` (strongest level), its
    ``taxon_prior_provenance`` (the database reports), and an evidence
    item (``relation="taxon_prior"``) naming the database that supplied
    the prior with the applied bonus as ``strength``.  Candidates with a
    confirmed literature hit additionally get
    ``relation="reported_in_species_paper"`` items.

    The output is stably sorted by adjusted score in the convention's
    direction: candidates with equal adjusted scores keep their original
    relative order.  A ``None``/empty prior is a no-op that returns the
    input ranks untouched (all matches stay "none").
    """
    estimate_logp = estimate_logp or _default_estimate_logp
    species_papers = species_papers or {}
    if prior is None or len(prior) == 0:
        logger.info("taxon prior empty: ranking returned unchanged")
        return list(ranked)

    adjusted: list[tuple[CandidateRank, float, int]] = []
    for index, rank in enumerate(ranked):
        level: Literal["species", "genus", "family", "none"] = "none"
        provenance = []
        bonus = 0.0
        new_evidence: list[EvidenceItem] = []

        inchikey = _inchikey_of(rank.candidate.smiles)
        if inchikey:
            best = prior.best_level(inchikey)
            provenance = prior.provenance_for(inchikey)
            if best is not None:
                level = best
                bonus = taxon_bonus_for(
                    best, rank.candidate.smiles, solvent, estimate_logp
                )
                primary = provenance[0]
                new_evidence.append(
                    EvidenceItem(
                        source_spectrum=primary.source_database,
                        spectral_feature=(
                            f"reported from {primary.source_organism} "
                            f"({level} level, {len(provenance)} report(s))"
                        ),
                        relation="taxon_prior",
                        strength=round(min(bonus, 1.0), 4),
                    )
                )
            new_evidence.extend(_paper_evidence(species_papers.get(inchikey, [])))

        score = rank.score - bonus if rank.ranking_convention == "lower_is_better" else rank.score + bonus
        graph = rank.evidence_graph
        if new_evidence:
            if graph is None:
                graph = EvidenceGraph(
                    node_id=rank.candidate.id,
                    claim=f"identity: {rank.candidate.name}",
                    evidence=[],
                    confidence=0.0,
                )
            graph = graph.model_copy(
                update={"evidence": [*graph.evidence, *new_evidence]}
            )
        adjusted.append(
            (
                rank.model_copy(
                    update={
                        "score": round(score, 6),
                        "taxon_prior_match": level,
                        "taxon_prior_provenance": provenance,
                        "evidence_graph": graph,
                    }
                ),
                score,
                index,
            )
        )

    def _sort_key(item: tuple[CandidateRank, float, int]) -> tuple[float, int]:
        rank, score, index = item
        direction = 1.0 if rank.ranking_convention == "lower_is_better" else -1.0
        return (direction * score, index)

    adjusted.sort(key=_sort_key)
    return [rank for rank, _score, _index in adjusted]


def _default_estimate_logp(smiles: str) -> float:
    from phyto_shared.solvent_prior import estimate_logp

    return estimate_logp(smiles)
