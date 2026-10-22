"""Natural-product taxon prior clients: LOTUS, KNApSAcK, NPASS (ADR-0054).

Pilot data-source status (probed 2026-10-22, see ADR-0054):

===============  =========================================  ====================
database         live interface                             pilot approach
===============  =========================================  ====================
LOTUS            https://api.lotus.naturalproducts.net      static snapshot
                 -- connection refused during the pilot;    (+ live path kept,
                 the documented API stays wired for the     falls back on any
                 cloud plane                                transport error)
KNApSAcK         knapsackfamily.com species-metabolite      static snapshot
                 core -- site redirects, no stable JSON     only (the core IS
                 API                                       a downloadable
                                                            species-metabolite
                                                            table)
NPASS            bcs.cmus.ac.kr:8313 web endpoints --      static snapshot
                 host unreachable during the pilot          (+ live path kept)
===============  =========================================  ====================

Every client returns ``ReportedCompound`` records carrying the source
database, organism, taxonomic level and reference, so a reranked
candidate can always answer "says who?".  Compounds are joined by
InChIKey (computed once per SMILES and cached), never by name.

Snapshots live in ``data/*.json`` and are intentionally small, curated
pilot fixtures -- replacing them with full database exports is a data
refresh, not a code change.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from phyto_shared.exceptions import CloudServiceError, InvalidInputError
from phyto_shared.taxonomy_models import ReportedCompound, TaxonLevel

__all__ = [
    "TaxonPriorClient",
    "LOTUSClient",
    "KNApSAcKClient",
    "NPASSClient",
    "SNAPSHOT_DIR",
]

logger = logging.getLogger(__name__)

SNAPSHOT_DIR = Path(__file__).parent / "data"


@lru_cache(maxsize=4096)
def _inchikey(smiles: str) -> str:
    """InChIKey for a SMILES, computed once and cached.

    Raises ``InvalidInputError`` for unparsable SMILES: a prior entry
    whose structure cannot be keyed cannot be deduplicated against
    candidates, and shipping it would silently break the join.
    """
    from rdkit import Chem

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise InvalidInputError("snapshot entry has unparsable SMILES", context={"smiles": smiles})
    return Chem.MolToInchiKey(mol)


class TaxonPriorClient(Protocol):
    """Uniform interface: compounds reported from one taxon, one level."""

    name: str

    def query_taxon(self, taxon_name: str, taxon_level: TaxonLevel) -> list[ReportedCompound]: ...


def _load_snapshot(database: str) -> list[dict]:
    path = SNAPSHOT_DIR / f"{database.lower()}_snapshot.json"
    if not path.exists():
        raise CloudServiceError(
            "taxon prior snapshot missing", context={"path": str(path)}
        )
    return json.loads(path.read_text())


def _snapshot_reports(
    database: str, taxon_name: str, taxon_level: TaxonLevel
) -> list[ReportedCompound]:
    wanted = taxon_name.strip().lower()
    reports: list[ReportedCompound] = []
    for entry in _load_snapshot(database):
        if entry["taxon_level"] != taxon_level:
            continue
        if entry["organism"].strip().lower() != wanted:
            continue
        reports.append(
            ReportedCompound(
                compound_id=entry["compound_id"],
                name=entry["name"],
                smiles=entry["smiles"],
                inchikey=_inchikey(entry["smiles"]),
                source_organism=entry["organism"],
                source_database=database,  # type: ignore[arg-type]
                source_reference=entry.get("reference", ""),
                taxon_level=entry["taxon_level"],
            )
        )
    return reports


class _SnapshotClient:
    """Shared machinery: snapshot-backed query with optional live path."""

    name: str = ""
    _live_url: str | None = None

    def query_taxon(self, taxon_name: str, taxon_level: TaxonLevel) -> list[ReportedCompound]:
        """Snapshot first; a configured live path augments, never replaces.

        The pilot snapshots are the source of truth because none of the
        three live interfaces was reachable/stable (see module docstring).
        ``try_live`` exists so the cloud plane can flip to the documented
        endpoints without touching call sites; a transport failure logs
        and falls back to the snapshot instead of failing the prior.
        """
        reports = _snapshot_reports(self.name, taxon_name, taxon_level)
        if getattr(self, "try_live", False) and self._live_url:
            try:
                reports.extend(self._fetch_live(taxon_name, taxon_level))
            except CloudServiceError as exc:
                logger.warning(
                    "%s live query failed (%s); serving snapshot only",
                    self.name, exc,
                )
        return reports

    def _fetch_live(
        self, taxon_name: str, taxon_level: TaxonLevel
    ) -> list[ReportedCompound]:
        raise CloudServiceError(f"{self.name} live path not configured")


class LOTUSClient(_SnapshotClient):
    """LOTUS: documented API base was unreachable during the pilot.

    Live path (kept for the cloud plane): the documented LOTUS organism
    query endpoint.  The base host refused connections on 2026-10-22, so
    the pilot serves the bundled dereferenced snapshot instead.
    """

    name = "LOTUS"
    _live_url = "https://api.lotus.naturalproducts.net"

    def __init__(self, try_live: bool = False) -> None:
        self.try_live = try_live

    def _fetch_live(
        self, taxon_name: str, taxon_level: TaxonLevel
    ) -> list[ReportedCompound]:
        try:
            import requests
        except ImportError as exc:
            raise CloudServiceError("requests is not installed") from exc
        try:
            response = requests.get(
                f"{self._live_url}/v1/lotus/natural_products",
                params={"organism_taxon": taxon_name, "limit": 1000},
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise CloudServiceError(
                "LOTUS API request failed", context={"error": str(exc)}
            ) from exc
        compounds = payload if isinstance(payload, list) else payload.get("results", [])
        reports: list[ReportedCompound] = []
        for item in compounds:
            smiles = item.get("smiles") or item.get("canonical_smiles")
            organism = item.get("organism_taxon") or taxon_name
            if not smiles:
                continue
            reports.append(
                ReportedCompound(
                    compound_id=str(item.get("id", item.get("lotus_id", "lotus-unknown"))),
                    name=item.get("pref_name") or item.get("name") or "unnamed",
                    smiles=smiles,
                    inchikey=_inchikey(smiles),
                    source_organism=organism,
                    source_database="LOTUS",
                    source_reference=str(item.get("reference", "")),
                    taxon_level=taxon_level,
                )
            )
        return reports


class KNApSAcKClient(_SnapshotClient):
    """KNApSAcK species-metabolite core: static download only.

    The KNApSAcK core is distributed as a species-metabolite table; the
    web site redirects and exposes no stable JSON query API, so this
    client is snapshot-backed by design (ADR-0054).  Refreshing means
    re-downloading the core table into ``data/``.
    """

    name = "KNApSAcK"
    _live_url = None

    def __init__(self, try_live: bool = False) -> None:
        if try_live:
            logger.warning(
                "KNApSAcK has no stable live API; ignoring try_live"
            )
        self.try_live = False


class NPASSClient(_SnapshotClient):
    """NPASS compounds-by-species: web endpoints unreachable in the pilot.

    The NPASS web endpoints (bcs.cmus.ac.kr:8313/NPASS2) did not answer
    during the pilot, so this client serves the bundled static export.
    """

    name = "NPASS"
    _live_url = "http://bcs.cmus.ac.kr:8313/NPASS2/"

    def __init__(self, try_live: bool = False) -> None:
        self.try_live = try_live
