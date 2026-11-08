# Paired HSQC executor benchmark

**REVIEW / FINAL LOCK PENDING.** No independently measured result, expert endorsement,
scientific validation or superiority claim is supplied. All generated examples and tests
are **SYNTHETIC SOFTWARE CONTRACTS**, not chemically plausible measured spectra.
Read [PROTOCOL.md](PROTOCOL.md) before use. No data acquisition, network, LLM or paid calls.

## Exact offline commands

From the checkout (existing `.venv`, no installation/download required):

```sh
cd '/Users/subhadipbanerjee/Documents/NMR-MS-data analysis/phyto-companion'
WORK="$(mktemp -d /tmp/paired-hsqc.XXXXXX)"
.venv/bin/python benchmark/paired_hsqc/evaluate.py synthetic --bundle "$WORK/bundle" --keys "$WORK/private-keys.json"
.venv/bin/python benchmark/paired_hsqc/evaluate.py lock --bundle "$WORK/bundle" --keys "$WORK/private-keys.json" --output "$WORK/snapshot.json"
.venv/bin/python benchmark/paired_hsqc/evaluate.py run --bundle "$WORK/bundle" --lock "$WORK/snapshot.json" --output "$WORK/predictions.json"
.venv/bin/python benchmark/paired_hsqc/evaluate.py score --bundle "$WORK/bundle" --lock "$WORK/snapshot.json" --keys "$WORK/private-keys.json" --records "$WORK/predictions.json" --output "$WORK/private-score.json"
.venv/bin/python -m pytest tests/test_paired_hsqc.py -q
```

`WORK` is outside the checkout. Protect it with appropriate permissions (e.g. `umask 077`
before generation). No output is overwritten; choose new filenames for each command.
`synthetic` generates fixtures, not predictions. `lock` writes a snapshot labelled
`review_lock_pending`, **not** a final scientific lock. Exit 2 means aborted preflight or
invalid artifacts; exit 0 is software completion, not a scientific acceptance decision.
Inspect private errors/inputs locally; CLI errors intentionally omit paths/content.

For an isolated source archive without `.git`, set `export PHYTO_SOURCE_REVISION=FULL_40_HEX_REVISION`
using the independently recorded original source revision before `lock`, `run` and `score`.
The variable must contain exactly 40 hex characters. Missing archive provenance fails;
in a checkout the override must agree with actual HEAD or is rejected. This records an
explicit archive provenance assertion, not a verified Git history; actual source hashes
still bind the code. Do not substitute a fabricated revision.

For a curator-supplied measured bundle, use the same last three commands with its
external bundle/key paths. Do not run a final measured evaluation until expert review,
external timestamping and protocol approval are complete. Scientific classification
requires full evidence below; changing a classification string is not sufficient.

## Architecture and comparison

- `harness.py`: strict bundle validation, source/config/environment snapshot, real A,
  directed-nearest B, sequential alternating A/B execution; **no key argument or scorer import**.
- `scorer.py`: evaluator-only external keys, evidence validation, prospective snapshot,
  planned-slot scoring and deterministic compound-cluster uncertainty.
- `synthetic.py`: invented fixtures only; schema-required reference provenance literal
  `experimental` is not a measurement claim.
- `evaluate.py`: separate `synthetic`, `lock`, key-free `run`, and private `score` CLI commands.

A invokes the production registry and `RealToolExecutor.execute('reference_hsqc.rank', ...)`
directly; it does not benchmark PlannerAgent, WorkflowEngine or audit writes. Both arms
retrieve the complete formula-compatible set from the same validated FileStructureDB and
all its references. More than 100 references per query is a preflight error. Multiple
references compete by best distance; ranking collapses to unique candidates.

A uses real rectangular Hungarian matching. B independently takes each query peak's
nearest reference cost, permitting reuse. Both use production pair-cost geometry,
tolerances, missing-reference abstention, minimum distinct coverage and candidate gap.
This is a meaningful lookup baseline, not a pure assignment ablation: normalization and
unmatched-peak handling differ. Distances/support flags are not probabilities or identities.
See protocol for limitations and fixed endpoints.

## Exact public bundle schema

Unknown fields, duplicate JSON object keys, duplicate case IDs and nonfinite values fail.
The runtime also validates the CSV/JSON libraries, peak budgets and molecular identities.

- `structures.csv`: production columns `name,smiles,provenance,source`. Candidate IDs are
  `name.lower().replace(' ', '-')`; valid unique structures/InChIKeys are required.
- `references.json`: production array of objects with exactly `reference_id`,
  `candidate_id`, `inchikey`, `source`, `solvent`, `license`, `provenance: "experimental"`,
  `peaks: [{"h_ppm": number, "c_ppm": number}]` (optional nonnegative intensity).
  In scientific mode `source` is an evidence source ID, not a free-text citation.
- `queries.json`: nonempty array, each object exactly:
  `{"case_id":"opaque-id","formula":"C2H6O","peaks":[{"h_ppm":1.0,"c_ppm":20.0}],"partition":"test"}`.
  Partitions are `dev`/`test`. Coordinates must be finite numeric, not strings/booleans;
  H in [-5,15], C in [-50,300], 1..256 peaks. Formula is neutral exact elemental counts.
  Include **all** dev and test queries for split checks; scorer uses only test slots.
