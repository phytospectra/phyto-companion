"""OpenRouter client: OpenAI-compatible chat completions (ADR-0034).

OpenRouter is the flexible/experimentation provider.  It speaks the OpenAI
chat-completions shape, so the client is a thin translation layer over
``httpx`` -- no vendor SDK, which keeps the local plane free of an extra
dependency and makes the whole client testable with ``httpx.MockTransport``.

Two rules mirror the Vertex client:

* The grounding instruction comes from ``clients.system_instruction`` so both
  providers are held to the same citation contract.
* A missing key, a non-2xx response, and an empty completion all raise
  ``CloudServiceError`` (never a bare exception), because the service layer
  maps that to HTTP 503 and the caller must be able to distinguish "provider
  unreachable" from "request is wrong".
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from phyto_shared.exceptions import CloudServiceError
from phyto_shared.literature_models import LLMOperation, PaperHit

from .clients import LLMCompletion, _estimate_tokens, system_instruction

__all__ = ["OpenRouterLLMClient", "DEFAULT_OPENROUTER_MODEL"]

# Instruction-tuned 31B model on OpenRouter's free tier: strong enough to
# follow the citation instruction and sized for RAG context.
# Fallback if rate-limited: xingchen-agi/xing4.0-29b-a4b:free
# OpenRouter's free tier rotates monthly. Verify availability with:
#   curl -s https://openrouter.ai/api/v1/models | python3 -c \
#     'import sys,json; [print(m["id"]) for m in json.load(sys.stdin)["data"] if ":free" in m["id"]]'
# Overridden by OPENROUTER_MODEL.
DEFAULT_OPENROUTER_MODEL = "google/gemma-4-31b-it:free"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterLLMClient:
    """Tier-3 client backed by OpenRouter's chat-completions API."""

    provider = "openrouter"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        model_version: str | None = None,
        *,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        # Never log or expose the key; it is read once at construction.
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY") or None
        self.model_id = model or os.environ.get("OPENROUTER_MODEL") or DEFAULT_OPENROUTER_MODEL
        self.base_url = (base_url or os.environ.get("OPENROUTER_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        # OpenRouter is a gateway: the same model slug can be served by
        # different upstreams, so the resolved upstream is part of provenance.
        self.model_version = model_version or "openrouter"
        self.timeout = timeout
        # Injectable transport is the test seam; production passes None.
        self._transport = transport

    def complete(
        self,
        operation: LLMOperation,
        context_papers: list[PaperHit],
        prompt: str,
    ) -> LLMCompletion:
        if not self.api_key:
            raise CloudServiceError(
                "OpenRouter API key not configured: set OPENROUTER_API_KEY "
                "(production: inject from Google Secret Manager)"
            )

        payload: dict[str, Any] = {
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": system_instruction(operation)},
                {"role": "user", "content": f"CONTEXT:\n{prompt}"},
            ],
            # Deterministic-ish output matters for reproducible grounding; the
            # provider still does not guarantee identical text.
            "temperature": 0.2,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            # OpenRouter uses these for attribution/rate-limit tiers.
            "HTTP-Referer": os.environ.get("OPENROUTER_SITE_URL", "https://phyto-companion.local"),
            "X-Title": "PhytoCompanion",
        }

        try:
            with httpx.Client(
                timeout=self.timeout,
                transport=self._transport,
            ) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise CloudServiceError(
                "OpenRouter request failed", context={"error": str(exc)}
            ) from exc

        if response.status_code >= 400:
            # Include the provider's error message but never the request
            # headers (they carry the key).
            raise CloudServiceError(
                "OpenRouter returned an error",
                context={
                    "status_code": response.status_code,
                    "body": response.text[:500],
                },
            )

        try:
            body = response.json()
            text = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise CloudServiceError(
                "OpenRouter returned an unexpected response shape",
                context={"error": str(exc)},
            ) from exc

        if not isinstance(text, str) or not text.strip():
            raise CloudServiceError("OpenRouter returned an empty completion")

        usage = body.get("usage") or {}
        # ``model`` is the upstream that actually served the request, which is
        # what provenance should record -- not our requested slug.
        served_model = body.get("model") or self.model_id

        return LLMCompletion(
            text=text.strip(),
            tokens_in=int(usage.get("prompt_tokens") or _estimate_tokens(prompt)),
            tokens_out=int(usage.get("completion_tokens") or _estimate_tokens(text)),
            model_id=served_model,
            model_version=self.model_version,
        )
