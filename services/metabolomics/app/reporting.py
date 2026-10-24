"""Analytical reporting checklist (ADR-0072).

Audits exactly the fields the 2025 NMR-reproducibility discussion and
the MSI precedent require, from the artefacts earlier layers actually
produced (``PreprocessingRecord``, model evidence, STOCSY/VIP
parameters).  Every required field is either populated or listed in
``missing_fields``; completeness = provided/required is part of the
evidence payload so a 60%-complete run is distinguishable from a
100%-complete one.  A missing field is NEVER defaulted to a guess.
"""

from __future__ import annotations

from phyto_shared.metabolomics import (
    PCAModelEvidence,
    PLSModelEvidence,
    PreprocessingRecord,
    ReportingChecklist,
)

__all__ = [
    "NMR_REQUIRED_FIELDS",
    "LCMS_REQUIRED_FIELDS",
    "STATISTICS_REQUIRED_FIELDS",
    "build_reporting_checklist",
]

NMR_REQUIRED_FIELDS: tuple[str, ...] = (
    "field_strength_mhz",
    "pulse_program",
    "apodisation_function",
    "line_broadening_hz",
    "zero_fill_factor",
    "phase_method",
    "referencing_standard",
    "baseline_method",
    "baseline_lambda",
    "alignment_method",
    "normalisation_method",
    "bucket_width_ppm",
    "excised_regions",
    "solvent",
)

LCMS_REQUIRED_FIELDS: tuple[str, ...] = (
    "tool_name",
    "tool_version",
    "rt_tolerance_s",
    "mz_tolerance_ppm",
    "normalisation_method",
)

STATISTICS_REQUIRED_FIELDS: tuple[str, ...] = (
    "pca_software",
    "pca_r2x",
    "model_software",
    "model_cv_scheme",
    "model_permutation_n",
    "model_permutation_p",
    "vip_threshold",
    "stocsy_driver",
    "stocsy_thresholds",
    "fdr_method",
)


def build_reporting_checklist(
    record: PreprocessingRecord | None,
    pca: PCAModelEvidence | None = None,
    model: PLSModelEvidence | None = None,
    vip_threshold: float | None = None,
    stocsy_driver_ppm: float | None = None,
    stocsy_thresholds: str | None = None,
) -> ReportingChecklist:
    """Assemble the completeness record; gaps are listed, never filled."""
    values: dict[str, object] = {}

    if record is not None:
        values.update(
            {
                "field_strength_mhz": record.spectrometer_frequency_mhz,
                "pulse_program": record.pulse_program,
                "apodisation_function": record.apodisation_function,
                "line_broadening_hz": record.line_broadening_hz,
                "zero_fill_factor": record.zero_fill_factor,
                "phase_method": record.phase_method,
                "referencing_standard": record.referencing_standard,
                "baseline_method": record.baseline_method,
                "baseline_lambda": record.baseline_lambda,
                "alignment_method": record.alignment_method,
                "normalisation_method": record.normalisation_method,
                "bucket_width_ppm": record.bucket_width_ppm,
                "excised_regions": record.excised_regions,
                "solvent": record.solvent,
                "tool_name": record.tool_name,
                "tool_version": record.tool_version,
                "rt_tolerance_s": record.rt_tolerance_s,
                "mz_tolerance_ppm": record.mz_tolerance_ppm,
            }
        )

    if pca is not None:
        values["pca_software"] = (
            f"in-house (numpy/sklearn PCA), R2X cumulative "
            f"{pca.cumulative_r2x:.4f}"
        )
        values["pca_r2x"] = pca.cumulative_r2x
    if model is not None:
        values["model_software"] = (
            "phyto-companion metabolomics service "
            f"({model.model_kind}; OPLS-DA deliberately absent, ADR-0015)"
        )
        values["model_cv_scheme"] = model.cv_scheme
        values["model_permutation_n"] = model.n_permutations
        values["model_permutation_p"] = model.permutation_p_value
    if vip_threshold is not None:
        values["vip_threshold"] = vip_threshold
    if stocsy_driver_ppm is not None:
        values["stocsy_driver"] = stocsy_driver_ppm
    if stocsy_thresholds is not None:
        values["stocsy_thresholds"] = stocsy_thresholds
    values["fdr_method"] = "Benjamini-Hochberg (scipy false_discovery_control)"

    required: list[str] = list(STATISTICS_REQUIRED_FIELDS)
    if record is not None:
        required += (
            list(NMR_REQUIRED_FIELDS)
            if record.modality == "nmr"
            else list(LCMS_REQUIRED_FIELDS)
        )

    provided = [
        field
        for field in required
        if values.get(field) is not None
        and values.get(field) != []
    ]
    missing = [field for field in required if field not in provided]
    completeness = len(provided) / len(required) if required else 1.0

    notes: list[str] = []
    if record is None:
        notes.append("no PreprocessingRecord: preprocessing layers were skipped")
    if missing:
        notes.append(
            f"{len(missing)} required reporting field(s) missing: "
            + ", ".join(missing)
        )

    return ReportingChecklist(
        required_fields=required,
        provided_fields=provided,
        missing_fields=missing,
        completeness=round(completeness, 4),
        notes=notes,
    )
