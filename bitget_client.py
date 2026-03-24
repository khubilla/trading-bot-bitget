"""
bitget_client.py — Authenticated Bitget REST API v2 Client

Handles:
  - HMAC-SHA256 signature (timestamp + METHOD + path + body → Base64)
  - paptrading: 1 header injected automatically in DEMO_MODE
  - Full error body shown on any failure (no more silent 400s)
"""

import time
import hmac
import hashlib
import base64
import json
import logging
import requests

from config import API_KEY, API_SECRET, API_PASSPHRASE, BASE_URL, DEMO_MODE

logger = logging.getLogger(__name__)

_session = requests.Session()


# ── Signature ─────────────────────────────────────────────────────── #

def _sign(timestamp: str, method: str, request_path: str, body: str = "") -> str:
    """
    message = timestamp + METHOD.upper() + requestPath + body
    sign    = Base64( HMAC-SHA256(secret, message) )
    """
    message = timestamp + method.upper() + request_path + (body or "")
    mac = hmac.new(
        API_SECRET.encode("utf-8"),
        message.encode("utf-8"),
        digestmod=hashlib.sha256,
    )
    return base64.b64encode(mac.digest()).decode("utf-8")


def _build_headers(method: str, path: str, body: str = "") -> dict:
    ts = str(int(time.time() * 1000))
    headers = {
        "ACCESS-KEY":        API_KEY,
        "ACCESS-SIGN":       _sign(ts, method, path, body),
        "ACCESS-PASSPHRASE": API_PASSPHRASE,
        "ACCESS-TIMESTAMP":  ts,
        "Content-Type":      "application/json",
        "locale":            "en-US",
    }
    if DEMO_MODE:
        headers["paptrading"] = "1"  # Bitget requires this for ALL demo API calls
    return headers


# ── Response handling ─────────────────────────────────────────────── #

def _handle(resp: requests.Response, url: str) -> dict:
    """
    Parse response. Always show the body on any error so we know what Bitget says.
    """
    # Try to parse JSON regardless of status code — Bitget often returns
    # error details in JSON even on HTTP 400/401/403
    try:
        data = resp.json()
    except Exception:
        # Not JSON — show raw text
        resp.raise_for_status()
        return {}

    # HTTP error — raise with Bitget's actual message
    if not resp.ok:
        code = data.get("code", resp.status_code)
        msg  = data.get("msg", data.get("message", resp.text))
        raise RuntimeError(
            f"Bitget HTTP {resp.status_code} [{code}]: {msg}\n"
            f"  URL: {url}\n"
            f"  Tip: {_hint(str(code))}"
        )

    # HTTP OK but Bitget-level error
    code = data.get("code", "00000")
    if code != "00000":
        msg = data.get("msg", "unknown error")
        raise RuntimeError(
            f"Bitget API error [{code}]: {msg}\n"
            f"  URL: {url}\n"
            f"  Tip: {_hint(code)}"
        )

    return data


def _hint(code: str) -> str:
    hints = {
        "40001": "Invalid signature — check API_SECRET has no extra spaces",
        "40002": "Invalid ACCESS-TIMESTAMP — check your system clock is accurate",
        "40003": "Wrong passphrase — check API_PASSPHRASE matches what you set on Bitget",
        "40004": "API key expired or deleted — regenerate on Bitget",
        "40007": "IP not whitelisted — add your IP in Bitget API settings, or remove the IP restriction",
        "40037": "API key does not have permission for this action — enable Futures in API settings",
        "40200": "Demo API key used without paptrading header — this should be auto-set; check DEMO_MODE",
        "40400": "Resource not found — check productType and symbol spelling",
        "400":   "Bad request — if DEMO_MODE=True, make sure you created a DEMO API key (not a live key)",
    }
    return hints.get(code, "See https://www.bitget.com/api-doc/common/error-code for code details")


# ── Public methods ────────────────────────────────────────────────── #

def get(path: str, params: dict | None = None) -> dict:
    """Authenticated GET."""
    qs        = ("?" + "&".join(f"{k}={v}" for k, v in params.items())) if params else ""
    full_path = path + qs
    url       = BASE_URL + full_path
    headers   = _build_headers("GET", full_path)
    logger.debug(f"GET {url}  demo={DEMO_MODE}")
    resp = _session.get(url, headers=headers, timeout=15)
    return _handle(resp, url)


def post(path: str, body: dict) -> dict:
    """Authenticated POST."""
    body_str = json.dumps(body)
    url      = BASE_URL + path
    headers  = _build_headers("POST", path, body_str)
    logger.debug(f"POST {url}  demo={DEMO_MODE}")
    resp = _session.post(url, headers=headers, data=body_str, timeout=15)
    return _handle(resp, url)


def get_public(path: str, params: dict | None = None) -> dict:
    """Unauthenticated GET for market data (no auth headers needed)."""
    qs   = ("?" + "&".join(f"{k}={v}" for k, v in params.items())) if params else ""
    url  = BASE_URL + path + qs
    resp = _session.get(url, timeout=15)
    return _handle(resp, url)
