# Binance USDT-M Futures Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline) since the user asked for speed. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fourth bot (Binance USDT-M Futures) that runs the same strategy main loop as Bitget/Bybit via `sys.modules` aliasing.

**Architecture:** Mirror the Bybit integration exactly. `binance_bot.py` is a ~95-line thin entry point that installs aliases (`config → config_binance`, `bitget → binance`, `trader → binance_trader`, `scanner → binance_scanner`, `config_sN → config_binance_sN`) and then runs `bot.MTFBot().run()`. All strategy logic in `strategies/*` and `bot.py` is reused unchanged.

**Tech Stack:**
- Binance USDT-M Futures REST API (FAPI v1/v2)
- Signing: HMAC-SHA256 of canonical query string, header `X-MBX-APIKEY`
- Base URL: `https://fapi.binance.com` (testnet: `https://testnet.binancefuture.com`)

**Key surface contract:** Every function `strategies/*` and `bot.py` calls on `bitget` (aliased to `binance`) must exist in `binance.py` with matching signature and return shape. The functions to mirror are enumerated in [bybit.py](bybit.py); diffing against [bitget.py](bitget.py) confirms parity.

---

## File Structure

| File | Lines (est.) | Responsibility |
|---|---|---|
| `config_binance.py` | ~110 | Top-level Binance config (creds, base URL, scanner thresholds, dry-run, state file paths) |
| `config_binance_s1.py..s7.py` | ~50 each | Per-strategy parameters (initial copies of `config_s1..s7.py`) |
| `binance_client.py` | ~280 | Low-level HMAC-SHA256 signing + retry/error handling. Mirrors [bybit_client.py](bybit_client.py) |
| `binance.py` | ~900 | Endpoint-level API ops (candles, orders, positions, plans, trailing stops). Mirrors [bybit.py](bybit.py) |
| `binance_trader.py` | ~400 | High-level open_long/open_short + scale-ins + plan-order shims. Mirrors [bybit_trader.py](bybit_trader.py) |
| `binance_scanner.py` | ~160 | 24h ticker scan + sentiment. Mirrors [bybit_scanner.py](bybit_scanner.py) |
| `binance_bot.py` | ~95 | Thin entry point; installs `sys.modules` aliases then runs `bot.MTFBot().run()`. Mirrors [bybit_bot.py](bybit_bot.py) |
| `tests/test_binance_client.py` | ~80 | Verify HMAC signature + canonical query string |
| `tests/test_binance_imports.py` | ~30 | Verify all 4 bot entry points still import cleanly |

---

### Binance USDT-M Futures endpoint cheatsheet (referenced by tasks below)

| Operation | Bybit (V5) | Binance (FAPI) |
|---|---|---|
| Klines (public) | `GET /v5/market/kline` | `GET /fapi/v1/klines` |
| Ticker (public) | `GET /v5/market/tickers` | `GET /fapi/v1/ticker/24hr` |
| Mark price | `GET /v5/market/tickers` `markPrice` | `GET /fapi/v1/premiumIndex` `markPrice` |
| Exchange info | `GET /v5/market/instruments-info` | `GET /fapi/v1/exchangeInfo` |
| Wallet balance (auth) | `GET /v5/account/wallet-balance` | `GET /fapi/v2/balance` |
| Account total equity (auth) | (same as balance) | `GET /fapi/v2/account` `totalMarginBalance` |
| Positions (auth) | `GET /v5/position/list` | `GET /fapi/v2/positionRisk` |
| Set leverage (auth) | `POST /v5/position/set-leverage` | `POST /fapi/v1/leverage` |
| Position mode (auth) | `POST /v5/position/switch-mode` | `POST /fapi/v1/positionSide/dual` |
| Market order (auth) | `POST /v5/order/create` orderType=Market | `POST /fapi/v1/order` type=MARKET |
| Stop / TP attached | `stopLoss`/`takeProfit` field in `/v5/order/create` | Separate orders: `STOP_MARKET` + `TAKE_PROFIT_MARKET` with `closePosition=true` |
| Trailing stop | `/v5/position/trading-stop` `trailingStop` | `TRAILING_STOP_MARKET` order with `activationPrice` + `callbackRate` |
| Conditional entry | `/v5/order/create` with `triggerPrice`+`triggerDirection` | `STOP_MARKET` order (BUY/SELL) with `stopPrice` |
| Cancel single | `POST /v5/order/cancel` | `DELETE /fapi/v1/order` |
| Cancel all | `POST /v5/order/cancel-all` | `DELETE /fapi/v1/allOpenOrders` |
| Order status | `GET /v5/order/realtime` then `/v5/order/history` | `GET /fapi/v1/openOrder` then `GET /fapi/v1/order` |
| Closed PnL history | `GET /v5/position/closed-pnl` | `GET /fapi/v1/income?incomeType=REALIZED_PNL` aggregated |
| Interval format | `"1"/"15"/"60"/"D"` | `"1m"/"15m"/"1h"/"1d"` (lowercase) |

**Critical Binance specifics:**
1. **TP/SL are not atomic on entry** — must place separate `STOP_MARKET` and `TAKE_PROFIT_MARKET` orders with `closePosition=true` after the entry market order fills. `closePosition=true` makes them auto-resize with the position (no need to track qty after partial closes).
2. **Position mode** — one-way vs hedge. We force one-way at startup via `POST /fapi/v1/positionSide/dual` `{dualSidePosition: false}`. Accounts already in one-way return error -4059 (treated as soft-success).
3. **Trailing stop** — `TRAILING_STOP_MARKET` with `callbackRate` (1.0 = 1%, max 10) and `activationPrice`. Position-level not natively supported; we attach it as a reduce-only order on the open side.
4. **Filters** — `exchangeInfo` returns per-symbol filters: `LOT_SIZE.stepSize`, `LOT_SIZE.minQty`, `PRICE_FILTER.tickSize`, `MIN_NOTIONAL.notional`.
5. **Signing** — query string canonicalization: alphabetically sorted is NOT required by Binance, but order must be deterministic. We use insertion order (Python dict preserves it as of 3.7+).

---

## Task 1: Config files

**Files:**
- Create: `config_binance.py`
- Create: `config_binance_s1.py` ... `config_binance_s7.py`

- [ ] **Step 1.1: Create `config_binance.py`**

```python
# ============================================================
#  MTF Breakout Bot — Binance USDT-M Futures — Configuration
# ============================================================

import os
import pathlib as _pl

# Load .env file if present (local development convenience)
_env_file = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_env_file):
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip())

# --- Binance API Credentials ---
# Primary:  BINANCE_API_KEY,        BINANCE_API_SECRET
# Backup:   BINANCE_API_KEY_BACKUP, BINANCE_API_SECRET_BACKUP  (optional)
API_KEY_PRIMARY    = os.environ.get("BINANCE_API_KEY",           "")
API_SECRET_PRIMARY = os.environ.get("BINANCE_API_SECRET",        "")
API_KEY_BACKUP     = os.environ.get("BINANCE_API_KEY_BACKUP",    "")
API_SECRET_BACKUP  = os.environ.get("BINANCE_API_SECRET_BACKUP", "")

API_KEY    = API_KEY_PRIMARY
API_SECRET = API_SECRET_PRIMARY

# --- Binance API Base ---
# Live:    https://fapi.binance.com
# Testnet: https://testnet.binancefuture.com
BASE_URL    = os.environ.get("BINANCE_BASE_URL", "https://fapi.binance.com")
RECV_WINDOW = "5000"
SETTLE_COIN = "USDT"

# --- Safety Switch ---
DRY_RUN = True   # start in dry-run; flip to False only after dashboards verify

# --- Pair Scanner ---
MIN_VOLUME_USDT         = 5_000_000
MAX_PRICE_USDT          = 1000
SCAN_INTERVAL_SEC       = 60
LIQUIDITY_CHECK_ENABLED = False
MIN_OB_DEPTH_USDT       = 50_000

# --- Bot Behaviour ---
MAX_CONCURRENT_TRADES = 4
POLL_INTERVAL_SEC     = 15
INITIAL_BALANCE       = 160.0

# --- Market Sentiment Filter ---
SENTIMENT_THRESHOLD = 0.70
SENTIMENT_SCAN_SEC  = 60

# --- Claude Trade Filter ---
CLAUDE_FILTER_ENABLED   = False
CLAUDE_FILTER_MODEL     = "claude-haiku-4-5"
CLAUDE_FILTER_HISTORY_N = 30

# --- Strategy Enable Flags ---
ENABLE_S1 = True
ENABLE_S2 = True
ENABLE_S3 = True
ENABLE_S4 = True
ENABLE_S5 = True
ENABLE_S6 = True
ENABLE_S7 = True

# --- Logging ---
_DATA_DIR = _pl.Path(os.environ.get("DATA_DIR", "."))
LOG_FILE   = str(_DATA_DIR / "binance_bot.log")
TRADE_LOG  = str(_DATA_DIR / "binance_trades.csv")
STATE_FILE = str(_DATA_DIR / "binance_state.json")

# --- Non-Trading Hours --- (PH time, UTC+8)
NON_TRADING_HOURS = [
    #(6, 11),
]

DISABLE_SATURDAY_TRADING = False
ENHANCED_TRADING_WINDOWS = [
    #(16, 19, 1.5),
]
REDUCE_TUESDAY_SIZE = False
DEMO_MODE = False
```

