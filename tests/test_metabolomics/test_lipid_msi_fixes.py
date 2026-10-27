"""Lipid-region exclusion from 13C assignment (ADR-0076) and the MSI
level attribution fix (ADR-0075).
"""

from __future__ import annotations

from phyto_shared.constants import MSI_LEVEL_NAMES, MSIConfidenceLevel
from phyto_shared.lipid_regions import (
    DEFAULT_LIPID_REGIONS_13C,
    LIPID_REGIONS_13C,
    curate_13c_assignment,
    is_in_regions,
)

from elucidation.candidate_ranker import (
    ObservedEvidence,
    RankingConfig,
    lipid_curation_13c,
    rank_candidates,
)


class TestLipidRegions:
    def test_default_mask_is_the_safe_aliphatic_window(self) -> None:
        # the default must NOT eat pterocarpan/flavonoid carbons
        assert DEFAULT_LIPID_REGIONS_13C == ((10.0, 40.0),)
        for analyte_carbon in (66.8, 78.5, 101.6, 130.3, 157.5, 175.8):
            assert not is_in_regions(analyte_carbon)
        for lipid_carbon in (14.1, 22.7, 29.7, 31.9, 34.0):
            assert is_in_regions(lipid_carbon)

    def test_catalog_carries_reasons(self) -> None:
        assert len(LIPID_REGIONS_13C) >= 4
        assert all(
            isinstance(reason, str) and reason
            for _lo, _hi, reason in LIPID_REGIONS_13C
        )

    def test_curation_keeps_analyte_drops_lipid(self) -> None:
        peaks = [
            (66.84, 1.0), (78.56, 1.0), (101.69, 1.0), (157.58, 1.0),
            (14.12, 9.0), (29.73, 20.0), (31.93, 6.0),
        ]
        curation = curate_13c_assignment(peaks)
        assert [round(p, 2) for p, _ in curation.kept] == [
            66.84, 78.56, 101.69, 157.58,
        ]
        assert curation.n_excluded == 3
        assert "fatty-acyl" in curation.summary()

    def test_curation_respects_opt_in_windows(self) -> None:
        peaks = [(63.1, 1.0), (101.7, 1.0)]
        glycerol_window = ((55.0, 75.0),)
        curation = curate_13c_assignment(peaks, regions=glycerol_window)
        assert [round(p, 1) for p, _ in curation.kept] == [101.7]
        assert curation.n_excluded == 1


class TestRankerLipidExclusion:
    def test_c_1d_lipid_peaks_removed_with_note(self) -> None:
        observed = ObservedEvidence(
            c_1d=[66.8, 101.7, 157.6, 29.7, 14.1],
            formula="C16H12O5",
        )
        filtered, notes = lipid_curation_13c(observed)
        assert filtered.c_1d == [66.8, 101.7, 157.6]
        assert len(notes) == 1
        assert "2 13C peak(s) excluded" in notes[0].spectral_feature
        assert notes[0].relation == "lipid_region_excluded"
        # input never mutated
        assert len(observed.c_1d) == 5

    def test_hsqc_lipid_crosspeaks_removed(self) -> None:
        observed = ObservedEvidence(
            hsqc=[(6.5, 110.0, "d"), (1.3, 29.7, "m"), (4.2, 66.8, "t")],
            formula="C16H12O5",
        )
        filtered, notes = lipid_curation_13c(observed)
        assert (1.3, 29.7, "m") not in filtered.hsqc
        assert len(filtered.hsqc) == 2
        assert any("HSQC" in n.spectral_feature for n in notes)

    def test_empty_mask_disables_filter(self) -> None:
        observed = ObservedEvidence(c_1d=[29.7], formula="C6H6")
        config = RankingConfig(exclude_lipid_regions_13c=())
        filtered, notes = lipid_curation_13c(observed, config)
        assert filtered.c_1d == [29.7]
        assert notes == []


class TestMSILevels:
    def test_standard_level_names(self) -> None:
        assert MSI_LEVEL_NAMES[MSIConfidenceLevel.LEVEL_1] == (
            "identified compound"
        )
        assert MSI_LEVEL_NAMES[MSIConfidenceLevel.LEVEL_2] == (
            "putatively annotated compound"
        )
        assert MSI_LEVEL_NAMES[MSIConfidenceLevel.LEVEL_3] == (
            "putatively characterized compound class"
        )
        assert MSI_LEVEL_NAMES[MSIConfidenceLevel.LEVEL_4] == (
            "unknown compound"
        )

    def test_ranker_attributes_level2_baseline(self) -> None:
        from elucidation.shift_predictor import get_shift_predictor

        candidates = rank_candidates(
            ObservedEvidence(h_1d=[7.26] * 6, formula="C6H6"),
            _benzene_candidates(),
            RankingConfig(),
            get_shift_predictor(),
        )
        assert candidates
        for rank in candidates:
            # spectral similarity without an authentic standard is the
            # standard's Level 2, never Level 1
            assert rank.msi_level == 2
            assert rank.level_1_match is None


def _benzene_candidates():
    from phyto_shared.models import Candidate, MolecularFormula

    formula = MolecularFormula(counts={"C": 6, "H": 6})
    return [
        Candidate(
            id="benzene",
            name="benzene",
            smiles="c1ccccc1",
            formula=formula,
            exact_mass=78.04695,
            provenance="structure_only",
            source="test",
        )
    ]
