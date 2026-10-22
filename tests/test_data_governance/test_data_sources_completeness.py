"""CI completeness check (ADR-0056, Part 7): every source dataset string
used in services/ and shared/ must have a governance record (Licence /
Allowed tiers / Commercial use / Source dataset ID) in
docs/DATA_SOURCES.md.

A new source integrated in code without a DATA_SOURCES.md row is a
documentation gap and fails this test rather than shipping unlicensed.
"""

from __future__ import annotations

import re
from pathlib import Path

from phyto_shared.data_governance import SOURCE_DATASET_IDS

_ROOT = Path(__file__).resolve().parents[2]
_DOC = _ROOT / "docs" / "DATA_SOURCES.md"

_GOV_FIELDS = ("Licence", "Allowed tiers", "Commercial use", "Source dataset ID")


def _doc_text() -> str:
    return _DOC.read_text(encoding="utf-8")


def _source_strings_used_in_code() -> set[str]:
    """Every identifier that looks like a source dataset id in the codebase.

    The closed vocabulary lives in ``SOURCE_DATASET_IDS``; this test also
    greps the code for its members to prove each one is actually governed
    by the doc, and reports code-introduced strings via the shared module.
    """
    used: set[str] = set(SOURCE_DATASET_IDS)
    pattern = re.compile(
        r"\"(user_upload|in_house_standard|gnps_public|np_mrd|np_mrd_fixtures|"
        r"bmrb|nmrshiftdb2|coconut|lotus|knapsack|npass|gbif|itis)\""
    )
    for base in ("services", "shared"):
        for path in (_ROOT / base).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            used.update(pattern.findall(path.read_text(encoding="utf-8", errors="ignore")))
    return used


def test_every_source_dataset_id_has_a_document_row():
    doc = _doc_text()
    missing = [
        source
        for source in sorted(_source_strings_used_in_code())
        if source not in doc
    ]
    assert not missing, (
        f"source dataset id(s) {missing} used in code but missing from "
        f"docs/DATA_SOURCES.md -- add a governance record (Licence, Allowed "
        f"tiers, Commercial use, Source dataset ID) before shipping"
    )


def test_documented_sources_carry_all_governance_fields():
    doc = _doc_text()
    for source in SOURCE_DATASET_IDS:
        # Locate the governance table row mentioning this source and check
        # the four fields appear in its vicinity (same section).
        idx = doc.find(source)
        assert idx != -1, f"{source} has no DATA_SOURCES.md row"
        section = doc[max(0, idx - 2000) : idx + 2000]
        for field in _GOV_FIELDS:
            assert field in section, (
                f"DATA_SOURCES.md section for {source!r} lacks the "
                f"{field!r} governance field"
            )


def test_np_mrd_row_states_non_commercial_terms():
    doc = _doc_text().lower()
    npmrd_idx = doc.find("np_mrd")
    section = doc[max(0, npmrd_idx - 3000) : npmrd_idx + 3000]
    assert "cc-by-nc" in section
    assert "commercial use | **no**" in section


def test_document_is_the_only_declared_source_of_truth():
    # Guard against the doc being moved without updating the CI check.
    assert _DOC.exists()
    assert "single source of truth" in _doc_text()