"""Chromatography domain models and math (ADR-0055..ADR-0058).

Design notes
------------
* Like ``taxonomy_models``, this module must NOT import
  ``models.StrictModel``: the shared ``models.CandidateRank`` will
  reference ``UVClass`` from ``uv_chromophore`` and chromatography types
  appear in ``schemas.SampleMetadata``, so the dependency arrow must
  stay ``models/schemas -> chromatography`` and never the other way.
  The base config below is identical to ``StrictModel``.
* ``LCMethod`` is user-supplied and per-method: retention prediction is
  ONLY valid for the method used in training (see the retention service
  docstring, ADR-0057).  No default method exists on purpose -- a caller
  that did not describe its method gets no RT layer, not a guessed one.
* Retention-index math follows Aalizadeh et al. 2022
  (https://doi.org/10.1016/j.aca.2022.341688): an injected homologous
  series (e.g. the cocamide diethanolamide homologous series) defines a
  piecewise-linear RT -> RI map, RI = 100 * n + 100 * (RT - RT_n) /
  (RT_n+1 - RT_n) between bracketing homologues of chain length n.
"""

from __future__ import annotations

from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .exceptions import InvalidInputError

__all__ = [
    "ChromatographyModel",
    "ColumnPhase",
    "LCMethod",
    "RetentionPrediction",
    "ReferenceStandard",
    "AnchorStandard",
    "AnchorCalibration",
    "REFERENCE_GRADIENT",
    "uv_cosine",
    "ms2_cosine",
    "rt_to_ri",
    "ri_to_rt",
    "build_anchor_calibration",
]


class ChromatographyModel(BaseModel):
    """Same contract as ``models.StrictModel`` (see module docstring)."""

    model_config = ConfigDict(frozen=False, extra="forbid")


ColumnPhase = Literal["C18", "C8", "HILIC", "phenyl"]

#: The gradient the hand-curated lookup RTs and pharmacopoeial RT ranges
#: are representative of (ADR-0057, ADR-0060): a 30-minute 5 -> 95% B
#: reversed-phase analytical gradient.  Documented so a reader knows what
#: "expected_rt_range_min" in the curated data means.
REFERENCE_GRADIENT: tuple[tuple[float, float], ...] = (
    (0.0, 5.0),
    (30.0, 95.0),
)


class LCMethod(ChromatographyModel):
    """A user-supplied liquid-chromatography method description.

    RT prediction is highly method-dependent: a prediction is only
    valid for the method used in training the model (ADR-0057).  The
    method is stored per user per method_id by the calibration and
    retention services; two methods with different gradients, columns or
    temperatures produce different RTs for the same compound and must
    never share calibration data.
    """

    column_type: ColumnPhase
    mobile_phase_a: str = Field(
        ..., min_length=1, description="e.g. 'water + 0.1% formic acid'"
    )
    mobile_phase_b: str = Field(
        ..., min_length=1, description="e.g. 'acetonitrile + 0.1% formic acid'"
    )
    gradient: list[tuple[float, float]] = Field(
        ...,
        min_length=1,
        description="(time_min, percent_B) points, time strictly increasing",
    )
    flow_rate_ml_min: float = Field(..., gt=0.0, le=10.0)
    column_temperature_c: float = Field(..., gt=0.0, le=200.0)

    @model_validator(mode="after")
    def _gradient_sane(self) -> "LCMethod":
        points = self.gradient
        times = [t for t, _ in points]
        if any(later <= earlier for earlier, later in zip(times, times[1:])):
            raise ValueError("gradient time points must be strictly increasing")
        for _time, percent_b in points:
            if not 0.0 <= percent_b <= 100.0:
                raise ValueError(
                    f"gradient percent_B must be in [0, 100] (got {percent_b})"
                )
        if points[0][0] != 0.0:
            raise ValueError("gradient must start at time 0.0 min")
        return self


class RetentionPrediction(ChromatographyModel):
    """One RT prediction with an honest interval (ADR-0057).

    ``prediction_interval`` carries the model's uncertainty: the curated
    lookup uses +/-0.6 min, the descriptor baseline +/-2.5 min.  The
    interval is what makes RT usable as a SOFT filter -- consumers
    compare the interval, not the point estimate, and never exclude a
    candidate on RT alone.
    """

    predicted_rt_min: float = Field(..., ge=0.0)
    prediction_interval: tuple[float, float]
    model_id: str
    model_version: str

    @model_validator(mode="after")
    def _interval_brackets(self) -> "RetentionPrediction":
        low, high = self.prediction_interval
        if not (0.0 <= low <= self.predicted_rt_min <= high):
            raise ValueError(
                "prediction_interval must bracket predicted_rt_min: "
                f"({low}, {self.predicted_rt_min}, {high})"
            )
        return self


