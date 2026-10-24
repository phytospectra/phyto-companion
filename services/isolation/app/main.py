"""FastAPI entrypoint for the isolation service (ADR-0055).

Structured bench records -- fractionation, TLC reagent responses,
yield -- stored per user and served back as ranking priors.

Routes (all require the auth middleware's Bearer token; the user id
comes from ``request.state.user``, never from a payload):

* ``POST   /isolation/records``                          create
* ``GET    /isolation/records``                          list mine
* ``GET    /isolation/records/{record_id}``              fetch one
* ``PUT    /isolation/records/{record_id}``              update
* ``POST   /isolation/records/{record_id}/compounds``    add a compound
* ``GET    /isolation/records/{record_id}/compounds/{compound_id}/evidence``
                                                          evidence summary

Governance (ADR-0056): every compound carries a ``data_tier`` (default
``training_only``) and every record an ABS status (default ``unknown``).
Writes are validated against the tier/ABS matrix and promotion out of
``training_only`` requires the server-side ``curator``/``admin`` role
(``auth.roles``); every attempt, accepted or not, lands in the
hash-chained governance audit log.

Record payloads may omit ``user_id`` (the service sets it from the
authenticated caller and overrides any client-supplied value) and
``record_id`` (server-assigned).
"""

from __future__ import annotations

import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from phyto_shared.audit_chain import ChainedAuditLogger
from phyto_shared.data_governance import ABSStatus, DEFAULT_ABS_STATUS
from phyto_shared.isolation import (
    IsolationCompound,
    IsolationRecord,
    TLCObservation,
)
from phyto_shared.isolation_prior import abundance_from_yield, class_from_tlc, polarity_from_fractions

from auth.config import AuthConfig
from auth.install import install_auth
from auth.roles import RoleStore, get_role_store, role_for

from . import __version__
from .governance import (
    audit_tier_change,
    validate_compound_tier,
)
from .store import (
    InMemoryIsolationStore,
    IsolationRecordNotFound,
    IsolationStore,
)

router = APIRouter()


@lru_cache(maxsize=1)
def get_store() -> IsolationStore:
    """Firestore in the cloud plane; in-process locally (mirrors the
    query-cache plane selection in the literature service)."""
    if os.environ.get("PHYTO_ENV") == "cloud":
        from .store import FirestoreIsolationStore

        return FirestoreIsolationStore(
            os.environ.get("GOOGLE_CLOUD_PROJECT", "phyto-companion-prod")
        )
    return InMemoryIsolationStore()


@lru_cache(maxsize=1)
def get_roles() -> RoleStore:
    """Curator roles: Firestore in the cloud plane, in-process locally."""
    return get_role_store()


@lru_cache(maxsize=1)
def get_audit_log() -> ChainedAuditLogger:
    """Hash-chained audit trail for every tier change (ADR-0056)."""
    return ChainedAuditLogger(
        os.environ.get("PHYTO_ISOLATION_AUDIT_LOG")
        or str(Path(tempfile.gettempdir()) / "phyto_isolation_governance_audit.jsonl")
    )


class IsolationRecordIn(BaseModel):
    """Create/update payload: the shared record minus server-owned fields.

    Governance fields (``collection_country`` / ``collection_permit`` /
    ``abs_status``) ride on the domain model; the compound tiers in the
    payload are validated against the record's ABS status and the
    caller's role before anything is stored (ADR-0056).
    """

    model_config = ConfigDict(extra="forbid")

    extract_id: str
    species: str
    plant_part: str | None = None
    mass_starting_g: float
    extraction_solvent: str
    fractions: list[Any]
    compounds: list[Any] = []
    collection_country: str | None = None
    collection_permit: str | None = None
    abs_status: ABSStatus = DEFAULT_ABS_STATUS

    def to_record(self, user_id: str) -> IsolationRecord:
        """Validate through the domain model, with the user pinned."""
        return IsolationRecord(
            **self.model_dump(),
            user_id=user_id,
        )


class IsolationRecordOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record: IsolationRecord


class IsolationRecordList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    records: list[IsolationRecord]
    count: int


class AddCompoundIn(BaseModel):
    """A new compound for an existing record; fractions must already exist."""

    model_config = ConfigDict(extra="forbid")

    compound: IsolationCompound


