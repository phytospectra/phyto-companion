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

> **Governance record (ADR-0056).** Every external source below carries its
> licence, the data tiers it may feed, whether commercial use is allowed, and
> the `source_dataset` ID used in provenance manifests
> (`phyto_shared.data_governance.SOURCE_DATASET_IDS`). The CI completeness
> test (`tests/test_data_governance/test_data_sources_completeness.py`) fails
> if a source string used in `services/` has no governance record here.

| Field | Value |
|---|---|
| Licence | **CC-BY-NC** (NP-MRD depositors) |
| Allowed tiers | `public` (free academic use), `training_only` |
| Commercial use | **no** — never in commercial analytics, the proprietary tier, or a sold model |
| Source dataset ID used in provenance | `np_mrd` |
| Derived fixtures (`tests/fixtures/real_npmrd/`, SUMMIT CSVs) | same terms; ID `np_mrd_fixtures` |

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
- **Governance (ADR-0056):** Licence CC-BY-NC; allowed tiers `public` (free
  academic use) and `training_only`; commercial use **no**; source dataset ID
  `np_mrd_fixtures`.

---

## 3. External database sources — governance records

### GNPS (GNPS2 molecular networking)

| Field | Value |
|---|---|
| Dataset | GNPS2 public spectral libraries and molecular networking jobs (`https://gnps2.org`) |
| Licence | **mixed** — GNPS public data is CC0-dedicated or CC-BY by individual contributors; verify per dataset at refresh |
| Allowed tiers | `public`, `proprietary`, `training_only` |
| Commercial use | **yes** (for CC0/CC-BY portions; verify per dataset) |
| Source dataset ID used in provenance | `gnps_public` |

### COCONUT

| Field | Value |
|---|---|
| Dataset | COCONUT natural products (structure database; dereferenced in `services/elucidation` structure-DB layer) |
| Licence | **CC-BY** (COCONUT releases are CC-BY 4.0; verify at refresh) |
| Allowed tiers | `public`, `proprietary`, `training_only` |
| Commercial use | **yes** (with attribution) |
| Source dataset ID used in provenance | `coconut` |

### LOTUS

| Field | Value |
|---|---|
| Dataset | LOTUS natural products, organism–compound links (pilot snapshot `services/taxon_prior/app/data/lotus_snapshot.json`; full export on Zenodo) |
| Licence | **CC0** (full LOTUS database releases are CC0; verify at refresh) |
| Allowed tiers | `public`, `proprietary`, `training_only` |
| Commercial use | **yes** |
| Source dataset ID used in provenance | `lotus` |

### KNApSAcK

| Field | Value |
|---|---|
| Dataset | KNApSAcK species–metabolite core (pilot snapshot `knapsack_snapshot.json`) |
| Licence | **academic use** (per KNApSAcK core terms; verify at refresh) |
| Allowed tiers | `public` (free academic use), `training_only` |
| Commercial use | **conditional** — requires permission from KNApSAcK; treat as non-commercial until cleared |
| Source dataset ID used in provenance | `knapsack` |

### NPASS

| Field | Value |
|---|---|
| Dataset | NPASS natural product activity–species database (pilot snapshot `npass_snapshot.json`) |
| Licence | **academic use** (per NPASS terms; verify at refresh) |
| Allowed tiers | `public` (free academic use), `training_only` |
| Commercial use | **conditional** — requires permission; treat as non-commercial until cleared |
| Source dataset ID used in provenance | `npass` |

### Platform-internal sources (governance records for completeness)

