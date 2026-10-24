"""LLM clients: Vertex AI hosted Qwen 2.5 7B (+QLoRA adapter), or a mock.

Split-plane rule: the local plane never imports the Google SDK at module
load.  ``VertexAILLMClient.complete`` imports lazily and raises
``CloudServiceError`` when the SDK or credentials are absent, so the failure
is explicit at call time instead of at import time.

The mock is not a random text generator: it returns a deterministic,
CITATION-BEARING response built from the context papers it is given, which is
what makes the grounding pipeline testable offline.  A mock that returned
uncited prose would make every test of grounding vacuous.
"""

from __future__ import annotations

import os
import re
from typing import Protocol

from phyto_shared.exceptions import CloudServiceError
from phyto_shared.literature_models import LLMOperation, PaperHit

__all__ = [
    "LLMClient",
    "LLMCompletion",
    "MockLLMClient",
    "VertexAILLMClient",
    "system_instruction",
    "build_llm_client",
]


class LLMCompletion:
    """Raw completion plus token accounting."""

    __slots__ = ("text", "tokens_in", "tokens_out", "model_id", "model_version")

    def __init__(
        self,
        text: str,
        tokens_in: int,
        tokens_out: int,
        model_id: str,
        model_version: str,
    ) -> None:
        self.text = text
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.model_id = model_id
        self.model_version = model_version

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"LLMCompletion(model={self.model_id!r}, tokens_in={self.tokens_in})"


class LLMClient(Protocol):
    """Uniform interface for the mocked and hosted models."""

    def complete(
        self,
        operation: LLMOperation,
        context_papers: list[PaperHit],
        prompt: str,
    ) -> LLMCompletion: ...


