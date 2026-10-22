"""Pharmacopoeial marker prior (ADR-0066).

Loads ``data/pharmacopoeial_markers.json`` -- curated, per-species
marker compounds with expected RT ranges and UV maxima drawn from
published pharmacopoeial monographs -- and turns a user-selected
species into extra anchors for the identify workflow: even with NO
reference standards in the home library, a pharmacopoeial species has
expected RT windows and lambda_max values for its quality-control
markers.

Honest scope (ADR-0066): markers are EXPECTATIONS, not observations.
They are priors with per-entry provenance; the report cites the
pharmacopoeial source per matched marker.  A marker check that fails
is evidence AGAINST the species-level identity claim, never proof of
identity.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "PharmacopoeialMarker",
    "MarkerHit",
    "load_markers",
    "markers_for_species",
    "check_rt_range",
    "check_uv_range",
    "evaluate_markers",
    "MARKER_FILE",
    "MARKER_SUPPORT_BONUS",
    "MARKER_MISS_PENALTY",
]

logger = logging.getLogger(__name__)

_MARKERS_DIR = Path(__file__).resolve().parents[3] / "data"
MARKER_FILE = _MARKERS_DIR / "pharmacopoeial_markers.json"

#: RT-window tolerance (min): the pharmacopoeial ranges are method-
#: representative, not method-identical; a small grace band keeps the
#: check a screening tool rather than a certificate (ADR-0066).
RT_GRACE_MIN = 0.5
#: UV lambda_max tolerance (nm): solvent shifts are 5-15 nm (ADR-0062).
UV_TOLERANCE_NM = 15.0

#: Score support for at least one marker hit (ADR-0066, threshold basis:
#: asserted, provisional).  A miss penalty must stay STRICTLY SMALLER in
#: magnitude than this bonus for unflagged markers: seasonal/ontogenetic
#: variation, extraction selectivity and co-elution mean a genuine sample
#: can lack a monograph marker, so a miss is never treated as proof of
#: absence.  Only markers explicitly flagged ``expected_in_matrix`` (the
#: species monograph makes the marker a passing criterion for THIS matrix)
#: take the larger penalty.
MARKER_SUPPORT_BONUS = 0.05
MARKER_MISS_PENALTY = 0.10  # flagged markers only; magnitude > bonus
MARKER_MISS_PENALTY_UNFLAGGED = 0.0  # chemotype-variation caveat


class PharmacopoeialMarker(BaseModel):
    """One curated marker entry (validated at load; bad entries are
    dropped with a logged reason, not propagated)."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    marker: str = Field(..., min_length=1)
    smiles: str = Field(..., min_length=1)
    expected_rt_range_min: tuple[float, float] = Field(..., min_length=2)
    uv_lambda_max_nm: list[float] = Field(default_factory=list)
    uv_secondary_max_nm: list[float] = Field(default_factory=list)
    reference: str = Field(..., min_length=1)
    expected_in_matrix: bool = Field(
        default=False,
        description="True when the species monograph makes this marker a "
        "passing criterion for the analysed matrix (e.g. the USP assay "
        "compound).  Only such markers may take the larger miss penalty; "
        "unflagged misses carry 0.0 (ADR-0066 chemotype caveat).",
    )


class MarkerHit(BaseModel):
    """The outcome of checking one marker for one observed peak."""

    model_config = ConfigDict(extra="forbid")

    marker: str
    smiles: str
    rt_in_range: bool | None = None
    uv_in_range: bool | None = None
    rt_expected: tuple[float, float] | None = None
    reference: str = ""
    expected_in_matrix: bool = False


@dataclass
class _MarkerCache:
    loaded: bool = False
    species: dict[str, list[PharmacopoeialMarker]] = None  # type: ignore[assignment]


_cache = _MarkerCache(species={})


