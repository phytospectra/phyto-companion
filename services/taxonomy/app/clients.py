"""Taxonomy resolution: GBIF primary, ITIS fallback (ADR-0053).

GBIF's ``/v1/species/match`` is the primary resolver: it is a documented,
stable, key-returning fuzzy matcher that answers in one request.  ITIS
(``searchByScientificName`` + full hierarchy) is the fallback for names
GBIF does not know (recently described taxa, orthographic variants GBIF
backbone rejects).  A name that NEITHER resolves raises
``InvalidInputError`` -- guessing a lineage would poison every
downstream prior, so an honest failure is the only option.

Network access lives entirely behind the ``transport`` callable each
client accepts, so resolution logic (parsing, lineage normalisation,
fallback ordering) is unit-testable offline with canned payloads.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Protocol

from phyto_shared.exceptions import CloudServiceError, InvalidInputError
from phyto_shared.taxonomy_models import (
    LINEAGE_RANKS,
    TaxonNode,
    TaxonResolution,
)

__all__ = [
    "TaxonomyClient",
    "GBIFClient",
    "ITISClient",
    "MockTaxonomyClient",
    "resolve_species",
    "get_lineage",
]

logger = logging.getLogger(__name__)

Transport = Callable[[str, dict[str, Any]], Any]


def _json_transport(url: str, params: dict[str, Any]) -> Any:
    """Default HTTP transport (cloud plane); offline tests inject their own."""
    try:
        import requests
    except ImportError as exc:
        raise CloudServiceError(
            "requests is not installed; network clients are cloud-plane only"
        ) from exc
    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()
    except CloudServiceError:
        raise
    except Exception as exc:
        raise CloudServiceError(
            "taxonomy API request failed", context={"url": url, "error": str(exc)}
        ) from exc


class TaxonomyClient(Protocol):
    """Uniform interface for a taxonomy provider."""

    name: str

    def resolve(self, name: str) -> TaxonResolution | None:
        """Resolve one scientific name, or None when the provider cannot."""
        ...

    def lineage(self, taxon_key: str) -> list[TaxonNode]:
        """Walk the hierarchy upward for one provider key, root first."""
        ...


# ---------------------------------------------------------------------------
# GBIF (primary)
# ---------------------------------------------------------------------------


class GBIFClient:
    """GBIF backbone taxonomy via ``/v1/species/match`` (ADR-0053)."""

    name = "GBIF"
    MATCH = "https://api.gbif.org/v1/species/match"
    SPECIES = "https://api.gbif.org/v1/species/{key}"

    def __init__(self, transport: Transport | None = None) -> None:
        self._transport = transport or _json_transport

    def resolve(self, name: str) -> TaxonResolution | None:
        payload = self._transport(self.MATCH, {"name": name})
        if not isinstance(payload, dict):
            raise CloudServiceError("unexpected GBIF match payload", context={"payload_type": type(payload).__name__})
        if payload.get("matchType") == "NONE" or not payload.get("usageKey"):
            return None
        lineage = {
            rank: payload[rank]
            for rank in LINEAGE_RANKS
            if payload.get(rank)
        }
        return TaxonResolution(
            query_name=name,
            canonical_name=payload.get("canonicalName") or payload["scientificName"],
            taxon_key=str(payload["usageKey"]),
            rank=payload.get("rank", "SPECIES"),
            source="GBIF",
            match_type=payload.get("matchType", "EXACT"),
            lineage=lineage,
        )

    def lineage(self, taxon_key: str) -> list[TaxonNode]:
        """Full record lookup; the rank fields carry the parents.

        ``/v1/species/{key}`` returns kingdom..species names together with
        their keys (``kingdomKey``..``genusKey``), which is exactly the
        upward walk ``get_lineage`` needs -- one request, no recursion.
        """
        payload = self._transport(self.SPECIES.format(key=taxon_key), {})
        if not isinstance(payload, dict):
            raise CloudServiceError("unexpected GBIF species payload")
        nodes: list[TaxonNode] = []
        for rank in LINEAGE_RANKS:
            name = payload.get(rank)
            if not name:
                continue
            key = payload.get(f"{rank}Key")
            nodes.append(TaxonNode(name=name, rank=rank, taxon_key=str(key) if key else None))
        return nodes


# ---------------------------------------------------------------------------
# ITIS (fallback)
# ---------------------------------------------------------------------------

# ITIS uses "Division" for the plant phylum; normalise onto LINEAGE_RANKS.
_ITIS_RANK_ALIASES = {"division": "phylum"}


class ITISClient:
    """ITIS web service fallback (ADR-0053).

    Two calls per resolve: name search for the TSN, then the full
    hierarchy for that TSN.  Only accepted names are used.
    """

    name = "ITIS"
    SEARCH = "https://www.itis.gov/ITISWebService/jsonservice/searchByScientificName"
    HIERARCHY = (
        "https://www.itis.gov/ITISWebService/jsonservice/getFullHierarchyFromTSN"
    )

    def __init__(self, transport: Transport | None = None) -> None:
        self._transport = transport or _json_transport

    def resolve(self, name: str) -> TaxonResolution | None:
        payload = self._transport(self.SEARCH, {"srchKey": name})
        matches = (payload or {}).get("scientificNames") or []
        accepted = [m for m in matches if m.get("tsn")]
        if not accepted:
            return None
        best = accepted[0]
        tsn = str(best["tsn"])
        lineage, rank = self._hierarchy(tsn)
        canonical = " ".join(
            part
            for part in (
                best.get("unitName1"),
                best.get("unitName2"),
            )
            if part
        )
        return TaxonResolution(
            query_name=name,
            canonical_name=canonical or name,
            taxon_key=tsn,
            rank=rank,
            source="ITIS",
            match_type="EXACT",
            lineage=lineage,
        )

    def lineage(self, taxon_key: str) -> list[TaxonNode]:
        lineage, _rank = self._hierarchy(taxon_key)
        return lineage

    def _hierarchy(self, tsn: str) -> tuple[dict[str, str], str]:
        payload = self._transport(self.HIERARCHY, {"searchFromTSN": tsn})
        nodes = (payload or {}).get("hierarchyList") or []
        lineage: dict[str, str] = {}
        own_rank = "SPECIES"
        for node in nodes:
            raw_rank = str(node.get("rankName", "")).strip().lower()
            rank = _ITIS_RANK_ALIASES.get(raw_rank, raw_rank)
            name = node.get("taxonName") or node.get("unit_name")
            if rank == "taxonname" or not name:
                continue
            if rank in LINEAGE_RANKS:
                lineage[rank] = name
            if str(node.get("tsn")) == str(tsn):
                own_rank = raw_rank.upper() or "SPECIES"
        return lineage, own_rank


# ---------------------------------------------------------------------------
# Resolution facade
# ---------------------------------------------------------------------------


def resolve_species(
    name: str,
    gbif: TaxonomyClient | None = None,
    itis: TaxonomyClient | None = None,
) -> TaxonResolution:
    """Resolve one scientific name: GBIF first, ITIS on a GBIF miss.

    Raises ``InvalidInputError`` when neither provider resolves the name
    (a blank prior built on a guessed lineage would be worse than no
    prior), and ``CloudServiceError`` when both providers were
    unreachable -- a network failure is not a taxonomy verdict.
    """
    cleaned = (name or "").strip()
    if not cleaned:
        raise InvalidInputError("species name is empty", context={"name": name})
    gbif = gbif or GBIFClient()
    itis = itis or ITISClient()

    network_failures: list[str] = []
    for client in (gbif, itis):
        try:
            resolution = client.resolve(cleaned)
        except CloudServiceError as exc:
            network_failures.append(client.name)
            logger.warning("taxonomy provider %s unreachable: %s", client.name, exc)
            continue
        if resolution is not None:
            if resolution.source == "ITIS":
                logger.info("GBIF had no match for %r; resolved via ITIS", cleaned)
            return resolution

    if len(network_failures) == 2:
        raise CloudServiceError(
            "no taxonomy provider reachable", context={"providers": network_failures}
        )
    raise InvalidInputError(
        "neither GBIF nor ITIS resolves this name",
        context={"name": cleaned},
    )


def get_lineage(
    taxon_key: str, source: str = "GBIF", client: TaxonomyClient | None = None
) -> list[TaxonNode]:
    """Walk a taxon's hierarchy upward via its provider (root first)."""
    if client is None:
        client = ITISClient() if source == "ITIS" else GBIFClient()
    return client.lineage(taxon_key)


