"""
IG REST API adapter for ig_bot.py.

Uses raw requests — no trading-ig library — to give full control over
IG's versioned headers and auth flow.

Public interface mirrors trader.py where possible so ig_bot.py reads naturally.
"""
import logging
import threading
import time

import pandas as pd
import requests

import config_ig

logger = logging.getLogger(__name__)

# ── Resolution mapping ──────────────────────────────────────── #
_RESOLUTION = {
    "1D":  "DAY",
    "1H":  "HOUR",
    "15m": "MINUTE_15",
    "5m":  "MINUTE_5",
    "1m":  "MINUTE",
}

# ── Candle cache ─────────────────────────────────────────────── #
# Keyed by (epic, interval, limit). Value: (fetched_at, DataFrame).
# TTL = one candle period so we re-fetch at most once per bar.
_INTERVAL_SECONDS = {
    "1D":  86400,
    "1H":  3600,
    "15m": 900,
    "5m":  300,
    "3m":  180,
    "1m":  60,
}
_candle_cache: dict[tuple, tuple[float, pd.DataFrame]] = {}


def clear_candle_cache() -> None:
    """Flush the in-memory candle cache (useful in tests)."""
    _candle_cache.clear()


# ── Session management ──────────────────────────────────────── #

class _IGSession:
    """
    Manages IG REST session tokens (CST + X-SECURITY-TOKEN).
    Handles initial login and automatic re-login on token expiry (401).
    Thread-safe via internal lock.
    """

    def __init__(self, api_key: str, username: str, password: str,
                 base_url: str, account_id: str = ""):
        self._api_key    = api_key
        self._username   = username
        self._password   = password
        self._base_url   = base_url.rstrip("/")
        self._account_id = account_id
        self._cst        = ""
        self._token      = ""
        self._ls_endpoint  = ""
        self._account_id_from_login = ""
        self._lock       = threading.Lock()

    def login(self) -> None:
        """POST /session (Version 2) to obtain CST + X-SECURITY-TOKEN."""
        url = f"{self._base_url}/session"
        headers = {
            "Content-Type":  "application/json; charset=UTF-8",
            "Accept":        "application/json; charset=UTF-8",
            "X-IG-API-KEY":  self._api_key,
            "Version":       "2",
        }
        body = {
            "identifier":        self._username,
            "password":          self._password,
            "encryptedPassword": False,
        }

        resp = requests.post(url, json=body, headers=headers, timeout=15)
        if resp.status_code != 200:
            raise RuntimeError(
                f"IG login failed {resp.status_code}: {resp.text[:200]}"
            )
        self._cst   = resp.headers.get("CST", "")
        self._token = resp.headers.get("X-SECURITY-TOKEN", "")
        if not self._cst or not self._token:
            raise RuntimeError("IG login succeeded but no tokens in response headers")
        resp_json = resp.json()
        self._ls_endpoint           = resp_json.get("lightstreamerEndpoint", "")
        self._account_id_from_login = resp_json.get("accountId", self._account_id)
        logger.info("IG session established")

    def _headers(self, version: str) -> dict:
        return {
            "Content-Type":      "application/json; charset=UTF-8",
            "Accept":            "application/json; charset=UTF-8",
            "X-IG-API-KEY":      self._api_key,
            "CST":               self._cst,
            "X-SECURITY-TOKEN":  self._token,
            "Version":           version,
        }

    def _request(self, method: str, endpoint: str, version: str,
                 params: dict = None, body: dict = None,
                 _retry: bool = True) -> dict:
        url  = f"{self._base_url}/{endpoint.lstrip('/')}"
        hdrs = self._headers(version)
        with self._lock:
            try:
                resp = requests.request(
                    method, url, headers=hdrs,
                    params=params, json=body, timeout=15,
                )
            except requests.RequestException as e:
                raise RuntimeError(f"IG request error: {e}") from e

        if resp.status_code == 401 and _retry:
            logger.warning("IG token expired — re-logging in")
            self.login()
            return self._request(method, endpoint, version,
                                  params=params, body=body, _retry=False)

        if resp.status_code == 429:
            raise RuntimeError("IG rate limit hit (429) — slow down")

        if not resp.ok:
            raise RuntimeError(
                f"IG {method} {endpoint} → {resp.status_code}: {resp.text[:300]}"
            )

        if not resp.content:
            return {}
        return resp.json()

    def get(self, endpoint: str, params: dict = None, version: str = "1") -> dict:
        return self._request("GET", endpoint, version, params=params)

    def post(self, endpoint: str, body: dict = None, version: str = "1") -> dict:
        return self._request("POST", endpoint, version, body=body)

    def put(self, endpoint: str, body: dict = None, version: str = "1") -> dict:
        return self._request("PUT", endpoint, version, body=body)

    def delete(self, endpoint: str, body: dict = None, version: str = "1") -> dict:
        return self._request("DELETE", endpoint, version, body=body)


