"""Citable analysis identifiers (ADR-0043).

Every completed analysis mints a stable, citable identifier.  Citations
are the primary acquisition loop for academic tools: every citation is a
permanent advertisement to exactly the right audience.  Citation
retrieval is never metered.

Two identifier forms:

* canonical: ``phytospectra.run:<run_id>`` -- always available, stable.
* DOI: ``https://doi.org/...`` -- only when DOI registration is
  configured (``ZENODO_TOKEN``).  Registration is metadata-only (no data
  files) and idempotent per ``run_id``; it must never block or fail a
  completed run.

The citation embeds its own provenance (model version, cost-model
version, manifest hash, audit chain head) so a citation is
self-verifying: a reader can re-derive the manifest hash and check the
audit chain without trusting the platform.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "CitationRecord",
    "canonical_id_for",
    "doi_from_identifier",
    "render_bibtex",
    "render_plaintext",
    "build_citation_record",
    "mint_analysis_doi",
    "DOIRegistrar",
    "MockDOIRegistrar",
    "ZenodoRegistrar",
    "registrar_from_env",
]


logger = logging.getLogger("phyto.citation")

_APP_BASE_URL = os.environ.get("PHYTO_APP_BASE_URL", "https://app.phytospectra.com")

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class CitationRecord(BaseModel):
    """The citable record for one completed analysis run."""

    model_config = ConfigDict(extra="forbid")

    canonical_id: str = Field(..., pattern=r"^phytospectra\.run:\S+$")
    doi: str | None = Field(
        default=None, description="DOI URL when registered, else null"
    )
    title: str
    authors: list[str] = Field(..., min_length=1)
    created_at: datetime
    model_version: str
    cost_model_version: str
    provenance_manifest_hash: str = Field(..., pattern=_SHA256_PATTERN)
    audit_chain_head: str = Field(..., pattern=_SHA256_PATTERN)
    citation_text: str = Field(
        ..., description="Ready-to-paste BibTeX + plaintext snippet"
    )
    share_url: str | None = Field(
        default=None,
        description="Read-only share URL when one exists for the run; "
        "an alternate access link in the citation",
    )


def canonical_id_for(run_id: str) -> str:
    return f"phytospectra.run:{run_id}"


def render_bibtex(record: CitationRecord) -> str:
    """A valid BibTeX ``@misc`` entry for the record."""
    authors = " and ".join(record.authors)
    year = record.created_at.year
    note = (
        f"Analysis {record.canonical_id}; model {record.model_version}; "
        f"cost model {record.cost_model_version}; "
        f"provenance {record.provenance_manifest_hash[:12]}"
    )
    url = record.doi or f"{_APP_BASE_URL}/runs/{_run_id_of(record.canonical_id)}"
    lines = [
        "@misc{" + record.canonical_id + ",",
        f"  title = {{{record.title}}},",
        f"  author = {{{authors}}},",
        f"  year = {{{year}}},",
        f"  howpublished = {{Phytospectra}},",
        f"  note = {{{note}}},",
        f"  url = {{{url}}}",
        "}",
    ]
    return "\n".join(lines)


def render_plaintext(record: CitationRecord) -> str:
    authors = ", ".join(record.authors)
    run_id = _run_id_of(record.canonical_id)
    link = record.doi or f"{_APP_BASE_URL}/runs/{run_id}"
    return (
        f"{authors} ({record.created_at.year}). {record.title}. "
        f"Phytospectra analysis {record.canonical_id}. {link}"
    )


def _run_id_of(canonical_id: str) -> str:
    return canonical_id.removeprefix("phytospectra.run:")


def build_citation_record(
    *,
    run_id: str,
    title: str,
    authors: list[str],
    created_at: datetime | None = None,
    model_version: str,
    cost_model_version: str,
    provenance_manifest_hash: str,
    audit_chain_head: str,
    doi: str | None = None,
    share_url: str | None = None,
) -> CitationRecord:
    """Assemble a CitationRecord and render its ready-to-paste text."""
    record = CitationRecord(
        canonical_id=canonical_id_for(run_id),
        doi=doi,
        title=title,
        authors=authors,
        created_at=created_at or datetime.now(timezone.utc),
        model_version=model_version,
        cost_model_version=cost_model_version,
        provenance_manifest_hash=provenance_manifest_hash,
        audit_chain_head=audit_chain_head,
        citation_text="",  # rendered below
        share_url=share_url,
    )
    return record.model_copy(
        update={
            "citation_text": render_bibtex(record) + "\n\n" + render_plaintext(record)
        }
    )


# ---------------------------------------------------------------------------
# DOI registration (deferred, gated by config -- ADR-0043)
# ---------------------------------------------------------------------------


class DOIRegistrar(Protocol):
    """Registers metadata-only DOI deposits, idempotently per run."""

    def register(self, run_id: str, metadata: dict[str, Any]) -> str | None:
        """Return the DOI URL, or None if registration did not happen."""


class MockDOIRegistrar:
    """Deterministic in-process registrar for tests and dry-run mode.

    Idempotent by ``run_id``: the second registration of the same run
    returns the existing deposit instead of creating a duplicate.
    """

    def __init__(self) -> None:
        self._deposits: dict[str, str] = {}
        self.calls: list[str] = []

    def register(self, run_id: str, metadata: dict[str, Any]) -> str | None:
        self.calls.append(run_id)
        if run_id not in self._deposits:
            self._deposits[run_id] = f"https://doi.org/10.5281/phytospectra.{run_id}"
        return self._deposits[run_id]


class ZenodoRegistrar:
    """Zenodo deposition client (metadata-only, no data files).

    Idempotency: before creating a deposit the registrar searches Zenodo
    for an existing deposition whose keywords contain ``run_id`` and
    returns its DOI; ``run_id`` is the dedup key (ADR-0043).  All failures
    are the caller's problem to log -- registration must never fail a
    completed run.

    Uses stdlib ``urllib`` only: the local plane must not grow an HTTP
    dependency for a cloud-gated feature.
    """

    def __init__(self, token: str, api_url: str = "https://zenodo.org/api"):
        self._token = token
        self._api = api_url.rstrip("/")

    def register(self, run_id: str, metadata: dict[str, Any]) -> str | None:
        existing = self._find_existing(run_id)
        if existing:
            return existing
        deposit_id = self._create_deposit(run_id, metadata)
        return self._publish(deposit_id)

    # -- Zenodo API calls --------------------------------------------------
    def _request(self, method: str, path: str, payload: dict | None = None):
        url = f"{self._api}{path}?access_token={self._token}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            url, data=data, method=method,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
        return json.loads(body) if body.strip() else {}

    def _find_existing(self, run_id: str) -> str | None:
        """Search deposited records by the run_id keyword (dedup key)."""
        try:
            result = self._request(
                "GET", f"/deposits?q=keywords:\"{run_id}\"&size=5"
            )
            for hit in result.get("hits", {}).get("hits", []):
                doi = hit.get("doi") or hit.get("metadata", {}).get("doi")
                if doi:
                    return f"https://doi.org/{doi}"
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            logger.debug("Zenodo dedup search failed for %s", run_id, exc_info=True)
        return None

    def _create_deposit(self, run_id: str, metadata: dict[str, Any]) -> str:
        creators = [
            {"name": author} for author in metadata.get("authors") or ["Anonymous"]
        ]
        payload = {
            "metadata": {
                "title": metadata.get("title") or f"Phytospectra analysis {run_id}",
                "upload_type": "publication",
                "publication_type": "annotation",
                "description": metadata.get("description")
                or "Analysis record minted by Phytospectra.",
                "creators": creators,
                "keywords": [run_id, "phytospectra"],
            }
        }
        result = self._request("POST", "/deposits", payload)
        return str(result["id"])

    def _publish(self, deposit_id: str) -> str | None:
        result = self._request("POST", f"/deposits/{deposit_id}/actions/publish")
        doi = result.get("doi")
        return f"https://doi.org/{doi}" if doi else None


def registrar_from_env() -> DOIRegistrar | None:
    """A registrar when DOI registration is configured, else None."""
    token = os.environ.get("ZENODO_TOKEN")
    if not token:
        return None
    return ZenodoRegistrar(token, os.environ.get("ZENODO_API_URL", "https://zenodo.org/api"))


def mint_analysis_doi(
    run_id: str,
    metadata: dict[str, Any],
    *,
    registrar: DOIRegistrar | None | object = ...,
) -> str:
    """Mint the citable identifier for a completed run.

    Returns the DOI URL when registration is configured and succeeds,
    otherwise the canonical ``phytospectra.run:<run_id>`` form.  Without
    ``ZENODO_TOKEN`` this logs that DOI registration is not configured
    and returns the canonical form.  Registration failures are logged,
    never raised: a completed run must not fail because a third party was
    down (ADR-0043).  Use ``doi_from_identifier`` to separate the two
    forms afterwards.
    """
    from .token_pricing import COST_MODEL_VERSION  # local import: avoid cycle at module load

    effective: DOIRegistrar | None
    if registrar is ...:
        effective = registrar_from_env()
    else:
        effective = registrar  # type: ignore[assignment]

    metadata = {
        **metadata,
        "cost_model_version": metadata.get(
            "cost_model_version", COST_MODEL_VERSION
        ),
        "run_id": run_id,
    }

    if effective is None:
        logger.info("DOI registration not configured; using canonical id for %s", run_id)
        return canonical_id_for(run_id)

    try:
        doi = effective.register(run_id, metadata)
    except Exception:  # third-party outage must not fail the run
        logger.exception("DOI registration failed for %s; keeping canonical id", run_id)
        return canonical_id_for(run_id)
    return doi or canonical_id_for(run_id)


def doi_from_identifier(identifier: str) -> str | None:
    """The DOI URL when ``identifier`` is the registered DOI form, else None."""
    return identifier if identifier.startswith("https://doi.org/") else None