- [ ] **Step 1.2: Copy strategy configs**

```bash
for n in 1 2 3 4 5 6 7; do
  cp config_s${n}.py config_binance_s${n}.py
done
```

- [ ] **Step 1.3: Commit configs**

```bash
git add config_binance*.py
git commit -m "feat(binance): config_binance + per-strategy configs"
```

---

## Task 2: Low-level client (binance_client.py)

**Files:**
- Create: `binance_client.py`
- Create: `tests/test_binance_client.py`

- [ ] **Step 2.1: Write failing signature test**

`tests/test_binance_client.py`:
```python
import binance_client as bc

def test_sign_known_vector():
    # Binance official example (docs):
    # secret = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"
    # query  = "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1&recvWindow=5000&timestamp=1499827319559"
    # expected = "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"
    sig = bc._sign(
        "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1&recvWindow=5000&timestamp=1499827319559",
        "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j",
    )
    assert sig == "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"


def test_canonical_query_preserves_insertion_order():
    qs = bc._canonical_qs({"symbol": "BTCUSDT", "side": "BUY", "quantity": "0.001"})
    assert qs == "symbol=BTCUSDT&side=BUY&quantity=0.001"
```

- [ ] **Step 2.2: Run test (expect ImportError)**

```bash
pytest tests/test_binance_client.py -v
```

Expected: `ModuleNotFoundError: No module named 'binance_client'`

- [ ] **Step 2.3: Implement `binance_client.py`**

```python
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

# Binance USDT-M futures rate limit: 2400 weight / minute, ~40/s sustained.
# REQUEST_WEIGHT shows up in headers but we don't enforce it locally; instead
# we retry on -1003 ("Too many requests") with exponential backoff.
_RATE_LIMIT_RETRIES = 4
_RATE_LIMIT_BACKOFF = 0.5


def _canonical_qs(params: dict) -> str:
    """Serialize params to canonical query string. Insertion order preserved (Python 3.7+ dict)."""
    if not params:
        return ""
    return urlencode(params, doseq=False)


def _sign(qs: str, api_secret: str) -> str:
    """HMAC-SHA256 of canonical query string. Hex digest."""
    mac = hmac.new(api_secret.encode("utf-8"), qs.encode("utf-8"), digestmod=hashlib.sha256)
    return mac.hexdigest()


def _with_rate_limit_retry(do_request, describe: str):
    for attempt in range(_RATE_LIMIT_RETRIES):
        try:
            return do_request()
        except RuntimeError as e:
            msg = str(e)
            if "-1003" not in msg or attempt == _RATE_LIMIT_RETRIES - 1:
                raise
            delay = _RATE_LIMIT_BACKOFF * (2 ** attempt)
            logger.warning(f"[Binance] rate-limited on {describe}, backoff {delay:.1f}s")
            time.sleep(delay)


# ── Auth failover ────────────────────────────────────────────────── #
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
    global _active_key_role
    active = _active_key_role
    try:
        return do_request(*_creds(active))
    except RuntimeError as e:
        if not _is_auth_error(e) or not _has_backup():
            raise
        fallback = _other(active)
        logger.warning(f"[Binance] auth failure on {describe} with {active} key, failover to {fallback}: {e}")
        result = do_request(*_creds(fallback))
        _active_key_role = fallback
        logger.warning(f"[Binance] active key is now {fallback}")
        return result


# ── Response handling ────────────────────────────────────────────── #

# Soft-success: Binance returns these when requested state already matches.
_SOFT_SUCCESS_CODES = {
    -4046,   # "No need to change margin type"
    -4048,   # "Margin type cannot be changed if there exists position"
    -4059,   # "No need to change position side"
    -4061,   # "Leverage not modified" — sometimes seen on /fapi/v1/leverage
}


def _handle(resp: requests.Response, url: str) -> dict:
    try:
        data = resp.json()
    except Exception:
        resp.raise_for_status()
        return {}

    # Binance error shape: {"code": -XXXX, "msg": "..."}
    if isinstance(data, dict) and data.get("code", 0) and data.get("code", 0) < 0:
        code = data["code"]
        if code in _SOFT_SUCCESS_CODES:
            logger.debug(f"[Binance] soft-success code={code} {data.get('msg')} ({url})")
            return data
        raise RuntimeError(
            f"Binance API error [{code}]: {data.get('msg')}\n"
            f"  URL: {url}\n"
            f"  Tip: {_hint(code)}"
        )

    if not resp.ok:
        raise RuntimeError(
            f"Binance HTTP {resp.status_code}: {resp.text}\n"
            f"  URL: {url}"
        )
    return data


def _hint(code) -> str:
    hints = {
        -1003: "Too many requests — rate limit; back off and retry",
        -1021: "Timestamp outside recvWindow — check clock skew",
        -1022: "Invalid signature — check API_SECRET formatting",
        -2010: "Insufficient balance for this order",
        -2014: "API-key format invalid",
        -2015: "Invalid API-key, IP, or permissions",
        -4046: "No need to change margin type — soft success",
        -4059: "No need to change position side — soft success",
        -4061: "Leverage not modified — soft success",
        -4131: "MARK_PRICE filter limit (price too far from mark)",
    }
    return hints.get(int(code), "See https://binance-docs.github.io/apidocs/futures/en/#error-codes for details")


# ── Public methods ───────────────────────────────────────────────── #

def get_public(path: str, params: dict | None = None) -> dict:
    """Unauthenticated GET for market data."""
    qs  = ("?" + _canonical_qs(params)) if params else ""
    url = BASE_URL + path + qs

    def do():
        resp = _session.get(url, timeout=15)
        return _handle(resp, url)

    return _with_rate_limit_retry(do, f"GET {path}")


def _signed_params(params: dict, api_secret: str) -> dict:
    """Append timestamp + recvWindow + signature in canonical order."""
    out = dict(params or {})
    out["recvWindow"] = RECV_WINDOW
    out["timestamp"]  = str(int(time.time() * 1000))
    qs  = _canonical_qs(out)
    out["signature"]  = _sign(qs, api_secret)
    return out


def get(path: str, params: dict | None = None) -> dict:
    """Authenticated GET. Signature appended to query string."""
    def do(api_key, api_secret):
        signed = _signed_params(params or {}, api_secret)
        qs     = _canonical_qs(signed)
        url    = BASE_URL + path + "?" + qs
        headers = {"X-MBX-APIKEY": api_key}
        resp = _session.get(url, headers=headers, timeout=15)
        return _handle(resp, url)

    return _with_rate_limit_retry(
        lambda: _with_auth_failover(do, f"GET {path}"),
        f"GET {path}",
    )


def post(path: str, body: dict) -> dict:
    """Authenticated POST. Binance puts params in query string even for POST."""
    def do(api_key, api_secret):
        signed = _signed_params(body or {}, api_secret)
        qs     = _canonical_qs(signed)
        url    = BASE_URL + path + "?" + qs
        headers = {"X-MBX-APIKEY": api_key}
        resp = _session.post(url, headers=headers, timeout=15)
        return _handle(resp, url)

    return _with_rate_limit_retry(
        lambda: _with_auth_failover(do, f"POST {path}"),
        f"POST {path}",
    )


def delete(path: str, params: dict | None = None) -> dict:
    """Authenticated DELETE. Same signing as GET."""
    def do(api_key, api_secret):
        signed = _signed_params(params or {}, api_secret)
        qs     = _canonical_qs(signed)
        url    = BASE_URL + path + "?" + qs
        headers = {"X-MBX-APIKEY": api_key}
        resp = _session.delete(url, headers=headers, timeout=15)
        return _handle(resp, url)

    return _with_rate_limit_retry(
        lambda: _with_auth_failover(do, f"DELETE {path}"),
        f"DELETE {path}",
    )
```

