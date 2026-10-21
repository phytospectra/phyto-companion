"""Auth service scaffolding tests (Step 6.5).

No authentication logic exists yet. These tests pin the health endpoint and the
explicit "not implemented" marker so the token layer cannot be mistaken for
being live.
"""

from __future__ import annotations

from auth.main import app
from fastapi.testclient import TestClient


class TestAuthServicePlaceholder:
    def test_health_reports_not_implemented(self):
        response = TestClient(app).get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["service"] == "auth"
        assert body["token_layer"] == "not_implemented"
