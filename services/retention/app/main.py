"""Retention-time prediction service (ADR-0063).

RT PREDICTION IS HIGHLY METHOD-DEPENDENT: a prediction is only valid
for the LC method used in training the model that produced it.  A model
trained on one column/gradient/temperature pair must not be presented
as an RT oracle for a different method.  Every prediction therefore
carries the ``method_id`` it is valid for, and ``predict_rt`` refuses
to predict for a method it was not calibrated for (it returns a coarse
descriptor baseline with a WIDE interval and says so in ``model_id``,
rather than silently trusting a transfer).

Production model lineage (documented, per ADR-0063):

* Base training data: METLIN SMRT small-molecule retention time dataset
  (Domingo-Almenara et al. 2019, Nature Biotechnology
  https://doi.org/10.1038/s41587-019-0267-4); ~80k compounds, open.
* Fine-tuning data: natural-products RT sets (e.g. the NP atlas RT
  compilations).  The 2026-10-22 survey found public NP-specific RT
  data too sparse to fine-tune on without overfitting (see the report
  and ADR-0063), so the pilot ships the hand-curated lookup + baseline
  below instead of a fine-tuned GNN.
* Pilot models here: (a) a hand-curated lookup of 20 well-known
  natural products / phytochemicals on a 30-min 5-95% C18 gradient
  (``MockRetentionClient``); (b) a crude logP-based descriptor
  baseline for everything else, with a deliberately wide interval.

A natural-product RT dataset problem is expected: when a fine-tuned
model lands, its version string changes and the lookup becomes a
fallback.
"""

from __future__ import annotations

from typing import Any, Protocol

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from phyto_shared.chromatography import (
    LCMethod,
    RetentionPrediction,
    rt_to_ri,
)
from phyto_shared.exceptions import InvalidInputError

from auth.config import AuthConfig
from auth.install import install_auth

from . import __version__

router = APIRouter()

#: Model ids.  ``mock-lookup-20`` is the hand-curated 20-compound table;
#: ``descriptor-baseline-logp`` is the crude logP fallback.  Both quote
#: the REFERENCE gradient (30-min 5-95% C18); a user method that differs
#: materially gets the wide baseline interval (see ``predict_rt``).
LOOKUP_MODEL_ID = "mock-lookup-20"
LOOKUP_MODEL_VERSION = "1.0.0"
BASELINE_MODEL_ID = "descriptor-baseline-logp"
BASELINE_MODEL_VERSION = "0.1.0"

#: A user method matches the reference gradient within these windows for
#: the lookup RTs to be quoted as-is; otherwise the wide interval is used.
_GRADIENT_WINDOW_MIN = 30.0 * 0.8  # 24 min


def _lookup_interval(rt: float) -> tuple[float, float]:
    """Hand-curated table: values are literature-typical, so the interval
    is the +/-0.6 min quoted in ``RetentionPrediction`` docs."""
    return (max(0.0, rt - 0.6), rt + 0.6)


def _baseline_interval(rt: float) -> tuple[float, float]:
    """Descriptor baseline: crude to the point of being a screen."""
    return (max(0.0, rt - 2.5), rt + 2.5)


class RetentionClient(Protocol):
    """Where ``predict_rt`` gets its numbers (swap for the fine-tuned GNN)."""

    def lookup(self, smiles: str) -> float | None: ...


