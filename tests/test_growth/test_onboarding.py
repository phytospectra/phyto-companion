"""Smoke tests for the guided first-story flow (ADR-0045).

The observable contract: the flow is an ordered sequence whose live token cost
fits inside the 50-token free allowance, and it includes elucidation plus at
least one LLM step.

The last test pins the actual total against the pricing table rather than a
hardcoded figure: the brief's illustrative "37 tokens" predates the frozen
price table, and the code derives 41. Asserting the invariant (fits in the
free tier, with a retry buffer) is what the ADR actually promises.
"""

from __future__ import annotations

from orchestrator.onboarding import first_story_steps, first_story_summary
from phyto_shared.plans import plan_features


class TestFirstStory:
    def test_returns_an_ordered_sequence_of_steps(self):
        steps = first_story_steps()
        assert len(steps) >= 5
        assert [step["step"] for step in steps] == list(range(1, len(steps) + 1))
        for step in steps:
            assert step["operation"]
            assert step["label"]
            assert isinstance(step["tokens"], int)

    def test_total_cost_fits_the_free_allowance(self):
        summary = first_story_summary()
        assert summary["monthly_allowance"] == 50
        assert summary["total_tokens"] <= 50
        assert summary["fits_in_free_tier"] is True
        assert summary["retry_buffer"] == 50 - summary["total_tokens"]

    def test_actual_total_is_41_tokens_with_nine_for_retries(self):
        """The documented sequence costs 41 of 50, leaving a 9-token buffer.

        This differs from the brief's illustrative 37/13 because step costs are
        derived from ROUTE_PRICING; if prices change, this assertion should
        change with them (and the invariant test above must still hold).
        """
        summary = first_story_summary()
        assert summary["total_tokens"] == 41
        assert summary["retry_buffer"] == 9

    def test_includes_elucidation_and_an_llm_step(self):
        operations = {step["operation"] for step in first_story_steps()}
        assert "run_elucidation" in operations
        assert "request_llm_explanation" in operations

    def test_evidence_reads_and_citation_are_free(self):
        by_operation = {step["operation"]: step for step in first_story_steps()}
        assert by_operation["view_evidence"]["tokens"] == 0
        assert by_operation["cite_and_share"]["tokens"] == 0

    def test_step_costs_match_the_pricing_table(self):
        from phyto_shared.token_pricing import cost_for_route

        for step in first_story_steps():
            expected = cost_for_route(step["method"], step["route"])
            assert expected is not None, f"{step['method']} {step['route']} is unpriced"
            assert step["tokens"] == expected.tokens

    def test_allowance_matches_the_free_plan(self):
        assert first_story_summary()["monthly_allowance"] == (
            plan_features("free").monthly_tokens
        )
