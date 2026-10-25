#!/usr/bin/env bash
# Create the DNS records for phytospectra.com via the Cloudflare API.
# Requires a Cloudflare API token with Zone.DNS Edit permission:
#   export CLOUDFLARE_API_TOKEN=...   (create one at
#   https://dash.cloudflare.com/profile/api-tokens)
# Idempotent: re-running skips records that already exist.

set -euo pipefail

: "${CLOUDFLARE_API_TOKEN:?Set CLOUDFLARE_API_TOKEN first}"
ZONE_NAME="${ZONE_NAME:-phytospectra.com}"
API="https://api.cloudflare.com/client/v4"

api() {
  curl -sS -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
    -H "Content-Type: application/json" "$@"
}

zone_id=$(api "${API}/zones?name=${ZONE_NAME}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["result"][0]["id"])')
echo "zone ${ZONE_NAME} -> ${zone_id}"

existing=$(api "${API}/zones/${zone_id}/dns_records?per_page=100" | python3 -c 'import sys,json;print(" ".join(f"{r[\"type\"]}:{r[\"name\"]}" for r in json.load(sys.stdin)["result"]))')

add_record() { # type name content proxied
  local type="$1" name="$2" content="$3" proxied="${4:-false}"
  local fqdn="${name}.${ZONE_NAME}"
  [[ "$name" == "@" ]] && fqdn="${ZONE_NAME}"
  if [[ " $existing " == *" ${type}:${fqdn} "* ]]; then
    echo "skip existing ${type} ${fqdn}"; return
  fi
  api -X POST "${API}/zones/${zone_id}/dns_records" \
    --data "{\"type\":\"${type}\",\"name\":\"${name}\",\"content\":\"${content}\",\"proxied\":${proxied},\"ttl\":3600}" \
    | python3 -c 'import sys,json;r=json.load(sys.stdin);print("created" if r["success"] else r.get("errors"))'
}

# Download site (GCS website endpoint, bucket phytospectra-downloads)
add_record CNAME '@' 'c.storage.googleapis.com.'
add_record CNAME 'www' 'c.storage.googleapis.com.'
# Web app (Cloud Run frontend)
add_record CNAME 'app' 'ghs.googlehosted.com.'

echo "done. Next: gcloud domains verify app.phytospectra.com, then recreate the Cloud Run domain mapping."
