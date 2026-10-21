"""Smoke tests for citable analysis identifiers (ADR-0043).

The observable contract: any completed run has a canonical id, the citation
record carries its provenance fields, and re-minting the same run is
idempotent.  DOI registration is config-gated, so the default path must work
with no token present.
"""

from __future__ import annotations

from datetime import datetime, timezone

from phyto_shared.citation import (
    CitationRecord,
    MockDOIRegistrar,
    build_citation_record,
    canonical_id_for,
    doi_from_identifier,
    mint_analysis_doi,
)

_PROVENANCE = {
    "model_version": "orchestrator:0.1.0",
    "cost_model_version": "1.0.0",
    "provenance_manifest_hash": "a" * 64,
    "audit_chain_head": "b" * 64,
}


def _record(**overrides):
    kwargs = {
        "run_id": "run-123",
        "title": "Analysis of sample s1",
        "authors": ["alice@example.org"],
        "created_at": datetime(2026, 10, 1, tzinfo=timezone.utc),
        **_PROVENANCE,
    }
    kwargs.update(overrides)
    return build_citation_record(**kwargs)


class TestMintIdentifier:
    def test_mint_returns_canonical_identifier_for_run(self):
        assert mint_analysis_doi("run-123", {}, registrar=None) == (
            "phytospectra.run:run-123"
        )

    def test_canonical_id_is_stable(self):
        assert canonical_id_for("run-123") == canonical_id_for("run-123")

    def test_mint_without_config_uses_canonical_form(self, monkeypatch):
        monkeypatch.delenv("ZENODO_TOKEN", raising=False)
        # Passing no registrar at all means "resolve from the environment".
        assert mint_analysis_doi("run-abc", {}) == "phytospectra.run:run-abc"

    def test_registration_failure_falls_back_rather_than_raising(self):
        class Broken:
            def register(self, run_id, metadata):
                raise RuntimeError("zenodo is down")

        assert mint_analysis_doi("run-9", {}, registrar=Broken()) == (
            "phytospectra.run:run-9"
        )

    def test_registered_doi_is_returned_and_recognised(self):
        identifier = mint_analysis_doi("run-77", {}, registrar=MockDOIRegistrar())
        assert identifier.startswith("https://doi.org/")
        assert doi_from_identifier(identifier) == identifier
        assert doi_from_identifier("phytospectra.run:run-77") is None


class TestCitationRecord:
    def test_record_has_every_required_field(self):
        record = _record()
        assert isinstance(record, CitationRecord)
        assert record.canonical_id == "phytospectra.run:run-123"
        assert record.title
        assert record.authors == ["alice@example.org"]
        assert record.created_at.year == 2026
        assert record.model_version
        assert record.cost_model_version
        assert record.provenance_manifest_hash == "a" * 64
        assert record.audit_chain_head == "b" * 64
        assert record.citation_text

    def test_citation_text_is_ready_to_paste(self):
        record = _record()
        assert record.citation_text.startswith("@misc{phytospectra.run:run-123")
        assert "alice@example.org" in record.citation_text

    def test_minting_the_same_run_is_idempotent(self):
        """Re-minting yields the same identifier and no duplicate deposit."""
        registrar = MockDOIRegistrar()
        first = mint_analysis_doi("run-idem", {}, registrar=registrar)
        second = mint_analysis_doi("run-idem", {}, registrar=registrar)
        assert first == second
        assert registrar.calls == ["run-idem", "run-idem"]
