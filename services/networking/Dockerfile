# Build context: repository root (see infrastructure/cloudbuild.yaml, Step 8)
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY shared/ /srv/shared/
COPY services/networking/pyproject.toml /srv/networking/pyproject.toml
COPY services/networking/app/ /srv/networking/app/

RUN pip install --no-cache-dir /srv/shared fastapi uvicorn httpx numpy scipy pandas networkx matplotlib \
    && pip install --no-cache-dir /srv/networking

ENV PHYTO_ENV=cloud
EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
