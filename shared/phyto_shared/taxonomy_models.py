"""Taxonomy and taxon-prior domain models (ADR-0052, ADR-0053, ADR-0054).

Design notes
------------
* This module deliberately does NOT import ``models.StrictModel``: the
  shared ``models.CandidateRank`` references ``ReportedCompound`` in its
  ``taxon_prior_provenance`` field, so the dependency arrow must point
  ``models -> taxonomy_models`` and never the other way.  The base config
  below is identical to ``StrictModel`` (``extra="forbid"``, mutable); the
  deviation is documented here rather than silent.
* ``ReportedCompound`` carries an ``inchikey`` because the prior
  deduplicates compounds across databases by InChIKey, not by name:
  the same metabolite appears under different names in LOTUS, KNApSAcK
  and NPASS, and structure is the only stable join key.
* ``taxon_level`` records WHERE the report came from taxonomically
  ("this exact species", "this genus", "this family").  Provenance is
  per-report, never merged away: a reranked candidate must be able to
  say which database claimed it and at which taxonomic depth.
"""

from __future__ import annotations

from typing import Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "TaxonLevel",
    "LINEAGE_RANKS",
    "TaxonNode",
    "TaxonResolution",
    "ReportedCompound",
    "TaxonPrior",
]


class _TaxonomyModel(BaseModel):
    """Same contract as ``models.StrictModel`` (see module docstring)."""

    model_config = ConfigDict(frozen=False, extra="forbid")


TaxonLevel = Literal["species", "genus", "family"]

#: The seven canonical lineage ranks, root first.  GBIF and ITIS keys are
#: normalised onto this order so consumers can index by rank name.
LINEAGE_RANKS: tuple[str, ...] = (
    "kingdom",
    "phylum",
    "class",
    "order",
    "family",
    "genus",
    "species",
)

# Level precedence for deduplication: species-level provenance outranks
# genus, which outranks family (ADR-0052).  Lower value = stronger.
_LEVEL_PRECEDENCE: dict[str, int] = {"species": 0, "genus": 1, "family": 2}


class TaxonNode(_TaxonomyModel):
    """One level of a taxonomic hierarchy."""

    name: str = Field(..., min_length=1)
    rank: str = Field(..., description="lowercase rank: kingdom..species")
    taxon_key: str | None = Field(
        default=None, description="Provider key (GBIF usageKey / ITIS TSN)"
    )


class TaxonResolution(_TaxonomyModel):
    """The resolved identity of a user-supplied species name.

    ``lineage`` maps each of ``LINEAGE_RANKS`` that the provider supplied
    to its name; ranks the provider omitted are absent, never invented.
    """

    query_name: str = Field(..., min_length=1)
    canonical_name: str = Field(..., min_length=1)
    taxon_key: str = Field(..., min_length=1)
    rank: str = Field(..., description="Rank of the matched usage, e.g. 'SPECIES'")
    source: Literal["GBIF", "ITIS"]
    match_type: str = Field(
        default="EXACT", description="EXACT / FUZZY for GBIF; EXACT for ITIS"
    )
    lineage: dict[str, str] = Field(default_factory=dict)

    def lineage_nodes(self) -> list[TaxonNode]:
        """The lineage as ordered nodes, root first (kingdom -> species)."""
        return [
            TaxonNode(name=self.lineage[rank], rank=rank)
            for rank in LINEAGE_RANKS
            if rank in self.lineage
        ]


class ReportedCompound(_TaxonomyModel):
    """One compound reported from one taxon, in one source database.

    This is the provenance unit of the whole taxon-prior feature: when a
    candidate is boosted, this record says who reported it, from which
    organism, at which taxonomic depth, and under which reference.
    """

    compound_id: str = Field(..., min_length=1, description="Id in the source database")
    name: str
    smiles: str = Field(..., min_length=1)
    inchikey: str = Field(
        ..., min_length=1, description="Structure join key; dedup never uses names"
    )
    source_organism: str = Field(..., min_length=1)
    source_database: Literal["LOTUS", "KNApSAcK", "NPASS"]
    source_reference: str = Field(
        default="",
        description="Reference (DOI/URL/database accession) backing the report",
    )
    taxon_level: TaxonLevel

    @model_validator(mode="after")
    def _organism_matches_level(self) -> "ReportedCompound":
        # A species-level report must name a binomial (two words); genus and
        # family reports name one word.  Loose on purpose: providers differ
        # in author annotations, so only the word count is checked.
        words = self.source_organism.split()
        if self.taxon_level == "species" and len(words) < 2:
            raise ValueError(
                f"species-level report must name a binomial, got {self.source_organism!r}"
            )
        if self.taxon_level in ("genus", "family") and len(words) != 1:
            raise ValueError(
                f"{self.taxon_level}-level report must name a single taxon, "
                f"got {self.source_organism!r}"
            )
        return self