# ---------------------------------------------------------------------------
# Mock (local plane and tests)
# ---------------------------------------------------------------------------


def _fixed_lineage(
    kingdom: str, phylum: str, klass: str, order: str, family: str, genus: str, species: str
) -> dict[str, str]:
    return {
        "kingdom": kingdom,
        "phylum": phylum,
        "class": klass,
        "order": order,
        "family": family,
        "genus": genus,
        "species": species,
    }


class MockTaxonomyClient:
    """Deterministic offline resolver for the pilot species (ADR-0053).

    Keys are the real GBIF usageKeys so lineage lookups are stable.
    Unknown names raise ``InvalidInputError`` exactly like the live pair,
    so tests exercise the same contract.
    """

    name = "MockGBIF"

    _FIXTURES: dict[str, TaxonResolution] = {
        "boesenbergia rotunda": TaxonResolution(
            query_name="Boesenbergia rotunda",
            canonical_name="Boesenbergia rotunda",
            taxon_key="2758480",
            rank="SPECIES",
            source="GBIF",
            match_type="EXACT",
            lineage=_fixed_lineage(
                "Plantae", "Tracheophyta", "Liliopsida",
                "Zingiberales", "Zingiberaceae", "Boesenbergia",
                "Boesenbergia rotunda",
            ),
        ),
        "andrographis paniculata": TaxonResolution(
            query_name="Andrographis paniculata",
            canonical_name="Andrographis paniculata",
            taxon_key="3173178",
            rank="SPECIES",
            source="GBIF",
            match_type="EXACT",
            lineage=_fixed_lineage(
                "Plantae", "Tracheophyta", "Magnoliopsida",
                "Lamiales", "Acanthaceae", "Andrographis",
                "Andrographis paniculata",
            ),
        ),
        "curcuma longa": TaxonResolution(
            query_name="Curcuma longa",
            canonical_name="Curcuma longa",
            taxon_key="2757624",
            rank="SPECIES",
            source="GBIF",
            match_type="EXACT",
            lineage=_fixed_lineage(
                "Plantae", "Tracheophyta", "Liliopsida",
                "Zingiberales", "Zingiberaceae", "Curcuma", "Curcuma longa",
            ),
        ),
    }

    def resolve(self, name: str) -> TaxonResolution | None:
        return self._FIXTURES.get((name or "").strip().lower())

    def lineage(self, taxon_key: str) -> list[TaxonNode]:
        for resolution in self._FIXTURES.values():
            if resolution.taxon_key == taxon_key:
                return resolution.lineage_nodes()
        raise InvalidInputError(
            "unknown taxon key in mock taxonomy",
            context={"taxon_key": taxon_key},
        )
