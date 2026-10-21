# Build context: repository root (see infrastructure/cloudbuild.yaml, Step 8)
FROM python:3.12-slim

WORKDIR /srv

COPY shared/ /srv/shared/
COPY services/billing/pyproject.toml /srv/billing/pyproject.toml
COPY services/billing/app/ /srv/billing/app/

RUN pip install --no-cache-dir /srv/shared fastapi uvicorn \
        google-cloud-pubsub google-cloud-firestore stripe \
    && pip install --no-cache-dir /srv/billing

ENV PHYTO_ENV=cloud
EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
