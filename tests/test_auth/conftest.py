"""Fixtures for the token-layer tests.

``services/auth`` is already aliased to ``auth`` by ``tests/conftest.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from auth.token_balance import MockTokenBalanceClient

from .helpers import FakePublisher, build_app

_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session", autouse=True)
def _ensure_auth_on_path():
    service_root = str(_ROOT / "services" / "auth")
    if service_root not in sys.path:
        sys.path.insert(0, service_root)
    yield


@pytest.fixture()
def publisher() -> FakePublisher:
    return FakePublisher()


@pytest.fixture()
def tokens() -> MockTokenBalanceClient:
    return MockTokenBalanceClient({"alice": 30})


@pytest.fixture()
def client(publisher, tokens) -> TestClient:
    return TestClient(build_app(publisher, tokens))
