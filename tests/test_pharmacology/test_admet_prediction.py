"""ADMET: honest unavailability in the local plane."""

from __future__ import annotations

from pharmacology.admet_prediction import UNAVAILABLE_NOTE, predict_admet


class TestADMET:
    def test_returns_unavailable_not_a_guess(self):
        result = predict_admet("CCO")
        assert result.method == "unavailable"
        assert result.predictions == {}
        assert result.model_version is None

    def test_limitations_always_reported(self):
        result = predict_admet("CCO")
        assert result.known_limitations == UNAVAILABLE_NOTE
        assert "honest" in result.known_limitations.lower()
        assert result.published_performance is None

    def test_smiles_preserved(self):
        assert predict_admet("c1ccccc1").smiles == "c1ccccc1"
