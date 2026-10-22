"""Taxon-prior tests: cross-database dedup, level preference, empty priors.

Runs entirely on the bundled snapshots (ADR-0054): the pilot's documented
data source for LOTUS/KNApSAcK/NPASS.
"""

from __future__ import annotations

import pytest
from rdkit import Chem

from phyto_shared.taxonomy_models import ReportedCompound, TaxonPrior
from phyto_shared.exceptions import CloudServiceError

from taxonomy.clients import MockTaxonomyClient
from taxon_prior.clients import KNApSAcKClient, LOTUSClient, NPASSClient
from taxon_prior.prior import MockTaxonPriorClient, build_taxon_prior


def _inchikey(smiles: str) -> str:
    return Chem.MolToInchiKey(Chem.MolFromSmiles(smiles))


CURCUMIN = "COc1cc(/C=C/C(=O)CC(=O)/C=C/c2ccc(O)c(OC)c2)ccc1O"
CARDAMONIN = "COc1cc(O)ccc1O/C=C/C(=O)c1ccc(O)cc1"
ANDROGRAPHOLIDE = (
    "C=C[C@]12C[C@@H](O)[C@H]3[C@]([C@@H]1CC[C@@]4(C(=CCC[C@]34C)C)O)(C(=O)O2)C"
)
PILOT_CLIENTS = [LOTUSClient(), KNApSAcKClient(), NPASSClient()]


def _report(inchikey: str, level: str) -> ReportedCompound:
    organism = {"species": "Boesenbergia rotunda", "genus": "Boesenbergia",
                "family": "Zingiberaceae"}[level]
    return ReportedCompound(
        compound_id=f"test-{level}", name="cardamonin", smiles=CARDAMONIN,
        inchikey=inchikey, source_organism=organism, source_database="LOTUS",
        source_reference="test", taxon_level=level,  # type: ignore[arg-type]
    )


class TestBuildTaxonPrior:
    def test_all_three_levels_queried_and_resolution_completes_ranks(self) -> None:
        """Species-only input still yields genus and family buckets, because
        the resolved lineage completes the ranks the user left blank."""
        prior = build_taxon_prior(
            "Curcuma longa", None, None,
            clients=PILOT_CLIENTS, resolver=MockTaxonomyClient(),
        )
        assert prior.resolution is not None
        assert prior.resolution.lineage["genus"] == "Curcuma"
        assert len(prior.species_compounds) >= 3  # curcuminoids + turmerones
        assert len(prior.genus_compounds) >= 1  # germacrone via resolved genus
        assert len(prior.family_compounds) >= 3  # Zingiberaceae (NPASS)

    def test_dedup_across_databases_prefers_species_level(self) -> None:
        """Curcumin: LOTUS+KNApSAcK species, NPASS family -> species wins."""
        prior = build_taxon_prior(
            "Curcuma longa", None, None,
            clients=PILOT_CLIENTS, resolver=MockTaxonomyClient(),
        )
        ik = _inchikey(CURCUMIN)
        assert prior.best_level(ik) == "species"
        assert prior.union()[ik].taxon_level == "species"
        # Full provenance keeps BOTH databases' reports, including the
        # family-level NPASS report.
        databases = {r.source_database for r in prior.provenance_for(ik)}
        assert databases == {"LOTUS", "KNApSAcK", "NPASS"}
        levels = {r.taxon_level for r in prior.provenance_for(ik)}
        assert levels == {"species", "family"}
        # Same rule for a genus-vs-species conflict: andrographolide is
        # LOTUS species AND KNApSAcK genus -> species wins.
        andrographis = build_taxon_prior(
            "Andrographis paniculata", "Andrographis", None,
            clients=PILOT_CLIENTS, resolver=MockTaxonomyClient(),
        )
        assert andrographis.best_level(_inchikey(ANDROGRAPHOLIDE)) == "species"

    def test_genus_only_skips_resolution_and_unknown_taxa_yield_empty_prior(
        self,
    ) -> None:
        prior = build_taxon_prior(
            None, "Boesenbergia", None,
            clients=PILOT_CLIENTS, resolver=MockTaxonomyClient(),
        )
        assert prior.resolution is None
        assert len(prior.genus_compounds) >= 1
        assert prior.species_compounds == {}

        unknown = build_taxon_prior(
            "Boesenbergia rotunda", "Unknownea", "Nonexistentaceae",
            clients=PILOT_CLIENTS, resolver=MockTaxonomyClient(),
        )
        assert len(unknown.family_compounds) == 0
        assert len(unknown) == (
            len(unknown.species_compounds) + len(unknown.genus_compounds)
        )


class TestClients:
    def test_snapshot_entries_are_wellformed_reports(self) -> None:
        for client in PILOT_CLIENTS:
            for level, name in (("species", "Boesenbergia rotunda"),
                                ("genus", "Curcuma"),
                                ("family", "Zingiberaceae")):
                for report in client.query_taxon(name, level):
                    assert report.source_database == client.name
                    assert report.taxon_level == level
                    assert report.inchikey == _inchikey(report.smiles)
                    assert report.source_reference

    def test_static_fallback_policy_knapsack_and_lotus(self) -> None:
        # KNApSAcK: static by design -- try_live is ignored (ADR-0054)
        assert KNApSAcKClient(try_live=True).try_live is False
        # LOTUS: a failing live path serves the snapshot, not an error
        class _FailingLOTUS(LOTUSClient):
            def _fetch_live(self, taxon_name, taxon_level):
                raise CloudServiceError("LOTUS host unreachable")

        reports = _FailingLOTUS(try_live=True).query_taxon("Curcuma longa", "species")
        assert reports, "snapshot must still serve when the live API is down"


class TestPriorModel:
    def test_level_precedence_and_report_validation(self) -> None:
        ik = _inchikey(CARDAMONIN)
        prior = TaxonPrior()
        prior.add(_report(ik, "family"))
        prior.add(_report(ik, "genus"))
        prior.add(_report(ik, "species"))
        assert prior.union()[ik].taxon_level == "species"
        assert len(prior.provenance_for(ik)) == 3
        with pytest.raises(ValueError):
            # a species-level report must name a binomial
            ReportedCompound(
                compound_id="x", name="x", smiles="CCO", inchikey="AA",
                source_organism="Boesenbergia",
                source_database="LOTUS", taxon_level="species",
            )


class TestMockTaxonPriorClient:
    def test_fixed_prior_returns_species_reports(self) -> None:
        prior = MockTaxonPriorClient().prior()
        assert prior.best_level(_inchikey(CARDAMONIN)) == "species"
        assert all(
            r.taxon_level == "species" for r in prior.species_compounds.values()
        )