- [ ] **Step 2.4: Run tests**

```bash
pytest tests/test_binance_client.py -v
```

Expected: 2 passed.

- [ ] **Step 2.5: Commit**

```bash
git add binance_client.py tests/test_binance_client.py
git commit -m "feat(binance): low-level signed REST client + signature test"
```

---

## Task 3: Exchange operations (binance.py)

**Files:**
- Create: `binance.py`

This is the largest file. It mirrors [bybit.py](bybit.py) function-for-function. Implementation strategy:

1. **Module-level scaffolding** — DRY_RUN guard, interval map, symbol cache.
2. **Symbol metadata** — pull `/fapi/v1/exchangeInfo`, walk filters.
3. **Market data** — `get_candles`, `fetch_candles_at`, `get_mark_price`, `get_last_price`.
4. **Account** — `get_usdt_balance`, `get_total_equity`, `get_all_open_positions`, `get_single_position_entry`.
5. **Leverage** — `set_leverage`.
6. **Order execution** — `place_market_order` (+ SL via second `STOP_MARKET closePosition=true` call), `place_pos_tpsl_full`, `place_pos_sl_only`, `place_profit_plan`, `place_moving_plan`, `refresh_plan_exits`, `update_position_sl`.
7. **Plan/trigger orders** — `place_plan_order`, `cancel_plan_order`, `cancel_all_orders`, `get_order_fill`.
8. **History** — `get_history_position`, `get_realized_pnl` (both via `/fapi/v1/income?incomeType=REALIZED_PNL`).

- [ ] **Step 3.1: Write the file**

Full content shown below. Public API must exactly match [bybit.py](bybit.py) signatures — `bot.py` and `strategies/*` call them via the `bitget` alias.

