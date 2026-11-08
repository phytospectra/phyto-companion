# Phytospectra v0.1.0a1 — research preview

Release preparation only. No GitHub release or tag has been created.

## Proposed release identity

- Repository: https://github.com/phytospectra/phyto-companion (public).
- Exact source target: `57c45541c6907a831f7f8ec72dc06b6918187239`.
- Source branch: `agent/phase0a-phase1a-failclosed-runtime`, not default `main`.
- Proposed tag: `v0.1.0a1`.
- Proposed title: `Phytospectra v0.1.0a1 — research preview`.
- Proposed state: draft and prerelease, not production release.
- Package metadata currently remains `0.1.0`; the proposed tag is not an assertion that package versions have been changed.

## Features and evidence included

- Fail-closed elucidation configuration, structure-library integration, model-identity provenance and authentication contract coverage.
- Recorded MZmine feature-detection and Thermo raw-conversion acceptance evidence.
- MS/MS assessments and OSF evidence packaging utilities.
- Blinded-pilot packaging, scoring and repair tests, plus an unexecuted fine-tuning template and decision gate.

## Verified local checks, 2026-11-08

- Full pytest: 1,641 passed, four skipped, 14 warnings.
- Focused OSF/benchmark tests: 49 passed.
- Authentication/runtime/structure-database tests: 222 passed.
- Benchmark repair harness: 15 checks, zero failures.
- CI-scoped Ruff passed; repository-wide Ruff reported six findings in benchmark scripts/notebook.

See `RELEASE-AUTH-READINESS-2026-11-08.md` for commands, scope and limitations. These are local checks, not a successful GitHub CI run on the feature commit.

## Important limitations

This is research software, not a validated structure-identification system. No comparative model accuracy, fine-tuning result, performance guarantee, or production-readiness guarantee is claimed.

- Current source requires `PHYTO_SCIENTIFIC_EXECUTION=mock|real` in every environment, subject to final integration on `main`. Missing/invalid configuration fails closed locally and in cloud. Auth `PHYTO_DRY_RUN` is independent, and `dry_run:false` does not prove real execution.
- Real mode supports only `reference_hsqc_identify` / `reference_hsqc.rank`, using local `FileStructureDB` plus `match_hsqc` and operator-supplied structure CSV/reference JSON. It never falls back to a mock predictor or LLM. Other workflows, registry tools, MS providers, and LLM workflows are not supported by this real executor.
- Reference `candidate_id` must match the normalized structure name. `provenance:"experimental"` is curator-asserted, not software-verified; sources and reuse licenses require independent review. No reference library or license is bundled.
- Real work is synchronous, local, and bounded (8 MiB per file, at most 1000 structures, 256 reference records, 256 peaks per record and 8192 total, and 256 observed peaks). It has no provider network timeout, retry, or nested ledger. Lower matcher distance is better, not probability or calibrated confidence. Output is incomplete evidence and ends at a mandatory human checkpoint; approval is not scientific validation.
- Process health, configuration validity, and dependency availability do not imply scientific readiness: `scientific_ready` remains `false`. The real acceptance target `tests/test_real_orchestrator.py` is pending and no result is claimed; offline auth injection would not prove cloud integration.
- Initial real `awaiting_review` responses skip citation minting. Current `_mint_citation` raises HTTP 503, `No real DOI registrar is configured`, in non-mock mode, so approval/completion suppresses a fake citation but does not return clean citation-free completion. Existing mock runs can mint placeholder `10.0000/phyto...` identifiers; they are not registered DOIs. Final `main` integration is expected to suppress automatic mock citations without failing real approval. No DOI or OSF upload/registration was created.
- Four live-integration tests were skipped. Firebase providers are enabled and anonymous/invalid backend credentials were rejected, but valid-user login/accounting and scientific cloud execution were not certified. The real executor described here is source-only, not deployed. Local Firebase/Stripe fakes do not prove production integration.

See [ORCHESTRATOR_EXECUTION.md](ORCHESTRATOR_EXECUTION.md) for the exact schemas, configuration, request/output map, audit behavior, and pending acceptance command. Follow the deployment gates in the readiness report; do not change auth dry-run as a scientific-mode fix.

The separate tea-seed-cake study has corrected derivative peak lists; those derivatives are not included in this source target. Frozen originals and prior annotations remain available for provenance. Instrument-data access and third-party redistribution rights remain subject to their own terms.

## Publication gates

1. Confirm the repository, exact commit, title, draft/prerelease state and notes before creating the GitHub draft. No release has been published.
2. Review benchmark answer-key visibility and source-data redistribution before treating this public archive as a genuinely blinded evaluation.
3. Complete expert case review and preregister the actual model/evaluation configuration before running the comparative benchmark. Do not run paid inference or training without an approved budget.
4. Resolve scientific-runtime mock execution before production/scientific release.
5. Obtain author declarations and final author approval for the manuscript.
6. Mint a version-specific archive DOI only through an authorized archival workflow after the release source is finalized. **DOI pending; no DOI assigned or verified.** Never substitute a placeholder DOI for a real identifier.
