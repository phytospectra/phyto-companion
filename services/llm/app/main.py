"""FastAPI entrypoint for the LLM service (Tier 3).

HARD RULE (ADR-0029): every endpoint rejects an empty ``context_papers``.
The model never answers without retrieved evidence.  The request model
deliberately does NOT declare ``min_length=1``: that would produce a generic
422, whereas the handler raises a 400 with an explanation the client can act
on.

Provider selection (ADR-0034): a request may name ``provider``
(``vertex`` | ``openrouter`` | ``local``).  Omitted means the deployment
default -- Vertex in the cloud plane, the offline mock locally.
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict

from phyto_shared.exceptions import CloudServiceError
from phyto_shared.literature_models import LLMRequest, LLMResponse
from phyto_shared.provenance import ProvenanceBuilder, sha256_dict

from . import __version__
from .clients import LLMClient, MockLLMClient, VertexAILLMClient
from .grounding import compute_grounding, extract_citations
from .openrouter_client import OpenRouterLLMClient

app = FastAPI(
    title="PhytoCompanion LLM Service",
    version=__version__,
    description="Grounded Tier-3 generation. Requires retrieved evidence for every call.",
)


def _default_provider() -> str:
    """Deployment default: Vertex in the cloud plane, the mock locally.

    ADR-0034 makes Vertex the production backbone.  Outside the cloud plane
    there is no Vertex endpoint to call, so the local default is the offline
    mock; an explicit ``provider="vertex"`` still gets the real client and
    fails loudly if it is unconfigured, rather than silently substituting.
    """
    configured = os.environ.get("PHYTO_LLM_PROVIDER")
    if configured:
        return configured.strip().lower()
    return "vertex" if os.environ.get("PHYTO_ENV") == "cloud" else "local"


def get_llm_client(provider: str | None = None) -> LLMClient:
    """Provider router (ADR-0034).

    Clients are built per call rather than cached: they hold no connection
    pool (httpx.Client is opened inside ``complete``), and caching would pin
    environment variables so a rotated key or changed model needed a restart.
    """
    resolved = (provider or _default_provider()).strip().lower()
    if resolved == "vertex":
        return VertexAILLMClient()
    if resolved == "openrouter":
        return OpenRouterLLMClient()
    if resolved == "local":
        return MockLLMClient()
    # The request model's Literal already rejects unknown values, so reaching
    # here means a bad env var or a direct programmatic call.
    raise ValueError(
        f"unknown LLM provider {resolved!r}; expected vertex, openrouter or local"
    )


class LLMServiceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response: LLMResponse
    provenance_manifest_id: str


_QUERY_TEMPLATE = {
    "hypothesize": (
        "compound: {compound}\nevidence:\n{papers}\n"
        "task: hypothesise a plausible biological mechanism consistent with the evidence."
    ),
    "explain": (
        "evidence:\n{papers}\ntask: explain what the evidence supports and what remains uncertain."
    ),
    "recommend_experiments": (
        "compound: {compound}\nevidence:\n{papers}\n"
        "task: recommend falsifiable follow-up experiments."
    ),
    "summarize_literature": (
        "evidence:\n{papers}\ntask: summarise the retrieved literature neutrally."
    ),
}


def _render_papers(papers) -> str:
    lines = []
    for paper in papers:
        identifier = paper.pmid or paper.doi or "no-id"
        lines.append(f"- [{identifier}] {paper.title} ({paper.year or 'n.d.'}): {paper.abstract}")
    return "\n".join(lines)


def _build_prompt(request: LLMRequest) -> str:
    compound = request.candidate.name if request.candidate else "the compound"
    template = _QUERY_TEMPLATE[request.operation]
    prompt = template.format(compound=compound, papers=_render_papers(request.context_papers))
    if request.question:
        prompt += f"\nquestion: {request.question}"
    return prompt


def _handle(request: LLMRequest) -> LLMServiceResponse:
    # Explicit 400 (not the Pydantic 422) so clients get an actionable message.
    if not request.context_papers:
        raise HTTPException(
            status_code=400,
            detail=(
                "context_papers must not be empty: the LLM never answers without "
                "retrieved evidence (ADR-0029). Run a literature search first."
            ),
        )

    prompt = _build_prompt(request)
    try:
        client = get_llm_client(request.provider)
        completion = client.complete(request.operation, request.context_papers, prompt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CloudServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    grounding = compute_grounding(completion.text, request.context_papers)
    cited = extract_citations(completion.text)

    response = LLMResponse(
        text=completion.text,
        citations=cited,
        grounding_score=grounding.score,
        model_id=completion.model_id,
        model_version=completion.model_version,
        tokens_in=completion.tokens_in,
        tokens_out=completion.tokens_out,
        claims_total=grounding.claims_total,
        claims_uncited=grounding.claims_uncited,
    )

    manifest_id = (
        ProvenanceBuilder()
        .with_input_hash("request", sha256_dict(request.model_dump(mode="json")))
        .with_model_version(completion.model_id, completion.model_version)
        .with_parameters(
            {
                "operation": request.operation,
                "provider": request.provider or _default_provider(),
                "context_paper_count": len(request.context_papers),
                "grounding_score": grounding.score,
                "claims_uncited": grounding.claims_uncited,
                "plane": os.environ.get("PHYTO_ENV", "local"),
            }
        )
        .with_environment()
        .build()
        .manifest_hash
    )
    return LLMServiceResponse(response=response, provenance_manifest_id=manifest_id)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "llm",
        "version": __version__,
        "plane": os.environ.get("PHYTO_ENV", "local"),
    }


@app.post("/llm/explain", response_model=LLMServiceResponse)
def llm_explain(request: LLMRequest) -> LLMServiceResponse:
    return _handle(request.model_copy(update={"operation": "explain"}))


@app.post("/llm/hypothesize", response_model=LLMServiceResponse)
def llm_hypothesize(request: LLMRequest) -> LLMServiceResponse:
    return _handle(request.model_copy(update={"operation": "hypothesize"}))


@app.post("/llm/recommend-experiments", response_model=LLMServiceResponse)
def llm_recommend_experiments(request: LLMRequest) -> LLMServiceResponse:
    return _handle(request.model_copy(update={"operation": "recommend_experiments"}))


@app.post("/llm/summarize-literature", response_model=LLMServiceResponse)
def llm_summarize_literature(request: LLMRequest) -> LLMServiceResponse:
    return _handle(request.model_copy(update={"operation": "summarize_literature"}))