```python
"""
binance.py — Binance USDT-M Futures API operations.

Wraps the endpoint-level calls over the low-level HTTP/auth client
(binance_client.py). No strategy knowledge lives here.

Mirrors the public surface of bitget.py / bybit.py so strategies/* exit
helpers and binance_trader.py can be near-clones of their Bitget counterparts.
"""

import math
import time as _time
import logging
from datetime import datetime, timezone

import pandas as pd

import binance_client as bc
from config_binance import SETTLE_COIN

logger = logging.getLogger(__name__)
_sym_cache: dict[str, dict] = {}


# ── DRY_RUN guard ──────────────────────────────────────────────── #

def _dry_run_active() -> bool:
    try:
        import config_binance
        return bool(config_binance.DRY_RUN)
    except Exception:
        return False


def _dry_run_skip(action: str, **payload) -> bool:
    if not _dry_run_active():
        return False
    short = " ".join(f"{k}={v}" for k, v in payload.items() if v is not None and v != "")
    logger.info(f"[Binance][DRY_RUN] {action} {short}".rstrip())
    return True


# ── Interval mapping ──────────────────────────────────────────── #
#   Bitget / strategy:  "1m"  "3m"  "5m"  "15m"  "1H"   "4H"   "1D"
#   Binance:            "1m"  "3m"  "5m"  "15m"  "1h"   "4h"   "1d"
_INTERVAL_MAP = {
    "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1H": "1h", "1h": "1h",
    "2H": "2h", "2h": "2h",
    "4H": "4h", "4h": "4h",
    "6H": "6h", "6h": "6h",
    "8H": "8h", "8h": "8h",
    "12H": "12h", "12h": "12h",
    "1D": "1d", "1d": "1d",
    "3D": "3d", "3d": "3d",
    "1W": "1w", "1w": "1w",
}


def _map_interval(interval: str) -> str:
    return _INTERVAL_MAP.get(interval, interval)


# ── Symbol metadata / rounding ────────────────────────────────── #

def _load_symbol_cache():
    global _sym_cache
    if _sym_cache:
        return
    data = bc.get_public("/fapi/v1/exchangeInfo")
    symbols = data.get("symbols") or []
    for s in symbols:
        symbol = s.get("symbol")
        status = s.get("status")
        contract_type = s.get("contractType")
        if not symbol or status != "TRADING" or contract_type != "PERPETUAL":
            continue
        if s.get("quoteAsset") != SETTLE_COIN:
            continue
        qty_step = 0.001
        min_qty = 0.001
        tick_size = 0.01
        min_notional = 0.0
        for f in s.get("filters") or []:
            ft = f.get("filterType")
            if ft == "LOT_SIZE":
                qty_step = float(f.get("stepSize") or qty_step)
                min_qty = float(f.get("minQty") or min_qty)
            elif ft == "PRICE_FILTER":
                tick_size = float(f.get("tickSize") or tick_size)
            elif ft == "MIN_NOTIONAL":
                min_notional = float(f.get("notional") or min_notional)
        _sym_cache[symbol] = {
            "qty_step": qty_step,
            "min_trade_num": min_qty,
            "tick_size": tick_size,
            "min_notional": min_notional,
            "price_place": _decimals(tick_size),
            "volume_place": _decimals(qty_step),
        }
    logger.info(f"[Binance] Symbol cache loaded: {len(_sym_cache)} contracts")


def _decimals(step: float) -> int:
    if step >= 1:
        return 0
    s = f"{step:.10f}".rstrip("0").rstrip(".")
    if "." in s:
        return len(s.split(".")[1])
    return 0


def sym_info(symbol: str) -> dict:
    _load_symbol_cache()
    return _sym_cache.get(symbol, {
        "qty_step": 0.001, "min_trade_num": 0.001,
        "tick_size": 0.01, "min_notional": 0.0,
        "price_place": 2, "volume_place": 3,
    })


def round_price(price: float, symbol: str) -> str:
    info = sym_info(symbol)
    tick = info["tick_size"]
    rounded = math.floor(price / tick) * tick
    return f"{rounded:.{info['price_place']}f}"


def round_qty(qty: float, symbol: str, mark_price: float | None = None) -> str:
    info = sym_info(symbol)
    step = info["qty_step"]
    qty  = math.floor(qty / step) * step
    qty  = max(qty, info["min_trade_num"])
    min_notional = info.get("min_notional", 0.0)
    if mark_price is not None and mark_price > 0 and min_notional > 0:
        min_qty_notional = math.ceil((min_notional / mark_price) / step) * step
        qty = max(qty, min_qty_notional)
    return f"{qty:.{info['volume_place']}f}"


# ── Market data ──────────────────────────────────────────────── #

def get_candles(symbol: str, interval: str, limit: int = 100) -> pd.DataFrame:
    try:
        rows = bc.get_public(
            "/fapi/v1/klines",
            params={"symbol": symbol, "interval": _map_interval(interval), "limit": str(limit)},
        )
        if not rows:
            return pd.DataFrame()
        # Binance row: [openTime, open, high, low, close, volume, closeTime, quoteVol, trades, takerBuyBase, takerBuyQuote, ignore]
        df = pd.DataFrame(rows, columns=[
            "ts", "open", "high", "low", "close", "vol",
            "close_ts", "quote_vol", "trades", "tb_base", "tb_quote", "ignore",
        ])
        df = df[["ts", "open", "high", "low", "close", "vol", "quote_vol"]]
        df[["open", "high", "low", "close", "vol", "quote_vol"]] = (
            df[["open", "high", "low", "close", "vol", "quote_vol"]].astype(float)
        )
        df["ts"] = df["ts"].astype(int)
        return df.sort_values("ts").reset_index(drop=True)
    except RuntimeError as e:
        msg = str(e)
        if "-1121" in msg or "Invalid symbol" in msg:
            logger.warning(f"[Binance][{symbol}] candles: invalid/delisted: {e}")
        else:
            logger.warning(f"[Binance][{symbol}] get_candles error: {e}")
        return pd.DataFrame()


def fetch_candles_at(symbol: str, interval: str, limit: int, end_ms: int) -> pd.DataFrame:
    try:
        rows = bc.get_public(
            "/fapi/v1/klines",
            params={
                "symbol": symbol, "interval": _map_interval(interval),
                "limit": str(limit), "endTime": str(end_ms),
            },
        )
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=[
            "ts", "open", "high", "low", "close", "vol",
            "close_ts", "quote_vol", "trades", "tb_base", "tb_quote", "ignore",
        ])
        df = df[["ts", "open", "high", "low", "close", "vol", "quote_vol"]]
        df[["open", "high", "low", "close", "vol", "quote_vol"]] = (
            df[["open", "high", "low", "close", "vol", "quote_vol"]].astype(float)
        )
        df["ts"] = df["ts"].astype(int)
        return df.sort_values("ts").reset_index(drop=True)
    except Exception as e:
        logger.warning(f"[Binance][{symbol}] fetch_candles_at error: {e}")
        return pd.DataFrame()


def get_mark_price(symbol: str) -> float:
    data = bc.get_public("/fapi/v1/premiumIndex", params={"symbol": symbol})
    return float(data.get("markPrice") or 0)


def get_last_price(symbol: str) -> float:
    data = bc.get_public("/fapi/v1/ticker/price", params={"symbol": symbol})
    return float(data.get("price") or 0)


# ── Account ──────────────────────────────────────────────────── #

def get_usdt_balance() -> float:
    """Free USDT balance — excludes locked margin and open-order reservations."""
    rows = bc.get("/fapi/v2/balance", params=None)
    for r in rows or []:
        if r.get("asset") != SETTLE_COIN:
            continue
        try:
            return float(r.get("availableBalance") or 0)
        except (ValueError, TypeError):
            return 0.0
    return 0.0


def get_total_equity() -> float:
    """Total margin balance (wallet + unrealised PnL across all positions)."""
    data = bc.get("/fapi/v2/account", params=None)
    try:
        return float(data.get("totalMarginBalance") or 0)
    except (ValueError, TypeError):
        return 0.0


def get_all_open_positions() -> dict[str, dict]:
    rows = bc.get("/fapi/v2/positionRisk", params=None)
    result: dict[str, dict] = {}
    for p in rows or []:
        amt = float(p.get("positionAmt") or 0)
        if amt == 0:
            continue
        symbol = p.get("symbol")
        side = "LONG" if amt > 0 else "SHORT"
        qty  = abs(amt)
        entry = float(p.get("entryPrice") or 0)
        mark  = float(p.get("markPrice") or 0)
        upnl  = float(p.get("unRealizedProfit") or 0)
        lev   = int(float(p.get("leverage") or 0))
        # Binance doesn't return positionIM directly; approximate as notional / leverage.
        notional = abs(amt) * mark
        margin   = (notional / lev) if lev > 0 else 0.0
        result[symbol] = {
            "side": side, "entry_price": entry, "qty": qty,
            "unrealised_pnl": upnl, "mark_price": mark,
            "margin": margin, "leverage": lev,
        }
    return result


def get_single_position_entry(symbol: str) -> float:
    try:
        rows = bc.get("/fapi/v2/positionRisk", params={"symbol": symbol})
        for p in rows or []:
            amt = float(p.get("positionAmt") or 0)
            if amt != 0:
                return float(p.get("entryPrice") or 0)
    except Exception:
        pass
    return 0.0


# ── Leverage ──────────────────────────────────────────────── #

def set_leverage(symbol: str, leverage: int):
    if _dry_run_skip("set_leverage", symbol=symbol, leverage=leverage):
        return
    try:
        bc.post("/fapi/v1/leverage", {"symbol": symbol, "leverage": str(leverage)})
        logger.info(f"[Binance][{symbol}] Leverage set to {leverage}x")
    except RuntimeError as e:
        # Binance returns success even when leverage is already set; if -4061 surfaces, treat as ok.
        if "-4061" in str(e):
            logger.debug(f"[Binance][{symbol}] Leverage already {leverage}x")
        else:
            logger.warning(f"[Binance][{symbol}] set_leverage warn: {e}")


# ── Position mode ──────────────────────────────────────────── #

def ensure_one_way_mode() -> None:
    """Force the account into one-way mode. Idempotent."""
    if _dry_run_skip("ensure_one_way_mode"):
        return
    try:
        bc.post("/fapi/v1/positionSide/dual", {"dualSidePosition": "false"})
        logger.info("[Binance] Position mode = ONE-WAY")
    except RuntimeError as e:
        # -4059 = "No need to change position side" → already one-way
        if "-4059" in str(e):
            logger.debug("[Binance] Position mode already ONE-WAY")
        else:
            logger.warning(f"[Binance] ensure_one_way_mode warn: {e}")


# ── Order execution primitives ─────────────────────────────── #

def place_market_order(symbol: str, side: str, qty_str: str,
                       sl_trigger: float | None = None,
                       tp_trigger: float | None = None) -> dict:
    """
    Market entry. SL/TP are NOT atomic on Binance — we place them as separate
    closePosition=true orders after the market fills. Caller (binance_trader)
    sleeps briefly to let the position settle before reading entry price.
    """
    if _dry_run_skip("place_market_order", symbol=symbol, side=side, qty=qty_str,
                     sl=sl_trigger, tp=tp_trigger):
        return {"orderId": f"DRY-{symbol}-{int(_time.time())}"}
    binance_side = "BUY" if side.lower() == "buy" else "SELL"
    body = {
        "symbol": symbol,
        "side": binance_side,
        "type": "MARKET",
        "quantity": qty_str,
    }
    info = sym_info(symbol)
    logger.info(
        f"[Binance][{symbol}] /fapi/v1/order MARKET {binance_side} qty={qty_str} "
        f"(min_qty={info.get('min_trade_num')}, qty_step={info.get('qty_step')}, "
        f"min_notional={info.get('min_notional')})"
    )
    resp = bc.post("/fapi/v1/order", body)

    # Attach SL/TP as closePosition orders (auto-resize with position).
    close_side = "SELL" if binance_side == "BUY" else "BUY"
    if sl_trigger is not None:
        try:
            bc.post("/fapi/v1/order", {
                "symbol": symbol, "side": close_side,
                "type": "STOP_MARKET",
                "stopPrice": round_price(sl_trigger, symbol),
                "closePosition": "true",
                "workingType": "MARK_PRICE",
                "priceProtect": "true",
            })
        except RuntimeError as e:
            logger.warning(f"[Binance][{symbol}] preset SL attach failed: {e}")
    if tp_trigger is not None:
        try:
            bc.post("/fapi/v1/order", {
                "symbol": symbol, "side": close_side,
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": round_price(tp_trigger, symbol),
                "closePosition": "true",
                "workingType": "MARK_PRICE",
                "priceProtect": "true",
            })
        except RuntimeError as e:
            logger.warning(f"[Binance][{symbol}] preset TP attach failed: {e}")
    return resp


def _cancel_position_close_orders(symbol: str, close_side: str, types: tuple[str, ...]) -> None:
    """Cancel existing closePosition orders matching given types/side. Used before re-attaching."""
    try:
        rows = bc.get("/fapi/v1/openOrders", params={"symbol": symbol})
        for o in rows or []:
            if o.get("side") != close_side:
                continue
            if o.get("type") not in types:
                continue
            if not o.get("closePosition"):
                continue
            try:
                bc.delete("/fapi/v1/order", params={"symbol": symbol, "orderId": o["orderId"]})
            except Exception as e:
                logger.warning(f"[Binance][{symbol}] cancel close-order {o.get('orderId')} warn: {e}")
    except Exception as e:
        logger.warning(f"[Binance][{symbol}] list openOrders warn: {e}")


def place_pos_tpsl_full(symbol: str, hold_side: str,
                        tp_trig: float, tp_exec: float,
                        sl_trig: float, sl_exec: float) -> bool:
    """Replace existing TP/SL closePosition orders with new ones."""
    if _dry_run_skip("place_pos_tpsl_full", symbol=symbol, hold=hold_side, tp=tp_trig, sl=sl_trig):
        return True
    close_side = "SELL" if hold_side == "long" else "BUY"
    _cancel_position_close_orders(symbol, close_side, ("STOP_MARKET", "TAKE_PROFIT_MARKET"))
    for attempt in range(3):
        try:
            bc.post("/fapi/v1/order", {
                "symbol": symbol, "side": close_side,
                "type": "STOP_MARKET",
                "stopPrice": round_price(sl_trig, symbol),
                "closePosition": "true",
                "workingType": "MARK_PRICE",
                "priceProtect": "true",
            })
            bc.post("/fapi/v1/order", {
                "symbol": symbol, "side": close_side,
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": round_price(tp_trig, symbol),
                "closePosition": "true",
                "workingType": "MARK_PRICE",
                "priceProtect": "true",
            })
            return True
        except Exception as e:
            logger.warning(f"[Binance][{symbol}] TP/SL attempt {attempt+1}/3: {e}")
            if attempt < 2:
                _time.sleep(1.5)
    return False


def place_pos_sl_only(symbol: str, hold_side: str, sl_trig: float, sl_exec: float) -> None:
    """Place SL-only closePosition order on this side. Cancels any existing STOP_MARKET first."""
    if _dry_run_skip("place_pos_sl_only", symbol=symbol, hold=hold_side, sl=sl_trig):
        return
    close_side = "SELL" if hold_side == "long" else "BUY"
    _cancel_position_close_orders(symbol, close_side, ("STOP_MARKET",))
    bc.post("/fapi/v1/order", {
        "symbol": symbol, "side": close_side,
        "type": "STOP_MARKET",
        "stopPrice": round_price(sl_trig, symbol),
        "closePosition": "true",
        "workingType": "MARK_PRICE",
        "priceProtect": "true",
    })


def place_profit_plan(symbol: str, hold_side: str, qty_str: str,
                      trigger: float, execute: str = "0") -> None:
    """Partial TP as a reduce-only TAKE_PROFIT_MARKET order with explicit qty."""
    if _dry_run_skip("place_profit_plan", symbol=symbol, hold=hold_side,
                     qty=qty_str, trigger=trigger):
        return
    close_side = "SELL" if hold_side == "long" else "BUY"
    bc.post("/fapi/v1/order", {
        "symbol": symbol, "side": close_side,
        "type": "TAKE_PROFIT_MARKET",
        "stopPrice": round_price(trigger, symbol),
        "quantity": qty_str,
        "reduceOnly": "true",
        "workingType": "MARK_PRICE",
        "priceProtect": "true",
    })


def place_moving_plan(symbol: str, hold_side: str, qty_str: str,
                      trigger: float, range_rate: str) -> None:
    """
    Trailing stop: TRAILING_STOP_MARKET reduce-only.

    range_rate semantics (matches Bitget/Bybit): values >= 1 are integer-percent
    (10 → 10%); values < 1 are decimal fractions (0.05 → 5%). Binance's
    callbackRate field expects integer-percent string with 1 decimal place,
    range [0.1, 5.0].
    """
    if _dry_run_skip("place_moving_plan", symbol=symbol, hold=hold_side,
                     trigger=trigger, range_rate=range_rate):
        return
    try:
        raw = float(range_rate)
    except (ValueError, TypeError):
        raw = 10.0
    pct = raw if raw >= 1 else raw * 100.0
    # Binance callbackRate is [0.1, 5.0]
    pct = max(0.1, min(5.0, pct))
    close_side = "SELL" if hold_side == "long" else "BUY"
    bc.post("/fapi/v1/order", {
        "symbol": symbol, "side": close_side,
        "type": "TRAILING_STOP_MARKET",
        "quantity": qty_str,
        "activationPrice": round_price(trigger, symbol),
        "callbackRate": f"{pct:.1f}",
        "reduceOnly": "true",
        "workingType": "MARK_PRICE",
    })


def refresh_plan_exits(symbol: str, hold_side: str, new_trail_trigger: float = 0) -> bool:
    """Cancel existing partial-TP + trailing stop and re-place at new total/2 qty."""
    if _dry_run_skip("refresh_plan_exits", symbol=symbol, hold=hold_side, trigger=new_trail_trigger):
        return True
    close_side = "SELL" if hold_side == "long" else "BUY"
    # 1. Find the existing reduce-only partial TP + trailing stop.
    try:
        rows = bc.get("/fapi/v1/openOrders", params={"symbol": symbol})
    except Exception as e:
        logger.error(f"[Binance][{symbol}] refresh_plan_exits openOrders failed: {e}")
        return False
    existing_tp = None
    existing_trail = None
    for o in rows or []:
        if o.get("side") != close_side or not o.get("reduceOnly"):
            continue
        if o.get("type") == "TAKE_PROFIT_MARKET":
            existing_tp = o
        elif o.get("type") == "TRAILING_STOP_MARKET":
            existing_trail = o
    if not existing_tp and not existing_trail:
        logger.warning(f"[Binance][{symbol}] refresh_plan_exits: no reduce-only TP/trailing found")
        return False
    # 2. Pick trigger: caller arg or existing TP's stopPrice.
    trigger = new_trail_trigger
    if trigger <= 0 and existing_tp:
        try:
            trigger = float(existing_tp.get("stopPrice") or 0)
        except (ValueError, TypeError):
            trigger = 0
    if trigger <= 0:
        logger.error(f"[Binance][{symbol}] refresh_plan_exits: no valid trigger")
        return False
    # 3. Cancel both.
    for o in (existing_tp, existing_trail):
        if not o:
            continue
        try:
            bc.delete("/fapi/v1/order", params={"symbol": symbol, "orderId": o["orderId"]})
            _time.sleep(0.3)
        except Exception as e:
            logger.warning(f"[Binance][{symbol}] cancel {o.get('type')} warn: {e}")
    _time.sleep(0.5)
    # 4. Read new total qty and re-place.
    positions = get_all_open_positions()
    total_qty = float((positions.get(symbol) or {}).get("qty", 0))
    if total_qty <= 0:
        logger.error(f"[Binance][{symbol}] refresh_plan_exits: position gone")
        return False
    half_qty = round_qty(total_qty / 2, symbol)
    rest_qty = round_qty(total_qty - float(half_qty), symbol)
    range_rate = "0.10"
    for attempt in range(3):
        try:
            place_profit_plan(symbol, hold_side, half_qty, trigger)
            _time.sleep(0.5)
            place_moving_plan(symbol, hold_side, rest_qty, trigger, range_rate)
            logger.info(
                f"[Binance][{symbol}] ✅ Plan exits refreshed: partial_tp={half_qty}@{trigger}, "
                f"trailing_stop @{trigger}"
            )
            return True
        except Exception as e:
            logger.warning(f"[Binance][{symbol}] refresh_plan_exits attempt {attempt+1}/3: {e}")
            if attempt < 2:
                _time.sleep(1.5)
    return False


def update_position_sl(symbol: str, new_sl: float, hold_side: str = "long") -> bool:
    """Replace SL by cancelling the existing closePosition STOP_MARKET and placing a new one."""
    if _dry_run_skip("update_position_sl", symbol=symbol, hold=hold_side, new_sl=new_sl):
        return True
    close_side = "SELL" if hold_side == "long" else "BUY"
    _cancel_position_close_orders(symbol, close_side, ("STOP_MARKET",))
    sl_str = round_price(new_sl, symbol)
    for attempt in range(3):
        try:
            bc.post("/fapi/v1/order", {
                "symbol": symbol, "side": close_side,
                "type": "STOP_MARKET",
                "stopPrice": sl_str,
                "closePosition": "true",
                "workingType": "MARK_PRICE",
                "priceProtect": "true",
            })
            return True
        except Exception as e:
            logger.warning(f"[Binance][{symbol}] update_position_sl attempt {attempt+1}/3: {e}")
            if attempt < 2:
                _time.sleep(1.0)
    return False


# ── Plan (conditional / trigger) orders ───────────────────── #

def place_plan_order(side: str, symbol: str, trigger_price: float,
                     sl_price: float, tp_price: float, qty_str: str) -> str:
    """Conditional market entry via STOP_MARKET. Returns orderId."""
    if _dry_run_skip("place_plan_order", symbol=symbol, side=side,
                     trigger=trigger_price, sl=sl_price, tp=tp_price, qty=qty_str):
        return f"DRY-{symbol}-{int(_time.time())}"
    binance_side = "BUY" if side.lower() == "buy" else "SELL"
    body = {
        "symbol": symbol, "side": binance_side,
        "type": "STOP_MARKET",
        "stopPrice": round_price(trigger_price, symbol),
        "quantity": qty_str,
        "workingType": "MARK_PRICE",
        "priceProtect": "true",
    }
    info = sym_info(symbol)
    logger.info(
        f"[Binance][{symbol}] /fapi/v1/order STOP_MARKET {binance_side} qty={qty_str} "
        f"trigger={body['stopPrice']} (min_qty={info.get('min_trade_num')}, "
        f"qty_step={info.get('qty_step')}, min_notional={info.get('min_notional')})"
    )
    resp = bc.post("/fapi/v1/order", body)
    order_id = resp.get("orderId")
    if not order_id:
        raise RuntimeError(f"[Binance] place_plan_order: missing orderId in response: {resp}")
    # Attach SL/TP as closePosition orders so they fire only after the entry fills.
    # Strictly speaking these activate immediately; on Binance there is no native
    # "OCO with parent" for futures. Callers that need atomicity must monitor the
    # entry fill themselves and call place_pos_tpsl_full after. The simple version
    # here matches Bybit's behaviour: SL/TP are attached now, will fire when triggered.
    close_side = "SELL" if binance_side == "BUY" else "BUY"
    if sl_price > 0:
        try:
            bc.post("/fapi/v1/order", {
                "symbol": symbol, "side": close_side,
                "type": "STOP_MARKET",
                "stopPrice": round_price(sl_price, symbol),
                "closePosition": "true",
                "workingType": "MARK_PRICE",
                "priceProtect": "true",
            })
        except RuntimeError as e:
            logger.warning(f"[Binance][{symbol}] plan SL attach warn: {e}")
    if tp_price and tp_price > 0:
        try:
            bc.post("/fapi/v1/order", {
                "symbol": symbol, "side": close_side,
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": round_price(tp_price, symbol),
                "closePosition": "true",
                "workingType": "MARK_PRICE",
                "priceProtect": "true",
            })
        except RuntimeError as e:
            logger.warning(f"[Binance][{symbol}] plan TP attach warn: {e}")
    return str(order_id)


def cancel_plan_order(symbol: str, order_id: str) -> None:
    if _dry_run_skip("cancel_plan_order", symbol=symbol, orderId=order_id):
        return
    bc.delete("/fapi/v1/order", params={"symbol": symbol, "orderId": order_id})


def cancel_all_orders(symbol: str) -> None:
    if _dry_run_skip("cancel_all_orders", symbol=symbol):
        return
    try:
        bc.delete("/fapi/v1/allOpenOrders", params={"symbol": symbol})
    except Exception as e:
        # -2011 = "Unknown order sent" — nothing to cancel; safe.
        if "-2011" in str(e):
            logger.debug(f"[Binance][{symbol}] cancel_all_orders: nothing to cancel")
        else:
            logger.warning(f"[Binance][{symbol}] cancel_all_orders warn: {e}")


def get_order_fill(symbol: str, order_id: str) -> dict:
    """Status of a single order. Returns {status, fill_price}."""
    try:
        o = bc.get("/fapi/v1/order", params={"symbol": symbol, "orderId": order_id})
        status = (o.get("status") or "").upper()
        if status in ("NEW",):
            return {"status": "live", "fill_price": 0.0}
        if status in ("FILLED", "PARTIALLY_FILLED"):
            fill = float(o.get("avgPrice") or 0) or get_single_position_entry(symbol)
            return {"status": "filled", "fill_price": fill}
        return {"status": "cancelled", "fill_price": 0.0}
    except Exception as e:
        logger.debug(f"[Binance][{symbol}] order lookup failed: {e}")
    return {"status": "cancelled", "fill_price": 0.0}


# ── History / closed positions ──────────────────────────── #

def get_history_position(symbol: str,
                         open_time_iso: str | None = None,
                         entry_price:   float | None = None,
                         retries: int = 3,
                         retry_delay: float = 1.5) -> dict | None:
    """
    Total realized P&L from /fapi/v1/income?incomeType=REALIZED_PNL, summed
    over the trade's time window. Binance returns ONE income row per close
    event so we sum all rows newer than open_time_iso.
    """
    for attempt in range(retries):
        try:
            params: dict = {"symbol": symbol, "incomeType": "REALIZED_PNL", "limit": "100"}
            if open_time_iso:
                try:
                    dt = datetime.fromisoformat(open_time_iso)
                    params["startTime"] = str(int(dt.timestamp() * 1000))
                except Exception:
                    pass
            rows = bc.get("/fapi/v1/income", params=params)
            if not rows:
                return None
            total_pnl = sum(float(r.get("income") or 0) for r in rows)
            if total_pnl == 0:
                if attempt < retries - 1:
                    _time.sleep(retry_delay)
                    continue
                return None
            # Exit price + close time from most recent userTrades row.
            exit_price = None
            close_dt = None
            try:
                trades = bc.get("/fapi/v1/userTrades",
                                params={"symbol": symbol, "limit": "20"})
                # The last trade row that's a position-close (reduceOnly side opposite of position)
                # is best-effort — sufficient for logging.
                if trades:
                    last = trades[-1]
                    exit_price = float(last.get("price") or 0) or None
                    ts = last.get("time")
                    if ts:
                        close_dt = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).isoformat()
            except Exception:
                pass
            return {"pnl": total_pnl, "exit_price": exit_price, "close_time": close_dt}
        except Exception as e:
            logger.warning(f"[Binance][{symbol}] get_history_position error: {e}")
            return None
    return None


def get_realized_pnl(symbol: str, retries: int = 3, retry_delay: float = 1.5) -> float | None:
    """Most recent REALIZED_PNL income row's value."""
    for attempt in range(retries):
        try:
            rows = bc.get("/fapi/v1/income",
                          params={"symbol": symbol, "incomeType": "REALIZED_PNL", "limit": "1"})
            if rows:
                pnl = float(rows[0].get("income") or 0)
                if pnl != 0:
                    return pnl
                if attempt < retries - 1:
                    _time.sleep(retry_delay)
        except Exception as e:
            logger.warning(f"[Binance][{symbol}] get_realized_pnl error: {e}")
            return None
    return None
```

