"""Building-blocks networking tests (He et al. 2021)."""

from __future__ import annotations

import numpy as np
import pytest

from networking.building_blocks import (
    BUILDING_BLOCK_CATALOGUE,
    BuildingBlock,
    annotate_building_blocks,
    detect_building_block,
)
from networking.gnps_client import Cluster, MNResults

HEXOSE_SPECTRUM = np.array(
    [[145.0495, 100.0], [163.0601, 60.0], [300.1000, 20.0]]
)
COUMAROYL_SPECTRUM = np.array([[147.0441, 90.0], [163.0390, 40.0]])


class TestDetection:
    def test_all_diagnostic_ions_present_detects_block(self):
        hexose = next(b for b in BUILDING_BLOCK_CATALOGUE if b.name == "hexose")
        assert detect_building_block(HEXOSE_SPECTRUM, hexose) is True

    def test_missing_one_ion_rejects_block(self):
        hexose = next(b for b in BUILDING_BLOCK_CATALOGUE if b.name == "hexose")
        partial = HEXOSE_SPECTRUM[1:]  # drop 145.0495
        assert detect_building_block(partial, hexose) is False

    def test_tolerance_window(self):
        single = BuildingBlock(name="single", fragment_mz=[100.0], smarts_pattern="C")
        assert detect_building_block(np.array([[100.015, 1.0]]), single, tol_da=0.02) is True
        assert detect_building_block(np.array([[100.030, 1.0]]), single, tol_da=0.02) is False

    def test_empty_spectrum_is_negative(self):
        hexose = next(b for b in BUILDING_BLOCK_CATALOGUE if b.name == "hexose")
        assert detect_building_block(np.empty((0, 2)), hexose) is False

    def test_multiple_blocks_in_one_spectrum(self):
        combined = np.vstack([HEXOSE_SPECTRUM, COUMAROYL_SPECTRUM])
        catalogue = {b.name: b for b in BUILDING_BLOCK_CATALOGUE}
        detected = [
            name
            for name in ("hexose", "deoxyhexose", "coumaroyl")
            if detect_building_block(combined, catalogue[name])
        ]
        assert detected == ["hexose", "coumaroyl"]


class TestCatalogue:
    def test_entries_are_well_formed(self):
        names = [block.name for block in BUILDING_BLOCK_CATALOGUE]
        assert len(names) == len(set(names))
        for block in BUILDING_BLOCK_CATALOGUE:
            assert len(block.fragment_mz) >= 1
            assert block.smarts_pattern


class TestAnnotation:
    def test_nodes_inherit_cluster_building_blocks(self):
        results = MNResults(
            graphml="<unused/>",
            clusters=[
                Cluster(
                    cluster_id="c1",
                    node_ids=["scan=1", "scan=2"],
                    consensus_spectrum_mz=[145.0495, 163.0601, 571.3012],
                    consensus_spectrum_intensity=[500.0, 400.0, 1000.0],
                ),
                Cluster(
                    cluster_id="c2",
                    node_ids=["scan=3"],
                    consensus_spectrum_mz=[802.4510],
                    consensus_spectrum_intensity=[700.0],
                ),
            ],
        )
        annotations = annotate_building_blocks(results)
        assert annotations["scan=1"] == ["hexose"]
        assert annotations["scan=2"] == ["hexose"]
        assert annotations["scan=3"] == []

    def test_rejects_wrong_input_type(self):
        with pytest.raises(TypeError):
            annotate_building_blocks({"not": "MNResults"})
