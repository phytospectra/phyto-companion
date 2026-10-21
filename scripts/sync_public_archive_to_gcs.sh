#!/usr/bin/env bash
#
# Upload the PUBLIC NP-MRD raw NMR archive to Google Cloud Storage.
#
# Source:  NP-MRD public download
#          https://np-mrd.org/system/downloads/current/experimental_spectra.zip
# License: CC-BY-NC (NP-MRD). Redistributable for research use;
#          COMMERCIAL USE IS PROHIBITED (see ADR-0026).
#          NOT the owner's experimental data and NOT confidential.
#
# TWO MODES
#   (default)   Upload a MATERIALIZED local copy from the Drive workspace.
#               Refuses dataless (placeholder) files.
#   --from-url  Download inside Google Cloud (Cloud Shell / GCE), upload, then
#               hash the uploaded object remotely. Refused outside Cloud Shell
#               unless --allow-file-destination is passed (tests only).
#
# HARD RULES
#   1. The local source folder is READ-ONLY: nothing written, renamed,
#      moved or deleted there.
#   2. Exactly ONE OBJECT is uploaded (the archive). Model checkpoints,
#      benchmark outputs and database dumps in the same workspace are never
#      uploaded by this script.
#   3. No `-d`: nothing in the bucket is ever deleted.
#   4. Google Drive placeholders are refused (0 allocated blocks / dataless).
#   5. The archive is NOT committed to git.
#
# USAGE
#   scripts/sync_public_archive_to_gcs.sh --bucket gs://phyto-companion-dev-public-data
#   scripts/sync_public_archive_to_gcs.sh --bucket ... --dry-run
#   scripts/sync_public_archive_to_gcs.sh --from-url \
#       --bucket gs://phyto-companion-dev-public-data --yes
#
# EXIT CODES
#   0 ok | 2 no bucket | 3 placeholder | 4 aborted | 5 not Cloud Shell
#
# REDACTION NOTE: the source path's account segment has been redacted (it
# contained a personal email address). Supply the real path via
# PHYTO_NPMRD_ARCHIVE; the default below is a placeholder and will not resolve.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

SOURCE_ARCHIVE="${PHYTO_NPMRD_ARCHIVE:-/Users/subhadipbanerjee/Library/CloudStorage/GoogleDrive-[redacted]/My Drive/NMR-Data/experimental_spectra.zip}"
OBJECT_NAME="experimental_spectra.zip"
GCS_PREFIX="npmrd/raw"
MANIFEST="${PHYTO_PUBLIC_MANIFEST:-${REPO_ROOT}/data/inventory/public_data_manifest.json}"

PUBLIC_URL="${PHYTO_PUBLIC_URL:-https://np-mrd.org/system/downloads/current/experimental_spectra.zip}"
EXPECTED_SIZE_BYTES="${PHYTO_EXPECTED_SIZE_BYTES:-13721283834}"
LICENSE="CC-BY-NC"
ATTRIBUTION="NP-MRD (Natural Products Magnetic Resonance Database), https://np-mrd.org"
LICENSE_WARNING="NP-MRD data is licensed CC-BY-NC, which prohibits commercial use. The platform's FREE academic tier may serve NP-MRD data (research use) with attribution. The PRO tier must NOT include NP-MRD data unless a separate commercial license is obtained from NP-MRD. The Pro-tier data strategy must be resolved before deployment (see ADR-0026, pending)."

BUCKET=""
DRY_RUN=0
ASSUME_YES=0
ALLOW_FILE_DEST=0
FROM_URL=0

usage() {
  sed -n '3,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bucket) BUCKET="${2:-}"; shift 2 ;;
    --bucket=*) BUCKET="${1#*=}"; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    --from-url) FROM_URL=1; shift ;;
    --allow-file-destination) ALLOW_FILE_DEST=1; shift ;;
    -h|--help) usage 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage 2 ;;
  esac
