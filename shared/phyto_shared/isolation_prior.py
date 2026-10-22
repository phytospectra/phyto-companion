"""Heuristic priors derived from a bench isolation record (ADR-0055).

EVERY function in this module is a PRIOR, not a measurement.  They
translate what a phytochemist observes at the bench into soft signals
for candidate ranking:

* ``polarity_from_fractions`` -- which declared polarity tiers a
  compound eluted in says something about its polarity;
* ``class_from_tlc`` -- TLC reagent responses are class-diagnostic to
  a first approximation (Naturstoff -> flavonoids, Dragendorff ->
  alkaloids, ...);
* ``abundance_from_yield`` -- isolated mass over starting mass is the
  % w/w abundance in the plant material.

Known heuristic limitations (read before trusting a number):

* TLC reagents are suggestive, not specific.  Dragendorff also fires
  on some lactones; FeCl3 on any decent phenol, enol or hydroxamic
  acid; Naturstoff on coumarins and anthraquinones, not just
  flavonoids.  A positive narrows the field, it never certifies a
  class -- which is why downstream these become bonuses/penalties,
  never filters.
* Vanillin_H2SO4 colours are matched by substring on the free-text
  ``color`` field: only "yellow" and "pink"/"red" carry a class hint
  (the two the prior tables were taught); every other colour (green,
  blue, violet, grey, ...) is ignored rather than guessed.
* Fraction tiers are the USER's declaration about their own column,
  not a measurement of the compound.  A "mid" tag on an overloaded
  column can hide a polarity spread; ``confidence`` reflects the
  spread across fractions, not chromatographic truth.
"""

from __future__ import annotations

from pydantic import Field

from .exceptions import InvalidInputError
from .isolation import (
    POLARITY_TIERS,
    Fraction,
    PolarityTier,
    TLCObservation,
    TLCResponse,
)
from .models import StrictModel

__all__ = [
    "PolarityEstimate",
    "polarity_from_fractions",
    "class_from_tlc",
    "abundance_from_yield",
]


class PolarityEstimate(StrictModel):
    """Estimated polarity of a compound from the fractions it appears in."""

    tier: PolarityTier | None = Field(
        default=None,
        description="Estimated tier, or None when no fraction data exists",
    )
    confidence: float = Field(
        ..., ge=0.0, le=1.0,
        description="1.0 when all fractions agree; falls with the spread "
        "between the most and least polar fraction the compound appeared in",
    )
    basis: str = Field(
        ..., min_length=1,
        description="Human-readable statement of which fractions drove "
        "the estimate, so the number can be audited",
    )


#: Confidence per ordinal spread between the least and most polar
#: fraction the compound appeared in (0 = all agree ... 4 = full smear).
_SPREAD_CONFIDENCE: dict[int, float] = {0: 1.0, 1: 0.75, 2: 0.5, 3: 0.25}
#: Floor for a compound smeared across the entire gradient: still a
#: (weak) signal, not noise.
_SPREAD_CONFIDENCE_FLOOR = 0.2


