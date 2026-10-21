"""Tool-tier enforcement and human-checkpoint policy.

The honesty rules of the platform, encoded:

* Tier 1 tools are always allowed (deterministic).
* Tier 2 tools require the QA/Critic agent to be registered in the
  workflow (``verification_available``).
* Tier 3 (LLM) tools additionally require explicit human approval, and
  their OUTPUTS can never be persisted without rule-based/LLM-judge
  verification.
"""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field

from phyto_shared.constants import ToolTier
from phyto_shared.exceptions import ToolTierViolationError

from .tool_registry import ToolSpec

__all__ = [
    "HumanCheckpoint",
    "HUMAN_CHECKPOINT_DESCRIPTIONS",
    "ExecutionContext",
    "enforce_tiering",
    "enforce_output_persistence",
]


class HumanCheckpoint(StrEnum):
    """The decision points where the workflow must pause for a human."""

    BEFORE_CONFIRMED_CLAIM = "before_confirmed_claim"
    BEFORE_NOVEL_STRUCTURE_GENERATION = "before_novel_structure_generation"
    BEFORE_EXPERIMENT_RECOMMENDATION = "before_experiment_recommendation"
    BEFORE_MECHANISM_HYPOTHESIS = "before_mechanism_hypothesis"
    BEFORE_KNOWLEDGE_GRAPH_WRITE = "before_knowledge_graph_write"


HUMAN_CHECKPOINT_DESCRIPTIONS: Mapping[HumanCheckpoint, str] = MappingProxyType(
    {
        HumanCheckpoint.BEFORE_CONFIRMED_CLAIM: (
            "Pause before the platform claims a CONFIRMED (MSI Level 1) "
            "identification; a human must see the evidence first."
        ),
        HumanCheckpoint.BEFORE_NOVEL_STRUCTURE_GENERATION: (
            "Pause before generating novel structure candidates by chemical "
            "graph editing; a human must authorize the enumeration space."
        ),
        HumanCheckpoint.BEFORE_EXPERIMENT_RECOMMENDATION: (
            "Pause before recommending follow-up experiments (they cost "
            "sample, time, and money)."
        ),
        HumanCheckpoint.BEFORE_MECHANISM_HYPOTHESIS: (
            "Pause before invoking the Tier-3 LLM for a mechanism hypothesis; "
            "the human authorizes the model call and the evidence it will see."
        ),
        HumanCheckpoint.BEFORE_KNOWLEDGE_GRAPH_WRITE: (
            "Pause before writing anything to the knowledge graph; persisted "
            "knowledge outlives the analysis that produced it."
        ),
    }
)


class ExecutionContext(BaseModel):
    """The policy state one workflow run executes under.

    Frozen: policy changes (approvals) produce a NEW context via
    ``with_approval`` so the audit trail can record exactly which context
    made which decision.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(..., min_length=1)
    dry_run: bool
    verification_available: bool = Field(
        default=False,
        description="True when the QA/Critic agent is registered in the workflow",
    )
    human_in_the_loop_approved: bool = Field(
        default=False,
        description="True when a human has approved Tier-3 (LLM) tool use "
        "for this run",
    )
    approved_checkpoints: frozenset[HumanCheckpoint] = frozenset()
    actor: str = Field(default="system", description="Who triggered this context")

    def with_approval(
        self, checkpoint: HumanCheckpoint, actor: str
    ) -> "ExecutionContext":
        """Return a new context with one checkpoint approved.

        Approving a checkpoint also grants Tier-3 tool use for the run:
        the approval session IS the human authorization the tiering rules
        demand.  Every approval is audit-logged by the caller.
        """
        return self.model_copy(
            update={
                "approved_checkpoints": self.approved_checkpoints | {checkpoint},
                "human_in_the_loop_approved": True,
                "actor": actor,
            }
        )

    def checkpoint_approved(self, checkpoint: HumanCheckpoint) -> bool:
        return checkpoint in self.approved_checkpoints


def enforce_tiering(tool_spec: ToolSpec, context: ExecutionContext) -> None:
    """Raise ``ToolTierViolationError`` if this tool call is not allowed.

    Rules:
    - Tier 1 tools are always allowed.
    - Tier 2 tools require ``verification_available=True`` (the QA/Critic
      agent is registered in the workflow).
    - Tier 3 tools require ``verification_available=True`` AND
      ``human_in_the_loop_approved=True``.
    """
    if tool_spec.tier is ToolTier.TIER_1_DETERMINISTIC:
        return

    if not context.verification_available:
        raise ToolTierViolationError(
            f"Tier {int(tool_spec.tier)} tool '{tool_spec.name}' used without "
            "verification capacity: no QA/Critic agent is registered in this "
            "workflow",
            context={
                "tool": tool_spec.name,
                "tier": int(tool_spec.tier),
                "run_id": context.run_id,
                "verification_available": context.verification_available,
            },
        )

    if (
        tool_spec.tier is ToolTier.TIER_3_LLM_GENERATED
        and not context.human_in_the_loop_approved
    ):
        raise ToolTierViolationError(
            f"Tier 3 (LLM) tool '{tool_spec.name}' requires recorded "
            "human-in-the-loop approval before use",
            context={
                "tool": tool_spec.name,
                "tier": 3,
                "run_id": context.run_id,
                "human_in_the_loop_approved": context.human_in_the_loop_approved,
            },
        )


def enforce_output_persistence(tool_spec: ToolSpec, verified: bool) -> None:
    """Raise if a tool OUTPUT may not be persisted.

    Tier 3 outputs are hypotheses, never evidence: they may only be
    persisted after verification (rule-based QA checks, optionally an
    LLM judge whose verdict is itself recorded).  Tier 1/2 outputs pass
    through (Tier 2 already carries its confidence scores).
    """
    if tool_spec.tier is ToolTier.TIER_3_LLM_GENERATED and not verified:
        raise ToolTierViolationError(
            f"Tier 3 output of '{tool_spec.name}' cannot be persisted without "
            "QA/Critic verification",
            context={"tool": tool_spec.name, "tier": 3, "verified": verified},
        )
