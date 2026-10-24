"""Metabolomics evidence-layer domain models and relation names
(ADR-0068..ADR-0072).

Design notes
------------
* Like ``chromatography`` and ``taxonomy_models``, this module must NOT
  import ``models.StrictModel``: ``schemas.SampleMetadata`` references
  ``MetabolomicsInput`` from here, so the dependency arrow must stay
  ``schemas/models -> metabolomics`` and never the other way.  The base
  config below is identical to ``StrictModel``.
* ``EvidenceItem.relation`` is a free-form string (models.py contract),
  so the "named relation" discipline for the metabolomics layers is
  enforced by THIS module's tuple plus tests, not by the type system.
  Every ``*_skipped`` / ``*_failed`` / ``*_empty`` relation carries its
  human-readable reason in ``EvidenceItem.spectral_feature`` -- the
  established house pattern (cf. layer_chain.py purity evidence),
  because ``EvidenceItem`` has no payload field and forbids extras.
* Absent metadata is ``None``, never fabricated: a ``PreprocessingRecord``
  built without batch metadata reports ``batch_association=None``, not
  "no association found".
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "MetabolomicsModel",
    "METABOLOMICS_RELATIONS",
    "SKIPPED_METABOLOMICS_RELATIONS",
    "MetabolomicsRelation",
    "PreprocessingRecord",
    "PCAModelEvidence",
    "PLSModelEvidence",
    "VIPFeature",
    "STOCSYCorrelation",
    "ReportingChecklist",
    "MetabolomicsInput",
    "PreprocessingOutcome",
]

METABOLOMICS_RELATIONS: tuple[str, ...] = (
    # Preprocessing provenance (ADR-0068, ADR-0069)
    "nmr_preprocessing_applied",
    "nmr_preprocessing_skipped",
    "lcms_preprocessing_applied",
    "lcms_preprocessing_skipped",
    # Statistical model evidence (ADR-0070)
    "pca_model",
    "pls_da_model",
    "opls_regression_model",
    "model_validation_failed",
    "statistical_model_skipped",
    # Feature ranking (ADR-0070)
    "vip_feature",
    "vip_selection_empty",
    # Correlation (ADR-0071)
    "stocsy_correlation",
    "stocsy_skipped",
    # Reporting (ADR-0072)
    "reporting_checklist",
)
"""The named evidence relations emitted by the metabolomics layer chain.

The skip/empty relations are the visibility contract: a layer that
cannot run emits its ``*_skipped`` relation with the reason string in
``spectral_feature`` -- degradation is named, never silent.
"""

SKIPPED_METABOLOMICS_RELATIONS: tuple[str, ...] = tuple(
    name
    for name in METABOLOMICS_RELATIONS
    if name.endswith(("_skipped", "_failed", "_empty"))
)

MetabolomicsRelation = Literal[
    "nmr_preprocessing_applied",
    "nmr_preprocessing_skipped",
    "lcms_preprocessing_applied",
    "lcms_preprocessing_skipped",
    "pca_model",
    "pls_da_model",
    "opls_regression_model",
    "model_validation_failed",
    "statistical_model_skipped",
    "vip_feature",
    "vip_selection_empty",
    "stocsy_correlation",
    "stocsy_skipped",
    "reporting_checklist",
]


class MetabolomicsModel(BaseModel):
    """Same contract as ``models.StrictModel`` (see module docstring)."""

    model_config = ConfigDict(frozen=False, extra="forbid")


class PreprocessingRecord(MetabolomicsModel):
    """Every preprocessing parameter, recorded rather than assumed
    (ADR-0068 NMR, ADR-0069 LC-MS).

    One record per chain run.  ``modality`` selects which field family is
    meaningful; the other family's fields stay ``None`` (absent, not
    zero, not guessed).  These are the 2025 NMR-reproducibility minimum
    fields plus the LC-MS adapter metadata; the reporting checklist
    (ADR-0072) audits exactly these fields.
    """

    modality: Literal["nmr", "lcms"]

    # --- NMR (ADR-0068) -----------------------------------------------------
    apodisation_function: str | None = None
    line_broadening_hz: float | None = None
    zero_fill_factor: int | None = None
    phase_method: str | None = None
    phase0_deg: float | None = None
    phase1_deg: float | None = None
    referencing_standard: str | None = None
    referencing_shift_ppm: float | None = None
    baseline_method: str | None = None
    baseline_lambda: float | None = None
    baseline_p: float | None = None
    alignment_method: str | None = None
    bucket_width_ppm: float | None = None
    excised_regions: list[tuple[float, float]] | None = None
    solvent: str | None = None
    spectrometer_frequency_mhz: float | None = None
    pulse_program: str | None = None

    # --- LC-MS (ADR-0069) ---------------------------------------------------
    tool_name: str | None = None
    tool_version: str | None = None

    # --- shared --------------------------------------------------------------
    normalisation_method: str | None = None
    normalisation_reference_region: str | None = None
    rt_tolerance_s: float | None = None
    mz_tolerance_ppm: float | None = None
    sample_ids: list[str] = Field(default_factory=list)


class PCAModelEvidence(MetabolomicsModel):
    """PCA outcome as evidence: R2X, outliers, optional batch check."""

    n_components: int
    r2x_per_component: list[float]
    cumulative_r2x: float
    outlier_sample_ids: list[str] = Field(default_factory=list)
    batch_association: str | None = Field(
        default=None,
        description="Human-readable batch-association test result; None "
        "when no batch metadata was supplied (never fabricated)",
    )
    n_samples: int


class PLSModelEvidence(MetabolomicsModel):
    """PLS-DA or O-PLS regression outcome as evidence (ADR-0070).

    ``model_kind`` records which supervised model produced the numbers;
    O-PLS regression serves continuous Y, PLS-DA categorical Y.  The
    discriminant OPLS-DA remains deliberately absent (ADR-0015).
    """

    model_kind: Literal["pls_da", "opls_regression"]
    n_components: int
    n_orthogonal_components: int | None = None
    r2y: float
    q2: float
    permutation_p_value: float | None = Field(
        default=None,
        description="None (with the reason recorded as evidence) when too "
        "few samples make a permutation test meaningless",
    )
    n_permutations: int | None = None
    class_balance: str | None = None
    continuous_y_summary: str | None = None
    cv_scheme: str = Field(
        description="e.g. 'balanced Monte Carlo, k=15, 2/3 train' or "
        "'3-fold CV'"
    )


class VIPFeature(MetabolomicsModel):
    """One feature's VIP score with the threshold verdict (ADR-0070)."""

    feature_id: str
    vip_score: float
    threshold: float
    passes_threshold: bool
    p_value: float | None = Field(
        default=None, description="Paired univariate p-value when available"
    )
    fdr_p_value: float | None = Field(
        default=None, description="Paired BH-adjusted p-value when available"
    )
    pairing_note: str = Field(
        default="no univariate test supplied; VIP reported unpaired",
        description="Stated whenever FDR/p pairing was not possible",
    )