def load_markers(path: Path | None = None) -> dict[str, list[PharmacopoeialMarker]]:
    """Load and validate the marker file once; invalid entries are skipped.

    A missing file returns an empty map (the layer degrades to "no
    pharmacopoeial markers available") -- it must not break the prior.
    """
    if _cache.loaded and path is None:
        return _cache.species
    file = path or MARKER_FILE
    species_map: dict[str, list[PharmacopoeialMarker]] = {}
    try:
        raw = json.loads(file.read_text())
    except FileNotFoundError:
        logger.warning("pharmacopoeial marker file not found: %s", file)
        _cache.loaded = True
        _cache.species = species_map
        return species_map
    except json.JSONDecodeError as exc:
        logger.warning("pharmacopoeial marker file is not valid JSON: %s", exc)
        _cache.loaded = True
        _cache.species = species_map
        return species_map

    for species, entries in raw.items():
        if species.startswith("_"):
            continue
        if not isinstance(entries, list):
            logger.warning(
                "marker entries for %r are not a list; skipped", species
            )
            continue
        valid: list[PharmacopoeialMarker] = []
        for entry in entries:
            try:
                marker = PharmacopoeialMarker.model_validate(entry)
                if marker.expected_rt_range_min[0] > marker.expected_rt_range_min[1]:
                    logger.warning(
                        "marker %r (%s): inverted RT range; skipped",
                        marker.marker, species,
                    )
                    continue
                valid.append(marker)
            except Exception as exc:
                logger.warning(
                    "invalid marker entry for %r: %s", species, exc
                )
        if valid:
            species_map[species] = valid
    _cache.loaded = True
    _cache.species = species_map
    return species_map


def markers_for_species(species: str) -> list[PharmacopoeialMarker]:
    """Markers for one species; case-insensitive lookup, empty when absent.

    A species without curated markers falls back gracefully: the caller
    gets an empty list and the layer reports itself as not-applicable
    rather than failing (ADR-0066).
    """
    table = load_markers()
    wanted = species.strip().lower()
    for name, markers in table.items():
        if name.lower() == wanted:
            return markers
    return []


def check_rt_range(
    observed_rt_min: float, marker: PharmacopoeialMarker
) -> bool | None:
    """Is the observed RT inside the marker's expected window (+ grace)?"""
    low, high = marker.expected_rt_range_min
    return bool(low - RT_GRACE_MIN <= observed_rt_min <= high + RT_GRACE_MIN)


def check_uv_range(
    observed_maxima_nm: list[float], marker: PharmacopoeialMarker
) -> bool | None:
    """Do any observed maxima match the marker's expected lambda_max?

    True when at least one observed maximum is within
    ``UV_TOLERANCE_NM`` of ANY expected (primary or secondary) maximum.
    Markers with no expected maxima return None (nothing to compare).
    """
    expected = [*marker.uv_lambda_max_nm, *marker.uv_secondary_max_nm]
    if not expected:
        return None
    if not observed_maxima_nm:
        return None
    return any(
        any(abs(observed - exp) <= UV_TOLERANCE_NM for exp in expected)
        for observed in observed_maxima_nm
    )


def evaluate_markers(
    species: str,
    observed_rt_min: float | None,
    observed_uv_maxima_nm: list[float] | None,
) -> list[MarkerHit]:
    """Evaluate every marker for the species against one observed peak.

    Checks that have no input (no RT observed, no UV observed) stay
    ``None``: an unchecked expectation is never reported as met.
    """
    hits: list[MarkerHit] = []
    for marker in markers_for_species(species):
        hits.append(
            MarkerHit(
                marker=marker.marker,
                smiles=marker.smiles,
                rt_in_range=(
                    check_rt_range(observed_rt_min, marker)
                    if observed_rt_min is not None
                    else None
                ),
                uv_in_range=(
                    check_uv_range(observed_uv_maxima_nm or [], marker)
                    if observed_uv_maxima_nm is not None
                    else None
                ),
                rt_expected=marker.expected_rt_range_min,
                reference=marker.reference,
                expected_in_matrix=marker.expected_in_matrix,
            )
        )
    return hits