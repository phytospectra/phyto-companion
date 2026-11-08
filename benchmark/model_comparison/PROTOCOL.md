# Offline model comparison: public regression only

This harness executes only local `llama_cpp` completions or evaluates already-collected local JSON records. It never makes provider API calls, downloads models, uploads data, or trains. It is fixed to the four existing public, unreviewed NMR-only pilot packets and public keys. **These are public regression fixtures, not blinded test cases or independent experimental cases**; the scientific benchmark denominator remains zero. The keys are readable in this repository, and model pretraining contamination cannot be ruled out. Strict allowlisting and a limited leak-token scan do not prove absence of memorization, reverse-identification, semantic leakage, training-set contamination, or true blinding. No private benchmark or external inference is authorized by this harness.

## Lock before runs

Copy `example-config.json` outside the repository or into a suitable local results directory and replace all placeholder identities with the actual immutable model and provider/runtime versions. All arms use identical prompt, inputs and generation settings. `repeats` indexes independent attempts; effective seed is base seed + repeat, paired across arms. `context_tokens` and `threads` are pinned; local execution uses CPU (`n_gpu_layers=0`). Record and retain the actual environment; seeds do not guarantee reproducibility across hardware, library versions or providers. Replay records are self-attested, not evidence that settings were actually honored. Model version must identify the immutable revision; for local models also specify an absolute model artifact path and its SHA-256, and set `adapter` to `llama_cpp` and provider_version to installed `llama_cpp.__version__`. No model is downloaded. Runtime requirements are Python 3.11+ and, only for local inference, an already installed compatible `llama-cpp-python` and model file. Verify rights before using local data/models.

From the repository root, with `$CONFIG` a prepared config and `$OUT` an existing, private local directory:

```sh
python benchmark/model_comparison/compare.py lock --config "$CONFIG" --output "$OUT/lock.json"
python benchmark/model_comparison/compare.py run-local --lock "$OUT/lock.json" --arm local_arm --output "$OUT/local.json"
python benchmark/model_comparison/compare.py evaluate --lock "$OUT/lock.json" --records "$OUT/local.json" --records "$OUT/other.json" --output "$OUT/report.json"
```

Omit `--records` for an availability/missing-slots report. Existing output files are never overwritten. Store the lock before collecting records; hash and retain it separately. It fixes the eligible case IDs, bytes of packets, prompt, evaluator keys, harness code, each exact model-visible input, settings, identities, and repeat count. Evaluation and inference refuse changed inputs. Hashes detect changes relative to a retained lock; they do not establish independent provenance or prevent an operator from creating a new lock. Input is exactly prompt bytes + `\nCASE JSON:\n` + sorted compact UTF-8 JSON packet + newline. Only the allowlisted packet and prompt go to the local model, never keys, worksheet, paths or manifest. Freeze and retain outputs locally; do not expose response files or evaluator keys to the model.

## Replay input contract

Each `--records` file is a JSON array of attempt objects. Create one object per planned arm/case/repeat, without silently retrying. A minimal complete example follows (replace all hashes with exact lock and input hashes and identity/settings with the locked values):

```json
[{"arm":"offline_replay_a", "case_id":"C001", "repeat":0,
  "lock_sha256":"<sha256-of-lock-file>", "input_sha256":"<lock.cases.C001.input_sha256>",
  "identity":{"provider":"<provider>","provider_version":"<version>","model":"<model>","model_version":"<immutable-version>","adapter":"replay","artifact_sha256":null},
  "settings":{"temperature":0,"top_p":1,"max_tokens":512,"seed":20261109,"context_tokens":4096,"threads":1},
  "status":"success", "response_text":"{\"case_id\":\"C001\",\"selected_candidate\":\"cand-a\",\"cited_evidence\":[],\"abstention_reason\":null}",
  "input_tokens":100,"output_tokens":20,"cost_usd":0.01,"cost_basis":"actual",
  "elapsed_seconds":1.2,"error":null,"runtime":{"environment":"record actual execution environment"}}]
```

For failures set `status` to `failure`, `response_text` to null, and `error` to a nonempty string. Token counts, cost and elapsed time may be null when unknown. Cost basis is `actual`, `estimated`, `unknown` (cost must be null), or `local_no_provider_charge` (only local llama_cpp; zero provider charge, **not** zero electricity/hardware cost). Token counts are provider-reported, not estimated by the harness. Do not treat unknown usage/cost as zero: reports give known sums and unknown counts, split cost basis and retain usage for valid failures and duplicates. Rejected records are listed but excluded from usage totals, so reconcile rejected records and any external billing separately. Record limits, provider settings, errors and costs faithfully; no claim of verified provenance follows from imported records.

## Scoring and safeguards

Missing slots, failures, malformed responses, duplicated attempts and rejected/unmatched records are explicit. Duplicate slots are never cherry-picked; preserve their usage and report them as unscored. Candidate accuracy excludes abstention-designed cases and reports scored denominator; coverage uses every planned slot. Abstention on C004 with any nonempty reason is only a procedural proxy: whether it names the **correct missing evidence** requires expert adjudication. Citation checks are packet-specific, HSQC-only; IDs must exist and have a compatible type. Without ID, both coordinates must uniquely match one peak within ±0.05 ppm H and ±0.50 ppm C. Unlike the older pilot scorer, an explicit bad ID cannot be rescued by coordinates, and unsupported citation fields are invalid. Existence is not evidence that a conclusion is supported: all valid citations need expert review. These scores are not directly identical to `score_run.py`, which conflates the designed abstention with candidate accuracy and accepts any nonempty abstention reason. Matched-arm deltas use only slots scored in both arms and exclude abstention cases; repeat slots are not independent samples. Review mismatch, failure and coverage before comparing; there is no significance or model-quality claim from four public fixtures.
