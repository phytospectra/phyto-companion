# Open-weight tuning, Gemini, and Antigravity comparison

Prepared 2026-11-09. **Proposed study, not preregistered, not scientifically run.**
This extends rather than replaces the deterministic paired-HSQC protocol. User
approved a total USD 50 OpenRouter pilot ceiling. Colab compute allocation is
separate; Pro+ access was reported, but the CLI account currently reports no
compute units. No training is authorized by an assertion that it will help.

## What could improve the platform

Test tuning for evidence selection, schema-correct tool calls, explicit unknowns,
ambiguous ranking, and evidence-grounded reporting. Keep formula calculation,
structure identity, peak matching and numerical results in deterministic tools.
A tuned model must not bypass rights checks, ownership, metering or review gates.
Do not train it to replace experimental observations with remembered shifts.

First compare prompt/skill/retrieval improvements with the untouched model. Only
consider adapter training if recurring errors remain with complete, correct
retrieval and reviewed evidence. Missing references and incorrect structures are
data failures, not a reason to tune. No measured evaluation set is currently
cleared, so a scientifically supported training decision remains blocked.

## Two distinct comparison tracks

### Controlled evidence/model track

| Arm | Configuration | Contrast |
|---|---|---|
| A | Existing deterministic Phytospectra reference workflow | Non-LLM anchor on supported tasks |
| B | Untuned open-weight model with fixed evidence packets | Model baseline |
| C | Same open model + locked Phytospectra tool/skill environment | Benefit of tool orchestration |
| D | Same model, same environment, reviewed LoRA/QLoRA adapter | Incremental value of tuning: D versus C |
| E | Exact Gemini model via OpenRouter + same permitted tools/evidence | Model comparison: E versus C/D |

For reasoning-only subtests, B/C/D/E must all receive the same computed evidence
and tool access. Do not mix those scores with a tools-versus-no-tools experiment.
Arm A is eligible only where the task matches its actual capabilities; no invented
score for literature or unsupported MS workflows. Preserve planned denominators,
failures, unsupported cases, and per-task eligibility.

### End-to-end agent-system track

Compare the actual Phytospectra agent deployment with Google Antigravity running
its official Science plugin/Cheminformatics Skills. Freeze application version,
model/version where exposed, skill version or digest, installed tools, database
snapshot/date, prompts, input files, tool budgets, time limits and operator actions.
A plain Gemini API completion is **not** Antigravity-with-Skills. If the product
hides model or skill versions, label that provenance limitation instead of
inventing immutable identities. Antigravity access is not yet available.

Run a closed-book track with identical allowed evidence, and optionally a separate
open-retrieval track. Never aggregate the two as if information access were equal.
No evaluator keys, review sheets or reference identities hidden by the protocol
may be present in the agent filesystem. Browser/workspace state must be reset
between cases. Manual assistance must be logged and scored separately.

## Tasks and measurements

Begin with original synthetic software smoke cases, explicitly not measured
spectra or scientific accuracy: missing modality, unsupported citation, conflicting
identity report, missing reference, ambiguous evidence, and clear supported rank.
These only test instruction/API/scoring behavior and are not fine-tuning evidence.

The eventual measured study covers verified structure consistency, extraction and
unit fidelity, known-formula HSQC reference ranking, evidence citation, missing-
evidence abstention and supported report generation. Broader cheminformatics
curation/reaction/QSAR tasks need their own ground truth and eligibility; do not
claim the HSQC platform matches all Antigravity demonstrations.

Predeclare top-1/top-k ranking, retrieval coverage, supported-wrong decisions,
abstention/coverage, fabricated observations, citation validity and expert support,
structure identity errors, schema/tool-call validity, failed/missing attempts,
latency, token use and actual cost. Use paired cases and compound/source/family
held-out splits. Repeats are not independent compounds. Blind human adjudicators
to arm identity. Do not use Gemini or the tuned model as the sole judge.

## Model shortlist and compute

- `Qwen/Qwen3-8B`: practical first adapter feasibility candidate; its official
  card reports Apache-2.0. Pin weights/tokenizer revision and training software
  before use; model availability is not evidence of chemistry accuracy.
- `Qwen/Qwen3.8-27B`: larger open-weight comparison candidate, official card
  reports Apache-2.0. Do not assume it fits this computer or the allocated GPU.
- Gemma 4 family: independent open-weight comparator; choose the exact size,
  artifact and applicable license after hardware/terms review.
- Gemini via OpenRouter: resolve an explicit catalog model ID before each locked
  study, retain catalog snapshot and returned model/provider/generation IDs. A
  mutable hosted slug is not an immutable weight revision.

The local machine reports 8 GiB RAM; do not launch large model downloads/training
there. Colab must probe the actual GPU/VRAM, framework versions and memory usage.
Train only after data/license/split approval and a one-batch memory check. Start
with adapter SFT, checkpoint/restart testing and a bounded runtime. Preferences or
more elaborate optimization require evidence that simpler SFT is insufficient.

Training targets should be reviewed structured decisions, tool traces and short
evidence-backed explanations—not unreviewed teacher chain-of-thought. Any teacher
output reuse requires applicable terms/data-rights review. Keep a final untouched
holdout separate from prompt development, adapter selection and all error analysis.
Compare tuned versus untuned with identical quantization, decoding, tools and
retrieval. Measure regressions in abstention and unsupported claims, not just ranks.

## Budget and execution safeguards

- USD 50 is a total authorization ceiling, not a target or per-model allowance.
- Read-only key preflight found a non-resetting USD 50 lifetime limit, with about
  USD 12.35 remaining. Prior usage is not attributed to this experiment.
- Do not raise that key limit or infer another USD 50 is available. Shared-key
  use can reduce the remainder; check before every batch and avoid concurrency.
- The observed key excludes BYOK spend from its cap. A comprehensive capped-run
  preflight must either establish no BYOK path or require BYOK included in the
  limit. Do not silently treat a partial cap as covering all provider charges.
- No automatic retries after ambiguous POST outcomes. Preserve pending journal
  entries and reconcile billing/generation IDs before resuming.
- Store secrets only in environment/secret storage; never in notebooks, CLI args,
  source, manifests or logs. Do not use Colab CLI `--env KEY=value` for secrets.
- Colab Pro+ does not guarantee a particular GPU or unlimited compute. Establish
  a separate bounded job allocation, output retrieval and runtime cleanup check.

The new synthetic runner is separate from `model_comparison/compare.py`, whose
four public NMR regression cases remain unsuitable as hidden evaluation data.
Do not upload owner-supplied measured spectra to any provider until their external
processing permission is established.

## Sources checked 2026-11-09

- Google describes Antigravity Cheminformatics Skills as a Science-plugin agent
  workflow grounded in RDKit and database connectors, not an NMR accuracy
  benchmark: https://deepmind.google/blog/agentic-cheminformatics-with-google-antigravity/
- Skill packaging/version context: https://antigravity.google/docs/skills/
- Model cards: https://huggingface.co/Qwen/Qwen3-8B and
  https://huggingface.co/Qwen/Qwen3.8-27B
- Gemma sizes and tuning considerations: https://ai.google.dev/gemma/docs/core
- Quantized adapter training: https://huggingface.co/docs/peft/developer_guides/quantization
- Key credit limits: https://openrouter.ai/docs/api/reference/limits
- Provider pinning/policies: https://openrouter.ai/docs/guides/routing/provider-selection
- Colab availability/limits: https://research.google.com/colaboratory/faq.html
