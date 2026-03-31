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


# ---------------------------------------------------------------------------
# TestRateLimiting
# ---------------------------------------------------------------------------

class TestRateLimiting:
    """/api/chat must enforce a rate limit to prevent Anthropic API billing abuse.

    EXPECTED FIX: Add slowapi (or in-memory counter) rate limiting to the
    /api/chat route — 10 requests per 60 seconds per IP.
    """

    def test_chat_rate_limit_enforced(self, live_server_url):
        """11 rapid requests to /api/chat — the 11th must return 429."""
        url = f"{live_server_url}/api/chat"
        payload = {"trade": {"symbol": "BTCUSDT"}, "messages": [{"role": "user", "content": "hi"}]}

        responses = []
        with httpx.Client(timeout=10.0) as client:
            for _ in range(11):
                r = client.post(url, json=payload)
                responses.append(r.status_code)

        assert 429 in responses, (
            f"/api/chat accepted all 11 rapid requests (status codes: {responses}). "
            "No rate limiting is enforced — any caller can exhaust Anthropic API credits."
        )

    def test_chat_within_limit_succeeds(self, live_server_url):
        """A single request to /api/chat must not be rate-limited."""
        r = httpx.post(
            f"{live_server_url}/api/chat",
            json={"trade": {"symbol": "BTCUSDT"}, "messages": [{"role": "user", "content": "hi"}]},
            timeout=10.0,
        )
        assert r.status_code != 429, (
            f"/api/chat returned 429 on the first request — rate limit threshold is too low"
        )


# ---------------------------------------------------------------------------
# TestInputValidation
# ---------------------------------------------------------------------------

class TestInputValidation:
    """GET /api/candles/{symbol} must reject symbols that don't match ^[A-Z0-9]{2,20}$.

    EXPECTED FIX: Add a regex allowlist validator to the symbol path parameter
    in the get_candles handler in dashboard.py.
    """

    INVALID_SYMBOLS = [
        "../etc/passwd",
        "..%2Fetc%2Fpasswd",
        "A" * 21,
        "btcusdt",
        "BTC USDT",
        "BTC;USDT",
        "BTC/USDT",
        "BTC\x00USDT",
    ]

    VALID_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BTC", "AB"]

    @pytest.mark.parametrize("symbol", INVALID_SYMBOLS)
    def test_invalid_symbol_rejected(self, live_server_url, symbol):
        r = httpx.get(f"{live_server_url}/api/candles/{symbol}", timeout=5.0)
        assert r.status_code == 400, (
            f"GET /api/candles/{symbol!r} returned {r.status_code}, expected 400. "
            "Invalid symbol was not rejected by input validation."
        )

    @pytest.mark.parametrize("symbol", VALID_SYMBOLS)
    def test_valid_symbol_passes(self, live_server_url, symbol):
        r = httpx.get(f"{live_server_url}/api/candles/{symbol}", timeout=5.0)
        assert r.status_code != 400, (
            f"GET /api/candles/{symbol!r} returned 400 — valid symbol was incorrectly rejected"
        )


# ---------------------------------------------------------------------------
# TestInjectionAttacks
# ---------------------------------------------------------------------------

class TestInjectionAttacks:
    """Injection payloads in API inputs must be rejected with 400.

    EXPECTED FIX: Same symbol allowlist as TestInputValidation (^[A-Z0-9]{2,20}$).
    JSON structure attacks are covered by payload size validation from TestAPIProxyAbuse.
    """

    COMMAND_INJECTION = [
        "$(python -c 'import os')",
        "`id`",
        "BTCUSDT;python -c 'import socket'",
        "BTCUSDT|python -c 'import sys'",
        "BTCUSDT&&whoami",
    ]

    SQL_INJECTION = [
        "' OR 1=1--",
        "'; DROP TABLE orders;--",
        "1 UNION SELECT NULL--",
    ]

    HEADER_INJECTION = [
        "BTCUSDT\r\nX-Injected: evil",
        "BTCUSDT\nSet-Cookie: session=hijacked",
    ]

    @pytest.mark.parametrize("payload", COMMAND_INJECTION)
    def test_command_injection_rejected(self, live_server_url, payload):
        r = httpx.get(f"{live_server_url}/api/candles/{payload}", timeout=5.0)
        assert r.status_code == 400, (
            f"Command injection payload {payload!r} returned {r.status_code}, "
            "expected 400. Allowlist is not enforced."
        )

    @pytest.mark.parametrize("payload", SQL_INJECTION)
    def test_sql_injection_rejected(self, live_server_url, payload):
        r = httpx.get(f"{live_server_url}/api/candles/{payload}", timeout=5.0)
        assert r.status_code == 400, (
            f"SQL injection payload {payload!r} returned {r.status_code}, "
            "expected 400. Allowlist is not enforced."
        )

    @pytest.mark.parametrize("payload", HEADER_INJECTION)
    def test_header_injection_rejected(self, live_server_url, payload):
        r = httpx.get(f"{live_server_url}/api/candles/{payload}", timeout=5.0)
        assert r.status_code == 400, (
            f"Header injection payload {payload!r} returned {r.status_code}, "
            "expected 400. Allowlist is not enforced."
        )

    def test_deeply_nested_json_rejected(self, live_server_url):
        """100-level deep nested object in /api/chat body must return 400 or 413."""
        nested: object = "x"
        for _ in range(100):
            nested = {"a": nested}
        r = httpx.post(
            f"{live_server_url}/api/chat",
            json={"trade": nested, "messages": []},
            timeout=10.0,
        )
        assert r.status_code in (400, 413), (
            f"Deeply nested JSON returned {r.status_code}, expected 400 or 413"
        )

    def test_null_byte_in_chat_payload_rejected(self, live_server_url):
        """Null byte in /api/chat trade field must return 400."""
        r = httpx.post(
            f"{live_server_url}/api/chat",
            json={"trade": "data\x00injection", "messages": []},
            timeout=10.0,
        )
        assert r.status_code == 400, (
            f"Null byte in payload returned {r.status_code}, expected 400"
        )


