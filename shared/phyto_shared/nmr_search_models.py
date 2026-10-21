"""NMR structure-search models (ADR-0031).

A structure-search hit is not the same object as a generic ``Candidate``: it
carries which spectral database matched, under which licence, and how well
the submitted peaks matched.  Licence metadata is part of the model because
the platform serves an academic tier and a commercial tier (ADR-0026), and a
downstream component must be able to tell, per hit, whether the record may be
used commercially.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .models import StrictModel

__all__ = [
    "NMRDatabase",
    "LicenseInfo",
    "NMRSpectrumMatch",
    "NMRSpectrumHit",
    "NMRSpectrumSearchRequest",
    "NMRSpectrumSearchResult",
    "CompoundShiftRequest",
    "CompoundShiftResult",
]


NMRDatabase = Literal["NP-MRD", "BMRB", "nmrshiftdb2", "local-fixture"]


class LicenseInfo(StrictModel):
    """Licence terms attached to one database record."""

    name: str = Field(..., min_length=1, description="e.g. 'CC-BY-NC', 'CC0'")
    commercial_use: bool = Field(
        ..., description="True only if commercial reuse is permitted"
    )
    url: str | None = None
    attribution: str | None = None


class NMRSpectrumMatch(StrictModel):
    """How well the submitted peak list matched one database record.

    Scores are distances: LOWER is better, matching the platform's
    ``lower_is_better`` convention for spectral matching (ADR-0008).
    """

    matched_peaks: int = Field(ge=0)
    query_peaks: int = Field(ge=1)
    distance: float = Field(
        ge=0.0, description="Mean matched 1H/13C distance in ppm (lower is better)"
    )
    coverage: Annotated[float, Field(ge=0.0, le=1.0)] = Field(
        ..., description="Fraction of query peaks matched"
    )
    ranking_convention: Literal["lower_is_better"] = "lower_is_better"


class NMRSpectrumHit(StrictModel):
    """One database structure that matched the query peaks."""

    database: NMRDatabase
    database_id: str = Field(..., min_length=1, description="Native record id in that DB")
    name: str
    smiles: str | None = None
    formula: str | None = None
    solvent: str | None = None
    field_strength_mhz: float | None = Field(default=None, gt=0.0)
    match: NMRSpectrumMatch
    license: LicenseInfo
    url: str | None = None


class NMRSpectrumSearchRequest(StrictModel):
    """Search databases by an observed peak list."""

    peaks: list[tuple[float, float]] = Field(
        ...,
        min_length=1,
        description="Observed (1H ppm, 13C ppm) cross-peaks",
    )
    h_tolerance_ppm: float = Field(default=0.05, gt=0.0, le=1.0)
    c_tolerance_ppm: float = Field(default=1.0, gt=0.0, le=20.0)
    max_results: int = Field(default=10, ge=1, le=100)
    databases: list[NMRDatabase] | None = Field(
        default=None, description="Restrict to these databases; None means all"
    )
    include_noncommercial: bool = Field(
        default=True,
        description="When False, drop records whose licence forbids commercial "
        "use (CC-BY-NC). The commercial tier sets this to False (ADR-0026).",
    )

    @model_validator(mode="after")
    def _distinct_peaks(self) -> "NMRSpectrumSearchRequest":
        if len(set(self.peaks)) != len(self.peaks):
            # Duplicate cross-peaks are almost always a parsing artefact, and
            # they silently bias the mean distance.
            raise ValueError("peaks contains duplicate (1H, 13C) pairs")
        return self


class NMRSpectrumSearchResult(StrictModel):
    """Ranked hits, best (lowest distance) first."""

    hits: list[NMRSpectrumHit] = Field(default_factory=list)
    total_count: int = Field(ge=0)
    database_counts: dict[str, int] = Field(default_factory=dict)
    query_peak_count: int = Field(ge=1)


class CompoundShiftRequest(StrictModel):
    """Fetch the reference shift table for a known compound."""

    name: str | None = Field(default=None, min_length=1)
    database_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _need_an_identifier(self) -> "CompoundShiftRequest":
        if not self.name and not self.database_id:
            raise ValueError("provide either name or database_id")
        return self


class CompoundShiftResult(StrictModel):
    """Reference shifts for one compound, with provenance."""

    database: NMRDatabase
    database_id: str
    name: str
    shifts: list[tuple[str, float, float]] = Field(
        default_factory=list,
        description="(assignment label, 1H ppm, 13C ppm) rows from the reference record",
    )
    solvent: str | None = None
    license: LicenseInfo