- `manifest.json`: exactly `{"classification":"synthetic_contract" or "independent_measured",
  "review_artifact_sha256":"64 lowercase hex characters"}`. Digest is SHA256 of canonical
  JSON of private key's `evidence`: UTF-8, sorted keys, separators `(',', ':')`, no NaN.

Do not include truth, acceptable IDs, compound names/accessions/reviewer notes in query
fields. Allowlisting rejects extra fields but cannot prove semantic blindness. Experts
must audit candidate/library text for leakage. No automatic peak picking or condition correction.

## External private evaluator schema

Keys must resolve outside this repository. They are never an inference input.
Root has exactly `schema: 1`, `evidence`, `cases`. Each case has exactly:

```json
{
  "case_id": "opaque-id",
  "truth_status": "present",
  "acceptable_candidate_ids": ["candidate-id"],
  "independence_group": "compound-family-group",
  "partition": "test",
  "query_source_id": "query-acquisition-source-id",
  "reference_source_ids": ["reference-acquisition-source-id"],
  "condition_mismatch": false,
  "provenance_review": {
    "rights_cleared": true,
    "independently_acquired": true,
    "identity_verified": true
  }
}
```

One key row per query, no extras. Present has exactly one accepted library identity;
ambiguous at least two; absent an empty set. Present may be absent from formula retrieval
or lack reference evidence: these remain in denominators. Absent means externally
adjudicated identity absent from the *whole* frozen structure library, not just the hit list.
Group compound repeats/families conservatively; no group may cross dev/test. External
curators must also group absent identities/families: empty accepted sets cannot prove this.

`evidence` has exactly:

- `classification`: same as manifest.
- `reviewer`, `adjudication_id`, `external_timestamp_receipt`: nonempty external review identifiers.
- `screening_log_sha256`: lowercase 64-hex digest of retained external screening/exclusion log.
- `records`: acquisition evidence array; may be empty only in synthetic contracts.

Each scientific evidence record has exactly:

```json
{
  "source_id": "opaque-source-id",
  "role": "query",
  "acquisition_id": "independent-acquisition-id",
  "raw_sha256": "64 lowercase hex characters",
  "rights_verified": true,
  "independently_acquired": true,
  "external_adjudication": "external-reviewed-artifact-id",
  "metadata": {
    "solvent": "recorded solvent",
    "temperature": "recorded temperature with units",
    "pH": "recorded value or justified not-applicable",
    "field": "recorded field with units",
    "referencing": "reviewed procedure",
    "processing": "frozen procedure/version",
    "peak_picking": "frozen procedure/version",
    "assignment_provenance": "external assignment review ID"
  }
}
```

`role` is `query` or `reference`. Scientific mode requires evidence for every query and
loaded reference source, per-case references exactly covering its retrieved reference
sources, cleared provenance flags, disjoint query/reference IDs/acquisitions/raw hashes,
unique query acquisitions, and no source/compound/group crossing dev/test (including
reference source use). All metadata strings must be nonempty; a documented justified
not-applicable value is allowed where appropriate, not an invented measurement.
Curator attestations/digests do not authenticate rights or scientific truth. Keep actual
signed review, screening log, source bytes and external timestamp receipts access-controlled.
Nothing in this implementation claims those artifacts have been acquired or verified by us.

## Outputs, safety and failures

Snapshot includes key digest only (no key path/content), all input byte hashes, retrieval
sets, configuration, revision and actual dirty source hashes, versions and machine metadata.
Changes invalidate execution/scoring; keep code and bundle stable after snapshot.
Predictions include compact all-reference evidence, dispositions/counts, input hashes,
statuses and monotonic per-slot runtimes. `deterministic_sha256` excludes elapsed time.

Scorer retains one planned slot per test query per arm. Missing records, duplicates,
malformed outputs, exceptions and timeouts are failures, never chemical abstentions.
Foreign case/arm IDs or lock mismatch abort scoring. Foreign candidate/reference hits
become malformed failures. A partial supervisor-captured run must preserve its header,
lock ID and config; omitted slots count missing. Do not cherry-pick attempts or retry.

Reports are private: contain per-case truth status/group and arm predictions. They report
retrieval/reference recall, diagnostic raw unique-candidate top-1/top-3, supported-correct,
coverage, supported-wrong, wrong-among-supported, failure/abstention, absent abstention and
ambiguous flag/abstention, with explicit numerators/denominators and descriptive Wilson CIs.
Ambiguous unique preferences count as unsupported safety errors, never uniquely correct.
Failures are zero on primary full present denominator; matched-success sensitivity is separate.
Whole-compound cluster paired bootstrap (10,000; seed 20261109) preserves repeated queries;
exact McNemar is suppressed when present groups repeat. Empty rates are null, single-group
bootstrap is degenerate. Stratum/count/condition rows remain available, not new primary tests.

Timeout is currently **post-completion detection** at 60 seconds, not hard cancellation.
A nonreturning call can block. External hard-timeout supervision (60 seconds per slot,
30 minutes per run) remains a final-evaluation blocker; no scientific run is authorized
merely by passing these contracts. Separate process identities/filesystem permissions are
needed for adversarial key isolation. No aggregate export/publication is automatic.
