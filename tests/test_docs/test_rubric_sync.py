"""The packaged prompt rubrics must stay byte-identical to the root
copies: the root file is the human-editable source, the shared copy is
what service containers (built from services/*/Dockerfile, which copy
only shared/ + the service app) ship inside the image.

Also pins the QA-critic rubric wiring: elucidation/nmr_preproc outputs
are judged against the rubric, other tools are not.
"""

from __future__ import annotations

from pathlib import Path

from phyto_shared.rubrics import load_rubric

_ROOT = Path(__file__).resolve().parents[2]


def test_elucidation_rubric_in_sync_with_root_prompt() -> None:
    root = _ROOT / "PROMPT_nmr_structure_elucidation.md"
    packaged = _ROOT / "shared" / "phyto_shared" / "rubrics" / "elucidation.md"
    assert root.read_text() == packaged.read_text(), (
        "PROMPT_nmr_structure_elucidation.md and its packaged rubric copy "
        "have drifted — edit the root file, then copy it into "
        "shared/phyto_shared/rubrics/"
    )


def test_load_rubric_returns_text() -> None:
    text = load_rubric("elucidation")
    assert "Data ingestion (hard rules)" in text
    assert "Regression tests" in text


def test_load_rubric_missing_raises() -> None:
    import pytest

    with pytest.raises(FileNotFoundError):
        load_rubric("no-such-rubric")


def test_critic_uses_rubric_for_elucidation_tools_only() -> None:
    from pydantic import BaseModel

    from orchestrator.qa_critic_agent import QACriticAgent, _RUBRIC_TOOL_PREFIXES

    class _Output(BaseModel):
        text: str = "sample output"

    captured: list[tuple[str, str]] = []

    class SpyLLM:
        def complete(self, system_prompt: str, user_prompt: str) -> str:
            captured.append((system_prompt, user_prompt))
            return "PASS"

    agent = QACriticAgent(llm=SpyLLM())
    agent._llm_judge_check("elucidation.rank_candidates", _Output())
    agent._llm_judge_check("llm.hypothesize", _Output())
    assert len(captured) == 2
    rubric_prompt, _ = captured[0]
    plain_prompt, _ = captured[1]
    assert "structure-elucidation rubric" in rubric_prompt
    assert "Never claim MSI Level 1" in rubric_prompt
    assert "structure-elucidation rubric" not in plain_prompt
    assert ("elucidation.", "nmr_preproc.") == _RUBRIC_TOOL_PREFIXES
