#!/usr/bin/env bash
# Create the DNS records for phytospectra.com via the Cloudflare API.
# Requires a Cloudflare API token with Zone.DNS Edit permission:
#   export CLOUDFLARE_API_TOKEN=...   (create one at
#   https://dash.cloudflare.com/profile/api-tokens)
# Idempotent: re-running skips records that already exist.
#
# All hostnames serve the Cloud Run web app (phyto-frontend). The apex uses
# the A records Google issued with the phytospectra.com domain mapping; www
# and app use the CNAME.

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

delete_record() { # type name — remove a stale record (e.g. old download site)
  local type="$1" name="$2"
  local fqdn="${name}.${ZONE_NAME}"
  [[ "$name" == "@" ]] && fqdn="${ZONE_NAME}"
  local ids
  ids=$(api "${API}/zones/${zone_id}/dns_records?type=${type}&name=${fqdn}" \
    | python3 -c 'import sys,json;print(" ".join(r["id"] for r in json.load(sys.stdin)["result"]))')
  for id in $ids; do
    api -X DELETE "${API}/zones/${zone_id}/dns_records/${id}" \
      | python3 -c 'import sys,json;r=json.load(sys.stdin);print("deleted" if r["success"] else r.get("errors"))'
  done
}

# Retire the old download-site records (GCS website endpoints), if present.
delete_record CNAME '@'
delete_record CNAME 'www'

# Web app on all hostnames (Cloud Run frontend phyto-frontend).
# Apex: A records issued by the phytospectra.com Cloud Run domain mapping.
for ip in 216.239.32.21 216.239.34.21 216.239.36.21 216.239.38.21; do
  add_record A '@' "${ip}."
done
# www + app: CNAME to Google's Cloud Run endpoint.
add_record CNAME 'www' 'ghs.googlehosted.com.'
add_record CNAME 'app' 'ghs.googlehosted.com.'

echo "done. SSL certs provision automatically (~15-60 min). Smoke-test:"
echo "  curl -I https://phytospectra.com/   https://www.phytospectra.com/   https://app.phytospectra.com/"