- [ ] **Step 3.2: Import check**

```bash
python -c "import binance; print('OK', len([x for x in dir(binance) if not x.startswith('_')]))"
```

Expected: prints "OK <number>"

- [ ] **Step 3.3: Commit**

```bash
git add binance.py
git commit -m "feat(binance): exchange operations mirror of bybit.py public surface"
```

---

## Task 4: High-level trader (binance_trader.py)

**Files:**
- Create: `binance_trader.py`

This is a structural clone of [bybit_trader.py](bybit_trader.py). Replace every `bybit`/`bb` with `binance`/`bb` (or import as `binance as bn`), `Bybit` log prefix → `Binance`, and `config_bybit` → `config_binance`. The strategy adapter dispatch (`_place_s1_exits`, `_place_s2_exits`, `_place_s5_exits`) calls into `strategies.*._place_exits` / `_place_partial_trail_exits` — these are reused unchanged via the `bitget`/`trader` aliasing.

- [ ] **Step 4.1: Create the file**

Full content: copy [bybit_trader.py](bybit_trader.py) and apply the substitutions:
- `import bybit_client as bc` → `import binance_client as bc`
- `import bybit as bb` → `import binance as bn` (then use `bn.X` everywhere it had `bb.X`)
- `from config_bybit` → `from config_binance`
- `from config_bybit_s1` → `from config_binance_s1`
- `[Bybit]` log prefix → `[Binance]`
- Remove `CATEGORY`/`SETTLE_COIN` imports — Binance doesn't need them at this layer

