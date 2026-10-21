# Build context: repository root (see infrastructure/cloudbuild.yaml, Step 8)
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY shared/ /srv/shared/
COPY services/elucidation/pyproject.toml /srv/elucidation/pyproject.toml
COPY services/elucidation/app/ /srv/elucidation/app/

RUN pip install --no-cache-dir /srv/shared fastapi uvicorn numpy scipy rdkit \
    && pip install --no-cache-dir /srv/elucidation

ENV PHYTO_ENV=cloud
EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
