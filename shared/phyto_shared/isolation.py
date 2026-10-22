"""Structured bench isolation record: fractionation, TLC, yield (ADR-0055).

The minimum viable isolation record captures what every phytochemist
already writes in the lab notebook when they fractionate an extract and
isolate a compound:

* which fractions the fractionation produced, each tagged with a
  DECLARED polarity tier (not a solvent name -- solvents vary across
  labs, tiers do not);
* the TLC behaviour of the isolated compound as TAGGED reagent
  responses (not free-text colours -- "yellow" is not reproducible
  between labs, ``Naturstoff``-positive is);
* the isolated mass and purity, from which the platform derives an
  abundance prior.

These observations are PRIORS on candidate ranking, never verdicts:
``isolation_prior`` turns them into soft bonuses/penalties and the
isolation reranker attaches one ``EvidenceItem`` per contributing
signal so a reader can always see -- and undo -- their effect.

The full isolation graph (sub-fractionation chains as first-class
edges, TLC image upload, cross-lab comparison) is deliberately
deferred until this MVP is validated by real users (ADR-0055).

Conventions follow ``phyto_shared.models``: every model is strict
(``extra="forbid"``) and numeric ranges encode bench reality (Rf is
0-1, purity is a fraction, a negative yield is bad data).
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .models import StrictModel

__all__ = [
    "PolarityTier",
    "POLARITY_TIERS",
    "TLCReagent",
    "TLCResponseKind",
    "Fraction",
    "TLCResponse",
    "TLCObservation",
    "IsolationCompound",
    "IsolationRecord",
]

PolarityTier = Literal[
    "very_nonpolar", "nonpolar", "mid", "polar", "very_polar"
]
"""Declared elution tier of a fraction.

A tier, not a solvent name: "the 30% EtOAc fraction" and "the DCM/MeOH
95:5 fraction" mean the same thing to the reranker, so the platform
asks the chemist WHICH TIER they consider the fraction and keeps the
solvent as free text for provenance (ADR-0055)."""

POLARITY_TIERS: tuple[str, ...] = (
    "very_nonpolar", "nonpolar", "mid", "polar", "very_polar",
)
#: Least to most polar; ordinal position drives distance comparisons
#: in ``isolation_prior.polarity_from_fractions``.

TLCReagent = Literal[
    "UV254",
    "UV365",
    "Naturstoff",
    "Dragendorff",
    "vanillin_H2SO4",
    "ninhydrin",
    "anisaldehyde_H2SO4",
    "Liebermann_Burchard",
    "FeCl3",
]
"""Visualization reagents with class-diagnostic responses.

Closed on purpose: a reagent the prior tables do not know about would
look like evidence while contributing nothing, so unknown reagents are
rejected at the boundary.  Adding one is a decision (ADR-0055)."""

TLCResponseKind = Literal[
    "positive_strong",
    "positive_weak",
    "negative",
    "absorbing",
    "fluorescent",
]


class Fraction(StrictModel):
    """One fraction of a fractionation, with its declared polarity tier."""

    fraction_id: str = Field(..., min_length=1)
    polarity_tier: PolarityTier
    solvent: str = Field(
        ..., min_length=1,
        description="Free-text solvent or gradient step, e.g. 'EtOAc'",
    )
    volume_ml: float | None = Field(default=None, ge=0.0)
    notes: str | None = None


class TLCResponse(StrictModel):
    """One reagent response observed on a TLC plate."""

    reagent: TLCReagent
    response: TLCResponseKind
    color: str | None = Field(
        default=None,
        description="Free-text colour, e.g. 'yellow' or 'pink'; used "
        "only by reagents whose class hint is colour-dependent "
        "(vanillin_H2SO4), never as the primary signal",
    )


class TLCObservation(StrictModel):
    """The TLC behaviour of one compound under one solvent system."""

    rf: float | None = Field(
        default=None, ge=0.0, le=1.0,
        description="Retention factor; 0-1 by definition",
    )
    solvent_system: str = Field(
        ..., min_length=1,
        description="e.g. 'EtOAc:MeOH:H2O 8:2:0.5'",
    )
    visualization: list[TLCResponse] = Field(default_factory=list)

    def responses_for(self, reagent: str) -> list[TLCResponse]:
        """All recorded responses for one reagent (usually 0 or 1)."""
        return [r for r in self.visualization if r.reagent == reagent]


class IsolationCompound(StrictModel):
    """One compound carried through the isolation, with its bench data."""

    compound_id: str = Field(..., min_length=1)
    present_in_fractions: list[str] = Field(
        ..., min_length=1,
        description="fraction_ids this compound was found in; must "
        "reference fractions of the same record",
    )
    tlc: TLCObservation | None = None
    flash_uv_lambda_max_nm: float | None = Field(
        default=None, gt=150.0, le=800.0,
        description="Lambda max from the flash/flash-UV detector trace",
    )
    yield_mg: float | None = Field(default=None, ge=0.0)
    purity: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Fraction, not percent"
    )
    isolation_chain: list[str] | None = Field(
        default=None,
        description="Parent fraction chain, e.g. ['F3', 'F3.7', 'F3.7.2']; "
        "provenance for humans, not (yet) a graph (ADR-0055)",
    )
    notes: str | None = None


class IsolationRecord(StrictModel):
    """One extract's fractionation and the compounds isolated from it."""

    record_id: str | None = Field(
        default=None,
        description="Server-assigned id (isolation service); None until "
        "the record is persisted",
    )
    extract_id: str = Field(
        ..., min_length=1,
        description="The lab's identifier for the extract this record "
        "belongs to; the key the identify workflow joins on",
    )
    species: str = Field(..., min_length=1)
    plant_part: str | None = None
    mass_starting_g: float = Field(..., gt=0.0)
    extraction_solvent: str = Field(..., min_length=1)
    fractions: list[Fraction] = Field(..., min_length=1)
    compounds: list[IsolationCompound] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    user_id: str = Field(
        ..., min_length=1,
        description="Owner; set by the isolation service from the "
        "authenticated request, never trusted from a payload",
    )

    @model_validator(mode="after")
    def _references_consistent(self) -> "IsolationRecord":
        ids = [f.fraction_id for f in self.fractions]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate fraction_id(s) in {ids}")
        known = set(ids)
        for compound in self.compounds:
            unknown = set(compound.present_in_fractions) - known
            if unknown:
                raise ValueError(
                    f"compound {compound.compound_id!r} references unknown "
                    f"fraction id(s) {sorted(unknown)}; known: {sorted(known)}"
                )
        cids = [c.compound_id for c in self.compounds]
        if len(cids) != len(set(cids)):
            raise ValueError(f"duplicate compound_id(s) in {cids}")
        return self

    def fraction_registry(self) -> dict[str, Fraction]:
        """fraction_id -> Fraction, the lookup the rerankers consume."""
        return {f.fraction_id: f for f in self.fractions}

    def compound(self, compound_id: str) -> IsolationCompound | None:
        """One compound by id, or None."""
        return next(
            (c for c in self.compounds if c.compound_id == compound_id), None
        )
