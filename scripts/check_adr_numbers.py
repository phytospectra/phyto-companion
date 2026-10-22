#!/usr/bin/env python3
"""ADR numbering safety check (ADR numbering note, docs/adr/INDEX.md).

Run standalone (`python3 scripts/check_adr_numbers.py`) or through pytest
(`tests/test_docs/test_adr_numbers.py`).  Fails on:

1. duplicate ADR-number *definitions* (`### ADR-NNNN:` headings) across
   all files under ``docs/``;
2. a definition that is missing from ``docs/adr/INDEX.md``;
3. an ADR number cited anywhere in ``docs/``, ``shared/``, ``services/``,
   ``tests/`` or ``scripts/`` that is neither defined nor explicitly
   listed as a reserved gap in the index.

The index is the reservation mechanism (option b in its header note):
``docs/DECISIONS.md`` stays the single append-only log; skipped/reserved
IDs (0019-0023, 0041-0042 historically) are listed there as
"reserved (gap)" rows and are the ONLY numbers allowed to be cited
without a definition.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

DEFINITION_RE = re.compile(r"^### (ADR-0\d{3}):", re.MULTILINE)
CITATION_RE = re.compile(r"ADR-0\d{3}")

SCAN_DIRS = ("docs", "shared", "services", "tests", "scripts")
SCAN_SUFFIXES = {".py", ".md", ".json", ".toml", ".yaml", ".yml"}


def find_definitions() -> dict[str, list[str]]:
    """ADR number -> files defining it (### ADR-NNNN: headings)."""
    found: dict[str, list[str]] = {}
    for md in sorted((REPO_ROOT / "docs").rglob("*.md")):
        rel = md.relative_to(REPO_ROOT).as_posix()
        for match in DEFINITION_RE.finditer(md.read_text(encoding="utf-8")):
            found.setdefault(match.group(1), []).append(rel)
    return found


def find_citations() -> dict[str, list[str]]:
    """ADR number -> files citing it anywhere in the scanned trees."""
    found: dict[str, list[str]] = {}
    for scan_dir in SCAN_DIRS:
        base = REPO_ROOT / scan_dir
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix not in SCAN_SUFFIXES or not path.is_file():
                continue
            # never scan build junk
            if any(part in {"__pycache__", "node_modules", ".venv"} for part in path.parts):
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            for match in CITATION_RE.finditer(path.read_text(encoding="utf-8", errors="replace")):
                found.setdefault(match.group(0), []).append(rel)
    return found


def reserved_from_index(index_text: str) -> set[str]:
    """Numbers whose INDEX.md row carries the 'reserved (gap)' status."""
    reserved = set()
    for line in index_text.splitlines():
        if line.startswith("| ADR-") and "reserved (gap)" in line:
            match = re.match(r"\| (ADR-0\d{3}) \|", line)
            if match:
                reserved.add(match.group(1))
    return reserved


def check() -> list[str]:
    index_path = REPO_ROOT / "docs" / "adr" / "INDEX.md"
    problems: list[str] = []
    if not index_path.exists():
        return ["docs/adr/INDEX.md is missing"]

    definitions = find_definitions()
    citations = find_citations()
    reserved = reserved_from_index(index_path.read_text(encoding="utf-8"))
    indexed = set(re.findall(r"^\| (ADR-0\d{3}) \|", index_path.read_text(encoding="utf-8"), re.MULTILINE))

    # 1. no duplicate definitions
    for number, files in sorted(definitions.items()):
        if len(files) > 1:
            problems.append(
                f"duplicate definition {number} in: {', '.join(sorted(files))}"
            )

    # 2. every definition is in the index
    for number in sorted(definitions):
        if number not in indexed:
            problems.append(f"{number} is defined but missing from docs/adr/INDEX.md")

    # 3. every citation resolves to a definition or an explicit reservation
    for number, files in sorted(citations.items()):
        if number in definitions:
            continue
        if number in reserved:
            continue
        problems.append(
            f"{number} is cited ({', '.join(sorted(set(files))[:3])}) but "
            "neither defined in docs/ nor reserved in docs/adr/INDEX.md"
        )

    return problems


def main() -> int:
    problems = check()
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}", file=sys.stderr)
        return 1
    print("ADR numbering check: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
