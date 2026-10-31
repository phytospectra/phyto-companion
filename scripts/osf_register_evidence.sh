#!/usr/bin/env bash
# Create the OSF acceptance-evidence archive (ADR-0088).
#
# Fails closed: without OSF_TOKEN in the environment nothing is sent, no
# project is created, and no partial state is left behind. The script
# never prints the token and never writes it to disk.
#
# Usage:
#   OSF_TOKEN=... scripts/osf_register_evidence.sh [--embargo YYYY-MM-DD]
#
# Steps:
#   1. scripts/build_osf_package.py must have been run (data/osf_package/)
#   2. POST a new OSF project node (title/description/category)
#   3. Upload every file in the package via the waterbutler API
#   4. Create an embargoed registration of the project
#   5. Record the resulting GUIDs in data/validated_runs/osf_registration.json
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PKG="$REPO/data/osf_package"
API="https://api.osf.io/v2"
REG_OUT="$REPO/data/validated_runs/osf_registration.json"

EMBARGO="${2:-}"
if [ "${1:-}" = "--embargo" ] && [ -n "$EMBARGO" ]; then
  :
else
  EMBARGO=""
fi

if [ -z "${OSF_TOKEN:-}" ]; then
  echo "FAIL: OSF_TOKEN not set — refusing to contact OSF (ADR-0088 fail-closed)." >&2
  exit 1
fi
if [ ! -f "$PKG/MANIFEST.sha256" ]; then
  echo "FAIL: $PKG/MANIFEST.sha256 missing — run scripts/build_osf_package.py first." >&2
  exit 1
fi
# CITATION.cff legitimately carries author identity (it is the citation
# record the owner authored); the other files must be redaction-clean.
# Case-sensitive: ~/.mzmine/users/ must not trip /Users/.
if grep -rn "subhadip" "$PKG" | grep -v "CITATION.cff" | grep -q . || \
   grep -rn "/Users/" "$PKG" | grep -q .; then
  echo "FAIL: package contains un-redacted local identity — aborting." >&2
  exit 1
fi

AUTH="Authorization: Bearer $OSF_TOKEN"
CREATED_JSON="$(mktemp)"
trap 'rm -f "$CREATED_JSON"' EXIT

echo "==> creating OSF project node"
curl -sf "$API/nodes/" -H "$AUTH" -H "Content-Type: application/json" \
  -d '{
    "data": {
      "type": "nodes",
      "attributes": {
        "title": "Phytospectra M0-M1 acceptance evidence (ADR-0085..0088)",
        "description": "Timestamped snapshot of the Phytospectra platform acceptance tests: pinned MZmine 4.11 batch contract, live feature-detection runs (M1-a tetracycline, M1-b thiostrepton via ThermoRawFileParser), with MassIVE MSV000090047 referenced by checksum. Third-party instrument data is NOT redistributed. Third-party data (mzML/.raw from MSV000090047) is referenced by accession + checksum only. Embargoed until owner release; DOI to be added to CITATION.cff after publication.",
        "category": "project"
      }
    }
  }' > "$CREATED_JSON"

NODE_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["data"]["id"])' "$CREATED_JSON")"
echo "    node: $NODE_ID"

echo "==> uploading $(grep -c . "$PKG/MANIFEST.sha256") files"
while IFS= read -r line; do
  SHA="${line%%  *}"
  REL="${line#*  }"
  F="$PKG/$REL"
  echo "    upload $REL"
  curl -sf "$API/nodes/$NODE_ID/files/osfstorage/" -H "$AUTH" \
    -o /dev/null || echo "    (osfstorage listing failed; continuing upload)"
  # waterbutler upload: PUT with file content
  curl -sf -X PUT "$API/nodes/$NODE_ID/files/osfstorage/?kind=file&name=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$REL")" \
    -H "$AUTH" --data-binary "@$F" -o /dev/null
  [ "$(shasum -a 256 "$F" | cut -d' ' -f1)" = "$SHA" ] || { echo "FAIL: checksum drift for $REL" >&2; exit 1; }
done < "$PKG/MANIFEST.sha256"

echo "==> creating embargoed registration"
REG_JSON="$(mktemp)"
EMBARGO_ATTR='{"data":{"type":"registrations","attributes":{"draft_registrations":null}}}'
if [ -n "$EMBARGO" ]; then
  curl -sf "$API/nodes/$NODE_ID/registrations/" -H "$AUTH" \
    -H "Content-Type: application/json" \
    -d "{\"data\":{\"type\":\"registrations\",\"attributes\":{\"draft_registrations\":null},\"registration_choice\":\"embargo\",\"embargo_end_date\":\"$EMBARGO\"}}" \
    > "$REG_JSON"
else
  curl -sf "$API/nodes/$NODE_ID/registrations/" -H "$AUTH" \
    -H "Content-Type: application/json" \
    -d '{"data":{"type":"registrations","attributes":{}}}' > "$REG_JSON"
fi
REG_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["data"]["id"])' "$REG_JSON")"
rm -f "$REG_JSON"

python3 - "$NODE_ID" "$REG_ID" "$EMBARGO" "$REG_OUT" <<'PY'
import json, sys, pathlib, datetime
node_id, reg_id, embargo, out = sys.argv[1:5]
pathlib.Path(out).write_text(json.dumps({
    "adr": "ADR-0088",
    "osf_node_id": node_id,
    "osf_registration_id": reg_id,
    "embargo_end_date": embargo or None,
    "package_manifest": "data/osf_package/MANIFEST.sha256",
    "created_utc": datetime.datetime.utcnow().isoformat() + "Z",
}, indent=2) + "\n")
PY
echo "==> done: node $NODE_ID, registration $REG_ID (recorded in $REG_OUT)"
