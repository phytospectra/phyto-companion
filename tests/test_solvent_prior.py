"""Solvent compatibility tests: logP, curves, monotonicity, unknowns."""

from __future__ import annotations

import pytest

from phyto_shared.exceptions import InvalidInputError
from phyto_shared.solvent_prior import (
    KNOWN_SOLVENTS,
    estimate_logp,
    normalise_solvent,
    solvent_compatibility,
)

LOGP_GRID = [-2.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0]


class TestEstimateLogP:
    def test_signs_are_chemically_sane_and_bad_smiles_raise(self) -> None:
        caffeine = estimate_logp("Cn1c(=O)c2c(ncn2C)n(C)c1=O")
        quercetin = estimate_logp("O=c1cc(-c2c(O)cc(O)cc2O)oc2cc(O)cc(O)c12")
        assert caffeine < 0 < quercetin
        with pytest.raises(InvalidInputError):
            estimate_logp("not-a-smiles")


class TestCompatibilityCurves:
    def test_water_favors_low_logp_monotonically(self) -> None:
        values = [solvent_compatibility(x, "water") for x in LOGP_GRID]
        assert all(a >= b for a, b in zip(values, values[1:]))

    def test_hexane_favors_high_logp_monotonically(self) -> None:
        values = [solvent_compatibility(x, "hexane") for x in LOGP_GRID]
        assert all(a <= b for a, b in zip(values, values[1:]))

    def test_documented_profile_extremes_and_bounds(self) -> None:
        # methanol: strongly compatible below its cutoff, not above
        assert solvent_compatibility(2.0, "methanol") > 0.85
        assert solvent_compatibility(7.0, "methanol") < 0.15
        # ethyl acetate: peaks inside its 1..6 window, falls outside
        inside = solvent_compatibility(3.5, "ethyl acetate")
        assert inside > 0.8
        assert inside > solvent_compatibility(-1.0, "ethyl acetate")
        assert inside > solvent_compatibility(9.0, "ethyl acetate")
        # every profile stays a probability on the whole grid
        for solvent in KNOWN_SOLVENTS:
            for logp in LOGP_GRID:
                assert 0.0 <= solvent_compatibility(logp, solvent) <= 1.0

    def test_transitions_are_smooth_not_stepwise(self) -> None:
        """One log unit either side of the water cutoff stays moderate:
        a hard cutoff would give ~1.0 vs ~0.0."""
        below = solvent_compatibility(1.0, "water")
        above = solvent_compatibility(3.0, "water")
        assert 0.3 < below < 1.0
        assert 0.0 < above < 0.7


class TestUnknownSolvents:
    def test_unknown_solvent_raises_and_aliases_normalise(self) -> None:
        with pytest.raises(InvalidInputError):
            solvent_compatibility(2.0, "vinegar")
        assert normalise_solvent("MeOH") == "methanol"
        assert normalise_solvent("DCM") == "dichloromethane"
        assert normalise_solvent("ethyl acetate") == "ethyl acetate"
