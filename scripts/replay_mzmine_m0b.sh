#!/bin/sh
# Replay the M0-b / M1-a recorded MZmine live-integration run (ADR-0085).
#
# Prerequisites:
#   1. MZMINE_HOME   -> the pinned MZmine installation
#                       (tools/mzmine-4.11.0 or tools/mzmine-4.10.90 in
#                       this workspace, or any install reporting the
#                       same version).
#   2. MZMINE_USER_FILE -> an .mzuser4 profile.  MZmine 4.x batch mode
#      is login-gated (measured on 4.11.0, 4.10.90, 4.10.6); create one
#      interactively ONCE with:
#         "$MZMINE_HOME/mzmine.app/Contents/MacOS/mzmine" -login
#      (browser sign-in with an mzio account) -- the file lands in
#      ~/.mzmine/users/ and is machine-locked.
#   3. The dataset (already verified in-repo):
#         data/raw/MSV000090047/20220426_UMETAB266_POS_Tetracycline.mzML
#      MD5  396f11793f20870de150b56408a33c21   (Zenodo 6948449 record)
#      SHA-256 fa3910452ae96f754a3eb05b2cf07ae2993f053fa4862b78d365cf1d857f6b9e
#
# Replay (the platform's normal entry point):
#
#   export MZMINE_HOME="/path/to/mzmine-4.11.0"
#   export MZMINE_USER_FILE="$HOME/.mzmine/users/<your>.mzuser4"
#   .venv/bin/python -m pytest tests/test_metabolomics/test_mzmine_live_integration.py -q
#
# Or through the HTTP surface, exactly as a client would (REAL mode,
# not the mock -- do NOT set PHYTO_DRY_RUN here):
#
#   PHYTO_ENV=cloud PHYTO_DRY_RUN=0 \
#   MZMINE_HOME=/path/to/mzmine-4.11.0 \
#   MZMINE_USER_FILE="$HOME/.mzmine/users/<your>.mzuser4" \
#     .venv/bin/python -m uvicorn --app-dir services/metabolomics \
#     app.main:create_app --factory
#   curl -s localhost:8000/metabolomics/detect -H 'content-type: application/json' \
#     -d '{"mzml_filenames":["data/raw/MSV000090047/20220426_UMETAB266_POS_Tetracycline.mzML"]}'
#   (PHYTO_ENV=cloud with PHYTO_DRY_RUN=0 constructs MetabolomicsConfig.from_env()
#    and validates it eagerly at first request, so a missing MZMINE_HOME or
#    MZMINE_USER_FILE fails closed instead of silently serving the mock.)
set -eu
: "${MZMINE_HOME:?set MZMINE_HOME to the pinned MZmine installation}"
: "${MZMINE_USER_FILE:?set MZMINE_USER_FILE to your .mzuser4 profile}"
cd "$(dirname "$0")/.."
exec .venv/bin/python -m pytest tests/test_metabolomics/test_mzmine_live_integration.py -q "$@"
