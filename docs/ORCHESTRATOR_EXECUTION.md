# Orchestrator scientific execution: source-only contract

**Status: source inspection, 2026-11-08.** This document describes the implementation currently present in this checkout. It is not deployment evidence, a scientific validation claim, or a release approval. Configuration names and behavior are subject to final integration on `main`. The deployed service must be verified separately.

No deployment, paid inference, model download, training, commit, or push is authorized by this document.

## Scope and hard boundary

`RealToolExecutor` supports **exactly one tool and one workflow**:

| Workflow | Tool | Implementation |
|---|---|---|
| `reference_hsqc_identify` | `reference_hsqc.rank` | Local `FileStructureDB` retrieval plus `match_hsqc` against a supplied reference JSON |

`reference_hsqc.rank` is a Tier-1 deterministic tool in the registry. The other registry tool names are not implemented by this executor, even though `GET /tools` lists their registry specifications. In real mode, `single_compound`, `compare_extracts`, `novelty_scan`, `build_network`, `mechanism_hypothesis`, and `identify` return HTTP 422 as unsupported. Existing NMR, MS/MS, SIRIUS, taxonomy, network, and LLM provider wiring does not extend this executor's support.

**The bare real `reference_hsqc_identify` workflow never invokes `MockToolExecutor`, a mock predictor, or an LLM.** Missing or invalid local configuration fails closed; it never falls back to synthetic science. An unauthenticated `/tools` request is rejected before registry inspection. Health reports `scientific_ready: false` in both real and mock modes.

The real executor is local, offline, synchronous, and bounded. There is no network provider or paid API. It supplies neither cross-modality identification nor an identification verdict; all hits still require human review.

## Configuration

| Variable | Required behavior |
|---|---|
| `PHYTO_SCIENTIFIC_EXECUTION` | Must be explicitly `mock` or `real` in every environment. Missing or invalid configuration fails closed, including locally. |
| `PHYTO_ENV` | Exactly `cloud` selects cloud environment rules; every other value, including unset, uses local rules. This is separate from scientific executor selection. |
| `PHYTO_DRY_RUN` | This is the authentication dry-run flag. `PHYTO_ENV=cloud` with `PHYTO_SCIENTIFIC_EXECUTION=mock` additionally requires `PHYTO_DRY_RUN=1`. `PHYTO_ENV=cloud` with `PHYTO_SCIENTIFIC_EXECUTION=real` additionally **rejects** `PHYTO_DRY_RUN=1`: offline mock authentication (which accepts `mock:<uid>` tokens) must never front real scientific execution on a cloud deployment. Local real mode may combine with `PHYTO_DRY_RUN=1` for offline contract tests. The flag does not make a real scientific workflow a dry run. |
| `PHYTO_STRUCTURE_DB_PATH` | Required in real mode. Path to a UTF-8 CSV structure library accepted by `FileStructureDB`; no default library is bundled. |
| `PHYTO_HSQC_REFERENCE_LIBRARY` | Required in real mode. Path to a UTF-8 JSON reference array; no default reference dataset is bundled. |

The selected mode and both real-mode paths are resolved when the cached runtime is first created. Both libraries are read, validated, and hashed once at startup. They are not reloaded per request. Changing configuration or libraries requires a new process/runtime; source has no in-process reload operation. A missing file, dependency, malformed record, duplicate/ambiguous identifier, unresolved reference candidate, or other invalid configuration prevents runtime construction rather than selecting mock execution.

Real scientific execution and authentication dry-run are independent controls. `dry_run:false` in the API is only a request field and does not prove real execution. Real workflows reject request-level `dry_run:true`; graph-only validation currently remains available through `PlannerAgent.plan`. Existing offline tests inject an authenticated identity and dependency overrides; they do not establish cloud authentication, deployment, or live-provider readiness. No real-mode deployment is claimed here.

### Structure CSV

The structure library uses the existing `FileStructureDB` format:

```csv
name,smiles,provenance,source
Ethyl Alcohol,CCO,experimental,dataset accession or measurement citation
```

