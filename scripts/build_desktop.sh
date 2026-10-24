#!/usr/bin/env bash
#
# Build the Phytospectra Desktop app (PyInstaller, one-folder bundle).
#
#   scripts/build_desktop.sh            # current platform -> dist/Phytospectra
#   scripts/build_desktop.sh --onefile  # single executable
#
# The report engine (services/nmr_preproc) is vendored into
# desktop/report_engine/ so the frozen app can render the DOCX report and
# the zipped bundle.  Only pure Python is vendored; the scientific stack
# comes from the build venv.
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DESKTOP_DIR="$REPO_ROOT/desktop"
PY="${PYTHON:-python3}"
PY="$REPO_ROOT/$PY"
[[ -x "$PY" ]] || PY="${PYTHON:-python3}"

cd "$DESKTOP_DIR"

echo "== vendoring report engine =="
rm -rf report_engine
mkdir -p report_engine/nmr_preproc
cp "$REPO_ROOT"/services/nmr_preproc/app/*.py report_engine/nmr_preproc/
touch report_engine/nmr_preproc/__init__.py

echo "== ensuring build deps =="
if ! "$PY" -c "import PyInstaller, docx, numpy, scipy, matplotlib" 2>/dev/null; then
  "$PY" -m pip install --quiet --upgrade pyinstaller python-docx numpy scipy matplotlib 2>/dev/null \
    || "$PY" -m pip install --quiet --user --upgrade pyinstaller python-docx numpy scipy matplotlib \
    || { echo "Install pyinstaller, python-docx, numpy, scipy, matplotlib into the build interpreter"; exit 1; }
fi

echo "== building =="
if [[ "${1:-}" == "--onefile" ]]; then
  "$PY" -m PyInstaller --clean -y --onefile \
    --name Phytospectra \
    --paths . --paths report_engine \
    --add-data "report_engine/nmr_preproc$( [[ "$(uname)" == Darwin ]] && echo ':' || echo ';' )nmr_preproc" \
    --hidden-import nmr_preproc --hidden-import nmr_preproc.report \
    __main__.py
else
  "$PY" -m PyInstaller --clean -y Phytospectra.spec
fi

echo "== done =="
ls -la dist/ | head -5
echo "App bundle: $(pwd)/dist/Phytospectra/"
