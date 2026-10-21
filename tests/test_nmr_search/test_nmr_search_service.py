"""NMR structure-search tests: matching, ranking, licence tiering, endpoints.

The fixture-driven mock is the subject under test; live database clients are
checked only for offline logic (no network).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from phyto_shared.nmr_search_models import LicenseInfo, NMRSpectrumHit, NMRSpectrumMatch

import nmr_search.main as nmr_main
from nmr_search.clients import (
    LICENSES,
    MockNMRSearchClient,
    NMRShiftDB2Client,
    assign_peaks,
    rank_hits,
)
from nmr_search.router import NMRSearchRouter


FIXTURE_PEAKS = [(1.575, 38.23), (1.8, 29.07), (3.41, 81.02), (1.35, 56.46)]


def _hit(database_id: str, coverage: float, distance: float, licence: LicenseInfo | None = None,
         database: str = "BMRB") -> NMRSpectrumHit:
    return NMRSpectrumHit(
        database=database,  # type: ignore[arg-type]
        database_id=database_id,
        name=database_id,
        match=NMRSpectrumMatch(
            matched_peaks=int(coverage * 10),
            query_peaks=10,
            distance=distance,
            coverage=coverage,
        ),
        license=licence or LICENSES["BMRB"],
    )


# ---------------------------------------------------------------------------
# Peak assignment
# ---------------------------------------------------------------------------


class TestAssignPeaks:
    def test_exact_match_is_full_coverage_zero_distance(self):
        match = assign_peaks(FIXTURE_PEAKS, FIXTURE_PEAKS, 0.05, 1.0)
        assert match.matched_peaks == 4
        assert match.coverage == 1.0
        assert match.distance == 0.0

    def test_outside_tolerance_is_unmatched(self):
        match = assign_peaks([(5.0, 100.0)], [(1.0, 20.0)], 0.05, 1.0)
        assert match.matched_peaks == 0
        assert match.coverage == 0.0
        assert match.distance == 999.0

    def test_partial_coverage_is_reported(self):
        match = assign_peaks(
            [(1.575, 38.23), (9.9, 200.0)], FIXTURE_PEAKS, 0.05, 1.0
        )
        assert match.matched_peaks == 1
        assert match.coverage == 0.5

    def test_each_reference_peak_is_used_once(self):
        # Two query peaks inside tolerance of one reference peak: only one can
        # match, otherwise coverage would overstate agreement.
        match = assign_peaks(
            [(1.575, 38.23), (1.58, 38.25)], [(1.575, 38.23)], 0.05, 1.0
        )
        assert match.matched_peaks == 1

    def test_closest_reference_is_preferred(self):
        match = assign_peaks([(1.58, 38.3)], [(1.575, 38.23), (1.60, 39.0)], 0.05, 1.0)
        # The nearer reference is consumed; the match is still 1 peak and the
        # distance reflects the closer pair.
        assert match.matched_peaks == 1
        assert match.distance < 0.1


# ---------------------------------------------------------------------------
# Ranking and licence filtering (ADR-0031 / ADR-0026)
# ---------------------------------------------------------------------------


class TestRanking:
    def test_coverage_beats_distance(self):
        ranked = rank_hits(
            [_hit("low", 0.5, 0.001), _hit("high", 0.9, 0.5)], max_results=10
        )
        assert [h.database_id for h in ranked] == ["high", "low"]

    def test_distance_breaks_coverage_ties(self):
        ranked = rank_hits(
            [_hit("far", 0.8, 0.5), _hit("near", 0.8, 0.1)], max_results=10
        )
        assert [h.database_id for h in ranked] == ["near", "far"]

    def test_zero_coverage_hits_are_dropped(self):
        ranked = rank_hits([_hit("none", 0.0, 999.0), _hit("some", 0.5, 0.1)], 10)
        assert [h.database_id for h in ranked] == ["some"]

    def test_max_results_is_respected(self):
        hits = [_hit(f"h{i}", 0.5, 0.1 * i) for i in range(5)]
        assert len(rank_hits(hits, max_results=2)) == 2


class TestLicenceTiering:
    def test_commercial_tier_drops_cc_by_nc(self):
        router = NMRSearchRouter([MockNMRSearchClient()])
        outcome = router.search_by_peaks(
            FIXTURE_PEAKS,
            h_tolerance=0.05,
            c_tolerance=1.0,
            max_results=10,
            include_noncommercial=False,
        )
        assert outcome.result.hits == []
        assert outcome.result.database_counts["local-fixture"] >= 1

    def test_academic_tier_keeps_cc_by_nc(self):
        router = NMRSearchRouter([MockNMRSearchClient()])
        outcome = router.search_by_peaks(
            FIXTURE_PEAKS,
            h_tolerance=0.05,
            c_tolerance=1.0,
            max_results=10,
            include_noncommercial=True,
        )
        assert outcome.result.hits
        assert all(h.license.name == "CC-BY-NC" for h in outcome.result.hits)

    def test_licence_map_flags_commercial_use_correctly(self):
        assert LICENSES["NP-MRD"].commercial_use is False
        assert LICENSES["BMRB"].commercial_use is True
        assert LICENSES["nmrshiftdb2"].commercial_use is True

    def test_licence_travels_with_each_hit(self):
        router = NMRSearchRouter([MockNMRSearchClient()])
        outcome = router.search_by_peaks(
            FIXTURE_PEAKS, h_tolerance=0.05, c_tolerance=1.0, max_results=5
        )
        for hit in outcome.result.hits:
            assert hit.license.name and hit.license.attribution


class TestRouter:
    def test_requires_a_client(self):
        with pytest.raises(ValueError):
            NMRSearchRouter([])

    def test_failing_database_does_not_sink_search(self):
        class Broken:
            name = "Broken"

            def search_by_peaks(self, *args, **kwargs):
                raise RuntimeError("database down")

            def get_compound_shifts(self, name, database_id):
                return None

        router = NMRSearchRouter([MockNMRSearchClient(), Broken()])
        outcome = router.search_by_peaks(
            FIXTURE_PEAKS, h_tolerance=0.05, c_tolerance=1.0, max_results=5
        )
        assert outcome.result.hits
        assert outcome.errors["Broken"] == "database down"

    def test_database_filter_restricts_clients(self):
        router = NMRSearchRouter([MockNMRSearchClient()])
        outcome = router.search_by_peaks(
            FIXTURE_PEAKS,
            h_tolerance=0.05,
            c_tolerance=1.0,
            max_results=5,
            databases=["BMRB"],  # fixture client is not BMRB
        )
        assert outcome.result.hits == []
        assert outcome.result.database_counts == {}

    def test_shifted_variant_ranks_below_primary(self):
        router = NMRSearchRouter([MockNMRSearchClient()])
        outcome = router.search_by_peaks(
            FIXTURE_PEAKS, h_tolerance=0.05, c_tolerance=1.0, max_results=10
        )
        ids = [h.database_id for h in outcome.result.hits]
        assert ids[0] == "FIXTURE-ANDROGRAPHOLIDE"
        assert "FIXTURE-ANDROGRAPHOLIDE-SHIFTED" in ids

    def test_compound_shifts_by_name(self):
        result = NMRSearchRouter([MockNMRSearchClient()]).get_compound_shifts(
            name="andrographolide", database_id=None
        )
        assert result is not None
        assert result.shifts
        assert result.license.commercial_use is False

    def test_compound_shifts_unknown_identifier(self):
        result = NMRSearchRouter([MockNMRSearchClient()]).get_compound_shifts(
            name="nonexistent-compound-xyz", database_id=None
        )
        assert result is None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


class TestEndpoints:
    def setup_method(self):
        nmr_main.get_router.cache_clear()
        self.client = TestClient(nmr_main.app)

    def teardown_method(self):
        nmr_main.get_router.cache_clear()

    def test_health(self):
        assert self.client.get("/health").json()["service"] == "nmr_search"

    def test_peak_search_returns_hits_and_manifest(self):
        response = self.client.post(
            "/nmr_search/peaks",
            json={"peaks": FIXTURE_PEAKS, "max_results": 5},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["result"]["hits"]
        assert payload["result"]["query_peak_count"] == 4
        assert payload["provenance_manifest_id"]

    def test_commercial_tier_request_drops_nc_records(self):
        response = self.client.post(
            "/nmr_search/peaks",
            json={"peaks": FIXTURE_PEAKS, "include_noncommercial": False},
        )
        assert response.status_code == 200
        assert response.json()["result"]["hits"] == []

    def test_duplicate_peaks_are_rejected(self):
        response = self.client.post(
            "/nmr_search/peaks",
            json={"peaks": [FIXTURE_PEAKS[0], FIXTURE_PEAKS[0]]},
        )
        assert response.status_code == 422

    def test_empty_peak_list_is_rejected(self):
        response = self.client.post("/nmr_search/peaks", json={"peaks": []})
        assert response.status_code == 422

    def test_compound_shifts_route(self):
        response = self.client.post(
            "/nmr_search/compound_shifts", json={"name": "andrographolide"}
        )
        assert response.status_code == 200
        assert response.json()["result"]["shifts"]

    def test_compound_request_needs_an_identifier(self):
        response = self.client.post("/nmr_search/compound_shifts", json={})
        assert response.status_code == 422

    def test_compound_shifts_unknown_is_404(self):
        response = self.client.post(
            "/nmr_search/compound_shifts", json={"name": "not-a-compound-xyz"}
        )
        assert response.status_code == 404


class TestNMRShiftDB2Parsing:
    def test_hit_parsing_is_offline_and_uses_licence(self):
        client = NMRShiftDB2Client()
        assert client.name == "nmrshiftdb2"
        assert LICENSES["nmrshiftdb2"].commercial_use is True


# ---------------------------------------------------------------------------
# Flavonoid fixtures (kaempferol / quercetin / luteolin)
# ---------------------------------------------------------------------------


def _reference_peaks(database_id: str) -> list[tuple[float, float]]:
    """Query peaks taken straight from a fixture's reference table."""
    result = MockNMRSearchClient().get_compound_shifts(
        name=None, database_id=database_id
    )
    assert result is not None, f"fixture {database_id} did not load"
    return [(h, c) for _label, h, c in result.shifts]


