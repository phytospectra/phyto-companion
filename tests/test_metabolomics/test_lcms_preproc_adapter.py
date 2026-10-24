"""LC-MS preprocessing adapter (ADR-0069): MZmine-format CSV -> the same
PreprocessingRecord the NMR pipeline produces, so the metabolomics layer
chain is modality-agnostic.  The adapter is deliberately the ONLY LC-MS
component: no peak picking, no deconvolution (ADR-0069)."""

from __future__ import annotations

import pytest

from lcms_preproc.adapter import SkipAdaptation, adapt_csv, build_record
from phyto_shared.metabolomics import PreprocessingRecord

MZMINE_ROWS = """row ID,row m/z,row retention time,feature area,C04_Rep1,C04_Rep2,C08_Rep1,C08_Rep2
1,104.1070,1.12,1234.5,1200.0,1234.5,2210.0,2301.0
2,146.0863,2.45,99.1,90.0,99.1,410.0,395.0
3,190.0500,3.02,55.2,51.0,55.2,58.0,52.0
"""


class TestAdaptCsv:
    def test_mzmine_schema_detected(self) -> None:
        table = adapt_csv(MZMINE_ROWS)
        assert table.tool_name == "MZmine 3"
        assert len(table.feature_ids) == 3
        assert table.sample_ids == ["C04_Rep1", "C04_Rep2", "C08_Rep1", "C08_Rep2"]

    def test_intensities_pivot(self) -> None:
        # the "feature area" column sits before the sample columns and
        # must NOT be treated as a sample
        table = adapt_csv(MZMINE_ROWS)
        assert table.intensities["1"]["C04_Rep1"] == pytest.approx(1200.0)
        assert table.intensities["2"]["C08_Rep2"] == pytest.approx(395.0)

    def test_unknown_schema_skips_with_reason(self) -> None:
        with pytest.raises(SkipAdaptation, match="no data rows|expected one of"):
            adapt_csv("a,b\n1,2\n")
        with pytest.raises(SkipAdaptation):
            adapt_csv("totally,unknown,header\n1,2,3\n")

    def test_empty_file_skips(self) -> None:
        with pytest.raises(SkipAdaptation):
            adapt_csv("")


class TestBuildRecord:
    def test_record_matches_nmr_shape(self) -> None:
        table = adapt_csv(MZMINE_ROWS)
        record = build_record(table, "pqn")
        assert isinstance(record, PreprocessingRecord)
        assert record.modality == "lcms"
        assert record.normalisation_method == "pqn"
        assert record.tool_name == "MZmine 3"
        assert record.sample_ids == table.sample_ids
        # the NMR field family stays None (absent, not zero, not guessed)
        assert record.apodisation_function is None
        assert record.baseline_method is None
        assert record.alignment_method is None
