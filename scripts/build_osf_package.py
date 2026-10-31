#!/usr/bin/env python3
"""Assemble the OSF acceptance-evidence package (ADR-0088).

Gathers exactly the ADR-0088 evidence subset into
``data/osf_package/`` with a MANIFEST.sha256, fail-closed on any missing
input. Run from the repository root:

    python3 scripts/build_osf_package.py

Outputs are written into ``data/osf_package/`` (gitignored). Upload of
that directory is a separate step performed by
``scripts/osf_register_evidence.sh`` after the owner supplies an OSF
token — this script never touches the network.
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
    "data/validated_runs/M1b/converted/20220426_UMETAB266_POS_Thiostreptone.mzML",
]

EXTRACT_ADRS = ["ADR-0085", "ADR-0086", "ADR-0087", "ADR-0088"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    # 1. Evidence subset, mirrored under osf/validated_runs/
    for rel in COPY_FILES:
        src = REPO / rel
        if not src.is_file():
            fail(f"missing evidence file {rel}")
        dst = OUT / "validated_runs" / rel.split("validated_runs/", 1)[1]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    # 2. ADR text (ADR-0085..0088 sections, copied from DECISIONS.md)
    decisions = (REPO / "docs" / "DECISIONS.md").read_text()
    for adr in EXTRACT_ADRS:
        start = decisions.index(f"### {adr}:")
        nxt = decisions.find("### ADR-", start + len(f"### {adr}:"))
        section = decisions[start : nxt if nxt != -1 else None].rstrip() + "\n"
        (OUT / "adr").mkdir(exist_ok=True)
        (OUT / "adr" / f"{adr}.md").write_text(section)

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
    (OUT / "dataset_reference.json").write_text(
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
        shutil.copy2(REPO / "CITATION.cff", OUT / "CITATION.cff")
    (OUT / "PROVENANCE.txt").write_text(
        f"Package commit: {commit}\n"
        f"Built from repository: phyto-companion\n"
        f"ADR basis: ADR-0085..0088 (see adr/)\n"
        f"Redaction: usernames, absolute home paths, and profile names "
        f"replaced with <redacted> markers before this package was built; "
        f"the redaction is recorded in each run_summary.json.\n"
    )

    # 5. MANIFEST.sha256 over every file in the package
    manifest = []
    for f in sorted(OUT.rglob("*")):
        if f.is_file() and f.name != "MANIFEST.sha256":
            manifest.append(f"{sha256(f)}  {f.relative_to(OUT)}")
    (OUT / "MANIFEST.sha256").write_text("\n".join(manifest) + "\n")

    print(f"package built: {OUT} ({len(manifest)} files)")


if __name__ == "__main__":
    main()
