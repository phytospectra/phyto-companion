# Phytospectra

**An agentic AI companion for academic natural product drug discovery.**

Phytospectra unifies NMR spectroscopy, mass spectrometry, infrared spectroscopy, and
metabolomics into a single agentic workflow: a complex plant extract goes in; ranked,
evidence-graded structural and biological hypotheses come out.

> The platform is **not an oracle. It is a companion.** It never claims identification it
> cannot support, always reports uncertainty and evidence gaps, and requires
> human-in-the-loop review at every critical decision.

---

## Project Vision

Academic natural product researchers — small groups (2–10 people), grant-funded, with
limited computational support — need reproducible, citable structural elucidation and
mechanistic hypotheses without becoming software engineers. Phytospectra works in a
browser, requires no installation, and produces fully provenance-tracked results.

Scientific foundations:

1. **SUMMIT MS/NMR hybrid workflow** (Bingol et al., 2015, *Analytical Chemistry*):
   formula enumeration → structural manifold → in silico NMR prediction → cross-modal
   matching → annotation propagation.
2. **NMR molecular networking** (Stienstra et al., 2025, *Communications Chemistry*):
   HSQC-based networking with the Modified Hungarian distance metric and algorithmic
   molecular networking.
3. **The fused MS²–HSQC molecular network** — the central architectural innovation.
   Nodes represent compounds; edges represent MS/MS similarity, HSQC similarity, or both,
   enabling cross-modal annotation propagation. This is what distinguishes Phytospectra
   from MS-only or NMR-only tools.
4. **GNPS molecular networking** (Wang et al., 2016; Aron et al., 2020): classical MN,
   FBMN, IIMN, bioactivity-based MN (Nothias et al., 2018), building-blocks MN
   (He et al., 2021), and the GNPS tool suite (NAP, Dereplicator+, MS2LDA, MolNetEnhancer).
5. **Network pharmacology** (Banerjee et al., 2019–2025): LC-MS/MS and NMR profiles
   connected to molecular mechanisms through pathway analysis.
6. **ShiftMPNN architecture** (SUMMIT v4 benchmark): message-passing GNN with GRU updates,
   edge features, and separate ¹H/¹³C output heads; Hungarian matching with multiplicity
   penalty; novel structure generation by chemical graph editing.

### Core principles

| Principle | Commitment |
|---|---|
| Honesty over optimism | No identification claims without support; ranking intervals, not just top-1; "candidate" ≠ "tentative match" ≠ "confirmed" |
| Provenance is mandatory | SHA256 for every input; model/database versions and parameters recorded; every agent action logged |
| Human-in-the-loop | Checkpoints before identification claims, novel-structure proposals, and experiment recommendations |
| Tool tiering | Tier 1 deterministic (RDKit, statistics) → Tier 2 ML-validated (ShiftMPNN, SIRIUS) → Tier 3 LLM-generated (mandatory QA/Critic verification) |
| Novelty without overclaiming | Flag when a compound *may* be novel; never claim novelty |
| Academic-friendly | Freemium, browser-based, reproducible, citable, open-source LLM option for data sovereignty |

---

## Architecture Overview

### Split-plane design

```
┌─────────────────────────────┐       ┌──────────────────────────────────┐
│  LOCAL PLANE (dev machine)  │       │  CLOUD PLANE (Google Cloud)      │
│  MacBook M2, 8 GB, <2 GB    │       │  Scalable, pay-per-use           │
│  RAM usage                  │       │                                  │
│                             │       │  • All spectral processing        │
│  • Code generation/editing  │  API  │    (NMR, MS, IR)                 │
│  • Unit tests (mocks)       │ ────► │  • All ML inference (ShiftMPNN,   │
│  • API contract validation  │       │    LLM on Vertex AI GPUs)         │
│  • Pydantic validation      │       │  • All database queries            │
│  • IaC generation           │       │  • GNPS job submission/retrieval   │
│  • Documentation            │       │  • Metabolomics statistics         │
│                             │       │  • Network pharmacology queries    │
│  Mocks & fixtures only:     │       │                                  │
│  tiny spectra, stub LLM,    │       │  Cloud Run · Vertex AI · Cloud SQL│
│  --dry-run mode             │       │  BigQuery · GCS · Firebase        │
└─────────────────────────────┘       └──────────────────────────────────┘
```