# Module-level singleton
_session: _IGSession | None = None


def _get_session() -> _IGSession:
    global _session
    if _session is None:
        base_url = (config_ig.IG_DEMO_URL if config_ig.IG_ACC_TYPE == "DEMO"
                    else config_ig.IG_LIVE_URL)
        _session = _IGSession(
            config_ig.IG_API_KEY,
            config_ig.IG_USERNAME,
            config_ig.IG_PASSWORD,
            base_url,
            config_ig.IG_ACCOUNT_ID,
        )
        _session.login()
    return _session


def get_stream_credentials() -> dict:
    """Return streaming auth details captured during the last login.

    Returns a dict with keys: account_id, cst, xst, ls_endpoint.
    Call after _get_session() has been invoked.
    """
    sess = _get_session()
    return {
        "account_id":  sess._account_id_from_login or sess._account_id,
        "cst":         sess._cst,
        "xst":         sess._token,
        "ls_endpoint": sess._ls_endpoint,
    }


def _refresh_session() -> None:
    """Clear the cached session so the next _get_session() performs a fresh login."""
    global _session
    _session = None


# ── Market data ──────────────────────────────────────────────── #

def get_candles(epic: str, interval: str, limit: int = 100) -> pd.DataFrame:
    """
    Fetch OHLCV candles from IG.
    Returns DataFrame with columns: ts (Unix ms), open, high, low, close, vol
    sorted ascending (oldest first) — same schema as trader.py.

    Results are cached for one candle period (TTL = interval duration) to
    avoid burning through IG's weekly historical-data allowance.
    """
    cache_key = (epic, interval, limit)
    ttl = _INTERVAL_SECONDS.get(interval, 60)
    cached = _candle_cache.get(cache_key)
    if cached is not None:
        fetched_at, df = cached
        if time.time() - fetched_at < ttl:
            return df

    resolution = _RESOLUTION.get(interval, interval)
    try:
        data = _get_session().get(
            f"/prices/{epic}/{resolution}/{limit}",
            version="1",
        )
    except Exception as e:
        logger.error(f"get_candles({epic},{interval}): {e}")
        return pd.DataFrame()

    prices = data.get("prices", [])
    if not prices:
        logger.warning(f"get_candles({epic},{interval}): empty response")
        return pd.DataFrame()
    logger.debug(f"get_candles({epic},{interval}): {len(prices)} rows, first ts raw={prices[0].get('snapshotTimeUTC') or prices[0].get('snapshotTime')}")

    rows = []
    for p in prices:
        # IG returns bid/ask/lastTraded sub-dicts for each OHLC field
        def mid(field: str) -> float:
            f = p.get(field, {}) or {}
            bid = f.get("bid")
            ask = f.get("ask") or f.get("offer")
            last = f.get("lastTraded")
            if bid is not None and ask is not None:
                return (float(bid) + float(ask)) / 2.0
            if last is not None:
                return float(last)
            return 0.0

        # Parse timestamp: "2025:08:10-01:00:00" (IG v1 snapshotTime format)
        raw_ts = p.get("snapshotTimeUTC") or p.get("snapshotTime", "")
        try:
            from datetime import datetime, timezone
            dt = datetime.strptime(raw_ts, "%Y:%m:%d-%H:%M:%S")
            ts = int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
        except Exception:
            ts = 0

        rows.append({
            "ts":    ts,
            "open":  mid("openPrice"),
            "high":  mid("highPrice"),
            "low":   mid("lowPrice"),
            "close": mid("closePrice"),
            "vol":   float(p.get("lastTradedVolume") or 0),
        })

    df = pd.DataFrame(rows)
    df = df.sort_values("ts").reset_index(drop=True)
    # Drop candles with invalid prices (IG returns 0 or garbage negatives for
    # market-closure gaps and some data quality issues).
    bad = (df["open"] <= 0) | (df["high"] <= 0) | (df["low"] <= 0) | (df["close"] <= 0)
    if bad.any():
        logger.debug(f"get_candles({epic},{interval}): dropped {bad.sum()} invalid candle(s)")
        df = df[~bad].reset_index(drop=True)
    _candle_cache[cache_key] = (time.time(), df)
    return df


