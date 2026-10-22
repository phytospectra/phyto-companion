"""Data governance: data tiers, ABS status, and the tier/whitelist rules.

This module implements the DATA MODEL for the public-by-default tiering
strategy with a proprietary escalation path (ADR-0056).  It is the
engineering half of the governance policy; the legal requirements live in
``docs/DATA_GOVERNANCE.md`` and the ToS itself is drafted by a lawyer from
that checklist.

Three tiers:

* ``public``        -- CC0.  Anyone may use the data, commercially included,
                       with no further license.
* ``proprietary``   -- Held in a commercial corpus, accessible only to
                       licensees under a separate agreement.
* ``training_only`` -- Used to train/validate models, never published or
                       licensed.  The DEFAULT for every contribution: data
                       starts here and is promoted only by explicit
                       curator action (nothing becomes public by accident).

ABS (access and benefit-sharing) status, per the Nagoya-Protocol framing in
``docs/DATA_GOVERNANCE.md``:

* ``clear``   -- executed PIC + MAT (prior informed consent and mutually
                 agreed terms) for the collection.
* ``pending`` -- collection underway or the agreement is in negotiation;
                 the data may not be published or licensed.
* ``exempt``  -- commercially supplied authenticated material, a country
                 with no ABS restriction on the species, or published
                 literature reproduction.
* ``unknown`` -- provenance not established; treated as ``pending`` for
                 every downstream decision.

Both helpers are pure and deterministic so they are trivially unit-testable
and safe to call from validators, the provenance builder, and CI checks.
"""

from __future__ import annotations

from typing import Literal

__all__ = [
    "DataTier",
    "TIERS",
    "ABSStatus",
    "ABS_STATUSES",
    "DEFAULT_DATA_TIER",
    "DEFAULT_ABS_STATUS",
    "is_tier_compatible_with_abs",
    "is_source_allowed_in_tier",
    "NON_COMMERCIAL_SOURCES",
    "SOURCE_DATASET_IDS",
    "USER_ROLES",
]

DataTier = Literal["public", "proprietary", "training_only"]
"""Disposition of one piece of contributed data (ADR-0056).

``public`` is CC0: publishing under it extinguishes the ability to
re-license the same data later, so promotion into it is an explicit,
curator-gated act.  ``proprietary`` requires an executed ABS agreement.
``training_only`` is the sink everything defaults to."""

TIERS: tuple[str, ...] = ("public", "proprietary", "training_only")

ABSStatus = Literal["clear", "pending", "exempt", "unknown"]
"""Access-and-benefit-sharing status of the physical collection (ADR-0056).

The obligation attaches to the physical sample, not the data licence:
publishing data openly does not extinguish ABS duties owed to the source
country.  ``unknown`` is deliberately a first-class value rather than an
absent field -- absence would silently read as clear."""

ABS_STATUSES: tuple[str, ...] = ("clear", "pending", "exempt", "unknown")

DEFAULT_DATA_TIER: DataTier = "training_only"
"""Every contribution starts in the most restrictive tier (ADR-0056)."""

DEFAULT_ABS_STATUS: ABSStatus = "unknown"
"""Un-provenanced collections default to ``unknown``, treated as ``pending``."""

USER_ROLES = ("user", "curator", "admin")
"""Curation reviewer roles (ADR-0056).  Only ``curator`` and ``admin`` may
promote a compound out of ``training_only`` into ``public`` or
``proprietary``."""


def is_tier_compatible_with_abs(tier: DataTier, abs: ABSStatus) -> bool:
    """Whether ``tier`` is reachable given the collection's ABS ``status``.

    Rules:

    * ``public`` requires ``clear`` or ``exempt`` -- publishing CC0 data
      whose collection provenance is unsettled would transfer the material's
      benefits to everyone while the source country's claim is unresolved.
    * ``proprietary`` requires ``clear`` -- commercial use of ABS-governed
      material needs an executed agreement (``exempt`` is not enough: an
      exemption covers legitimate non-commercial distribution, not putting
      the data into a licensed corpus).
    * ``training_only`` allows any status -- internal model training
      publishes and licenses nothing.

    ``unknown`` is never compatible with a promotable tier: it is the
    caller's job (isolation validator, provenance enforcement) to treat it
    as ``pending`` and reject the promotion.  Pure and deterministic.
    """
    if tier == "training_only":
        return True
    if tier == "public":
        return abs in ("clear", "exempt")
    if tier == "proprietary":
        return abs == "clear"
    raise ValueError(f"unknown tier: {tier!r}")


NON_COMMERCIAL_SOURCES: frozenset[str] = frozenset(
    {
        "np_mrd",
        # NP-MRD literature-assignment fixtures committed under
        # tests/fixtures/real_npmrd are CC-BY-NC like their source archive.
        "np_mrd_fixtures",
        # nmrshiftdb2 is CC-BY-SA: commercial use is permitted with care, but
        # share-alike terms are incompatible with a licensed proprietary
        # corpus, so it is confined to the free academic tier as well.
        "nmrshiftdb2",
    }
)
"""Source dataset IDs whose licence bars commercial use.

Every source tagged CC-BY-NC (or otherwise non-commercial / share-alike) in
``docs/DATA_SOURCES.md`` must appear here; the provenance validator rejects
proprietary-tier analyses that used one of these, and the CI completeness
test asserts each ID has a licence row in DATA_SOURCES.md."""

SOURCE_DATASET_IDS: tuple[str, ...] = (
    "user_upload",
    "in_house_standard",
    "gnps_public",
    "np_mrd",
    "np_mrd_fixtures",
    "bmrb",
    "nmrshiftdb2",
    "coconut",
    "lotus",
    "knapsack",
    "npass",
    "gbif",
    "itis",
)
"""The closed vocabulary of ``source_dataset`` strings.

Every spectrum contribution and provenance manifest names where its data
came from using one of these IDs, and every ID must have a licence row in
``docs/DATA_SOURCES.md`` (enforced by the CI completeness test).  Adding an
ID here is a licence decision and requires a DATA_SOURCES.md entry."""


def is_source_allowed_in_tier(source: str, tier: DataTier) -> bool:
    """Whether ``source`` may contribute to an analysis destined for ``tier``.

    Licence constraint (``docs/DATA_SOURCES.md`` sections 1 and 3): NP-MRD
    data is licensed CC-BY-NC by its original depositors, and the NP-MRD
    literature fixtures derive their terms from it.  Every source in
    :data:`NON_COMMERCIAL_SOURCES` is therefore allowed ONLY in the
    ``public`` tier for free academic use with attribution, and NEVER in
    ``proprietary`` or in any commercial context: not in commercial
    analytics, not in the proprietary corpus, and not in a model that is
    sold.  ``training_only`` may use them (internal training publishes and
    licenses nothing).  Pure and deterministic.
    """
    if tier == "proprietary":
        return source not in NON_COMMERCIAL_SOURCES
    return True