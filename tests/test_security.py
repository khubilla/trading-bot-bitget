"""
tests/test_security.py — Security regression tests.

All tests in this file assert DESIRED SECURE BEHAVIOR.
They are expected to FAIL until the corresponding security fixes are applied.

Run:
    pytest tests/test_security.py -v
    pytest tests/test_security.py::TestAuthentication -v
"""
import httpx
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ENDPOINTS = [
    ("GET",  "/"),
    ("GET",  "/api/state"),
    ("GET",  "/api/candles/BTCUSDT"),
    ("GET",  "/api/entry-chart"),
    ("GET",  "/api/trade-chart"),
    ("GET",  "/api/ig/state"),
    ("POST", "/api/chat"),
]

# ---------------------------------------------------------------------------
# TestAuthentication
# ---------------------------------------------------------------------------

class TestAuthentication:
    """All endpoints must return 401 when no Authorization header is sent.

    EXPECTED FIX: Add bearer-token middleware to dashboard.py that reads
    DASHBOARD_API_KEY from .env and rejects requests missing
    'Authorization: Bearer <token>'.
    """

    def test_root_requires_auth(self, live_server_url):
        r = httpx.get(f"{live_server_url}/")
        assert r.status_code == 401, (
            f"GET / returned {r.status_code} — endpoint is unauthenticated"
        )

    def test_api_state_requires_auth(self, live_server_url):
        r = httpx.get(f"{live_server_url}/api/state")
        assert r.status_code == 401, (
            f"GET /api/state returned {r.status_code} — endpoint is unauthenticated"
        )

    def test_api_candles_requires_auth(self, live_server_url):
        r = httpx.get(f"{live_server_url}/api/candles/BTCUSDT")
        assert r.status_code == 401, (
            f"GET /api/candles/BTCUSDT returned {r.status_code} — endpoint is unauthenticated"
        )

    def test_api_entry_chart_requires_auth(self, live_server_url):
        r = httpx.get(f"{live_server_url}/api/entry-chart")
        assert r.status_code == 401, (
            f"GET /api/entry-chart returned {r.status_code} — endpoint is unauthenticated"
        )

    def test_api_trade_chart_requires_auth(self, live_server_url):
        r = httpx.get(f"{live_server_url}/api/trade-chart")
        assert r.status_code == 401, (
            f"GET /api/trade-chart returned {r.status_code} — endpoint is unauthenticated"
        )

    def test_api_ig_state_requires_auth(self, live_server_url):
        r = httpx.get(f"{live_server_url}/api/ig/state")
        assert r.status_code == 401, (
            f"GET /api/ig/state returned {r.status_code} — endpoint is unauthenticated"
        )

    def test_api_chat_requires_auth(self, live_server_url):
        r = httpx.post(f"{live_server_url}/api/chat", json={"trade": {}, "messages": []})
        assert r.status_code == 401, (
            f"POST /api/chat returned {r.status_code} — endpoint is unauthenticated"
        )

    def test_valid_token_grants_access(self, live_server_url):
        """A request with a valid Bearer token must NOT return 401.

        EXPECTED FIX: Once auth is implemented, set DASHBOARD_API_KEY=test-token
        in the test environment and pass it here.
        """
        r = httpx.get(
            f"{live_server_url}/api/state",
            headers={"Authorization": "Bearer test-token"},
        )
        assert r.status_code != 401, (
            "Valid token was rejected — auth middleware is not accepting correct tokens"
        )
