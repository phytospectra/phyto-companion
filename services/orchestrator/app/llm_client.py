"""LLM access: Vertex AI endpoint in the cloud plane, deterministic mock locally.

The local plane NEVER loads the Google SDK at import time (8 GB MacBook,
split-plane rules): ``VertexAILLMClient.complete`` imports lazily and
raises ``CloudServiceError`` if the SDK or credentials are absent.
"""

from __future__ import annotations

from typing import Literal, Protocol

from phyto_shared.exceptions import CloudServiceError

__all__ = ["LLMClient", "MockLLMClient", "VertexAILLMClient", "get_llm_client"]


class LLMClient(Protocol):
    """Minimal chat interface used by Tier 3 tools and the QA critic.

    ``model_identity`` is a short, non-secret description of the judge
    (e.g. endpoint id or model slug) so any judge-written verdict can be
    attributed to a specific model -- the QA critic records it whenever
    ``verified_by == "llm_judge"`` (ADR-0080).
    """

    def complete(self, system_prompt: str, user_prompt: str) -> str: ...

    @property
    def model_identity(self) -> str: ...


class MockLLMClient:
    """Deterministic, offline stand-in for the fine-tuned endpoint.

    Its output is always marked as mock and always reminds the caller it
    is Tier 3 material requiring QA/Critic verification.
    """

    @property
    def model_identity(self) -> str:
        return "mock-llm-client:deterministic"

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        excerpt = " ".join(user_prompt.split())[:200]
        return (
            f"[MOCK-LLM] (system: {system_prompt[:60]}...) "
            f"Draft response to: '{excerpt}'. "
            "This is a Tier-3 hypothesis requiring QA/Critic verification "
            "before it may be persisted."
        )


class VertexAILLMClient:
    """Client for the fine-tuned open-source LLM on a Vertex AI endpoint.

    Configured in the cloud plane (L4 GPU).  All Google SDK imports are
    deferred to ``complete()`` so importing this module locally is free.
    """

    def __init__(
        self,
        endpoint_id: str,
        project: str,
        location: str = "us-central1",
    ) -> None:
        self.endpoint_id = endpoint_id
        self.project = project
        self.location = location

    @property
    def model_identity(self) -> str:
        return f"vertex-endpoint:{self.endpoint_id}"

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        try:
            from google.cloud import aiplatform  # deferred: cloud plane only
        except ImportError as exc:
            raise CloudServiceError(
                "google-cloud-aiplatform is not installed; the local plane "
                "must use MockLLMClient (split-plane rule)",
                context={"endpoint_id": self.endpoint_id},
            ) from exc
        try:
            aiplatform.init(project=self.project, location=self.location)
            endpoint = aiplatform.Endpoint(self.endpoint_id)
            response = endpoint.predict(
                instances=[
                    {
                        "system_prompt": system_prompt,
                        "user_prompt": user_prompt,
                    }
                ]
            )
        except Exception as exc:  # SDK raises many error types
            raise CloudServiceError(
                "Vertex AI endpoint call failed",
                context={
                    "endpoint_id": self.endpoint_id,
                    "error": str(exc),
                },
            ) from exc
        if not response.predictions:
            raise CloudServiceError(
                "Vertex AI endpoint returned no predictions",
                context={"endpoint_id": self.endpoint_id},
            )
        return str(response.predictions[0])


def get_llm_client(
    environment: Literal["local", "cloud"] = "local",
    **kwargs,
) -> LLMClient:
    """Factory: mock on the local plane, Vertex AI in the cloud plane."""
    if environment == "local":
        return MockLLMClient()
    required = ("endpoint_id", "project")
    missing = [name for name in required if not kwargs.get(name)]
    if missing:
        raise CloudServiceError(
            "Cloud LLM client requires endpoint configuration",
            context={"missing": missing},
        )
    return VertexAILLMClient(**{
        key: kwargs[key] for key in ("endpoint_id", "project", "location")
        if key in kwargs
    })