The local plane **never** loads large databases (COCONUT, NMRShiftDB2, NP-MRD), runs GPU
inference, submits real GNPS jobs, trains models, or processes spectral files >100 MB.

### Services

| Service | Responsibility |
|---|---|
| `services/ingestion` | Bruker/Varian/JEOL NMR, mzML/mzXML/MGF MS, JCAMP-DX/CSV IR parsing; Pydantic validation; SHA256 provenance manifests; QC flagging (solvent peaks, impurities, missing quaternary carbons) |
| `services/metabolomics` | Feature detection, normalization, PCA/PLS-DA/OPLS-DA, univariate statistics with FDR, biomarker discovery, dose–response, multi-block fusion (DIABLO, MFA), pathway enrichment |
| `services/networking` | GNPS2 API client (classical MN, FBMN, IIMN), HSQC-based NMR networking, **fused MS²–HSQC network**, bioactivity networking, building-blocks networking, Cytoscape export, MassIVE deposition |
| `services/elucidation` | ShiftMPNN NMR prediction, Hungarian matching with multiplicity penalty, candidate ranking, novel structure generation, site localization, HMBC/COSY constraint checking, evidence graphs, uncertainty quantification |
| `services/pharmacology` | Target prediction, pathway mapping, network pharmacology, synergy analysis, ADMET, knowledge graph (compounds–targets–pathways–diseases–traditional uses–literature) |
| `services/literature` | PubMed/Semantic Scholar/OpenAlex retrieval, DOI-then-PMID deduplication, BM25 ranking, per-source provenance |
| `services/nmr_search` | Structure search by observed HSQC peaks against NP-MRD, BMRB and nmrshiftdb2, with per-record licence metadata and a commercial-tier filter |
| `services/llm` | Grounded Tier-3 generation; provider-agnostic (Vertex AI primary, OpenRouter fallback); refuses any request without retrieved context |
| `services/orchestrator` | Planner agent, specialist agents (Spectroscopist, Metabolomist, Chemist, Biologist, QA/Critic), tool tiering, sandboxed code-as-actions, LLM-as-judge, audit logging, human-in-the-loop checkpoints |
| `services/frontend` | Browser-based UI: guided workflows, spectrum viewers, network visualization, candidate tables, uncertainty badges, evidence panels |
| `shared/phyto_shared` | Pydantic models, JSON schemas, provenance/SHA256 utilities, uncertainty quantification, constants (solvents, adducts, MSI levels), domain exceptions |
| `infrastructure/` | Terraform for GCP (Cloud Run, Vertex AI, Cloud SQL, BigQuery, GCS, IAM) + Cloud Build CI/CD |

### Repository layout

```
phyto-companion/
├── README.md                  ← you are here
├── pyproject.toml             (Step 2+)
├── docker-compose.yml         (local development)
├── Makefile
├── .github/workflows/         (ci.yml, deploy.yml)
├── infrastructure/            (terraform/, cloudbuild.yaml)
├── services/                  (ingestion, metabolomics, networking, elucidation,
│                               pharmacology, orchestrator, frontend)
├── shared/phyto_shared/       (models, schemas, provenance, uncertainty, constants, exceptions)
├── tests/                     (fixtures: 5–10 spectra, 3–5 compounds; unit + dry-run tests)
└── docs/                      (architecture, api_reference, deployment_guide,
                                user_guide, scientific_methods)
```

---

## Quick Start (Local Development)

Requirements: Python 3.11+, Docker Desktop, ~2 GB free RAM. No GPU, no GCP account,
no large databases needed.

```bash
cd phyto-companion

# 1. Create an isolated environment and install dependencies
make install          # .venv via uv; pydantic, pytest, fastapi, uvicorn, httpx

# 2. Run the test suite — all external services mocked, LLM stubbed
make test             # pytest (shared + orchestrator; <1 s, no cloud calls)

# 3. Validate workflows end-to-end without calling any cloud service
make dry-run          # the whole suite runs in dry-run mode by design

# 4. Run the orchestrator API locally (mock tools, no cloud)
make serve            # uvicorn on :8000 — POST /analysis with "dry_run": true

# 5. (Optional) Run services in lightweight containers (added in Step 8)
```

Local guardrails are enforced in code: mock GNPS/Vertex/DB clients are injected unless
`PHYTO_ENV=cloud`, and `--dry-run` validates the full workflow graph without executing
any cloud call.

### LLM provider selection