def _estimate_tokens(text: str) -> int:
    """Rough token count: ~4 characters per token.

    Used where the provider does not return usage.  Deliberately an estimate
    and labelled as such downstream; billing must never depend on it.
    """
    return max(1, len(text) // 4)


def _citation_for(paper: PaperHit) -> str:
    """Citation string for one context paper, preferring PMID."""
    if paper.pmid:
        return f"PMID: {paper.pmid}"
    if paper.doi:
        return f"doi: {paper.doi}"
    return "reference unavailable"


def system_instruction(operation: LLMOperation) -> str:
    """Shared grounding instruction for every hosted provider.

    Defined once so Vertex and OpenRouter cannot drift into different
    citation contracts; the grounding gate accepts both PMID and DOI forms.
    """
    base = (
        "You are a scientific assistant for natural product research. "
        "Answer ONLY using the supplied context. Every factual sentence MUST "
        "end with a citation in the form 'PMID: <id>' or 'doi: <doi>' taken "
        "from the context. If the context does not support a statement, say so "
        "instead of asserting it."
    )
    extra = {
        "hypothesize": " Frame mechanisms explicitly as hypotheses to be tested.",
        "explain": " Explain the evidence and state what remains uncertain.",
        "recommend_experiments": " Recommend concrete, falsifiable experiments.",
        "summarize_literature": " Summarise the retrieved literature neutrally.",
    }[operation]
    return base + extra


class MockLLMClient:
    """Deterministic, offline stand-in for the fine-tuned endpoint.

    For ``hypothesize`` it emits a hypothesis naming the compound and citing
    two context papers, so the grounding score is computable and non-trivial.
    Other operations emit a grounded summary of the supplied papers.
    """

    model_id = "mock-qwen2.5-7b-instruct"
    model_version = "mock-1.0.0"

    def complete(
        self,
        operation: LLMOperation,
        context_papers: list[PaperHit],
        prompt: str,
    ) -> LLMCompletion:
        text = self._render(operation, context_papers, prompt)
        return LLMCompletion(
            text=text,
            tokens_in=_estimate_tokens(prompt) + sum(
                _estimate_tokens(p.title + p.abstract) for p in context_papers
            ),
            tokens_out=_estimate_tokens(text),
            model_id=self.model_id,
            model_version=self.model_version,
        )

    def _render(
        self,
        operation: LLMOperation,
        papers: list[PaperHit],
        prompt: str,
    ) -> str:
        compound = self._extract_compound(prompt)
        first = papers[0]
        second = papers[1] if len(papers) > 1 else papers[0]

        # Every sentence below is a factual claim, so every one carries a
        # citation. The framing caveat is folded into a cited sentence rather
        # than left standing alone, because an uncited closing sentence is
        # exactly what the grounding gate is designed to reject.
        if operation == "hypothesize":
            return (
                f"We hypothesise that {compound} may act through modulation of "
                f"the pathway reported for {first.title}. {_citation_for(first)}. "
                "Supporting mechanistic evidence indicates cytokine signalling is "
                f"affected in a dose-dependent manner. {_citation_for(second)}. "
                "This mechanism remains a hypothesis requiring experimental "
                f"confirmation and is not an established conclusion. {_citation_for(first)}."
            )

        if operation == "explain":
            return (
                f"The identification is supported by {len(papers)} retrieved "
                f"record(s), and {first.title} reports the relevant spectroscopic "
                f"behaviour. {_citation_for(first)}. This evidence remains "
                "provisional pending an authentic-standard comparison. "
                f"{_citation_for(first)}."
            )

        if operation == "recommend_experiments":
            return (
                f"Acquire an authentic standard of {compound} and compare "
                f"retention time and MS/MS. {_citation_for(first)}. "
                f"Run HMBC to constrain long-range correlations described by "
                f"{second.title}. {_citation_for(second)}."
            )

        # summarize_literature
        parts = [
            f"Across {len(papers)} retrieved records, the reported findings converge "
            f"on the bioactivity summarised in {first.title}. {_citation_for(first)}."
        ]
        for paper in papers[1:3]:
            parts.append(
                f"{paper.title} provides supporting context. {_citation_for(paper)}."
            )
        return " ".join(parts)

    @staticmethod
    def _extract_compound(prompt: str) -> str:
        """Pull a compound name out of the prompt, or fall back to 'the compound'."""
        patterns = (
            r"compound[:\s]+([A-Za-z][A-Za-z0-9\-\s]{2,40})",
            r"for ([A-Za-z][A-Za-z0-9\-]{2,30})",
        )
        for pattern in patterns:
            match = re.search(pattern, prompt, re.IGNORECASE)
            if match:
                return match.group(1).strip().split("\n")[0][:40]
        return "the compound"


class VertexAILLMClient:
    """Client for a Vertex AI endpoint hosting Qwen 2.5 7B + QLoRA adapter.

    All Google SDK imports are deferred to ``complete()``.  ``model_version``
    is supplied by the caller (the adapter revision), because the endpoint id
    alone does not identify which adapter weights are live.
    """

    def __init__(
        self,
        endpoint_id: str | None = None,
        project: str | None = None,
        location: str = "us-central1",
        model_version: str = "qlora-unknown",
        model_id: str = "qwen2.5-7b-instruct",
    ) -> None:
        self.endpoint_id = endpoint_id or os.environ.get("VERTEX_ENDPOINT_ID")
        self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.location = location or os.environ.get("VERTEX_LOCATION", "us-central1")
        self.model_version = model_version
        self.model_id = model_id

    def complete(
        self,
        operation: LLMOperation,
        context_papers: list[PaperHit],
        prompt: str,
    ) -> LLMCompletion:
        if not self.endpoint_id or not self.project:
            raise CloudServiceError(
                "Vertex AI endpoint not configured: set VERTEX_ENDPOINT_ID and "
                "GOOGLE_CLOUD_PROJECT (the model must be deployed separately)"
            )
        try:
            from google.cloud import aiplatform  # deferred: cloud plane only
        except ImportError as exc:
            raise CloudServiceError(
                "google-cloud-aiplatform is not installed; the local plane must "
                "use MockLLMClient (split-plane rule)"
            ) from exc

        instruction = system_instruction(operation)
        full_prompt = f"{instruction}\n\nCONTEXT:\n{prompt}"
        try:
            aiplatform.init(project=self.project, location=self.location)
            endpoint = aiplatform.Endpoint(self.endpoint_id)
            response = endpoint.predict(instances=[{"prompt": full_prompt}])
        except Exception as exc:
            raise CloudServiceError(
                "Vertex AI prediction failed", context={"error": str(exc)}
            ) from exc

        prediction = response.predictions[0]
        if isinstance(prediction, dict):
            text = str(prediction.get("text") or prediction.get("generated_text") or "")
        else:
            text = str(prediction)
        if not text.strip():
            raise CloudServiceError("Vertex AI returned an empty completion")

        return LLMCompletion(
            text=text,
            tokens_in=_estimate_tokens(full_prompt),
            tokens_out=_estimate_tokens(text),
            model_id=self.model_id,
            model_version=self.model_version,
        )

    @staticmethod
    def _system_instruction(operation: LLMOperation) -> str:
        return system_instruction(operation)


def build_llm_client(provider: str | None = None) -> LLMClient:
    """Return a client for ``provider``, defaulting by plane (ADR-0034).

    Retained for callers that construct the client directly; the FastAPI
    service routes through ``main.get_llm_client``.
    """
    if provider is None:
        provider = "vertex" if os.environ.get("PHYTO_ENV") == "cloud" else "local"
    provider = provider.strip().lower()
    if provider == "vertex":
        return VertexAILLMClient()
    if provider == "openrouter":
        # Imported here to keep the http dependency out of the module import
        # path for callers that only use the mock or Vertex.
        from .openrouter_client import OpenRouterLLMClient

        return OpenRouterLLMClient()
    if provider == "local":
        return MockLLMClient()
    raise ValueError(
        f"unknown LLM provider {provider!r}; expected vertex, openrouter or local"
    )