- [ ] **Step 4.2: Import check**

```bash
python -c "import binance_trader; print('OK')"
```

- [ ] **Step 4.3: Commit**

```bash
git add binance_trader.py
git commit -m "feat(binance): high-level trader mirror of bybit_trader.py"
```

---

## Task 5: Scanner (binance_scanner.py)

**Files:**
- Create: `binance_scanner.py`

Mirror of [bybit_scanner.py](bybit_scanner.py). Binance ticker endpoint:
- `GET /fapi/v1/ticker/24hr` returns a list of `{symbol, lastPrice, priceChangePercent, quoteVolume, ...}`.
- `priceChangePercent` is a percentage string (e.g. `"2.35"` = +2.35%) — NOT a decimal fraction like Bybit. **No multiplication by 100.**
- `quoteVolume` is the 24h USDT volume.
- For top-of-book depth, the 24h ticker endpoint does not include bidQty/askQty fields. Liquidity check defaults to off; if enabled, call `/fapi/v1/ticker/bookTicker` separately.

- [ ] **Step 5.1: Create the file**

```python
"""
binance_scanner.py — Pair Scanner + Volume-Weighted Market Sentiment (Binance)

Endpoint: GET /fapi/v1/ticker/24hr (no auth)

Key response fields per ticker:
  symbol              — e.g. "BTCUSDT"
  quoteVolume         — 24h volume in USDT
  priceChangePercent  — 24h price change as PERCENT string (e.g. "2.35" = +2.35%)
  lastPrice           — last price

Returns the same (qualified_pairs, SentimentResult) tuple as scanner.py so
binance_bot.py can be a structural clone of bot.py.
"""

import math
import logging
from dataclasses import dataclass

import binance_client as bc
from config_binance import (
    MIN_VOLUME_USDT, MAX_PRICE_USDT, SENTIMENT_THRESHOLD,
    LIQUIDITY_CHECK_ENABLED, MIN_OB_DEPTH_USDT,
)

logger = logging.getLogger(__name__)


@dataclass
class SentimentResult:
    direction:      str
    bullish_weight: float
    green_count:    int
    red_count:      int
    total_pairs:    int
    green_volume:   float
    red_volume:     float


def _filter_by_liquidity(pairs: list[str], depth_map: dict[str, float]) -> list[str]:
    liquid, excluded = [], []
    for sym in pairs:
        if depth_map.get(sym, 0.0) >= MIN_OB_DEPTH_USDT:
            liquid.append(sym)
        else:
            excluded.append(sym)
    if excluded:
        logger.info(
            f"[Binance] Liquidity filter: removed {len(excluded)} illiquid pair(s): "
            + ", ".join(f"{s}(${depth_map.get(s, 0.0):.0f})" for s in excluded)
        )
    return liquid


def _fetch_book_depth(symbols: list[str]) -> dict[str, float]:
    """Top-of-book bid+ask USDT depth via /fapi/v1/ticker/bookTicker (single call returns all)."""
    try:
        rows = bc.get_public("/fapi/v1/ticker/bookTicker")
    except Exception as e:
        logger.warning(f"[Binance] bookTicker fetch failed: {e}")
        return {}
    by_symbol = {r.get("symbol"): r for r in rows or []}
    out: dict[str, float] = {}
    for s in symbols:
        r = by_symbol.get(s)
        if not r:
            out[s] = 0.0
            continue
        try:
            bid_d = float(r.get("bidQty") or 0) * float(r.get("bidPrice") or 0)
            ask_d = float(r.get("askQty") or 0) * float(r.get("askPrice") or 0)
            out[s] = bid_d + ask_d
        except (ValueError, TypeError):
            out[s] = 0.0
    return out


def get_qualified_pairs_and_sentiment() -> tuple[list[str], SentimentResult]:
    try:
        tickers = bc.get_public("/fapi/v1/ticker/24hr")
    except Exception as e:
        logger.error(f"[Binance] Scanner: ticker fetch failed: {e}")
        return [], SentimentResult("NEUTRAL", 0.5, 0, 0, 0, 0.0, 0.0)

    qualified:    list[str] = []
    green_volume = 0.0
    red_volume   = 0.0
    green_count  = 0
    red_count    = 0
    btc_change   = 0.0

    for t in tickers or []:
        symbol = t.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue
        try:
            vol_usdt     = float(t.get("quoteVolume") or 0)
            last_pr      = float(t.get("lastPrice")   or 0)
            price_change = float(t.get("priceChangePercent") or 0)   # ALREADY a percent
        except (ValueError, TypeError):
            continue

        if vol_usdt != 0 and vol_usdt < MIN_VOLUME_USDT:
            continue
        if MAX_PRICE_USDT != 0 and last_pr > MAX_PRICE_USDT:
            continue

        qualified.append(symbol)
        if symbol == "BTCUSDT":
            btc_change = price_change

        magnitude = abs(price_change)
        weight = math.sqrt(vol_usdt) * magnitude if magnitude >= 0.15 else 0
        if price_change > 0:
            green_volume += weight
            green_count  += 1
        else:
            red_volume   += weight
            red_count    += 1

    qualified.sort()

    total_volume = green_volume + red_volume
    bullish_w    = (green_volume / total_volume) if total_volume > 0 else 0.5

    if btc_change < -3.0:
        direction = "BEARISH"
    elif bullish_w >= SENTIMENT_THRESHOLD:
        direction = "BULLISH"
    elif bullish_w <= (1 - SENTIMENT_THRESHOLD):
        direction = "BEARISH"
    else:
        direction = "NEUTRAL"

    sentiment = SentimentResult(
        direction      = direction,
        bullish_weight = round(bullish_w, 4),
        green_count    = green_count,
        red_count      = red_count,
        total_pairs    = len(qualified),
        green_volume   = round(green_volume, 0),
        red_volume     = round(red_volume, 0),
    )

    logger.info(
        f"[Binance] Scanner: {len(qualified)} pairs | "
        f"Sentiment: {direction} ({bullish_w*100:.1f}% green by vol×magnitude) | "
        f"🟢 {green_count}  🔴 {red_count} | BTC={btc_change:+.1f}%"
    )
    if LIQUIDITY_CHECK_ENABLED:
        depth_map = _fetch_book_depth(qualified)
        qualified = _filter_by_liquidity(qualified, depth_map)
    return qualified, sentiment


def get_qualified_pairs() -> list[str]:
    pairs, _ = get_qualified_pairs_and_sentiment()
    return pairs
```

