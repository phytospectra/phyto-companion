# Build context: repository root (see infrastructure/cloudbuild.yaml, Step 8)
FROM python:3.12-slim

WORKDIR /srv

COPY shared/ /srv/shared/
COPY services/orchestrator/pyproject.toml /srv/orchestrator/pyproject.toml
COPY services/orchestrator/app/ /srv/orchestrator/app/

# The token layer is a shared library imported as ``auth`` by every
# service. It is staged as /srv/auth (not /srv/auth/app) so the module
# name matches the import, and /srv is on the path to expose it.
COPY services/auth/app/ /srv/auth/

RUN pip install --no-cache-dir /srv/shared firebase-admin google-cloud-firestore google-cloud-pubsub fastapi uvicorn \
    && pip install --no-cache-dir /srv/orchestrator

ENV PHYTO_ENV=cloud
ENV PYTHONPATH=/srv
EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
