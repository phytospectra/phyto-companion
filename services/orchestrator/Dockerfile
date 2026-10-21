# Build context: repository root (see infrastructure/cloudbuild.yaml, Step 8)
FROM python:3.12-slim

WORKDIR /srv

COPY shared/ /srv/shared/
COPY services/orchestrator/pyproject.toml /srv/orchestrator/pyproject.toml
COPY services/orchestrator/app/ /srv/orchestrator/app/

RUN pip install --no-cache-dir /srv/shared fastapi uvicorn \
    && pip install --no-cache-dir /srv/orchestrator

ENV PHYTO_ENV=cloud
EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
