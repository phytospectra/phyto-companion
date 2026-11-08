# Blinded NMR–MS Benchmark and Model-Comparison Protocol (pilot)

Status: **repaired harness, smoke test passing; expert review NOT yet done;
no model inference performed.** The builder, scorer, 15 regression checks and
4 blinded cases exist in `benchmark/blinded_pilot/`. No accuracy claim may be
quoted from this directory: the four cases are a **software smoke test**, not
a benchmark denominator (§2).

## 0. Purpose and hypothesis

Test the concrete question the study prompt poses: **when two candidate
structures both fit the precursor mass and most of the NMR data, can the
model identify the correlation that distinguishes them — and can it
recognize when that correlation is missing?** A model can name the right
compound and still cite a cross-peak that was never observed; the benchmark
scores those separately.

## 1. Directory layout and what is where

| Path | Seen by | Role |
|---|---|---|
| `build_cases.py` | operator | Case builder. Validates chemistry (RDKit formula check vs displayed formula, canonical-SMILES duplicate check), shuffles candidate order with recorded seed 20261102, and performs a leak-token self-check before writing. |
| `cases/C001…C004.json` | model | Blinded packets. Allowlisted fields only: opaque case ID, task (candidate IDs + validated structures), acquisition conditions, observed HSQC peaks with stable evidence IDs (E###) and **unassigned** coordinates. No compound names, no answer labels, no expected abstention, no reviewer notes, no source filenames. |
| `cases/C005, C006.json` | (none) | Placeholders (empty candidate lists). Excluded from the eligible set by the scorer; kept only to mark the known gap. |
| `keys/answer_keys.json` | evaluator only | True compound, answer label (post-shuffle), assigned atom positions per evidence ID, draft decisive-evidence proposal. |
| `review/worksheets.json` | evaluator only | Per-case expert-review worksheets: candidate structures, evidence rejoined with atom labels, proposed decisive observations (marked awaiting expert review), plausible alternatives, insufficient-evidence field, empty `reviewer` field to fill. |
| `score_run.py` | evaluator only | Scorer (§6). Frozen eligible set, missing/duplicate/unknown/malformed handling, coordinate-fallback rules. |
| `test_repairs.py` | evaluator only | 15 regression checks (§7). |
| `PACKAGING.sha256` | evaluator only | Deterministic hash over sorted case packets + answer keys; same-seed rebuilds must reproduce it. |

Evaluator-only files must never be sent to a model arm. The leak surface is
enforced by the builder's token self-check and test 1a/1b.

## 2. Denominator plan and current honest count

Target pilot size: **20–30 independent, expert-reviewed cases** (study prompt).
Currently built: **4 eligible cases** (C001–C004; C005/C006 are empty
placeholders excluded by the scorer). These 4 are **curated smoke-test
fixtures** (flavonoid trio + designed abstention), not independent
experimental cases — they establish that the pipeline works, and their
scores are regression checks only. The benchmark denominator stays at
zero until the 20–30 case set is assembled and expert-reviewed.

Smoke test (excluded from all reported accuracy): M1-a tetracycline,
M1-b thiostrepton, and the two NMR archives covered by rubric §13.

## 3. Arms (modality ablation)

Every paired case (same sample, documented linkage) runs three times:
`nmr_only`, `ms_only`, `nmr_ms_paired` — measuring what the second modality
adds. Current smoke-test cases are NMR-only; the paired arm awaits paired-data
acquisition (documented-sample NMR+MS from the same extract), which the study
prompt itself flags as the likely data requirement.

## 4. Rights and privacy gates

- Local curated fixtures (quercetin/kaempferol/luteolin): in-repo curation,
  external inference allowed. Structure records trace to PubChem CIDs
  5280343 / 5280863 / 5280445.
- SUMMIT v4 / NP-MRD lineage: **CC-BY-NC** — academic use with attribution;
  commercial use prohibited; `external_inference: unknown` until the provider's
  terms are checked against non-commercial data (recorded per case in the manifest).
- NMRTrans-Data (MIT, verified 2026-10-31 in NMR_RESEARCH_REGISTER.md):
  verified usable Tier-2 1D corpus; **verify per-record rights before
  redistribution**; usable for both dev and training partitions.
- NMRGym (MIT, email-gated, 269,999 mols) and NMRArena (HTTP 401 unauthenticated
  — license unreadable): counted **zero** in the benchmark until access and
  terms are established (per the study prompt and ADR register).
- Private CaseStudy archives: **local only**; never sent to a named external
  provider without explicit owner authorization.

## 5. Deduplication and partitioning (before any fitting or prompt selection)

- Deduplicate by standardized structure (canonical SMILES) and compound
  family — enforced by the builder's duplicate check and test 2b.
- Replicates, salts, stereoisomer families, related scaffolds stay in the same
  partition.
- Reserve an untouched `test` partition and, where sources allow, a
  `test_source_held_out` subset.
- Public cases are assumed pretrain-contaminated: answers are stripped from
  every model-visible surface (builder allowlist) and, where available, a
  local-only private-case evaluation is added (pending owner authorization).

## 6. Scoring (separate, never blended)

Run from the repo root:

```
python benchmark/blinded_pilot/build_cases.py            # rebuild packets + keys + worksheets (deterministic, seed 20261102)
python benchmark/blinded_pilot/test_repairs.py           # 15 regression checks
python benchmark/blinded_pilot/score_run.py <run_dir>    # score a response directory
```

Response files are named `<CASEID>.response.json` (a second submission for the
same case may be `<CASEID>.response.2.json` and is reported as a duplicate).
Scorer behavior:

1. **Eligible set frozen from `cases/`** before any response is read; every
   eligible case is reported even when its response is missing or malformed
   (status `missing_response` / `malformed_response`), and the placeholder
   cases are excluded. Unknown case IDs are listed, never scored.
2. **Candidate accuracy** — top-1 named candidate matches the answer key.
   Cases with missing or duplicate submissions are excluded from numerator
   AND denominator (eligible count preserved in the report).
3. **Invalid citations** — every cited evidence ID must exist in that case's
   packet. Without an evidence ID, the coordinate fallback (HSQC only) accepts
   a citation only when BOTH δH (±0.05 ppm) and δC (±0.50 ppm) match the SAME
   observed peak unambiguously. A peak label (e.g. "H-7") alone never
   validates a citation; the packet carries no assigned labels.
4. **Abstention** — separate numerator/denominator over abstention-designed
   cases; abstention on sufficient cases is a miss there.
5. **Citation-exists ≠ supports-conclusion.** The scorer verifies that cited
   evidence exists in the packet; whether a cited peak actually supports the
   stated conclusion is marked `requires expert review` unless a later
   adjudicated rubric covers it.
6. **Designed abstention cases** require the model to decline AND name the
   missing evidence.

## 7. Regression checks (`test_repairs.py`, all 15 pass)

1a/1b packet leak scan (no answer-bearing tokens, allowlisted keys only);
2a formula↔structure agreement, 2b no duplicate canonical structures,
2c candidate-order shuffle integrity; 3a valid HSQC citation passes,
3b fabricated HMBC sharing one endpoint fails, 3c label-only citation fails;
4 cross-case evidence citation fails; 5/5b/5c/5d missing response recorded
with correct denominator, duplicate detected, unknown case detected;
6a placeholders excluded, 6b same-seed rebuild is byte-identical
(PACKAGING.sha256 reproduced).

## 8. Execution checklist

1. [x] Repair generator, scorer, packets, keys; regression checks green
2. [ ] **Expert review of C001–C004 via `review/worksheets.json`** — fill the
  `reviewer` field; confirm or override the proposed decisive observations
  (H-3 presence for luteolin; B-ring pattern for quercetin vs kaempferol);
  adjudicate whether the kaempferol-vs-luteolin absence argument is decisive
  or the case must be marked ambiguous; confirm C004 abstention is the
  required behavior
3. [ ] Retrieve NMRShiftDB2 records for C005/C006 or drop them
4. [ ] Expand to ≥20 cases from NMRTrans-Data after per-record rights check;
  include NMR-only, MS-only and paired cases, near-isomers, ambiguous and
  insufficient-evidence cases
5. [ ] Pre-register: tolerances, prompts, candidate-list sizes, seed, primary metric
6. [ ] Run arm A (pinned Claude) — identical packets, prompts, output limits
7. [ ] Run arm B (Phytospectra engine) on the same payloads
8. [ ] Run arm C (one untuned open-weight model, local)
9. [ ] Score with `score_run.py`; report three scores × three arms × partitions
10. [ ] Record actual denominator; publish only partition-respecting numbers
11. [ ] Only if recurring interpretation errors persist on dev cases: LoRA/QLoRA
  fine-tuning experiment per study prompt §5 (rights-cleared expert labels,
  frozen base checkpoint and split, tuned-vs-untuned on untouched test cases)
