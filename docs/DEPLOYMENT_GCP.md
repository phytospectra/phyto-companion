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
  --set-env-vars=PHYTO_ENV=cloud,PHYTO_DRY_RUN=1,PHYTO_APP_BASE_URL=https://www.phytospectra.com,PHYTO_FREE_TOKENS=50
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

## Public domains (phytospectra.com, phytospectra.org)

The web app is the only Phytospectra client — the desktop app (and with it
the download site) was removed on 2026-10-26, and on the same day the .org
was switched from the planned 301 redirect to serving the same app (ADR-0077).
Six hostnames serve the web app: `phytospectra.com`, `www.phytospectra.com`,
`app.phytospectra.com`, `phytospectra.org`, `www.phytospectra.org`,
`app.phytospectra.org`. `www.phytospectra.com` remains the canonical host
(citations, share links and Stripe return URLs default to it via
`PHYTO_APP_BASE_URL`).

Domain registration: Cloudflare (both zones, 2026-10-21, auto-renew).

Cloud Run domain mappings (service `phyto-frontend`, region `us-central1`):

| Domain                    | Status                        | DNS records needed                |
|---------------------------|-------------------------------|-----------------------------------|
| app.phytospectra.com      | LIVE (cert issued)            | CNAME app → ghs.googlehosted.com. |
| www.phytospectra.com      | awaiting DNS                  | CNAME www → ghs.googlehosted.com. |
| phytospectra.com (apex)   | awaiting DNS                  | A @ → 216.239.32/34/36/38.21      |
| phytospectra.org (apex)   | BLOCKED — domain verification | A @ → 216.239.32/34/36/38.21      |
| www.phytospectra.org      | BLOCKED — domain verification | CNAME www → ghs.googlehosted.com. |
| app.phytospectra.org      | BLOCKED — domain verification | CNAME app → ghs.googlehosted.com. |

**The .org mappings cannot be created until Google verifies the domain.**
`gcloud domains verify phytospectra.org` opens Search Console in a browser;
complete the verification there while signed in as the project owner
(the .com was already verified this way). Then:

```bash
gcloud beta run domain-mappings create --project phyto-companion-prod \
  --region us-central1 --service phyto-frontend --domain phytospectra.org
gcloud beta run domain-mappings create --project phyto-companion-prod \
  --region us-central1 --service phyto-frontend --domain www.phytospectra.org
gcloud beta run domain-mappings create --project phyto-companion-prod \
  --region us-central1 --service phyto-frontend --domain app.phytospectra.org
```

DNS records for BOTH zones (Cloudflare dashboard per zone, or run
`infrastructure/cloudflare_dns_setup.sh` with `CLOUDFLARE_API_TOKEN`; it
manages `phytospectra.com` and `phytospectra.org` in one pass and also
deletes the stale download-site records pointing at
`c.storage.googleapis.com` / the .org redirect placeholders):

- Per zone, root: four A records `216.239.32.21`, `216.239.34.21`,
  `216.239.36.21`, `216.239.38.21` (Google-issued for apex mappings; a CNAME
  at the apex is not usable), Proxy: DNS only.
- Per zone, www and app: CNAME → `ghs.googlehosted.com.`, Proxy: DNS only.

NOTE: the script writes .org records even before the mappings exist — that
is harmless (Google simply returns its default until the mapping is created)
but cert issuance only starts once the mapping is in place. To do it in the
cleanest order, finish the Search Console verification + mappings first, then
run the script once.

SSL certificates provision automatically once DNS resolves; allow ~15-60
min, then confirm with
`gcloud beta run domain-mappings describe --domain <domain> --project
phyto-companion-prod --region us-central1` (all conditions `True`).

Smoke test once certs are provisioned:

```bash
for h in phytospectra.com www.phytospectra.com app.phytospectra.com \
         phytospectra.org www.phytospectra.org app.phytospectra.org; do
  curl -sI "https://$h/" | head -1 | sed "s|^|$h: |"
done
```

## Retired download site

The desktop-app download page lived in GCS buckets whose names matched the
request Host header: `phytospectra.com`, `www.phytospectra.com` and
`phytospectra-downloads`. With the desktop app removed, these buckets (and
the `downloads/` installer objects) are obsolete and can be deleted:

```bash
gcloud storage buckets delete gs://phytospectra.com \
  gs://www.phytospectra.com gs://phytospectra-downloads \
  --project=phyto-companion-prod
```

## phytospectra.org — replaced plan

An earlier version of this section served the .org as a Cloudflare 301
redirect to the .com. Superseded 2026-10-26 (ADR-0077): the .org serves the
same web app directly — see "Public domains" above. No redirect rule is
needed; the Cloudflare Redirect Rules for the .org (if any were created)
can be deleted once the domain mappings are live.
