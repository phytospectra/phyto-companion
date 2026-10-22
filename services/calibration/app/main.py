"""Home-library calibration service (ADR-0061).

A phytochemist's first question about an unknown peak is not "what is
the NMR?" but "is this one of MY standards?".  This service stores,
per user and per LC method, the authentic reference standards the user
has run, and answers two questions:

* **Level-1 matching** (``POST /calibrate/match``): does an observed
  peak match a stored standard within tight tolerances on up to three
  orthogonal properties -- RT (±0.1 min), UV (cosine > 0.95) and MS2
  (cosine > 0.85)?  A match on every supplied property is an MSI Level
  1 identification (authentic standard, orthogonal confirmation).
* **RT offset calibration** (``compute_rt_offset``): the difference
  between the user's observed RT for a standard and the RT the
  retention model predicts for it.  This delta absorbs the systematic
  method-vs-model gap and is applied to every other candidate's
  predicted RT in the same method.

Storage is behind a small protocol (``HomeLibraryStore``).  The local
plane ships an in-memory store; production swaps in Firestore with the
same per-user/per-method keying: ``users/{uid}/methods/{method_id}/
standards/{standard_name}``.  **Method isolation is structural**: every
lookup is keyed by (user, method), so standards from one method can
never match peaks run under another.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from phyto_shared.chromatography import (
    LCMethod,
    ReferenceStandard,
    ms2_cosine,
    uv_cosine,
)

from auth.config import AuthConfig
from auth.install import install_auth

from . import __version__

router = APIRouter()

#: Matching tolerances (ADR-0061).  Level 1 needs EVERY supplied property
#: to agree; a property the standard or the peak does not carry is
#: skipped rather than failed (a UV-less standard can still match on
#: RT + MS2), but at least ONE property must match.
#:
#: Threshold basis: asserted, provisional.  RT ±0.1 min is the reproducibility
#: of an injected standard on a well-retained peak under one method (worth
#: tightening only with replication data); UV cosine > 0.95 and MS2 cosine
#: > 0.85 follow the GNPS-library-match convention the elucidation ranker
#: already uses (ADR-0014) so a Level-1 stamp cannot disagree with a spectral
#: match on the same pair.  Calibrate all three against matched
#: standard-vs-peak pairs before treating them as validated.
RT_TOLERANCE_MIN = 0.1
UV_COSINE_THRESHOLD = 0.95
MS2_COSINE_THRESHOLD = 0.85


class HomeLibraryStore(Protocol):
    """Per-user, per-method persistence (Firestore in production)."""

    def save_standards(
        self, user_id: str, method_id: str,
        standards: list[ReferenceStandard], method: LCMethod | None,
    ) -> None: ...

    def load_standards(
        self, user_id: str, method_id: str
    ) -> list[ReferenceStandard]: ...

    def load_method(self, user_id: str, method_id: str) -> LCMethod | None: ...


class InMemoryHomeLibraryStore:
    """Local-plane store; production implements the protocol in Firestore."""

    def __init__(self) -> None:
        # (user_id, method_id) -> {"standards": [...], "method": LCMethod|None}
        self._libraries: dict[tuple[str, str], dict[str, Any]] = {}

    def save_standards(
        self, user_id: str, method_id: str,
        standards: list[ReferenceStandard], method: LCMethod | None,
    ) -> None:
        entry = self._libraries.setdefault(
            (user_id, method_id), {"standards": [], "method": None}
        )
        by_name = {s.name: s for s in entry["standards"]}
        for standard in standards:
            by_name[standard.name] = standard
        entry["standards"] = sorted(by_name.values(), key=lambda s: s.name)
        if method is not None:
            entry["method"] = method

    def load_standards(
        self, user_id: str, method_id: str
    ) -> list[ReferenceStandard]:
        return list(self._libraries.get((user_id, method_id), {}).get("standards", []))

    def load_method(self, user_id: str, method_id: str) -> LCMethod | None:
        return self._libraries.get((user_id, method_id), {}).get("method")


_STORE: HomeLibraryStore = InMemoryHomeLibraryStore()


def get_store() -> HomeLibraryStore:
    return _STORE


def set_store(store: HomeLibraryStore) -> None:
    global _STORE
    _STORE = store


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


class ObservedPeak(BaseModel):
    """The unknown peak to match against the home library."""

    model_config = ConfigDict(extra="forbid")

    rt_min: float = Field(..., ge=0.0, le=240.0)
    uv_spectrum: list[tuple[float, float]] = Field(default_factory=list)
    ms2: list[tuple[float, float]] = Field(default_factory=list)


class MatchOutcome(BaseModel):
    """The result of one Level-1 matching attempt.

    ``level`` is 1 only when every supplied property agreed AND at least
    one property was actually compared; otherwise it is 0 and
    ``matched_standard`` is None.  ``property_scores`` records every
    comparison that was made, so a near-miss shows WHY it failed.
    """

    model_config = ConfigDict(extra="forbid")

    level: Literal[0, 1]
    matched_standard: str | None = None
    property_scores: dict[str, float] = Field(default_factory=dict)
    notes: str = ""


def _peak_matches(
    peak: ObservedPeak, standard: ReferenceStandard
) -> tuple[bool, dict[str, float], list[str]]:
    """Every supplied property must agree; at least one must be compared."""
    scores: dict[str, float] = {}
    failures: list[str] = []
    compared = 0

    rt_delta = abs(peak.rt_min - standard.rt_min)
    scores["rt_delta_min"] = round(rt_delta, 4)
    compared += 1
    if rt_delta > RT_TOLERANCE_MIN:
        failures.append(
            f"RT delta {rt_delta:.3f} min > {RT_TOLERANCE_MIN} min"
        )

    if standard.uv_spectrum and peak.uv_spectrum:
        cosine = uv_cosine(standard.uv_spectrum, peak.uv_spectrum)
        scores["uv_cosine"] = round(cosine, 4)
        compared += 1
        if cosine <= UV_COSINE_THRESHOLD:
            failures.append(f"UV cosine {cosine:.3f} <= {UV_COSINE_THRESHOLD}")
    elif peak.uv_spectrum and not standard.uv_spectrum:
        failures.append("peak carries UV but the standard has none")

    if standard.ms2 and peak.ms2:
        cosine, _n = ms2_cosine(standard.ms2, peak.ms2)
        scores["ms2_cosine"] = round(cosine, 4)
        compared += 1
        if cosine <= MS2_COSINE_THRESHOLD:
            failures.append(f"MS2 cosine {cosine:.3f} <= {MS2_COSINE_THRESHOLD}")
    elif peak.ms2 and not standard.ms2:
        failures.append("peak carries MS2 but the standard has none")

    return (not failures and compared >= 1), scores, failures


def match_peak(
    peak: ObservedPeak,
    standards: list[ReferenceStandard],
) -> MatchOutcome:
    """Match one observed peak against the user's standards for a method.

    Ties (two standards both matching) resolve to the smallest RT delta --
    the tightest chromatographic agreement wins.
    """
    best: ReferenceStandard | None = None
    best_delta = float("inf")
    best_scores: dict[str, float] = {}
    # The nearest miss keeps its comparison scores so the outcome shows
    # WHY the peak failed Level 1 (the MatchOutcome contract).
    near_scores: dict[str, float] = {}
    near_failures: list[str] = []
    for standard in standards:
        matched, scores, failures = _peak_matches(peak, standard)
        if matched and abs(peak.rt_min - standard.rt_min) < best_delta:
            best = standard
            best_delta = abs(peak.rt_min - standard.rt_min)
            best_scores = scores
        if not near_scores or len(failures) < len(near_failures):
            near_scores, near_failures = scores, failures
    if best is None:
        return MatchOutcome(
            level=0,
            property_scores=near_scores,
            notes=(
                "no standard matched Level 1"
                + (f"; nearest: {'; '.join(near_failures)}" if near_failures else "")
            ),
        )
    return MatchOutcome(
        level=1, matched_standard=best.name, property_scores=best_scores
    )


def compute_rt_offset(
    standard: ReferenceStandard,
    predicted_rt_min: float,
) -> float:
    """Observed minus predicted RT for a standard (the systematic delta).

    Callers apply this delta to every other candidate predicted under
    the same method (ADR-0061).  A standard whose predicted RT is
    unknown cannot calibrate the offset -- the caller skips the offset,
    it does not guess zero.
    """
    return round(standard.rt_min - predicted_rt_min, 4)


def apply_rt_offset(predicted_rt_min: float, offset: float) -> float:
    """Apply the calibration offset to one candidate's predicted RT.

    Negative results clamp to zero: time does not run backwards.
    """
    return max(0.0, round(predicted_rt_min + offset, 4))


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class CalibrateStandardsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method_id: str = Field(..., min_length=1)
    standards: list[ReferenceStandard] = Field(..., min_length=1)
    method: LCMethod | None = None


class CalibrateStandardsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method_id: str
    n_standards: int
    provenance_manifest_id: str


class CalibrateMethodsItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method_id: str
    n_standards: int
    method: LCMethod | None = None


class CalibrateMethodsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    methods: list[CalibrateMethodsItem]


class CalibrateMatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method_id: str = Field(..., min_length=1)
    peak: ObservedPeak


class CalibrateMatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match: MatchOutcome
    provenance_manifest_id: str


def _manifest(payload: BaseModel) -> str:
    from phyto_shared.provenance import ProvenanceBuilder, sha256_dict

    return (
        ProvenanceBuilder()
        .with_input_hash("request", sha256_dict(payload.model_dump()))
        .with_model_version("calibration-service", __version__)
        .with_environment()
        .build()
        .manifest_hash
    )


@router.post("/calibrate/standards", response_model=CalibrateStandardsResponse)
def calibrate_standards(request: CalibrateStandardsRequest) -> CalibrateStandardsResponse:
    store = get_store()
    # Local plane: single-user.  Cloud plane: request.state.user.uid.
    store.save_standards(
        "local-user", request.method_id, request.standards, request.method
    )
    return CalibrateStandardsResponse(
        method_id=request.method_id,
        n_standards=len(request.standards),
        provenance_manifest_id=_manifest(request),
    )


@router.get("/calibrate/methods", response_model=CalibrateMethodsResponse)
def calibrate_methods() -> CalibrateMethodsResponse:
    store = get_store()
    methods = getattr(store, "methods", None)
    if methods is None:
        # InMemory store exposes its key space for listing.
        methods = sorted(
            {method_id for _user, method_id in getattr(store, "_libraries", {})}
        )
    items = []
    for method_id in methods:
        standards = store.load_standards("local-user", method_id)
        items.append(
            CalibrateMethodsItem(
                method_id=method_id,
                n_standards=len(standards),
                method=store.load_method("local-user", method_id),
            )
        )
    return CalibrateMethodsResponse(methods=items)


@router.post("/calibrate/match", response_model=CalibrateMatchResponse)
def calibrate_match(request: CalibrateMatchRequest) -> CalibrateMatchResponse:
    standards = get_store().load_standards("local-user", request.method_id)
    if not standards:
        raise HTTPException(
            status_code=404,
            detail=f"no calibrated standards for method {request.method_id!r}",
        )
    return CalibrateMatchResponse(
        match=match_peak(request.peak, standards),
        provenance_manifest_id=_manifest(request),
    )


def create_app(config: AuthConfig | None = None) -> FastAPI:
    application = FastAPI(
        title="PhytoCompanion Calibration Service",
        version=__version__,
        description="Per-user reference-standard home libraries: Level-1 "
        "matching and RT offset calibration (ADR-0061).",
    )
    cfg = install_auth(application, config)
    application.include_router(router)

    @application.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "calibration",
            "version": __version__,
            "store": type(get_store()).__name__,
            "dry_run": cfg.dry_run,
        }

    return application


app = create_app()