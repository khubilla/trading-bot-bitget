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

from config_bybit import (
    API_KEY_PRIMARY,
    API_SECRET_PRIMARY,
    API_KEY_BACKUP,
    API_SECRET_BACKUP,
    BASE_URL,
    RECV_WINDOW,
)

logger = logging.getLogger(__name__)

_session = requests.Session()

# ── Rate-limit retry ──────────────────────────────────────────────── #
#
# Bybit V5 public market endpoints allow ~120 req/s per IP. Our scanner can
# burst above that briefly when fanning out candle requests across all pairs
# (3m + 15m + 1H + 1D × ~120 pairs). The exchange responds with retCode=10006
# ("Too many visits"). It's transient — exponential-backoff retry recovers.
# Production endpoints (POST /v5/order/create etc.) have their own per-key
# limits but we hit them far less, so the same retry helps there too.
_RATE_LIMIT_RETRIES = 4         # total attempts including the first
_RATE_LIMIT_BACKOFF = 0.5       # seconds for the first retry; doubles each attempt


def _with_rate_limit_retry(do_request, describe: str):
    """Run do_request(); on retCode=10006 sleep & retry with exponential backoff."""
    for attempt in range(_RATE_LIMIT_RETRIES):
        try:
            return do_request()
        except RuntimeError as e:
            msg = str(e)
            if "[10006]" not in msg or attempt == _RATE_LIMIT_RETRIES - 1:
                raise
            delay = _RATE_LIMIT_BACKOFF * (2 ** attempt)
            logger.warning(
                f"[Bybit] rate-limited on {describe} (10006), "
                f"backoff {delay:.1f}s (attempt {attempt+1}/{_RATE_LIMIT_RETRIES-1})"
            )
            time.sleep(delay)
    # Unreachable — raise propagates from the loop above.


# ── Signature ─────────────────────────────────────────────────────── #

def _sign(timestamp: str, payload: str, api_key: str, api_secret: str) -> str:
    """
    Bybit V5 signature:
      message = timestamp + api_key + recv_window + payload
      payload = queryString (GET) or raw JSON body (POST)
      sign    = HMAC-SHA256(secret, message).hexdigest()
    """
    message = timestamp + api_key + RECV_WINDOW + payload
    mac = hmac.new(
        api_secret.encode("utf-8"),
        message.encode("utf-8"),
        digestmod=hashlib.sha256,
    )
    return mac.hexdigest()


def _build_headers(payload: str, api_key: str, api_secret: str) -> dict:
    ts = str(int(time.time() * 1000))
    return {
        "X-BAPI-API-KEY":     api_key,
        "X-BAPI-SIGN":        _sign(ts, payload, api_key, api_secret),
        "X-BAPI-TIMESTAMP":   ts,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW,
        "X-BAPI-SIGN-TYPE":   "2",
        "Content-Type":       "application/json",
    }


# ── Primary ↔ backup key failover ─────────────────────────────────── #
#
# When a request fails with an auth-related Bybit retCode, retry once with
# the other key. Rate limits and other errors don't trigger failover —
# swapping keys wouldn't help and could mask real problems.
#
# Stickiness: once a key works, all subsequent requests use it until *it*
# auth-fails. If the other key then works, we flip again. This avoids
# hammering a broken key on every call. State is process-local, so a
# restart always begins with the primary key — that's by design (it gives
# the primary a fresh chance whenever the bot reboots).
_AUTH_FAILOVER_CODES = ("10003", "10004", "10005", "10010")

_active_key_role = "primary"  # "primary" or "backup"


def _has_backup() -> bool:
    return bool(API_KEY_BACKUP and API_SECRET_BACKUP)


def _is_auth_error(err: RuntimeError) -> bool:
    msg = str(err)
    return any(f"[{c}]" in msg for c in _AUTH_FAILOVER_CODES)


def _creds(role: str):
    if role == "backup":
        return API_KEY_BACKUP, API_SECRET_BACKUP
    return API_KEY_PRIMARY, API_SECRET_PRIMARY


def _other(role: str) -> str:
    return "backup" if role == "primary" else "primary"


def _with_auth_failover(do_request, describe: str):
    """Try the active key; on auth failure try the other key and stick to it."""
    global _active_key_role
    active = _active_key_role
    try:
        return do_request(*_creds(active))
    except RuntimeError as e:
        if not _is_auth_error(e) or not _has_backup():
            raise
        fallback = _other(active)
        logger.warning(
            f"[Bybit] auth failure on {describe} with {active} key, "
            f"failing over to {fallback}: {e}"
        )
        result = do_request(*_creds(fallback))
        # Only flip after the fallback actually succeeds; if it raises, the
        # original active role stays (next request will try it again first).
        _active_key_role = fallback
        logger.warning(f"[Bybit] active key is now {fallback} (sticky until next auth failure)")
        return result


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
        # Soft-success codes: Bybit returns these when the requested state already
        # matches what's set (e.g. SL already attached via entry order's preset,
        # leverage already at the requested value, position mode already correct).
        # They're not failures — the operation completed without changing anything.
        # Swallowing them here means every caller benefits without per-function
        # try/except boilerplate.
        if code in _SOFT_SUCCESS_CODES:
            logger.debug(f"[Bybit] soft-success retCode={code} {msg} ({url})")
            return data
        raise RuntimeError(
            f"Bybit API error [{code}]: {msg}\n"
            f"  URL: {url}\n"
            f"  Tip: {_hint(code)}"
        )

    return data


# Codes Bybit returns when the requested state is already in place — treat as success.
_SOFT_SUCCESS_CODES = {
    34040,    # position TPSL: "not modified" (e.g. SL already at requested value)
    110043,   # set-leverage: leverage already at requested value
    30084,    # set-position-mode: mode already at requested value
}


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
    """Authenticated GET. Bybit V5 signs queryString. Retries on 10006."""
    qs = "&".join(f"{k}={v}" for k, v in (params or {}).items())
    url = BASE_URL + path + (("?" + qs) if qs else "")

    def do(api_key, api_secret):
        # Rebuild headers on each attempt — X-BAPI-TIMESTAMP must be fresh
        # to stay within Bybit's recv_window on retry.
        headers = _build_headers(qs, api_key, api_secret)
        logger.debug(f"GET {url}")
        resp = _session.get(url, headers=headers, timeout=15)
        return _handle(resp, url)

    return _with_rate_limit_retry(
        lambda: _with_auth_failover(do, f"GET {path}"),
        f"GET {path}",
    )


def post(path: str, body: dict) -> dict:
    """Authenticated POST. Bybit V5 signs raw JSON body. Retries on 10006."""
    body_str = json.dumps(body, separators=(",", ":"))  # compact JSON, no spaces
    url      = BASE_URL + path

    def do(api_key, api_secret):
        headers = _build_headers(body_str, api_key, api_secret)
        logger.debug(f"POST {url}")
        resp = _session.post(url, headers=headers, data=body_str, timeout=15)
        return _handle(resp, url)

    return _with_rate_limit_retry(
        lambda: _with_auth_failover(do, f"POST {path}"),
        f"POST {path}",
    )


def get_public(path: str, params: dict | None = None) -> dict:
    """Unauthenticated GET for market data. Retries on 10006."""
    qs  = ("?" + "&".join(f"{k}={v}" for k, v in params.items())) if params else ""
    url = BASE_URL + path + qs

    def do():
        resp = _session.get(url, timeout=15)
        return _handle(resp, url)

    return _with_rate_limit_retry(do, f"GET {path}")
