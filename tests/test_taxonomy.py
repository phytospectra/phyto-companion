"""Taxonomy service tests: resolution, fallback, lineage, unresolved names.

All offline: the live GBIF/ITIS transports are replaced with canned
payloads, and the mock client exercises the same contract the live pair
implements (ADR-0053).
"""

from __future__ import annotations

import pytest

from phyto_shared.exceptions import CloudServiceError, InvalidInputError
from phyto_shared.taxonomy_models import LINEAGE_RANKS

from taxonomy.clients import (
    GBIFClient,
    ITISClient,
    MockTaxonomyClient,
    get_lineage,
    resolve_species,
)


def _gbif_match_payload(match_type: str = "EXACT") -> dict:
    return {
        "usageKey": 2758480,
        "scientificName": "Boesenbergia rotunda (L.) Mansf.",
        "canonicalName": "Boesenbergia rotunda",
        "rank": "SPECIES",
        "matchType": match_type,
        "kingdom": "Plantae",
        "phylum": "Tracheophyta",
        "class": "Liliopsida",
        "order": "Zingiberales",
        "family": "Zingiberaceae",
        "genus": "Boesenbergia",
        "species": "Boesenbergia rotunda",
    }


def _raise(url, params):
    raise AssertionError("ITIS must not be called when GBIF matches")


def _itis_transport(url: str, params: dict) -> dict:
    if "searchByScientificName" in url:
        return {
            "scientificNames": [
                {
                    "tsn": "506504",
                    "unitName1": "Boesenbergia",
                    "unitName2": "rotunda",
                }
            ]
        }
    tsn = params.get("searchFromTSN")
    if tsn == "506504":
        return {
            "hierarchyList": [
                {"tsn": "50", "rankName": "Kingdom", "taxonName": "Plantae"},
                {"tsn": "60", "rankName": "Division", "taxonName": "Tracheophyta"},
                {"tsn": "70", "rankName": "Class", "taxonName": "Liliopsida"},
                {"tsn": "80", "rankName": "Order", "taxonName": "Zingiberales"},
                {"tsn": "90", "rankName": "Family", "taxonName": "Zingiberaceae"},
                {"tsn": "100", "rankName": "Genus", "taxonName": "Boesenbergia"},
                {"tsn": "506504", "rankName": "Species", "taxonName": "Boesenbergia rotunda"},
            ]
        }
    raise AssertionError(f"unexpected ITIS call {url} {params}")


class TestResolveSpecies:
    def test_gbif_exact_and_fuzzy_matches_return_full_lineage(self) -> None:
        exact = GBIFClient(transport=lambda url, params: _gbif_match_payload())
        resolution = resolve_species(
            "Boesenbergia rotunda", gbif=exact, itis=ITISClient(transport=_raise)
        )
        assert resolution.canonical_name == "Boesenbergia rotunda"
        assert resolution.taxon_key == "2758480"
        assert resolution.rank == "SPECIES"
        assert resolution.source == "GBIF"
        assert resolution.match_type == "EXACT"
        for rank in LINEAGE_RANKS:
            assert rank in resolution.lineage, f"missing lineage rank {rank}"
        assert resolution.lineage["family"] == "Zingiberaceae"

        fuzzy = GBIFClient(
            transport=lambda url, params: _gbif_match_payload("FUZZY")
        )
        resolution = resolve_species(
            "Boesenbergia rotunda", gbif=fuzzy, itis=ITISClient(transport=_raise)
        )
        assert resolution.match_type == "FUZZY"
        assert resolution.canonical_name == "Boesenbergia rotunda"

    def test_itis_fallback_when_gbif_has_no_match(self) -> None:
        gbif = GBIFClient(
            transport=lambda url, params: {"matchType": "NONE", "usageKey": None}
        )
        itis = ITISClient(transport=_itis_transport)
        resolution = resolve_species("Boesenbergia rotunda", gbif=gbif, itis=itis)
        assert resolution.source == "ITIS"
        assert resolution.taxon_key == "506504"
        assert resolution.lineage["family"] == "Zingiberaceae"
        # ITIS calls plants' phylum a Division; it must be normalised.
        assert resolution.lineage["phylum"] == "Tracheophyta"

    def test_unresolved_or_blank_names_raise_invalid_input(self) -> None:
        gbif = GBIFClient(
            transport=lambda url, params: {"matchType": "NONE", "usageKey": None}
        )
        itis = ITISClient(transport=lambda url, params: {"scientificNames": []})
        with pytest.raises(InvalidInputError):
            resolve_species("Nonexistentus nullus", gbif=gbif, itis=itis)
        # blank names never reach a provider at all
        with pytest.raises(InvalidInputError):
            resolve_species("   ", gbif=MockTaxonomyClient(), itis=MockTaxonomyClient())

    def test_both_providers_unreachable_raises_cloud_error(self) -> None:
        def _fail(url, params):
            raise CloudServiceError("down")

        with pytest.raises(CloudServiceError):
            resolve_species(
                "Boesenbergia rotunda",
                gbif=GBIFClient(transport=_fail),
                itis=ITISClient(transport=_fail),
            )


class TestLineage:
    def test_gbif_lineage_walks_root_first_and_facade_uses_client(self) -> None:
        payload = _gbif_match_payload()
        payload["kingdomKey"] = 6
        payload["familyKey"] = 4687
        client = GBIFClient(transport=lambda url, params: payload)
        nodes = client.lineage("2758480")
        assert [n.rank for n in nodes] == list(LINEAGE_RANKS)
        assert nodes[0].name == "Plantae" and nodes[0].taxon_key == "6"
        family = nodes[LINEAGE_RANKS.index("family")]
        assert family.name == "Zingiberaceae" and family.taxon_key == "4687"
        facade_nodes = get_lineage("2758480", client=client)
        assert facade_nodes[0].rank == "kingdom"
        assert facade_nodes[-1].rank == "species"

    def test_mock_lineage_for_each_pilot_species(self) -> None:
        client = MockTaxonomyClient()
        for name, family in (
            ("Boesenbergia rotunda", "Zingiberaceae"),
            ("Andrographis paniculata", "Acanthaceae"),
            ("Curcuma longa", "Zingiberaceae"),
        ):
            resolution = client.resolve(name)
            assert resolution is not None, f"mock must know {name}"
            assert resolution.lineage["family"] == family
            nodes = client.lineage(resolution.taxon_key)
            assert nodes[0].rank == "kingdom"
            assert nodes[-1].name == resolution.canonical_name

    def test_mock_unknown_species_and_keys_rejected(self) -> None:
        assert MockTaxonomyClient().resolve("Arabidopsis thaliana") is None
        with pytest.raises(InvalidInputError):
            MockTaxonomyClient().lineage("0000000")