- `name` and valid `smiles` are required. Optional `provenance` defaults to `literature`; a blank `source` is replaced in returned candidates with `configured structure library` rather than exposing a local path.
- RDKit derives formula, InChIKey, canonical SMILES, and mass. The real executor normalizes the trimmed, lowercased name with `.replace(" ", "-")` to produce the candidate ID. For the example above, the ID is `ethyl-alcohol`. Underlying `FileStructureDB` also falls back to the InChIKey when name is blank, but the real executor rejects blank names.
- This normalized name is the required reference `candidate_id`; it is **not** an InChIKey or arbitrary display name. Candidate IDs must be nonempty, unique after normalization, and at most 200 characters. `candidate_by_inchikey` must resolve every normalized ID uniquely. Prefer unique InChIKeys and distinct names in curated input.
- Only candidate name normalization is defined here: reference IDs and candidate IDs in the JSON are otherwise compared exactly after surrounding string whitespace is stripped.

### Reference JSON

The file must contain one JSON array of zero or more records. Unknown fields, duplicate JSON object keys, nonfinite JSON numbers, duplicate `reference_id` values, unknown `candidate_id` values, and references whose `inchikey` does not resolve to the same structure library candidate are rejected.

```json
[
  {
    "reference_id": "accession-1",
    "candidate_id": "ethyl-alcohol",
    "inchikey": "LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
    "source": "dataset accession / measurement citation",
    "solvent": "CDCl3",
    "license": "explicit reuse license",
    "provenance": "experimental",
    "peaks": [
      {"h_ppm": 1.2, "c_ppm": 18.0, "intensity": 1.0}
    ]
  }
]
```

All fields except each peak's optional `intensity` are required. `inchikey` is the reference's structural identity (full 27-character InChIKey); it must match the structure library candidate named by `candidate_id`, so a mislabelled reference cannot attach another compound's measurements. `reference_id` and `candidate_id` are bounded to 200 characters; `source`, `solvent`, and `license` are required, nonempty bounded strings. Reference peak coordinates are numeric, finite, and constrained to the `Peak2D` axis ranges: H -5..15 ppm and C -50..300 ppm; optional intensity is nonnegative. Multiple references per candidate are allowed and ranked separately. The real query does not filter by solvent.

`provenance: "experimental"` is a **curator assertion, not verification by the software**. Supplying that literal does not establish that a record is measured, and synthetic fixtures do not become experimental public references by passing validation. `source` and `license` must identify actual evidence and reuse terms; the software does not verify either. No reference dataset, license, or permission to reuse third-party measurements is supplied with this implementation.

## Request, schemas, and bounded work

Real query parameters are validated through both the registry contract and the stricter real-executor contract. The registry allows 1..2000 peaks, H tolerance greater than 0 and at most 1 ppm, C tolerance greater than 0 and at most 20 ppm, and `max_results` 1..100. The real executor narrows this to **1..256 peaks**, both tolerances 0.000001 ppm through their respective maxima, and a strict integer `max_results`. Defaults are 0.05 ppm H, 0.5 ppm C, and 10 results. Unknown fields are rejected.

Every peak must be a full `Peak2D` with `peak_type: "2d"`, `experiment_type: "HSQC"`, finite `h_ppm`, `c_ppm`, and nonnegative finite `intensity`. Coordinates must lie within the model ranges above. HMBC, COSY, TOCSY, and NOESY peaks are rejected even though they belong to the broader shared model. Declare `modalities: ["hsqc"]` for this request. The molecular formula is independently established input, not inferred from HSQC; only neutral elemental formula syntax is accepted, without charge/adduct guessing.

A complete request example for the example library is:

```json
{
  "sample_id": "sample-hsqc-001",
  "workflow": "reference_hsqc_identify",
  "modalities": ["hsqc"],
  "dry_run": false,
  "parameters": {
    "formula": "C2H6O",
    "peaks": [
      {
        "peak_type": "2d",
        "h_ppm": 1.2,
        "c_ppm": 18.0,
        "intensity": 1.0,
        "experiment_type": "HSQC",
        "solvent_flag": false,
        "review_status": "unreviewed"
      }
    ],
    "h_tolerance_ppm": 0.05,
    "c_tolerance_ppm": 0.5,
    "max_results": 10
  }
}
```

`sample_metadata` may be supplied through the shared API but is not used by this workflow's planner or matcher.

| Resource | Limit in this executor |
|---|---|
| Each structure/reference file | At most 8 MiB (8 × 1024 × 1024 bytes) |
| Structure rows | 1..1000 |
| SMILES length | At most 4096 characters per row |
| Formula | 1..100 characters; at most four digits per element count; total atom count at most 10000 |
| Reference records | 0..256 |
| Peaks per reference record | 1..256 |
| Total peaks across all reference records | At most 8192 |
| Observed real-executor peaks | 1..256 |
| Returned hits | `max_results`, 1..100 |