# ---------------------------------------------------------------------------
# TestCORS
# ---------------------------------------------------------------------------

class TestCORS:
    """Cross-origin requests from untrusted origins must be blocked.
    Trusted localhost origins must be explicitly allowed.

    EXPECTED FIX: Add FastAPI CORSMiddleware with:
        allow_origins=["http://localhost:8080", "http://127.0.0.1:8080",
                       "http://localhost:8081", "http://127.0.0.1:8081"]
    """

    def test_evil_origin_not_wildcard_allowed(self, live_server_url):
        """A cross-origin request from an untrusted origin must not receive a wildcard ACAO header."""
        r = httpx.get(
            f"{live_server_url}/api/state",
            headers={"Origin": "https://evil.example.com"},
            timeout=5.0,
        )
        acao = r.headers.get("access-control-allow-origin", "")
        assert acao != "*", (
            "Access-Control-Allow-Origin: * is set — any origin can read dashboard data"
        )

    def test_localhost_origin_explicitly_allowed(self, live_server_url):
        """A request from http://localhost:8080 must receive an explicit ACAO header."""
        r = httpx.get(
            f"{live_server_url}/api/state",
            headers={"Origin": "http://localhost:8080"},
            timeout=5.0,
        )
        acao = r.headers.get("access-control-allow-origin", "")
        assert acao == "http://localhost:8080", (
            f"Expected ACAO: http://localhost:8080, got {acao!r}. "
            "CORSMiddleware is not configured for localhost origins."
        )

    def test_127_origin_explicitly_allowed(self, live_server_url):
        """A request from http://127.0.0.1:8080 must receive an explicit ACAO header."""
        r = httpx.get(
            f"{live_server_url}/api/state",
            headers={"Origin": "http://127.0.0.1:8080"},
            timeout=5.0,
        )
        acao = r.headers.get("access-control-allow-origin", "")
        assert acao == "http://127.0.0.1:8080", (
            f"Expected ACAO: http://127.0.0.1:8080, got {acao!r}. "
            "CORSMiddleware is not configured for 127.0.0.1 origins."
        )


# ---------------------------------------------------------------------------
# TestInformationExposure
# ---------------------------------------------------------------------------

class TestInformationExposure:
    """Error responses must not expose stack traces, file paths, or internal details.

    KNOWN BUG: dashboard.py ~line 621 returns {"error": str(e), "trace": traceback.format_exc()}
    from the /api/candles handler. Full Python tracebacks including file paths are exposed.

    EXPECTED FIX: Remove the "trace" key from error responses. Replace with
    {"error": "internal server error"} or a generic safe message.
    """

    def test_candles_error_does_not_expose_traceback(self, live_server_url):
        """When /api/candles raises an internal exception, the response must not contain a traceback."""
        from unittest.mock import patch

        with patch("trader.get_candles", side_effect=Exception("Error reading /Users/kevin/.env")):
            r = httpx.get(f"{live_server_url}/api/candles/BTCUSDT", timeout=5.0)

        body = r.text
        assert "trace" not in r.json(), (
            "Response contains a 'trace' key with a full Python traceback. "
            "This exposes internal file paths and code structure."
        )
        assert "Traceback" not in body, (
            "Response body contains a Python traceback string."
        )

    def test_candles_error_does_not_expose_file_paths(self, live_server_url):
        """Error responses from /api/candles must not contain filesystem paths."""
        from unittest.mock import patch

        with patch("trader.get_candles", side_effect=Exception("Internal error: /Users/kevin/Downloads/bitget_mtf_bot/.env")):
            r = httpx.get(f"{live_server_url}/api/candles/BTCUSDT", timeout=5.0)

        body = r.text
        assert "/Users/" not in body and "/home/" not in body, (
            f"Response body exposes a filesystem path: {body[:200]}"
        )

    def test_404_does_not_expose_traceback(self, live_server_url):
        """A 404 response must not contain a Python traceback."""
        r = httpx.get(f"{live_server_url}/nonexistent-route", timeout=5.0)
        assert r.status_code == 404
        assert "Traceback" not in r.text, (
            "404 response contains a Python traceback."
        )

    def test_candles_error_returns_safe_format(self, live_server_url):
        """Error responses from /api/candles must use a safe generic format without 'trace' key."""
        from unittest.mock import patch

        with patch("trader.get_candles", side_effect=Exception("something went wrong")):
            r = httpx.get(f"{live_server_url}/api/candles/BTCUSDT", timeout=5.0)

        data = r.json()
        assert "trace" not in data, (
            f"Response contains 'trace' key: {list(data.keys())}. Remove traceback from error response."
        )


