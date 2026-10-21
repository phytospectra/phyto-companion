"""Feature detection: MZmine 3 (cloud) or a deterministic mock (local).

The mock generates synthetic feature tables whose group structure is
known by construction, so every downstream statistical test has a
ground truth to assert against.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Literal, Protocol, Sequence

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
    """MZmine 3 batch mode via subprocess (cloud plane).

    Requires the MZmine home directory (MZMINE_HOME) and a licensed
    local installation; fails closed otherwise.
    """

    def __init__(self, mzmine_home: str | None = None) -> None:
        self.mzmine_home = mzmine_home or os.environ.get("MZMINE_HOME", "")
        self.executable = shutil.which("mzmine") or (
            str(Path(self.mzmine_home) / "bin" / "mzmine") if self.mzmine_home else None
        )

    def detect(self, mzml_paths: list[Path], params: DetectParams) -> FeatureTable:
        if not self.executable:
            raise CloudServiceError(
                "MZmine executable not found; the local plane must use "
                "MockDetector (split-plane rule)",
                context={"mzmine_home": self.mzmine_home},
            )
        batch = self._build_batch(mzml_paths, params)
        result = subprocess.run(  # noqa: S603 - fixed executable
            [self.executable, "-batch", str(batch)],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if result.returncode != 0:
            raise CloudServiceError(
                "MZmine batch failed",
                context={"stderr": result.stderr[-500:]},
            )
        csv_path = batch.with_suffix(".feature_table.csv")
        if not csv_path.exists():
            raise CloudServiceError(
                "MZmine did not produce a feature table", context={}
            )
        return self._parse_mzmine_csv(csv_path, mzml_paths)

    def _build_batch(self, mzml_paths: list[Path], params: DetectParams) -> Path:
        import csv as _csv
        import tempfile

        rows = [{"mzml": str(path)} for path in mzml_paths]
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".csv", delete=False
        )
        writer = _csv.DictWriter(handle, fieldnames=["mzml"])
        writer.writeheader()
        writer.writerows(rows)
        handle.close()
        return Path(handle.name)

    def _parse_mzmine_csv(self, csv_path: Path, mzml_paths: list[Path]) -> FeatureTable:
        import csv as _csv

        with csv_path.open() as handle:
            rows = list(_csv.DictReader(handle))
        samples = [
            SampleMetadata(sample_id=path.stem, group=None) for path in mzml_paths
        ]
        features: list[Feature] = []
        for row in rows:
            intensities = {
                key: float(value)
                for key, value in row.items()
                if key not in {"feature_id", "mz", "rt"} and value
            }
            features.append(
                Feature(
                    feature_id=row.get("feature_id", f"FT{len(features) + 1}"),
                    mz=float(row["mz"]),
                    rt=float(row["rt"]),
                    intensities=intensities,
                )
            )
        return FeatureTable(
            features=features,
            samples=samples,
            provenance_notes=[f"mzmine batch from {csv_path.name}"],
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
    """Mock when PHYTO_DRY_RUN=1 or no MZmine installation is configured."""
    if os.environ.get("PHYTO_DRY_RUN") == "1" or not os.environ.get("MZMINE_HOME"):
        return MockDetector()
    return MZmineDetector(**kwargs)