done

step() { printf '
=== %s ===
' "$1"; }
fail() { printf 'ERROR: %s
' "$1" >&2; exit "${2:-1}"; }

# ---------------------------------------------------------------------------
# Preconditions
# ---------------------------------------------------------------------------
step "Preconditions"

command -v gsutil >/dev/null 2>&1 || fail "gsutil not found in PATH"
command -v python3 >/dev/null 2>&1 || fail "python3 not found"

if [[ -z "${BUCKET}" ]]; then
  cat >&2 <<'ERR_NO_BUCKET'
ERROR: no bucket given.

The upload target must be named explicitly -- this script never guesses a
bucket. Create one first if needed:

  gsutil mb -l us-central1 gs://phyto-companion-dev-public-data

then re-run:

  scripts/sync_public_archive_to_gcs.sh --bucket gs://phyto-companion-dev-public-data
ERR_NO_BUCKET
  exit 2
fi

DEST_IS_GCS=1
case "${BUCKET}" in
  gs://*) ;;
  file://*)
    [[ "${ALLOW_FILE_DEST}" -eq 1 ]] || fail       "bucket must be a gs:// URL (got: ${BUCKET}); local destinations require --allow-file-destination (testing only)" 1
    DEST_IS_GCS=0
    ;;
  *) fail "bucket must be a gs:// URL (got: ${BUCKET})" 1 ;;
esac

DESTINATION="${BUCKET}/${GCS_PREFIX}/${OBJECT_NAME}"

# ===========================================================================
# MODE 2: --from-url  (Cloud Shell / GCE only)
# ===========================================================================
if [[ "${FROM_URL}" -eq 1 ]]; then

  step "Cloud Shell mode (--from-url)"

  if [[ -z "${CLOUD_SHELL:-}" && "${ALLOW_FILE_DEST}" -ne 1 ]]; then
    cat >&2 <<'ERR_NOT_CLOUD_SHELL'
ERROR: --from-url is a CLOUD-ONLY mode and must not run on a local machine.

It exists so the 13.7 GB public archive is downloaded inside Google Cloud
(Cloud Shell or a GCE instance), uploaded to GCS, and hashed there -- never
stored on this MacBook.

Where to run it:
  1. Open Cloud Shell:  https://console.cloud.google.com/  ->  Activate Cloud Shell
  2. Set the project:   gcloud config set project phyto-companion-prod
  3. Run this same repository's script with:
       scripts/sync_public_archive_to_gcs.sh          --from-url --bucket gs://phyto-companion-dev-public-data --yes

Why this restriction exists: the split-plane rule forbids pulling multi-GB
data onto the development machine, and Cloud Shell has 5 GB of persistent
home storage (downloads go to ephemeral /tmp, which is discarded).
ERR_NOT_CLOUD_SHELL
    exit 5
  fi

  printf 'source_url:     %s
' "${PUBLIC_URL}"
  printf 'destination:    %s
' "${DESTINATION}"
  printf 'expected size:  %s bytes
' "${EXPECTED_SIZE_BYTES}"
  printf 'license:        %s (CC-BY-NC; no commercial use)
' "${LICENSE}"
  printf 'download target: ephemeral /tmp (never persistent storage)
'

  if [[ "${DRY_RUN}" -eq 1 ]]; then
    printf '
[dry-run] would download %s, upload to %s, then hash the remote object. Nothing fetched.
'       "${PUBLIC_URL}" "${DESTINATION}"
    exit 0
  fi

  if [[ "${ASSUME_YES}" -ne 1 ]]; then
    printf '
Download ~13.7 GB inside Cloud Shell and upload to %s ? [type "yes"] ' "${DESTINATION}"
    read -r CONFIRMATION
    [[ "${CONFIRMATION}" == "yes" ]] || { echo "Aborted by user." >&2; exit 4; }
  fi

  TMP_ARCHIVE="${TMPDIR:-/tmp}/npmrd_${OBJECT_NAME}"
  step "Downloading archive into ephemeral storage"
  curl --fail --location --progress-bar --output "${TMP_ARCHIVE}" "${PUBLIC_URL}"

  ACTUAL_SIZE="$(stat -c%s "${TMP_ARCHIVE}" 2>/dev/null || stat -f%z "${TMP_ARCHIVE}")"
  printf 'downloaded: %s bytes
' "${ACTUAL_SIZE}"
  if [[ -n "${EXPECTED_SIZE_BYTES}" && "${ACTUAL_SIZE}" != "${EXPECTED_SIZE_BYTES}" ]]; then
    fail "downloaded size ${ACTUAL_SIZE} differs from expected ${EXPECTED_SIZE_BYTES}; upstream archive may have been updated" 1
  fi

  step "Uploading to GCS"
  if [[ "${DEST_IS_GCS}" -eq 1 ]]; then
    gsutil -m cp "${TMP_ARCHIVE}" "${DESTINATION}"
    step "Hashing the UPLOADED object remotely (streamed; no local copy)"
    HASH_OUTPUT="$(gsutil hash -m "${DESTINATION}")"
    printf '%s
' "${HASH_OUTPUT}"
    REMOTE_MD5="$(printf '%s
' "${HASH_OUTPUT}" | awk -F': *' '/Hash \(md5\)/ {print $2; exit}')"
    SHA256="$(python3 -c "import hashlib; print(hashlib.sha256(open('${TMP_ARCHIVE}', 'rb').read()).hexdigest())")"
  else
    mkdir -p "$(dirname "${BUCKET#file://}/${GCS_PREFIX}/${OBJECT_NAME}")"
    cp "${TMP_ARCHIVE}" "${BUCKET#file://}/${GCS_PREFIX}/${OBJECT_NAME}"
    REMOTE_MD5=""
    SHA256="$(python3 -c "import hashlib; print(hashlib.sha256(open('${TMP_ARCHIVE}', 'rb').read()).hexdigest())")"
  fi

  step "Cleaning up ephemeral download"
  rm -f "${TMP_ARCHIVE}"
  printf 'removed %s
' "${TMP_ARCHIVE}"

  step "Writing manifest"
  DOWNLOAD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  mkdir -p "$(dirname "${MANIFEST}")"
  ACTUAL_SIZE="${ACTUAL_SIZE}"   REMOTE_MD5="${REMOTE_MD5}"   DESTINATION="${DESTINATION}"   DOWNLOAD_DATE="${DOWNLOAD_DATE}"   PUBLIC_URL="${PUBLIC_URL}"   LICENSE="${LICENSE}"   LICENSE_WARNING="${LICENSE_WARNING}"   ATTRIBUTION="${ATTRIBUTION}"   SHA256="${SHA256}"   MANIFEST="${MANIFEST}"   python3 - <<'PY_MANIFEST_URL'
import json, os, pathlib

m = {
    "dataset": "NP-MRD public raw NMR archive (experimental spectra)",
    "source_url": os.environ["PUBLIC_URL"],
    "license": os.environ["LICENSE"],
    "license_warning": os.environ["LICENSE_WARNING"],
    "attribution": os.environ["ATTRIBUTION"],
    "local_source": None,
    "download_date": os.environ["DOWNLOAD_DATE"],
    "sha256": os.environ["SHA256"],
    "size_bytes": int(os.environ["ACTUAL_SIZE"]),
    "gcs_uri": os.environ["DESTINATION"],
    "gcs_generation_md5": os.environ.get("REMOTE_MD5") or None,
    "upload_date_utc": os.environ["DOWNLOAD_DATE"],
    "uploaded_by": "scripts/sync_public_archive_to_gcs.sh (--from-url)",
    "integrity_note": "SHA256 computed during ephemeral cloud-side ingest; remote object stored in GCS.",
    "not_committed_to_git": True,
}
pathlib.Path(os.environ["MANIFEST"]).write_text(json.dumps(m, indent=2) + chr(10))
print(f"manifest written: {os.environ['MANIFEST']}")
PY_MANIFEST_URL

  step "Done"
  printf 'Uploaded: %s
' "${DESTINATION}"
  printf 'SHA256:   %s
' "${SHA256}"
  printf 'Manifest: %s
' "${MANIFEST}"
  exit 0
fi

# ===========================================================================
# MODE 1: default (local materialized archive)
# ===========================================================================

[[ -f "${SOURCE_ARCHIVE}" ]] || fail "archive not found: ${SOURCE_ARCHIVE}" 1

step "1. Materialization check"

SIZE_BYTES="$(stat -c%s "${SOURCE_ARCHIVE}" 2>/dev/null || stat -f%z "${SOURCE_ARCHIVE}")"
BLOCKS="$(stat -c%b "${SOURCE_ARCHIVE}" 2>/dev/null || stat -f%b "${SOURCE_ARCHIVE}")"
FILE_FLAGS="$(ls -lO "${SOURCE_ARCHIVE}" 2>/dev/null | awk '{print $5}')"

printf 'path:        %s
' "${SOURCE_ARCHIVE}"
printf 'size:        %s bytes
' "${SIZE_BYTES}"
printf 'blocks:      %s
' "${BLOCKS}"
printf 'flags:       %s
' "${FILE_FLAGS:-none}"

if [[ "${BLOCKS}" -eq 0 || "${FILE_FLAGS}" == *dataless* ]]; then
  cat >&2 <<EOF

ERROR: the archive is still a Google Drive placeholder (0 allocated blocks,
flags: ${FILE_FLAGS:-none}). Its bytes are not on this machine, so it cannot
be uploaded.

Materialize it ONCE, then re-run this script:

  Option A (Finder):  right-click experimental_spectra.zip
                      -> "Make available offline", wait for the 13.7 GB
                      download to finish.

  Option B (Cloud):   Run from Google Cloud Shell using --from-url:
                      scripts/sync_public_archive_to_gcs.sh --from-url \
                        --bucket ${BUCKET} --yes

Then verify it is materialized:
  stat -f "blocks=%b size=%z" "${SOURCE_ARCHIVE}"
blocks must be non-zero. Re-run this script afterwards.
EOF
  exit 3
fi

step "2. SHA256 (before upload)"
printf 'hashing (this can take a few minutes)...
'
SHA256="$(shasum -a 256 "${SOURCE_ARCHIVE}" | awk '{print $1}')"
printf 'sha256: %s
' "${SHA256}"

step "3. Upload plan"
printf 'source:      %s
' "${SOURCE_ARCHIVE}"
printf 'destination: %s
' "${DESTINATION}"
printf 'filter:      ONLY %s (everything else in workspace excluded)
' "${OBJECT_NAME}"
printf 'deletion:    NONE (no -d flag)
'

if [[ "${DRY_RUN}" -eq 1 ]]; then
  printf '
[dry-run] no upload performed
'
  exit 0
fi

if [[ "${ASSUME_YES}" -ne 1 ]]; then
  printf '
Upload 13.7 GB to %s ? [type "yes" to continue] ' "${DESTINATION}"
  read -r CONFIRMATION
  if [[ "${CONFIRMATION}" != "yes" ]]; then
    echo "Aborted by user." >&2
    exit 4
  fi
fi

step "4. Upload"
if [[ "${DEST_IS_GCS}" -eq 0 ]]; then
  mkdir -p "${BUCKET#file://}/${GCS_PREFIX}"
  RSYNC_DEST="${BUCKET}/${GCS_PREFIX}/"
else
  RSYNC_DEST="${BUCKET}/${GCS_PREFIX}"
fi
gsutil -m rsync -r -e -x '^(?!experimental_spectra\.zip$).*'   "$(dirname "${SOURCE_ARCHIVE}")" "${RSYNC_DEST}"

step "5. Verify uploaded object"
if [[ "${DEST_IS_GCS}" -eq 1 ]]; then
  REMOTE_SIZE="$(gsutil stat "${DESTINATION}" | awk -F': *' '/Content-Length|Size/ {print $2; exit}')"
  REMOTE_MD5="$(gsutil stat "${DESTINATION}" | awk -F': *' '/Hash \(md5\)/ {print $2; exit}')"
  printf 'remote size: %s bytes
' "${REMOTE_SIZE}"
  printf 'remote md5:  %s
' "${REMOTE_MD5}"
  if [[ -n "${REMOTE_SIZE}" && "${REMOTE_SIZE}" != "${SIZE_BYTES}" ]]; then
    fail "size mismatch after upload (local ${SIZE_BYTES} vs remote ${REMOTE_SIZE})" 1
  fi
else
  LOCAL_DEST="${BUCKET#file://}/${GCS_PREFIX}/${OBJECT_NAME}"
  REMOTE_SIZE="$(stat -c%s "${LOCAL_DEST}" 2>/dev/null || stat -f%z "${LOCAL_DEST}" 2>/dev/null || echo "")"
  REMOTE_MD5=""
  printf 'destination size: %s bytes (local filesystem destination)
' "${REMOTE_SIZE}"
  if [[ "${REMOTE_SIZE}" != "${SIZE_BYTES}" ]]; then
    fail "size mismatch after upload (local ${SIZE_BYTES} vs destination ${REMOTE_SIZE})" 1
  fi
fi

step "6. Manifest"
UPLOAD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
mkdir -p "$(dirname "${MANIFEST}")"
SIZE_BYTES="${SIZE_BYTES}" REMOTE_MD5="${REMOTE_MD5}" DESTINATION="${DESTINATION}" UPLOAD_DATE="${UPLOAD_DATE}" PUBLIC_URL="${PUBLIC_URL}" LICENSE="${LICENSE}" LICENSE_WARNING="${LICENSE_WARNING}" ATTRIBUTION="${ATTRIBUTION}" SHA256="${SHA256}" MANIFEST="${MANIFEST}" python3 - <<'PY_MANIFEST_LOCAL'
import json, os, pathlib

m = {
    "dataset": "NP-MRD public raw NMR archive (experimental spectra)",
    "source_url": os.environ["PUBLIC_URL"],
    "license": os.environ["LICENSE"],
    "license_warning": os.environ["LICENSE_WARNING"],
    "attribution": os.environ["ATTRIBUTION"],
    "local_path_note": (
        "Held in the public SUMMIT benchmark workspace on the owner's "
        "Google Drive (path redacted); not committed to git."
    ),
    "download_date": "unknown (public NP-MRD 'current' archive; date not recorded at download time)",
    "sha256": os.environ["SHA256"],
    "size_bytes": int(os.environ["SIZE_BYTES"]),
    "gcs_uri": os.environ["DESTINATION"],
    "gcs_generation_md5": os.environ.get("REMOTE_MD5") or None,
    "upload_date_utc": os.environ["UPLOAD_DATE"],
    "uploaded_by": "scripts/sync_public_archive_to_gcs.sh",
    "integrity_note": (
        "SHA256 recorded from local file before upload; compare against "
        "gsutil hash or vendor-published checksum before use."
    ),
    "not_committed_to_git": True,
}
pathlib.Path(os.environ["MANIFEST"]).write_text(json.dumps(m, indent=2) + chr(10))
print(f"manifest written: {os.environ['MANIFEST']}")
PY_MANIFEST_LOCAL

step "Done"
printf 'Uploaded: %s
' "${DESTINATION}"
printf 'SHA256:   %s
' "${SHA256}"
printf 'Manifest: %s
' "${MANIFEST}"
