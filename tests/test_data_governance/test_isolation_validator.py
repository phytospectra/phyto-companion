"""Isolation-record governance validator (ADR-0056, Part 5).

Unit tests for `isolation.governance.validate_compound_tier`: each
rejection case and the happy path for each tier.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from isolation.governance import (
    NOT_CURATOR_REJECTION,
    PROPRIETARY_REJECTION,
    PUBLIC_REJECTION,
    validate_compound_tier,
)


def test_proprietary_with_unknown_abs_rejected():
    with pytest.raises(HTTPException) as excinfo:
        validate_compound_tier("proprietary", "unknown", actor_role="curator")
    assert excinfo.value.status_code == 422
    assert excinfo.value.detail == PROPRIETARY_REJECTION


def test_proprietary_with_pending_abs_rejected():
    with pytest.raises(HTTPException) as excinfo:
        validate_compound_tier("proprietary", "pending", actor_role="admin")
    assert excinfo.value.status_code == 422
    assert excinfo.value.detail == PROPRIETARY_REJECTION


def test_public_with_pending_or_unknown_abs_rejected():
    for status in ("pending", "unknown"):
        with pytest.raises(HTTPException) as excinfo:
            validate_compound_tier("public", status, actor_role="curator")
        assert excinfo.value.status_code == 422
        assert excinfo.value.detail == PUBLIC_REJECTION


def test_proprietary_by_non_curator_rejected():
    with pytest.raises(HTTPException) as excinfo:
        validate_compound_tier("proprietary", "clear", actor_role="user")
    assert excinfo.value.status_code == 422
    assert excinfo.value.detail == NOT_CURATOR_REJECTION


def test_public_by_non_curator_rejected():
    with pytest.raises(HTTPException) as excinfo:
        validate_compound_tier("public", "exempt", actor_role="user")
    assert excinfo.value.status_code == 422
    assert excinfo.value.detail == NOT_CURATOR_REJECTION


def test_happy_paths():
    # proprietary: curator + clear
    validate_compound_tier("proprietary", "clear", actor_role="curator")
    validate_compound_tier("proprietary", "clear", actor_role="admin")
    # public: curator + clear or exempt
    validate_compound_tier("public", "clear", actor_role="curator")
    validate_compound_tier("public", "exempt", actor_role="admin")
    # training_only: anyone, any status
    validate_compound_tier("training_only", "unknown", actor_role="user")
    validate_compound_tier("training_only", "pending", actor_role="user")


def test_training_only_write_needs_no_promotion_even_for_promotable_previous(
    monkeypatch=None,
):
    # A compound already in a promotable tier being (re-)written as
    # training_only is a demotion, not a promotion: allowed for a user.
    validate_compound_tier(
        "training_only", "unknown", previous_tier="public", actor_role="user"
    )