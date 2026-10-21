# Data Sources

This document records **every** dataset the platform is validated against,
with its provenance, licence, integrity hash, and integration mode. It is the
single source of truth for the question "where did this data come from, and may
I redistribute it?"

> **Status note.** No per-sample Bruker acquisitions are included in the current
> data. The platform's real-data validation uses **NP-MRD (public)** and the
> **SUMMIT benchmark outputs (public)**. Owner-specific experimental data will be
> integrated separately when available — see "Pending datasets" at the end.

---

## 1. NP-MRD Public Raw NMR Archive

| Field | Value |
|---|---|
| Dataset | NP-MRD experimental spectra archive |
| URL | https://np-mrd.org/system/downloads/current/experimental_spectra.zip |
| Licence | **CC-BY-NC** (NP-MRD) — public; research/non-commercial use with attribution |
| Attribution | NP-MRD — Natural Products Magnetic Resonance Database (https://np-mrd.org) |
| Size | 13,721,283,834 bytes (~13.7 GB) |
| Download date | NP-MRD rolling "current" release |
| GCS Destination | `gs://phyto-companion-dev-public-data/npmrd/raw/experimental_spectra.zip` |
| Sync tool | `scripts/sync_public_archive_to_gcs.sh` (supports local or `--from-url` Cloud Shell mode) |
| Committed to git? | **No** — 13.7 GB; public cloud mirror |
| Integration mode | **Cloud-only (Mode D)** |

> **Upload destination.** The archive is uploaded to
> `gs://phyto-companion-dev-public-data/npmrd/raw/` when the Cloud Shell workflow
> (`scripts/sync_public_archive_to_gcs.sh --from-url`) runs. The bucket name retains
> "phyto-companion" for consistency with the GCP project ID, even though the public
> product is now named Phytospectra (ADR-0035). The bucket is public-read data only;
> nothing is written under the other subprefixes by this sync.

### Commercial Use Prohibition Warning (ADR-0026)

> **WARNING:** NP-MRD data is licensed CC-BY-NC, which prohibits commercial use.
> The platform's FREE academic tier may serve NP-MRD data (research use) with
> attribution. The PRO tier must NOT include NP-MRD data unless a separate commercial
> license is obtained from NP-MRD. The Pro-tier data strategy must be resolved
> before deployment (see ADR-0026, pending).

---

## 2. SUMMIT Benchmark Workspace CSVs (Committed Fixtures)

Small, public benchmark CSV outputs and assigned peak lists from the SUMMIT
v4 benchmark workspace are committed as local references under
`tests/fixtures/real_npmrd/` (Step 3B). Every file is small (< 25 KB, ~70 KB total)
and contains no raw time-domain FID data.

| Fixture Path | Size (bytes) | SHA256 Checksum |
|---|---|---|
| `real_npmrd/andrographolide_hsqc_literature.csv` | 302 | `32eacebda81a2f43f03eabaa9664c1efc0a88bed7c6ece9a08b4c8d49a8cbe13` |
| `real_npmrd/benchmark_v4/partA_by_class.csv` | 1054 | `82ba918e1ce8b2a433a7c805630151cd70ca41886cc61c35e28d10b7b344064e` |
| `real_npmrd/benchmark_v4/partA_failures.csv` | 5173 | `78e2535051b4c00afd76cd15e06347d099574f38d03b2ccb9c3e9e646c525541` |
| `real_npmrd/benchmark_v4/partA_methods.csv` | 649 | `a3c1a81e41f14af13f0f65215e8788dcf51cabb08559be1c451d523736afdac2` |
| `real_npmrd/benchmark_v4/partA_per_compound.csv` | 23273 | `428842ab6819c9f3813f416573c5a0171138d9a5db66d3dcb52b8d978b5c0c29` |
| `real_npmrd/benchmark_v4/partB_localization.csv` | 124 | `351320e4f6732364aba1718b76e84f9e88a7cde825a4d7ee1389b9a22663f4e9` |
| `real_npmrd/benchmark_v4/partB_v4.csv` | 7477 | `575d214316e13551098bd99edac11e844f5128d171f656a558cea8ba4ce27d6c` |
| `real_npmrd/benchmark_v4/partC_v4.csv` | 5975 | `43b5c61392a98189e0c1a919f20d2a9dfc2dcb429f8da37a28b6d7358bafc41f` |
| `real_npmrd/benchmark_v4/summary_v4.txt` | 3586 | `d9335feb87bbb61fd7db2085ee945ff5b443d633750169662da49bb0dbdfe0b0` |

- **Source:** SUMMIT v4 benchmark proof-of-concept outputs and NP-MRD literature assignments.
- **License:** CC-BY-NC (NP-MRD).
- **Validation:** Tested via `tests/test_real_npmrd_fixtures.py` and documented in `data/inventory/validation_report.json`.

---

## 3. What Was Deliberately NOT Integrated

| Item | Reason |
|---|---|
| `summit_poc/*.pt` (SHIFT-MPNN checkpoints, HOSE tables) | Reference-implementation artifacts, not platform code (ADR-0025) |
| `summit_poc/benchmark_v2`, `benchmark_v3` | Superseded benchmark runs; retained in external workspace for cross-checking |
| Database dumps (`NP-MRD/`, `NPAtlas/`, `coconut_csv_lite*.zip`, `nmrshiftdb2*`) | Public mirrors; fetched on demand in the cloud plane, never committed to git |
| `experimental_spectra.zip` (13.7 GB) | Public raw archive; transferred directly to GCS via `scripts/sync_public_archive_to_gcs.sh` |
| Cloud placeholders (51 files, 14.91 GB) | Not materialized locally; never opened offline |

---

## 4. PII Handling and Redaction

The local data folder path contains personal account details (`[redacted]@...`).
Every committed document, manifest, and fixture metadata uses a redacted description
("the owner's Google Drive SUMMIT workspace"). All inventory reports record relative
paths.

---

## 5. Pending Datasets

- **Owner-specific experimental Bruker acquisitions:** Not present in the analyzed workspace.
  When provided, Bruker raw parsing will be integrated and validated separately (ADR-0024).
- **Curated public demo dataset:** Frontend demo mode remains synthetic until a curated
  public dataset is integrated in Step 8 / deployment validation.