class TestFlavonoidFixtures:
    def _client(self) -> MockNMRSearchClient:
        return MockNMRSearchClient()

    def test_all_four_compounds_load(self):
        tables = self._client()._tables
        assert set(tables) == {
            "andrographolide_hsqc_literature.csv",
            "kaempferol_hsqc.csv",
            "quercetin_hsqc.csv",
            "luteolin_hsqc.csv",
        }
        assert all(len(peaks) > 0 for peaks in tables.values())

    def test_comment_lines_do_not_leak_into_tables(self):
        # Each flavonoid fixture opens with provenance comments; none of them
        # may be parsed as a data row.
        for database_id in ("FIXTURE-KAEMPFEROL", "FIXTURE-LUTEOLIN", "FIXTURE-QUERCETIN"):
            assert _reference_peaks(database_id), f"{database_id} parsed empty"
            for h, c in _reference_peaks(database_id):
                assert 0.0 < h < 12.0
                assert 0.0 < c < 220.0

    def test_kaempferol_query_returns_kaempferol_top1(self):
        query = _reference_peaks("FIXTURE-KAEMPFEROL")
        hits = self._client().search_by_peaks(query, 0.05, 1.0, 10)
        assert hits, "kaempferol query returned no candidates"
        assert hits[0].database_id == "FIXTURE-KAEMPFEROL"
        assert hits[0].match.coverage == 1.0

    def test_luteolin_query_returns_luteolin_top1(self):
        query = _reference_peaks("FIXTURE-LUTEOLIN")
        hits = self._client().search_by_peaks(query, 0.05, 1.0, 10)
        assert hits, "luteolin query returned no candidates"
        assert hits[0].database_id == "FIXTURE-LUTEOLIN"
        assert hits[0].match.coverage == 1.0

    def test_quercetin_query_returns_quercetin_top1(self):
        query = _reference_peaks("FIXTURE-QUERCETIN")
        hits = self._client().search_by_peaks(query, 0.05, 1.0, 10)
        assert hits
        assert hits[0].database_id == "FIXTURE-QUERCETIN"

    def test_isomeric_overlap_returns_both_candidates(self):
        # Kaempferol and luteolin are C15H10O6 isomers sharing most A/B-ring
        # shifts: a kaempferol query must surface luteolin as a plausible --
        # lower-ranked -- candidate rather than suppressing it.
        query = _reference_peaks("FIXTURE-KAEMPFEROL")
        hits = self._client().search_by_peaks(query, 0.05, 1.0, 10)
        ids = [hit.database_id for hit in hits]
        assert ids[0] == "FIXTURE-KAEMPFEROL"
        assert "FIXTURE-LUTEOLIN" in ids
        luteolin = hits[ids.index("FIXTURE-LUTEOLIN")]
        assert 0.0 < luteolin.match.coverage < 1.0

    def test_ambiguous_mixed_query_returns_both(self):
        mixed = [
            _reference_peaks("FIXTURE-KAEMPFEROL")[0],   # H-6 region
            _reference_peaks("FIXTURE-KAEMPFEROL")[2],   # H-2'/6' region
            _reference_peaks("FIXTURE-LUTEOLIN")[0],     # H-3 (flavone only)
            _reference_peaks("FIXTURE-LUTEOLIN")[-1],    # H-6' region
        ]
        hits = self._client().search_by_peaks(mixed, 0.05, 1.0, 10)
        ids = {hit.database_id for hit in hits}
        assert {"FIXTURE-KAEMPFEROL", "FIXTURE-LUTEOLIN"} <= ids

    def test_records_without_any_matching_peak_are_not_candidates(self):
        # Aliphatic andrographolide peaks must not surface flavonoid records.
        hits = self._client().search_by_peaks(FIXTURE_PEAKS, 0.05, 1.0, 10)
        ids = {hit.database_id for hit in hits}
        assert ids <= {
            "FIXTURE-ANDROGRAPHOLIDE",
            "FIXTURE-ANDROGRAPHOLIDE-13C",
            "FIXTURE-ANDROGRAPHOLIDE-SHIFTED",
        }

    def test_compound_shifts_resolves_each_flavonoid(self):
        for needle, expected in (
            ("kaempferol", "FIXTURE-KAEMPFEROL"),
            ("quercetin", "FIXTURE-QUERCETIN"),
            ("luteolin", "FIXTURE-LUTEOLIN"),
        ):
            result = self._client().get_compound_shifts(name=needle, database_id=None)
            assert result is not None
            assert result.database_id == expected
            assert result.shifts
            assert result.license.commercial_use is False
