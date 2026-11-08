#!/usr/bin/env python3
"""Assemble the OSF acceptance-evidence package (ADR-0088).

Gathers exactly the ADR-0088 evidence subset into
``data/osf_package/`` with a MANIFEST.sha256, fail-closed on any missing
input. Run from the repository root:

    python3 scripts/build_osf_package.py

The build happens in ``data/osf_package.staging/``. The staging tree is
checked against ``ALLOWED_TREE`` and every checksum re-verified before the
old ``data/osf_package/`` is removed and the staging directory renamed
into place. A file that is not on the allowlist can therefore never
survive from a previous build.

Third-party instrument data (MassIVE MSV000090047 mzML/.raw) is never
copied here: ADR-0088 keeps it by reference (accession + checksum) in
``dataset_reference.json``.

This script never touches the network. Upload and registration are
separate, gated steps in ``scripts/osf_register_evidence.sh``.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "osf_package"
STAGING = REPO / "data" / "osf_package.staging"

# ADR-0088 evidence subset: platform-owned output only. Third-party
# instrument data (mzML/.raw) is referenced by accession + checksum, not
# copied.
COPY_FILES = [
    "data/validated_runs/M1a/run_summary.json",
    "data/validated_runs/M1a/batch.mzbatch",
    "data/validated_runs/M1a/features.csv",
    "data/validated_runs/M1a/mzmine_run.log",
    "data/validated_runs/M1a/api_detect_response.json",
    "data/validated_runs/M1b/run_summary.json",
    "data/validated_runs/M1b/batch.mzbatch",
    "data/validated_runs/M1b/features.csv",
    "data/validated_runs/M1b/mzmine_run.log",
]

EXTRACT_ADRS = ["ADR-0085", "ADR-0086", "ADR-0087", "ADR-0088"]

#: The complete, explicit package tree. Nothing else may exist in the
#: built package. Keep this in sync deliberately: this is the allowlist
#: the remote preflight compares against.
ALLOWED_TREE = frozenset({
    "CITATION.cff",
    "PROVENANCE.txt",
    "MANIFEST.sha256",
    "dataset_reference.json",
    "adr/ADR-0085.md",
    "adr/ADR-0086.md",
    "adr/ADR-0087.md",
    "adr/ADR-0088.md",
    "validated_runs/M1a/api_detect_response.json",
    "validated_runs/M1a/batch.mzbatch",
    "validated_runs/M1a/features.csv",
    "validated_runs/M1a/mzmine_run.log",
    "validated_runs/M1a/run_summary.json",
    "validated_runs/M1b/batch.mzbatch",
    "validated_runs/M1b/features.csv",
    "validated_runs/M1b/mzmine_run.log",
    "validated_runs/M1b/run_summary.json",
})

#: Extensions that must never appear in the package (bulk instrument
#: data is third-party and is referenced, not redistributed).
FORBIDDEN_SUFFIXES = (".mzml", ".raw", ".wiff", ".baf", ".d")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def verify_tree(root: Path) -> list[str]:
    """Return the sorted relative paths of every file under ``root``."""
    return sorted(
        str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()
    )


def main() -> None:
    # A clean staging directory is the only place this build writes, so
    # a stale file from an earlier build cannot be carried forward.
    if STAGING.exists():
        shutil.rmtree(STAGING)
    STAGING.mkdir(parents=True)

    # 1. Evidence subset, mirrored under validated_runs/
    for rel in COPY_FILES:
        src = REPO / rel
        if not src.is_file():
            fail(f"missing evidence file {rel}")
        dst = STAGING / "validated_runs" / rel.split("validated_runs/", 1)[1]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    # 2. ADR text (ADR-0085..0088 sections, copied from DECISIONS.md)
    decisions = (REPO / "docs" / "DECISIONS.md").read_text()
    for adr in EXTRACT_ADRS:
        start = decisions.index(f"### {adr}:")
        nxt = decisions.find("### ADR-", start + len(f"### {adr}:"))
        section = decisions[start : nxt if nxt != -1 else None].rstrip() + "\n"
        (STAGING / "adr").mkdir(exist_ok=True)
        (STAGING / "adr" / f"{adr}.md").write_text(section)

    # 3. Dataset reference: MassIVE accession + checksums, no bulk data
    reference = {
        "dataset": "MSV000090047 (MassIVE, UCSD; public)",
        "landing_page": "https://massive.ucsd.edu/ProteoSAFe/dataset.jsp?accession=MSV000090047",
        "usage": ("third-party instrument data used for acceptance testing "
                  "only; not redistributed in this package. CC-BY-4.0 "
                  "attribution duties respected by reference."),
        "files": [],
    }
    for name in [
        "20220426_UMETAB266_POS_Tetracycline.mzML",
        "20220426_UMETAB266_POS_Thiostreptone.raw",
    ]:
        p = REPO / "data" / "raw" / "MSV000090047" / name
        if not p.is_file():
            fail(f"missing referenced dataset file {name}")
        reference["files"].append(
            {"name": name, "sha256": sha256(p), "size_bytes": p.stat().st_size}
        )
    (STAGING / "dataset_reference.json").write_text(
        json.dumps(reference, indent=2) + "\n"
    )

    # 4. Commit provenance: exact SHA this package documents
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
            text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        fail("git rev-parse failed — package must record a commit SHA")
    if REPO.joinpath("CITATION.cff").is_file():
        shutil.copy2(REPO / "CITATION.cff", STAGING / "CITATION.cff")
    else:
        fail("CITATION.cff missing — the package must carry its citation record")
    (STAGING / "PROVENANCE.txt").write_text(
        f"Package commit: {commit}\n"
        f"Built from repository: phyto-companion\n"
        f"ADR basis: ADR-0085..0088 (see adr/)\n"
        f"Third-party data: referenced by accession + checksum in "
        f"dataset_reference.json; never copied into this package.\n"
        f"Redaction: usernames, absolute home paths, and profile names "
        f"replaced with <redacted> markers before this package was built; "
        f"the redaction is recorded in each run_summary.json.\n"
    )

    # 5. MANIFEST.sha256 over every file in the staging tree
    manifest = []
    for f in sorted(STAGING.rglob("*")):
        if f.is_file() and f.name != "MANIFEST.sha256":
            manifest.append(f"{sha256(f)}  {f.relative_to(STAGING)}")
    (STAGING / "MANIFEST.sha256").write_text("\n".join(manifest) + "\n")

    # 6. Verify the tree against the explicit allowlist, then re-verify
    #    every checksum in the manifest before publishing.
    actual = set(verify_tree(STAGING))
    unexpected = sorted(actual - ALLOWED_TREE)
    missing = sorted(ALLOWED_TREE - actual)
    if unexpected or missing:
        fail(
            "staged tree does not match ALLOWED_TREE "
            f"(unexpected={unexpected}, missing={missing})"
        )
    for rel in sorted(actual):
        if rel.lower().endswith(FORBIDDEN_SUFFIXES):
            fail(f"forbidden bulk/third-party data in package: {rel}")

    for line in (STAGING / "MANIFEST.sha256").read_text().splitlines():
        if not line.strip():
            continue
        digest, _, rel = line.partition("  ")
        target = STAGING / rel
        if not target.is_file():
            fail(f"manifest names a missing file: {rel}")
        if sha256(target) != digest:
            fail(f"checksum mismatch while publishing: {rel}")

    # 7. Publish: remove the old directory first so nothing can survive.
    if OUT.exists():
        shutil.rmtree(OUT)
    STAGING.rename(OUT)

    print(f"package built: {OUT} ({len(actual)} files)")
    print("verified against ALLOWED_TREE and MANIFEST.sha256")


if __name__ == "__main__":
    main()
