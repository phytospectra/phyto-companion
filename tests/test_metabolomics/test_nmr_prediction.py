"""Rule-based 1H prediction engine (services/nmr_preproc/app/predict.py).

Ground truth: chemistry every organic chemist can verify -- ethanol's
CH3/CH2/OH pattern and a para-disubstituted benzene's 4H aromatic set.
"""

from __future__ import annotations

import numpy as np
import pytest

from nmr_preproc.predict import NMRPredictionEngine, PredictedPeak


@pytest.fixture(scope="module")
def engine() -> NMRPredictionEngine:
    return NMRPredictionEngine()


class TestFeatureExtractionAndShifts:
    def test_ethanol_pattern(self, engine) -> None:
        preds = engine.predict_shifts("CCO")
        by_shift = sorted(p.chemical_shift for p in preds)
        assert len(preds) == 2  # OH excluded: predictor covers C-H only
        # methyl upfield; CH2 deshielded by its O neighbour (rule engine)
        assert by_shift[0] == pytest.approx(0.9, abs=0.05)
        assert by_shift[1] > by_shift[0] + 0.3
        labels = {p.multiplicity for p in preds}
        assert labels == {"t", "q"}  # CH3 sees CH2 (4); CH2 sees CH3 (3+1)

    def test_homotopic_protons_collapsed(self, engine) -> None:
        preds = engine.predict_shifts("c1ccccc1")
        assert len(preds) == 1  # benzene: six equivalent CH -> one entry
        assert preds[0].integration == 6
        assert 6.5 < preds[0].chemical_shift < 8.0

    def test_invalid_smiles_raises(self, engine) -> None:
        with pytest.raises(ValueError, match="Invalid SMILES"):
            engine.predict_shifts("not-a-smiles")


class TestSpectralSimulation:
    def test_quartet_has_four_components(self, engine) -> None:
        peak = PredictedPeak("H_C0", 3.6, 2, "q", 3)
        x, y = engine.simulate_spectrum([peak], ppm_start=4.2, ppm_end=3.0,
                                        points=4000)
        # local maxima count == vicinal + 1 (Pascal-split quartet)
        from scipy.signal import find_peaks

        pk, _ = find_peaks(y, height=0.15 * y.max())
        assert len(pk) == 4
        # symmetric intensities 1:3:3:1 -> middle pair tallest
        assert y[pk[1]] == pytest.approx(y[pk[2]], rel=0.02)
        assert y[pk[0]] < y[pk[1]]

    def test_output_normalized(self, engine) -> None:
        preds = engine.predict_shifts("CCO")
        _, y = engine.simulate_spectrum(preds)
        assert y.max() == pytest.approx(1.0)
        assert np.all(y >= 0.0)
