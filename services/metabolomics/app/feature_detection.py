"""Feature detection: MZmine 3 (cloud) or a deterministic mock (local).

The mock generates synthetic feature tables whose group structure is
known by construction, so every downstream statistical test has a
ground truth to assert against.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Literal, Protocol

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from phyto_shared.exceptions import CloudServiceError, InvalidInputError

__all__ = [
    "DetectParams",
    "SampleMetadata",
    "Feature",
    "FeatureTable",
    "FeatureDetector",
    "MZmineDetector",
    "MockDetector",
    "table_to_matrix",
    "matrix_to_table",
    "get_detector",
]


class DetectParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mz_tolerance_ppm: float = 10.0
    rt_tolerance_s: float = 12.0
    min_intensity: float = 1000.0
    noise_threshold: float = 500.0
    adducts: list[str] = Field(default_factory=lambda: ["[M+H]+", "[M-H]-", "[M+Na]+"])
    alignment_method: Literal["join", "obwarp"] = "join"


class SampleMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_id: str
    group: str | None = None
    is_qc: bool = False
    injection_order: int = 0


class Feature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feature_id: str
    mz: float = Field(..., gt=0.0)
    rt: float = Field(..., ge=0.0)
    intensities: dict[str, float] = Field(default_factory=dict)
    ms2_spectrum: list[tuple[float, float]] | None = None
    adduct: str | None = None
    isotope: str | None = None


class FeatureTable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    features: list[Feature] = Field(default_factory=list)
    samples: list[SampleMetadata] = Field(default_factory=list)
    qc_flags: list[str] = Field(default_factory=list)
    provenance_notes: list[str] = Field(
        default_factory=list,
        description="Every transformation applied to this table, in order",
    )


class FeatureDetector(Protocol):
    def detect(self, mzml_paths: list[Path], params: DetectParams) -> FeatureTable: ...


def table_to_matrix(
    table: FeatureTable,
) -> tuple[list[str], list[str], np.ndarray]:
    """(sample_ids, feature_ids, intensities[sample, feature])."""
    sample_ids = [sample.sample_id for sample in table.samples]
    feature_ids = [feature.feature_id for feature in table.features]
    matrix = np.zeros((len(sample_ids), len(feature_ids)))
    for j, feature in enumerate(table.features):
        for i, sample_id in enumerate(sample_ids):
            matrix[i, j] = feature.intensities.get(sample_id, 0.0)
    return sample_ids, feature_ids, matrix


def matrix_to_table(
    table: FeatureTable, sample_ids: list[str], feature_ids: list[str],
    matrix: np.ndarray,
) -> FeatureTable:
    """Rebuild a FeatureTable's intensities from a matrix."""
    feature_by_id = {feature.feature_id: feature for feature in table.features}
    features: list[Feature] = []
    for j, feature_id in enumerate(feature_ids):
        base = feature_by_id[feature_id]
        features.append(
            base.model_copy(
                update={
                    "intensities": {
                        sample_ids[i]: float(matrix[i, j])
                        for i in range(len(sample_ids))
                    }
                }
            )
        )
    return FeatureTable(
        features=features,
        samples=table.samples,
        qc_flags=list(table.qc_flags),
        provenance_notes=list(table.provenance_notes),
    )


class MZmineDetector:
    """MZmine batch mode via subprocess (real plane).

    .. warning:: **UNVERIFIED INTEGRATION (ADR-0084).** The batch file
       this detector once wrote was a one-column CSV, but MZmine's
       official batch files are XML documents with explicit module
       steps, invoked as ``-batch <batch.xml> -input "<mzML glob>"``.
       The CSV "batch" cannot have worked against a real MZmine 3 and
       no test has ever run one; the expected output schema is equally
       an assumption. Raw LC-MS processing is therefore a BLOCKED
       capability: it fails closed at startup (ADR-0083) and must not
       be claimed as working in any report until a live integration
       test has validated the XML batch format, the executable version
       and the actual CSV-export schema against a real installation.

    Requires the MZmine home directory (MZMINE_HOME); fails closed
    otherwise.
    """

    def __init__(self, mzmine_home: str | None = None) -> None:
        self.mzmine_home = mzmine_home or os.environ.get("MZMINE_HOME", "")
        self.executable = shutil.which("mzmine") or (
            str(Path(self.mzmine_home) / "bin" / "mzmine") if self.mzmine_home else None
        )

    def detect(self, mzml_paths: list[Path], params: DetectParams) -> FeatureTable:
        if not mzml_paths:
            raise InvalidInputError("no mzML files given", context={})
        missing = [str(p) for p in mzml_paths if not p.is_file()]
        if missing:
            raise InvalidInputError(
                "mzML input files do not exist",
                context={"missing": missing},
            )
        if not self.executable:
            raise CloudServiceError(
                "MZmine executable not found; the local plane must use "
                "MockDetector (split-plane rule)",
                context={"mzmine_home": self.mzmine_home},
            )
        raise CloudServiceError(
            "MZmine integration is unverified (ADR-0084): the batch "
            "contract (XML batch format, export schema, executable "
            "version) has never been validated against a real "
            "installation. Refusing to run rather than return "
            "mis-shaped results. See the capability register: raw "
            "LC-MS processing is a blocked capability.",
            context={"mzmine_home": self.mzmine_home,
                     "executable": str(self.executable)},
        )


