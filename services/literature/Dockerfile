# Build context: repository root (see infrastructure/cloudbuild.yaml, Step 8)
#
# Cloud Run, no GPU, scales to zero.  No model weights are bundled; the
# service is pure I/O plus local ranking maths, so the image stays small and
# cold starts stay fast.
FROM python:3.12-slim

WORKDIR /srv

COPY shared/ /srv/shared/
COPY services/literature/pyproject.toml /srv/literature/pyproject.toml
COPY services/literature/app/ /srv/literature/app/

RUN pip install --no-cache-dir /srv/shared fastapi uvicorn requests \
    && pip install --no-cache-dir /srv/literature

# NCBI_API_KEY and SEMANTIC_SCHOLAR_API_KEY are injected at runtime from
# Secret Manager (ADR-0032) -- never baked into the image.
ENV PHYTO_ENV=cloud
ENV PORT=8080
EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
