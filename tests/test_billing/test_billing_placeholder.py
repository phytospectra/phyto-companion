"""Billing service scaffolding tests (Step 6.5)."""

from __future__ import annotations

from billing.main import app
from fastapi.testclient import TestClient


class TestBillingServicePlaceholder:
    def test_health_reports_not_implemented(self):
        response = TestClient(app).get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["service"] == "billing"
        assert body["token_layer"] == "not_implemented"
