"""Data governance validation and curator promotion (ADR-0056).

The rules enforced here mirror ``docs/DATA_GOVERNANCE.md``:

* a compound may only enter ``proprietary`` when the parent record's ABS
  status is ``clear`` (executed PIC/MAT, provenance fields filled);
* a compound may only enter ``public`` when the ABS status is ``clear``
  or ``exempt``;
* ``training_only`` -- the default -- is unrestricted: it publishes and
  licenses nothing, so most data lives here for the first year;
* promotion OUT of ``training_only`` into either promotable tier requires
  the caller to hold the ``curator`` or ``admin`` role (the user document
  field ``role``, resolved by :mod:`auth.roles`).

The audit log (already hash-chained) records every accepted tier change
and every attempted violation, so governance decisions are reviewable.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from phyto_shared.audit_chain import ChainedAuditLogger
from phyto_shared.data_governance import (
    ABSStatus,
    DataTier,
    is_tier_compatible_with_abs,
)

PROPRIETARY_REJECTION = (
    "Proprietary tier requires ABS-clear collection status. Set "
    "collection_country and collection_permit, then have the record "
    "reviewed by the data governance curator."
)

PUBLIC_REJECTION = (
    "Public release requires ABS-clear or exempt status. If the material "
    "is commercially supplied or the species has no ABS restriction, set "
    "abs_status to 'exempt' with a note."
)

NOT_CURATOR_REJECTION = (
    "Promoting a compound out of training_only requires the curator or "
    "admin role; your account role does not permit tier promotion."
)


def validate_compound_tier(
    compound_tier: DataTier,
    abs_status: ABSStatus,
    *,
    previous_tier: DataTier | None = None,
    actor_role: str = "user",
) -> None:
    """Raise 422 when a compound's tier is not reachable, else return None.

    ``previous_tier`` is the tier already stored for the compound (None on
    first write).  When a write PROMOTES a compound out of
    ``training_only``, the caller's role must be curator or admin -- this
    is the curator-only promotion rule.  First writes defaulting to
    ``training_only`` are always allowed; a user creating a compound
    directly in a promotable tier is treated as a promotion attempt and
    needs curator rights too.

    ``actor_role`` is trusted ONLY from ``request.state.user`` claims
    resolved through the role store -- never from a payload.
    """
    # ABS gating: the tier must be reachable given the collection status.
    if not is_tier_compatible_with_abs(compound_tier, abs_status):
        if compound_tier == "proprietary":
            raise HTTPException(status_code=422, detail=PROPRIETARY_REJECTION)
        if compound_tier == "public":
            raise HTTPException(status_code=422, detail=PUBLIC_REJECTION)
        raise HTTPException(
            status_code=422,
            detail=f"tier {compound_tier!r} is not compatible with "
            f"abs_status {abs_status!r}",
        )

    # Curator-only promotion: any non-default tier requires elevation,
    # whether it arrives as a first write or as an edit of a
    # training_only compound.
    promoted = (
        previous_tier is None and compound_tier != "training_only"
    ) or (
        previous_tier == "training_only" and compound_tier != "training_only"
    )
    if promoted and actor_role not in ("curator", "admin"):
        raise HTTPException(status_code=422, detail=NOT_CURATOR_REJECTION)


def audit_tier_change(
    audit_log: ChainedAuditLogger,
    *,
    record_id: str,
    compound_id: str,
    tier: DataTier,
    abs_status: ABSStatus,
    actor_id: str,
    accepted: bool,
    detail: dict[str, Any] | None = None,
) -> None:
    """Append one audit record for a tier change attempt (advisory).

    A failure to write the audit record must never fail the user's
    request (same contract as the usage-event publisher, ADR-0028).
    """
    try:
        audit_log.log_event(
            run_id=record_id,
            event="isolation_tier_change",
            detail={
                "compound_id": compound_id,
                "tier": tier,
                "abs_status": abs_status,
                "actor_id": actor_id,
                "accepted": accepted,
                **(detail or {}),
            },
        )
    except Exception:  # pragma: no cover - advisory only
        pass