def _user_id(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if user is None:
        # Auth disabled (local plane without enforcement): a stable local
        # identity keeps the per-user store keyed and tests honest.
        return "local"
    return user.uid


def _role(request: Request) -> str:
    """The caller's curation role, resolved from the server-side role store.

    The role NEVER comes from the payload; an unauthenticated local-plane
    request resolves to the least-privileged role.
    """
    return role_for(get_roles(), _user_id(request))


def _validate_record_governance(
    record: IsolationRecord, previous: IsolationRecord | None, *, actor_role: str
) -> None:
    """Enforce the tier/ABS rules for every compound in ``record``.

    Raises 422 (governance violations) or 403 (non-curator promotion);
    ``previous`` is the stored record on update (None on create).
    """
    prev_compounds = (
        {c.compound_id: c for c in previous.compounds} if previous else {}
    )
    for compound in record.compounds:
        prev = prev_compounds.get(compound.compound_id)
        previous_tier = prev.data_tier if prev is not None else None
        try:
            validate_compound_tier(
                compound.data_tier,
                record.abs_status,
                previous_tier=previous_tier,
                actor_role=actor_role,
            )
        except HTTPException as exc:
            audit_tier_change(
                get_audit_log(),
                record_id=record.record_id or "-",
                compound_id=compound.compound_id,
                tier=compound.data_tier,
                abs_status=record.abs_status,
                actor_id="-",
                accepted=False,
                detail={"reason": str(exc.detail)},
            )
            raise


def _not_found(record_id: str) -> HTTPException:
    # Missing and foreign are the same 404: existence is not disclosed.
    return HTTPException(status_code=404, detail=f"isolation record {record_id} not found")


@router.post("/isolation/records", response_model=IsolationRecordOut, status_code=201)
def create_record(payload: IsolationRecordIn, request: Request) -> IsolationRecordOut:
    user_id = _user_id(request)
    try:
        record = payload.to_record(user_id)
    except Exception as exc:  # pydantic ValidationError and friends
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _validate_record_governance(record, None, actor_role=_role(request))
    stored = get_store().create(record)
    for compound in record.compounds:
        audit_tier_change(
            get_audit_log(),
            record_id=stored.record_id or "-",
            compound_id=compound.compound_id,
            tier=compound.data_tier,
            abs_status=record.abs_status,
            actor_id=user_id,
            accepted=True,
        )
    return IsolationRecordOut(record=stored)


@router.get("/isolation/records", response_model=IsolationRecordList)
def list_records(request: Request) -> IsolationRecordList:
    records = get_store().list_for_user(_user_id(request))
    return IsolationRecordList(records=records, count=len(records))


@router.get("/isolation/records/{record_id}", response_model=IsolationRecordOut)
def get_record(record_id: str, request: Request) -> IsolationRecordOut:
    try:
        record = get_store().get(record_id, user_id=_user_id(request))
    except IsolationRecordNotFound as exc:
        raise _not_found(record_id) from exc
    return IsolationRecordOut(record=record)


@router.put("/isolation/records/{record_id}", response_model=IsolationRecordOut)
def update_record(
    record_id: str, payload: IsolationRecordIn, request: Request
) -> IsolationRecordOut:
    user_id = _user_id(request)
    try:
        record = payload.to_record(user_id)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    store = get_store()
    try:
        previous = store.get(record_id, user_id=user_id)
    except IsolationRecordNotFound as exc:
        raise _not_found(record_id) from exc
    _validate_record_governance(record, previous, actor_role=_role(request))
    try:
        stored = store.update(record_id, record, user_id=user_id)
    except IsolationRecordNotFound as exc:
        raise _not_found(record_id) from exc
    prev_compounds = {c.compound_id: c for c in previous.compounds}
    for compound in record.compounds:
        prev = prev_compounds.get(compound.compound_id)
        if prev is None or prev.data_tier != compound.data_tier:
            audit_tier_change(
                get_audit_log(),
                record_id=record_id,
                compound_id=compound.compound_id,
                tier=compound.data_tier,
                abs_status=record.abs_status,
                actor_id=user_id,
                accepted=True,
                detail={"previous_tier": prev.data_tier if prev else None},
            )
    return IsolationRecordOut(record=stored)


@router.post(
    "/isolation/records/{record_id}/compounds",
    response_model=IsolationRecordOut,
    status_code=201,
)
def add_compound(
    record_id: str, payload: AddCompoundIn, request: Request
) -> IsolationRecordOut:
    user_id = _user_id(request)
    store = get_store()
    try:
        record = store.get(record_id, user_id=user_id)
    except IsolationRecordNotFound as exc:
        raise _not_found(record_id) from exc
    known = {f.fraction_id for f in record.fractions}
    unknown = set(payload.compound.present_in_fractions) - known
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=(
                f"compound references unknown fraction id(s) {sorted(unknown)}; "
                f"known: {sorted(known)}"
            ),
        )
    if record.compound(payload.compound.compound_id) is not None:
        raise HTTPException(
            status_code=409,
            detail=f"compound {payload.compound.compound_id} already exists "
            "in this record; PUT the record to replace it",
        )
    actor_role = _role(request)
    try:
        validate_compound_tier(
            payload.compound.data_tier,
            record.abs_status,
            previous_tier=None,
            actor_role=actor_role,
        )
    except HTTPException as exc:
        audit_tier_change(
            get_audit_log(),
            record_id=record_id,
            compound_id=payload.compound.compound_id,
            tier=payload.compound.data_tier,
            abs_status=record.abs_status,
            actor_id=user_id,
            accepted=False,
            detail={"reason": str(exc.detail)},
        )
        raise
    updated = record.model_copy(
        update={"compounds": [*record.compounds, payload.compound]}
    )
    stored = store.update(record_id, updated, user_id=user_id)
    audit_tier_change(
        get_audit_log(),
        record_id=record_id,
        compound_id=payload.compound.compound_id,
        tier=payload.compound.data_tier,
        abs_status=record.abs_status,
        actor_id=user_id,
        accepted=True,
    )
    return IsolationRecordOut(record=stored)