def get_mark_price(epic: str) -> float:
    """Current mid price (bid+offer)/2 from /markets/{epic}."""
    try:
        data = _get_session().get(f"/markets/{epic}", version="1")
        snap = data.get("snapshot", {})
        bid  = snap.get("bid")
        ask  = snap.get("offer")
        if bid is not None and ask is not None:
            return (float(bid) + float(ask)) / 2.0
        last = snap.get("lastTradedPrice", {}).get("bid")
        if last is not None:
            return float(last)
    except Exception as e:
        logger.error(f"get_mark_price({epic}): {e}")
    return 0.0


def get_usdt_balance() -> float:
    """Available cash balance in account currency."""
    try:
        data = _get_session().get("/accounts", version="1")
        accounts = data.get("accounts", [])
        target_id = config_ig.IG_ACCOUNT_ID
        for acc in accounts:
            if target_id and acc.get("accountId") != target_id:
                continue
            bal = acc.get("balance", {}) or {}
            avail = bal.get("available")
            if avail is not None:
                return float(avail)
        # fallback: first account
        if accounts:
            bal = accounts[0].get("balance", {}) or {}
            return float(bal.get("available", 0))
    except Exception as e:
        logger.error(f"get_usdt_balance: {e}")
    return 0.0


# ── Position queries ─────────────────────────────────────────── #

def get_open_position(deal_id: str) -> dict | None:
    """
    Returns the position dict for the given dealId, or None if not found.
    {side, entry_price, qty, sl, tp, deal_id}
    """
    try:
        data = _get_session().get("/positions", version="2")
        for item in data.get("positions", []):
            pos = item.get("position", {}) or {}
            if pos.get("dealId") == deal_id:
                direction = pos.get("direction", "BUY")
                return {
                    "side":        "LONG" if direction == "BUY" else "SHORT",
                    "entry_price": float(pos.get("openLevel") or 0),
                    "qty":         float(pos.get("size") or 0),
                    "sl":          pos.get("stopLevel"),
                    "tp":          pos.get("limitLevel"),
                    "deal_id":     deal_id,
                }
    except Exception as e:
        logger.error(f"get_open_position({deal_id}): {e}")
    return None


# ── Confirm polling ──────────────────────────────────────────── #

def _poll_confirm(deal_ref: str, max_attempts: int = 12,
                  sleep_sec: float = 0.5) -> dict:
    """
    Poll GET /confirms/{dealRef} until dealStatus != 'PROCESSING'.
    Returns the confirm dict or raises RuntimeError on rejection.
    """
    for attempt in range(max_attempts):
        try:
            data = _get_session().get(f"/confirms/{deal_ref}", version="1")
        except Exception as e:
            raise RuntimeError(f"confirm poll error: {e}") from e

        status = data.get("dealStatus", "PROCESSING")
        if status == "ACCEPTED":
            return data
        if status in ("REJECTED", "DELETED"):
            reason = data.get("reason", "unknown")
            raise RuntimeError(f"Deal rejected: {reason}")
        if status != "PROCESSING":
            return data
        time.sleep(sleep_sec)

    raise RuntimeError(f"Confirm timed out after {max_attempts} attempts")


# ── Order placement ──────────────────────────────────────────── #

def open_long(epic: str, sl_price: float, tp1_price: float,
              tp_price: float, size: float = None,
              currency: str = None) -> dict:
    """
    Place a BUY market order with SL and TP.
    Returns {deal_id, entry, side, qty, sl, tp, tp1, tpsl_set}
    """
    if size is None:
        size = config_ig.INSTRUMENTS[0]["contract_size"]
    return _place_order(epic, "BUY", sl_price, tp_price, size, tp1_price, currency=currency)


