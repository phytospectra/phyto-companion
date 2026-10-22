"""Isolation-aware post-ranking reranker (ADR-0055).

Additive by contract, exactly like ``taxonomy_reranker``: this module
NEVER touches ``candidate_ranker``'s scoring logic.  It consumes the
ranked list the ranker produced plus ONE compound's bench isolation
record (fractions, TLC, yield, flash-UV) and adjusts each candidate's
score with soft signals derived from the record:

    polarity: candidate's predicted tier (logP + glycoside SMARTS)
              vs the tier estimated from the fractions it eluted in
                  match (same or adjacent tier):  +0.15 (scaled by the
                                                  fraction evidence's
                                                  confidence)
                  contradiction (>= 2 tiers apart): -0.25
    class:    candidate's SMARTS-predicted class vs the TLC-derived
              class list (``isolation_prior.class_from_tlc``)
                  predicted class in the list:    +0.20
                  list non-empty, no overlap:     -0.30
    flash UV: recorded lambda-max vs the candidate's chromophore band
                  inside the band:                +0.15
                  outside the band:               -0.30

Every signal that fires attaches one ``EvidenceItem`` (relation
``isolation_polarity`` / ``isolation_tlc_class`` / ``isolation_flash_uv``),
and the net adjustment is kept separately in ``CandidateRank.isolation_bonus``
so a reader can always undo it.  These are PRIORS, not measurements:
a bonus never certifies and a penalty never excludes --
``candidate_ranker`` already excluded what needed excluding (ADR-0055).

Scores keep their ``ranking_convention`` (bonus subtracts from a
``lower_is_better`` distance, adds to a ``higher_is_better``
similarity) and the output is stably sorted: candidates with equal
adjusted scores keep their input order.

Structure prediction heuristics (all documented, none authoritative):

* polarity tiers come from Crippen logP with thresholds calibrated on
  common natural products (caffeine ~ -1, quercetin ~ +2, panduratin A
  ~ +4, beta-sitosterol ~ +7.8), then floored by glycoside count: one
  pyranose pins the candidate at "polar" or higher, two at
  "very_polar" -- sugars dominate partition behaviour (ADR-0055);
* "alkaloid" means a ring nitrogen; flavone/flavonol/flavanone are a
  chromen-4-one core (flavonol = the core plus a 3-OH); sterol/
  triterpene mean >= 4 fused carbocyclic rings (a sterol additionally
  has an OH); a bare fused triple ring reads as "terpene";
* chromophore bands are coarse literature windows (flavonol band I
  340-390 nm, flavone 310-350, flavanone 270-330, simple aromatics
  250-290); a candidate whose class has no band (acyclic terpene,
  fatty acid) is NOT cross-checked -- absence of a prediction is a
  skip, not a contradiction.

Prediction runs AFTER the spectral ranking and BEFORE the taxon prior
reranker when both are active: bench isolation of THIS sample is more
specific than what the species is generally reported to contain.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from phyto_shared.isolation import (
    POLARITY_TIERS,
    Fraction,
    IsolationCompound,
    PolarityTier,
)
from phyto_shared.isolation_prior import (
    PolarityEstimate,
    class_from_tlc,
    polarity_from_fractions,
)
from phyto_shared.exceptions import InvalidInputError
from phyto_shared.models import CandidateRank, EvidenceGraph, EvidenceItem

__all__ = [
    "POLARITY_MATCH_BONUS",
    "POLARITY_CONTRADICTION",
    "TLC_MATCH_BONUS",
    "TLC_CONTRADICTION",
    "UV_MATCH_BONUS",
    "UV_CONTRADICTION",
    "CHROMOPHORE_BANDS",
    "predicted_classes",
    "predicted_polarity_tier",
    "rerank_with_isolation",
]

logger = logging.getLogger(__name__)

POLARITY_MATCH_BONUS: float = 0.15
POLARITY_CONTRADICTION: float = -0.25
TLC_MATCH_BONUS: float = 0.20
TLC_CONTRADICTION: float = -0.30
UV_MATCH_BONUS: float = 0.15
UV_CONTRADICTION: float = -0.30

#: Lambda-max windows (nm) per chromophore class; the FIRST class in
#: key order that the candidate predicts wins (flavonol before flavone
#: before flavanone before plain aromatic).
CHROMOPHORE_BANDS: dict[str, tuple[float, float]] = {
    "flavonol": (340.0, 390.0),
    "flavone": (310.0, 350.0),
    "flavanone": (270.0, 330.0),
    "aromatic": (250.0, 290.0),
}

# logP tier boundaries; calibrated on common natural products (see
# module docstring) and intentionally coarse -- one log unit is about
# the honest resolution of Crippen predictions.
_LOGP_TIERS: tuple[float, ...] = (-1.0, 1.5, 3.5, 5.5)


@lru_cache(maxsize=1)
def _patterns() -> dict:
    """Compiled SMARTS, built once (RDKit import stays lazy)."""
    from rdkit import Chem

    return {
        "flavonol": Chem.MolFromSmarts("O=c1c(O)c(-c2ccccc2)oc2ccccc12"),
        "flavone": Chem.MolFromSmarts("O=c1cc(-c2ccccc2)oc2ccccc12"),
        "flavanone": Chem.MolFromSmarts("O=C1CC(c2ccccc2)Oc2ccccc12"),
        "ring_n": Chem.MolFromSmarts("[#7;R]"),
        "quaternary_n": Chem.MolFromSmarts("[N+]"),
        "phenolic_oh": Chem.MolFromSmarts("[OX2H]c"),
        "any_oh": Chem.MolFromSmarts("[OX2H]"),
        "pyranose": Chem.MolFromSmarts("[#6R]1[#6R][#6R][#6R][#6R][OX2R]1"),
    }


@lru_cache(maxsize=4096)
def _mol(smiles: str):
    from rdkit import Chem

    return Chem.MolFromSmiles(smiles)


def _fused_carbocyclic_rings(mol) -> int:
    """Ring count of the largest fused all-carbon ring system.

    Steroid/triterpene nuclei are fused carbocycles; counting RINGS in
    the system (4 for a steroid, 5 for an ursane/oleanane) is robust
    to substituents that atom counting is not.
    """
    ring_info = mol.GetRingInfo()
    carbon_rings = [
        frozenset(idx for idx in ring if mol.GetAtomWithIdx(idx).GetAtomicNum() == 6)
        for ring in ring_info.AtomRings()
        if all(mol.GetAtomWithIdx(idx).GetAtomicNum() == 6 for idx in ring)
    ]
    systems: list[frozenset[int]] = []
    for ring in carbon_rings:
        merged: frozenset[int] | None = None
        for existing in systems[:]:
            if existing & ring:
                merged = (existing | ring) if merged is None else (merged | existing)
                systems.remove(existing)
        systems.append(merged if merged is not None else ring)
    return max((len(s) for s in systems), default=0)


def predicted_classes(smiles: str) -> list[str]:
    """Compound classes predicted from the structure, in the same
    vocabulary ``class_from_tlc`` emits, so the two can be intersected.

    An unparseable SMILES returns ``[]`` -- no prediction, so no
    signal, never a contradiction (ADR-0055 honesty rule).
    """
    mol = _mol(smiles)
    if mol is None:
        return []
    p = _patterns()
    classes: set[str] = set()
    if mol.HasSubstructMatch(p["flavonol"]):
        classes |= {"flavonol", "flavonoid"}
    elif mol.HasSubstructMatch(p["flavone"]):
        classes |= {"flavone", "flavonoid"}
    if mol.HasSubstructMatch(p["flavanone"]):
        classes |= {"flavanone", "flavonoid"}
    if mol.HasSubstructMatch(p["ring_n"]):
        classes.add("alkaloid")
    fused = _fused_carbocyclic_rings(mol)
    if fused >= 4:
        classes.add("triterpene")
        if fused == 4 and mol.HasSubstructMatch(p["any_oh"]):
            # 4 fused rings + OH = the steroid-with-OH the TLC
            # Liebermann-Burchard test calls a "sterol".
            classes.add("sterol")
    elif fused == 3:
        classes.add("terpene")
    if mol.HasSubstructMatch(p["phenolic_oh"]):
        classes.add("phenolic")
    if any(atom.GetIsAromatic() for atom in mol.GetAtoms()):
        classes.add("aromatic")
    return sorted(classes)


def predicted_polarity_tier(smiles: str) -> PolarityTier | None:
    """Polarity tier predicted from logP, floored by glycoside count.

    Returns None for an unparseable SMILES or a logP that cannot be
    computed: no prediction, no signal.
    """
    mol = _mol(smiles)
    if mol is None:
        return None
    try:
        from phyto_shared.solvent_prior import estimate_logp

        logp = estimate_logp(smiles)
    except InvalidInputError:
        return None
    p = _patterns()
    if mol.HasSubstructMatch(p["quaternary_n"]):
        # A permanent cation partitions with the aqueous end, always.
        return "very_polar"
    # POLARITY_TIERS runs least-to-most polar, so the index DESCENDS
    # with logP: logP < -1 -> very_polar (index 4), logP >= 5.5 ->
    # very_nonpolar (index 0).
    tier_index = 4 - sum(1 for cut in _LOGP_TIERS if logp >= cut)
    n_sugars = len(mol.GetSubstructMatches(p["pyranose"]))
    if n_sugars >= 2:
        tier_index = max(tier_index, 4)
    elif n_sugars == 1:
        tier_index = max(tier_index, 3)
    return POLARITY_TIERS[tier_index]  # type: ignore[index]


def _chromophore_band(classes: list[str]) -> tuple[str, float, float] | None:
    for cls in CHROMOPHORE_BANDS:
        if cls in classes:
            lo, hi = CHROMOPHORE_BANDS[cls]
            return cls, lo, hi
    return None


def _evidence(
    relation: str, source: str, feature: str, delta: float
) -> EvidenceItem:
    return EvidenceItem(
        source_spectrum=source,
        spectral_feature=feature,
        relation=relation,
        strength=round(min(abs(delta), 1.0), 4),
    )


def rerank_with_isolation(
    ranked: list[CandidateRank],
    compound_record: IsolationCompound | None,
    fraction_registry: dict[str, Fraction],
) -> list[CandidateRank]:
    """Rerank a ranked list against one compound's bench isolation record.

    A ``None`` record is a no-op returning the input untouched (the
    identify workflow's "isolation layer skipped: no record" case).
    Fraction ids the registry does not know skip the polarity signal
    with a logged warning rather than poisoning the whole rerank; the
    TLC and UV signals still run -- they do not depend on the registry.
    """
    if compound_record is None or not ranked:
        return list(ranked)

    tlc_classes = class_from_tlc(compound_record.tlc)
    polarity: PolarityEstimate | None = None
    try:
        polarity = polarity_from_fractions(
            compound_record.present_in_fractions, fraction_registry
        )
    except InvalidInputError as exc:
        logger.warning("polarity prior skipped: %s", exc)

    adjusted: list[tuple[CandidateRank, float, int]] = []
    for index, rank in enumerate(ranked):
        smiles = rank.candidate.smiles
        classes = predicted_classes(smiles)
        tier = predicted_polarity_tier(smiles)
        new_evidence: list[EvidenceItem] = []
        bonus = 0.0

        # -- polarity vs the fractions the compound eluted in ----------
        if polarity is not None and polarity.tier is not None and tier is not None:
            distance = abs(
                POLARITY_TIERS.index(tier) - POLARITY_TIERS.index(polarity.tier)  # type: ignore[arg-type]
            )
            if distance <= 1:
                delta = POLARITY_MATCH_BONUS * polarity.confidence
                wording = "consistent with"
            else:
                delta = POLARITY_CONTRADICTION
                wording = "contradicts"
            bonus += delta
            new_evidence.append(
                _evidence(
                    "isolation_polarity",
                    "isolation:fractions",
                    f"elutes in {', '.join(compound_record.present_in_fractions)} "
                    f"({polarity.basis}) -> {polarity.tier}; candidate predicted "
                    f"{tier} -- {wording}",
                    delta,
                )
            )

        # -- SMARTS-predicted class vs the TLC reagent responses --------
        # Bare UV254 "aromatic" is not diagnostic (every natural product
        # absorbs): overlap must be on a reagent-derived class, and only
        # when the plate showed NONE of them may UV254 carry the match.
        if tlc_classes:
            diagnostic = [c for c in tlc_classes if c != "aromatic"]
            pool = diagnostic or tlc_classes
            overlap = [c for c in pool if c in classes]
            if overlap:
                delta = TLC_MATCH_BONUS
                feature = (
                    f"TLC classes {tlc_classes}; candidate predicts "
                    f"{overlap} (of {classes})"
                )
            elif classes:
                # An empty prediction means "could not parse", not
                # "no class": only a real prediction may contradict.
                delta = TLC_CONTRADICTION
                feature = (
                    f"TLC classes {tlc_classes}; candidate predicts only "
                    f"{classes} -- no overlap"
                )
            else:
                delta = 0.0
            if delta != 0.0:
                bonus += delta
                new_evidence.append(
                    _evidence(
                        "isolation_tlc_class", "isolation:TLC", feature, delta
                    )
                )

        # -- flash UV lambda-max vs the candidate's chromophore ---------
        lambda_max = compound_record.flash_uv_lambda_max_nm
        band = _chromophore_band(classes)
        if lambda_max is not None and band is not None:
            cls, lo, hi = band
            if lo <= lambda_max <= hi:
                delta = UV_MATCH_BONUS
                wording = "inside"
            else:
                delta = UV_CONTRADICTION
                wording = "outside"
            bonus += delta
            new_evidence.append(
                _evidence(
                    "isolation_flash_uv",
                    "isolation:flash_uv",
                    f"flash lambda-max {lambda_max} nm {wording} the candidate's "
                    f"{cls} chromophore band {lo:.0f}-{hi:.0f} nm",
                    delta,
                )
            )

        score = (
            rank.score - bonus
            if rank.ranking_convention == "lower_is_better"
            else rank.score + bonus
        )
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
                        "isolation_bonus": round(bonus, 6),
                        "isolation_evidence": new_evidence,
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
