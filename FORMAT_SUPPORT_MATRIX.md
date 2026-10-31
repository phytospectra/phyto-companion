# Format Support Matrix — M0 (honest coverage and contract design)

Date: 2026-10-31 · Milestone M0 of `../MULTIVENDOR_LCMS_NMR_DISCOVERY_PROMPT.md`.
Rule (prompt §4): extensions alone are insufficient; a format is "supported"
only with a validated fixture at a named software version and acquisition mode.
Status vocabulary: **supported** (validated fixture + tests), **planned**
(designed, not yet validated), **blocked** (external dependency/license named),
**unavailable** (no code), **n/a**.

## LC–MS/MS

| Vendor / input | Signature / layout | Acquisition modes to distinguish | Reader / converter route | Required SDK / terms | Validated fixture | Status | Limitations |
|---|---|---|---|---|---|---|---|
| Open — mzML (1.1) | indexedmzML XML, controlled vocabulary | MS1, DDA, DIA/SWATH, MSn, polarity switching — each needs separate validation | pyteomics or pymzml (pinned) behind the ingestion service | MIT/Apache (verify at pin time) | none in repo (only a 422-byte synthetic e2e stub) | planned — M1-a first reader | mobility metadata varies by converter; profile-vs-centroid must be recorded |
| Open — mzXML / MGF / MSP | legacy XML; peak-list text | MGF/MSP lack metadata for raw reprocessing | same reader stack | open | none | planned (lower priority) | record lost/absent metadata explicitly |
| Agilent MassHunter `.d` | directory; acquire/B.0x + data.subsys | LC–MS vs CE/other `.d` generations | msconvert worker or vendor export (CEF) | Windows worker likely; license review required | none | blocked (worker + license) | whole-directory preservation required; version generations differ |
| Waters MassLynx `.raw` | directory | full-scan / DDA / MSE / HDMSE / SONAR / mobility | msconvert or authorized vendor export | license review | none | blocked | MSE-family deconvolution is a separate validation, not implied by reading |
| Thermo `.raw` | single proprietary file | MS1/DDA/DIA/MSn/PRM/SRM | msconvert or official SDK route where permitted | SDK terms review | none | blocked | scan relationships + method metadata must survive conversion |
| SCIEX `.wiff`/`.wiff2` | file + companion set | experiments, SWATH/DIA, MRM | msconvert (current-version compat) | license review | none | blocked | sample indexing and companion files mandatory |
| Shimadzu `.lcd` | proprietary | — | msconvert documents LCD with exceptions | license review | none | blocked | do not generalize to LabSolutions formats |
| Bruker MS `.d` / TDF/TSF | directory | TIMS/PASEF relationships | vendor readers | license review | none | blocked | distinct from Bruker NMR (below) |
| Vendor processed outputs — MZmine 3 feature table | CSV, "row m/z"/"row retention time" | post-processing only | `services/lcms_preproc/app/adapter.py` | none | none in repo (dialect covered by tests, synthetic) | supported (schema-level) | table import only — never raw processing; real exported fixture still to be pinned |
| Vendor processed outputs — XCMS peaklist | CSV, `mz`/`rt` (seconds) | post-processing | same adapter (converts to minutes, recorded) | none | synthetic tests | supported (schema-level) | same |
| Vendor processed outputs — MS-DIAL alignment | CSV with metadata prelines | post-processing | same adapter (scan-past header) | none | synthetic tests | supported (schema-level) | same |
| GNPS/FBMN outputs | MGF + quantification table + GraphML | networking results | `services/networking/gnps_result_parser.py` | open | `tests/fixtures/gnps/*` (synthetic) | supported (schema-level, mock) | mock-only provenance until a real job is recorded |

## NMR

| Vendor / input | Signature / layout | Reader route | Validated fixture | Status | Limitations |
|---|---|---|---|---|---|
| Bruker `fid`/`ser` + `pdata` + acqus | DTYPA/BYTORDA dtype, XDIM block storage, DIGMOD digital filter | `services/ingestion` `nmr_parser.py` + `services/nmr_preproc` | `tests/fixtures/nmr_case_study/` real archives (M6-4 series) | supported (scientific validation via §13 regression, CI-gated) | NUS reconstruction not supported (explicit); per-experiment decay guard (ADR-0075) |
| Agilent/Varian `.fid` + `procpar` | directory format | not implemented | none | unavailable | distinct from Agilent MS `.d` |
| JEOL `.jdf` | proprietary container | not implemented | none | unavailable | needs validated reader/conversion path |
| JCAMP-DX | text exchange | not implemented | none | planned (low) | — |
| nmrML / NMReDATA | XML exchange / assigned-data standard | not implemented | none | planned (M3) | assigned-data via peak/correlation tables preferred |
| Mnova / TopSpin project files | proprietary projects | export-only fallback | none | unavailable | PDFs/images are supplementary evidence, never raw spectra |
| Experiment coverage | 1H, 13C, DEPT/APT, COSY, TOCSY, HSQC, HMBC, NOESY/ROESY, DOSY | `twod.py`, regression suite | §13 archives (1H/13C/COSY/HSQC/HMBC) | partially supported (validated: 1H, 13C, COSY, HSQC, HMBC; others unavailable, named) | unvalidated experiments fail loudly rather than silently |

## Ingestion acceptance rules (binding for any "supported" promotion)

1. At least one licensed representative real fixture per advertised
   format/version/mode; negative cases for incomplete directories, corruption,
   unsupported modes, mislabeled extensions.
2. Numerical comparison against an independent reference — never only another
   wrapper of the same reader.
3. Conversion-loss report for every lossy route; unsupported acquisition
   dimensions are named, never dropped silently.
4. Unpublished/private data never leaves the workspace for conversion or
   search without explicit authorization.

## Redistributable reference datasets (M0-b shortlist — rights to verify before download)

| Candidate dataset family | Source | Why | Rights check required |
|---|---|---|---|
| Public study mzML subset with known features | MassIVE / MetaboLights / Metabolomics Workbench | M1 vertical + engine benchmark | per-study license; record accession + hash; verify redistribution terms |
| NIST-adjacent open SRM/standard mixture data (e.g. GNPS public jobs' inputs) | GNPS2 public datasets | library-search acceptance | CC-variant per dataset; verify |
| NMR reference assignments | BMRB small-molecule entries; nmrshiftdb2 | NMR pipeline checks | dataset-specific terms |

Owner-provided private test datasets are registered by name only; contents
never enter the repository or any prompt/fixture (brief rule).
