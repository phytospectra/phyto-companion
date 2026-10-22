"""ADR numbering safety check (reservation mechanism documented in
docs/adr/INDEX.md): the pytest entry point for
``scripts/check_adr_numbers.py``.

Fails on duplicate ADR definitions, definitions missing from the index,
and citations that are neither defined nor explicitly reserved.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "check_adr_numbers.py"

_spec = importlib.util.spec_from_file_location("check_adr_numbers", _SCRIPT)
check_adr_numbers = importlib.util.module_from_spec(_spec)
assert _spec is not None and _spec.loader is not None
_spec.loader.exec_module(check_adr_numbers)


def test_adr_numbers_are_consistent() -> None:
    """Every ADR citation resolves; no duplicates; index covers all."""
    problems = check_adr_numbers.check()
    assert problems == [], "\n".join(problems)


def test_index_exists_and_lists_definitions() -> None:
    index = (_ROOT / "docs" / "adr" / "INDEX.md").read_text(encoding="utf-8")
    assert "| ADR | Title | Status | Primary citing file |" in index
    definitions = check_adr_numbers.find_definitions()
    for number in definitions:
        assert f"| {number} |" in index, f"{number} missing from INDEX.md"