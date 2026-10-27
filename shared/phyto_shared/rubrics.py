"""Packaged prompt rubrics for the platform's LLM-assisted checks.

The repository root holds the human-editable system prompt
(``PROMPT_nmr_structure_elucidation.md``).  Service containers are built
from ``services/*/Dockerfile`` which copy only ``shared/`` plus their own
app code, so the prompt is mirrored into ``shared/phyto_shared/rubrics/``
for in-image use.  ``tests/test_docs/test_rubric_sync.py`` keeps the two
copies byte-identical — edit the root file, then copy it here.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["load_rubric"]

_RUBRICS_DIR = Path(__file__).resolve().parent / "rubrics"


def load_rubric(name: str) -> str:
    """Return the text of the packaged rubric ``name`` (e.g. 'elucidation')."""
    path = _RUBRICS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"rubric not packaged: {name} ({path})")
    return path.read_text()