class TaxonPrior(_TaxonomyModel):
    """Compounds reported from the sample's taxon, bucketed by level.

    Each map is keyed by InChIKey and holds the BEST report at that level
    (species-level provenance preferred over genus over family when the
    same InChIKey was reported at several levels, ADR-0052).  ``union``
    merges the three levels with the same precedence rule, while
    ``all_reports`` keeps EVERY report (all databases, all levels) so
    reranked candidates can carry full provenance, not a summary.
    """

    species_compounds: dict[str, ReportedCompound] = Field(default_factory=dict)
    genus_compounds: dict[str, ReportedCompound] = Field(default_factory=dict)
    family_compounds: dict[str, ReportedCompound] = Field(default_factory=dict)
    all_reports: dict[str, list[ReportedCompound]] = Field(
        default_factory=dict,
        description="Every report per InChIKey across databases and levels; "
        "the per-level buckets above hold only the best report per key",
    )
    resolution: TaxonResolution | None = Field(
        default=None, description="Taxonomy resolution used to build the prior"
    )

    @staticmethod
    def _prefer(
        current: ReportedCompound | None, candidate: ReportedCompound
    ) -> ReportedCompound:
        if current is None:
            return candidate
        if _LEVEL_PRECEDENCE[candidate.taxon_level] < _LEVEL_PRECEDENCE[
            current.taxon_level
        ]:
            return candidate
        return current

    def add(self, compound: ReportedCompound) -> None:
        """Add one report: full record kept, best-per-level bucket updated.

        Within one level the first-added report wins ties (client order
        LOTUS -> KNApSAcK -> NPASS, ADR-0054); ``all_reports`` keeps the
        others regardless.
        """
        bucket = {
            "species": self.species_compounds,
            "genus": self.genus_compounds,
            "family": self.family_compounds,
        }[compound.taxon_level]
        bucket[compound.inchikey] = self._prefer(
            bucket.get(compound.inchikey), compound
        )
        self.all_reports.setdefault(compound.inchikey, []).append(compound)

    def union(self) -> dict[str, ReportedCompound]:
        """All levels merged; for each InChIKey the strongest-level report.

        Species beats genus beats family regardless of which database
        supplied the report -- the taxonomic depth of the evidence is the
        ordering, not the database's reputation.
        """
        merged: dict[str, ReportedCompound] = {}
        for bucket in (
            self.family_compounds,
            self.genus_compounds,
            self.species_compounds,
        ):
            # Insert weakest level first so stronger levels overwrite.
            for key, compound in bucket.items():
                merged[key] = self._prefer(merged.get(key), compound)
        return merged

    def best_level(self, inchikey: str) -> TaxonLevel | None:
        """The strongest taxonomic level at which this InChIKey is reported."""
        for level in ("species", "genus", "family"):
            bucket = {
                "species": self.species_compounds,
                "genus": self.genus_compounds,
                "family": self.family_compounds,
            }[level]
            if inchikey in bucket:
                return level  # type: ignore[return-value]
        return None

    def provenance_for(self, inchikey: str) -> list[ReportedCompound]:
        """Every report of this InChIKey: all databases, all levels."""
        return list(self.all_reports.get(inchikey, []))

    def __len__(self) -> int:
        return len(self.union())


def dedupe_reports(reports: Iterable[ReportedCompound]) -> dict[str, ReportedCompound]:
    """Deduplicate reports by InChIKey, preferring species-level provenance."""
    merged: dict[str, ReportedCompound] = {}
    for compound in reports:
        merged[compound.inchikey] = TaxonPrior._prefer(
            merged.get(compound.inchikey), compound
        )
    return merged
