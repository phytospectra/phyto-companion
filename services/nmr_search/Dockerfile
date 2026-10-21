# Build context: repository root (see infrastructure/cloudbuild.yaml, Step 8)
#
# Cloud Run, no GPU, scales to zero.  Pure HTTP I/O plus local matching maths,
# so the image stays small and cold starts stay fast.  The local fixture is
# NOT copied: the mock client degrades to built-in records when it is absent,
# and the container runs with PHYTO_ENV=cloud anyway.
FROM python:3.12-slim

WORKDIR /srv

COPY shared/ /srv/shared/
COPY services/nmr_search/pyproject.toml /srv/nmr_search/pyproject.toml
COPY services/nmr_search/app/ /srv/nmr_search/app/

# The token layer is a shared library imported as ``auth`` by every
# service. It is staged as /srv/auth (not /srv/auth/app) so the module
# name matches the import, and /srv is on the path to expose it.
COPY services/auth/app/ /srv/auth/

RUN pip install --no-cache-dir /srv/shared firebase-admin google-cloud-firestore google-cloud-pubsub fastapi uvicorn requests \
    && pip install --no-cache-dir /srv/nmr_search

# Database credentials (if any) are injected at runtime from Secret Manager
# (ADR-0032) -- never baked into the image.
ENV PHYTO_ENV=cloud
ENV PYTHONPATH=/srv
ENV PORT=8080
EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
