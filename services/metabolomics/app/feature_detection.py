"""Feature detection: MZmine 3 (cloud) or a deterministic mock (local).

The mock generates synthetic feature tables whose group structure is
known by construction, so every downstream statistical test has a
ground truth to assert against.
"""

from __future__ import annotations

import hashlib
import csv
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
    "parse_mzmine_csv",
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
    table: FeatureTable,
    sample_ids: list[str],
    feature_ids: list[str],
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
                        sample_ids[i]: float(matrix[i, j]) for i in range(len(sample_ids))
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


def parse_mzmine_csv(csv_path: Path, sample_names: list[str] | None = None) -> FeatureTable:
    """Parse MZmine's modular CSV export into a FeatureTable.

    Schema (validated live against MZmine 4.11.0's CSVExportModular,
    ADR-0085): header ``id, mz, mz_range:min, ..., rt, rt_range:min,
    rt_range:max, area, height, ..., charge, fragment_scans, ...`` then
    one column block per data file named
    ``datafile:<filename>:<quantity>``.  Row ``mz`` is the aligned
    average m/z and ``rt`` the aligned RT **in minutes**.  Per-sample
    intensities come from each file's ``height`` column.
    """
    with open(csv_path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    if not rows or not fieldnames:
        raise InvalidInputError("MZmine CSV export is empty", context={"path": str(csv_path)})
    required = {"id", "mz", "rt"}
    missing_columns = sorted(required - set(fieldnames))
    if missing_columns:
        raise InvalidInputError(
            "MZmine CSV export is missing required columns",
            context={"missing": missing_columns, "path": str(csv_path)},
        )

    # Identify the per-file height columns; fall back to the aligned
    # height column when the export contains a single (unaligned) file.
    per_file: dict[str, str] = {}
    for column in fieldnames:
        if column.startswith("datafile:") and column.endswith(":height"):
            per_file[column.removeprefix("datafile:").removesuffix(":height")] = column
    if not per_file and "height" in fieldnames:
        per_file = {"": "height"}

    samples = [
        SampleMetadata(sample_id=name or f"sample{i + 1}", injection_order=i + 1)
        for i, name in enumerate(sample_names or [])
    ]
    sample_keys = list(per_file)

    features: list[Feature] = []
    for row in rows:
        intensities: dict[str, float] = {}
        for sample, key in zip(samples, sample_keys):
            raw = (row.get(per_file[key]) or "").strip()
            intensities[sample.sample_id] = float(raw) if raw else 0.0
        if not samples:
            aligned = (row.get("height") or "").strip()
            if aligned:
                intensities["aligned"] = float(aligned)
        features.append(
            Feature(
                feature_id=str(row["id"]),
                mz=float(row["mz"]),
                # MZmine exports RT in minutes; the platform's canonical
                # unit is seconds.
                rt=float(row["rt"]) * 60.0,
                intensities=intensities,
            )
        )
    return FeatureTable(
        features=features,
        samples=samples,
        qc_flags=[],
        provenance_notes=[
            "parsed from MZmine 4.11 CSVExportModular export "
            "(RT minutes -> seconds; per-file height columns)"
        ],
    )


class MZmineDetector:
    """MZmine batch mode via subprocess (real plane).

    Contract (validated live, ADR-0085): a version-pinned XML batch
    (see ``batch_xml``) is executed with MZmine 4.11.0's CLI
    (``-user <mzuser4> -batch <xml>``) and the modular CSV export is
    parsed back into a FeatureTable.  The input file list and the CSV
    output path are written into the batch XML itself (import step +
    CSV export step), so the CLI carries no ``-input``/``-output``
    overrides: MZmine documents ``-output`` as renaming every export
    file with module suffixes, which would make the parser's expected
    ``features.csv`` path a guess.
    MZmine 4.11 requires an authenticated user file for batch mode;
    ``MZMINE_USER_FILE`` must point at an ``.mzuser4`` profile.

    The single recorded live run (MSV000090047 tetracycline mzML,
    positive mode) validated this contract once.  It is NOT a broad
    scientific validation; vendor-native conversion (.raw/.d) is a
    separate acceptance test and must not be claimed.
    """

    def __init__(
        self,
        mzmine_home: str | None = None,
        user_file: str | None = None,
        threads: int | None = None,
    ) -> None:
        self.mzmine_home = mzmine_home or os.environ.get("MZMINE_HOME", "")
        self.user_file = user_file or os.environ.get("MZMINE_USER_FILE", "")
        self.threads = threads
        self.executable = shutil.which("mzmine") or self._find_executable()

    def _find_executable(self) -> str | None:
        if not self.mzmine_home:
            return None
        home = Path(self.mzmine_home)
        # macOS .app bundle (portable academia build) and classic bin/ layout
        for candidate in (
            home / "mzmine.app" / "Contents" / "MacOS" / "mzmine",
            home / "bin" / "mzmine",
            home / "mzmine",
        ):
            if candidate.is_file():
                return str(candidate)
        return None

    def _require_login_gate_inputs(self) -> None:
        if not self.executable:
            raise CloudServiceError(
                "MZmine executable not found under MZMINE_HOME; the local "
                "plane must use MockDetector (split-plane rule)",
                context={"mzmine_home": self.mzmine_home},
            )
        # MEASURED (ADR-0085): every official MZmine 4.x build (4.11.0,
        # 4.10.90 and 4.10.6 confirmed live; the gate exists in source
        # since v4.0.x) aborts batch mode without an authenticated
        # user: "Requires user login." / "Invalid user: You need to be
        # logged in to continue."  MZMINE_USER_FILE must point at an
        # .mzuser4 profile created via 'mzmine -login' (browser sign-in
        # with an mzio account).
        if not self.user_file or not Path(self.user_file).is_file():
            raise CloudServiceError(
                "MZmine batch mode requires an authenticated user "
                "profile (measured on 4.11.0, 4.10.90 and 4.10.6); set "
                "MZMINE_USER_FILE to an .mzuser4 file created via "
                "'mzmine -login' or the mzio account console. Refusing "
                "to run without it.",
                context={"mzmine_user_file": self.user_file},
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
        self._require_login_gate_inputs()
        version = self._engine_version()
        return self._run_batch(mzml_paths, params, version)

    def _run_batch(
        self, mzml_paths: list[Path], params: DetectParams, version: str
    ) -> FeatureTable:
        import subprocess
        import tempfile

        from .batch_xml import build_batch_xml

        if version not in ("4.11.0", "4.10.90", "4.10.6"):
            raise CloudServiceError(
                "MZmine version mismatch: this integration is pinned to "
                "4.11.0 / 4.10.90 / 4.10.6 (all login-gated, see "
                f"ADR-0085) but the executable reports {version!r}. "
                "Regenerate the batch template against the installed "
                "engine before running raw LC-MS processing.",
                context={"expected": "4.11.0 | 4.10.90 | 4.10.6", "found": version},
            )

        with tempfile.TemporaryDirectory(prefix="phyto-mzmine-") as tmp:
            tmp_dir = Path(tmp)
            batch_path = tmp_dir / "batch.mzbatch"
            output_csv = tmp_dir / "features.csv"
            resolved = [str(p.resolve()) for p in mzml_paths]
            batch_xml_text = build_batch_xml(
                resolved,
                str(output_csv),
                params,
                mzmine_version=version,
            )
            batch_path.write_text(batch_xml_text, encoding="utf-8")
            # No -input/-output overrides: the batch XML already carries
            # the resolved input paths (import step) and the exact CSV
            # path (export step).  MZmine's -output rewrites every
            # export filename with module-specific suffixes, which would
            # make the parser's expected path a guess instead of a
            # contract.
            cmd: list[str] = [
                str(self.executable),
                "-user",
                self.user_file,
                "-batch",
                str(batch_path),
            ]
            if self.threads:
                cmd.extend(["--threads", str(self.threads)])
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=3600,
                )
            except subprocess.TimeoutExpired as exc:
                raise CloudServiceError(
                    "MZmine batch run timed out",
                    context={"timeout_s": exc.timeout},
                ) from exc
            log_path = tmp_dir / "mzmine_run.log"
            log_path.write_text(
                (proc.stdout or "") + "\n" + (proc.stderr or ""),
                encoding="utf-8",
            )
            if proc.returncode != 0 or not output_csv.is_file():
                raise CloudServiceError(
                    "MZmine batch run failed; see the engine log in the error context",
                    context={
                        "returncode": proc.returncode,
                        "mzmine_version": version,
                        "log_tail": "\n".join(
                            ((proc.stdout or "") + (proc.stderr or "")).splitlines()[-40:]
                        ),
                    },
                )
            table = parse_mzmine_csv(output_csv, sample_names=[p.stem for p in mzml_paths])
        table.provenance_notes.append(
            f"engine=MZmine {version}; batch sha256="
            f"{hashlib.sha256(batch_xml_text.encode()).hexdigest()[:16]}; "
            "pinned integration ADR-0085; vendor-native conversion is a "
            "separate acceptance test"
        )
        return table

    def _engine_version(self) -> str:
        import re
        import subprocess

        try:
            proc = subprocess.run(
                [str(self.executable), "-v"],
                capture_output=True,
                text=True,
                timeout=180,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise CloudServiceError(
                "could not determine the MZmine version",
                context={"error": str(exc)},
            ) from exc
        match = re.search(
            r"mzmine (\d+\.\d+\.\d+)",
            (proc.stdout or "") + (proc.stderr or ""),
        )
        if not match:
            raise CloudServiceError(
                "could not parse the MZmine version from its -v output",
                context={"stdout_tail": (proc.stdout or "")[-500:]},
            )
        return match.group(1)


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
                        "qc"
                        if stem.startswith("qc") or stem.startswith("pooled")
                        else "treated"
                        if stem.startswith("treated")
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
                intensities[sample.sample_id] = round(self._intensity(j, sample), 3)
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
    return MZmineDetector(
        mzmine_home=config.mzmine_home,
        user_file=getattr(config, "mzmine_user_file", None),
        **kwargs,
    )
