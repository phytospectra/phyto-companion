# Model-comparison follow-through — 2026-11-12

Scope: triage the November 11 literature proposals (LLM_SOTA_NMR_elucidation.md
§8.2, phytospectra-paper/RELATED-WORK-PROPOSAL-2026-11-11.md) against the
locked comparison plan, verify what can be verified without spending, and
record the current budget state. No downloads, no paid inference, no training
were performed for this decision.

## 1. Where each proposal belongs

The controlled comparison (`benchmark/agent_comparison/RESEARCH-PLAN.md`,
arms A–E plus the end-to-end Antigravity track) stays exactly as proposed.
**No new arm is added to it.** The proposed "cheap fourth arm" (NaFM or
NPCLM embeddings as a non-LLM candidate ranker) is triaged to a **separate
exploratory retrieval experiment**, which is not part of, and must not be
reported as part of, the controlled comparison.

| Proposal (Nov 11) | Triage | Status |
|---|---|---|
| NaFM/NPCLM embedding candidate ranker ("cheap fourth arm") | Separate exploratory retrieval experiment, pre-declared | Proposed only — blocked on weight/interface verification (§2) |
| NPCLM + NaFM as fine-tuning citation backbone | Banked as reopen-condition citations on the fine-tuning gate | Future research only — NOT-JUSTIFIED decision stands |
| DiSE external baseline (de novo arm) | Future research (roadmap) | Proposed only |
| BGC-MLM, unified BGC↔NP retrieval | Future research (workflow C roadmap) | Proposed only |
| MoleculeSTM structure–text retrieval | Future research (copilot UI) | Proposed only |
| Drug-like vs NP-like subset reporting, pairwise ordering metric, canonical isomeric SMILES | Study protocol additions (§3–4 of the plan) | Adopted into protocol wording only; no experimental run |
| Manuscript Background paragraph + reference entries | Manuscript edit | Explicitly NOT applied (proposal only; author approval required) |

## 2. NaFM / NPCLM verification (metadata only, 2026-11-12)

Verified against the arXiv abstract pages; nothing was downloaded.

| Item | NaFM | NPCLM |
|---|---|---|
| Citation | Ding Y, Qiang B, Li S, et al. *Pretraining a Foundation Model for Small-Molecule Natural Products.* Nat Mach Intell 2026, 8:777–788; arXiv:2503.17656 | Wang H-H, Sultan A, Volkamer A, Klakow D. *Foundation Chemical Language Models for de novo NP Generation and Property Prediction.* arXiv:2602.13958; NeurIPS 2026 workshop |
| Modality | Scaffold-aware graph pretraining (scaffold-subgraph reconstruction + contrastive learning) | SMILES language models (Mamba/Mamba-2/GPT variants, several tokenizations) |
| Training data | ~0.6M COCONUT natural products (per the 2026-11-11 survey verification) | ~1M NP SMILES |
| Task fit | Candidate embedding/retrieval is plausible but not the paper's stated task | Generation + property prediction; NOT a retrieval embedder by construction |
| Weights / usable interface | **Not verifiable from the abstract page** — no HF/GitHub URL or license stated | **Not verifiable from the abstract page** — no HF/GitHub URL or license stated |
| License | Unverified | Unverified |
| Verdict for the retrieval experiment | **Blocked** until weights, license, dependencies and an inference interface are actually located | **Blocked** — same, plus the model is a generator, so "retrieval coverage" would need an embedding read-out the paper does not define |

Neither model can be described as a free, drop-in retrieval arm. Any future
experiment must first verify the released artifacts and then pre-declare
(§3); until then the status is **proposed, not implemented, blocked**.

## 3. Pre-declaration sketch if the retrieval experiment is ever unblocked

Kept here so the decision is reversible with evidence, not re-litigated:

- **Task**: candidate retrieval for the known-formula reference-ranking
  workflow — does an NP-pretrained embedding surface the correct candidate
  within the ranked list on the same query packets the controlled comparison
  uses?
- **Candidate pool**: a fixed, rights-checked COCONUT/LOTUS subset, with
  membership disclosure recorded per candidate (train/validation overlap
  against the embedder's pretraining corpus must be checked; COCONUT
  membership cannot be assumed absent).
- **Separation**: retrieval coverage (does the correct candidate enter the
  list at all) is measured separately from reranking and interpretation
  accuracy; an embedding ranker is never scored as if it produced an
  evidence-based identification.
- **Deterministic baseline**: the existing curated reference library /
  formula-constrained enumeration, so the retrieval gain is measured against
  a non-LLM anchor.
- **Costs**: model downloads, embedding computation and storage must be
  accounted — the arm is not cost-free, and the US$50 OpenRouter ceiling
  does not fund it (it is not an OpenRouter spend; any compute needs its own
  approved allowance).

## 4. Budget and access state (checked 2026-11-12, safely)

- **OpenRouter**: `OPENROUTER_API_KEY` is absent from this environment, so
  the live ceiling recheck cannot run (the runner fails closed without it).
  No paid request was executed; the previously observed ~US$12.35-remaining
  figure is not re-verified. The US$50 total pilot ceiling remains the only
  authorization and is not treated as refreshed.
- **Colab**: `colab usage` reports **0.00 compute units, 0.00/hr usage,
  0 active assignments**. No compute allowance exists for this work; no VM,
  upload, download or training was performed.
- **Local models**: no `ollama`/`llama-cli`/`llama-server` on PATH; the local
  machine is not a model host (8 GiB RAM recorded earlier).

## 5. Status ledger

- Controlled comparison arms A–E + Antigravity track: **proposed, not run,
  not modified by this decision.**
- NaFM/NPCLM retrieval arm: **proposed, blocked on artifact verification.**
- Fine-tuning: **NOT JUSTIFIED (2026-11-09 decision stands)**; NPCLM/NaFM
  citations banked as reopen conditions.
- Gemini API output remains a Gemini API result, not an Antigravity
  evaluation; the end-to-end system comparison requires actual Antigravity
  access, which is still not available.
