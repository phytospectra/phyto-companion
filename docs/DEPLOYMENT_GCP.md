# GCP Deployment Guide — Phytospectra

Project: `phyto-companion-prod` (billing enabled, region `us-central1`)

## Current state (deployed 2026-10-25)

| Component | Status | Where |
|---|---|---|
| Orchestrator (full plane) | **LIVE** | Cloud Run `phyto-orchestrator` → https://phyto-orchestrator-819429615655.us-central1.run.app |
| Firestore (native) | LIVE | `us-central1` default database |
| Firebase project link | DONE | Web app `Phytospectra Web`, appId `1:819429615655:web:528d9a995b18a8b3bf8602` |
| Artifact Registry | LIVE | `phyto` repo, image `orchestrator-fullplane:latest` |
| GCS | LIVE | `phyto-companion-dev-public-data` (public NP-MRD archive) |

Deployed image = `infrastructure/Dockerfile.fullplane` (orchestrator + all 18
specialist services staged on `PYTHONPATH`, exactly the layout the local
full-plane boot uses).

## Redeploy

```bash
gcloud builds submit --project=phyto-companion-prod --region=us-central1 \
  --config=infrastructure/cloudbuild-fullplane.yaml .
gcloud run deploy phyto-orchestrator --project=phyto-companion-prod \
  --region=us-central1 \
  --image=us-central1-docker.pkg.dev/phyto-companion-prod/phyto/orchestrator-fullplane:latest \
  --allow-unauthenticated --memory=2Gi --cpu=2 --timeout=900 --max-instances=10 \
  --set-env-vars=PHYTO_ENV=cloud,PHYTO_DRY_RUN=1,PHYTO_APP_BASE_URL=https://app.phytospectra.com,PHYTO_FREE_TOKENS=50
```

(`infrastructure/cloudbuild-fullplane.yaml` wraps the Dockerfile build.)

## Firebase Auth — one manual step remains

`identitytoolkit.googleapis.com` is enabled but the default auth config is
`CONFIGURATION_NOT_FOUND`. Activating Identity Platform from the CLI is
denied (`servicemanagement.services.bind` refused for both the gmail owner
and the firebase-admin service account — it requires the interactive
console consent):

1. Open https://console.firebase.google.com/project/phyto-companion-prod/authentication
2. Click **Get started** → enable **Email/Password**.
3. Under Users, add the first user if needed.

Then redeploy with real Firebase credentials:

```bash
gcloud secrets create firebase-credentials --data-file=- <<< '{}'   # real SA json
gcloud run deploy phyto-orchestrator ... \
  --set-secrets=FIREBASE_CREDENTIALS_PATH=... \
  --set-env-vars=PHYTO_ENV=cloud,PHYTO_DRY_RUN=0,PHYTO_AUTH_ENABLED=1,...
```

Until that step, the deployment runs with `PHYTO_DRY_RUN=1`, which keeps
the mock token verifier active: any request with
`Authorization: Bearer mock:<uid>` is accepted as that user. This is
intended for bring-up only; **flip to real Firebase Auth before public
launch.**

## Smoke test (verified)

```bash
BASE=https://phyto-orchestrator-819429615655.us-central1.run.app
curl -s $BASE/health
curl -s -X POST $BASE/analysis -H "Authorization: Bearer mock:smoketest" \
  -H "Content-Type: application/json" \
  -d '{"sample_id":"smoke-1","workflow":"identify","dry_run":false,
       "modalities":["h_1d","c_1d","hsqc","ms1","ms2"]}'
# -> awaiting_review (checkpoint before_confirmed_claim)
curl -s -X POST $BASE/runs/<run_id>/approve \
  -H "Authorization: Bearer mock:smoketest" -H "Content-Type: application/json" \
  -d '{"checkpoint":"before_confirmed_claim","actor":"me"}'
# -> status complete + citation minted
```

## Public domains (phytospectra.com)

Both domains are registered at Cloudflare (2026-10-21, auto-renew). The GCP
side is ready; only the DNS records in the Cloudflare dashboard are missing.

Already done on the GCP side:

- Download site: bucket `gs://phytospectra-downloads` with website config
  (`index.html` / `404.html`), public via `allUsers:objectViewer`. Installers
  live under `downloads/` (macOS zip, Windows exe, SHA256SUMS.txt).
- Web app: Cloud Run service `phyto-frontend`
  (https://phyto-frontend-819429615655.us-central1.run.app).
- Orchestrator: `phyto-orchestrator` (real Firebase auth, dry_run off).

Records to add (Cloudflare dashboard → phytospectra.com → DNS, or run
`infrastructure/cloudflare_dns_setup.sh` with `CLOUDFLARE_API_TOKEN`):

| Type  | Name | Target                     | Purpose            |
|-------|------|----------------------------|--------------------|
| CNAME | @    | c.storage.googleapis.com   | download site      |
| CNAME | www  | c.storage.googleapis.com   | download site      |
| CNAME | app  | ghs.googlehosted.com       | web app (Cloud Run)|

Then:

```bash
gcloud domains verify app.phytospectra.com
gcloud beta run domain-mappings create --project phyto-companion-prod \
  --region us-central1 --service phyto-frontend --domain app.phytospectra.com
# follow its instructions to verify; SSL cert provisioning takes ~15-60 min
```

Smoke test once DNS propagates:

```bash
curl -I https://phytospectra.com/          # 200, download page
curl -I https://phytospectra.com/downloads/Phytospectra-macos.zip
curl -I https://app.phytospectra.com/      # 200, web app
```

## Domain-named bucket (required for GCS CNAME hosting)

GCS resolves the bucket from the request's Host header, so the bucket serving
`https://phytospectra.com` **must be named `phytospectra.com`** (created in the
US multi-region, website config `index.html`/`404.html`, public via
`allUsers:objectViewer`). Creating it requires one-time ownership verification:
`gcloud domains verify phytospectra.com` (Cloudflare one-click works because
DNS lives there). `gs://phytospectra-downloads` remains as a mirror for legacy
`storage.googleapis.com` links; the CI publish job writes to both.

## phytospectra.org

The .org zone is on the same Cloudflare account. Serve it as a 301 redirect
to the .com (keeps one canonical host for SSL, SEO and cert management):

1. Cloudflare → phytospectra.org → DNS: add proxied (orange) record
   `A @ 192.0.2.1` and `CNAME www 192.0.2.1` (placeholder targets; the
   redirect rule intercepts all traffic).
2. Rules → Redirect Rules → Create rule:
   - If: Hostname equals `phytospectra.org` **or** `www.phytospectra.org`
   - Then: Static redirect, 301, `https://phytospectra.com`,
     preserve query and path.