def polarity_from_fractions(
    fractions_present: list[str],
    fraction_registry: dict[str, Fraction],
) -> PolarityEstimate:
    """Estimate a compound's polarity tier from the fractions it elutes in.

    Heuristic: a compound appears in fractions whose declared tier
    matches its polarity, so

    * all fractions in ONE tier -> that tier, confidence 1.0;
    * fractions spread across tiers -> the ordinal middle of the
      span, with confidence falling as the spread widens (a wide
      smear is weaker evidence than a tight elution);
    * no fractions -> tier None, confidence 0.0 (no data: the
      downstream reranker must skip, not invent);
    * a fraction id missing from the registry is a caller wiring
      error and raises ``InvalidInputError`` -- guessing a tier for an
      unknown fraction would fabricate evidence.
    """
    if not fractions_present:
        return PolarityEstimate(
            tier=None, confidence=0.0, basis="no fraction data recorded"
        )
    unknown = [f for f in fractions_present if f not in fraction_registry]
    if unknown:
        raise InvalidInputError(
            "fraction id(s) not in the registry",
            context={"unknown": unknown, "known": sorted(fraction_registry)},
        )
    tiers = [fraction_registry[f].polarity_tier for f in fractions_present]
    ordinals = sorted(POLARITY_TIERS.index(t) for t in tiers)  # type: ignore[arg-type]
    spread = ordinals[-1] - ordinals[0]
    # Round-half-up on the mean ordinal: a span centred between two
    # tiers resolves to the MORE polar one (compounds tail toward
    # polar in normal-phase practice).
    middle = -(-sum(ordinals) // len(ordinals))
    tier: PolarityTier = POLARITY_TIERS[min(middle, len(POLARITY_TIERS) - 1)]  # type: ignore[assignment]
    confidence = _SPREAD_CONFIDENCE.get(spread, _SPREAD_CONFIDENCE_FLOOR)
    basis = (
        f"present in {', '.join(fractions_present)} "
        f"(tiers: {', '.join(tiers)}; spread {spread})"
    )
    return PolarityEstimate(tier=tier, confidence=confidence, basis=basis)


#: Reagent -> class hints, most specific first within each entry.
#: Weight per response kind is applied separately.
_REAGENT_CLASS_HINTS: dict[str, tuple[str, ...]] = {
    "Naturstoff": ("flavonol", "flavone", "flavanone"),
    "Dragendorff": ("alkaloid",),
    "Liebermann_Burchard": ("sterol", "triterpene"),
    "FeCl3": ("phenolic",),
}

#: Weight of one class hint by response kind: a strong spot is real
#: evidence, a faint one is a whisper, and Naturstoff fluorescence
#: (UV365, orange/yellow) is the classic flavonoid screen.
_RESPONSE_WEIGHT: dict[str, float] = {
    "positive_strong": 1.0,
    "positive_weak": 0.6,
    "fluorescent": 0.8,  # counted only for Naturstoff/UV365 below
}

#: UV254 quenching says "conjugated/aromatic" and little else, so it
#: always ranks LAST: it adds to whatever the reagents say rather than
#: leading the list (ADR-0055).
_AROMATIC_WEIGHT = 0.3


def _vanillin_classes(response: TLCResponse) -> list[tuple[str, float]]:
    """Class hints from a vanillin_H2SO4 colour (substring, documented).

    Only the two colours the prior tables were taught carry weight;
    anything else is an explicit no-hint rather than a guess.
    """
    color = (response.color or "").lower()
    if response.response not in ("positive_strong", "positive_weak"):
        return []
    if "yellow" in color:
        return [("flavonoid", _RESPONSE_WEIGHT[response.response])]
    if "pink" in color or "red" in color:
        weight = _RESPONSE_WEIGHT[response.response]
        return [("terpene", weight), ("sterol", weight)]
    return []


def class_from_tlc(tlc: TLCObservation | None) -> list[str]:
    """Ranked compound classes consistent with the TLC reagent responses.

    Heuristic ranking: classes from STRONG positives outrank weak
    ones; within equal weight, more specific reagents (Naturstoff,
    Dragendorff, Liebermann-Burchard) keep their natural order; a
    bare UV254 absorption appends "aromatic" at the end whatever else
    the plate said.  An empty result means the plate said nothing
    class-diagnostic (all negative, or only untaught colours) -- the
    downstream reranker must treat that as NO signal, not as evidence
    against every class.
    """
    if tlc is None:
        return []
    weighted: dict[str, float] = {}
    for response in tlc.visualization:
        if response.response in ("negative",):
            continue
        weight = _RESPONSE_WEIGHT.get(response.response, 0.0)
        if weight == 0.0 and not (
            response.reagent == "UV254" and response.response == "absorbing"
        ):
            continue
        if response.reagent == "UV254" and response.response == "absorbing":
            weighted["aromatic"] = max(weighted.get("aromatic", 0.0), _AROMATIC_WEIGHT)
            continue
        if response.reagent == "vanillin_H2SO4":
            for cls, w in _vanillin_classes(response):
                weighted[cls] = max(weighted.get(cls, 0.0), w)
            continue
        if response.reagent not in _REAGENT_CLASS_HINTS:
            # Known reagent, untaught response (e.g. anisaldehyde
            # negative): contributes nothing.
            continue
        for cls in _REAGENT_CLASS_HINTS[response.reagent]:
            weighted[cls] = max(weighted.get(cls, 0.0), weight)
    # Sort by weight desc, then by insertion order for determinism.
    ranked = sorted(weighted.items(), key=lambda kv: (-kv[1],))
    return [cls for cls, _w in ranked]


def abundance_from_yield(mass_starting_g: float, yield_mg: float) -> float:
    """Isolated abundance as % w/w of the starting plant material.

    ``12.5 mg from 50 g`` -> ``0.025`` (% w/w).  A yield larger than
    the starting mass is physically impossible and raises
    ``InvalidInputError`` rather than returning an absurd percentage.
    """
    if mass_starting_g <= 0:
        raise InvalidInputError(
            "starting mass must be positive", context={"mass_starting_g": mass_starting_g}
        )
    if yield_mg < 0:
        raise InvalidInputError("yield must be non-negative", context={"yield_mg": yield_mg})
    if yield_mg > mass_starting_g * 1000.0:
        raise InvalidInputError(
            "yield exceeds the starting mass",
            context={"yield_mg": yield_mg, "mass_starting_g": mass_starting_g},
        )
    return round(100.0 * yield_mg / (mass_starting_g * 1000.0), 6)