| Source | Licence | Allowed tiers | Commercial use | Source dataset ID |
|---|---|---|---|---|
| User-contributed spectra and isolation records | user's own data; platform rights per ToS (docs/DATA_GOVERNANCE.md §5) | all three (tier is the contribution's label) | per tier label | `user_upload` |
| In-house authenticated reference standards (owner lab) | owner proprietary | `proprietary`, `training_only`, `public` after curator review | **yes** (owner's own data) | `in_house_standard` |
| BMRB | CC-BY-4.0 | all three | **yes** (attribution) | `bmrb` |
| nmrshiftdb2 | CC-BY-SA | `public` (free academic use), `training_only` | **conditional** — share-alike bars inclusion in a licensed corpus; confined to the free academic tier (`phyto_shared.data_governance.NON_COMMERCIAL_SOURCES`) | `nmrshiftdb2` |
| GBIF species taxonomy | CC-BY 4.0 (GBIF-mediated data) | all three | **yes** (attribution) | `gbif` |
| ITIS taxonomy | public domain (US government) | all three | **yes** | `itis` |

---

## 4. What Was Deliberately NOT Integrated

| Item | Reason |
|---|---|
| `summit_poc/*.pt` (SHIFT-MPNN checkpoints, HOSE tables) | Reference-implementation artifacts, not platform code (ADR-0025) |
| `summit_poc/benchmark_v2`, `benchmark_v3` | Superseded benchmark runs; retained in external workspace for cross-checking |
| Database dumps (`NP-MRD/`, `NPAtlas/`, `coconut_csv_lite*.zip`, `nmrshiftdb2*`) | Public mirrors; fetched on demand in the cloud plane, never committed to git |
| `experimental_spectra.zip` (13.7 GB) | Public raw archive; transferred directly to GCS via `scripts/sync_public_archive_to_gcs.sh` |
| Cloud placeholders (51 files, 14.91 GB) | Not materialized locally; never opened offline |

---

## 5. PII Handling and Redaction

The local data folder path contains personal account details (`[redacted]@...`).
Every committed document, manifest, and fixture metadata uses a redacted description
("the owner's Google Drive SUMMIT workspace"). All inventory reports record relative
paths.

---

## 6. Taxon Prior Databases (LOTUS, KNApSAcK, NPASS) — pilot snapshots

Added 2026-10-22 for the taxonomy-aware reranking layer (ADR-0052, ADR-0054).
All three live interfaces were probed on 2026-10-22; none was usable, so the
pilot ships curated snapshots under `services/taxon_prior/app/data/`.

| Field | LOTUS | KNApSAcK | NPASS |
|---|---|---|---|
| Dataset | LOTUS natural products (dereferenced organism–compound links) | KNApSAcK species–metabolite core | NPASS natural product activity–species database |
| Live interface | `https://api.lotus.naturalproducts.net` | `http://www.knapsackfamily.com/KNApSAcK_core/` | `http://bcs.cmus.ac.kr:8313/NPASS2/` |
| Probe result (2026-10-22) | connection refused | 301 redirect; no stable JSON API | host unreachable |
| Pilot integration | snapshot `lotus_snapshot.json` + coded live path | snapshot `knapsack_snapshot.json` (static by design) | snapshot `npass_snapshot.json` + coded live path |
| Snapshot size | 9 entries | 5 entries | 3 entries |
| Licence | open (CC-BY-style per LOTUS releases; verify at refresh) | academic use (verify at refresh) | academic use (verify at refresh) |
| Committed to git? | **Yes** — curated pilot fixtures | **Yes** | **Yes** |
| Integration mode | **Local + cloud (snapshot), live path retained** | **Local + cloud (snapshot only)** | **Local + cloud (snapshot), live path retained** |

> **Provenance note.** Snapshot entries carry a `reference` field naming the
> original source; every reranked candidate exposes these as
> `taxon_prior_provenance` (ADR-0052). Replacing a snapshot with a full
> database export is a data refresh, not a code change; re-verify the licence
> column at each refresh.

## 7. Taxonomy Resolution (GBIF, ITIS)

| Field | GBIF (primary) | ITIS (fallback) |
|---|---|---|
| Endpoint | `https://api.gbif.org/v1/species/match`, `/v1/species/{key}` | `https://www.itis.gov/ITISWebService/jsonservice/` |
| Probe result (2026-10-22) | working (exact match, confidence 99) | working |
| Auth | none | none |
| Integration mode | live (cloud plane); `MockTaxonomyClient` locally | live fallback (cloud plane) |
| Decision | ADR-0053 | ADR-0053 |

## 8. Pending Datasets

- **Owner-specific experimental Bruker acquisitions:** Not present in the analyzed workspace.
  When provided, Bruker raw parsing will be integrated and validated separately (ADR-0024).
- **Curated public demo dataset:** Frontend demo mode remains synthetic until a curated
  public dataset is integrated in Step 8 / deployment validation.

## 9. Pharmacopoeial Marker Data (committed curated file)

Added 2026-10-22 for the pharmacopoeial marker prior (ADR-0066).

| Field | Value |
|---|---|
| Dataset | `data/pharmacopoeial_markers.json` — per-species QC marker compounds with expected RT ranges and UV maxima |
| Species covered | 26 medicinal plants with pharmacopoeial monographs (IP / USP / Ph. Eur. / ChP) |
| Entries | Per marker: name, SMILES, expected RT range (min, relative to `REFERENCE_GRADIENT` — 30-min 5→95% C18, ADR-0063), expected λmax (nm), per-entry `reference` citing the source monograph |
| Provenance | Each entry names the pharmacopoeial source in its `reference` field; every marker evidence item in a report carries that reference to the user |
| Licence | **Verify per monograph at each refresh.** Pharmacopoeial monographs are copyrighted works; the committed file stores factual marker data (compound identity, RT window, λmax) with attribution, not monograph text. Re-check the licence column before redistributing a refreshed file. |
| Committed to git? | **Yes** — small curated factual table |
| Integration mode | **Local + cloud** (loaded by `services/taxon_prior/app/markers.py`; validated at load, invalid entries dropped with a logged reason) |

> **Honesty note (ADR-0066).** These are EXPECTATIONS, not observations: a
> marker match is weak supporting evidence (+0.05) and a miss is evidence
> against the species-level identity claim. The file must be refreshed from
> the monographs whenever the reference gradient definition changes, because
> every RT window is quoted against it.

## 10. Retention-Time Model Training Data (documented lineage, not committed)

Added 2026-10-22 for the retention service (ADR-0063).

| Field | Value |
|---|---|
| Dataset | METLIN SMRT small-molecule retention time dataset (Domingo-Almenara et al. 2019, Nature Biotechnology, https://doi.org/10.1038/s41587-019-0267-4) |
| Size | ~80,000 compounds with experimental RT on standardized LC-MS gradients |
| Licence | open (CC-BY-style per METLIN SMRT release; verify at refresh) |
| Committed to git? | **No** — the pilot does not ship the dataset; the production model trains on it in the cloud plane |
| Pilot stand-in | Hand-curated 20-compound lookup table (literature-typical RTs on the reference gradient) + logP descriptor baseline, both inside `services/retention/app/main.py` |
| NP fine-tuning data | Surveyed 2026-10-22: public natural-product RT sets too sparse to fine-tune without overfitting; deferred (ADR-0063). When a fine-tuned model lands, its version string changes and the lookup becomes the fallback. |

## 11. MSV000090047 — Commercial standards of Streptomyces antibiotics (M0-b LC–MS acceptance dataset)

Added 2026-10-31 for the MZmine live integration (ADR-0085).

| Field | Value |
|---|---|
| Repository | MassIVE (UCSD), accession `MSV000090047`, marked **Public** |
| Landing page | https://massive.ucsd.edu/ProteoSAFe/dataset.jsp?accession=MSV000090047 |
| Dataset license (MassIVE page, verbatim) | `[dataset license: CC0 1.0 Universal (CC0 1.0)]` → http://creativecommons.org/publicdomain/zero/1.0/ |
| Zenodo mirror | DOI `10.5281/zenodo.6948449` — metadata records **CC-BY-4.0** (divergence from MassIVE's CC0; treat CC-BY-4.0 as the stricter applicable term and attribute Kontou & Weber when redistributing) |
| Publication | UmetaFlow, J. Cheminform. 2023, doi:10.1186/s13321-023-00724-w |
| Download route | `https://massive.ucsd.edu/ProteoSAFe/DownloadResultFile?file=f.MSV000090047/peak/<name>.mzML&forceDownload=true` (verified 200; the `ccms_peak/` copies are different bytes and 500 on this route) |
| File used | `20220426_UMETAB266_POS_Tetracycline.mzML` (positive mode, Thermo Orbitrap ID-X DDA, converted by ThermoRawFileParser 1.3.4), 33,379,150 bytes |
| MD5 | `396f11793f20870de150b56408a33c21` (authoritative: Zenodo record 6948449 `files[].checksum`; recomputed locally after download — match) |
| SHA-256 (local) | `fa3910452ae96f754a3eb05b2cf07ae2993f053fa4862b78d365cf1d857f6b9e` |
| Committed to git? | **No** — `data/raw/` is gitignored (bulk data rule) |
| Integration mode | Local acceptance anchor for the pinned MZmine batch integration |

Integrity caveats: the mzML's embedded `<fileChecksum>` could not be
reconciled with any local hash (whole-file SHA-1, prefix-to-index SHA-1)
— do not use it; anchor on the Zenodo MD5. Only `peak/` files are
covered by those checksums.

Independently checkable expected signals used for acceptance:

1. Instrument-level (parsed from the mzML itself): indexedmzML 1.1.0,
   centroid, positive polarity only, `spectrumList count="4988"` =
   782 MS1 + 4206 MS2, RT 0.708–11.002 min, HCD 40, isolation ±0.8 m/z,
   instrument `MS:1003112 Orbitrap ID-X`.
2. Reference spectrum: MassBank `MSBNK-BAFG-CSL23111012700`
   (Tetracycline, LC-ESI-QTOF, MS2 40 V, precursor 445.1605 [M+H]+,
   51 peaks, license `dl-de/by-2-0` — attribution REQUIRED with any
   stored copy of the reference peak list).
   https://massbank.jp/MassBank/RecordDisplay?id=MSBNK-BAFG-CSL23111012700
3. Formula arithmetic anchors measured in the dataset's own MS2
   (scan 2517, precursor 445.1610, RT 5.844 min): 445.1600 [M+H]+
   (−0.1 ppm), 428.1332 [M+H−NH3]+ (−1.6 ppm), 427.1494 [M+H−H2O]+
   (−0.2 ppm), 410.1229 [M+H−NH3−H2O]+, 154.0496 (ring-A fragment),
   98.0599.
4. The dataset's own `quant/annotated_FeatureMatrix_commercial_std.csv`
   lists `Tetracycline///tetracycline(1-)` at m/z 445.1606.

Scope note: this dataset is the acceptance anchor for the MZmine live
integration — one file, one ion mode, one recorded run. It is NOT a
scientific performance benchmark for the platform, and fixture/demo
outputs remain excluded from scientific performance claims (enterprise
brief).

### 11b. Vendor-native `.raw` file for M1-b (ADR-0087) — same dataset

Added 2026-10-31 for the vendor-conversion acceptance test (M1-b).

| Field | Value |
|---|---|
| File | `20220426_UMETAB266_POS_Thiostreptone.raw` (Thermo vendor-native, same UMETAB266_POS batch as the tetracycline mzML) |
| Source | Zenodo record 6948449 (mirror of MassIVE MSV000090047), downloaded 2026-10-31 |
| Size | 68,362,907 bytes (matches the Zenodo `files[].size` exactly) |
| MD5 | `ac0890f515c05f2ad3d3b6e599a3b11c` (authoritative: Zenodo record 6948449 `files[].checksum`; recomputed locally after download — match) |
| SHA-256 (local) | `0f039d2578c2b244f57cc212362e438e20fab242c483f3f5defc650596ebe6d8` |
| Committed to git? | **No** — `data/raw/` is gitignored (bulk data rule) |
| Conversion | ThermoRawFileParser 2.0.0.0 (bundled under the pinned MZmine 4.11.0 `external_tools/`; ADR-0087) → indexed mzML, 5,023 scans, 34,638,312 bytes |
| Converted mzML SHA-256 | `f1e7409e717790a7456f31216b343ce35239930c3fd869ebca9df0704449ae15` |
| Integration mode | Local acceptance anchor for the vendor-conversion + feature-detection chain |

Independently checkable expected signals used for acceptance:

1. Thiostrepton (PubChem CID 16129666): formula C72H85N19O18S5,
   monoisotopic neutral mass 1663.4923528 Da → [M+2H]2+ theoretical
   m/z 832.7530 (monoisotopic; the 5-sulfur isotope envelope makes the
   M+1/M+2 satellites prominent).
2. Measured in the recorded run (`data/validated_runs/M1b/`): feature
   1423 at m/z 832.75345 (+0.54 ppm), RT 8.4204 min, area 6.9e5
   (base peak by area); isotope-satellite group 832.75/833.75 at the
   same RT (833.754 = M+2 per-charge, as expected for 5 S atoms).
3. The run's mzML contains NO MS2 scans (full-scan-only acquisition) —
   so, as for M1-a, this is a precursor-mass + RT match pending MS/MS
   confirmation, NOT an identification.

Scope note: same as section 11 — acceptance anchor for the conversion
and detection integration, not a scientific performance benchmark.
