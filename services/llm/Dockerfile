# Build context: repository root (see infrastructure/cloudbuild.yaml, Step 8)
#
# PRIMARY PATH: this container is a thin HTTP client for a Vertex AI endpoint
# hosting Qwen 2.5 7B Instruct + a QLoRA adapter.  It is CPU-only and scales
# to zero; the GPU lives behind the Vertex endpoint.
#
# SECONDARY PROVIDER (ADR-0034): the same image can route to OpenRouter by
# setting PHYTO_LLM_PROVIDER=openrouter (or per request with
# provider="openrouter").  OPENROUTER_API_KEY is injected from Secret Manager
# at runtime; nothing provider-specific is baked in.
#
# FALLBACK PATH: if Vertex AI is unavailable in the target region, deploy a
# vLLM image instead -- see infrastructure/Dockerfile.vllm.  The application
# code does not change; only VERTEX_ENDPOINT_ID vs VLLM_BASE_URL.
FROM python:3.12-slim

WORKDIR /srv

COPY shared/ /srv/shared/
COPY services/llm/pyproject.toml /srv/llm/pyproject.toml
COPY services/llm/app/ /srv/llm/app/

# The token layer is a shared library imported as ``auth`` by every
# service. It is staged as /srv/auth (not /srv/auth/app) so the module
# name matches the import, and /srv is on the path to expose it.
COPY services/auth/app/ /srv/auth/

RUN pip install --no-cache-dir /srv/shared firebase-admin google-cloud-firestore google-cloud-pubsub fastapi uvicorn httpx \
        google-cloud-aiplatform \
    && pip install --no-cache-dir /srv/llm

# VERTEX_ENDPOINT_ID / GOOGLE_CLOUD_PROJECT / OPENROUTER_API_KEY come from
# Secret Manager at runtime (ADR-0032/ADR-0034); no credentials are baked in.
ENV PHYTO_ENV=cloud
ENV PYTHONPATH=/srv
ENV PORT=8080
EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
