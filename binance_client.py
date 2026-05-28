"""
binance_client.py — Authenticated Binance USDT-M Futures REST client.

Signing:
  - Authenticated requests append `timestamp` (ms) + `recvWindow` to params.
  - Signature = HMAC-SHA256(secret, canonical_qs).hexdigest() appended as `signature`.
  - Header `X-MBX-APIKEY` carries the API key.

Mirrors the public surface of bybit_client.py so binance.py can be a near
structural clone of bybit.py.
"""

import time
import hmac
import hashlib
import logging
from urllib.parse import urlencode

import requests

from config_binance import (
    API_KEY_PRIMARY,
    API_SECRET_PRIMARY,
    API_KEY_BACKUP,
    API_SECRET_BACKUP,
    BASE_URL,
    RECV_WINDOW,
)

logger = logging.getLogger(__name__)

_session = requests.Session()


# ── Canonicalisation + signing ────────────────────────────────────── #

def _canonical_qs(params: dict | None) -> str:
    """Serialize params to canonical query string. Insertion order preserved (Python 3.7+ dict)."""
    if not params:
        return ""
    return urlencode(params, doseq=False)


def _sign(qs: str, api_secret: str) -> str:
    """HMAC-SHA256 of canonical query string. Hex digest."""
    mac = hmac.new(
        api_secret.encode("utf-8"),
        qs.encode("utf-8"),
        digestmod=hashlib.sha256,
    )
    return mac.hexdigest()


def _signed_params(params: dict | None, api_secret: str) -> dict:
    """Append recvWindow + timestamp + signature in canonical order.

    Binance expects `signature` to be the FINAL key in the query string.
    """
    out = dict(params or {})
    out["recvWindow"] = RECV_WINDOW
    out["timestamp"]  = str(int(time.time() * 1000))
    qs  = _canonical_qs(out)
    out["signature"]  = _sign(qs, api_secret)
    return out


# ── Rate-limit retry ──────────────────────────────────────────────── #
#
# Binance USDT-M Futures FAPI weight budget is 2400/min per IP for public
# endpoints; private endpoints have separate order-rate limits. -1003 is the
# "Too many requests" error. Exponential backoff recovers from transient
# bursts (e.g. scanner fanning out candle requests).
_RATE_LIMIT_RETRIES = 4
_RATE_LIMIT_BACKOFF = 0.5


def _with_rate_limit_retry(do_request, describe: str):
    """Run do_request(); on -1003 sleep & retry with exponential backoff."""
    for attempt in range(_RATE_LIMIT_RETRIES):
        try:
            return do_request()
        except RuntimeError as e:
            msg = str(e)
            if "-1003" not in msg or attempt == _RATE_LIMIT_RETRIES - 1:
                raise
            delay = _RATE_LIMIT_BACKOFF * (2 ** attempt)
            logger.warning(
                f"[Binance] rate-limited on {describe} (-1003), "
                f"backoff {delay:.1f}s (attempt {attempt+1}/{_RATE_LIMIT_RETRIES-1})"
            )
            time.sleep(delay)
    # Unreachable — raise propagates from the loop above.


# ── Primary ↔ backup key failover ─────────────────────────────────── #
#
# When a request fails with an auth-related Binance error code, retry once
# with the other key. Rate limits and other errors don't trigger failover.
# Stickiness: once a key works, subsequent requests use it until *it*
# auth-fails. State is process-local.
_AUTH_FAILOVER_CODES = ("-2014", "-2015", "-1022", "-2008")

_active_key_role = "primary"


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
            f"[Binance] auth failure on {describe} with {active} key, "
            f"failing over to {fallback}: {e}"
        )
        result = do_request(*_creds(fallback))
        _active_key_role = fallback
        logger.warning(f"[Binance] active key is now {fallback} (sticky until next auth failure)")
        return result


# ── Response handling ─────────────────────────────────────────────── #

# Soft-success codes: Binance returns these when the requested state already
# matches what's set. They're not failures.
_SOFT_SUCCESS_CODES = {
    -4046,   # "No need to change margin type"
    -4048,   # "Margin type cannot be changed if there exists position"
    -4059,   # "No need to change position side"
    -4061,   # "Leverage not modified"
}


