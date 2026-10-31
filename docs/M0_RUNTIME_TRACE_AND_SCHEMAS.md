# M0 Runtime Trace + Canonical Schema Proposals

Date: 2026-10-31 · Milestone M0 deliverable of
`../MULTIVENDOR_LCMS_NMR_DISCOVERY_PROMPT.md` ("actual runtime trace, proposed
canonical schemas"). The runtime trace is code-derived (this branch, backend
HEAD `c2558a8` + ADR-0082/0083/0084 changes), verified by the test suite.

## 1. Actual runtime trace — what happens per service/plane today

Startup validation is now **eager** (`create_app()` runs at container-import
time) in every service with a real/cloud dependency. Failure = named exception
with the missing variable(s); the process refuses to start. On the dry-run
plane every mock is deterministic, labeled, and excluded from scientific
claims.

| Service | Runtime object | Local plane (`PHYTO_ENV=local`) | Cloud real (`PHYTO_ENV=cloud`, no dry-run) | Validated at | Missing-config failure | `/health` reports |
|---|---|---|---|---|---|---|
| elucidation | shift predictor + structure DB | `MockShiftPredictor`; `PHYTO_STRUCTURE_DB_PATH` set → `FileStructureDB`, else `MockStructureDB` | `VertexAIShiftPredictor` (needs `VERTEX_ENDPOINT_ID`, `GOOGLE_CLOUD_PROJECT`); DB = `FileStructureDB` if path set, else **503 on access** | `create_app` (ADR-0078) | `ElucidationConfigError` naming the variables | `predictor_mode`, `structure_db_available`, `db_size`, `dry_run` |
| metabolomics | feature detector | `MockDetector` (synthetic ground truth) | `MZmineDetector` (needs `MZMINE_HOME`; **batch contract unverified, ADR-0084**) | `create_app` (ADR-0083) | `MetabolomicsConfigError` naming `MZMINE_HOME` | `detector`, `detector_mode`, `dry_run` |
| networking | GNPS client + job manager | `MockGNPSClient` (fabricated handles, labeled) | `GNPSClient` (needs `GNPS_API_KEY` or explicit `PHYTO_ALLOW_PUBLIC_GNPS=1`) | `create_app` (ADR-0083) | `NetworkingConfigError` naming the route | `gnps_client`, `gnps_mode`, `dry_run` |
| pharmacology | knowledge graph | `NetworkxKnowledgeGraph` (mock traditional-use records, labeled) | **explicitly unavailable** — real mode raises even with `NEO4J_URI` until the Step-8 Neo4j backend is wired | `create_app` (ADR-0083) | `PharmacologyConfigError` naming `NEO4J_URI` | `knowledge_graph`, `kg_mode`, `dry_run` |
| ingestion | NMR archive readers | real (no external deps) | same | n/a (deterministic) | — | standard |
| nmr_preproc | processing/deconvolution/qNMR | real | same | n/a | — | standard |
| lcms_preproc | feature-table adapter | real (tables only) | same | n/a | — | standard |
| calibration | MSI-level calibration | real | same | n/a | — | standard |
| llm | tiered LLM clients | dry-run ladder (mock) | real clients, fail-closed on missing key (pre-existing) | first use (fail-closed) | `CloudServiceError` | standard |
| orchestrator | planner/critic/sandbox | dry-run ladder | real via llm service | first use | provider error | standard |
| auth | Firebase middleware | disabled | real (creds required) | `create_app` (pre-existing `AuthConfig`) | auth config error | `auth_enabled` |

Trace evidence: `tests/test_elucidation/test_runtime_modes.py`,
`tests/test_metabolomics/test_detector_runtime_modes.py`,
`tests/test_networking/test_gnps_runtime_modes.py`,
`tests/test_pharmacology/test_kg_runtime_modes.py` (new), plus the existing
auth/local-plane suites. Full suite on this branch: **1591 passed, ruff clean**.

## 2. Canonical schema proposals (M0 contract design)

These are the proposed interchange types for the M1 vertical. They extend
existing shared models rather than replacing them.

