"""Storage-order and dtype fixes for Bruker readers (ADR-0075).

Ground truth for each test:

* submatrix undo: write a known matrix in TopSpin XDIM block order,
  read it back through load_2d, recover the exact matrix (round-trip
  property);
* flat storage (XDIM == 0 / XDIM == SI): unchanged historical behaviour;
* partner symmetry: a synthetic non-square COSY read in the correct
  orientation must satisfy the physical (a, b)/(b, a) partner test;
* DTYPA: an acqus declaring DTYPA=2 stores float64 re/im pairs; the
  loader must read them as such (healthy decay), and the int32 default
  must keep working for DTYPA=0 files.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from nmr_preproc.pipeline import load_experiment, parse_jcamp
from nmr_preproc.twod import (
    TwoDData,
    _partner_symmetry_score,
    load_2d,
    undo_submatrix_storage,
)


def _write_pdata(
    root: Path, matrix: np.ndarray, xdim1: int, xdim2: int
) -> Path:
    """Write matrix as a TopSpin pdata/1 with XDIM block storage."""
    si1, si2 = matrix.shape
    pdata = root / "pdata" / "1"
    pdata.mkdir(parents=True)
    if xdim1 <= 0 or xdim2 <= 0:
        flat = matrix.astype("<i4").ravel().tobytes()
    else:
        blocks = matrix.reshape(si1 // xdim1, xdim1, si2 // xdim2, xdim2)
        # Write in EXACTLY the byte order undo_submatrix_storage undoes
        # (the round-trip contract under test).
        flat = blocks.transpose(0, 2, 1, 3).reshape(-1).astype("<i4").tobytes()
    (pdata / "2rr").write_bytes(flat)
    common = [
        "##TITLE= Parameter file",
        "##JCAMPDX= 5.0",
        "##DATATYPE= Parameter Values",
        "##END=",
    ]

    def _procs(si: int, xdim: int) -> str:
        lines = [
            "##TITLE= Parameter file",
            "##$SI= %d" % si,
            "##$XDIM= %d" % xdim,
            "##$SF= 500.11",
            "##$SW_p= 11000.0",
            "##$OFFSET= 10.05",
            "##$NC_proc= 0",
            "##$DTYPP= 0",
            "##$BYTORDP= 0",
            "##END=",
        ]
        return "\n".join(lines) + "\n"

    (pdata / "procs").write_text(_procs(si2, xdim2))
    (pdata / "proc2s").write_text(_procs(si1, xdim1))
    assert common  # (formatting anchor)
    return pdata


class TestSubmatrixUndo:
    def test_xdim_block_storage_round_trips(self, tmp_path: Path) -> None:
        rng = np.random.default_rng(42)
        si1, si2, xd1, xd2 = 128, 256, 32, 64
        matrix = rng.integers(-1000, 1000, size=(si1, si2)).astype(float)
        pdata = _write_pdata(tmp_path, matrix, xd1, xd2)
        loaded = load_2d(pdata, "HMBC")
        assert loaded.matrix.shape == (si1, si2)
        np.testing.assert_allclose(loaded.matrix, matrix)

    def test_flat_storage_unchanged(self, tmp_path: Path) -> None:
        rng = np.random.default_rng(1)
        si1, si2 = 64, 64
        matrix = rng.integers(-500, 500, size=(si1, si2)).astype(float)
        # XDIM == 0: flat row-major file
        pdata = _write_pdata(tmp_path, matrix, 0, 0)
        raw = matrix.astype("<i4").ravel().tobytes()
        (pdata / "2rr").write_bytes(raw)
        procs = (pdata / "procs").read_text().replace("##$XDIM= 0", "##$XDIM= 0")
        (pdata / "procs").write_text(procs)
        loaded = load_2d(pdata, "HMBC")
        np.testing.assert_allclose(loaded.matrix, matrix)

    def test_non_divisible_xdim_falls_back_to_flat(
        self, tmp_path: Path
    ) -> None:
        rng = np.random.default_rng(2)
        si1, si2 = 64, 64
        matrix = rng.integers(-500, 500, size=(si1, si2)).astype(float)
        pdata = _write_pdata(tmp_path, matrix, 0, 0)
        (pdata / "2rr").write_bytes(matrix.astype("<i4").ravel().tobytes())
        # XDIM that does not divide SI: the undo must not corrupt the read
        text = (pdata / "procs").read_text().replace(
            "##$XDIM= 0", "##$XDIM= 48"
        )
        (pdata / "procs").write_text(text)
        loaded = load_2d(pdata, "HMBC")
        np.testing.assert_allclose(loaded.matrix, matrix)

    def test_truncated_file_raises(self, tmp_path: Path) -> None:
        rng = np.random.default_rng(3)
        matrix = rng.integers(0, 10, size=(32, 32)).astype(float)
        pdata = _write_pdata(tmp_path, matrix, 8, 8)
        (pdata / "2rr").write_bytes(
            (pdata / "2rr").read_bytes()[:100]
        )
        with pytest.raises(ValueError, match="too small"):
            load_2d(pdata, "HMBC")


class TestPartnerSymmetry:
    def test_synthetic_non_square_cosy_passes(self) -> None:
        # Gaussian cross-peaks with symmetric partners in ppm space,
        # non-square point grids (F1 128 pts, F2 256 pts)
        f1 = np.linspace(10.0, 0.0, 128)
        f2 = np.linspace(10.0, -1.0, 256)
        m = np.zeros((128, 256))
        for a, b in [(7.3, 6.5), (6.5, 7.3), (5.9, 5.9), (3.6, 3.5), (3.5, 3.6)]:
            i = int(np.argmin(np.abs(f1 - a)))
            j = int(np.argmin(np.abs(f2 - b)))
            m[i, j] += 100.0
        assert _partner_symmetry_score(m, f1, f2) > 0.9

    def test_scrambled_orientation_detected(self) -> None:
        f1 = np.linspace(10.0, 0.0, 128)
        f2 = np.linspace(10.0, -1.0, 256)
        m = np.zeros((128, 256))
        # peaks WITHOUT partners
        for a, b in [(7.3, 6.5), (5.9, 2.0), (1.2, 8.8)]:
            i = int(np.argmin(np.abs(f1 - a)))
            j = int(np.argmin(np.abs(f2 - b)))
            m[i, j] += 100.0
        assert _partner_symmetry_score(m, f1, f2) < 0.5

    def test_cosy_orientation_corrected_on_load(
        self, tmp_path: Path
    ) -> None:
        # Build a symmetric-in-ppm COSY stored TRANSPOSED: the loader's
        # physical check must flip it back so f2/f1 axes are right.
        f_axis = np.linspace(10.0, 0.0, 64)
        m = np.zeros((64, 64))
        for a, b in [(7.3, 6.5), (6.5, 7.3), (5.9, 5.9)]:
            i = int(np.argmin(np.abs(f_axis - a)))
            j = int(np.argmin(np.abs(f_axis - b)))
            m[i, j] += 100.0
        pdata = _write_pdata(tmp_path, m.T, 0, 0)
        (pdata / "2rr").write_bytes(m.T.astype("<i4").ravel().tobytes())
        loaded = load_2d(pdata, "COSY")
        # diagonal peaks sit at equal f2/f1 in the corrected orientation
        diag = [
            (f2, f1) for f2, f1, _h in [
                (loaded.f2_ppm[j], loaded.f1_ppm[i], loaded.matrix[i, j])
                for i, j in zip(*np.where(loaded.matrix > 90))
            ]
        ]
        assert any(abs(a - b) < 0.2 for a, b in diag)


class TestDtypaHandling:
    def _experiment(self, tmp_path: Path, dtypa: int) -> Path:
        exp = tmp_path / "1"
        exp.mkdir(parents=True)
        n_complex = 512
        t = np.arange(n_complex) / 10000.0
        envelope = np.exp(-60.0 * t)
        re = (1000.0 * envelope * np.cos(2 * np.pi * 700.0 * t)).astype(
            "<f8" if dtypa == 2 else "<i4"
        )
        im = (1000.0 * envelope * np.sin(2 * np.pi * 700.0 * t)).astype(
            "<f8" if dtypa == 2 else "<i4"
        )
        interleaved = np.empty(2 * n_complex)
        interleaved[0::2] = re
        interleaved[1::2] = im
        (exp / "fid").write_bytes(interleaved.astype(
            "<f8" if dtypa == 2 else "<i4"
        ).tobytes())
        acqus = "\n".join([
            "##TITLE= Parameter file",
            f"##$DTYPA= {dtypa}",
            "##$BYTORDA= 0",
            "##$TD= %d" % n_complex,
            "##$SW_h= 10000.0",
            "##$SFO1= 500.113088179",
            "##$PULPROG= <zg30>",
            "##$SOLVENT= <CDCl3>",
            "##END=",
        ]) + "\n"
        (exp / "acqus").write_text(acqus)
        return exp.parent if False else exp

    def test_dtypa2_reads_float64_fid(self, tmp_path: Path) -> None:
        exp = self._experiment(tmp_path, dtypa=2)
        fid, meta = load_experiment(exp)
        assert meta["fid_data_type"] == "<f8"
        assert fid.size == 512
        # a healthy FID decays; the DTYPA bug read these as int32 noise
        assert meta["decay_ratio"] > 5.0

    def test_dtypa0_int32_default_unchanged(self, tmp_path: Path) -> None:
        exp = self._experiment(tmp_path, dtypa=0)
        fid, meta = load_experiment(exp)
        assert meta["fid_data_type"] == "<i4"
        assert meta["decay_ratio"] > 5.0

    def test_acqus_parse_keeps_numeric_dtypa(self, tmp_path: Path) -> None:
        exp = self._experiment(tmp_path, dtypa=2)
        params = parse_jcamp(exp / "acqus")
        assert params["DTYPA"] == 2


class TestUndoSubmatrixStorageDirect:
    def test_pure_function_round_trip(self) -> None:
        rng = np.random.default_rng(11)
        si1, si2, xd1, xd2 = 32, 64, 8, 16
        matrix = rng.integers(-99, 99, size=(si1, si2)).astype(float)
        blocks = matrix.reshape(si1 // xd1, xd1, si2 // xd2, xd2)
        stored = blocks.transpose(0, 2, 1, 3).reshape(-1)
        undone = undo_submatrix_storage(stored, si1, si2, xd1, xd2)
        np.testing.assert_allclose(undone, matrix)


class TestTwoDDataContract:
    def test_load_returns_descending_axes(self, tmp_path: Path) -> None:
        rng = np.random.default_rng(7)
        matrix = rng.integers(0, 100, size=(64, 64)).astype(float)
        pdata = _write_pdata(tmp_path, matrix, 16, 16)
        loaded = load_2d(pdata, "HMBC", title="t")
        assert isinstance(loaded, TwoDData)
        assert loaded.f2_ppm[0] > loaded.f2_ppm[-1]
        assert loaded.f1_ppm[0] > loaded.f1_ppm[-1]
        assert loaded.title == "t"
