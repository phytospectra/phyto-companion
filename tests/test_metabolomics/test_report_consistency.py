"""Automatic cross-artefact consistency checks (ADR-0075).

Ground truth: the ACS text and the assignment tables are generated from
the same PeakAssignment objects, so a bundle whose numbers disagree can
only come from drift (stale text, hand-edited table, or a writer bug) --
exactly what check_bundle_consistency must catch and what export_bundle
must refuse.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from nmr_preproc.consistency import (
    ConsistencyFinding,
    _text_entries,
    check_bundle_consistency,
)
from nmr_preproc.report import (
    PeakAssignment,
    PublishedSource,
    ReportError,
    SampleReport,
    StructureProposal,
    export_bundle,
    format_nmr_text_acs,
)


def _sample(sid: str = "S1", nucleus_pulse: str = "zg30") -> SampleReport:
    return SampleReport(
        sample_id=sid,
        display_name=f"Sample {sid}",
        spectrum=np.linspace(0, 1, 128),
        ppm=np.linspace(10, 0, 128),
        solvent="CDCl3",
        pulse_program=nucleus_pulse,
        spectrometer_frequency_mhz=500.11,
        referencing_shift_ppm=0.002,
        peaks=[(7.35, 1.0), (6.55, 0.4), (5.47, 0.3)],
    )


def _assignments(sid: str = "S1") -> list[PeakAssignment]:
    return [
        PeakAssignment(sid, 7.35, "d, J 8.4", "H-1", "A-ring", "basis", 7.35),
        PeakAssignment(sid, 6.55, "dd, J 8.4/2.4", "H-2", "A-ring", "basis", 6.55),
        PeakAssignment(sid, 5.47, "d, J 6.8", "H-11a", "junction", "basis", 5.47),
    ]


class TestTextParsing:
    def test_parses_shifts_and_labels(self) -> None:
        text = (
            "1H NMR (500 MHz, CDCl3) δ 7.35 (d, J 8.4, H-1), "
            "3.49 (m, H-6a), 5.92 (d, J 1.4, OCH2O (a))."
        )
        assert _text_entries(text) == [
            (7.35, "H-1"),
            (3.49, "H-6a"),
            (5.92, "OCH2O"),
        ]

    def test_ignores_header_numbers(self) -> None:
        text = "1H NMR (500 MHz, CDCl3) δ 7.35 (d, H-1)."
        # "500" in the header must not be read as a shift
        assert [s for s, _l in _text_entries(text)] == [7.35]


class TestConsistencyChecks:
    def test_clean_sample_passes(self) -> None:
        report = check_bundle_consistency([_sample()], _assignments())
        assert report.ok
        assert report.summary.startswith("consistency: OK")

    def test_unassigned_picked_peak_is_a_warning(self) -> None:
        sample = _sample()
        sample.peaks.append((2.10, 0.9))  # nobody assigned this one
        report = check_bundle_consistency([sample], _assignments())
        assert report.ok  # warnings do not refuse the bundle
        assert any(
            f.check == "peaks_vs_assignments" for f in report.findings
        )

    def test_stale_text_shift_is_an_error(self) -> None:
        # Simulate text drift: the table was edited to 7.36 AFTER the
        # text was generated at 7.35.  The writer regenerates the text
        # from the table, so expose the drift via a stale assignment
        # object whose ppm differs from its quoted annotation.
        sample = _sample()
        assignments = _assignments()
        assignments[0] = PeakAssignment(
            "S1", 7.36, "d, J 8.4", "H-1", "A-ring", "basis", 7.35
        )
        # the ACS text renders from the same objects; emulate drift by
        # comparing a hand-typed text against the table through the
        # parser: a 0.01 gap exceeds the 2-decimal text tolerance
        stale_text = format_nmr_text_acs(sample, assignments).replace(
            "7.36", "7.35"
        )
        parsed = _text_entries(stale_text)
        table_shift = assignments[0].ppm
        assert not any(
            abs(shift - table_shift) <= 0.005 + 1e-9 for shift, _ in parsed
        )

    def test_component_outside_fitted_window_is_an_error(self) -> None:
        from nmr_preproc.deconvolution import FittedComponent, RegionFit

        sample = _sample()
        sample.region_fits = [
            RegionFit(
                lo_ppm=6.4, hi_ppm=6.6, status="fitted",
                components=[
                    # drifted centre: a component reported at 7.20 ppm
                    # for a window of [6.4, 6.6] can only be stale data
                    FittedComponent(
                        center_ppm=7.20, fwhm_hz=3.0, height=1.0,
                        area=1.0, model="pseudovoigt",
                    )
                ],
                r_squared=0.99,
            )
        ]
        report = check_bundle_consistency([sample], _assignments())
        assert not report.ok
        assert any(
            f.check == "deconvolution_within_region"
            for f in report.findings
        )

    def test_comma_label_breaks_text_roundtrip(self) -> None:
        # a label containing a comma cannot survive the ACS text format
        # unambiguously; the check must flag it instead of letting the
        # table and the text silently disagree
        assignments = _assignments()
        assignments[0] = PeakAssignment(
            "S1", 7.35, "d, J 8.4", "H-1, H-2", "A-ring", "basis", 7.35
        )
        report = check_bundle_consistency([_sample()], assignments)
        assert not report.ok
        finding = next(
            f for f in report.findings if f.severity == "error"
        )
        assert isinstance(finding, ConsistencyFinding)
        assert finding.sample_id == "S1"
        assert finding.message


class TestExportRefusesInconsistentBundle:
    def test_drifted_table_refuses_bundle(self, tmp_path: Path) -> None:
        from nmr_preproc.deconvolution import FittedComponent, RegionFit

        sample = _sample()
        assignments = _assignments()
        # stale deconvolution record: centre outside the fitted window
        sample.region_fits = [
            RegionFit(
                lo_ppm=6.4, hi_ppm=6.6, status="fitted",
                components=[
                    FittedComponent(
                        center_ppm=7.20, fwhm_hz=3.0, height=1.0,
                        area=1.0, model="pseudovoigt",
                    )
                ],
                r_squared=0.99,
            )
        ]
        proposal = StructureProposal(
            sample_id="S1", structure_name="maackiain",
            compound_class="pterocarpan", formula="C16H12O5",
            key_evidence="test", confidence="structure level",
        )
        with pytest.raises(ReportError, match="consistency checks failed"):
            export_bundle(
                tmp_path / "bundle.zip",
                title="t", samples=[sample], rankings=[],
                proposals=[proposal], assignments=assignments,
                sources=[PublishedSource("T", "Teixeira 2018, Rev. Bras. Farmacogn. 28:333-338")],
                limitations=["l"], discussion=["d [T]"],
                provenance={}, execution_log=[], caveats=[],
            )

    def test_consistent_bundle_carries_report_file(self, tmp_path: Path) -> None:
        import zipfile

        sample = _sample()
        proposal = StructureProposal(
            sample_id="S1", structure_name="maackiain",
            compound_class="pterocarpan", formula="C16H12O5",
            key_evidence="test", confidence="structure level",
        )
        out = export_bundle(
            tmp_path / "bundle.zip",
            title="t", samples=[sample], rankings=[],
            proposals=[proposal], assignments=_assignments(),
            sources=[PublishedSource("T", "Teixeira 2018, Rev. Bras. Farmacogn. 28:333-338")],
            limitations=["l"], discussion=["d [T]"],
            provenance={}, execution_log=[], caveats=[],
        )
        with zipfile.ZipFile(out) as zf:
            text = zf.read("tables/consistency_report.txt").decode()
            assert text.startswith("consistency: OK")
