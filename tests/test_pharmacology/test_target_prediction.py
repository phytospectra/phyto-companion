"""Target prediction: method labels, citations, honest unavailability."""

from __future__ import annotations

import pytest

from phyto_shared.exceptions import CloudServiceError
from pharmacology.target_prediction import (
    TANIMOTO_CONFIDENCE_SCALE,
    ml_target_prediction,
    docking_target_prediction,
    pharmacophore_target_prediction,
    similarity_target_prediction,
)

QUERCETIN_IK = "REFJWTPEDVJJIY-UHFFFAOYSA-N"
QUERCETIN_SMILES = "O=c1cc(-c2c(O)cc(O)cc2O)oc2cc(O)cc(O)c12"


class TestSimilarity:
    def test_returns_method_labelled_predictions(self):
        predictions = similarity_target_prediction(QUERCETIN_IK)
        assert predictions
        for prediction in predictions:
            assert prediction.method == "similarity"
            assert 0.0 <= prediction.confidence <= 1.0
            assert prediction.confidence_basis
            assert prediction.citation, "every prediction must cite a source"
            assert prediction.evidence

    def test_confidence_follows_documented_scale(self):
        prediction = similarity_target_prediction(QUERCETIN_IK)[0]
        low, high = TANIMOTO_CONFIDENCE_SCALE
        assert "Tanimoto" in prediction.confidence_basis
        assert f"{low}-{high}" in prediction.confidence_basis

    def test_unknown_compound_returns_empty(self):
        assert similarity_target_prediction("ZZZZZZZZZZZZZZ-ZZZZZZZZZZ-Z") == []


class TestDocking:
    def test_disabled_locally_with_honest_error(self):
        with pytest.raises(CloudServiceError) as excinfo:
            docking_target_prediction(QUERCETIN_SMILES, [])
        assert "docking" in str(excinfo.value).lower()
        assert "local plane" in str(excinfo.value)

    def test_missing_binary_fails_closed(self, tmp_path):
        fake_structure = tmp_path / "target.pdbqt"
        fake_structure.write_text("ATOM")
        with pytest.raises(CloudServiceError) as excinfo:
            docking_target_prediction(
                QUERCETIN_SMILES, [fake_structure], vina_binary="vina-does-not-exist"
            )
        assert "Vina" in str(excinfo.value)


class TestPharmacophore:
    def test_feature_signature_match(self):
        predictions = pharmacophore_target_prediction(
            QUERCETIN_SMILES, {"T1": ["Donor", "Aromatic"], "T2": ["Aromatic"]}
        )
        by_target = {p.target_id: p for p in predictions}
        assert by_target["T1"].confidence == 1.0
        assert by_target["T2"].confidence == 1.0
        assert all(p.method == "pharmacophore" for p in predictions)

    def test_unmatched_features_lower_confidence(self):
        predictions = pharmacophore_target_prediction(
            "C", {"T1": ["Donor", "Aromatic", "Acceptor"]}
        )
        assert predictions[0].confidence < 1.0

    def test_unparseable_smiles_raises(self):
        with pytest.raises(CloudServiceError):
            pharmacophore_target_prediction("nope", {"T": ["Aromatic"]})


class TestML:
    def test_returns_empty_not_guess(self):
        assert ml_target_prediction(QUERCETIN_SMILES) == []


class TestMethodSeparateness:
    def test_methods_never_merged(self):
        similarity = similarity_target_prediction(QUERCETIN_IK)
        pharmacophore = pharmacophore_target_prediction(
            QUERCETIN_SMILES, {"P35354": ["Aromatic"]}
        )
        combined = similarity + pharmacophore
        assert {p.method for p in combined} == {"similarity", "pharmacophore"}
        # the same target can appear twice, once per method: never merged
        assert len(combined) == len(similarity) + len(pharmacophore)