- [ ] **Step 5.2: Import check**

```bash
python -c "import binance_scanner; print('OK')"
```

- [ ] **Step 5.3: Commit**

```bash
git add binance_scanner.py
git commit -m "feat(binance): pair scanner + sentiment via /fapi/v1/ticker/24hr"
```

---

## Task 6: Entry point (binance_bot.py)

**Files:**
- Create: `binance_bot.py`

Direct structural clone of [bybit_bot.py](bybit_bot.py). All `Bybit`/`bybit` → `Binance`/`binance`. Add `bybit_bot` to `_FORBIDDEN_MODULES` (and vice versa later) so both bots cannot share a Python process.

- [ ] **Step 6.1: Create the file**

```python
"""
binance_bot.py — Binance USDT-M Futures Entry Point

This file is intentionally short. It does NOT clone bot.py's 2700+ lines.

Instead, it installs sys.modules aliases at startup that redirect:
    config       → config_binance
    config_s1..7 → config_binance_s1..7
    bitget       → binance
    trader       → binance_trader
    scanner      → binance_scanner

…and then runs bot.MTFBot().run() — the same main loop the Bitget bot uses,
but every exchange-coupled lookup transparently targets Binance.

⚠️  IMPORTANT — DO NOT import this module from bot.py, ig_bot.py, or bybit_bot.py.
    The aliases are process-global; mixing bots in one Python process will
    corrupt the sibling bots' exchange references. Run as: `python binance_bot.py`
"""

import sys
import logging

# ── 1. Forbid same-process collisions ──────────────────────────── #

_FORBIDDEN_MODULES = ("bot", "ig_bot", "bybit_bot")
for _m in _FORBIDDEN_MODULES:
    if _m in sys.modules:
        raise RuntimeError(
            f"binance_bot.py cannot run in the same Python process as '{_m}'. "
            f"Run as a separate process."
        )

# ── 2. Reject paper mode (not supported for Binance) ───────────── #

if "--paper" in sys.argv:
    print("ERROR: --paper is not supported for Binance. Use config_binance.DRY_RUN instead.")
    sys.exit(1)

# ── 3. Install sys.modules aliases BEFORE importing bot.py ─────── #

import config_binance
sys.modules["config"] = config_binance

import config_binance_s1
import config_binance_s2
import config_binance_s3
import config_binance_s4
import config_binance_s5
import config_binance_s6
import config_binance_s7
sys.modules["config_s1"] = config_binance_s1
sys.modules["config_s2"] = config_binance_s2
sys.modules["config_s3"] = config_binance_s3
sys.modules["config_s4"] = config_binance_s4
sys.modules["config_s5"] = config_binance_s5
sys.modules["config_s6"] = config_binance_s6
sys.modules["config_s7"] = config_binance_s7

import binance
import binance_trader
import binance_scanner
sys.modules["bitget"]  = binance
sys.modules["trader"]  = binance_trader
sys.modules["scanner"] = binance_scanner

# ── 4. Redirect state file BEFORE bot.py touches state ─────────── #

import state
state.set_file(config_binance.STATE_FILE)

# ── 5. Import bot.py — aliases are now in effect ───────────────── #

import bot

logger = logging.getLogger(__name__)
logger.info(
    f"[Binance] Aliases installed: config→config_binance, bitget→binance, "
    f"trader→binance_trader, scanner→binance_scanner | "
    f"DRY_RUN={config_binance.DRY_RUN} | state={config_binance.STATE_FILE}"
)


# ── 6. Main entry ──────────────────────────────────────────────── #

if __name__ == "__main__":
    if not config_binance.API_KEY or not config_binance.API_SECRET:
        print("ERROR: Set BINANCE_API_KEY and BINANCE_API_SECRET in environment or .env")
        sys.exit(1)

    # Force one-way position mode at startup (idempotent).
    binance.ensure_one_way_mode()

    bot._check_disclaimer()
    bot.MTFBot().run()
```