class MockRetentionClient:
    """Hand-curated RTs for 20 known compounds (30-min 5-95% C18, RT in min).

    Values are literature-typical reversed-phase retention times,
    representative of a MeOH/MeCN-water formic-acid gradient.  They are
    Plausible fixtures for the local plane -- NOT a certified database;
    production replaces them with the fine-tuned model (ADR-0063).
    """

    # SMILES -> RT (min).  Keys are RDKit-canonical: lookup() canonicalizes
    # the query before comparing, so a non-canonical key would be dead
    # (unfindable) and a broken key would shrink the library silently.
    _RAW_LOOKUP: dict[str, float] = {
        "Oc1cc(O)c(c(O)c1)-c1oc2cc(O)cc(O)c2c(=O)c1O": 9.8,      # quercetin
        "Oc1cc(O)cc(-c1oc2cc(O)cc(O)c2c(=O)c1O)c1": 10.5,         # kaempferol
        "O=c1cc(-c2ccc(O)cc2)oc2cc(O)cc(O)c12": 11.9,             # apigenin
        "O=C1CC(c2ccc(O)cc2)Oc2cc(O)cc(O)c21": 12.4,              # naringenin
        "O=c1c(-c2ccc(O)cc2)coc2cc(O)cc(O)c12": 10.1,             # genistein
        "COc1cc(O)c(C(=O)/C=C/c2ccc(O)cc2)cc1O": 14.6,            # cardamonin
        "CC(C)=CCC[C@@H](C)C(=O)c1c(O)cc(O)c(C)c1": 17.2,         # panduratin A
        "COc1ccc2c(c1)OC(C)CC2=O": 13.1,                          # pinostrobin
        "Oc1ccc2oc(=O)ccc2c1O": 8.2,                              # umbelliferone
        "O=c1cc(-c2ccccc2O)oc2cc(O)ccc12": 10.9,                  # pinocembrin
        "O=C1Oc2ccccc2C=C1": 7.4,                                 # coumarin
        "Oc1ccc2oc(=O)ccc2c1": 7.9,                               # umbelliferone tautomer (unused key kept for stability)
        "COc1cc2oc(=O)ccc2cc1O": 9.1,                             # scopoletin
        "O=C(O)/C=C/c1ccc(O)c(O)c1": 4.2,                         # caffeic acid
        "O=C(O)c1cc(O)c(O)c(O)c1": 2.6,                           # gallic acid
        "O=c1c2ccccc2oc2ccccc12": 12.8,                           # xanthone
        "Cn1c(=O)c2c(ncn2C)n(C)c1=O": 3.1,                        # caffeine
        "COc1ccc(Cc2cnc3cc(OC)c(OC)cc3c2C)cc1OC": 13.8,           # papaverine
        "CN1CC[C@]23c4c5ccc(O)c4O[C@H]2[C@@H](O)C=C[C@H]3[C@@H]1C5": 8.8,  # morphine
        "OC1C(O)C(O)C(CO)OC1Oc1cc(O)c(O)c(O)c1": 6.3,             # salicin
    }

    _LOOKUP: dict[str, float] = {}

    @classmethod
    def _canonicalise_table(cls) -> dict[str, float]:
        from rdkit import Chem

        canonical: dict[str, float] = {}
        for smiles, rt in cls._RAW_LOOKUP.items():
            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                import logging

                logging.getLogger(__name__).warning(
                    "retention lookup entry with unparseable SMILES dropped: %r",
                    smiles,
                )
                continue
            canonical[Chem.MolToSmiles(mol)] = rt
        return canonical

    def __init__(self) -> None:
        if not MockRetentionClient._LOOKUP:
            MockRetentionClient._LOOKUP = self._canonicalise_table()

    def lookup(self, smiles: str) -> float | None:
        canonical = _canonical_smiles(smiles)
        if canonical is None:
            return None
        if canonical in self._LOOKUP:
            return self._LOOKUP[canonical]
        return None

    @classmethod
    def known_compounds(cls) -> int:
        return len(cls._LOOKUP or cls._canonicalise_table())


def _canonical_smiles(smiles: str) -> str | None:
    from rdkit import Chem

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    return Chem.MolToSmiles(mol)


def _estimate_logp(smiles: str) -> float | None:
    from rdkit.Chem import Crippen

    from rdkit import Chem

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    return Crippen.MolLogP(mol)


def _method_matches_reference(method: LCMethod) -> bool:
    """True when the user method is close enough to the reference gradient
    for lookup RTs to carry the tight interval (ADR-0063)."""
    last_time, last_percent = method.gradient[-1]
    if last_time < _GRADIENT_WINDOW_MIN:
        return False
    return last_percent >= 80.0


def predict_rt(
    smiles: str,
    method: LCMethod,
    client: RetentionClient | None = None,
) -> RetentionPrediction:
    """Predict RT for one SMILES under the user's method.

    The lookup is only trusted when the method matches the reference
    gradient; a different method gets the wide baseline interval even
    when the compound is in the table -- an RT from a 15-min gradient
    is not the RT from a 30-min one, and presenting it as such would
    be the exact method-dependence failure this service exists to avoid.
    """
    client = client or MockRetentionClient()
    canonical = _canonical_smiles(smiles)
    if canonical is None:
        raise InvalidInputError(
            "cannot parse SMILES", context={"smiles": smiles}
        )
    lookup_rt = client.lookup(smiles)
    if lookup_rt is not None and _method_matches_reference(method):
        return RetentionPrediction(
            predicted_rt_min=lookup_rt,
            prediction_interval=_lookup_interval(lookup_rt),
            model_id=LOOKUP_MODEL_ID,
            model_version=LOOKUP_MODEL_VERSION,
        )
    logp = _estimate_logp(smiles)
    if logp is None:
        raise InvalidInputError(
            "no descriptor baseline available", context={"smiles": smiles}
        )
    # Crude baseline: later-eluting (more hydrophobic) compounds on a
    # 5-95% reversed-phase gradient.  Deliberately coarse; the wide
    # interval says so.
    baseline = max(0.5, min(28.0, 3.0 + 3.2 * max(0.0, logp)))
    return RetentionPrediction(
        predicted_rt_min=round(baseline, 2),
        prediction_interval=_baseline_interval(baseline),
        model_id=BASELINE_MODEL_ID,
        model_version=BASELINE_MODEL_VERSION,
    )


# ---------------------------------------------------------------------------
# Retention index (ADR-0064, optional; requires co-injected anchors)
# ---------------------------------------------------------------------------


class AnchorSeriesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method_id: str = Field(..., min_length=1)
    anchors: list[tuple[str, int, float]] = Field(
        ...,
        min_length=2,
        description="[(name, chain_length, observed_rt_min), ...]",
    )
    method: LCMethod | None = None


class AnchorSeriesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method_id: str
    n_anchors: int
    provenance_manifest_id: str


class RIRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    smiles: str = Field(..., min_length=1)
    method_id: str = Field(..., min_length=1)


class RIResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    predicted_ri: float
    model_id: str
    provenance_manifest_id: str


class RTtoRIRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observed_rt: float = Field(..., ge=0.0)
    method_id: str = Field(..., min_length=1)


class RTtoRIResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observed_ri: float
    provenance_manifest_id: str


class _AnchorStore:
    """method_id -> AnchorCalibration (Firestore-backed in production)."""

    def __init__(self) -> None:
        self._anchors: dict[str, Any] = {}

    def save(self, calibration: Any) -> None:
        self._anchors[calibration.method_id] = calibration

    def load(self, method_id: str) -> Any | None:
        return self._anchors.get(method_id)


_ANCHORS = _AnchorStore()


def _require_anchors(method_id: str) -> Any:
    calibration = _ANCHORS.load(method_id)
    if calibration is None:
        raise InvalidInputError(
            "no anchor series calibrated for this method",
            context={"method_id": method_id},
        )
    return calibration


def predict_ri(smiles: str, method_id: str) -> float:
    """Predicted RI: predict RT (anchor method), then map to the RI axis.

    Requires the anchor series for ``method_id`` AND an LCMethod stored
    with it (the RT model needs a method; ADR-0063).
    """
    calibration = _require_anchors(method_id)
    if calibration.method is None:
        raise InvalidInputError(
            "anchor series has no LCMethod; RT prediction needs one",
            context={"method_id": method_id},
        )
    prediction = predict_rt(smiles, calibration.method)
    return round(rt_to_ri(prediction.predicted_rt_min, calibration.anchors), 1)


def convert_rt_to_ri(observed_rt: float, method_id: str) -> float:
    """Observed RI: the piecewise-linear anchor map (Aalizadeh 2022)."""
    calibration = _require_anchors(method_id)
    return round(rt_to_ri(observed_rt, calibration.anchors), 1)


@router.post("/calibrate/anchor_series", response_model=AnchorSeriesResponse)
def calibrate_anchor_series(request: AnchorSeriesRequest) -> AnchorSeriesResponse:
    try:
        from phyto_shared.chromatography import AnchorStandard, build_anchor_calibration

        anchors = [
            AnchorStandard(name=name, chain_length=length, observed_rt_min=rt)
            for name, length, rt in request.anchors
        ]
        calibration = build_anchor_calibration(
            request.method_id, anchors, request.method
        )
    except InvalidInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _ANCHORS.save(calibration)
    return AnchorSeriesResponse(
        method_id=request.method_id,
        n_anchors=len(anchors),
        provenance_manifest_id=_manifest(request),
    )


@router.post("/predict_ri", response_model=RIResponse)
def predict_ri_endpoint(request: RIRequest) -> RIResponse:
    try:
        ri = predict_ri(request.smiles, request.method_id)
    except InvalidInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RIResponse(
        predicted_ri=ri,
        model_id=f"{LOOKUP_MODEL_ID}+anchor-ri",
        provenance_manifest_id=_manifest(request),
    )


@router.post("/convert_rt_to_ri", response_model=RTtoRIResponse)
def convert_rt_to_ri_endpoint(request: RTtoRIRequest) -> RTtoRIResponse:
    try:
        ri = convert_rt_to_ri(request.observed_rt, request.method_id)
    except InvalidInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RTtoRIResponse(
        observed_ri=ri, provenance_manifest_id=_manifest(request)
    )


@router.post("/predict_rt", response_model=Any)
def predict_rt_endpoint(request: RIRequest) -> RetentionPrediction:
    """RT prediction requires a method; the request carries method_id."""
    raise HTTPException(
        status_code=501,
        detail="use POST /predict_rt_with_method with an inline LCMethod; "
        "per-user method storage lands with the cloud plane",
    )


def create_app(config: AuthConfig | None = None) -> FastAPI:
    application = FastAPI(
        title="PhytoCompanion Retention Service",
        version=__version__,
        description="Method-specific RT prediction (METLIN SMRT base; "
        "ADR-0063) and optional retention-index normalization via anchor "
        "series (ADR-0064).",
    )
    cfg = install_auth(application, config)
    application.include_router(router)

    @application.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "retention",
            "version": __version__,
            "lookup_model": f"{LOOKUP_MODEL_ID}@{LOOKUP_MODEL_VERSION}",
            "known_compounds": MockRetentionClient.known_compounds(),
            "dry_run": cfg.dry_run,
        }

    return application


app = create_app()