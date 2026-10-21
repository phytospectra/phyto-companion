"""Synergy analysis: shared/complementary targets and Chou-Talalay
combination indices -- always reported as hypotheses."""

from __future__ import annotations

from collections import defaultdict

import numpy as np
from pydantic import BaseModel, ConfigDict

from phyto_shared.exceptions import InvalidInputError

from .target_prediction import TargetPrediction

__all__ = [
    "SynergyResult",
    "SynergyHypothesis",
    "predict_synergy",
    "combination_index",
]


class SynergyHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    compounds: tuple[str, str]
    shared_targets: list[str]
    complementary_pathway_targets: list[str]
    rationale: str
    is_hypothesis: bool = True


class SynergyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypotheses: list[SynergyHypothesis]
    single_target_compounds: list[str]


def predict_synergy(
    compounds: list[str],
    targets_per_compound: dict[str, list[TargetPrediction]],
) -> SynergyResult:
    """Shared-target and complementary-target synergy hypotheses.

    These are HYPOTHESES: shared targets suggest additive or synergistic
    activity; distinct targets in the same pathway suggest complementary
    action.  Neither is a conclusion.
    """
    if len(compounds) < 2:
        raise InvalidInputError(
            "synergy analysis needs at least two compounds", context={}
        )
    targets: dict[str, set[str]] = {}
    for compound in compounds:
        predictions = targets_per_compound.get(compound, [])
        targets[compound] = {p.target_id for p in predictions}
    pathway_of: dict[str, set[str]] = defaultdict(set)
    for compound in compounds:
        for prediction in targets_per_compound.get(compound, []):
            for pathway, _ in _pathways_for(prediction.target_id):
                pathway_of[pathway].add(compound)

    hypotheses: list[SynergyHypothesis] = []
    for i in range(len(compounds)):
        for j in range(i + 1, len(compounds)):
            a, b = compounds[i], compounds[j]
            shared = sorted(targets[a] & targets[b])
            complementary = sorted(
                {
                    target
                    for pathway, members in pathway_of.items()
                    if {a, b} <= members
                    for target in targets[a] | targets[b]
                    if target in _pathway_targets(pathway, targets_per_compound)
                } - set(shared)
            )
            if not shared and not complementary:
                continue
            if shared:
                rationale = (
                    f"{a} and {b} both target {', '.join(shared)}: additive "
                    "or synergistic activity is plausible"
                )
            else:
                rationale = (
                    f"{a} and {b} engage distinct targets within the same "
                    "pathways: complementary action is plausible"
                )
            hypotheses.append(
                SynergyHypothesis(
                    compounds=(a, b),
                    shared_targets=shared,
                    complementary_pathway_targets=complementary,
                    rationale=rationale,
                )
            )
    singles = [c for c in compounds if not targets.get(c)]
    return SynergyResult(hypotheses=hypotheses, single_target_compounds=singles)


def _pathways_for(target_id: str) -> list[tuple[str, str]]:
    from .pathway_mapping import MOCK_TARGET_PATHWAYS

    return MOCK_TARGET_PATHWAYS.get(target_id, [])


def _pathway_targets(
    pathway: str, targets_per_compound: dict[str, list[TargetPrediction]]
) -> set[str]:
    result: set[str] = set()
    for predictions in targets_per_compound.values():
        for prediction in predictions:
            if any(pid == pathway for pid, _ in _pathways_for(prediction.target_id)):
                result.add(prediction.target_id)
    return result


def combination_index(
    effects: list[float] | None, single_effects: list[list[float]] | None
) -> float | None:
    """Chou-Talalay combination index from dose-effect data.

    ``effects[0]`` is the effect level (fa) at which to evaluate; the
    remaining entries are the COMBINATION doses D1, D2 that achieve it;
    ``single_effects[i]`` is [fa_i, Dx_i]: the single-agent dose needed
    for that effect level.  CI = D1/Dx1 + D2/Dx2 (CI < 1 synergy,
    = 1 additivity, > 1 antagonism).  Returns None when the data does
    not support the computation.
    """
    if not effects or not single_effects or len(effects) < 3:
        return None
    if len(single_effects) < 2:
        return None
    fa, d1, d2 = effects[0], effects[1], effects[2]
    if fa <= 0 or fa >= 1:
        return None
    doses = []
    for entry in single_effects[:2]:
        try:
            fa_single, dx = entry[0], entry[1]
        except (TypeError, IndexError):
            return None
        if abs(fa_single - fa) > 1e-6 or dx <= 0:
            return None
        doses.append(dx)
    if d1 <= 0 or d2 <= 0:
        return None
    return float(d1 / doses[0] + d2 / doses[1])
