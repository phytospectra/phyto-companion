"""ShiftPredictor tests: deterministic mock, fallback rules, ranges."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from phyto_shared.exceptions import CloudServiceError
from elucidation.shift_predictor import (
    MockShiftPredictor,
    ShiftPrediction,
    VertexAIShiftPredictor,
    get_shift_predictor,
)

QUERCETIN = "O=c1cc(-c2c(O)cc(O)cc2O)oc2cc(O)cc(O)c12"


@pytest.fixture()
def predictor() -> MockShiftPredictor:
    return MockShiftPredictor()


class TestMockLookup:
    def test_benzene_table(self, predictor):
        prediction = predictor.predict("c1ccccc1")
        assert prediction.atom_symbols == ["C"] * 6
        assert prediction.predicted_c_shifts == [128.5] * 6
        assert prediction.predicted_h_shifts == [7.26] * 6
        assert prediction.model_id == "shiftmpnn-mock"

    def test_one_entry_per_heavy_atom_for_references(self, predictor):
        # length assertions pin the hand-typed tables to RDKit atom counts
        for smiles, expected in [
            ("c1ccccc1", 6),
            ("CCO", 3),
            (QUERCETIN, 22),
            ("O=c1cc(-c2ccc(O)cc2O)oc2cc(O)cc(O)c12", 21),  # kaempferol
            ("Cn1c(=O)c2c(ncn2C)n(C)c1=O", 14),  # caffeine
        ]:
            prediction = predictor.predict(smiles)
            assert len(prediction.atom_indices) == expected
            assert len(prediction.predicted_c_shifts) == expected
            assert len(prediction.predicted_h_shifts) == expected

    def test_deterministic(self, predictor):
        first = predictor.predict(QUERCETIN)
        second = predictor.predict(QUERCETIN)
        assert first.predicted_c_shifts == second.predicted_c_shifts
        assert first.predicted_h_shifts == second.predicted_h_shifts

    def test_latency_recorded(self, predictor):
        assert predictor.predict("CCO").inference_latency_ms >= 0.0


class TestFallbackRules:
    def test_unknown_molecule_gets_rules_not_exception(self, predictor):
        prediction = predictor.predict("CCCCCC")  # hexane: not in the table
        assert len(prediction.atom_indices) == 6
        assert all(c >= 0.0 for c in prediction.predicted_c_shifts)
        assert all(c <= 220.0 for c in prediction.predicted_c_shifts)
        assert all(0.0 <= h <= 12.0 for h in prediction.predicted_h_shifts)

    def test_rule_shifts_are_plausible(self, predictor):
        aromatic = predictor.predict("c1ccccc1O")  # phenol
        carbons = [
            c
            for symbol, c in zip(aromatic.atom_symbols, aromatic.predicted_c_shifts)
            if symbol == "C"
        ]
        assert all(0.0 <= c <= 220.0 for c in carbons)
        hydrogens = [h for h in aromatic.predicted_h_shifts if h >= 0.0]
        assert all(0.0 <= h <= 12.0 for h in hydrogens)

    def test_unparseable_smiles_raises(self, predictor):
        with pytest.raises(CloudServiceError):
            predictor.predict("not-a-smiles")


class TestRegistration:
    def test_register_and_length_validation(self, predictor):
        predictor.register("CCO", [(10.0, 1.0), (20.0, 2.0), (-1.0, 3.0)])
        assert predictor.predict("CCO").predicted_c_shifts == [10.0, 20.0, -1.0]
        with pytest.raises(ValueError):
            predictor.register("CCO", [(1.0, 1.0)])


class TestVertexClient:
    def test_fails_closed_without_sdk(self):
        client = VertexAIShiftPredictor(endpoint_id="1", project="p")
        with pytest.raises(CloudServiceError) as excinfo:
            client.predict("CCO")
        assert "local plane" in str(excinfo.value)

    def test_cost_table_lookup(self):
        client = VertexAIShiftPredictor(
            endpoint_id="1", project="p", model_id="shiftmpnn-v4-l4"
        )
        assert client.estimate_cost_usd() == 0.0004


class TestFactory:
    def test_dry_run_forces_mock(self, monkeypatch):
        monkeypatch.setenv("PHYTO_DRY_RUN", "1")
        assert isinstance(get_shift_predictor(), MockShiftPredictor)

    def test_no_sdk_defaults_to_mock(self, monkeypatch):
        monkeypatch.delenv("PHYTO_DRY_RUN", raising=False)
        assert isinstance(get_shift_predictor(), MockShiftPredictor)


class TestModel:
    def test_shift_prediction_requires_all_fields(self):
        with pytest.raises(ValidationError):
            ShiftPrediction(smiles="CCO")  # everything else missing
