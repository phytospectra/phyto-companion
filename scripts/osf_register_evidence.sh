#!/usr/bin/env bash
# OSF acceptance-evidence upload + embargoed registration (ADR-0088).
#
# This is a thin wrapper around scripts/osf_evidence.py, which owns all
# OSF interaction and every safety rule:
#   * OSF_TOKEN must be set — never contacted without it;
#   * --embargo YYYY-MM-DD is mandatory and must be in the future;
#   * read-only preflight (--preflight) must report a clean remote tree
#     before registration, and a live contract check (--verify-contract)
#     must be current;
#   * uploads are retry-safe and never silently overwrite differing files.
#
# Usage:
#   OSF_TOKEN=... OSF_NODE_ID=t628k scripts/osf_register_evidence.sh --preflight
#   OSF_TOKEN=... OSF_NODE_ID=t628k scripts/osf_register_evidence.sh --verify-contract
#   OSF_TOKEN=... OSF_NODE_ID=t628k scripts/osf_register_evidence.sh --embargo 2027-06-30

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "$REPO/scripts/osf_evidence.py" "$@"