- [ ] **Step 6.2: Import check (without running)**

```bash
python -c "
import sys
# Simulate the alias install up to (but not including) importing bot.
import config_binance
sys.modules['config'] = config_binance
import binance, binance_trader, binance_scanner
print('Binance modules import OK')
"
```

- [ ] **Step 6.3: Commit**

```bash
git add binance_bot.py
git commit -m "feat(binance): thin entry point installs sys.modules aliases"
```

---

## Task 7: Add `bybit_bot` ↔ `binance_bot` mutual exclusion

**Files:**
- Modify: `bybit_bot.py:27`
- Modify: `ig_bot.py` (only if it has a `_FORBIDDEN_MODULES` block)

- [ ] **Step 7.1: Update bybit_bot.py**

Change `_FORBIDDEN_MODULES = ("bot", "ig_bot")` → `_FORBIDDEN_MODULES = ("bot", "ig_bot", "binance_bot")` so running both Bybit and Binance in one process fails fast.

- [ ] **Step 7.2: Verify ig_bot.py**

```bash
grep -n "_FORBIDDEN_MODULES" ig_bot.py || echo "ig_bot has no forbidden-modules check; skipping."
```

If present, add `"binance_bot"` to its tuple. If absent, skip.

- [ ] **Step 7.3: Commit**

```bash
git add bybit_bot.py
git commit -m "chore(bybit): forbid co-running with binance_bot.py"
```

---

## Task 8: Import-sanity tests

**Files:**
- Create: `tests/test_binance_imports.py`

- [ ] **Step 8.1: Write the test**

```python
"""All four bot stacks must import cleanly in isolated subprocesses."""

import subprocess
import sys


def _run(code: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=30,
    )
    return proc.returncode, (proc.stdout + proc.stderr)


def test_bitget_imports():
    code, out = _run("import bot; print('OK')")
    assert code == 0, out


def test_ig_imports():
    code, out = _run("import ig_bot; print('OK')")
    assert code == 0, out


def test_bybit_modules_import():
    code, out = _run(
        "import bybit, bybit_trader, bybit_scanner, bybit_client; print('OK')"
    )
    assert code == 0, out


def test_binance_modules_import():
    code, out = _run(
        "import binance, binance_trader, binance_scanner, binance_client; print('OK')"
    )
    assert code == 0, out


def test_binance_bot_aliases_install():
    # Don't run the main loop — just verify alias install + bot import succeeds.
    code, out = _run(
        "import sys; "
        "import config_binance; sys.modules['config'] = config_binance; "
        "import binance, binance_trader, binance_scanner; "
        "sys.modules['bitget'] = binance; "
        "sys.modules['trader'] = binance_trader; "
        "sys.modules['scanner'] = binance_scanner; "
        "for n in (1,2,3,4,5,6,7):\n"
        "    mod = __import__(f'config_binance_s{n}'); "
        "    sys.modules[f'config_s{n}'] = mod\n"
        "import state; state.set_file(config_binance.STATE_FILE); "
        "import bot; print('OK')"
    )
    assert code == 0, out
```

- [ ] **Step 8.2: Run**

```bash
pytest tests/test_binance_imports.py -v
```

Expected: 5 passed.

- [ ] **Step 8.3: Commit**

```bash
git add tests/test_binance_imports.py
git commit -m "test(binance): all four bot stacks import cleanly"
```

---

## Task 9: Update DEPENDENCIES.md

**Files:**
- Modify: `docs/DEPENDENCIES.md` section 1 (architecture overview) and section 5 (config)

- [ ] **Step 9.1: Add Binance box to the architecture diagram (§ 1)**

After the Bybit box, append a Binance box mirroring its shape:

```
┌─────────────────────────────────────────────────────────────┐
│                BINANCE BOT (binance_bot.py)                │
│  Crypto USDT-M Futures · S1-S7 strategies                  │
│  Thin entry point: installs sys.modules aliases then        │
│  delegates to bot.MTFBot().run() — no logic duplication.    │
│  Output: binance_state.json, binance_trades.csv,           │
│          binance_bot.log                                    │
└─────────────────────────────────────────────────────────────┘
```

- [ ] **Step 9.2: Add Binance to the "Shared by all four bots" line**

Find "Shared by all three bots (Bitget, IG, Bybit):" and update to four bots, listing Binance.

- [ ] **Step 9.3: Note the Binance-specific TP/SL non-atomicity**

In § 10 (Confusing Names & Pitfalls), add an entry:

```
### 10.X Binance TP/SL is NOT atomic on entry

Unlike Bybit (where stopLoss/takeProfit fields on /v5/order/create attach the
exits atomically), Binance USDT-M Futures requires SEPARATE orders:
  - STOP_MARKET with closePosition=true for SL
  - TAKE_PROFIT_MARKET with closePosition=true for TP

binance.place_market_order() handles this by issuing the two follow-up orders
after the entry market order. Brief race window between entry fill and SL/TP
attachment — acceptable for swing strategies, NOT for HFT.
```

- [ ] **Step 9.4: Commit**

```bash
git add docs/DEPENDENCIES.md
git commit -m "docs: add Binance bot to architecture + TP/SL non-atomic note"
```

---

## Task 10: Push and open PR

- [ ] **Step 10.1: Push**

```bash
git push -u origin ship-binance
```

- [ ] **Step 10.2: Open PR**

```bash
gh pr create --base master --head ship-binance \
  --title "Add Binance USDT-M Futures bot" \
  --body "$(cat <<'EOF'
## Summary
- Adds binance_bot.py as a fourth bot mirroring the Bybit aliasing pattern: thin entry point installs sys.modules aliases (config → config_binance, bitget → binance, trader → binance_trader, scanner → binance_scanner) then runs bot.MTFBot().run() unchanged.
- New files: binance.py (FAPI exchange ops), binance_client.py (HMAC-SHA256 signing), binance_trader.py (open_long/open_short), binance_scanner.py (24h ticker sentiment), binance_bot.py (entry point), config_binance.py + config_binance_s1..s7.py.
- DRY_RUN defaults to True. Position mode forced to one-way at startup.

## Test plan
- [ ] pytest tests/test_binance_client.py + tests/test_binance_imports.py — all pass
- [ ] python binance_bot.py dry-runs an evaluation cycle without auth keys (should error on missing key, not crash on import)
- [ ] With test credentials on testnet: scanner returns pairs, get_candles returns 100 rows, get_usdt_balance returns a float

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review

**Spec coverage:**
- Config ✓ (Task 1)
- Low-level client + signing ✓ (Task 2)
- Exchange ops mirroring bybit.py ✓ (Task 3)
- High-level trader ✓ (Task 4)
- Scanner ✓ (Task 5)
- Entry point + aliasing ✓ (Task 6)
- Mutual exclusion ✓ (Task 7)
- Tests ✓ (Tasks 2.1, 8)
- Docs ✓ (Task 9)
- PR ✓ (Task 10)

**Placeholders:** none — every step has either full code or a precise command.

**Type consistency:**
- `place_market_order` returns `dict` in both bybit and binance (verified)
- `place_plan_order` returns `str` (orderId) in both
- `get_all_open_positions` returns `dict[str, dict]` with the same inner keys (`side`, `entry_price`, `qty`, `unrealised_pnl`, `mark_price`, `margin`, `leverage`)
- `SentimentResult` dataclass has identical fields

**Risk-flagged:**
- **TP/SL race window** on Binance entry — documented in Task 9.3. Strategies that use `place_pos_tpsl_full` after a deliberate delay (S5 limit-entry path) are unaffected; the bare `place_market_order` path is briefly naked. Acceptable for swing strategies.
- **Trailing stop callback rate clamped to [0.1, 5.0]** — Binance's hard limit. S2's 10% trail will be capped at 5%; flag in implementation but ship as-is.
- **min_notional** on Binance USDT-M is typically $5 — same order of magnitude as Bybit; `round_qty(mark_price=...)` handles it identically.
