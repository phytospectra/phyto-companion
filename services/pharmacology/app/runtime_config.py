"""Pharmacology service runtime configuration (extends ADR-0078's rule).

``get_knowledge_graph`` used to serve the in-memory networkx graph on
every plane; in real mode without a Neo4j URI that graph -- whose
traditional-use records are literal mock strings ("LOTUS (mock)") --
answered production requests while looking identical to a real
database. The fail-closed rule instead: real mode requires
``NEO4J_URI`` and refuses startup without one; the networkx graph
stays a dry-run-only tool. (The Neo4j backend itself is still Step 8
wiring: until it exists, real mode fails closed at startup with an
actionable message rather than pretending.)

Plane semantics (matching ``auth.config.AuthConfig``):
- ``PHYTO_ENV=local`` (default): dry-run, mocks allowed.
- ``PHYTO_ENV=cloud``: real mode unless ``PHYTO_DRY_RUN=1`` is set
  explicitly (bring-up only, reported as such).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

__all__ = ["PharmacologyConfig", "PharmacologyConfigError"]


class PharmacologyConfigError(RuntimeError):
    """Raised when a real deployment is missing required configuration."""


@dataclass(frozen=True)
class PharmacologyConfig:
    env: str
    dry_run: bool
    neo4j_uri: str | None

    @property
    def real_graph(self) -> bool:
        return not self.dry_run

    @property
    def mode(self) -> str:
        """The human-facing mode label reported by ``/health``."""
        return "local-mock" if self.dry_run else "cloud-real"

    @classmethod
    def from_env(cls) -> "PharmacologyConfig":
        env = os.getenv("PHYTO_ENV", "local")
        dry_run_default = env != "cloud"
        explicit = os.getenv("PHYTO_DRY_RUN")
        dry_run = dry_run_default if explicit is None else explicit == "1"
        return cls(
            env=env,
            dry_run=dry_run,
            neo4j_uri=os.getenv("NEO4J_URI") or None,
        )

    def validate(self) -> "PharmacologyConfig":
        """Fail loudly when real mode lacks a real dependency.

        Called by the runtime at construction so a misconfigured cloud
        deployment stops at startup.  Dry-run deliberately skips the
        check: that is the local plane's contract.
        """
        if self.dry_run:
            return self
        if not self.neo4j_uri:
            raise PharmacologyConfigError(
                "pharmacology service is in real mode (PHYTO_ENV="
                f"{self.env!r}, PHYTO_DRY_RUN=0) but NEO4J_URI is not "
                "set. Refusing to start rather than serve the in-memory "
                "networkx graph (its traditional-use records are mock "
                "strings) as a production knowledge graph. Set NEO4J_URI "
                "when the Neo4j backend is wired (Step 8), or run with "
                "PHYTO_DRY_RUN=1 on the local plane.",
            )
        return self
