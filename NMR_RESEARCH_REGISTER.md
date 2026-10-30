# NMR Research Register (Phase 0A)

Source document: `../LLM_SOTA_NMR_elucidation.md` (survey dated 2026-10-31).
Verification method: Hugging Face Hub pages/API, arXiv abstract pages, and a
direct code inventory of this repository. "Unresolved" means network or
authentication blocked the check on 2026-10-31 — it is NOT a finding that the
claim is false. Every entry below was checked on **2026-10-31**.

Claims asserting exclusivity ("only", "first", "no open model exists") are
inherently unverifiable by spot checks; they are marked `unverified*` and must
not be used in external communication without a dedicated search.

| Claim/resource | Primary URL | Checked date/version | Verdict | Modalities & task | Exp/synthetic | Availability | Rights/provenance | Implementation implication |
|---|---|---|---|---|---|---|---|---|
| `AI4Chem/ChemLLM-7B-Chat` is Apache-2.0, InternLM2 base, not gated | huggingface.co/AI4Chem/ChemLLM-7B-Chat | 2026-10-31, current card | **verified** | Generative chemistry LLM (text) | n/a | downloadable, not gated | Apache-2.0 confirmed on card; card adds "weights open for academic research and free commercial use, commercial licensing via support@chemllm.org" — the *card wording* is broader-risk than the tag; read the full card before productizing | Acceptable Phase 4 base-model candidate; license tag is permissive but the card's dual wording needs a second look |
| `AI4Chem/CHEMLLM-2b-1_5` is Apache-2.0 (cheap-iteration model) | huggingface.co/AI4Chem/CHEMLLM-2b-1_5 | 2026-10-31, current card | **verified** | Generative chemistry LLM (text) | n/a | downloadable | Apache-2.0 on page | OK for the LoRA-recipe dry run recommended in §6.7 |
| `OpenDFM/ChemDFM-v1.5-8B` is AGPL-3.0 (license blocker) | huggingface.co/OpenDFM/ChemDFM-v1.5-8B | 2026-10-31, current card | **verified** | Generative chemistry LLM | n/a | downloadable | AGPL-3.0 confirmed | Correctly excluded from proprietary plans; do not adopt |
| `osunlp/LlaSMol-Mistral-7B` is cc-by-4.0 (fallback base) | huggingface.co/osunlp/LlaSMol-Mistral-7B | 2026-10-31, current card | **verified** | Generative chemistry LLM | n/a | downloadable | CC BY 4.0 confirmed | Valid fallback; note attribution requirements |
| `osunlp/SMolInstruct` has 3M+ instruction samples, cc-by-4.0 | huggingface.co/datasets/osunlp/SMolInstruct | 2026-10-31 | **verified** | Text instruction (14 chemistry tasks) | mix (incl. computed) | downloadable | CC BY 4.0 confirmed, "over 3M samples" confirmed | Usable as instruction-mix tier 3 |
| `little1d/NMRTrans-Data` is MIT structure-elucidation data | huggingface.co/datasets/little1d/NMRTrans-Data | 2026-10-31 | **verified** | 1H/13C peak lists → structure | **experimental** (per NMRTrans paper) | downloadable (pickled splits) | MIT confirmed | Tier-2 public corpus; usable |
| `meaw0415/NMRGym` is MIT, 270k mols, 1H+13C peak lists, email-gated | huggingface.co/datasets/meaw0415/NMRGym | 2026-10-31 | **verified** | 1H+13C peak lists → structure (Spec2Struct) | mixed; built to fix synthetic-domain-shift issues | downloadable after email request (zhengf723@connect.hkust-gz.edu.cn) | MIT confirmed; 269,999 mols confirmed; **email-gated confirmed** | Send the gate request (§6.7 action 1) before Phase 4B planning; mirror existence not checked |
| `jablonkagroup/nmrexp-cnmr-peaklist-1.5M` license is **undeclared** | huggingface.co/datasets/jablonkagroup/nmrexp-cnmr-peaklist-1.5M | 2026-10-31 (3,712,691 rows, 804 MB observed) | **verified (risk confirmed)** | 13C peak lists + SMILES | **experimental** | downloadable without gate | **No license tag or field on the page** — matches the survey's "undeclared" | Do NOT train or publish on it without contacting authors; keep out of any artifact until cleared |
| `odanchem/NMRArena` benchmark is MIT, 105 + 1,015 leakage-free molecules | huggingface.co/datasets/odanchem/NMRArena | 2026-10-31 | **unresolved** | 1H+13C experimental spectra → structure eval | experimental | Page and API both return **HTTP 401 unauthenticated** — gated | License unreadable without auth; survey claims MIT | Request access; re-verify license terms before using as the external eval |
| `romboai/rose-1h-nmr` is the only released NMR foundation model; 1D-only encoder, Apache-2.0, ~3.2M spectra | huggingface.co/romboai/rose-1h-nmr | 2026-10-31, ROSE-Pretrain-L 7.8M params | **verified** (model facts) / **unverified*** ("only released") | 1H spectra: denoise, peak-pick, retrieval, forward shift prediction — **encoder, not generative** | experimental per cited sources | downloadable | Apache-2.0 confirmed (code + weights) | Confirms the survey: no 2D capability, not a reasoning model; does not compete with the planned agent |
| arXiv 2607.19406 "NMR Elucidation as an Agentic Search Problem" — frozen LLM + tools reaches graduate-student-level | arxiv.org/abs/2607.19406 | 2026-10-31, submitted 2026-07-07 | **verified** | Agentic: LLM + chemistry tools + validation + shift tables, 1H/13C elucidation | experimental (Alberts, van Bramer, AstraZeneca sets) | public abstract; paper text not fetched | n/a (published claim) | 71% top-1 vs 66% graduate students confirmed in abstract; supports the agent-over-end-to-end strategy |
| arXiv 2606.29776 NMRAgent — evidential, atom-level interpretability | arxiv.org/abs/2606.29776 | 2026-10-31 | **verified** | LLM agent, NMR elucidation with evidence attribution | experimental | public | n/a | Corroborates E1–E5 evidence-tier philosophy; citation for Phase 4A design |
| "No open-weight generative LLM fine-tuned for NMR peak-list→structure exists today" | (survey §1) | 2026-10-31 | **unverified*** | — | — | — | — | Spot checks cannot prove absence; treat as a working hypothesis requiring a dedicated search before any release claim |
| "This is the only 2D-NMR instruction data in existence" (Phytospectra's own traces) | (survey §6.3) | 2026-10-31 | **unverified*** | — | — | — | — | Same as above; also depends on the archives' data-rights review before any release |
| "The only LLM call in the platform is the QA critic judge" | (survey §2) | 2026-10-31 | **DISPUTED by code inventory** | — | — | — | — | `services/llm/` exposes provider-backed completion endpoints (OpenRouter client + Vertex client, `services/llm/app/clients.py`, `openrouter_client.py`, `main.py` route calling `client.complete(...)`) beyond the QA critic. The claim is true only of the *elucidation* service. Update the survey wording and keep the inventory in `CAPABILITY_REGISTER.md` |
| Zotero local API disabled; SQLite read directly (§7.3) | local | 2026-10-31 | **verified in source doc**; not re-tested today | — | — | — | — | If Zotero MCP is needed later, re-enable port 23119 first |

## Consequences for the plan

1. **Strategy is sound**: the two load-bearing literature claims (agentic
   frontier, NMRAgent's evidential philosophy) verify, and the permissive base
   models (ChemLLM-7B-Chat, CHEMLLM-2b, LlaSMol) all verify with their stated
   licenses.
2. **Data-tier actions before Phase 4B**: email-gate request for NMRGym,
   access request for NMRArena (also re-verify its license), and a hard
   no-contact/no-train rule on all `jablonkagroup/*` and `SpectrumWorld/*`
   datasets until their license is declared.
3. **Correction owed to the survey**: the "only LLM call is the QA critic"
   sentence must be narrowed to the elucidation service; the platform-wide LLM
   inventory is tracked in `CAPABILITY_REGISTER.md`.
4. **Exclusivity claims** ("only", "first", "no open model exists") stay
   marked unverified and out of external claims until a dedicated search says
   otherwise.
