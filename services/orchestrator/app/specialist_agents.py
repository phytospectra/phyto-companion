"""Specialist agents: deterministic (Tier 1) domain reviewers.

Each specialist reviews outputs from its domain before they move on.
The reviews are rule-based heuristics -- cheap, auditable, and honest
about what they cannot judge.  The QA/Critic agent (qa_critic_agent.py)
handles Tier-3 verification; specialists review domain quality.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from phyto_shared.models import Candidate, Spectrum

__all__ = [
    "ReviewFinding",
    "SpecialistAgent",
    "SpectroscopistAgent",
    "MetabolomistAgent",
    "ChemistAgent",
    "BiologistAgent",
]


@dataclass(frozen=True)
class ReviewFinding:
    """The outcome of one specialist review."""

    status: Literal["approve", "reject", "needs_info"]
    notes: tuple[str, ...]


class SpecialistAgent:
    """Base: names the specialist and the tools it reviews."""

    name: str = "specialist"
    expertise: str = ""
    reviews_tools: tuple[str, ...] = ()

    def review(self, *args, **kwargs) -> ReviewFinding:  # pragma: no cover
        raise NotImplementedError


class SpectroscopistAgent(SpecialistAgent):
    """Reviews spectral evidence quality (NMR/MS/IR)."""

    name = "spectroscopist"
    expertise = "NMR and MS spectral interpretation"
    reviews_tools = ("io.parse_peak_csv", "shift_mpnn.predict", "hungarian.match")

    def review_spectrum(self, spectrum: Spectrum) -> ReviewFinding:
        if not spectrum.data:
            return ReviewFinding(
                "needs_info", ("spectrum contains no peaks; check the source file",)
            )
        solvent = sum(
            1
            for peak in spectrum.data
            if getattr(peak, "solvent_flag", False)
        )
        if solvent > len(spectrum.data) // 2:
            return ReviewFinding(
                "reject",
                (
                    f"{solvent}/{len(spectrum.data)} peaks flagged as solvent; "
                    "the sample is mostly solvent signal",
                ),
            )
        return ReviewFinding(
            "approve",
            (f"{len(spectrum.data)} peaks, {solvent} solvent-flagged: acceptable",),
        )


class MetabolomistAgent(SpecialistAgent):
    """Reviews statistical outputs (group comparisons, FDR)."""

    name = "metabolomist"
    expertise = "untargeted metabolomics statistics"
    reviews_tools = ("stats.t_test", "stats.anova", "stats.fdr_correction")

    def review_statistics(
        self, p_values: list[float], fdr_applied: bool
    ) -> ReviewFinding:
        invalid = [p for p in p_values if not 0.0 <= p <= 1.0]
        if invalid:
            return ReviewFinding(
                "reject", (f"p-values outside [0,1]: {invalid[:3]}",)
            )
        if len(p_values) > 1 and not fdr_applied:
            return ReviewFinding(
                "needs_info",
                ("multiple testing without FDR correction; run stats.fdr_correction",),
            )
        return ReviewFinding(
            "approve",
            (f"{len(p_values)} p-values, FDR applied: acceptable",),
        )


class ChemistAgent(SpecialistAgent):
    """Reviews chemical plausibility of candidates."""

    name = "chemist"
    expertise = "structure plausibility and formula consistency"
    reviews_tools = ("rdkit.formula", "formula.exact_mass", "sirius.predict_formula")

    def review_candidate(self, candidate: Candidate) -> ReviewFinding:
        computed_mass = candidate.formula.exact_mass()
        if abs(computed_mass - candidate.exact_mass) > 0.05:
            return ReviewFinding(
                "reject",
                (
                    f"exact_mass {candidate.exact_mass:.6f} inconsistent with "
                    f"formula ({computed_mass:.6f})",
                ),
            )
        rdbe = candidate.formula.rdbe()
        if rdbe < 0:
            return ReviewFinding(
                "needs_info",
                (f"implausible RDBE {rdbe:.1f}: check the formula",),
            )
        return ReviewFinding(
            "approve", (f"mass consistent; RDBE {rdbe:.1f}: plausible",)
        )


class BiologistAgent(SpecialistAgent):
    """Reviews biological hypotheses: framing, not truth."""

    name = "biologist"
    expertise = "network pharmacology and mechanism hypotheses"
    reviews_tools = ("llm.hypothesize_mechanism", "llm.propose_experiments")

    def review_hypothesis(self, text: str) -> ReviewFinding:
        lowered = text.lower()
        if "proven" in lowered or "established mechanism" in lowered:
            return ReviewFinding(
                "reject",
                ("hypothesis overclaims: mechanisms are never proven by "
                 "in silico analysis",),
            )
        if "hypothes" not in lowered and "may" not in lowered:
            return ReviewFinding(
                "needs_info",
                ("rephrase as an explicit hypothesis (hedged language)",),
            )
        return ReviewFinding(
            "approve",
            ("framed as a hypothesis; experimental validation still required",),
        )
