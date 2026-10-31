"""MZmine 4.11 batch XML generation and CSV-export parsing (ADR-0085).

The batch contract pinned here was extracted from the official MZmine
v4.11 repository and validated once against a real 4.11.0 installation
(MSV000090047 tetracycline mzML).  These tests keep the generator
honest: module class names and parameter versions must keep matching
the pinned engine, and the CSV schema must keep matching what the
engine actually exports.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from phyto_shared.exceptions import CloudServiceError, InvalidInputError
from metabolomics.batch_xml import BATCH_STEPS, MZMINE_VERSION, build_batch_xml
from metabolomics.feature_detection import (
    DetectParams,
    MZmineDetector,
    parse_mzmine_csv,
)


class TestBatchXml:
    def test_root_is_version_pinned(self, tmp_path):
        xml = build_batch_xml(
            [str(tmp_path / "a.mzML")], str(tmp_path / "out.csv"), DetectParams()
        )
        root = ET.fromstring(xml)
        assert root.tag == "batch"
        assert root.attrib["mzmine_version"] == MZMINE_VERSION == "4.11.0"

    def test_supports_all_verified_engines(self, tmp_path):
        for version in ("4.11.0", "4.10.90", "4.10.6"):
            xml = build_batch_xml(
                [str(tmp_path / "a.mzML")],
                str(tmp_path / "out.csv"),
                DetectParams(),
                mzmine_version=version,
            )
            root = ET.fromstring(xml)
            assert root.attrib["mzmine_version"] == version
            assert len(list(root)) == len(BATCH_STEPS)

    def test_steps_match_pinned_engine_contract(self, tmp_path):
        xml = build_batch_xml(
            [str(tmp_path / "a.mzML")], str(tmp_path / "out.csv"), DetectParams()
        )
        root = ET.fromstring(xml)
        steps = list(root)
        assert [(s.attrib["method"], s.attrib["parameter_version"])
                for s in steps] == [
            (method, str(version)) for method, _, version in BATCH_STEPS
        ]
        # Every module class name resolves through the strict
        # BatchQueue mapping only if the names are fully qualified.
        assert all(step.attrib["method"].startswith("io.github.mzmine.modules.")
                   for step in steps)

    def test_import_step_lists_files_and_export_writes_csv(self, tmp_path):
        inputs = [str(tmp_path / "a.mzML"), str(tmp_path / "b.mzML")]
        out = str(tmp_path / "features.csv")
        xml = build_batch_xml(inputs, out, DetectParams())
        root = ET.fromstring(xml)
        import_step, export_step = root[0], root[-1]
        files = [
            f.text for f in import_step.iter("file")
        ]
        assert files == inputs
        current = [f.text for f in export_step.iter("current_file")]
        assert current == [out]
        # The export must come last so BATCH_LAST_FEATURELISTS resolves
        # to the aligned/deduplicated feature list.
        assert export_step.attrib["method"].endswith("CSVExportModularModule")

    def test_params_drive_tolerances(self, tmp_path):
        params = DetectParams(mz_tolerance_ppm=7.5, rt_tolerance_s=6.0,
                              min_intensity=2500.0, noise_threshold=900.0)
        xml = build_batch_xml(
            [str(tmp_path / "a.mzML")], str(tmp_path / "out.csv"), params
        )
        assert "<ppmtolerance>7.5</ppmtolerance>" in xml
        assert "<parameter name=\"Minimum absolute height\">2500.0</parameter>" in xml
        assert "<parameter name=\"Minimum intensity for consecutive scans\">900.0</parameter>" in xml
        # rt_tolerance_s seconds -> minutes for MZmine
        assert 'unit="MINUTES">0.100000' in xml

    def test_refuses_unverified_engine_versions(self, tmp_path):
        with pytest.raises(ValueError, match="contract covers"):
            build_batch_xml(
                [str(tmp_path / "a.mzML")],
                str(tmp_path / "out.csv"),
                DetectParams(),
                mzmine_version="4.9.14",
            )

    def test_refuses_empty_inputs(self, tmp_path):
        with pytest.raises(ValueError, match="mzML paths"):
            build_batch_xml([], str(tmp_path / "out.csv"), DetectParams())


class TestParseMzmineCsv:
    def _write(self, tmp_path: Path) -> Path:
        path = tmp_path / "features.csv"
        path.write_text(
            "id,mz,mz_range:min,mz_range:max,rt,rt_range:min,rt_range:max,"
            "area,height,charge,fragment_scans,"
            "datafile:s1.mzML:height,datafile:s2.mzML:height\n"
            "1,445.1605,445.16,445.17,5.747,5.7,5.8,1.0E6,9.8E5,1,3,9.8E5,9.0E5\n"
            "2,154.0499,154.049,154.050,5.75,5.7,5.8,2.0E4,1.9E4,1,0,1.9E4,\n",
            encoding="utf-8",
        )
        return path

    def test_maps_rows_and_per_file_heights(self, tmp_path):
        table = parse_mzmine_csv(
            self._write(tmp_path), sample_names=["s1", "s2"]
        )
        assert [f.feature_id for f in table.features] == ["1", "2"]
        first = table.features[0]
        assert first.mz == pytest.approx(445.1605)
        # RT minutes -> seconds
        assert first.rt == pytest.approx(5.747 * 60.0)
        assert first.intensities["s1"] == pytest.approx(9.8e5)
        assert first.intensities["s2"] == pytest.approx(9.0e5)
        second = table.features[1]
        assert second.intensities["s2"] == 0.0  # empty cell

    def test_missing_required_columns_fail_closed(self, tmp_path):
        path = tmp_path / "bad.csv"
        path.write_text("foo,bar\n1,2\n", encoding="utf-8")
        with pytest.raises(InvalidInputError, match="missing required columns"):
            parse_mzmine_csv(path)

    def test_empty_export_fails_closed(self, tmp_path):
        path = tmp_path / "empty.csv"
        path.write_text("", encoding="utf-8")
        with pytest.raises(InvalidInputError, match="empty"):
            parse_mzmine_csv(path)


class TestExecutableResolution:
    def test_macos_app_bundle_layout(self, tmp_path):
        exe = tmp_path / "mzmine.app" / "Contents" / "MacOS" / "mzmine"
        exe.parent.mkdir(parents=True)
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
        detector = MZmineDetector(mzmine_home=str(tmp_path))
        assert detector.executable == str(exe)

    def test_missing_home_fails_closed(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MZMINE_HOME", raising=False)
        detector = MZmineDetector(mzmine_home="")
        assert detector.executable is None

    def test_version_mismatch_refuses_batch(self, monkeypatch, tmp_path):
        exe = tmp_path / "mzmine"
        exe.write_text(
            "#!/bin/sh\n"
            'case "$1" in -v) echo "mzmine 4.9.14 libraries: 4.9.14";; '
            "*) echo unexpected >&2; exit 1;; esac\n"
        )
        exe.chmod(0o755)
        real_file = tmp_path / "sample.mzML"
        real_file.write_bytes(b"<indexedmzML/>")
        user = tmp_path / "u.mzuser4"
        user.write_text("{}")
        monkeypatch.setattr(
            "metabolomics.feature_detection.shutil.which",
            lambda _: str(exe),
        )
        detector = MZmineDetector(
            mzmine_home=str(tmp_path), user_file=str(user)
        )
        with pytest.raises(CloudServiceError, match="version mismatch"):
            detector.detect([real_file], DetectParams())
