"""Build a compound prior for a sample's taxon (ADR-0052, ADR-0054).

``build_taxon_prior`` is the only entry point other services need: give
it the species/genus/family the user supplied (any subset) and it
resolves the species through the taxonomy service, queries LOTUS,
KNApSAcK and NPASS at species, genus and family level, and returns a
``TaxonPrior`` whose per-level buckets already resolved duplicate
InChIKeys in favour of species-level provenance.

An unknown-but-well-formed taxon is NOT an error: plenty of real
organisms have no entry in any of the three databases, and an empty
prior must flow through the pipeline as "no taxon evidence", not kill
the run.  An unresolvable species NAME (neither GBIF nor ITIS knows it)
IS an error, because the lineage the rest of the prior keys on would be
a guess.
"""

from __future__ import annotations

import logging
from typing import Iterable

from phyto_shared.taxonomy_models import (
    ReportedCompound,
    TaxonLevel,
    TaxonPrior,
    TaxonResolution,
)

from .clients import KNApSAcKClient, LOTUSClient, NPASSClient, TaxonPriorClient

__all__ = ["build_taxon_prior", "MockTaxonPriorClient", "default_clients"]

logger = logging.getLogger(__name__)


def default_clients() -> list[TaxonPriorClient]:
    """The three pilot clients (snapshot-backed; ADR-0054)."""
    return [LOTUSClient(), KNApSAcKClient(), NPASSClient()]


def build_taxon_prior(
    species_name: str | None,
    genus: str | None,
    family: str | None,
    clients: Iterable[TaxonPriorClient] | None = None,
    resolver=None,
) -> TaxonPrior:
    """Query all three databases for compounds reported from this taxon.

    ``resolver`` (a taxonomy-service client) fills genus/family from the
    resolved lineage when the user supplied only a species, and vice
    versa the user's explicit genus/family are used verbatim.  Every
    client failure is contained per level: a prior from two databases
    with the third logged as missing beats no prior at all.
    """
    clients = list(clients) if clients is not None else default_clients()
    resolution: TaxonResolution | None = None
    species = (species_name or "").strip() or None
    genus = (genus or "").strip() or None
    family = (family or "").strip() or None

    if species and resolver is not None:
        resolution = resolver.resolve(species)
        if resolution is not None:
            # The resolved lineage completes whatever ranks the user left
            # blank; explicit user input always wins over the lineage.
            genus = genus or resolution.lineage.get("genus")
            family = family or resolution.lineage.get("family")

    prior = TaxonPrior(resolution=resolution)
    for level, name in (("species", species), ("genus", genus), ("family", family)):
        if not name:
            continue
        for client in clients:
            try:
                reports = client.query_taxon(name, level)  # type: ignore[arg-type]
            except Exception as exc:  # per-client containment (see docstring)
                logger.warning(
                    "taxon prior client %s failed for %s %r: %s",
                    client.name, level, name, exc,
                )
                continue
            for report in reports:
                prior.add(report)
    logger.info(
        "taxon prior built: %d species / %d genus / %d family compounds",
        len(prior.species_compounds), len(prior.genus_compounds),
        len(prior.family_compounds),
    )
    return prior


class MockTaxonPriorClient:
    """Deterministic offline prior for tests: a small fixed Boesenbergia set.

    Implements the ``TaxonPriorClient`` protocol (``query_taxon``) plus a
    convenience ``prior()`` that returns the assembled ``TaxonPrior``.
    """

    name = "MockLOTUS"

    _FIXTURE_SMILES: dict[str, str] = {
        "panduratin A": "CC(C)=CCC[C@@H](C)C(=O)c1c(O)cc(O)c(C)c1",
        "pinocembrin": "O=c1cc(-c2ccccc2O)oc2cc(O)ccc12",
        "cardamonin": "COc1cc(O)ccc1O/C=C/C(=O)c1ccc(O)cc1",
    }

    def query_taxon(self, taxon_name: str, taxon_level: TaxonLevel) -> list[ReportedCompound]:
        wanted = taxon_name.strip().lower()
        species_reports: list[ReportedCompound] = []
        if wanted == "boesenbergia rotunda":
            species_reports = [
                ReportedCompound(
                    compound_id=f"MOCK-{index}",
                    name=name,
                    smiles=smiles,
                    inchikey=_mock_inchikey(smiles),
                    source_organism="Boesenbergia rotunda",
                    source_database="LOTUS",
                    source_reference="mock fixture",
                    taxon_level="species",
                )
                for index, (name, smiles) in enumerate(
                    self._FIXTURE_SMILES.items(), start=1
                )
            ]
        elif wanted == "boesenbergia" and taxon_level == "genus":
            smiles = self._FIXTURE_SMILES["cardamonin"]
            species_reports = [
                ReportedCompound(
                    compound_id="MOCK-G1",
                    name="cardamonin",
                    smiles=smiles,
                    inchikey=_mock_inchikey(smiles),
                    source_organism="Boesenbergia",
                    source_database="KNApSAcK",
                    source_reference="mock fixture",
                    taxon_level="genus",
                )
            ]
        return [r for r in species_reports if r.taxon_level == taxon_level]

    def prior(self) -> TaxonPrior:
        prior = TaxonPrior()
        for report in self.query_taxon("Boesenbergia rotunda", "species"):
            prior.add(report)
        return prior


def _mock_inchikey(smiles: str) -> str:
    from rdkit import Chem

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:  # pragma: no cover - fixtures are validated in tests
        raise ValueError(f"mock fixture SMILES unparsable: {smiles}")
    return Chem.MolToInchiKey(mol)
