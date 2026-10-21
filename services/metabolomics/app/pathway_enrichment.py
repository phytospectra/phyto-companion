"""Pathway enrichment (KEGG / Reactome) with explicit background sets.

Honesty rules (Step 6 principle 2, guarantee 6): every result records
the background set size and the exact statistical test used.  Local
plane uses a mock pathway database; the real clients hit the KEGG and
Reactome REST APIs (free, documented at rest.kegg.jp and
reactome.org/ContentService).
"""

from __future__ import annotations

import os
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field
from scipy.stats import false_discovery_control

from phyto_shared.exceptions import CloudServiceError, InvalidInputError

__all__ = [
    "PathwayHit",
    "EnrichmentResult",
    "PathwayDatabase",
    "KEGGClient",
    "ReactomeClient",
    "MockPathwayDatabase",
    "get_pathway_database",
    "enrich_kegg",
    "enrich_reactome",
    "map_to_compounds",
    "ENRICHMENT_TEST_DESCRIPTION",
]

ENRICHMENT_TEST_DESCRIPTION = (
    "hypergeometric over-representation test (one-sided), "
    "Benjamini-Hochberg FDR correction"
)

# Mock pathway members use KEGG compound ids; feature ids are m/z-based
# (FT1..FT7), so the mock also provides the feature -> compound mapping.
_MOCK_FEATURE_TO_COMPOUND = {
    "FT1": "C00031",  # internal standard -> glucose
    "FT2": "C00137",
    "FT3": "C00311",
    "FT4": "C00259",  # myricetin
    "FT5": "C00389",  # quercetin -- the differentially abundant one
    "FT6": "C99999",  # noise feature, maps nowhere
    "FT7": "C00042",
}
_MOCK_PATHWAYS: dict[str, tuple[str, set[str]]] = {
    "map00061": ("Fatty acid biosynthesis (mock)", {"C00031", "C00022", "C00249"}),
    "map00945": ("Flavonoid biosynthesis (mock)", {"C00259", "C00389", "C00311"}),
    "map01100": ("Metabolic pathways (mock, large)", {
        "C00031", "C00022", "C00042", "C00137", "C00259", "C00389", "C00311",
        "C00068", "C00073", "C00079", "C00103", "C00148",
    }),
    "map00940": ("Phenylpropanoid biosynthesis (mock)", {"C00259", "C00311", "C00073"}),
}


class PathwayHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pathway_id: str
    pathway_name: str
    p_value: float
    fdr: float
    overlap_size: int
    background_size: int
    overlap_features: list[str]
    source: str


class EnrichmentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pathways: list[PathwayHit]
    background_size: int
    query_size: int
    test: str = ENRICHMENT_TEST_DESCRIPTION
    source: str
    compound_map: dict[str, list[str]] = Field(default_factory=dict)


class PathwayDatabase(Protocol):
    source: str

    def pathway_members(self) -> dict[str, tuple[str, set[str]]]: ...


class MockPathwayDatabase:
    source = "mock"

    def pathway_members(self) -> dict[str, tuple[str, set[str]]]:
        return {k: (name, set(members)) for k, (name, members) in _MOCK_PATHWAYS.items()}

    def feature_to_compound(self, feature_id: str) -> str | None:
        return _MOCK_FEATURE_TO_COMPOUND.get(feature_id)


class _RestPathwayDatabase:
    """Base for real REST clients; transport injectable for tests."""

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport


class KEGGClient(_RestPathwayDatabase):
    source = "kegg"

    def pathway_members(
        self, organism: str = "hsa"
    ) -> dict[str, tuple[str, set[str]]]:
        # GET http://rest.kegg.jp/link/<organism>/pathway -> compound paths
        try:
            with httpx.Client(
                base_url="http://rest.kegg.jp", timeout=30.0, transport=self._transport
            ) as client:
                response = client.get(f"/link/{organism}/pathway")
                response.raise_for_status()
        except Exception as exc:
            raise CloudServiceError(
                "KEGG REST unavailable", context={"error": str(exc)}
            ) from exc
        pathways: dict[str, tuple[str, set[str]]] = {}
        for line in response.text.splitlines():
            parts = line.split("\t")
            if len(parts) == 2 and parts[1].startswith("cpd:"):
                pathways.setdefault(
                    parts[0].replace("path:", ""),
                    ("", set()),
                )[1].add(parts[1].replace("cpd:", ""))
        return pathways