class TLCEvidence(BaseModel):
    """One TLC reagent response, verbatim from the record."""

    model_config = ConfigDict(extra="forbid")

    reagent: str
    response: str
    color: str | None = None


class CompoundEvidenceSummary(BaseModel):
    """All bench evidence attached to one compound (ADR-0055).

    This is the payload the frontend evidence panel renders alongside
    UV / RT / taxon evidence, and what the identify workflow's
    isolation reranker consumed.  Derived fields (predicted tier,
    TLC classes, abundance) are labelled as priors, not measurements.
    """

    model_config = ConfigDict(extra="forbid")

    compound_id: str
    fractions: list[dict[str, Any]]
    polarity_estimate: dict[str, Any] | None = None
    tlc: TLCObservation | None = None
    tlc_classes: list[str] = []
    flash_uv_lambda_max_nm: float | None = None
    yield_mg: float | None = None
    purity: float | None = None
    abundance_percent_w_w: float | None = None
    isolation_chain: list[str] | None = None
    notes: str | None = None


@router.get(
    "/isolation/records/{record_id}/compounds/{compound_id}/evidence",
    response_model=CompoundEvidenceSummary,
)
def compound_evidence(
    record_id: str, compound_id: str, request: Request
) -> CompoundEvidenceSummary:
    user_id = _user_id(request)
    try:
        record = get_store().get(record_id, user_id=user_id)
    except IsolationRecordNotFound as exc:
        raise _not_found(record_id) from exc
    compound = record.compound(compound_id)
    if compound is None:
        raise HTTPException(
            status_code=404,
            detail=f"compound {compound_id} not found in record {record_id}",
        )
    registry = record.fraction_registry()
    estimate = polarity_from_fractions(compound.present_in_fractions, registry)
    abundance = (
        abundance_from_yield(record.mass_starting_g, compound.yield_mg)
        if compound.yield_mg is not None
        else None
    )
    return CompoundEvidenceSummary(
        compound_id=compound_id,
        fractions=[f.model_dump() for f in record.fractions],
        polarity_estimate=estimate.model_dump(),
        tlc=compound.tlc,
        tlc_classes=class_from_tlc(compound.tlc),
        flash_uv_lambda_max_nm=compound.flash_uv_lambda_max_nm,
        yield_mg=compound.yield_mg,
        purity=compound.purity,
        abundance_percent_w_w=abundance,
        isolation_chain=compound.isolation_chain,
        notes=compound.notes,
    )


def create_app(config: AuthConfig | None = None) -> FastAPI:
    application = FastAPI(
        title="PhytoCompanion Isolation Service",
        version=__version__,
        description="Structured bench isolation records: fractionation, "
        "TLC reagent responses, yield (ADR-0055).",
    )
    cfg = install_auth(application, config)
    application.include_router(router)

    @application.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "isolation",
            "version": __version__,
            "plane": os.environ.get("PHYTO_ENV", "local"),
            "dry_run": cfg.dry_run,
        }

    return application


app = create_app()