def _handle(resp: requests.Response, url: str) -> dict | list:
    """
    Parse response. Always show the body on any error so we know what Binance says.
    Binance error shape on both 4xx and 200: {"code": -XXXX, "msg": "..."}.
    Successful list endpoints (klines, ticker/24hr) return a JSON array — pass through.
    """
    try:
        data = resp.json()
    except Exception:
        resp.raise_for_status()
        return {}

    # Error envelope appears as a dict with negative `code`.
    if isinstance(data, dict):
        code = data.get("code", 0)
        try:
            code_i = int(code)
        except (ValueError, TypeError):
            code_i = 0
        if code_i < 0:
            if code_i in _SOFT_SUCCESS_CODES:
                logger.debug(f"[Binance] soft-success code={code_i} {data.get('msg')} ({url})")
                return data
            raise RuntimeError(
                f"Binance API error [{code_i}]: {data.get('msg')}\n"
                f"  URL: {url}\n"
                f"  Tip: {_hint(code_i)}"
            )

    if not resp.ok:
        raise RuntimeError(
            f"Binance HTTP {resp.status_code}: {resp.text}\n  URL: {url}"
        )
    return data


def _hint(code) -> str:
    code_i = int(code) if isinstance(code, (int, str)) and str(code).lstrip("-").isdigit() else 0
    hints = {
        -1003: "Too many requests — rate limit; back off and retry",
        -1021: "Timestamp outside recvWindow — check clock skew",
        -1022: "Invalid signature — check API_SECRET formatting",
        -1121: "Invalid symbol",
        -2010: "Insufficient balance for this order",
        -2011: "Unknown order — already filled or cancelled",
        -2013: "Order does not exist",
        -2014: "API-key format invalid",
        -2015: "Invalid API-key, IP, or permissions",
        -2019: "Margin insufficient",
        -4046: "No need to change margin type — soft success",
        -4059: "No need to change position side — soft success",
        -4061: "Leverage not modified — soft success",
        -4131: "PERCENT_PRICE filter limit (price too far from mark)",
    }
    return hints.get(code_i, "See https://binance-docs.github.io/apidocs/futures/en/#error-codes")


# ── Public methods ────────────────────────────────────────────────── #

def get_public(path: str, params: dict | None = None) -> dict | list:
    """Unauthenticated GET for market data. Retries on -1003."""
    qs  = ("?" + _canonical_qs(params)) if params else ""
    url = BASE_URL + path + qs

    def do():
        resp = _session.get(url, timeout=15)
        return _handle(resp, url)

    return _with_rate_limit_retry(do, f"GET {path}")


def get(path: str, params: dict | None = None) -> dict | list:
    """Authenticated GET. Signature appended to query string."""

    def do(api_key, api_secret):
        signed = _signed_params(params, api_secret)
        qs     = _canonical_qs(signed)
        url    = BASE_URL + path + "?" + qs
        headers = {"X-MBX-APIKEY": api_key}
        resp = _session.get(url, headers=headers, timeout=15)
        return _handle(resp, url)

    return _with_rate_limit_retry(
        lambda: _with_auth_failover(do, f"GET {path}"),
        f"GET {path}",
    )


def post(path: str, body: dict | None) -> dict | list:
    """Authenticated POST. Binance places params in query string (no JSON body)."""

    def do(api_key, api_secret):
        signed = _signed_params(body, api_secret)
        qs     = _canonical_qs(signed)
        url    = BASE_URL + path + "?" + qs
        headers = {"X-MBX-APIKEY": api_key}
        resp = _session.post(url, headers=headers, timeout=15)
        return _handle(resp, url)

    return _with_rate_limit_retry(
        lambda: _with_auth_failover(do, f"POST {path}"),
        f"POST {path}",
    )


def delete(path: str, params: dict | None = None) -> dict | list:
    """Authenticated DELETE. Same signing as GET."""

    def do(api_key, api_secret):
        signed = _signed_params(params, api_secret)
        qs     = _canonical_qs(signed)
        url    = BASE_URL + path + "?" + qs
        headers = {"X-MBX-APIKEY": api_key}
        resp = _session.delete(url, headers=headers, timeout=15)
        return _handle(resp, url)

    return _with_rate_limit_retry(
        lambda: _with_auth_failover(do, f"DELETE {path}"),
        f"DELETE {path}",
    )
