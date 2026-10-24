"""NMR preprocessing pipeline (ADR-0068): the in-house Bruker FID chain.

Ground truth: the Curcuma longa fixture (20 real Bruker-style experiment
directories with raw fid + processed 1r, documented curcuminoid decay +
7.55 ppm degradation-marker story).  Tests assert against the DOCUMENTED
dataset properties, not circularly against the implementation.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from nmr_preproc.pipeline import (
    SkipPreprocessing,
    load_experiment,
    preprocess_experiments,
)

CURCUMA = Path(__file__).resolve().parents[1] / "fixtures" / "metabolomics" / "curcuma"
DATASET = CURCUMA / "Curcuma_longa_NMR_dataset"
SAMPLE_SHEET = CURCUMA / "sample_sheet_Curcuma_longa.csv"


def _experiment_dirs() -> list[str]:
    return sorted(str(p) for p in DATASET.iterdir() if p.is_dir())


class TestLoadExperiment:
    def test_raw_fid_preferred(self) -> None:
        data, meta = load_experiment(DATASET / "CL_D00_S1")
        assert meta["source"] == "raw fid"
        assert np.iscomplexobj(data)
        assert data.size > 0

    def test_processed_1r_fallback(self, tmp_path: Path) -> None:
        # copy one experiment without fid/acqu -> only the processed data
        import shutil

        dest = tmp_path / "CL_D07_S1"
        shutil.copytree(DATASET / "CL_D07_S1", dest)
        (dest / "1" / "fid").unlink()
        (dest / "1" / "acqus").unlink()
        data, meta = load_experiment(dest)
        assert meta["source"] == "processed 1r"
        assert "ppm" in meta

    def test_not_a_directory_skips_with_reason(self) -> None:
        with pytest.raises(SkipPreprocessing, match="not a directory"):
            load_experiment("/nonexistent/experiment")

    def test_sfo1_fallback_when_sf_absent(self) -> None:
        # the Curcuma acqus carries SFO1 but no SF: the loader must still
        # resolve the spectrometer frequency
        _, meta = load_experiment(DATASET / "CL_D00_S1")
        assert meta["sf_mhz"] == pytest.approx(400.13, abs=0.01)

    def test_decay_ratio_reported_for_healthy_fid(self) -> None:
        # a physical FID decays: the real fixture must sit far above the
        # flat-envelope threshold
        _, meta = load_experiment(DATASET / "CL_D00_S1")
        assert meta["decay_ratio"] > 5.0

    def test_corrupt_fid_recorded_not_silent(self, tmp_path: Path) -> None:
        # a flat-envelope FID (corrupt/placeholder export) must surface a
        # named warning in the run notes, never be processed silently
        import shutil

        dest = tmp_path / "CL_D00_S1_flat"
        shutil.copytree(DATASET / "CL_D00_S1", dest)
        fid_path = dest / "1" / "fid"
        raw = np.fromfile(fid_path, dtype="<i4")
        # constant-magnitude alternating signal: no decay at all
        flat = np.empty_like(raw)
        flat[0::2] = 2**20
        flat[1::2] = (-1.0) ** np.arange(raw.size // 2) * (2**20)
        flat.tofile(fid_path)
        result = preprocess_experiments([str(dest)])
        assert any("no decay" in note for note in result.notes)


class TestPreprocessExperiments:
    @pytest.fixture(scope="class")
    def result(self):
        return preprocess_experiments(_experiment_dirs())

    def test_all_20_samples(self, result) -> None:
        assert result.matrix.shape[0] == 20
        assert len(result.sample_ids) == 20
        assert result.sample_ids[0].startswith("CL_")

    def test_bucket_axis_descends_in_ppm(self, result) -> None:
        assert result.ppm.shape[0] == result.matrix.shape[1]
        assert result.ppm[0] > result.ppm[-1]
        assert 9.0 <= result.ppm[0] <= 12.5
        assert 0.0 <= result.ppm[-1] <= 1.0

    def test_record_carries_every_step(self, result) -> None:
        rec = result.record
        assert rec.modality == "nmr"
        assert rec.apodisation_function
        assert rec.baseline_method
        assert rec.alignment_method
        assert rec.normalisation_method == "pqn"

    def test_pqn_normalisation_scales_comparable(self, result) -> None:
        # after PQN the spectra have comparable total scale (each row
        # normalised); no row dominates by orders of magnitude
        norms = np.linalg.norm(result.matrix, axis=1)
        assert norms.max() / norms.min() < 10.0

    def test_storage_decay_visible_in_ground_truth_region(self, result) -> None:
        # documented ground truth: curcuminoid signal DECREASES with
        # storage day (half-life ~22 days).  The cleanest single marker is
        # the curcumin enol-CH singlet at 5.95 ppm (peak_assignment_table):
        # its intensity must anti-correlate with storage_day and the
        # log-linear slope must recover a half-life in a plausible band.
        sheet = (SAMPLE_SHEET).read_text().splitlines()
        days = {
            line.split(",")[0]: float(line.split(",")[1])
            for line in sheet[1:] if line.strip()
        }
        order = np.array([days[s] for s in result.sample_ids])
        band = (result.ppm >= 5.89) & (result.ppm <= 6.01)
        intensities = result.matrix[:, band].mean(axis=1)
        r = np.corrcoef(order, intensities)[0, 1]
        assert r < -0.5, (
            "documented curcuminoid decay not visible at the 5.95 ppm "
            f"enol-CH marker (r={r:.3f})"
        )
        # group means must decrease monotonically across the 5 time points
        by_day = {}
        for d, v in zip(order, intensities):
            by_day.setdefault(d, []).append(v)
        means = [float(np.mean(by_day[d])) for d in sorted(by_day)]
        assert all(a > b for a, b in zip(means, means[1:])), (
            f"curcuminoid marker not monotone decreasing: {means}"
        )
        slope = float(np.polyfit(order, np.log(intensities), 1)[0])
        half_life = float(np.log(2) / -slope)
        assert 10.0 < half_life < 50.0, (
            f"recovered half-life {half_life:.1f} d outside the plausible "
            "band around the documented ~22 d"
        )

    def test_mixed_modalities_rejected(self, tmp_path: Path) -> None:
        # one raw-fid experiment + one processed-only experiment must be
        # refused with a NAMED reason, never silently merged
        import shutil

        a = tmp_path / "raw_exp"
        shutil.copytree(DATASET / "CL_D00_S1", a)
        b = tmp_path / "proc_exp"
        shutil.copytree(DATASET / "CL_D07_S1", b)
        (b / "1" / "fid").unlink()
        (b / "1" / "acqus").unlink()
        with pytest.raises(SkipPreprocessing, match="mixed input modalities"):
            preprocess_experiments([str(a), str(b)])


class TestSkipReporting:
    def test_empty_input_skips(self) -> None:
        with pytest.raises(SkipPreprocessing, match="no experiment directories"):
            preprocess_experiments([])
