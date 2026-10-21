# Build context: repository root (see infrastructure/cloudbuild.yaml, Step 8)
FROM python:3.12-slim

WORKDIR /srv

COPY shared/ /srv/shared/
COPY services/auth/pyproject.toml /srv/auth/pyproject.toml
COPY services/auth/app/ /srv/auth/app/

RUN pip install --no-cache-dir /srv/shared fastapi uvicorn firebase-admin \
    && pip install --no-cache-dir /srv/auth

ENV PHYTO_ENV=cloud
EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
