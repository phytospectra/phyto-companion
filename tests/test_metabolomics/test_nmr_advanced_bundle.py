"""Contract tests for 2D, home-library, NMReDATA, and pharmacology."""

from __future__ import annotations

from pathlib import Path
import zipfile
import numpy as np

from nmr_preproc.report import (
    CorrelationEvidence, DereplicationResult, PeakAssignment,
    PharmacologyRow, PublishedSource, SampleReport, StructureProposal, TwoDSpectrum,
    export_bundle,
)
from nmr_preproc.home_library import dereplicate, load_library


def _full_bundle(tmp_path: Path):
    s = SampleReport(
        sample_id="S1", display_name="Sample S1 (1H, CDCl3)",
        spectrum=np.linspace(0, 1, 256), ppm=np.linspace(10, 0, 256),
        solvent="CDCl3", pulse_program="zg30",
        spectrometer_frequency_mhz=500.11, referencing_shift_ppm=0.002,
        peaks=[(7.35, 1.0), (6.55, 0.4), (5.47, 0.3)],
    )
    prop = StructureProposal(
        sample_id="S1", structure_name="maackiain", compound_class="pterocarpan",
        formula="C16H12O5", key_evidence="test", confidence="structure level",
        smiles="C1[C@@H]2[C@H](C3=C(O1)C=C(C=C3)O)OC4=CC5=C(C=C24)OCO5",
        atom_labels={2: "11a", 0: "6"},
    )
    asg = [
        PeakAssignment("S1", 7.35, "d", "H-1", "A-ring", "basis", 7.35),
        PeakAssignment("S1", 6.55, "dd", "H-2", "A-ring", "basis", 6.55),
        PeakAssignment("S1", 5.47, "d", "H-11a", "junction", "basis", 5.47),
    ]
    tw = TwoDSpectrum(
        sample_id="S1", kind="COSY",
        matrix=np.eye(64), f2_ppm=np.linspace(10, 0, 64),
        f1_ppm=np.linspace(10, 0, 64), title="COSY S1",
        correlations=[CorrelationEvidence("S1", "COSY", 5.47, 3.49, "H-11a <-> H-6a")],
    )
    derep = DereplicationResult(
        sample_id="S1", best_match="maackiain", citation="Teixeira 2018",
        coverage=1.0, mean_abs_deviation=0.005,
        verdict="MSI Level 1 pending human sign-off",
    )
    pharm = PharmacologyRow(
        compound="maackiain", target="MAO-B", method="similarity",
        confidence=0.78, confidence_basis="known ligand", citation="PMID:27575476",
    )
    src = [PublishedSource("T", "Teixeira 2018, Rev. Bras. Farmacogn. 28:333-338")]
    return export_bundle(
        tmp_path / "bundle.zip", title="Full bundle",
        samples=[s], rankings=[], proposals=[prop], assignments=asg,
        sources=src, limitations=["limits"], discussion=["disc [T]"],
        provenance={"tool": "test"}, execution_log=["run"], caveats=["c"],
        twod=[tw], dereplications=[derep], pharmacology=[pharm],
    )


class TestFullBundleCapabilities:
    def test_nmredata_sdf_valid(self, tmp_path: Path) -> None:
        p = _full_bundle(tmp_path)
        with zipfile.ZipFile(p) as zf:
            assert "nmredata/S1.nmredata.sdf" in zf.namelist()
            content = zf.read("nmredata/S1.nmredata.sdf").decode()
            assert "NMREDATA_VERSION" in content
            assert "NMREDATA_1D_1H" in content
            assert "$$$$" in content

    def test_json_assignments_valid(self, tmp_path: Path) -> None:
        import json as _j
        p = _full_bundle(tmp_path)
        with zipfile.ZipFile(p) as zf:
            content = _j.loads(zf.read("tables/assignments_S1.json"))
            assert content["sample_id"] == "S1"
            assert len(content["assignments"]) == 3
            assert content["correlations"][0]["kind"] == "COSY"

    def test_2d_and_pharmacology_tables_present(self, tmp_path: Path) -> None:
        p = _full_bundle(tmp_path)
        with zipfile.ZipFile(p) as zf:
            names = set(zf.namelist())
            assert "tables/correlations.csv" in names
            assert "tables/dereplication.csv" in names
            assert "tables/pharmacology.csv" in names
            assert "figures/spectrum2d_S1_COSY.png" in names


class TestHomeLibrary:
    def test_maackiain_stamps_level1_pending(self) -> None:
        lib = load_library("data/curated_home_library.csv")
        assert len(lib) >= 3
        maack_obs = [7.35, 6.55, 6.42, 3.49, 3.64, 4.22, 6.72, 6.43, 5.47, 5.92]
        res, verdict = dereplicate(maack_obs, [e for e in lib if e.nucleus == "1H"])
        assert res is not None
        assert res.entry.name == "maackiain"
        assert res.coverage >= 0.90
        assert "MSI Level 1 pending" in verdict
