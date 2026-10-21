"""Grounding verification for Tier-3 LLM output (ADR-0029).

The platform's hard rule: an LLM must never answer without retrieved
evidence, and every factual claim it makes must be traceable to a citation
that was actually in the context.  This module implements that check
deterministically, without asking the model to grade itself.

It lives in the shared library because TWO gates need the same verdict:
the LLM service (which computes ``grounding_score`` on every response) and
the orchestrator's QA/Critic agent (which refuses to persist an output that
fails it).  A second implementation would be free to drift, and a grounding
gate that disagrees with itself is worse than none.

Two separate concepts, deliberately kept apart:

``grounding_score``
    cited-and-valid claims / total claims.
    A citation counts only if its identifier was among the supplied context
    papers.  Citing PMID 99999999 that exists nowhere is not grounding.

``claims_uncited``
    Claims with no citation at all.  The QA/Critic rejects any output with a
    single uncited claim, regardless of the aggregate score: a hypothesis
    that is 90% grounded but asserts one invented fact is not safe to
    persist.
"""

from __future__ import annotations

import re

from .literature_models import PaperHit

__all__ = [
    "extract_citations",
    "extract_claims",
    "compute_grounding",
    "GroundingReport",
    "GROUNDING_MIN_SCORE",
]


# The QA/Critic's acceptance threshold (ADR-0029).  Named here rather than in
# the orchestrator so the service and the gate cannot disagree about it.
GROUNDING_MIN_SCORE = 0.7

# PMIDs appear as "PMID: 12345", "PMID 12345", "PMID12345".
_PMID_RE = re.compile(r"\bPMID[\s:]*(\d{5,9})\b", re.IGNORECASE)
# DOIs appear as "doi:10.1000/xyz" or "https://doi.org/10.1000/xyz".
_DOI_RE = re.compile(r"(?:doi:\s*|https?://doi\.org/)(10\.\d{4,9}/[^\s,;)\]]+)", re.IGNORECASE)
# A bracketed numeric citation, e.g. "[1]" or "[2,3]" -- position-based, so it
# is resolved against the context list order, not against PMIDs.
_BRACKET_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

# Sentences that are framing rather than factual assertion.  A hypothesis
# sentence ("we hypothesise ...") still asserts something, so it is NOT in
# this list; only meta-statements and hedges that carry no content are.
_NON_CLAIM_PATTERNS = (
    re.compile(r"^(references|sources|citations)\s*[:.]", re.IGNORECASE),
    re.compile(r"^(note|disclaimer|caution)\s*[:.]", re.IGNORECASE),
)


class GroundingReport:
    """Result of a grounding audit."""

    __slots__ = (
        "claims_total",
        "claims_cited",
        "claims_uncited",
        "valid_citations",
        "invalid_citations",
        "score",
    )

    def __init__(
        self,
        claims_total: int,
        claims_cited: int,
        claims_uncited: int,
        valid_citations: list[str],
        invalid_citations: list[str],
    ) -> None:
        self.claims_total = claims_total
        self.claims_cited = claims_cited
        self.claims_uncited = claims_uncited
        self.valid_citations = valid_citations
        self.invalid_citations = invalid_citations
        self.score = round(claims_cited / claims_total, 4) if claims_total else 0.0

    def to_dict(self) -> dict:
        return {
            "claims_total": self.claims_total,
            "claims_cited": self.claims_cited,
            "claims_uncited": self.claims_uncited,
            "valid_citations": self.valid_citations,
            "invalid_citations": self.invalid_citations,
            "grounding_score": self.score,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"GroundingReport(score={self.score}, claims={self.claims_total}, "
            f"uncited={self.claims_uncited}, invalid={self.invalid_citations})"
        )


def extract_citations(text: str, context_papers: list[PaperHit] | None = None) -> list[str]:
    """Collect every citation identifier mentioned in ``text``.

    Returns identifiers as strings: ``PMID:<id>``, ``DOI:<doi>``, or
    ``REF:<n>`` for bracketed positional references.  Order is first
    appearance, duplicates collapsed.
    """
    found: list[str] = []
    seen: set[str] = set()

    def add(identifier: str) -> None:
        if identifier not in seen:
            seen.add(identifier)
            found.append(identifier)

    for match in _PMID_RE.finditer(text):
        add(f"PMID:{match.group(1)}")
    for match in _DOI_RE.finditer(text):
        add(f"DOI:{match.group(1).lower().rstrip('.')}")
    for match in _BRACKET_RE.finditer(text):
        for number in re.findall(r"\d+", match.group(1)):
            add(f"REF:{int(number)}")

    return found


def _split_sentences(text: str) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(stripped) if s.strip()]


def extract_claims(text: str) -> list[str]:
    """Heuristically split ``text`` into factual claims.

    A sentence ending in a period that contains at least three content words
    (citation identifiers excluded) is a claim.  A sentence that is nothing
    but a citation is appended to the claim before it, because models
    routinely emit the citation as its own sentence.
    """
    claims: list[str] = []
    for sentence in _split_sentences(text):
        if not sentence.endswith((".", "!", "?")):
            continue
        if any(pattern.match(sentence) for pattern in _NON_CLAIM_PATTERNS):
            continue

        without_ids = _BRACKET_RE.sub("", _DOI_RE.sub("", _PMID_RE.sub("", sentence)))
        content_words = re.findall(r"[A-Za-z0-9][A-Za-z0-9-]*", without_ids)

        if len(content_words) < 3:
            if claims and extract_citations(sentence):
                claims[-1] = f"{claims[-1]} {sentence}"
            continue

        claims.append(sentence)
    return claims


def _sentence_has_valid_citation(
    sentence: str, context_papers: list[PaperHit], ref_count: int
) -> tuple[bool, list[str], list[str]]:
    """Return (has_valid, valid_ids, invalid_ids) for one sentence."""
    identifiers = extract_citations(sentence)
    if not identifiers:
        return False, [], []

    valid_pmids = {p.pmid for p in context_papers if p.pmid}
    valid_dois = {p.doi.lower() for p in context_papers if p.doi}

    valid: list[str] = []
    invalid: list[str] = []
    for identifier in identifiers:
        kind, _, value = identifier.partition(":")
        if kind == "PMID" and value in valid_pmids:
            valid.append(identifier)
        elif kind == "DOI" and value in valid_dois:
            valid.append(identifier)
        elif kind == "REF":
            # Positional refs resolve 1-indexed against the context list.
            if 1 <= int(value) <= ref_count:
                valid.append(identifier)
            else:
                invalid.append(identifier)
        else:
            invalid.append(identifier)

    return bool(valid), valid, invalid


def compute_grounding(text: str, context_papers: list[PaperHit]) -> GroundingReport:
    """Audit ``text`` against ``context_papers``.

    A claim is grounded only if it carries at least one citation that
    resolves to a supplied context paper.  Text with no claims at all scores
    0.0 -- an empty or purely decorative response is not evidence.
    """
    claims = extract_claims(text)
    valid_all: list[str] = []
    invalid_all: list[str] = []
    cited = 0

    for claim in claims:
        has_valid, valid, invalid = _sentence_has_valid_citation(
            claim, context_papers, len(context_papers)
        )
        valid_all.extend(v for v in valid if v not in valid_all)
        invalid_all.extend(v for v in invalid if v not in invalid_all)
        if has_valid:
            cited += 1

    return GroundingReport(
        claims_total=len(claims),
        claims_cited=cited,
        claims_uncited=len(claims) - cited,
        valid_citations=valid_all,
        invalid_citations=invalid_all,
    )
