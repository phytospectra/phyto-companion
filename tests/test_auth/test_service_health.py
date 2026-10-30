"""Health and 401 contracts across every real service factory.

Phase 3 (enterprise hardening), finding 6: the token layer must be wired into
*every* business service, and the contract must be tested per service — a
parametrized test over a hand-maintained list silently rots when a new service
lands.  ``test_every_business_service_wires_install_auth`` scans the service
tree structurally, so a new service without the middleware fails this module
even before anyone adds it to the route lists below.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from .service_runner import enforced_client

_ROOT = Path(__file__).resolve().parents[2]

# Services that carry the token layer.  ``auth`` IS the middleware owner and
# ``billing`` is an internal Pub/Sub consumer (unauthenticated webhook surface
# by design, ADR-0028); ``frontend`` is not a Python service.
_NON_TOKEN_SERVICES = {"auth", "billing", "frontend"}

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
    # Phase 3: these seven were wired with install_auth but never had the
    # 401 contract test (found in the enterprise survey).
    "calibration",
    "isolation",
    "lcms_preproc",
    "retention",
    "taxon_prior",
    "taxonomy",
    "nmr_preproc",
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
    "isolation": ("POST", "/isolation/records"),
}

# Routes on services that are wired with the middleware but have NO price in
# ROUTE_PRICING yet: unauthenticated requests must still be rejected with 401
# before the metering layer can even consider a price (ADR-0028 ordering).
UNPRICED_ROUTE = {
    "calibration": ("POST", "/calibrate/standards"),
    "lcms_preproc": ("POST", "/lcms-preproc/ingest"),
    "retention": ("POST", "/predict_ri"),
    "taxon_prior": ("POST", "/taxon_prior/build"),
    "taxonomy": ("POST", "/taxonomy/resolve"),
    "nmr_preproc": ("POST", "/nmr-preproc/preprocess"),
}


def test_every_business_service_wires_install_auth():
    """Structural guard: a new service cannot ship without the token layer.

    Scans ``services/*/app/main.py`` for the ``install_auth`` call.  Services
    in ``_NON_TOKEN_SERVICES`` are exempt by design (see the comment there);
    anything else must wire the middleware, and must appear in ``SERVICES``
    above so its health/401 contract stays tested.
    """
    missing: list[str] = []
    unlisted: list[str] = []
    for main_py in sorted((_ROOT / "services").glob("*/app/main.py")):
        service = main_py.parts[-3]
        if service in _NON_TOKEN_SERVICES:
            continue
        if "install_auth(" not in main_py.read_text():
            missing.append(service)
        if service not in SERVICES:
            unlisted.append(service)
    assert not missing, (
        f"services without install_auth: {missing} -- every business service "
        "must enforce the token layer (ADR-0028); exempting one requires a "
        "deliberate _NON_TOKEN_SERVICES entry with a written reason"
    )
    assert not unlisted, (
        f"services wired with install_auth but missing from the health/401 "
        f"contract test: {unlisted} -- add them to SERVICES and pick a route"
    )


@pytest.mark.parametrize("service", SERVICES)
def test_health_needs_no_auth_and_reports_dry_run(service):
    response = enforced_client(service).get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == service
    assert payload["dry_run"] is True


@pytest.mark.parametrize("service", sorted(PRICED_ROUTE))
def test_priced_route_without_auth_returns_401(service):
    method, path = PRICED_ROUTE[service]
    response = enforced_client(service).request(method, path, json={})
    assert response.status_code == 401, f"{service} {method} {path}"
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("service", sorted(UNPRICED_ROUTE))
def test_unpriced_service_still_rejects_anonymous_requests(service):
    """Auth runs before metering: no price is never an excuse for no auth.

    These six services are wired with the middleware but have no price in
    ROUTE_PRICING (the pricing table's explicit-unpriced policy resolves
    ``None``, and the metering layer rejects priced-but-unpriced requests
    with 503 -- see the networking guard).  Anonymous traffic must still be
    stopped at the auth layer with 401 + ``WWW-Authenticate: Bearer``.
    """
    method, path = UNPRICED_ROUTE[service]
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
