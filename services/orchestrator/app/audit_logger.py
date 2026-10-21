"""The chained audit logger is implemented in ``phyto_shared.audit_chain``.

It is re-exported here so the orchestrator's existing imports keep working
and so the module's public surface is unchanged.  The implementation moved
to the shared library because cohort analytics events (ADR-0048) are
appended to audit chains from every service that raises them; two copies
could drift, and a chain verifier that disagrees with the writer is not
tamper evidence.
"""

from phyto_shared.audit_chain import (  # noqa: F401  (re-exported API)
    GENESIS_HASH,
    ChainedAuditLogger,
)

__all__ = ["GENESIS_HASH", "ChainedAuditLogger"]