There is no applicable provider network timeout: the provider does no network I/O and calls are synchronous local computation. The input/file bounds limit work but are not a wall-clock execution deadline. There is no retry loop, queue, nested ledger, or nested workflow execution. Existing auth/token middleware handles its own accounting around the synchronous route; the real executor creates no additional ledger or reservation.

## Request-to-output and audit map

1. **Request.** Authenticated `POST /analysis` passes the normal route quota checks. The API creates a run ID and a provenance-manifest ID. Missing execution configuration returns HTTP 503 rather than starting scientific work. Invalid modalities or parameters and unsupported real workflows return HTTP 422.
2. **Plan.** `PlannerAgent` validates `parameters` with `ReferenceHSQCInput` and builds exactly two tasks: `rank-reference-hsqc`, then `review-reference-hsqc`, which depends on the first and requires the `before_confirmed_claim` human checkpoint. There is no MS/NMR predictor, literature, QA, or LLM task in this plan.
3. **Task execution.** `WorkflowEngine` preflights the entire tool list before side effects. It resolves Tier-1 permission, validates the input again at execution, and delegates to `RealToolExecutor`. The audit `input_hash` is `sha256_dict(resolved_inputs)` and `output_hash` is `sha256_dict(output.model_dump(mode="json"))`; the tool name is recorded separately in the tool-call record.
4. **Real provider.** `FileStructureDB` retrieves candidates whose RDKit-derived formula exactly matches the supplied formula, sorted by candidate ID. Candidates without references are listed in `unavailable_references`. Every available reference is compared independently by `match_hsqc` using H/C tolerances, `cap=4.0`, and `multiplicity_mode="none"`. Hits sort by ascending distance, candidate ID, then reference ID and are truncated to `max_results`.
5. **Output schema.** The executor validates its typed internal output, then validates a serialized copy through the registry's public `ReferenceHSQCOutput`. Public fields are `hits`, `query_peak_count`, `formula`, `unavailable_references`, `limitations`, `evidence_assessment`, and `provenance`. Each hit contains `candidate`, `reference_id`, `source`, `solvent`, `license`, `reference_provenance`, and `matcher_result`. The public hit list is dictionary-typed rather than a separate public hit model.
6. **Meaning of output.** Lower matcher distance is better. It is not a probability or calibrated confidence. Missing references, no hits, or truncation are incomplete evidence, not negative identification. Intensity, multiplicity, and solvent flags are not used. Solvent, temperature, pH, field strength, stereochemistry, and mixtures are not corrected or resolved. The inherited rectangular assignment does not explicitly penalize excess peaks; inspect coverage, counts, and unmatched indices. The `evidence_assessment` block applies documented **engineering defaults** (values surfaced in `provenance.assessment_rules`) to make weak or ambiguous ranking support explicit: `no_hits` (no ranked reference evidence), `insufficient_evidence` (fewer than 3 distinct observed or matched cross-peaks, top-hit query coverage below 50%, fewer than two comparable candidates, or formula-compatible candidates missing references), `ambiguous` (top and runner-up candidates separated by less than 0.25 distance units), or `ranked`. These dispositions describe ranking support only; they are not scientifically evaluated decision rules, never a confidence, and never an identification. Solvent compatibility is not evaluated (`solvent_compatibility_checked: false`); each hit carries the reference's declared solvent for the reviewer.
7. **Audit.** The engine logs `run_started`, a `tool_call` with input/output hashes and executor provenance, `checkpoint_requested`, and `run_finished` with `status: awaiting_review`. Wall time is returned in the response rather than recorded in the tool-call audit record. Records are hash-chained JSONL. Tool provenance includes provider and algorithm identities, implementation/library SHA-256 hashes, available package versions, tolerances, reference/candidate counts, truncation and incompleteness flags, zero API cost, no network requirement, and explicit not-validated/human-review markers. No reference-library raw content or configured path is intentionally included in startup errors. These logs and IDs are provenance evidence, not scientific validation.
8. **Output and human checkpoint.** The API returns `status: "awaiting_review"` with `summary.scientific_results` containing the ranked output, task counts, and `awaiting_task_id: "review-reference-hsqc"`; it does not create a separate results artifact for this route. `POST /runs/{run_id}/approve` takes `{"checkpoint":"before_confirmed_claim","actor":"reviewer-name"}`. The engine reruns the graph but skips the completed tool task, logs checkpoint approval, completes the bookkeeping checkpoint task, and returns `complete`. It does not recompute matching, validate reference science, or create a confirmed identification. Initial real `awaiting_review` responses do not mint a citation; approval still invokes the citation path and is subject to the limitation below. Run state and checkpoints are process memory, not a durable run store.

