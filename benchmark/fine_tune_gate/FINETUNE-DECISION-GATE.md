# Fine-Tuning Decision Gate — experimental arm, not a commitment

Status: **gate memo only; no fine-tuning run, no GPU work executed.**
The study prompt sets fine-tuning as an experimental arm with a decision gate.
This memo defines the gate; the benchmark pilot (§`benchmark/blinded_pilot/PROTOCOL.md`)
produces the evidence the gate reads. Until the pilot completes, fine-tuning
stays at planning level.

## 2026-11-09 planning update

The user approved a USD 50 OpenRouter pilot ceiling and proposed Colab Pro+.
See `../agent_comparison/RESEARCH-PLAN.md` for controlled model/skill ablations
and the distinct Antigravity-with-Science-plugin comparator. This authorizes
bounded inference preparation, not a claim that adapter training is justified.
Historical source/model choices below must be rechecked against actual rights,
availability and the final frozen protocol before any training. Non-commercial
licenses do not automatically authorize commercial-platform training or reuse.

## 1. Proposed gate (no external preregistration receipt verified)

Fine-tuning an open-weight model is **justified only if the pilot shows the
error pattern that fine-tuning can fix** — the model receives the correct
candidates and the decisive correlations in its payload, and still repeatedly
misinterprets them (e.g. names the right scaffold but ignores that the decisive
HMBC was absent, or invents correlations under ranking pressure).

Fine-tuning is **NOT justified** when the pilot shows:

| Pilot finding | Correct response instead of fine-tuning |
|---|---|
| Correct structure never reaches the candidate list | Improve retrieval (candidate generation), not the model |
| Decisive cross-peak missing from the model's payload | Fix extraction/parser (Phase 2 readers), not the model |
| Model uses evidence correctly but candidate labels/filenames leak answers | Fix packaging, not the model |
| Errors are few and non-repeatable | No action; report pilot as error-finding exercise |

## 2. Training-data requirements (all must hold before any GPU run)

1. **Separate, rights-cleared, expert-reviewed examples** — distinct from the
   benchmark's `test` and `test_source_held_out` partitions (source-held-out
   means the training corpus's source never appears in the held-out evaluation).
2. Verified per-record rights: NMRTrans-Data (MIT, verified) is the current
   approved 1D source; NMRGym (MIT, email-gated) counts only after access;
   nmrexp (license undeclared) stays excluded; anything CC-BY-NC may train
   `training_only` models but must be excluded from any redistribution plan.
3. Deduplicated by canonical structure and compound family (PROTOCOL §5).
4. Decisive-evidence annotations reviewed by a domain expert per case.

## 3. Model and method (as registered in NMR_RESEARCH_REGISTER.md)

- Baseline: **CHEMLLM-2b** (Apache-2.0, verified 2026-10-31) for the cheap
  LoRA recipe dry run; **ChemLLM-7B-Chat** (Apache-2.0 tag, card carries
  commercial-licensing wording — re-read card before productizing) for the
  serious run. ChemDFM excluded (AGPL-3.0 blocker).
- Method: LoRA/QLoRA adapter on the baseline; **tuned vs untuned compared on
  the untouched test partition only**, same prompts, same seeds.
- Comparison arms stay as in PROTOCOL §7 (Claude, Phytospectra engine,
  open-weight local).

## 4. GPU execution environment (prepared, NOT run)

A restartable Colab notebook template is prepared at
`benchmark/fine_tune_gate/colab_finetune_template.ipynb` with: one-batch
memory check before full training, checkpoints every N steps, resume-from-
checkpoint cell, and exportable results. Colab GPU availability varies (see
https://research.google.com/colaboratory/faq.html); **size the run against
the hardware actually assigned at session start** — the notebook prints the
assigned GPU and refuses to start the full run if the one-batch check exceeds
memory. Quantized adapter training follows the PEFT/QLoRA guidance at
https://huggingface.co/docs/peft/developer_guides/quantization.

## 5. What would falsify the fine-tuning plan

If the untuned open-weight model matches Claude's accuracy on the pilot's
retrieval-complete cases, the comparison cost/benefit flips: ship retrieval +
prompting fixes for the open model and skip the training run. The memo is
written so that the gate can be answered "do not fine-tune" — that is a
successful outcome of the study, not a failure.
