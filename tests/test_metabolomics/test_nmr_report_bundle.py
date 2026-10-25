"""Publication-bundle export (services/nmr_preproc/app/report.py).

Contract tests for the Q1 report rules R1-R6 and bundle integrity: a
bundle must contain every promised component, carry a SHA256 for each
file, refuse to write when rules are violated, and be re-openable by a
third party with only the standard library.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pytest

from nmr_preproc.report import (
    CandidateRankRow,
    PeakAssignment,
    PublishedSource,
    ReportError,
    SampleReport,
    StructureProposal,
    _layout_label_levels,
    export_bundle,
)


def _sample(sample_id: str = "S1") -> SampleReport:
    ppm = np.linspace(10.0, 0.0, 512)
    spectrum = np.exp(-((ppm - 7.2) ** 2) / 0.01)  # one line at 7.2 ppm
    return SampleReport(
        sample_id=sample_id,
        display_name=f"Sample {sample_id} (1H, CDCl3)",
        spectrum=spectrum,
        ppm=ppm,
        solvent="CDCl3",
        pulse_program="zg30",
        spectrometer_frequency_mhz=500.11,
        referencing_shift_ppm=0.002,
        peaks=[(7.201, 1.0), (3.65, 0.21)],
        excluded_regions=[(7.24, 7.28)],
        notes=["line picked at threshold 0.004"],
    )


def _proposal(sample_id: str = "S1") -> StructureProposal:
    return StructureProposal(
        sample_id=sample_id,
        structure_name="maackiain",
        compound_class="pterocarpan",
        formula="C16H12O5",
        key_evidence="H-11a doublet + methylenedioxy pair",
        confidence="structure level (multi-spectral match)",
        smiles="C1[C@@H]2[C@H](C3=C(O1)C=C(C=C3)O)OC4=CC5=C(C=C24)OCO5",
        atom_labels={2: "11a", 19: "OCH2O", 0: "6"},
    )


def _assignments(sample_id: str = "S1") -> list[PeakAssignment]:
    return [
        PeakAssignment(sample_id, 7.20, "d, J=8.4 Hz", "H-1",
                       "aromatic A-ring", "shift matches published value",
                       annotation_x=7.2),
        PeakAssignment(sample_id, 5.47, "d, J=6.8 Hz", "H-11a",
                       "benzylic junction", "coupling pattern",
                       annotation_x=5.5),
        PeakAssignment(sample_id, 5.90, "2d, J=1.4 Hz", "OCH2O",
                       "methylenedioxy", "diastereotopic pair"),
    ]


def _sources() -> list[PublishedSource]:
    return [
        PublishedSource(
            "T2018",
            "Teixeira et al. 2018, Rev. Bras. Farmacogn. 28:333-338",
        )
    ]


def _export(tmp_path: Path, **overrides):
    kwargs = dict(
        title="Test bundle",
        samples=[_sample()],
        rankings=[
            CandidateRankRow(
                sample_id="S1", candidate_name="maackiain", score=1.888,
                ranking_convention="lower_is_better", coverage=0.18,
                ranking_interval=(1, 1), verdict="abstain",
                evaluated_modalities=["h_1d", "c_1d"],
                unavailable_modalities=["ms2"],
            )
        ],
        proposals=[_proposal()],
        assignments=_assignments(),
        sources=_sources(),
        limitations=["automatic peak picks require review"],
        discussion=["The pterocarpan pattern matches published data [T2018]."],
        provenance={"tool": "phyto-companion", "model": "shiftmpnn-mock"},
        execution_log=["loaded S1", "picked peaks", "ranked candidates"],
        caveats=["automatic peak picks require review"],
    )
    kwargs.update(overrides)
    return export_bundle(tmp_path / "bundle.zip", **kwargs)


class TestLabelLayout:
    """Mnova-style stacked labels: same-level labels must never overlap."""

    def test_close_peaks_go_to_different_levels(self) -> None:
        # 5.89 / 5.92 ppm pair with realistic label half-widths (~0.25 ppm)
        levels = _layout_label_levels([(5.89, 0.25), (5.92, 0.25), (6.43, 0.25)])
        assert levels[0] != levels[1]
        assert levels[2] == 0  # far enough away -> back to the lowest row

    def test_no_same_level_overlap_for_many_labels(self) -> None:
        import random

        random.seed(7)
        xs = sorted(round(random.uniform(0, 10), 3) for _ in range(40))
        positions = [(x, 0.35) for x in xs]
        levels = _layout_label_levels(positions)
        by_level: dict[int, list[tuple[float, float]]] = {}
        for (x, hw), lvl in zip(positions, levels):
            by_level.setdefault(lvl, []).append((x - hw, x + hw))
        for intervals in by_level.values():
            intervals.sort()
            for (a_lo, _), (_, b_hi) in zip(intervals, intervals[1:]):
                assert a_lo >= b_hi or b_hi <= a_lo or _ >= intervals[0][0], (
                    "same-level labels overlap"
                )
                # strict check:
                prev_hi = b_hi
                del prev_hi
        # strict pairwise check per level
        for intervals in by_level.values():
            intervals.sort()
            for i in range(len(intervals) - 1):
                assert intervals[i][1] <= intervals[i + 1][0], "overlap on level"

    def test_single_label_gets_level_zero(self) -> None:
        assert _layout_label_levels([(7.2, 0.3)]) == [0]


class TestQ1Rules:
    def test_r1_missing_proposal_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ReportError, match="R1"):
            _export(tmp_path, proposals=[])
        assert not (tmp_path / "bundle.zip").exists()

    def test_r1_deferral_needs_resolving_experiment(self, tmp_path) -> None:
        prop = _proposal()
        prop.deferred = True
        prop.resolves_with = None
        with pytest.raises(ReportError, match="resolves_with"):
            _export(tmp_path, proposals=[prop])

    def test_bad_atom_label_index_refused(self, tmp_path: Path) -> None:
        prop = _proposal()
        prop.atom_labels = {999: "X"}
        with pytest.raises(ReportError, match="out of range"):
            _export(tmp_path, proposals=[prop])

    def test_unparseable_smiles_refused(self, tmp_path: Path) -> None:
        prop = _proposal()
        prop.smiles = "not-a-smiles"
        with pytest.raises(ReportError, match="cannot parse SMILES"):
            _export(tmp_path, proposals=[prop])

    def test_r2_too_few_assignments_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ReportError, match="R2"):
            _export(tmp_path, assignments=_assignments()[:2])

    def test_r4_unresolvable_citation_refused(self, tmp_path: Path) -> None:
        bad = [PublishedSource("X", "some paper")]
        with pytest.raises(ReportError, match="R4"):
            _export(tmp_path, sources=bad)

    def test_r5_empty_limitations_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ReportError, match="R5"):
            _export(tmp_path, limitations=[])


class TestExportBundle:
    def test_bundle_contains_every_component(self, tmp_path: Path) -> None:
        path = _export(tmp_path)
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
        for required in (
            "report.docx",
            "README.txt",
            "tables/experiment_inventory.csv",
            "tables/peak_lists.csv",
            "tables/peak_assignments.csv",
            "tables/candidate_rankings.csv",
            "figures/spectrum_S1.png",
            "figures/structure_S1.png",
            "provenance/manifest.json",
            "provenance/execution_log.txt",
            "provenance/checksums.json",
        ):
            assert required in names, f"missing bundle component {required}"

    def test_checksums_cover_every_file_and_match(self, tmp_path: Path) -> None:
        path = _export(tmp_path)
        with zipfile.ZipFile(path) as zf:
            recorded = json.loads(zf.read("provenance/checksums.json"))
            for name in zf.namelist():
                # checksums.json cannot contain its own digest
                if name == "provenance/checksums.json":
                    continue
                assert name in recorded, f"{name} not checksummed"
                digest = hashlib.sha256(zf.read(name)).hexdigest()
                assert digest == recorded[name], f"checksum drift in {name}"

    def test_empty_samples_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ReportError, match="no samples"):
            _export(tmp_path, samples=[], proposals=[], assignments=[])
        assert not (tmp_path / "bundle.zip").exists()

    def test_report_docx_opens_and_carries_q1_sections(
        self, tmp_path: Path
    ) -> None:
        from docx import Document

        path = _export(tmp_path, title="Readable report")
        with zipfile.ZipFile(path) as zf:
            doc = Document(io.BytesIO(zf.read("report.docx")))
        headings = [p.text for p in doc.paragraphs if p.style.name.startswith("Heading")]
        for section in (
            "Proposed structures", "Limitations", "Discussion", "References"
        ):
            assert section in headings, f"missing Q1 section {section}"
        text = "\n".join(p.text for p in doc.paragraphs)
        cell_text = "\n".join(
            c.text for t in doc.tables for row in t.rows for c in row.cells
        )
        assert "maackiain" in (text + cell_text)
        assert "[T2018] Teixeira et al. 2018" in text
        # assignment table content present
        assert any("H-11a" in row.cells[2].text
                   for t in doc.tables for row in t.rows)

    def test_assignment_table_rows_written(self, tmp_path: Path) -> None:
        path = _export(tmp_path)
        with zipfile.ZipFile(path) as zf:
            content = zf.read("tables/peak_assignments.csv").decode()
        assert "H-11a" in content
        assert "benzylic junction" in content


class TestQnmrBundleOutputs:
    """ADR-0073 additions: region fits and quantification land in the
    bundle as a figure and CSV tables, checksummed like every payload."""

    def _sample_with_fits(self) -> SampleReport:
        import numpy as np

        from nmr_preproc.deconvolution import DeconvolutionParams, fit_region
        from nmr_preproc.quantify import (
            AnalyteSpec,
            InternalStandard,
            qnmr_calculate,
        )

        ppm = np.linspace(7.4, 7.0, 4001)
        gamma2 = (0.003 / 2.0) ** 2
        spectrum = gamma2 / ((ppm - 7.2) ** 2 + gamma2)
        sample = _sample()
        sample.ppm = ppm
        sample.spectrum = spectrum
        sample.region_fits = [
            fit_region(
                ppm, spectrum, 7.15, 7.25,
                DeconvolutionParams(model="lorentzian", sf_mhz=500.0),
            )
        ]
        sample.quantification = qnmr_calculate(
            InternalStandard(
                name="maleate", n_protons=2, integral=100.0, moles_nmol=100.0
            ),
            [AnalyteSpec(name="flavone-A", n_protons=3, integral=150.0,
                         molecular_weight=300.0)],
            sample_mass_mg=100.0,
        )
        assert sample.quantification.analytes[0].moles_nmol == 100.0
        return sample

    def test_deconvolution_csv_and_quantification_csv_written(
        self, tmp_path: Path
    ) -> None:
        path = _export(tmp_path, samples=[self._sample_with_fits()])
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            assert "tables/deconvolution_S1.csv" in names
            assert "tables/quantification.csv" in names
            deconv = zf.read("tables/deconvolution_S1.csv").decode()
            assert "component_center_ppm" in deconv
            assert "7.2" in deconv
            quant = zf.read("tables/quantification.csv").decode()
            assert "flavone-A" in quant
            assert "n(flavone-A)" in quant  # substituted equation travels
            assert "figures/deconvolution_S1.png" in names
            checksums = json.loads(zf.read("provenance/checksums.json"))
            assert "tables/quantification.csv" in checksums

    def test_bundle_without_fits_unchanged(self, tmp_path: Path) -> None:
        path = _export(tmp_path)
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
        assert not any("deconvolution" in n for n in names)
        assert "tables/quantification.csv" not in names
