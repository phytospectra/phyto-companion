#!/usr/bin/env bash
# Point the Phytospectra domains at the Cloud Run web app, via the Cloudflare
# API. Covers BOTH zones: phytospectra.com and phytospectra.org (the .org is
# no longer a redirect — it serves the same web app; see ADR-0077).
#
#   export CLOUDFLARE_API_TOKEN=...        # Zone.DNS Edit on both zones
#   DRY_RUN=1 infrastructure/cloudflare_dns_setup.sh   # preview, changes nothing
#   infrastructure/cloudflare_dns_setup.sh             # apply
#
# Create a token at https://dash.cloudflare.com/profile/api-tokens
# (template "Edit zone DNS", include both zones).
#
# Desired end state — every hostname serves the web app (Cloud Run
# phyto-frontend); there is no download site any more:
#   <zone>            A 216.239.32.21 / .34.21 / .36.21 / .38.21  (DNS only)
#   www.<zone>        CNAME ghs.googlehosted.com.                 (DNS only)
#   app.<zone>        CNAME ghs.googlehosted.com.                 (DNS only)
#
# The apex A records are the ones Google issues with each domain mapping
# (apex mappings cannot use a CNAME). NOTE: the .org mappings cannot be
# created until the domain is verified in Google Search Console — the
# `gcloud domains verify phytospectra.org` output opens the page; complete
# it in the browser, then run:
#   gcloud beta run domain-mappings create --project phyto-companion-prod \
#     --region us-central1 --service phyto-frontend --domain phytospectra.org
#   gcloud beta run domain-mappings create --project phyto-companion-prod \
#     --region us-central1 --service phyto-frontend --domain www.phytospectra.org
#
# Old address records at those names (the GCS download-site CNAMEs) are
# removed; unrelated record types at the apex (MX, TXT, SPF) are left alone.
# Idempotent: a second run finds the desired state and changes nothing.

set -euo pipefail

: "${CLOUDFLARE_API_TOKEN:?Set CLOUDFLARE_API_TOKEN to a Cloudflare token with Zone.DNS Edit on the Phytospectra zones}"

export ZONE_NAMES="${ZONE_NAMES:-phytospectra.com phytospectra.org}"
export DRY_RUN="${DRY_RUN:-0}"

python3 - <<'PY'
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

zones_arg = os.environ["ZONE_NAMES"]
token = os.environ["CLOUDFLARE_API_TOKEN"].strip()
dry_run = os.environ.get("DRY_RUN", "0").lower() in {"1", "true", "yes"}
api = os.environ.get("CF_API_BASE", "https://api.cloudflare.com/client/v4").rstrip("/")

# Google-issued targets for the Cloud Run domain mappings.
APEX_IPS = ["216.239.32.21", "216.239.34.21", "216.239.36.21", "216.239.38.21"]
WEB_CNAME = "ghs.googlehosted.com."
ADDRESS_TYPES = {"A", "AAAA", "CNAME"}

# Catch the copy-paste failure that produced Cloudflare error 6111 ("Invalid
# format for Authorization header") before spending a request on it.
PLACEHOLDERS = {"PLACEHOLDER", "YOUR_TOKEN", "TOKEN", "...", "<token>", "xxx"}
if token.upper() in {p.upper() for p in PLACEHOLDERS} or len(token) < 20:
    raise SystemExit(
        "CLOUDFLARE_API_TOKEN does not look like a real token "
        f"(got {len(token)} characters).\n"
        "Create one: https://dash.cloudflare.com/profile/api-tokens ->\n"
        '  Create Token -> template "Edit zone DNS", scope: both zones\n'
        "then paste the generated value, e.g.:\n"
        "  export CLOUDFLARE_API_TOKEN=AbCdEf0123456789_-real-token-here"
    )


