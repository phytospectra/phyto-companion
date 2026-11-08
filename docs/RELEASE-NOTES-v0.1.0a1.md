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

This is research software, not a validated structure-identification system. No comparative model accuracy or fine-tuning result is claimed. The orchestrator currently uses `MockToolExecutor`; a response declaring `dry_run:false` is not evidence of real tool execution. Four live-integration tests were skipped. No performance or production-readiness guarantee is made.

Firebase providers are enabled and observed backend requests without valid credentials were rejected. Do not change `PHYTO_DRY_RUN` as a standalone authentication fix: authentication and predictor selection are coupled. Follow the deployment gates in the readiness report.

The separate tea-seed-cake study has corrected derivative peak lists; those derivatives are not included in this source target. Frozen originals and prior annotations remain available for provenance. Instrument-data access and third-party redistribution rights remain subject to their own terms.

## Publication gates

1. Confirm the repository, exact commit, title, draft/prerelease state and notes before creating the GitHub draft. No release has been published.
2. Review benchmark answer-key visibility and source-data redistribution before treating this public archive as a genuinely blinded evaluation.
3. Complete expert case review and preregister the actual model/evaluation configuration before running the comparative benchmark. Do not run paid inference or training without an approved budget.
4. Resolve scientific-runtime mock execution before production/scientific release.
5. Obtain author declarations and final author approval for the manuscript.
6. Mint a version-specific archive DOI only through an authorized archival workflow after the release source is finalized. **DOI pending; no DOI assigned or verified.** Never substitute a placeholder DOI for a real identifier.