class MockDetector:
    """Deterministic synthetic feature table with known group structure.

    Ground truth (asserted by tests): feature FT1 is an internal standard
    (flat everywhere); FT5 is 2x elevated in the "treated" group; FT6 is
    pure noise; FT7 carries a linear injection drift (QC correction
    target).  Sample roles come from file names: 'qc*' -> QC sample,
    'treated*' -> treated group, anything else -> control.
    """

    FEATURE_MZ = (100.0524, 115.0390, 130.0500, 163.0601, 271.0601, 301.0354, 341.1089)
    FEATURE_RT = (60.0, 120.0, 180.0, 240.0, 300.0, 360.0, 420.0)

    def detect(self, mzml_paths: list[Path], params: DetectParams) -> FeatureTable:
        if not mzml_paths:
            raise InvalidInputError("no mzML files given", context={})
        samples: list[SampleMetadata] = []
        for index, path in enumerate(mzml_paths, start=1):
            stem = path.stem.lower()
            samples.append(
                SampleMetadata(
                    sample_id=path.stem,
                    group=(
                        "qc" if stem.startswith("qc") or stem.startswith("pooled")
                        else "treated" if stem.startswith("treated")
                        else "control"
                    ),
                    is_qc=stem.startswith("qc") or stem.startswith("pooled"),
                    injection_order=index,
                )
            )

        features: list[Feature] = []
        for j, (mz, rt) in enumerate(zip(self.FEATURE_MZ, self.FEATURE_RT), start=1):
            intensities: dict[str, float] = {}
            for sample in samples:
                intensities[sample.sample_id] = round(
                    self._intensity(j, sample), 3
                )
            features.append(
                Feature(
                    feature_id=f"FT{j}",
                    mz=mz,
                    rt=rt,
                    intensities=intensities,
                    adduct="[M+H]+",
                )
            )
        kept = [f for f in features if max(f.intensities.values()) >= params.min_intensity]
        return FeatureTable(
            features=kept,
            samples=samples,
            qc_flags=[],
            provenance_notes=[
                f"MockDetector: {len(kept)} synthetic features, ground truth "
                "documented in class docstring"
            ],
        )

    @staticmethod
    def _intensity(feature_index: int, sample: SampleMetadata) -> float:
        base = 10000.0 * feature_index
        if feature_index == 1:  # internal standard: flat
            return 50000.0
        if feature_index == 5:  # elevated 2x in treated
            return base * (2.0 if sample.group == "treated" else 1.0)
        if feature_index == 6:  # noise feature
            # deterministic pseudo-noise from the injection order
            return base * (0.5 + ((sample.injection_order * 7) % 13) / 13.0)
        if feature_index == 7:  # linear drift with injection order
            return base * (1.0 + 0.02 * (sample.injection_order - 1))
        if sample.is_qc:
            return base * 1.05
        return base


def get_detector(**kwargs) -> FeatureDetector:
    """Mock on the dry-run plane; real mode requires an explicit detector.

    Fail-closed (ADR-0078 rule): a caller that knows it is in real mode
    (``dry_run=False``) gets ``MZmineDetector`` or nothing -- a missing
    MZmine installation raises, it never silently degrades to the mock.
    Callers that don't pass a config (legacy/tests) resolve the plane
    from the environment, which still returns the mock when MZMINE_HOME
    is unset -- that path must never be reachable from a validated cloud
    runtime (``main._Runtime`` validates first).
    """
    config = kwargs.pop("config", None)
    if config is None:
        from .runtime_config import MetabolomicsConfig

        config = MetabolomicsConfig.from_env()
    if config.dry_run:
        return MockDetector()
    if not config.mzmine_home:
        from phyto_shared.exceptions import CloudServiceError

        raise CloudServiceError(
            "real mode requires MZMINE_HOME (a licensed local MZmine "
            "installation); refusing to fall back to mock feature "
            "detection",
            context={"env": config.env, "capability": "feature_detection"},
        )
    return MZmineDetector(mzmine_home=config.mzmine_home, **kwargs)