def call(method, path, body=None):
    url = f"{api}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            payload = json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        try:
            detail = json.dumps(json.loads(detail).get("errors", detail))
        except ValueError:
            pass
        hint = ""
        if exc.code in (400, 401, 403) and ("6111" in detail or "6003" in detail
                                            or "10000" in detail):
            hint = ("\nHint: the token string itself was rejected. Re-copy it from\n"
                    "https://dash.cloudflare.com/profile/api-tokens — it must be one\n"
                    "unbroken ~40-character value, with no quotes or placeholder text.")
        elif exc.code == 403:
            hint = ("\nHint: this token lacks Zone.DNS Edit on the zone.\n"
                    "Edit the token's permissions in the Cloudflare dashboard.")
        raise SystemExit(f"Cloudflare API {method} {path} failed ({exc.code}): {detail}{hint}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"Cannot reach Cloudflare: {exc.reason}")
    if not payload.get("success"):
        raise SystemExit(f"Cloudflare API error on {method} {path}: {payload.get('errors')}")
    return payload.get("result")


def same(record, want):
    return (
        record["type"] == want["type"]
        and record["content"].rstrip(".") == want["content"].rstrip(".")
        and record.get("proxied") is False
    )


deletions, creations, summaries = [], [], []

for zone_name in zones_arg.split():
    zones = call("GET", f"/zones?name={urllib.parse.quote(zone_name)}")
    if not zones:
        print(f"WARNING: zone {zone_name} not found on this account — skipped.")
        continue
    zone_id = zones[0]["id"]
    print(f"zone {zone_name} -> {zone_id}")
    existing = call("GET", f"/zones/{zone_id}/dns_records?per_page=100")

    # Only address records are ours to manage; MX/TXT/NS/SRV at the apex stay.
    desired = {}
    for ip in APEX_IPS:
        desired.setdefault(zone_name, []).append({"type": "A", "name": zone_name, "content": ip})
    desired.setdefault(f"www.{zone_name}", []).append(
        {"type": "CNAME", "name": f"www.{zone_name}", "content": WEB_CNAME})
    desired.setdefault(f"app.{zone_name}", []).append(
        {"type": "CNAME", "name": f"app.{zone_name}", "content": WEB_CNAME})

    zone_del, zone_new = [], []
    for name, wanted in desired.items():
        current = [r for r in existing if r["name"] == name and r["type"] in ADDRESS_TYPES]
        matched = []
        for want in wanted:
            hit = next((r for r in current if r not in matched and same(r, want)), None)
            if hit is None:
                zone_new.append(dict(want, proxied=False, ttl=3600))
            else:
                matched.append(hit)
        # Anything address-typed at this name that is not part of the wanted
        # set goes: stale download-site CNAMEs, proxied copies, extra AAAA.
        zone_del.extend(r for r in current if r not in matched)

    deletions.extend((zone_id, r) for r in zone_del)
    creations.extend((zone_id, r) for r in zone_new)
    summaries.append((zone_name, zone_del, zone_new))

print(f"\n{'DRY RUN — nothing will change' if dry_run else 'Applying changes'}")
for zone_name, zone_del, zone_new in summaries:
    if zone_del or zone_new:
        print(f"  {zone_name}:")
        for record in zone_del:
            print(f"    delete {record['type']:<5} {record['name']:<28} {record['content']}"
                  + ("  (proxied)" if record.get("proxied") else ""))
        for record in zone_new:
            print(f"    create {record['type']:<5} {record['name']:<28} {record['content']}")
if not deletions and not creations:
    print("  already up to date (all zones)")
    raise SystemExit(0)
if dry_run:
    raise SystemExit(0)

# Deletes first: Cloudflare refuses an A record where a CNAME still sits.
for zone_id, record in deletions:
    call("DELETE", f"/zones/{zone_id}/dns_records/{record['id']}")
    print(f"  deleted {record['type']} {record['name']}")
for zone_id, record in creations:
    call("POST", f"/zones/{zone_id}/dns_records", record)
    print(f"  created {record['type']} {record['name']} -> {record['content']}")
PY

echo
echo "DNS updated. Google provisions the SSL certificate automatically"
echo "(~15-60 min after the records are live). Check with:"
echo "  gcloud beta run domain-mappings list --project phyto-companion-prod --region us-central1"
echo "Then smoke-test: curl -I https://phytospectra.com/ https://www.phytospectra.com/ \\"
echo "  https://app.phytospectra.com/ https://phytospectra.org/ https://www.phytospectra.org/ https://app.phytospectra.org/"