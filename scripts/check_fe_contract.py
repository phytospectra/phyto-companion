#!/usr/bin/env python3
"""Frontend ↔ backend contract check (field parity + pinned type shapes).

Run standalone (``python3 scripts/check_fe_contract.py``) or through the
repo health script, which auto-executes every ``scripts/check_*.py``.

What it checks
--------------
1. **Field parity** — for each backend pydantic model mapped below to a
   frontend interface, every backend field must appear in the frontend
   interface. Fields the frontend intentionally defers are listed in
   ``ALLOWED_FE_GAPS`` so the check fails on NEW drift, not on the
   documented pass-2 backlog (see HANDOFF_frontend_gap_audit.md and
   HANDOFF_frontend_followup_pass1.md).
2. **Pinned type shapes** — the three silent-corruption bugs fixed in
   follow-up pass 1 are pinned by signature grep so a regression (e.g.
   ``tied_candidates: number`` again) fails immediately:
   - ``CandidateRank.tied_candidates`` must be ``string[]``
   - ``Candidate.formula`` must be ``{ counts: ... }`` (MolecularFormula)
   - ``CandidateRank.margin`` must be ``number | null``

The backend is parsed from ``shared/phyto_shared/models.py`` with a
line-oriented scanner over ``Field``/annotated assignments — pydantic is
not imported, so the script runs with the stdlib alone.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BE_MODELS = REPO_ROOT / "shared" / "phyto_shared" / "models.py"
FE_TYPES = REPO_ROOT / "frontend" / "src" / "lib" / "phyto" / "types.ts"

# backend class -> (frontend interface name, backend fields the FE may
# legitimately omit in this demo build). Everything NOT listed here must
# exist in the FE interface. When pass 2 lands a field, move it out of the
# allowlist — the checker then enforces it.
MODEL_MAP: dict[str, tuple[str, set[str]]] = {
    "Candidate": (
        "Candidate",
        {
            # Backend-optional (default None); the FE renders evidence from
            # the rank level (CandidateRank.evidence_graph), so the FE
            # Candidate interface omits it until a page needs it.
            "evidence_graph",
        },
    ),
    "CandidateRank": (
        "CandidateRank",
        {
            # Pass-2 charter (frontend gap audit): these backend fields are
            # not yet rendered by any page. The FE interface omits them
            # until the pages that need them land.
            "evidence_profile",
            "taxon_prior_match",
            "taxon_prior_provenance",
            "isolation_evidence",
            "isolation_bonus",
            "level_1_match",
            "rt_offset_applied",
            "uv_evidence",
            "uv_bonus",
            "rt_prediction",
            "rt_bonus",
            "ri_used",
            "purity_score",
            "classification_chain",
            "evidence_chain",
        },
    ),
}

# Pinned post-pass-1 FE signatures: (label, regex that MUST match types.ts)
PINNED_SHAPES: list[tuple[str, str]] = [
    (
        "CandidateRank.tied_candidates is string[] (backend list[str])",
        r"tied_candidates\s*:\s*string\[\]",
    ),
    (
        "Candidate.formula is MolecularFormula-shaped ({ counts })",
        r"formula\s*:\s*\{\s*counts\s*:\s*Record\s*<\s*string\s*,\s*number\s*>\s*\}",
    ),
    (
        "CandidateRank.margin is number | null (backend float | None, may be inf)",
        r"margin\s*:\s*number\s*\|\s*null",
    ),
]

FIELD_RE = re.compile(r"^\s{4}([a-z_][a-z0-9_]*)\s*:", re.MULTILINE)
TS_FIELD_RE = re.compile(r"^\s{2}([a-z_][a-z0-9_]*)\s*\??\s*:", re.MULTILINE)


def pydantic_fields(src: str, class_name: str) -> list[str]:
    """Field names of a top-level class, up to the next top-level class."""
    m = re.search(rf"^class {class_name}\(", src, re.MULTILINE)
    if not m:
        raise SystemExit(f"backend class {class_name} not found in {BE_MODELS}")
    body = src[m.end():]
    nxt = re.search(r"^class [A-Z]", body, re.MULTILINE)
    body = body[: nxt.start()] if nxt else body
    return FIELD_RE.findall(body)


def ts_interface_fields(src: str, interface_name: str) -> list[str]:
    m = re.search(rf"^export interface {interface_name} \(", src, re.MULTILINE) or \
        re.search(rf"^export interface {interface_name} \{{", src, re.MULTILINE)
    if not m:
        raise SystemExit(
            f"frontend interface {interface_name} not found in {FE_TYPES}"
        )
    body = src[m.end():]
    nxt = re.search(r"^export (interface|type) [A-Z]", body, re.MULTILINE)
    body = body[: nxt.start()] if nxt else body
    return TS_FIELD_RE.findall(body)


def main() -> int:
    if not BE_MODELS.exists() or not FE_TYPES.exists():
        print(f"FAIL: missing {BE_MODELS} or {FE_TYPES}")
        return 2
    be_src = BE_MODELS.read_text()
    fe_src = FE_TYPES.read_text()

    failures: list[str] = []
    notes: list[str] = []

    for be_cls, (fe_iface, allowed_gaps) in MODEL_MAP.items():
        be_fields = set(pydantic_fields(be_src, be_cls))
        fe_fields = set(ts_interface_fields(fe_src, fe_iface))
        missing = sorted(be_fields - fe_fields - allowed_gaps)
        unexpected_gaps = sorted(
            f for f in allowed_gaps if f in fe_fields
        )
        if unexpected_gaps:
            notes.append(
                f"{be_cls}: fields {unexpected_gaps} are now in the FE "
                "interface — remove them from ALLOWED_FE_GAPS so this "
                "check enforces them"
            )
        if missing:
            failures.append(
                f"{be_cls}: FE interface {fe_iface} lacks backend field(s): "
                + ", ".join(missing)
                + "  (add to the interface or, if intentionally deferred, "
                "to ALLOWED_FE_GAPS in scripts/check_fe_contract.py)"
            )
        covered = sorted(be_fields & fe_fields)
        notes.append(f"{be_cls}: {len(covered)}/{len(be_fields)} backend fields mirrored in FE")

    for label, pattern in PINNED_SHAPES:
        if not re.search(pattern, fe_src):
            failures.append(f"pinned shape regressed: {label}")

    for n in notes:
        print(f"  note: {n}")
    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("OK: frontend contract types match the backend surface "
          "(allowlisted pass-2 gaps excluded; pinned shapes intact)")
    return 0


if __name__ == "__main__":
    sys.exit(main())