"""QA/Critic agent: verifies Tier-3 outputs before they may be persisted.

Verification is rule-based first (deterministic, auditable).  An optional
LLM judge adds a narrative-quality check, but the persistence gate ALWAYS
includes the rule-based checks -- an LLM judging an LLM can supplement
verification, never replace it.

The grounding gate (ADR-0029) is the load-bearing rule for Tier-3 output:
a response is rejected if its grounding score is below
``GROUNDING_MIN_SCORE``, if ANY factual claim lacks a citation, or if it
cites an identifier that was not in the retrieved context (a fabricated
citation).  The score is recomputed here from the text and the context
papers; a model's self-reported number is never trusted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from phyto_shared.grounding import GROUNDING_MIN_SCORE, GroundingReport, compute_grounding
from phyto_shared.literature_models import PaperHit

from .llm_client import LLMClient

__all__ = ["CheckResult", "VerificationResult", "QACriticAgent"]


@dataclass(frozen=True)
class CheckResult:
    """One named verification check."""

    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass
class VerificationResult:
    """Aggregate outcome for one Tier-3 output."""

    passed: bool
    checks: list[CheckResult] = field(default_factory=list)
    verified_by: Literal["rule_based", "llm_judge"] = "rule_based"


# Fields that hold model-GENERATED prose and are therefore subject to the
# grounding gate.  Structural fields (ids, smiles, scores) are excluded: they
# are not claims about the literature.
_GENERATED_TEXT_FIELDS = ("hypothesis", "explanation", "rationale", "summary")

# Tools whose output asserts things ABOUT THE LITERATURE and therefore must be
# backed by retrieved papers.  For these the gate fails closed when no context
# is supplied: an unverifiable literature claim is not a verified one.
#
# ``llm.explain`` is deliberately excluded.  Its evidence is a structured
# ``EvidenceGraph`` (peaks, matches, statistics), not a paper list, so there is
# no citation set to audit it against; demanding context_papers there would
# reject a correctly-grounded explanation for the wrong reason.  Tools not in
# this set are still audited when papers ARE supplied -- the exemption is about
# which evidence type is required, not about skipping the check.
_LITERATURE_GROUNDED_TOOLS = frozenset(
    {"llm.hypothesize", "llm.hypothesize_mechanism", "llm.summarize_literature"}
)


def _generated_text(dumped: dict[str, Any]) -> str:
    """Collect the generated prose from a tool output for grounding."""
    parts = [
        str(dumped[key])
        for key in _GENERATED_TEXT_FIELDS
        if isinstance(dumped.get(key), str) and str(dumped[key]).strip()
    ]
    return " ".join(parts)


def _context_from_output(dumped: dict[str, Any]) -> list[PaperHit]:
    """Recover ``context_papers`` carried on the output itself, if any.

    The engine normally passes the retrieved papers explicitly; this is the
    fallback for outputs that embed their own evidence.
    """
    raw = dumped.get("context_papers")
    if not isinstance(raw, list):
        return []
    papers: list[PaperHit] = []
    for item in raw:
        try:
            papers.append(PaperHit.model_validate(item))
        except Exception:
            continue
    return papers


class QACriticAgent:
    """Deterministic rubric checks, optionally extended by an LLM judge."""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm

    def verify(
        self,
        tool_name: str,
        output: BaseModel,
        context_papers: list[PaperHit] | None = None,
    ) -> VerificationResult:
        """Verify one Tier-3 output.

        ``context_papers`` is the retrieved evidence the model was given.
        When this tool is a grounded-generation tool and the papers are
        omitted, the rounding gate FAILS CLOSED rather than skipping the
        check: an unverifiable output is not a verified one.
        """
        checks: list[CheckResult] = self._rule_based_checks(tool_name, output)

        grounded_checks, grounded_context = self._grounding_checks(
            tool_name, output, context_papers
        )
        checks.extend(grounded_checks)

        verified_by: Literal["rule_based", "llm_judge"] = "rule_based"
        if self.llm is not None:
            checks.append(self._llm_judge_check(tool_name, output))
            verified_by = "llm_judge"

        return VerificationResult(
            passed=all(check.passed for check in checks),
            checks=checks,
            verified_by=verified_by,
        )

    # -- grounding gate ------------------------------------------------------

    def _grounding_checks(
        self,
        tool_name: str,
        output: BaseModel,
        context_papers: list[PaperHit] | None,
    ) -> tuple[list[CheckResult], GroundingReport | None]:
        """Audit generated text against the evidence it claims to rest on."""
        dumped = output.model_dump()
        text = _generated_text(dumped)
        if not text:
            return [], None

        # Recompute rather than trust any score the model or caller supplied.
        papers = context_papers or _context_from_output(dumped)
        if not papers:
            if tool_name in _LITERATURE_GROUNDED_TOOLS:
                return [
                    CheckResult(
                        name="grounding_context_present",
                        passed=False,
                        detail=(
                            "no retrieved context supplied to the QA/Critic; a "
                            "literature-grounded Tier-3 output cannot be verified "
                            "against absent evidence"
                        ),
                    )
                ], None
            # Non-literature tool (e.g. llm.explain over an evidence graph):
            # there are no papers to audit against, so the grounding gate does
            # not apply.  The rule-based rubric still runs.
            return [], None

        report = compute_grounding(text, papers)
        checks = [
            CheckResult(
                name="grounding_score_above_threshold",
                passed=report.score >= GROUNDING_MIN_SCORE,
                detail=(
                    f"grounding_score={report.score:.4f} "
                    f"(minimum {GROUNDING_MIN_SCORE})"
                ),
            ),
            CheckResult(
                name="all_claims_cited",
                passed=report.claims_uncited == 0,
                detail=(
                    f"{report.claims_cited}/{report.claims_total} claim(s) carry "
                    f"a valid citation"
                ),
            ),
            CheckResult(
                name="no_fabricated_citations",
                passed=not report.invalid_citations,
                detail=(
                    "every cited identifier is in the retrieved context"
                    if not report.invalid_citations
                    else f"citations not found in context: {report.invalid_citations}"
                ),
            ),
        ]
        return checks, report

    # -- rule-based rubric --------------------------------------------------

    def _rule_based_checks(
        self, tool_name: str, output: BaseModel
    ) -> list[CheckResult]:
        dumped = output.model_dump()
        checks: list[CheckResult] = []

        # Universal: no empty required text fields
        empty_fields = [
            key
            for key, value in dumped.items()
            if isinstance(value, str) and not value.strip()
        ]
        checks.append(
            CheckResult(
                name="no_empty_text_fields",
                passed=not empty_fields,
                detail=(
                    "all text fields populated"
                    if not empty_fields
                    else f"empty fields: {empty_fields}"
                ),
            )
        )

        # Universal: no overclaiming language in hypotheses/explanations
        text = " ".join(
            str(v) for v in dumped.values() if isinstance(v, str)
        ).lower()
        if tool_name in ("llm.hypothesize", "llm.propose_experiments"):
            overclaiming = [w for w in ("proven", "definitively shows") if w in text]
            checks.append(
                CheckResult(
                    name="no_overclaiming",
                    passed=not overclaiming,
                    detail=(
                        "hypothesis framed as hypothesis"
                        if not overclaiming
                        else f"overclaiming language: {overclaiming}"
                    ),
                )
            )

        # Tool-specific
        if tool_name == "llm.explain":
            checks.append(
                CheckResult(
                    name="explanation_present",
                    passed=len(dumped.get("explanation", "")) > 10,
                    detail="explanation longer than a stub",
                )
            )
        elif tool_name == "llm.propose_experiments":
            experiments = dumped.get("experiments", [])
            checks.append(
                CheckResult(
                    name="experiments_non_empty",
                    passed=bool(experiments),
                    detail=f"{len(experiments)} experiment(s) proposed",
                )
            )
        elif tool_name == "llm.hypothesize":
            hypothesis = dumped.get("hypothesis", "")
            framed = "hypothes" in hypothesis.lower() or "may" in hypothesis.lower()
            checks.append(
                CheckResult(
                    name="framed_as_hypothesis",
                    passed=framed and len(hypothesis) > 10,
                    detail="mechanism text explicitly framed as a hypothesis",
                )
            )

        return checks

    # -- optional LLM judge ---------------------------------------------------

    def _llm_judge_check(
        self, tool_name: str, output: BaseModel
    ) -> CheckResult:
        """LLM-as-judge narrative review; supplements, never replaces rules."""
        try:
            response = self.llm.complete(
                system_prompt=(
                    "You are a QA critic for scientific AI outputs. Judge "
                    "whether the output is coherent, evidence-grounded, and "
                    "honest about uncertainty. Reply PASS or FAIL with a reason."
                ),
                user_prompt=(
                    f"Tool: {tool_name}\nOutput: {output.model_dump_json()}"
                ),
            )
        except Exception as exc:  # judge unavailable -> fail closed
            return CheckResult(
                name="llm_judge",
                passed=False,
                detail=f"judge call failed (fail closed): {exc}",
            )
        passed = "FAIL" not in response.upper()[:200]
        return CheckResult(
            name="llm_judge",
            passed=passed,
            detail=response[:200],
        )
