"""Health and 401 contracts across every real service factory."""

from __future__ import annotations

import pytest

from .service_runner import enforced_client

SERVICES = [
    "ingestion",
    "networking",
    "elucidation",
    "metabolomics",
    "pharmacology",
    "orchestrator",
    "literature",
    "llm",
    "nmr_search",
]

AUTH = {"Authorization": "Bearer mock:alice"}

# One priced route per service, taken from ROUTE_PRICING rather than guessed.
PRICED_ROUTE = {
    "ingestion": ("POST", "/ingest/peak_csv"),
    "elucidation": ("POST", "/elucidate/novel"),
    "metabolomics": ("POST", "/metabolomics/statistics"),
    "pharmacology": ("POST", "/pharmacology/targets"),
    "orchestrator": ("GET", "/runs/abc"),
    "literature": ("POST", "/literature/search"),
    "llm": ("POST", "/llm/explain"),
    "nmr_search": ("POST", "/nmr_search/peaks"),
}


@pytest.mark.parametrize("service", SERVICES)
def test_health_needs_no_auth_and_reports_dry_run(service):
    response = enforced_client(service).get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == service
    assert payload["dry_run"] is True


# One priced route per service, taken from ROUTE_PRICING rather than guessed.
PRICED_ROUTE = {
    "ingestion": ("POST", "/ingest/peak_csv"),
    "elucidation": ("POST", "/elucidate/novel"),
    "metabolomics": ("POST", "/metabolomics/statistics"),
    "pharmacology": ("POST", "/pharmacology/targets"),
    "orchestrator": ("GET", "/runs/abc"),
    "literature": ("POST", "/literature/search"),
    "llm": ("POST", "/llm/explain"),
    "nmr_search": ("POST", "/nmr_search/peaks"),
}


@pytest.mark.parametrize("service", sorted(PRICED_ROUTE))
def test_priced_route_without_auth_returns_401(service):
    method, path = PRICED_ROUTE[service]
    response = enforced_client(service).request(method, path, json={})
    assert response.status_code == 401, f"{service} {method} {path}"
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_networking_has_no_priced_route_and_rejects_unpriced_with_auth():
    """Regression guard for a real gap: /networks/* is not in ROUTE_PRICING.

    Enforcement therefore returns 503 rather than inventing a price. If someone
    prices networking, this test should be updated deliberately.
    """
    client = enforced_client("networking")
    response = client.post("/networks/hsqc", headers=AUTH, json={})
    assert response.status_code == 503, "networking gained a price; update this guard"