def open_short(epic: str, sl_price: float, tp1_price: float,
               tp_price: float, size: float = None,
               currency: str = None) -> dict:
    """
    Place a SELL market order with SL and TP.
    Returns {deal_id, entry, side, qty, sl, tp, tp1, tpsl_set}
    """
    if size is None:
        size = config_ig.INSTRUMENTS[0]["contract_size"]
    return _place_order(epic, "SELL", sl_price, tp_price, size, tp1_price, currency=currency)


def _place_order(epic: str, direction: str, sl_price: float,
                 tp_price: float, size: float, tp1_price: float,
                 currency: str = None) -> dict:
    if currency is None:
        currency = config_ig.INSTRUMENTS[0]["currency"]
    body = {
        "epic":            epic,
        "expiry":          "-",
        "direction":       direction,
        "size":            size,
        "orderType":       "MARKET",
        "timeInForce":     "FILL_OR_KILL",
        "stopLevel":       round(sl_price, 1) if sl_price else None,
        "limitLevel":      round(tp_price, 1) if tp_price else None,
        "currencyCode":    currency,
        "forceOpen":       True,
        "guaranteedStop":  False,
        "trailingStop":    False,
    }
    # Remove None values
    body = {k: v for k, v in body.items() if v is not None}

    try:
        resp = _get_session().post("/positions/otc", body=body, version="2")
    except Exception as e:
        raise RuntimeError(f"open order failed: {e}") from e

    deal_ref = resp.get("dealReference", "")
    confirm  = _poll_confirm(deal_ref)

    fill = float(confirm.get("level") or 0)
    return {
        "deal_id":  confirm.get("dealId", ""),
        "entry":    fill,
        "side":     "LONG" if direction == "BUY" else "SHORT",
        "qty":      size,
        "sl":       sl_price,
        "tp":       tp_price,
        "tp1":      tp1_price,
        "tpsl_set": True,
    }


def partial_close(deal_id: str, size: float, direction: str) -> bool:
    """
    Close `size` contracts of an existing position at market.
    `direction` must be the opposite of the open position:
      LONG position → direction="SELL"
      SHORT position → direction="BUY"
    """
    body = {
        "dealId":      deal_id,
        "epic":        None,
        "expiry":      "-",
        "direction":   direction,
        "size":        size,
        "orderType":   "MARKET",
        "timeInForce": "FILL_OR_KILL",
        "level":       None,
        "quoteId":     None,
    }
    body = {k: v for k, v in body.items() if v is not None}
    try:
        resp = _get_session().delete("/positions/otc", body=body, version="1")
        deal_ref = resp.get("dealReference", "")
        if deal_ref:
            _poll_confirm(deal_ref)
        return True
    except Exception as e:
        logger.error(f"partial_close({deal_id},{size}): {e}")
        return False


def update_sl(deal_id: str, new_sl: float, new_tp: float = None) -> bool:
    """Update stop loss (and optionally limit) on an open position."""
    body: dict = {
        "stopLevel":             round(new_sl, 1),
        "trailingStop":          False,
        "trailingStopDistance":  None,
        "guaranteedStop":        False,
    }
    if new_tp is not None:
        body["limitLevel"] = round(new_tp, 1)
    try:
        resp = _get_session().put(f"/positions/otc/{deal_id}", body=body, version="2")
        deal_ref = resp.get("dealReference", "")
        if deal_ref:
            _poll_confirm(deal_ref)
        return True
    except Exception as e:
        logger.error(f"update_sl({deal_id},{new_sl}): {e}")
        return False


def close_position(deal_id: str, size: float, direction: str) -> bool:
    """Force-close remaining size at market (same as partial_close)."""
    return partial_close(deal_id, size, direction)


# ── Working / limit orders ───────────────────────────────────────── #

