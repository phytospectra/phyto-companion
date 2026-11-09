# Synthetic model interpretation pilot

This is six original, public instruction-following contracts, not an identification
accuracy benchmark or an Antigravity Skills evaluation. The proposed scientific
comparison and tuning decision are in [RESEARCH-PLAN.md](RESEARCH-PLAN.md).

Prepare an immutable local plan (no network or key required):

```sh
python benchmark/agent_comparison/pilot.py plan --output /tmp/phyto-pilot-plan
```

Only after verifying the dedicated key's nonresetting cap (at most US$50), remaining
allowance and inclusion of BYOK usage, explicitly execute with `run` in place of
`plan`. Supply `OPENROUTER_API_KEY` through the environment, never command arguments
or source files. Output contains original synthetic prompts and generated responses;
keep execution artifacts outside public source control. No run was performed as
part of this implementation.

The runner has 18 fixed slots, serial dispatch, no retries, no restart replay,
persistent pending markers, provider fallback disabled, and bounded output tokens.
A US$1 local reservation estimate is not a hard billing limit; the provider key cap
is the separate spending guard. Existing usage reduces the available allowance.
Unknown costs, transport ambiguity, mismatched model identity or provider failures
stop dispatch. Reconcile stopped runs before preparing another plan. Hosted aliases
are mutable; record returned model/provider IDs rather than claiming pinned weights.

Tests use mocked HTTP only. Passing them says nothing about actual model performance,
measured chemistry accuracy, Colab GPU access or scientific validation.