Generation is provider-agnostic (ADR-0034). Vertex AI is the production backbone; OpenRouter
is the experimentation/fallback provider:

| Variable | Purpose |
|---|---|
| `PHYTO_LLM_PROVIDER` | Deployment default: `vertex`, `openrouter` or `local`. Defaults to `vertex` under `PHYTO_ENV=cloud`, otherwise `local` (offline mock). |
| `OPENROUTER_API_KEY` | OpenRouter key. Required only when that provider is selected; supplied by Secret Manager in production. |
| `OPENROUTER_MODEL` | Model slug, default `qwen/qwen3-4b:free`. |
| `VERTEX_ENDPOINT_ID`, `GOOGLE_CLOUD_PROJECT` | Vertex endpoint and project (the fine-tuned Qwen 2.5 7B + QLoRA deployment). |

Any request may override the default with a `provider` field. Grounding rules and the
QA/Critic gate are identical for every provider.

## Deployment Guide (GCP) — Summary

Full walkthrough in `docs/deployment_guide.md` (generated in Step 10).

```bash
# One-time setup
gcloud auth login && gcloud config set project YOUR_PROJECT
gsutil mb -l us-central1 gs://YOUR_PROJECT-tfstate   # remote Terraform state

# Provision infrastructure (Cloud Run, Vertex AI endpoints, Cloud SQL,
# BigQuery datasets, GCS buckets, IAM, Secret Manager)
cd infrastructure/terraform
terraform init && terraform apply

# Build & deploy each service via Cloud Build
gcloud builds submit --config ../cloudbuild.yaml services/ingestion
# ... repeated per service; frontend deploys to Firebase Hosting
```

GPU-backed inference (ShiftMPNN, fine-tuned Qwen 3 8B / Llama 3.1 8B) runs on Vertex AI
endpoints (L4 GPU); everything else stays on CPU-friendly Cloud Run. All credentials live
in Secret Manager; nothing is committed.

## Data Availability

The platform is validated against **public** data only:

- **NP-MRD** public raw NMR archive (CC-BY-NC) — cloud-only, synced to GCS by
  `scripts/sync_public_archive_to_gcs.sh`; never committed.
- **SUMMIT benchmark outputs** — small CSV fixtures under
  `tests/fixtures/real_npmrd/`.

No per-sample acquisitions are included. Full provenance, licences, SHA256
hashes, and integration modes are recorded in
[DATA_SOURCES.md](docs/DATA_SOURCES.md). The read-only inventory of the source
workspace is in `data/inventory/dataset_inventory.json`.

## Contributing

- **Scientific honesty first.** Any PR that hides uncertainty, drops alternatives, or
  weakens provenance will be rejected — even if it improves benchmark numbers.
- **Split-plane discipline.** New features must ship with mocks + fixtures so the full
  test suite runs on a laptop in minutes. Anything needing >2 GB RAM locally belongs in
  the cloud plane behind a client interface.
- **Tests required.** Every module lands with unit tests against fixtures and a
  `--dry-run` integration path. No test trains on test data; demo data must be clearly
  labeled synthetic.
- **Provenance required.** Any new tool integrated into the workflow must record model
  version, database version, parameters, and input/output hashes in the audit trail.
- **Tool tiering respected.** Deterministic (Tier 1) → ML with confidence scores (Tier 2)
  → LLM hypotheses gated by QA/Critic (Tier 3). LLM output may never alter data or scores.
- Conventional commits (`feat:`, `fix:`, `sci:` for scientific-method changes); CI must
  pass (lint, unit tests, API contract validation) before merge.

## Build Status

Step-by-step delivery (per the working plan — each step awaits review before the next):

- [x] **Step 1** — Directory tree + README
- [x] **Step 2** — Shared library (`phyto_shared`)
- [x] **Step 3** — Orchestrator service
- [x] **Step 4** — Networking service (GNPS + NMR + fused MS²–HSQC)
- [x] **Step 5** — Elucidation service
- [ ] **Step 6** — Metabolomics + pharmacology services (delivered; awaiting review)
- [ ] **Step 7** — Frontend
- [ ] **Step 8** — Terraform infrastructure + deployment scripts
- [ ] **Step 9** — Test suite
- [ ] **Step 10** — Documentation

## License & Citation

To be decided with the research group (open-source intended for the academic core;
freemium hosting for advanced tiers). Every analysis emits a provenance manifest and
model/version metadata so results remain citable and reproducible.
