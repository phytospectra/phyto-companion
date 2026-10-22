"""UV-chromophore classifier tests (ADR-0062): profiles, confidence,
edge cases and the SMILES structural predictor."""

from __future__ import annotations

import pytest

from phyto_shared.uv_chromophore import (
    UVClass,
    classify_uv,
    predict_class_from_smiles,
)


def _two_band_spectrum(band_i: float, band_ii: float, i_height=2.0, ii_height=1.0):
    """A minimal PDA-like spectrum with maxima at the two band positions."""
    points = []
    for wl in range(220, 421, 5):
        a_i = i_height * max(0.0, 1.0 - abs(wl - band_i) / 12.5)
        a_ii = ii_height * max(0.0, 1.0 - abs(wl - band_ii) / 10.0)
        points.append((float(wl), round(a_i + a_ii, 6)))
    return points


FLAVONOL_SPECTRUM = _two_band_spectrum(370.0, 280.0)      # ratio ~2.0
TERPENE_SPECTRUM = [(float(w), 0.01 + 0.001 * w) for w in range(220, 421, 10)]


def test_flavonol_spectrum_classifies_as_flavonol() -> None:
    result = classify_uv(FLAVONOL_SPECTRUM)
    assert result.primary_class == "flavonol"
    assert result.confidence >= 0.6
    assert result.band_i_nm == pytest.approx(370.0, abs=6)
    assert result.band_ii_nm == pytest.approx(280.0, abs=6)
    assert result.band_ratio is not None


def test_isoflavone_spectrum_classifies_as_isoflavone() -> None:
    # Isoflavone: Band I (295-330), Band II (240-275), ratio 0.45-1.0.
    result = classify_uv(_two_band_spectrum(310.0, 260.0, 1.0, 1.6))
    assert result.primary_class == "isoflavone"


def test_flat_spectrum_returns_terpene_like() -> None:
    result = classify_uv(TERPENE_SPECTRUM)
    assert result.primary_class == "terpene_like"


def test_monotonic_ramp_is_terpene_like_not_unclassified() -> None:
    # A baseline drift has no resolved maximum: the endpoint of a ramp is
    # an acquisition-window artefact, never a band.
    ramp = [(float(w), 0.01 + 0.0002 * w) for w in range(220, 421, 5)]
    assert classify_uv(ramp).primary_class == "terpene_like"


def test_empty_spectrum_returns_unclassified_zero_confidence() -> None:
    result = classify_uv([])
    assert result.primary_class == "unclassified"
    assert result.confidence == 0.0


def test_bands_outside_every_profile_give_unclassified() -> None:
    # Maxima at 220 and 400 with ratio far from every window.
    result = classify_uv(_two_band_spectrum(400.0, 220.0, 3.0, 3.0))
    assert result.primary_class in ("unclassified", "chalcone")


def test_band_ratio_requires_both_bands() -> None:
    with pytest.raises(ValueError, match="band_ratio"):
        UVClass(primary_class="flavonol", confidence=0.9, band_ratio=2.0)


def test_confidence_is_clamped_to_unit_interval() -> None:
    with pytest.raises(ValueError):
        UVClass(primary_class="flavonol", confidence=1.5)


# --- SMILES structural predictor -------------------------------------------


def test_quercetin_predicts_flavonol_family() -> None:
    # 3-hydroxy chromen-4-one (flavonol) tautomer; RDKit kekulization
    # needs the C3-OH written on the pyranone carbon for the SMARTS to
    # see [OX2H] adjacent to the ring carbonyl.
    classes = predict_class_from_smiles(
        "O=c1c(O)c(-c2c(O)cc(O)cc2O)oc2cc(O)cc(O)c12"
    )
    assert "flavonol" in classes


def test_terpene_smiles_predicts_terpene_like() -> None:
    # limonene: no aromatic atoms, no conjugated carbonyl
    classes = predict_class_from_smiles("CC1=CCC(CC(C1)C(=C)C)C")
    assert classes == ["terpene_like"]


def test_unparseable_smiles_returns_empty_not_guessed() -> None:
    assert predict_class_from_smiles("not-a-smiles") == []


def test_papaverine_predicts_isoquinoline_alkaloid() -> None:
    classes = predict_class_from_smiles(
        "COc1ccc(Cc2cnc3cc(OC)c(OC)cc3c2C)cc1OC"
    )
    assert "alkaloid_isoquinoline" in classes