# ---------------------------------------------------------------------------
# TestAPIProxyAbuse
# ---------------------------------------------------------------------------

class TestAPIProxyAbuse:
    """/api/chat must reject oversized payloads to prevent Anthropic API billing abuse.

    EXPECTED FIX: Add a payload size check at the start of the /api/chat handler:
        if len(str(body)) > 15_000:
            return JSONResponse({"error": "payload too large"}, status_code=413)
    """

    def test_oversized_messages_rejected(self, live_server_url):
        """messages field > 10,000 chars must return 413."""
        r = httpx.post(
            f"{live_server_url}/api/chat",
            json={
                "trade": {"symbol": "BTCUSDT"},
                "messages": [{"role": "user", "content": "A" * 10_001}],
            },
            timeout=10.0,
        )
        assert r.status_code == 413, (
            f"/api/chat accepted an oversized messages payload (status {r.status_code}). "
            "Any caller can trigger large Anthropic API requests at server's expense."
        )

    def test_oversized_trade_rejected(self, live_server_url):
        """trade field > 5,000 chars must return 413."""
        r = httpx.post(
            f"{live_server_url}/api/chat",
            json={
                "trade": {"symbol": "B" * 5_001},
                "messages": [],
            },
            timeout=10.0,
        )
        assert r.status_code == 413, (
            f"/api/chat accepted an oversized trade payload (status {r.status_code})."
        )

    def test_normal_payload_accepted(self, live_server_url):
        """A normal-sized payload must not return 413."""
        r = httpx.post(
            f"{live_server_url}/api/chat",
            json={
                "trade": {"symbol": "BTCUSDT", "entry": 100.0},
                "messages": [{"role": "user", "content": "What do you think of this trade?"}],
            },
            timeout=10.0,
        )
        assert r.status_code != 413, (
            f"/api/chat returned 413 for a normal payload — size limit is too low"
        )


# ---------------------------------------------------------------------------
# TestSecurityHeaders
# ---------------------------------------------------------------------------

class TestSecurityHeaders:
    """Every dashboard response must include HTTP security headers.

    EXPECTED FIX: Add a response middleware to dashboard.py:

        @app.middleware("http")
        async def add_security_headers(request, call_next):
            response = await call_next(request)
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' https://unpkg.com; "
                "style-src 'self' 'unsafe-inline'"
            )
            response.headers.pop("server", None)
            return response
    """

    CHECKED_ENDPOINTS = [
        "/api/state",
        "/api/ig/state",
    ]

    @pytest.mark.parametrize("path", CHECKED_ENDPOINTS)
    def test_x_frame_options_present(self, live_server_url, path):
        r = httpx.get(f"{live_server_url}{path}", timeout=5.0)
        assert r.headers.get("x-frame-options") == "DENY", (
            f"{path} missing X-Frame-Options: DENY header. "
            "Dashboard can be embedded in an iframe (clickjacking risk)."
        )

    @pytest.mark.parametrize("path", CHECKED_ENDPOINTS)
    def test_x_content_type_options_present(self, live_server_url, path):
        r = httpx.get(f"{live_server_url}{path}", timeout=5.0)
        assert r.headers.get("x-content-type-options") == "nosniff", (
            f"{path} missing X-Content-Type-Options: nosniff header."
        )

    @pytest.mark.parametrize("path", CHECKED_ENDPOINTS)
    def test_referrer_policy_present(self, live_server_url, path):
        r = httpx.get(f"{live_server_url}{path}", timeout=5.0)
        assert r.headers.get("referrer-policy") == "no-referrer", (
            f"{path} missing Referrer-Policy: no-referrer header."
        )

    @pytest.mark.parametrize("path", CHECKED_ENDPOINTS)
    def test_csp_present_and_no_unsafe_wildcard(self, live_server_url, path):
        r = httpx.get(f"{live_server_url}{path}", timeout=5.0)
        csp = r.headers.get("content-security-policy", "")
        assert csp, (
            f"{path} has no Content-Security-Policy header."
        )
        assert "script-src *" not in csp and "default-src *" not in csp, (
            f"{path} CSP contains a wildcard source: {csp}"
        )

    @pytest.mark.parametrize("path", CHECKED_ENDPOINTS)
    def test_server_header_not_exposed(self, live_server_url, path):
        r = httpx.get(f"{live_server_url}{path}", timeout=5.0)
        server = r.headers.get("server", "")
        assert "uvicorn" not in server.lower(), (
            f"{path} exposes server software in Server header: {server!r}"
        )
