"""Pathway enrichment: hypergeometric correctness, background honesty."""

from __future__ import annotations

import pytest

from phyto_shared.exceptions import InvalidInputError
from metabolomics.pathway_enrichment import (
    ENRICHMENT_TEST_DESCRIPTION,
    MockPathwayDatabase,
    enrich_kegg,
    map_to_compounds,
)


class TestEnrichment:
    def test_quercetin_lands_in_flavonoid_pathway(self):
        result = enrich_kegg(["FT5"])
        top = result.pathways[0]
        assert top.pathway_id in {"map00945", "map00940"}
        assert "Flavonoid" in top.pathway_name or "Phenylpropanoid" in top.pathway_name

    def test_reports_background_and_test(self):
        result = enrich_kegg(["FT5", "FT2"])
        assert result.background_size > 0
        assert result.test == ENRICHMENT_TEST_DESCRIPTION
        assert "hypergeometric" in result.test
        assert all(hit.background_size == result.background_size for hit in result.pathways)

    def test_fdr_values_bounded_and_sorted(self):
        result = enrich_kegg(["FT5", "FT2", "FT4"])
        assert all(0.0 <= hit.fdr <= 1.0 for hit in result.pathways)
        p_values = [hit.p_value for hit in result.pathways]
        assert p_values == sorted(p_values)

    def test_explicit_background_set_used(self):
        result = enrich_kegg(["FT5"], background=["C00389", "C00259", "C00311"])
        assert result.background_size == 3

    def test_more_overlap_lower_p_value(self):
        single = enrich_kegg(["FT5"])
        multi = enrich_kegg(["FT5", "FT4"])  # two flavonoid-pathway compounds
        single_map = {h.pathway_id: h.p_value for h in single.pathways}
        multi_map = {h.pathway_id: h.p_value for h in multi.pathways}
        shared = set(single_map) & set(multi_map)
        assert shared
        assert any(multi_map[p] <= single_map[p] for p in shared)

    def test_unmappable_feature_gives_empty(self):
        result = enrich_kegg(["FT_UNKNOWN"])
        assert result.pathways == []

    def test_no_query_raises(self):
        with pytest.raises(InvalidInputError):
            enrich_kegg([])


class TestCompoundMapping:
    def test_compounds_attached_per_pathway(self):
        enrichment = enrich_kegg(["FT5", "FT4"])
        mapped = map_to_compounds(enrichment, {"FT5": "IK-A", "FT4": "IK-B"})
        assert mapped.compound_map
        assert any("IK-A" in values for values in mapped.compound_map.values())


class TestMockDatabase:
    def test_pathways_have_members(self):
        members = MockPathwayDatabase().pathway_members()
        assert all(compounds for _, compounds in members.values())
