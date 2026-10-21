"""Site localization: where does the novel structure differ from its parent?

For every atom of the parent that could host the observed edit, simulate
the edit, predict its shifts, and score it against the full observed
spectrum.  Sites are ranked by score improvement over the parent
baseline; sites within ``rank_gap_threshold`` of the top are reported as
COMPETING -- a localization with many competing sites is honestly
uninformative, and the report says so via ``n_possible_sites``.

Benchmarking mode: when the true edit site is known (SUMMIT v4 Part B
notebook, "site localization against chance"), ``rank_of_true_site``
records where the truth landed.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from phyto_shared.models import Candidate, EvidenceItem

from .candidate_ranker import ObservedEvidence, RankingConfig, score_molecule
from .hungarian_matcher import MatchResult
from .novel_structure_generator import NovelCandidate, apply_edit_at
from .shift_predictor import ShiftPredictor, get_shift_predictor

__all__ = ["SiteLocalization", "localize_site"]


class SiteLocalization(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_smiles: str
    novel_smiles: str
    edit_atom_index: int
    edit_type: str
    localization_confidence: float = Field(..., ge=0.0, le=1.0)
    supporting_evidence: list[EvidenceItem] = Field(default_factory=list)
    competing_sites: list[int] = Field(default_factory=list)
    n_possible_sites: int = Field(..., ge=0)
    rank_of_true_site: int | None = Field(default=None, ge=1)
    notes: list[str] = Field(default_factory=list)


def localize_site(
    observed: ObservedEvidence,
    parent_candidate: Candidate,
    parent_match: MatchResult,
    novel_candidate: NovelCandidate,
    predictor: ShiftPredictor | None = None,
    config: RankingConfig | None = None,
    rank_gap_threshold: float = 0.10,
    true_edit_site: int | None = None,
) -> SiteLocalization:
    """Localize the edit site of ``novel_candidate`` within its parent."""
    predictor = predictor or get_shift_predictor()
    config = config or RankingConfig()
    edit_type = novel_candidate.edit_type

    from .novel_structure_generator import _sites_for
    from rdkit import Chem

    parent_mol = Chem.MolFromSmiles(parent_candidate.smiles)
    if parent_mol is None:
        from phyto_shared.exceptions import InvalidInputError

        raise InvalidInputError(
            "cannot parse parent SMILES", context={"smiles": parent_candidate.smiles}
        )
    sites = _sites_for(parent_mol, edit_type)
    if not sites:
        return SiteLocalization(
            parent_smiles=parent_candidate.smiles,
            novel_smiles=novel_candidate.generated_smiles,
            edit_atom_index=-1,
            edit_type=edit_type,
            localization_confidence=0.0,
            n_possible_sites=0,
            notes=["no site in the parent can host this edit type"],
        )

    baseline = score_molecule(
        observed, parent_candidate.smiles, config, predictor,
        exact_mass=parent_candidate.exact_mass,
    )[0]

    scored: list[tuple[float, int, str]] = []
    for site in sites:
        product_smiles = apply_edit_at(parent_candidate.smiles, edit_type, site)
        if product_smiles is None:
            continue  # invalid intermediate at this site
        try:
            score, _ = score_molecule(observed, product_smiles, config, predictor)
        except Exception:
            continue  # e.g. predicted views empty for this modality mix
        scored.append((baseline - score, site, product_smiles))
    if not scored:
        return SiteLocalization(
            parent_smiles=parent_candidate.smiles,
            novel_smiles=novel_candidate.generated_smiles,
            edit_atom_index=-1,
            edit_type=edit_type,
            localization_confidence=0.0,
            n_possible_sites=len(sites),
            notes=["no simulated site produced a scoreable structure"],
        )

    scored.sort(key=lambda item: (-item[0], item[1]))
    best_improvement, best_site, best_smiles = scored[0]
    competing = [
        site for improvement, site, _ in scored
        if best_improvement - improvement <= rank_gap_threshold
    ]
    confidence = round(1.0 / len(competing), 4)

    rank_of_true = None
    if true_edit_site is not None:
        for rank_position, (_, site, _) in enumerate(scored, start=1):
            if site == true_edit_site:
                rank_of_true = rank_position
                break

    notes = [
        f"baseline parent score {baseline:.4f}; best site improves by "
        f"{best_improvement:.4f}"
    ]
    if len(competing) == 1:
        notes.append("single unambiguous site")
    else:
        notes.append(
            f"{len(competing)} sites within the gap threshold: localization "
            "is not site-resolved"
        )
    if len(sites) == 1:
        notes.append(
            "n_possible_sites=1: the localization is trivially uninformative"
        )

    return SiteLocalization(
        parent_smiles=parent_candidate.smiles,
        novel_smiles=best_smiles,
        edit_atom_index=best_site,
        edit_type=edit_type,
        localization_confidence=confidence,
        supporting_evidence=[
            EvidenceItem(
                source_spectrum="observed:all",
                spectral_feature=f"simulated {edit_type} at atom {best_site}",
                relation="site_improvement",
                strength=round(max(0.0, min(best_improvement, 1.0)), 4),
            )
        ],
        competing_sites=sorted(competing),
        n_possible_sites=len(sites),
        rank_of_true_site=rank_of_true,
        notes=notes,
    )