class ReferenceStandard(ChromatographyModel):
    """One authentic reference standard the user ran under a method.

    This is the calibration unit of the home library (ADR-0055): the
    observed ``rt_min`` anchors Level-1 matching, ``uv_spectrum`` and
    ``ms2`` anchor orthogonal-property agreement.
    """

    name: str = Field(..., min_length=1)
    smiles: str = Field(..., min_length=1)
    rt_min: float = Field(..., ge=0.0, le=240.0)
    uv_spectrum: list[tuple[float, float]] = Field(
        default_factory=list,
        description="[(wavelength_nm, absorbance), ...]; may be empty when no "
        "PDA was available",
    )
    ms2: list[tuple[float, float]] = Field(
        default_factory=list,
        description="[(mz, intensity), ...]; may be empty when no MS2 was "
        "acquired for the standard",
    )


class AnchorStandard(ChromatographyModel):
    """One member of a co-injected homologous anchor series (ADR-0058)."""

    name: str = Field(..., min_length=1)
    chain_length: int = Field(..., ge=1, le=60)
    observed_rt_min: float = Field(..., ge=0.0, le=240.0)


class AnchorCalibration(ChromatographyModel):
    """A stored anchor series for one method, defining the RT<->RI map.

    ``method_id`` ties the calibration to the user's method; the
    ``method`` itself is optional because the anchors are what define
    the RI axis -- the LCMethod is needed only if predicted RIs must
    also be computed from predicted RTs.
    """

    method_id: str = Field(..., min_length=1)
    anchors: list[AnchorStandard] = Field(..., min_length=2)
    method: LCMethod | None = None

    @model_validator(mode="after")
    def _anchors_monotonic(self) -> "AnchorCalibration":
        ordered = sorted(self.anchors, key=lambda a: a.chain_length)
        rts = [a.observed_rt_min for a in ordered]
        if any(later <= earlier for earlier, later in zip(rts, rts[1:])):
            raise ValueError(
                "anchor RTs must strictly increase with chain length on a "
                "reversed-phase system; check the co-injection"
            )
        return self


def build_anchor_calibration(
    method_id: str, anchors: Sequence[AnchorStandard], method: LCMethod | None = None
) -> AnchorCalibration:
    """Validate and store an anchor series (strict-monotonicity gate)."""
    return AnchorCalibration(
        method_id=method_id, anchors=list(anchors), method=method
    )


# ---------------------------------------------------------------------------
# Spectral similarity
# ---------------------------------------------------------------------------


def uv_cosine(
    reference: Sequence[tuple[float, float]],
    observed: Sequence[tuple[float, float]],
    grid_step_nm: float = 2.0,
) -> float:
    """Cosine between two UV spectra sampled on a common wavelength grid.

    PDA spectra arrive with arbitrary sampling; both are linearly
    interpolated onto a common grid (the overlap of the two wavelength
    ranges, ``grid_step_nm`` spacing) before the cosine.  Disjoint
    wavelength ranges or empty spectra return 0.0 -- similarity is never
    invented from nothing.
    """
    if not reference or not observed:
        return 0.0

    def _axis(spectrum: Sequence[tuple[float, float]]) -> tuple[list[float], list[float]]:
        points = sorted(spectrum, key=lambda p: p[0])
        return [p[0] for p in points], [p[1] for p in points]

    ref_x, ref_y = _axis(reference)
    obs_x, obs_y = _axis(observed)
    low = max(min(ref_x), min(obs_x))
    high = min(max(ref_x), max(obs_x))
    if high <= low:
        return 0.0

    def _interp(xs: list[float], ys: list[float], x: float) -> float:
        if x <= xs[0]:
            return ys[0]
        if x >= xs[-1]:
            return ys[-1]
        for i in range(1, len(xs)):
            if xs[i] >= x:
                span = xs[i] - xs[i - 1]
                weight = (x - xs[i - 1]) / span if span else 0.0
                return ys[i - 1] + weight * (ys[i] - ys[i - 1])
        return ys[-1]  # pragma: no cover - guarded by the bounds above

    steps = max(2, int((high - low) / grid_step_nm) + 1)
    grid = [low + (high - low) * i / (steps - 1) for i in range(steps)]
    a = [_interp(ref_x, ref_y, x) for x in grid]
    b = [_interp(obs_x, obs_y, x) for x in grid]
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def ms2_cosine(
    reference: Sequence[tuple[float, float]],
    observed: Sequence[tuple[float, float]],
    fragment_tolerance_da: float = 0.02,
) -> tuple[float, int]:
    """Greedy-match MS2 cosine (sqrt intensities, matched pairs only).

    Same convention as the elucidation ranker's ``ms2_cosine``
    (GNPS-style); duplicated here because services must not import each
    other's app packages -- the elucidation ranker keeps its own copy
    and the two must stay in sync (ADR-0055).
    """
    ref = sorted(reference)
    obs = sorted(observed)
    matched_ref: list[float] = []
    matched_obs: list[float] = []
    used = [False] * len(obs)
    for ref_mz, ref_intensity in ref:
        best_index = -1
        best_distance = fragment_tolerance_da + 1.0
        for j, (obs_mz, _intensity) in enumerate(obs):
            if used[j]:
                continue
            distance = abs(ref_mz - obs_mz)
            if distance <= fragment_tolerance_da and distance < best_distance:
                best_distance = distance
                best_index = j
        if best_index >= 0:
            used[best_index] = True
            matched_ref.append(ref_intensity)
            matched_obs.append(obs[best_index][1])
    if not matched_ref:
        return 0.0, 0
    sqrt_ref = [v**0.5 for v in matched_ref]
    sqrt_obs = [v**0.5 for v in matched_obs]
    denominator = (
        sum(v * v for v in sqrt_ref) * sum(v * v for v in sqrt_obs)
    ) ** 0.5
    if denominator == 0.0:
        return 0.0, 0
    return (
        sum(a * b for a, b in zip(sqrt_ref, sqrt_obs)) / denominator,
        len(matched_ref),
    )