class STOCSYCorrelation(MetabolomicsModel):
    """One driver/bucket correlation as evidence (ADR-0071).

    Correlations are HYPOTHESES requiring chemical confirmation, never
    assignments; the layer's evidence text says so.
    """

    mode: Literal["driver_1d", "all_pairs_2d"]
    driver_ppm: float
    bucket_ppm: float
    pearson_r: float
    p_value: float
    fdr: float
    passes_threshold: bool


class ReportingChecklist(MetabolomicsModel):
    """The analytical reporting completeness record (ADR-0072).

    ``missing_fields`` lists every required field that was absent; the
    chain turns a non-empty list into a visible caveat, never a silent
    default.  ``completeness`` is provided_fields/required_fields and is
    itself evidence: 60% and 100% complete runs must be distinguishable.
    """

    required_fields: list[str]
    provided_fields: list[str]
    missing_fields: list[str]
    completeness: float = Field(..., ge=0.0, le=1.0)
    notes: list[str] = Field(default_factory=list)


class MetabolomicsInput(MetabolomicsModel):
    """User-supplied metabolomics dataset reference in SampleMetadata.

    A layer chain runs only when this was supplied; without it the
    metabolomics layers are SKIPPED with a visible reason, exactly like
    the chromatography metadata gates (ADR-0061..ADR-0066).
    """

    kind: Literal["nmr_bruker_dirs", "feature_table_csv"]
    paths: list[str] = Field(..., min_length=1)
    sample_sheet_path: str | None = Field(
        default=None,
        description="CSV with per-sample metadata; supplies the continuous "
        "covariate or class labels for the supervised layers",
    )
    continuous_covariate: str | None = Field(
        default=None,
        description="Sample-sheet column used as continuous Y (e.g. "
        "'storage_day'); selects O-PLS regression over PLS-DA",
    )
    group_column: str | None = Field(
        default=None,
        description="Sample-sheet column with class labels for PLS-DA",
    )
    driver_peak_ppm: float | None = Field(
        default=None,
        description="STOCSY driver peak (e.g. 7.55); without it the 1D "
        "driver analysis is skipped and only 2D all-pairs runs",
    )


class PreprocessingOutcome(MetabolomicsModel):
    """What one preprocessing layer did: applied, or skipped with a reason.

    The reason is a human-readable string, never an empty default -- the
    same skip-visibility contract as the chromatography layers.
    """

    applied: bool
    reason: str | None = Field(
        default=None,
        description="Required when applied=False: why the layer was skipped",
    )
    record: PreprocessingRecord | None = None
