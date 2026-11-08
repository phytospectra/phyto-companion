# GCP Deployment Guide — Phytospectra

Project: `phyto-companion-prod` (billing enabled, region `us-central1`)

## Readiness update — 2026-11-08

The October bring-up instructions below have been superseded. Firebase providers
were already enabled in the November 8 readiness inspection. Fresh unauthenticated
and deliberately invalid-token requests to `/tools` both return 401. This does
not verify valid-user login, accounting, or scientific execution.

Use [the readiness report](RELEASE-AUTH-READINESS-2026-11-08.md) for the recorded
configuration, staging gates and rollback requirements. The source orchestrator
still uses `MockToolExecutor`; its new health fields explicitly report
`scientific_execution: mock` and `scientific_ready: false`, independently of auth
`dry_run`. These new fields require deployment of the updated source; the observed
live service still returns the older health payload.

## Historical infrastructure inventory (2026-10-25; not re-certified)

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

## Deployment prerequisites

Before deployment, select a reviewed source revision with passing CI, build and
record an immutable image digest, and establish an isolated staging target and
rollback configuration. Preserve existing CORS origins, secret references and
other service settings. Staging specialist modules on `PYTHONPATH` does not wire
them into the orchestrator's executor or expose their routes.

Real authentication uses `PHYTO_AUTH_ENABLED=1` and `PHYTO_DRY_RUN=0` with the
intended project and workload identity. Do not set mock authentication on a public
service. These settings do not turn the mock scientific executor into a real one.
Use Cloud Run workload identity/ADC where configured. If
`FIREBASE_CREDENTIALS_PATH` is used, it is a path to a readable credentials file,
not raw secret JSON. Do not create empty credential secrets or replace the full
environment using the former bring-up command.

Firebase activation is not a pending step established by current evidence.
Valid-user login, refresh/revocation, browser CORS, ledger/IAM and scientific
provider acceptance remain staging checks. No deployment was performed during
this update.

## Non-mutating access checks

```bash
BASE=https://phyto-orchestrator-sbqloxr3xa-uc.a.run.app
curl -sS "$BASE/health"
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/tools"
curl -sS -o /dev/null -w '%{http_code}\n' \
  -H 'Authorization: Bearer deliberately-invalid' "$BASE/tools"
```

Both `/tools` checks returned 401 on November 8. `/health` returned 200 with
`dry_run:false`; this is not proof of real science. The former mock-token analysis
and approval commands are removed from this public-deployment guide. A mock
citation is not a registered DOI.

## Historical domain configuration (2026-10-26; verify before changes)

The web app is the only Phytospectra client — the desktop app (and with it
the download site) was removed on 2026-10-26, and on the same day the .org
was switched from the planned 301 redirect to serving the same app (ADR-0077).
Six hostnames serve the web app: `phytospectra.com`, `www.phytospectra.com`,
`app.phytospectra.com`, `phytospectra.org`, `www.phytospectra.org`,
`app.phytospectra.org`. `www.phytospectra.com` remains the canonical host
(citations, share links and Stripe return URLs default to it via
`PHYTO_APP_BASE_URL`).

Domain registration: Cloudflare (both zones, 2026-10-21, auto-renew).

Cloud Run domain mappings (service `phyto-frontend`, region `us-central1`;
all six created and LIVE with issued certificates as of 2026-10-26 —
verified end-to-end, HTTP 200 on every hostname):

| Domain                    | Status     | DNS records                       |
|---------------------------|------------|-----------------------------------|
| app.phytospectra.com      | LIVE       | CNAME app → ghs.googlehosted.com. |
| phytospectra.com (apex)   | LIVE       | A @ → 216.239.32/34/36/38.21      |
| www.phytospectra.com      | LIVE       | CNAME www → ghs.googlehosted.com. |
| phytospectra.org (apex)   | LIVE       | A @ → 216.239.32/34/36/38.21      |
| www.phytospectra.org      | LIVE       | CNAME www → ghs.googlehosted.com. |
| app.phytospectra.org      | LIVE       | CNAME app → ghs.googlehosted.com. |

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
