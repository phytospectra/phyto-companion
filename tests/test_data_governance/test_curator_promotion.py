"""Curator-only promotion rule (ADR-0056, Part 5): role store semantics
and enforcement through the isolation service endpoints.

Endpoint tests use the real app with the enforced-but-dry-run auth layer
(mock Firebase tokens ``mock:<uid>``), so the tests cover the whole
pipeline: middleware -> role store -> governance validator -> store.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from auth.roles import InMemoryRoleStore, can_promote, role_for
from phyto_shared.audit_chain import ChainedAuditLogger


def _isolation_main():
    # Fresh import so module-level lru_caches (roles, store, audit) are
    # clean for this test module.
    import isolation.main as main

    return importlib.reload(main)


def _record_payload(**overrides):
    payload = {
        "extract_id": "EXT-1",
        "species": "Melicope semicarpoides",
        "mass_starting_g": 12.5,
        "extraction_solvent": "MeOH",
        "fractions": [
            {"fraction_id": "F1", "polarity_tier": "mid", "solvent": "EtOAc"}
        ],
        "compounds": [
            {
                "compound_id": "C1",
                "present_in_fractions": ["F1"],
                "data_tier": "training_only",
            }
        ],
    }
    payload.update(overrides)
    return payload


@pytest.fixture()
def client(monkeypatch):
    main = _isolation_main()
    roles = InMemoryRoleStore({"cur": "curator", "adm": "admin"})
    monkeypatch.setattr(main, "get_roles", lambda: roles)
    from auth.config import AuthConfig

    config = AuthConfig(
        project_id="test",
        firebase_credentials_path=None,
        dry_run=True,
        default_free_tokens=100,
        enabled=True,
    )
    return TestClient(main.create_app(config)), main, roles


class TestRoleStore:
    def test_can_promote_only_curator_and_admin(self):
        assert can_promote("curator") is True
        assert can_promote("admin") is True
        assert can_promote("user") is False

    def test_unknown_user_defaults_to_least_privilege(self):
        store = InMemoryRoleStore()
        assert role_for(store, "nobody") == "user"
        assert can_promote(role_for(store, "nobody")) is False

    def test_unknown_role_value_rejected(self):
        store = InMemoryRoleStore()
        with pytest.raises(ValueError):
            store.set_role("u1", "emperor")


class TestRejectedPromotionAudit:
    def test_rejected_promotion_is_audited_with_accepted_false(self, client, tmp_path, monkeypatch):
        """ADR-0056: every attempted violation lands in the hash-chained
        audit log with ``accepted: false`` -- not only accepted changes."""
        tc, main, _roles = client
        audit_path = tmp_path / "governance_audit.jsonl"
        monkeypatch.setattr(
            main, "get_audit_log", lambda: ChainedAuditLogger(audit_path)
        )
        resp = tc.post(
            "/isolation/records",
            json=_record_payload(
                abs_status="unknown",
                compounds=[
                    {
                        "compound_id": "C1",
                        "present_in_fractions": ["F1"],
                        "data_tier": "proprietary",
                    }
                ],
            ),
            headers={"Authorization": "Bearer mock:alice"},
        )
        assert resp.status_code == 422, resp.text
        records = ChainedAuditLogger(audit_path).records()
        assert records, "rejected promotion must produce an audit record"
        rejected = [r for r in records if r["detail"]["accepted"] is False]
        assert rejected, "the audit record must carry accepted=false"
        record = rejected[-1]
        assert record["event"] == "isolation_tier_change"
        assert record["detail"]["tier"] == "proprietary"
        assert record["detail"]["compound_id"] == "C1"
        assert "ABS-clear" in record["detail"]["reason"]
        # The audit record itself is tamper-evident (hash-chained).
        assert ChainedAuditLogger(audit_path).verify_chain() is True

    def test_accepted_and_rejected_attempts_chain_together(self, client, tmp_path, monkeypatch):
        """The audit trail is one append-only chain covering BOTH accepted
        tier changes and rejected attempts, in order."""
        tc, main, _roles = client
        audit_path = tmp_path / "governance_audit.jsonl"
        monkeypatch.setattr(
            main, "get_audit_log", lambda: ChainedAuditLogger(audit_path)
        )
        # Rejected attempt (user, unknown ABS, proprietary).
        tc.post(
            "/isolation/records",
            json=_record_payload(
                compounds=[
                    {
                        "compound_id": "C1",
                        "present_in_fractions": ["F1"],
                        "data_tier": "proprietary",
                    }
                ],
            ),
            headers={"Authorization": "Bearer mock:alice"},
        )
        # Accepted change (curator, clear ABS, proprietary).
        ok = tc.post(
            "/isolation/records",
            json=_record_payload(
                collection_country="TH",
                collection_permit="PIC/MAT-2026-001",
                abs_status="clear",
                compounds=[
                    {
                        "compound_id": "C1",
                        "present_in_fractions": ["F1"],
                        "data_tier": "proprietary",
                    }
                ],
            ),
            headers={"Authorization": "Bearer mock:cur"},
        )
        assert ok.status_code == 201, ok.text
        records = ChainedAuditLogger(audit_path).records()
        accepted_flags = [r["detail"]["accepted"] for r in records]
        assert accepted_flags == [False, True]
        assert ChainedAuditLogger(audit_path).verify_chain() is True


class TestCuratorPromotionEndpoint:
    def test_user_can_create_training_only_record(self, client):
        tc, _main, _roles = client
        resp = tc.post(
            "/isolation/records",
            json=_record_payload(),
            headers={"Authorization": "Bearer mock:alice"},
        )
        assert resp.status_code == 201, resp.text
        record = resp.json()["record"]
        assert record["abs_status"] == "unknown"
        assert record["compounds"][0]["data_tier"] == "training_only"

    def test_user_cannot_promote_to_public(self, client):
        tc, _main, _roles = client
        resp = tc.post(
            "/isolation/records",
            json=_record_payload(
                abs_status="clear",
                compounds=[
                    {
                        "compound_id": "C1",
                        "present_in_fractions": ["F1"],
                        "data_tier": "public",
                    }
                ],
            ),
            headers={"Authorization": "Bearer mock:alice"},
        )
        assert resp.status_code == 422, resp.text
        assert "curator" in resp.json()["detail"]

    def test_curator_promotes_to_proprietary_with_clear_abs(self, client):
        tc, _main, _roles = client
        resp = tc.post(
            "/isolation/records",
            json=_record_payload(
                collection_country="TH",
                collection_permit="PIC/MAT-2026-001",
                abs_status="clear",
                compounds=[
                    {
                        "compound_id": "C1",
                        "present_in_fractions": ["F1"],
                        "data_tier": "proprietary",
                    }
                ],
            ),
            headers={"Authorization": "Bearer mock:cur"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["record"]["compounds"][0]["data_tier"] == "proprietary"

    def test_curator_promotes_training_only_record_to_public_on_update(self, client):
        tc, main, roles = client
        # The record owner (alice, role "user") creates a training_only record.
        created = tc.post(
            "/isolation/records",
            json=_record_payload(abs_status="clear"),
            headers={"Authorization": "Bearer mock:alice"},
        )
        assert created.status_code == 201
        record_id = created.json()["record"]["record_id"]

        # The owner cannot change the tier: owner, but not a curator.
        denied = tc.put(
            f"/isolation/records/{record_id}",
            json=_record_payload(
                abs_status="clear",
                compounds=[
                    {
                        "compound_id": "C1",
                        "present_in_fractions": ["F1"],
                        "data_tier": "public",
                    }
                ],
            ),
            headers={"Authorization": "Bearer mock:alice"},
        )
        assert denied.status_code == 422, denied.text
        assert "curator" in denied.json()["detail"]

        # Records are per-user, so promotion happens on the curator's own
        # copy of the workflow: the curator creates the same record and
        # its tier change goes through.
        cur_created = tc.post(
            "/isolation/records",
            json=_record_payload(abs_status="clear"),
            headers={"Authorization": "Bearer mock:cur"},
        )
        assert cur_created.status_code == 201
        cur_record_id = cur_created.json()["record"]["record_id"]
        promoted = tc.put(
            f"/isolation/records/{cur_record_id}",
            json=_record_payload(
                abs_status="clear",
                compounds=[
                    {
                        "compound_id": "C1",
                        "present_in_fractions": ["F1"],
                        "data_tier": "public",
                    }
                ],
            ),
            headers={"Authorization": "Bearer mock:cur"},
        )
        assert promoted.status_code == 200, promoted.text
        assert promoted.json()["record"]["compounds"][0]["data_tier"] == "public"