def _place_working_order(epic: str, direction: str, limit_price: float,
                         sl_price: float, tp_price: float, size: float,
                         currency: str = None) -> str:
    """
    POST /workingorders/otc to create a GTC LIMIT working order.
    Returns the deal_id string.
    """
    if currency is None:
        currency = config_ig.INSTRUMENTS[0]["currency"]
    level = round(limit_price, 1)
    # Use distances (relative to trigger level) rather than absolute levels —
    # more reliable across instrument types and avoids spread-related rejections.
    if direction == "SELL":
        stop_dist  = round(sl_price - level, 1)
        limit_dist = round(level - tp_price, 1)
    else:
        stop_dist  = round(level - sl_price, 1)
        limit_dist = round(tp_price - level, 1)
    body = {
        "epic":           epic,
        "direction":      direction,
        "size":           size,
        "level":          level,
        "type":           "LIMIT",
        "timeInForce":    "GOOD_TILL_CANCELLED",
        "stopDistance":   max(stop_dist,  1.0),
        "limitDistance":  max(limit_dist, 1.0),
        "currencyCode":   currency,
        "expiry":         "-",
        "forceOpen":      True,
        "guaranteedStop": False,
        "trailingStop":   False,
    }
    logger.debug(f"place_working_order body: {body}")
    try:
        resp = _get_session().post("/workingorders/otc", body=body, version="2")
    except Exception as e:
        raise RuntimeError(f"place_working_order failed: {e}") from e

    deal_ref = resp.get("dealReference", "")
    confirm  = _poll_confirm(deal_ref)
    return confirm.get("dealId", "")


def place_limit_long(epic: str, limit_price: float, sl_price: float,
                     tp_price: float, size: float,
                     currency: str = None) -> str:
    """Place a GTC limit buy working order. Returns deal_id string."""
    return _place_working_order(epic, "BUY", limit_price, sl_price, tp_price, size, currency=currency)


def place_limit_short(epic: str, limit_price: float, sl_price: float,
                      tp_price: float, size: float,
                      currency: str = None) -> str:
    """Place a GTC limit sell working order. Returns deal_id string."""
    return _place_working_order(epic, "SELL", limit_price, sl_price, tp_price, size, currency=currency)


def cancel_working_order(deal_id: str) -> None:
    """Cancel a working order by deal_id."""
    try:
        _get_session().delete(f"/workingorders/otc/{deal_id}", version="2")
    except Exception as e:
        raise RuntimeError(f"cancel_working_order({deal_id}) failed: {e}") from e


def get_working_order_status(deal_id: str) -> dict:
    """
    Returns {"status": "open"|"filled"|"deleted", "fill_price": float | None}.
    Checks /workingorders first; if not found checks /positions.
    """
    # 1. Check if still a working (pending) order
    try:
        data = _get_session().get("/workingorders", version="2")
        for item in data.get("workingOrders", []):
            wo = item.get("workingOrderData", {}) or {}
            if wo.get("dealId") == deal_id:
                return {"status": "open", "fill_price": None}
    except Exception as e:
        logger.error(f"get_working_order_status({deal_id}) workingorders: {e}")
        return {"status": "unknown", "fill_price": None}

    # 2. Not in working orders — check if it became a position (filled)
    try:
        data = _get_session().get("/positions", version="2")
        for item in data.get("positions", []):
            pos = item.get("position", {}) or {}
            if pos.get("dealId") == deal_id:
                fill_price = float(pos.get("openLevel") or 0)
                return {"status": "filled", "fill_price": fill_price}
    except Exception as e:
        logger.error(f"get_working_order_status({deal_id}) positions: {e}")
        return {"status": "unknown", "fill_price": None}

    # 3. Not found anywhere — treated as deleted/expired
    return {"status": "deleted", "fill_price": None}


def get_realized_pnl(deal_id: str) -> float | None:
    """
    Attempt to retrieve realized PnL from IG activity history.
    Returns None if unavailable (bot handles gracefully).
    """
    try:
        data = _get_session().get(
            "/history/activity",
            params={"dealId": deal_id, "pageSize": 10},
            version="3",
        )
        for activity in data.get("activities", []):
            details = activity.get("details", {}) or {}
            if (activity.get("type") == "POSITION" and
                    details.get("dealReference") and
                    details.get("actions")):
                for action in details["actions"]:
                    if action.get("actionType") == "POSITION_CLOSED":
                        pnl = details.get("profitAndLoss")
                        if pnl is not None:
                            # Strip currency symbol if present (e.g. "U$32.00")
                            pnl_str = str(pnl).replace(",", "").strip()
                            import re
                            nums = re.findall(r"-?\d+\.?\d*", pnl_str)
                            if nums:
                                return float(nums[0])
    except Exception as e:
        logger.warning(f"get_realized_pnl({deal_id}): {e}")
    return None