### 2.1 `RawAcquisition` (per injected file; ingestion → processing)

```python
class RawAcquisition(BaseModel):
    file_id: str                     # workspace-unique, immutable original
    source_format: str               # "mzml", "bruker-d", "thermo-raw", ...
    source_format_version: str | None
    converter: ConverterProvenance | None  # who produced this file, how
    ms_level_summary: dict[int, int] # MS level -> scan count
    polarity: list[Literal["positive", "negative", "mixed"]]
    profile_or_centroid: Literal["profile", "centroid", "mixed"]
    rt_range_s: tuple[float, float]
    mz_range: tuple[float, float]
    instrument_metadata: dict[str, Any]    # method, collision energies, etc.
    unsupported_dimensions: list[str]      # named, never dropped silently
    checksum: str
```

### 2.2 `ProcessingProvenance` (engine run record; travels with every result)

```python
class ProcessingProvenance(BaseModel):
    engine: str                      # "mzmine", "xcms", "msdial", "in-house"
    engine_version: str
    batch_config_hash: str           # hash of the exact serialized batch
    batch_config: dict[str, Any]     # inspectable, reproducible
    input_file_ids: list[str]
    started_at: datetime
    duration_s: float
    warnings: list[str]              # e.g. "MSn scans present but not processed"
```

### 2.3 `CanonicalFeatureTable` (engine output → analysis; generalizes the current `FeatureTable`)

```python
class FeatureOrigin(BaseModel):
    detection: Literal["direct", "gap_filled", "background_subtracted"]
    alignment_group: str | None      # cross-sample correspondence id
    isotope_group: str | None
    adduct_hypotheses: list[AdductHypothesis]   # competing, ranked, never one
    ms2_spectra: list[Ms2Assignment] | None     # with chimerism assessment

class CanonicalFeatureTable(BaseModel):
    schema_version: str
    acquisition_ids: list[str]
    features: list[CanonicalFeature]       # feature_id, mz, rt, per-sample
    samples: list[SampleMetadata]          # + study-design roles
    processing: list[ProcessingProvenance] # ordered chain, engine runs + QC
    missing_reason: dict[tuple[str, str], Literal["not_detected", "below_limit", "gap_filled_zero"]]
```

Key rule from the prompt: gap-filled integrations are distinguishable from
directly detected peaks, and missing/zero/below-limit are distinct states.

### 2.4 `CandidateEvidence` (evidence-separated ranking; extends `CandidateRank`)

```python
class CandidateEvidence(BaseModel):
    tier: Literal[
        "mass_formula", "isotope", "experimental_msms", "predicted_fragmentation",
        "nmr", "rt_ccs", "taxonomy", "literature",
    ]
    provider_id: str                 # connector id from the registry
    provider_record: str | None      # accession/record id
    score: float
    details: dict[str, Any]          # matched peaks, errors, contradictions
    provenance: Literal["measured", "retrieved", "deterministic", "predicted", "llm_hypothesis"]
```

Every ranking result reports which providers were queried and which were
excluded, with the distinct failure statuses from the connector registry.

### 2.5 Ingestion acceptance record (per format/version/mode)

```python
class FormatAcceptance(BaseModel):
    format_id: str                   # row id in FORMAT_SUPPORT_MATRIX.md
    vendor: str | None
    software_version: str | None
    acquisition_mode: str | None
    fixture_id: str                  # licensed representative fixture
    checks: list[CheckResult]        # scan counts, ranges, units, polarity, ...
    reference_comparison: str        # independent-reference method + result
    conversion_loss: list[str]       # what did not survive
    status: Literal["supported", "planned", "blocked", "unavailable"]
```

## 3. Ownership and next gate

Schemas are proposals for the M1 vertical; they are frozen (with test data and
tolerances) **before** engine tuning, per the prompt's milestone rule. Next
acceptance gate: M1-a — one live MZmine run (XML batch, recorded version,
verified export schema) on a rights-verified open mzML fixture, with the
canonical table compared against the engine's own export.
