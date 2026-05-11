"""
bybit_client.py — Authenticated Bybit V5 REST API Client

Handles:
  - HMAC-SHA256 signature (Bybit V5: timestamp + api_key + recv_window + body/queryString → hex)
  - X-BAPI-* headers
  - Full error body shown on any failure (no silent 400s)

Mirrors the public surface of bitget_client.py so bybit.py can be a near
structural clone of bitget.py.
"""

import time
import hmac
import hashlib
import json
import logging
import requests

from config_bybit import API_KEY, API_SECRET, BASE_URL, RECV_WINDOW

logger = logging.getLogger(__name__)

_session = requests.Session()


# ── Signature ─────────────────────────────────────────────────────── #

def _sign(timestamp: str, payload: str) -> str:
    """
    Bybit V5 signature:
      message = timestamp + api_key + recv_window + payload
      payload = queryString (GET) or raw JSON body (POST)
      sign    = HMAC-SHA256(secret, message).hexdigest()
    """
    message = timestamp + API_KEY + RECV_WINDOW + payload
    mac = hmac.new(
        API_SECRET.encode("utf-8"),
        message.encode("utf-8"),
        digestmod=hashlib.sha256,
    )
    return mac.hexdigest()


def _build_headers(payload: str) -> dict:
    ts = str(int(time.time() * 1000))
    return {
        "X-BAPI-API-KEY":     API_KEY,
        "X-BAPI-SIGN":        _sign(ts, payload),
        "X-BAPI-TIMESTAMP":   ts,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW,
        "X-BAPI-SIGN-TYPE":   "2",
        "Content-Type":       "application/json",
    }


# ── Response handling ─────────────────────────────────────────────── #

def _handle(resp: requests.Response, url: str) -> dict:
    """
    Parse response. Always show the body on any error so we know what Bybit says.
    Bybit V5 success is retCode == 0; non-zero is an API-level error even on HTTP 200.
    """
    try:
        data = resp.json()
    except Exception:
        resp.raise_for_status()
        return {}

    if not resp.ok:
        code = data.get("retCode", resp.status_code)
        msg  = data.get("retMsg", data.get("message", resp.text))
        raise RuntimeError(
            f"Bybit HTTP {resp.status_code} [{code}]: {msg}\n"
            f"  URL: {url}\n"
            f"  Tip: {_hint(code)}"
        )

    code = data.get("retCode", 0)
    if code != 0:
        msg = data.get("retMsg", "unknown error")
        raise RuntimeError(
            f"Bybit API error [{code}]: {msg}\n"
            f"  URL: {url}\n"
            f"  Tip: {_hint(code)}"
        )

    return data


def _hint(code) -> str:
    code_str = str(code)
    hints = {
        "10001":  "Invalid params — check field names/types against Bybit V5 docs",
        "10002":  "Request timestamp out of recv_window — check system clock",
        "10003":  "Invalid API key — regenerate on Bybit",
        "10004":  "Invalid signature — check API_SECRET has no extra spaces",
        "10005":  "Permission denied — enable derivatives trade permission on API key",
        "10006":  "Rate limit exceeded — back off and retry",
        "10010":  "IP not whitelisted — add your IP in Bybit API settings",
        "10016":  "Service unavailable / system maintenance",
        "110003": "Order price is out of permitted range",
        "110004": "Insufficient wallet balance",
        "110007": "Insufficient available balance for this order",
        "110017": "Position quantity exceeds maximum allowed",
        "110025": "Position idx not match position mode (one-way vs hedge)",
        "110043": "Leverage not modified (same as current value) — safe to ignore",
        "30084":  "Position mode is not modified — safe to ignore",
    }
    return hints.get(code_str, "See https://bybit-exchange.github.io/docs/v5/error for code details")


# ── Public methods ────────────────────────────────────────────────── #

def get(path: str, params: dict | None = None) -> dict:
    """Authenticated GET. Bybit V5 signs queryString."""
    qs = "&".join(f"{k}={v}" for k, v in (params or {}).items())
    url = BASE_URL + path + (("?" + qs) if qs else "")
    headers = _build_headers(qs)
    logger.debug(f"GET {url}")
    resp = _session.get(url, headers=headers, timeout=15)
    return _handle(resp, url)


def post(path: str, body: dict) -> dict:
    """Authenticated POST. Bybit V5 signs raw JSON body."""
    body_str = json.dumps(body, separators=(",", ":"))  # compact JSON, no spaces
    url      = BASE_URL + path
    headers  = _build_headers(body_str)
    logger.debug(f"POST {url}")
    resp = _session.post(url, headers=headers, data=body_str, timeout=15)
    return _handle(resp, url)


def get_public(path: str, params: dict | None = None) -> dict:
    """Unauthenticated GET for market data."""
    qs  = ("?" + "&".join(f"{k}={v}" for k, v in params.items())) if params else ""
    url = BASE_URL + path + qs
    resp = _session.get(url, timeout=15)
    return _handle(resp, url)