Tool-call audit metadata carries the request `dry_run` flag. Do not infer the scientific mode from the request alone: use `summary.scientific_execution`, health `scientific_execution`/`execution_readiness.mode`, provenance `mode`, and explicit configuration evidence.

## Health and capability are different signals

`GET /health` exposes process health, `scientific_execution`, and executor capability. In real mode, successful startup implies configuration parsing and provider imports succeeded and reports `configuration_valid: true` and `dependencies_available: true`. It also reports library counts, supported tool/workflow lists, versions, and `requires_network: false`.

Those fields do **not** establish scientific readiness. Both executors report `scientific_ready: false` and `scientific_validation: "not_validated"`. A healthy process can serve authentication, documentation, or mock routes while having no validated scientific capability. Cloud deployment and auth checks require separate evidence.

## Citation and accounting limitations

Real mode initializes `doi_registrar` to `None`. Initial real `awaiting_review` responses do not mint a citation, and checkpoint approval completes cleanly with `citation: null` in real mode (verified by `tests/test_real_orchestrator.py`; `POST /runs/{run_id}/approve` returns HTTP 200 with a null citation). `GET /runs/{run_id}/citation` for a real-mode run without a minted citation returns HTTP 503 with `No real DOI registrar is configured; no synthetic DOI was minted`. No code path mints a mock DOI for a real scientific run. Mock mode with auth dry-run uses `MockDOIRegistrar`, whose `10.0000/phyto...` prefix is a placeholder and not an externally registered DOI.

The route uses existing synchronous auth/token accounting; the real executor creates no second or nested ledger. The tool's declared `api_cost_usd` is 0.0; this is not a claim that hosting or curation is free. The registry's token estimate is not a paid-provider invocation.

## Explicit mock mode

Local mock workflows require `PHYTO_SCIENTIFIC_EXECUTION=mock`; omission is no longer accepted. The deterministic `MockToolExecutor` emits synthetic scientific results, is not validated, and must never be described as a real provider. Explicit cloud mock science additionally requires auth `PHYTO_DRY_RUN=1`. Real scientific mode is independent of auth dry-run: real local science may use dry-run auth for offline tests, while cloud auth keeps its normal guard. Tests set mock science explicitly in `tests/conftest.py`.

A useful explicit local real configuration is:

```sh
export PHYTO_ENV=local
export PHYTO_SCIENTIFIC_EXECUTION=real
export PHYTO_STRUCTURE_DB_PATH=/absolute/path/to/curated-structures.csv
export PHYTO_HSQC_REFERENCE_LIBRARY=/absolute/path/to/curated-references.json
export PHYTO_DRY_RUN=1  # local test auth only; independent of science mode
```

With the existing full-plane import layout, run from the repository root:

```sh
make stage-auth
PYTHONPATH=shared:build/auth-path:services:. \
  .venv/bin/python -m uvicorn app.main:app \
  --app-dir services/orchestrator --port 8000
```

The elucidation package is resolved via `services` on `PYTHONPATH`; do not copy libraries or suppress missing provider imports. Container packaging is source availability only, not a deployment claim.

## Acceptance command: pending, not a result claim

The dedicated acceptance test is `tests/test_real_orchestrator.py`. It is expected to exercise authenticated local real execution, strict reference validation, independent mode controls, unsupported workflows, and no fallback to mock prediction. Its expected state is **pending until actually run on the integrated source**; this documentation does not claim a pass count or result.

From the repository root, with existing dependencies available and no paid/network provider required by the real executor:

```sh
make stage-auth
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=shared:build/auth-path:services:. \
  .venv/bin/python -m pytest -p no:cacheprovider \
  tests/test_real_orchestrator.py -q
```

Tests may inject offline authentication and temporary local libraries. A passing local test would not prove cloud deployment, public reference provenance/licensing, general workflow support, or scientific validity.

## Authoritative source for this description

- `services/orchestrator/app/execution_config.py`
- `services/orchestrator/app/real_executor.py`
- `services/orchestrator/app/main.py`
- `services/orchestrator/app/planner_agent.py`
- `services/orchestrator/app/tool_registry.py`
- `services/elucidation/app/structure_db.py`
- `services/elucidation/app/hungarian_matcher.py`
- `shared/phyto_shared/models.py` and `shared/phyto_shared/schemas.py`