class ReactomeClient(_RestPathwayDatabase):
    source = "reactome"

    def pathway_members(self) -> dict[str, tuple[str, set[str]]]:
        try:
            with httpx.Client(
                base_url="https://reactome.org/ContentService",
                timeout=30.0,
                transport=self._transport,
            ) as client:
                response = client.get(
                    "/data/participants/Pathway", headers={"Accept": "text/csv"}
                )
                response.raise_for_status()
        except Exception as exc:
            raise CloudServiceError(
                "Reactome REST unavailable", context={"error": str(exc)}
            ) from exc
        pathways: dict[str, tuple[str, set[str]]] = {}
        for line in response.text.splitlines()[1:]:
            parts = line.split(",")
            if len(parts) >= 3:
                pathways.setdefault(parts[0], (parts[1], set()))[1].add(parts[2])
        return pathways


def get_pathway_database(
    source: str = "kegg", transport: httpx.BaseTransport | None = None
) -> PathwayDatabase:
    if os.environ.get("PHYTO_DRY_RUN") == "1" or os.environ.get("PHYTO_ENV") != "cloud":
        return MockPathwayDatabase()
    return KEGGClient(transport) if source == "kegg" else ReactomeClient(transport)


def _enrich(
    feature_ids: list[str],
    database: PathwayDatabase,
    background: set[str] | None,
    feature_to_compound: dict[str, str] | None,
) -> EnrichmentResult:
    from scipy.stats import hypergeom

    members = database.pathway_members()
    mapper = feature_to_compound or {}
    if feature_to_compound is None and isinstance(database, MockPathwayDatabase):
        mapper = _MOCK_FEATURE_TO_COMPOUND
    query = {mapper.get(f, f) for f in feature_ids}
    query.discard(None)

    if background is None:
        background = set().union(*(compounds for _, compounds in members.values()))
    if not query:
        raise InvalidInputError("no query features after mapping", context={})
    background_size = len(background)
    query_in_background = query & background
    if not query_in_background:
        return EnrichmentResult(
            pathways=[], background_size=background_size,
            query_size=len(query), source=database.source,
        )

    hits: list[PathwayHit] = []
    for pathway_id, (name, compounds) in sorted(members.items()):
        pathway_background = compounds & background
        overlap = pathway_background & query_in_background
        if not overlap:
            continue
        n_pathway = len(pathway_background)
        # P(X >= k) under hypergeometric(M=background, n=pathway, N=query)
        p_value = float(
            hypergeom.sf(len(overlap) - 1, background_size, n_pathway, len(query_in_background))
        )
        hits.append(
            PathwayHit(
                pathway_id=pathway_id,
                pathway_name=name,
                p_value=p_value,
                fdr=1.0,
                overlap_size=len(overlap),
                background_size=background_size,
                overlap_features=sorted(overlap),
                source=database.source,
            )
        )
    if hits:
        adjusted = false_discovery_control([h.p_value for h in hits], method="bh")
        for hit, q in zip(hits, adjusted):
            hit.fdr = float(q)
    hits.sort(key=lambda h: h.p_value)
    return EnrichmentResult(
        pathways=hits,
        background_size=background_size,
        query_size=len(query_in_background),
        source=database.source,
    )


def enrich_kegg(
    feature_ids: list[str],
    organism: str = "hsa",
    background: list[str] | None = None,
    database: PathwayDatabase | None = None,
) -> EnrichmentResult:
    database = database or get_pathway_database("kegg")
    return _enrich(feature_ids, database, set(background) if background else None, None)


def enrich_reactome(
    feature_ids: list[str],
    background: list[str] | None = None,
    database: PathwayDatabase | None = None,
) -> EnrichmentResult:
    database = database or get_pathway_database("reactome")
    return _enrich(feature_ids, database, set(background) if background else None, None)


def map_to_compounds(
    enrichment: EnrichmentResult, compound_map: dict[str, str]
) -> EnrichmentResult:
    """Attach compound InChIKeys to each pathway.

    ``compound_map`` is feature_id -> InChIKey.  Overlap sets hold the
    compound ids used for enrichment (KEGG C-numbers in the mock), so the
    mapping is inverted through the mock feature->compound table to
    translate back to features before applying the caller's keys.
    """
    feature_to_compound = _MOCK_FEATURE_TO_COMPOUND
    compound_to_feature = {
        compound: feature for feature, compound in feature_to_compound.items()
    }
    per_pathway: dict[str, list[str]] = {}
    for hit in enrichment.pathways:
        keys: list[str] = []
        for overlap_id in hit.overlap_features:
            feature_id = (
                overlap_id
                if overlap_id in compound_map
                else compound_to_feature.get(overlap_id)
            )
            if feature_id is not None and feature_id in compound_map:
                keys.append(compound_map[feature_id])
        per_pathway[hit.pathway_id] = keys
    return enrichment.model_copy(update={"compound_map": per_pathway})