# ---------------------------------------------------------------------------
# Retention index (Aalizadeh 2022)
# ---------------------------------------------------------------------------


def rt_to_ri(rt_min: float, anchors: Sequence[AnchorStandard]) -> float:
    """Convert RT to retention index by piecewise-linear interpolation.

    RI = 100 * n1 + 100 * (rt - rt1) / (rt2 - rt1) between the bracketing
    homologues of chain length n1 < n2; outside the anchor span the
    nearest segment's slope extrapolates linearly.  Values below the
    first anchor can go below 100 * n_first: that is a faithful
    extrapolation, not an error.
    """
    if len(anchors) < 2:
        raise InvalidInputError(
            "at least two anchors are required to define the RI axis",
            context={"n_anchors": len(anchors)},
        )
    ordered = sorted(anchors, key=lambda a: (a.chain_length, a.observed_rt_min))
    # Duplicated chain lengths are ambiguous: which anchor defines the axis?
    lengths = [a.chain_length for a in ordered]
    if len(set(lengths)) != len(lengths):
        raise InvalidInputError(
            "anchor chain lengths must be unique", context={"chain_lengths": lengths}
        )
    # The validator on AnchorCalibration enforces monotonic RT; defend here
    # too so a bare anchor list cannot produce an inverted segment.
    if any(
        later.observed_rt_min <= earlier.observed_rt_min
        for earlier, later in zip(ordered, ordered[1:])
    ):
        raise InvalidInputError(
            "anchor RTs must strictly increase with chain length",
            context={"anchors": [(a.chain_length, a.observed_rt_min) for a in ordered]},
        )

    for earlier, later in zip(ordered, ordered[1:]):
        if rt_min <= later.observed_rt_min:
            return _ri_on_segment(rt_min, earlier, later)
    # Beyond the last anchor: extrapolate the final segment.
    return _ri_on_segment(rt_min, ordered[-2], ordered[-1])


def _ri_on_segment(rt_min: float, low: AnchorStandard, high: AnchorStandard) -> float:
    span = high.observed_rt_min - low.observed_rt_min
    fraction = (rt_min - low.observed_rt_min) / span if span else 0.0
    return 100.0 * (low.chain_length + fraction)


def ri_to_rt(ri: float, anchors: Sequence[AnchorStandard]) -> float:
    """Inverse map: retention index back to RT on the anchor axis."""
    if len(anchors) < 2:
        raise InvalidInputError(
            "at least two anchors are required to define the RI axis",
            context={"n_anchors": len(anchors)},
        )
    ordered = sorted(anchors, key=lambda a: a.chain_length)
    for earlier, later in zip(ordered, ordered[1:]):
        if ri <= 100.0 * later.chain_length:
            return _rt_on_segment(ri, earlier, later)
    return _rt_on_segment(ri, ordered[-2], ordered[-1])


def _rt_on_segment(ri: float, low: AnchorStandard, high: AnchorStandard) -> float:
    ri_low = 100.0 * low.chain_length
    ri_high = 100.0 * high.chain_length
    span = ri_high - ri_low
    fraction = (ri - ri_low) / span if span else 0.0
    return low.observed_rt_min + fraction * (
        high.observed_rt_min - low.observed_rt_min
    )
