# Measured HSQC reference libraries — schema, rights, and acquisition

Status: 2026-11-09. This document defines the reference-library format consumed
by `RealToolExecutor` (`PHYTO_STRUCTURE_DB_PATH` + `PHYTO_HSQC_REFERENCE_LIBRARY`),
the reproducible import/validation pipeline, and the rights status of every
data source considered. It is the control point for what may enter a
distributable library.

## Library format and builder

A library directory contains:

```
<source>/manifest.csv          # curation manifest, one row per record
<source>/<compound>_hsqc.csv   # position,dH,dC table per compound
structures.csv                 # builder output (executor structure library)
references.json                # builder output (executor reference library)
MANIFEST.json                  # per-record status, hashes, evidence nature
```

`manifest.csv` columns: `name, smiles, solvent, source, license, table,
expected_inchikey, declared_formula, evidence_nature`.

Build (deterministic — identical inputs give byte-identical outputs):

```sh
.venv/bin/python scripts/build_reference_library.py \
  --source data/reference_libraries/<name>/source \
  --output data/reference_libraries/<name> [--strict]
```

Validation performed per record (failures are **quarantined with the reason
in `MANIFEST.json`**, never silently repaired or dropped):

- SMILES parses with RDKit; `expected_inchikey` (when supplied) must match the
  RDKit-derived full InChIKey; duplicate InChIKeys across records are rejected;
- peak-table parse: columns `position,dH,dC`; every shift finite; H within
  −5..15 ppm and C within −50..300 ppm; 1..256 peaks; duplicate position
  labels and duplicate identical cross-peak coordinates are rejected
  (ambiguous assignment — refusing to guess);
- `declared_formula` (when the table header declares one) must equal the
  RDKit-derived molecular formula;
- `solvent`, `source`, `license` are required nonempty strings.

Reference IDs are content-addressed (`<candidate-id>-<sha256(table)[:12]>`).
`MANIFEST.json` records input/output SHA-256 hashes and `evidence_nature`
per record.

### Provenance semantics (important)

- Reference `provenance: "experimental"` is a **curator assertion** the
  software cannot verify; the honest evidence classification lives in
  `MANIFEST.json` `evidence_nature` (e.g. `literature_hand_curated_rounded`).
- Structure rows are `structure_only`: the structure file carries identity,
  not measurement claims.
- A quarantined record stays out of the library until a human resolves the
  stated reason. Example in the starter library: andrographolide's literature
  shift table is quarantined because no verified structure identity is
  available offline (see `docs/DECISIONS.md`).

## Committed starter library — `data/reference_libraries/literature_flavonoids_v1`

3 imported records (quercetin, kaempferol, luteolin — DMSO-d6), 1 quarantined
(andrographolide). Values are **hand-curated, rounded (2 dp) literature
assignments** from the in-repo NP-MRD/SUMMIT fixture tables
(`tests/fixtures/real_npmrd/`), not authoritative database records. License
per record: CC-BY-NC (NP-MRD fixture README) — noncommercial attributed
research use; **must not** ship in a commercial tier (ADR-0026).

Honest status: this is a **regression/self-match library**, not a validated
scientific reference database. It supports software regression tests and the
public quickstart; it does not support identification-accuracy claims. Tests:
`tests/test_reference_library.py`.

## Rights matrix and acquisition procedures

| Source | Terms (as verified) | Redistribution into this repo | Procedure |
|---|---|---|---|
| In-repo NP-MRD/SUMMIT fixture tables | CC-BY-NC, attribution, noncommercial (fixture README) | Already redistributed under those terms | n/a (used for starter library) |
| NP-MRD (np-mrd.org) | CC-BY-NC for noncommercial use; bulk downloads require an account; per-record terms at source | Only with per-record license fields filled; noncommercial tiers only | Register, download per-compound HSQC records, export `position,dH,dC` tables + manifest rows with the record URL/ID as `source` and the database license as `license`; run the builder; keep the original downloads outside the repo |
| NMRShiftDB2 | Open-content license (CC-BY-SA style; confirm the exact statement on the site before bulk import); full-data dumps on SourceForge (`nmrshiftdb2` project, NMReDATA SDF); web-service API | Permissible with attribution + share-alike carried per record | Download the NMReDATA SDF dump; derive HSQC cross-peaks **only** where atom-level assignments pair a ^1H and ^13C shift on the same carbon atom; record the derivation as a transformation in `MANIFEST.json`. **Not executed**: `api.nmrshiftdb.org` was unreachable from this environment on 2026-11-09 (connection failure); the exact license text must be re-verified at access time |
| BMRB (bmrb.io) | Freely available spectral data; confirm the current reuse statement (public-domain-style dedication) at source before import | Verify per-record terms first; expected permissible with attribution | BMRB's small-molecule HSQC coverage is limited (biomolecule focus); query assigned entries via their API, export timed-domain or peak lists, transform to tables |
| Owner data (`phyto_study_2026-11-02/`, `CaseStudy_Photospectra/`) | Owner-owned measurements; publication/redistribution is an **author decision not yet given** | **MUST NOT be committed to this public repository** before explicit permission | Build owner libraries by running the builder against a source directory **outside the repo**; the resulting library works with the same executor; record permission status in the private ledger |

Rules:

1. Public accessibility is not redistribution permission; the repository's
   Apache-2.0 software license says nothing about dataset rights.
2. Every imported record must carry a real `source` identifier and an explicit
   `license` string; placeholder values are quarantine-worthy.
3. CC-BY-NC data may back research/noncommercial use only and must be labeled
   per record; commercial tiers must exclude it (ADR-0026).
4. Synthetic fixtures may exercise the pipeline but are never measured
   references; label them `synthetic` in `evidence_nature` and keep them out
   of distributable libraries.

## Scientific-use gate

No library built here is scientifically validated. Before any accuracy claim
is made from a reference library: (a) records must be measured (not rounded
literature transcriptions) with acquisition conditions recorded, (b) the
evaluation protocol in `benchmark/` must have been executed with held-out
queries, and (c) the limitation must still be stated alongside any result.
