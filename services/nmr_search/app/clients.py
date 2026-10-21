"""NMR structure-search clients: NP-MRD, BMRB, nmrshiftdb2 (ADR-0031).

Every client returns ``NMRSpectrumHit`` objects carrying the database name and
its licence, because licence terms differ per database and the platform serves
both an academic free tier and a commercial Pro tier (ADR-0026):

* NP-MRD      CC-BY-NC   -- no commercial use
* BMRB        CC-BY-4.0  -- commercial use permitted with attribution
* nmrshiftdb2 CC-BY-SA   -- share-alike; commercial use permitted with care

Matching uses a greedy nearest-neighbour assignment on (1H, 13C) distances.
Greedy rather than optimal: within these ppm tolerances ambiguity is rare, and
a deterministic, explainable assignment is worth more than a marginally better
cost.  Unmatched query peaks count against ``coverage`` so a record matching
two of fifteen peaks cannot masquerade as an excellent hit.

Network access lives entirely in ``_post_json`` / ``_get_json``.  Every live
client's request shape is written against the provider's published API, but
these calls have NOT been exercised against the live services from this
machine; the mock client is what the test suite and the local plane use.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any, Protocol

from phyto_shared.exceptions import CloudServiceError
from phyto_shared.nmr_search_models import (
    CompoundShiftResult,
    LicenseInfo,
    NMRSpectrumHit,
    NMRSpectrumMatch,
)

__all__ = [
    "NMRSearchClient",
    "LICENSES",
    "assign_peaks",
    "rank_hits",
    "NPMRDClient",
    "BMRBClient",
    "NMRShiftDB2Client",
    "MockNMRSearchClient",
]


LICENSES: dict[str, LicenseInfo] = {
    "NP-MRD": LicenseInfo(
        name="CC-BY-NC",
        commercial_use=False,
        url="https://np-mrd.org",
        attribution="NP-MRD (Natural Products Magnetic Resonance Database)",
    ),
    "BMRB": LicenseInfo(
        name="CC-BY-4.0",
        commercial_use=True,
        url="https://bmrb.io",
        attribution="Biological Magnetic Resonance Data Bank (BMRB)",
    ),
    "nmrshiftdb2": LicenseInfo(
        name="CC-BY-SA",
        commercial_use=True,
        url="https://nmrshiftdb.nmr.uni-koeln.de",
        attribution="nmrshiftdb2 (University of Cologne)",
    ),
    "local-fixture": LicenseInfo(
        name="CC-BY-NC",
        commercial_use=False,
        url=None,
        attribution="NP-MRD-derived fixture (tests/fixtures/real_npmrd)",
    ),
}


class NMRSearchClient(Protocol):
    """Uniform interface for spectral databases."""

    name: str

    def search_by_peaks(
        self,
        peaks: list[tuple[float, float]],
        h_tolerance: float,
        c_tolerance: float,
        max_results: int,
    ) -> list[NMRSpectrumHit]: ...

    def get_compound_shifts(
        self, name: str | None, database_id: str | None
    ) -> CompoundShiftResult | None: ...


def assign_peaks(
    query: list[tuple[float, float]],
    reference: list[tuple[float, float]],
    h_tolerance: float,
    c_tolerance: float,
) -> NMRSpectrumMatch:
    """Greedily assign query peaks to reference peaks within tolerance."""
    remaining = list(reference)
    matched = 0
    total_distance = 0.0

    for h_obs, c_obs in query:
        best_index = None
        best_score = None
        for index, (h_ref, c_ref) in enumerate(remaining):
            dh = abs(h_obs - h_ref)
            dc = abs(c_obs - c_ref)
            if dh <= h_tolerance and dc <= c_tolerance:
                # Normalise both axes by their tolerance so the two ppm scales
                # contribute comparably to the assignment cost.
                score = (dh / h_tolerance) ** 2 + (dc / c_tolerance) ** 2
                if best_score is None or score < best_score:
                    best_score = score
                    best_index = index
        if best_index is not None:
            h_ref, c_ref = remaining.pop(best_index)
            matched += 1
            total_distance += abs(h_obs - h_ref) + abs(c_obs - c_ref) / 100.0

    coverage = matched / len(query) if query else 0.0
    distance = (total_distance / matched) if matched else 999.0
    return NMRSpectrumMatch(
        matched_peaks=matched,
        query_peaks=len(query),
        distance=round(distance, 6),
        coverage=round(coverage, 4),
    )


def rank_hits(hits: list[NMRSpectrumHit], max_results: int) -> list[NMRSpectrumHit]:
    """Sort by coverage (desc) then distance (asc); drop zero-coverage hits."""
    usable = [h for h in hits if h.match.matched_peaks > 0]
    usable.sort(key=lambda h: (-h.match.coverage, h.match.distance, h.database_id))
    return usable[:max_results]


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> Any:
    """POST JSON and decode the response.  ``requests`` is imported lazily."""
    try:
        import requests
    except ImportError as exc:
        raise CloudServiceError(
            "requests is not installed; network clients are cloud-plane only"
        ) from exc
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=60)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        raise CloudServiceError(
            "NMR database request failed", context={"url": url, "error": str(exc)}
        ) from exc


def _get_json(url: str, params: dict[str, Any] | None = None) -> Any:
    try:
        import requests
    except ImportError as exc:
        raise CloudServiceError(
            "requests is not installed; network clients are cloud-plane only"
        ) from exc
    try:
        response = requests.get(url, params=params, timeout=60)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        raise CloudServiceError(
            "NMR database request failed", context={"url": url, "error": str(exc)}
        ) from exc


def _hit_from_shift_table(
    *,
    database: str,
    database_id: str,
    name: str,
    reference: list[tuple[float, float]],
    query: list[tuple[float, float]],
    h_tolerance: float,
    c_tolerance: float,
    smiles: str | None = None,
    formula: str | None = None,
    solvent: str | None = None,
    url: str | None = None,
) -> NMRSpectrumHit:
    return NMRSpectrumHit(
        database=database,  # type: ignore[arg-type]
        database_id=database_id,
        name=name,
        smiles=smiles,
        formula=formula,
        solvent=solvent,
        match=assign_peaks(query, reference, h_tolerance, c_tolerance),
        license=LICENSES[database],
        url=url,
    )


# ---------------------------------------------------------------------------
# NP-MRD
# ---------------------------------------------------------------------------


class NPMRDClient:
    """NP-MRD (Natural Products Magnetic Resonance Database) client.

    NP-MRD exposes a public REST API, but its structure-search payload has no
    stable, documented peak-list endpoint that can be relied on from this
    codebase.  The client therefore performs a free-text lookup for candidate
    records and matches the returned shift tables locally -- the server does
    the retrieval, and the ppm tolerances stay under this service's control,
    where they are reproducible and testable.

    NP-MRD data is CC-BY-NC: hits are marked non-commercial by ``LICENSES``
    and must not be served to the commercial tier (ADR-0026).
    """

    name = "NP-MRD"
    BASE = "https://np-mrd.org/api/v1"

    def search_by_peaks(
        self,
        peaks: list[tuple[float, float]],
        h_tolerance: float,
        c_tolerance: float,
        max_results: int,
    ) -> list[NMRSpectrumHit]:
        # Anchor the retrieval on the most downfield 1H shift: it is the most
        # discriminative single feature in a flavonoid/terpenoid mixture.
        anchor = max(peaks, key=lambda p: p[0])
        payload = _get_json(
            f"{self.BASE}/spectra/search",
            params={
                "nmr_solvent": None,
                "shift_1h_from": round(anchor[0] - h_tolerance, 3),
                "shift_1h_to": round(anchor[0] + h_tolerance, 3),
                "limit": max_results * 5,
            },
        )
        hits: list[NMRSpectrumHit] = []
        for record in payload.get("results", []) or []:
            reference = _reference_pairs(record.get("shifts") or [])
            if not reference:
                continue
            hits.append(
                _hit_from_shift_table(
                    database="NP-MRD",
                    database_id=str(record.get("id") or record.get("np_mrd_id") or "unknown"),
                    name=str(record.get("name") or "unnamed record"),
                    reference=reference,
                    query=peaks,
                    h_tolerance=h_tolerance,
                    c_tolerance=c_tolerance,
                    smiles=record.get("smiles"),
                    formula=record.get("molecular_formula"),
                    solvent=record.get("nmr_solvent"),
                    url=record.get("url"),
                )
            )
        return rank_hits(hits, max_results)

    def get_compound_shifts(
        self, name: str | None, database_id: str | None
    ) -> CompoundShiftResult | None:
        params: dict[str, Any] = {"limit": 1}
        if database_id:
            params["id"] = database_id
        elif name:
            params["name"] = name
        else:
            return None
        payload = _get_json(f"{self.BASE}/spectra/search", params=params)
        results = payload.get("results") or []
        if not results:
            return None
        record = results[0]
        return CompoundShiftResult(
            database="NP-MRD",
            database_id=str(record.get("id") or database_id or "unknown"),
            name=str(record.get("name") or name or "unnamed record"),
            shifts=_labelled_shifts(record.get("shifts") or []),
            solvent=record.get("nmr_solvent"),
            license=LICENSES["NP-MRD"],
        )


# ---------------------------------------------------------------------------
# BMRB
# ---------------------------------------------------------------------------


class BMRBClient:
    """BMRB client using the ``/search/multiple_shift_search`` endpoint.

    The endpoint accepts a list of observed chemical shifts and returns
    entries whose assigned shifts are within the requested tolerances.  Two
    searches are issued -- one per nucleus -- rather than one combined call,
    because BMRB returns per-entry shift lists that must be intersected
    locally to confirm a *pair* matched rather than just one of its elements.
    """

    name = "BMRB"
    BASE = "https://api.bmrb.io/v2"

    def search_by_peaks(
        self,
        peaks: list[tuple[float, float]],
        h_tolerance: float,
        c_tolerance: float,
        max_results: int,
    ) -> list[NMRSpectrumHit]:
        h_hits = self._multiple_shift_search(
            [p[0] for p in peaks], "1H", h_tolerance, max_results * 10
        )
        c_hits = self._multiple_shift_search(
            [p[1] for p in peaks], "13C", c_tolerance, max_results * 10
        )
        # An entry must appear in BOTH result sets: a proton-only or
        # carbon-only match cannot confirm an HSQC cross-peak.
        common = set(h_hits) & set(c_hits)
        hits: list[NMRSpectrumHit] = []
        for entry_id in sorted(common):
            record = h_hits[entry_id]
            reference = _reference_pairs(record.get("shifts") or [])
            if not reference:
                continue
            hits.append(
                _hit_from_shift_table(
                    database="BMRB",
                    database_id=entry_id,
                    name=str(record.get("name") or entry_id),
                    reference=reference,
                    query=peaks,
                    h_tolerance=h_tolerance,
                    c_tolerance=c_tolerance,
                    smiles=record.get("smiles"),
                    formula=record.get("formula"),
                    solvent=record.get("solvent"),
                    url=f"https://bmrb.io/data_library/summary/index.php?bmrbId={entry_id}",
                )
            )
        return rank_hits(hits, max_results)

    def _multiple_shift_search(
        self,
        shifts: list[float],
        nucleus: str,
        tolerance: float,
        limit: int,
    ) -> dict[str, dict[str, Any]]:
        payload = _post_json(
            f"{self.BASE}/search/multiple_shift_search",
            {
                "shifts": [{"nucleus": nucleus, "shift": round(s, 4)} for s in shifts],
                "tolerance": tolerance,
                "limit": limit,
            },
        )
        results: dict[str, dict[str, Any]] = {}
        for record in payload.get("results", payload if isinstance(payload, list) else []):
            entry_id = str(record.get("entry_id") or record.get("id") or "")
            if entry_id:
                results[entry_id] = record
        return results

    def get_compound_shifts(
        self, name: str | None, database_id: str | None
    ) -> CompoundShiftResult | None:
        if database_id:
            payload = _get_json(f"{self.BASE}/entry/{database_id}")
        elif name:
            payload = _get_json(f"{self.BASE}/search/entry", params={"name": name})
        else:
            return None
        record = payload[0] if isinstance(payload, list) and payload else payload
        if not isinstance(record, dict) or not record:
            return None
        entry_id = str(record.get("entry_id") or database_id or "unknown")
        return CompoundShiftResult(
            database="BMRB",
            database_id=entry_id,
            name=str(record.get("name") or name or entry_id),
            shifts=_labelled_shifts(record.get("shifts") or []),
            solvent=record.get("solvent"),
            license=LICENSES["BMRB"],
        )


# ---------------------------------------------------------------------------
# nmrshiftdb2
# ---------------------------------------------------------------------------


class NMRShiftDB2Client:
    """nmrshiftdb2 REST peak-list search.

    nmrshiftdb2 accepts a list of peaks and a per-nucleus tolerance and
    returns candidate structures with their assigned shifts.  It is
    CC-BY-SA, so results may be reused commercially provided the share-alike
    obligation is honoured; ``LICENSES`` records that.
    """

    name = "nmrshiftdb2"
    ENDPOINT = "https://nmrshiftdb.nmr.uni-koeln.de/nmrshiftdb2/peaksearch"

    def search_by_peaks(
        self,
        peaks: list[tuple[float, float]],
        h_tolerance: float,
        c_tolerance: float,
        max_results: int,
    ) -> list[NMRSpectrumHit]:
        payload = _post_json(
            self.ENDPOINT,
            {
                "peaks": [
                    {"shift": round(h, 4), "nucleus": "1H", "tolerance": h_tolerance}
                    for h, _c in peaks
                ]
                + [
                    {"shift": round(c, 4), "nucleus": "13C", "tolerance": c_tolerance}
                    for _h, c in peaks
                ],
                "maxResults": max_results,
            },
        )
        hits: list[NMRSpectrumHit] = []
        for record in payload.get("results", []) or []:
            reference = _reference_pairs(record.get("shifts") or [])
            if not reference:
                continue
            hits.append(
                _hit_from_shift_table(
                    database="nmrshiftdb2",
                    database_id=str(record.get("spectrumid") or record.get("id") or "unknown"),
                    name=str(record.get("name") or "unnamed structure"),
                    reference=reference,
                    query=peaks,
                    h_tolerance=h_tolerance,
                    c_tolerance=c_tolerance,
                    smiles=record.get("smiles"),
                    formula=record.get("molecularformula") or record.get("formula"),
                    solvent=record.get("solvent"),
                    url=record.get("url"),
                )
            )
        return rank_hits(hits, max_results)

    def get_compound_shifts(
        self, name: str | None, database_id: str | None
    ) -> CompoundShiftResult | None:
        if not database_id:
            return None  # nmrshiftdb2 addresses records by spectrum id only
        payload = _get_json(f"{self.ENDPOINT}/{database_id}")
        record = payload[0] if isinstance(payload, list) and payload else payload
        if not isinstance(record, dict) or not record:
            return None
        return CompoundShiftResult(
            database="nmrshiftdb2",
            database_id=database_id,
            name=str(record.get("name") or database_id),
            shifts=_labelled_shifts(record.get("shifts") or []),
            solvent=record.get("solvent"),
            license=LICENSES["nmrshiftdb2"],
        )


# ---------------------------------------------------------------------------
# Shift-list helpers
# ---------------------------------------------------------------------------


def _reference_pairs(shifts: Any) -> list[tuple[float, float]]:
    """Extract (1H, 13C) pairs from a provider shift list.

    Providers return either ``{"nucleus": "1H", "shift": ...}`` rows or
    ``[label, h, c]`` rows.  Both are normalised here so ``assign_peaks``
    only ever sees pairs.
    """
    pairs: list[tuple[float, float]] = []
    for row in shifts:
        if isinstance(row, dict):
            h = row.get("1H") or row.get("h") or row.get("proton")
            c = row.get("13C") or row.get("c") or row.get("carbon")
            if h is not None and c is not None:
                pairs.append((float(h), float(c)))
        elif isinstance(row, (list, tuple)) and len(row) >= 3:
            h, c = row[-2], row[-1]
            if h is not None and c is not None:
                pairs.append((float(h), float(c)))
    return pairs


def _labelled_shifts(shifts: Any) -> list[tuple[str, float, float]]:
    """Extract (label, 1H, 13C) rows, falling back to positional labels."""
    rows: list[tuple[str, float, float]] = []
    for index, row in enumerate(shifts, start=1):
        if isinstance(row, dict):
            h = row.get("1H") or row.get("h")
            c = row.get("13C") or row.get("c")
            if h is not None and c is not None:
                rows.append((str(row.get("atom") or row.get("label") or index), float(h), float(c)))
        elif isinstance(row, (list, tuple)) and len(row) >= 3:
            rows.append((str(row[0]), float(row[1]), float(row[2])))
    return rows


# ---------------------------------------------------------------------------
# Mock client (local tests and dry-run)
# ---------------------------------------------------------------------------


def _fixture_path() -> Path | None:
    """Locate the committed NP-MRD fixture directory.

    An explicit ``PHYTO_NMR_FIXTURES`` wins (containers can mount it); then
    the repo-relative location; then nothing.  The mock must not crash when
    the fixture is absent -- it degrades to its built-in records, which keeps
    the local plane useful outside a checkout.
    """
    override = os.environ.get("PHYTO_NMR_FIXTURES")
    if override:
        candidate = Path(override)
        return candidate if candidate.is_dir() else None
    # services/nmr_search/app/clients.py -> repo root is three levels up.
    candidate = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "real_npmrd"
    return candidate if candidate.is_dir() else None


class MockNMRSearchClient:
    """Deterministic candidates derived from the local NP-MRD fixture.

    The fixture is a literature-assigned andrographolide HSQC table.  It is
    returned as one high-coverage hit, plus a small set of deterministic
    variant records that exercise ranking, tolerance and licence handling
    without inventing chemistry beyond the fixture's own peaks.

    The variants are labelled ``local-fixture`` with an explicit note that
    they are derived, so no test or caller can mistake them for genuine
    database records.
    """

    name = "local-fixture"

    def __init__(self, fixture_dir: Path | str | None = None) -> None:
        self._fixture_dir = Path(fixture_dir) if fixture_dir else _fixture_path()

    @property
    def _primary_table(self) -> list[tuple[float, float]]:
        if self._fixture_dir is None:
            return []
        path = self._fixture_dir / "andrographolide_hsqc_literature.csv"
        if not path.is_file():
            return []
        pairs: list[tuple[float, float]] = []
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                try:
                    pairs.append((float(row["dH"]), float(row["dC"])))
                except (KeyError, TypeError, ValueError):
                    continue
        return pairs

    def _records(self) -> list[dict[str, Any]]:
        table = self._primary_table
        records: list[dict[str, Any]] = [
            {
                "database_id": "FIXTURE-ANDROGRAPHOLIDE",
                "name": "andrographolide (literature-assigned HSQC fixture)",
                "smiles": "CC12CCC(C1CCC(=C)C2CC(C(=C)CO)O)O",
                "formula": "C20H30O5",
                "solvent": "DMSO-d6",
                "pairs": table,
                "url": "https://np-mrd.org",
            }
        ]
        if table:
            records.append(
                {
                    "database_id": "FIXTURE-ANDROGRAPHOLIDE-13C",
                    "name": "andrographolide analogue (fixture carbon-only variant)",
                    "smiles": None,
                    "formula": "C20H30O5",
                    "solvent": "DMSO-d6",
                    # Carbon shifts only: a 1H of 0.0 can never match a real
                    # proton, so this record ranks below the paired one.
                    "pairs": [(0.0, c) for _h, c in table],
                    "url": None,
                }
            )
            records.append(
                {
                    "database_id": "FIXTURE-ANDROGRAPHOLIDE-SHIFTED",
                    "name": "andrographolide analogue (fixture solvent-shifted variant)",
                    "smiles": None,
                    "formula": "C20H30O5",
                    "solvent": "CD3OD",
                    # A uniform small perturbation: still within tolerance,
                    # but a larger distance, so ranking is exercised.
                    "pairs": [(h + 0.02, c + 0.4) for h, c in table],
                    "url": None,
                }
            )
        return records

    def search_by_peaks(
        self,
        peaks: list[tuple[float, float]],
        h_tolerance: float,
        c_tolerance: float,
        max_results: int,
    ) -> list[NMRSpectrumHit]:
        hits: list[NMRSpectrumHit] = []
        for record in self._records():
            if not record["pairs"]:
                continue
            hits.append(
                _hit_from_shift_table(
                    database="local-fixture",
                    database_id=record["database_id"],
                    name=record["name"],
                    reference=record["pairs"],
                    query=peaks,
                    h_tolerance=h_tolerance,
                    c_tolerance=c_tolerance,
                    smiles=record["smiles"],
                    formula=record["formula"],
                    solvent=record["solvent"],
                    url=record["url"],
                )
            )
        return rank_hits(hits, max_results)

    def get_compound_shifts(
        self, name: str | None, database_id: str | None
    ) -> CompoundShiftResult | None:
        needle = (database_id or name or "").lower()
        for record in self._records():
            if needle and needle not in record["database_id"].lower() and needle not in record["name"].lower():
                continue
            return CompoundShiftResult(
                database="local-fixture",
                database_id=record["database_id"],
                name=record["name"],
                shifts=[
                    (f"C{index}", h, c)
                    for index, (h, c) in enumerate(record["pairs"], start=1)
                ],
                solvent=record["solvent"],
                license=LICENSES["local-fixture"],
            )
        return None
