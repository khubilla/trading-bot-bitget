"""
bybit.py — Bybit USDT-Perpetual API operations.

Wraps the V5 endpoint-level calls over the low-level HTTP/auth client
(bybit_client.py). No strategy knowledge lives here.

Mirrors the public surface of bitget.py so strategies/* exit helpers and
bybit_trader.py can be near-clones of their Bitget counterparts.
"""

import math
import time as _time
import logging
from datetime import datetime, timezone

import pandas as pd

import bybit_client as bc
from config_bybit import CATEGORY, SETTLE_COIN

logger = logging.getLogger(__name__)
_sym_cache: dict[str, dict] = {}


# ── DRY_RUN guard ─────────────────────────────────────────────────── #
#
# bybit_trader.open_long/short already intercept the top-level entry call when
# config_bybit.DRY_RUN is True, but strategies/* exit helpers reach into
# bybit.place_pos_sl_only / place_profit_plan / place_moving_plan directly via
# `import bitget as bg` (aliased to bybit). Without a guard here those calls
# hit the real Bybit API and fail — there's no position to attach SL/TP to.
# This helper short-circuits every write at the lowest level so DRY_RUN is
# truly read-only.

def _dry_run_active() -> bool:
    """Re-read DRY_RUN every call so a config flip takes effect on next call."""
    try:
        import config_bybit
        return bool(config_bybit.DRY_RUN)
    except Exception:
        return False


def _dry_run_skip(action: str, **payload) -> bool:
    if not _dry_run_active():
        return False
    short = " ".join(f"{k}={v}" for k, v in payload.items() if v is not None and v != "")
    logger.info(f"[Bybit][DRY_RUN] {action} {short}".rstrip())
    return True


# ── Bybit candle interval mapping ────────────────────────────────── #
#   Bitget:  "1m"   "5m"   "15m"   "1H"   "4H"   "1D"
#   Bybit:   "1"    "5"    "15"    "60"   "240"  "D"
_INTERVAL_MAP = {
    "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
    "1H": "60", "1h": "60",
    "2H": "120", "2h": "120",
    "4H": "240", "4h": "240",
    "6H": "360", "6h": "360",
    "12H": "720", "12h": "720",
    "1D": "D",  "1d": "D",
    "1W": "W",  "1w": "W",
}


def _map_interval(interval: str) -> str:
    return _INTERVAL_MAP.get(interval, interval)


# ── Symbol metadata / rounding ───────────────────────────────────── #

def _load_symbol_cache():
    global _sym_cache
    if _sym_cache:
        return
    # Bybit returns max 500 contracts per page; paginate via nextPageCursor.
    # Without this, any symbol alphabetically after the 500th (~SKRUSDT) falls
    # back to default rounding (qty_step=0.001, tick_size=0.01), corrupting
    # qty/price for ~half the linear universe.
    rows: list = []
    cursor = ""
    page = 0
    while True:
        params = {"category": CATEGORY, "limit": "1000"}
        if cursor:
            params["cursor"] = cursor
        data = bc.get_public("/v5/market/instruments-info", params=params)
        result = data.get("result") or {}
        page_rows = result.get("list") or []
        rows.extend(page_rows)
        cursor = result.get("nextPageCursor") or ""
        page += 1
        if not cursor or not page_rows or page >= 20:  # 20-page safety cap (~20k symbols)
            break
    for s in rows:
        symbol = s.get("symbol")
        if not symbol:
            continue
        lot   = s.get("lotSizeFilter")  or {}
        price = s.get("priceFilter")    or {}
        qty_step     = float(lot.get("qtyStep")          or "0.001")
        min_qty      = float(lot.get("minOrderQty")      or "0.001")
        tick_size    = float(price.get("tickSize")       or "0.01")
        # Bybit V5 also enforces a minimum *notional* value per order (qty × price ≥ N).
        # When equity × trade_size_pct × leverage is small, the qty-derived-from-notional
        # can satisfy minOrderQty but still fail minNotionalValue. round_qty() takes a
        # mark_price arg to handle this.
        min_notional = float(lot.get("minNotionalValue") or "0")
        _sym_cache[symbol] = {
            "qty_step":      qty_step,
            "min_trade_num": min_qty,
            "tick_size":     tick_size,
            "min_notional":  min_notional,
            "price_place":   _decimals(tick_size),
            "volume_place":  _decimals(qty_step),
        }
    logger.info(f"[Bybit] Symbol cache loaded: {len(_sym_cache)} contracts")


def _decimals(step: float) -> int:
    """Return number of decimal places implied by a step size (e.g. 0.001 → 3)."""
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
    """
    Floor qty to qty_step, then enforce min_trade_num. When `mark_price` is
    provided AND the symbol has a min_notional, also bump up so that
    qty × mark_price ≥ min_notional (Bybit V5 rejects orders below this with
    "Qty invalid").

    Callers that are *splitting* an existing position qty (e.g. half/rest for
    profit_plan + moving_plan) should NOT pass mark_price — the notional
    requirement was already satisfied by the original entry order.
    """
    info = sym_info(symbol)
    step = info["qty_step"]
    # +1e-9 absorbs float error so a remainder like 0.05 (stored as
    # 0.04999999999999) doesn't floor down a tick (AMDUSDT split bug). It does
    # not change genuine flooring (e.g. 4.5 steps still floors to 4).
    qty  = math.floor(qty / step + 1e-9) * step
    qty  = max(qty, info["min_trade_num"])
    min_notional = info.get("min_notional", 0.0)
    if mark_price is not None and mark_price > 0 and min_notional > 0:
        # Smallest qty step that meets min_notional
        min_qty_notional = math.ceil((min_notional / mark_price) / step) * step
        qty = max(qty, min_qty_notional)
    return f"{qty:.{info['volume_place']}f}"


# ── Market data ──────────────────────────────────────────────────── #

def get_candles(symbol: str, interval: str, limit: int = 100) -> pd.DataFrame:
    """
    Fetch klines via /v5/market/kline. Bybit returns most-recent-first;
    we sort ascending to match Bitget's get_candles return shape.

    Returns DataFrame with columns: ts, open, high, low, close, vol, quote_vol
    """
    try:
        data = bc.get_public(
            "/v5/market/kline",
            params={
                "category": CATEGORY,
                "symbol":   symbol,
                "interval": _map_interval(interval),
                "limit":    str(limit),
            },
        )
        rows = (data.get("result") or {}).get("list") or []
        if not rows:
            return pd.DataFrame()
        # Bybit row: [start, open, high, low, close, volume, turnover]
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "vol", "quote_vol"])
        df[["open", "high", "low", "close", "vol", "quote_vol"]] = (
            df[["open", "high", "low", "close", "vol", "quote_vol"]].astype(float)
        )
        df["ts"] = df["ts"].astype(int)
        return df.sort_values("ts").reset_index(drop=True)
    except RuntimeError as e:
        msg = str(e)
        if "10001" in msg or "param" in msg.lower():
            logger.warning(f"[Bybit][{symbol}] candles: invalid/delisted, skipping: {e}")
        else:
            logger.warning(f"[Bybit][{symbol}] get_candles error: {e}")
        return pd.DataFrame()


def fetch_candles_at(symbol: str, interval: str, limit: int, end_ms: int) -> pd.DataFrame:
    """Fetch up to `limit` candles ending at `end_ms` (epoch ms). Bybit uses `end`."""
    try:
        data = bc.get_public(
            "/v5/market/kline",
            params={
                "category": CATEGORY,
                "symbol":   symbol,
                "interval": _map_interval(interval),
                "limit":    str(limit),
                "end":      str(end_ms),
            },
        )
        rows = (data.get("result") or {}).get("list") or []
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "vol", "quote_vol"])
        df[["open", "high", "low", "close", "vol", "quote_vol"]] = (
            df[["open", "high", "low", "close", "vol", "quote_vol"]].astype(float)
        )
        df["ts"] = df["ts"].astype(int)
        return df.sort_values("ts").reset_index(drop=True)
    except Exception as e:
        logger.warning(f"[Bybit][{symbol}] fetch_candles_at error: {e}")
        return pd.DataFrame()


def get_mark_price(symbol: str) -> float:
    """Mark price from /v5/market/tickers."""
    data = bc.get_public("/v5/market/tickers", params={"category": CATEGORY, "symbol": symbol})
    rows = (data.get("result") or {}).get("list") or []
    if not rows:
        raise RuntimeError(f"[Bybit][{symbol}] no ticker rows in response")
    return float(rows[0].get("markPrice") or rows[0].get("lastPrice") or 0)


def get_funding_rate(symbol: str) -> float | None:
    """Current funding rate (fraction) from /v5/market/tickers. None on any error."""
    try:
        data = bc.get_public("/v5/market/tickers", params={"category": CATEGORY, "symbol": symbol})
        rows = (data.get("result") or {}).get("list") or []
        if not rows or rows[0].get("fundingRate") in (None, ""):
            return None
        return float(rows[0]["fundingRate"])
    except Exception as e:
        logger.warning(f"[Bybit][{symbol}] get_funding_rate failed: {e}")
        return None


def get_last_price(symbol: str) -> float:
    data = bc.get_public("/v5/market/tickers", params={"category": CATEGORY, "symbol": symbol})
    rows = (data.get("result") or {}).get("list") or []
    if not rows:
        raise RuntimeError(f"[Bybit][{symbol}] no ticker rows in response")
    return float(rows[0].get("lastPrice") or 0)


# ── Account ──────────────────────────────────────────────────────── #

def get_usdt_balance() -> float:
    """
    *Free* USDT balance under Unified Trading Account — matches Bitget's
    semantic of `available` (excludes locked position/order margin AND
    unrealised PnL). Used by the dashboard formula:

        total_equity = balance + sum(open_trade.margin) + sum(open_trade.upnl)

    so balance must NOT already include locked margin. Bybit's `walletBalance`
    already includes margin, so we subtract `totalPositionIM + totalOrderIM`.
    `availableToWithdraw` is sometimes the right value but is empty whenever
    cross-margin holds collateral against open positions — unreliable.
    """
    data = bc.get("/v5/account/wallet-balance", params={"accountType": "UNIFIED"})
    accounts = (data.get("result") or {}).get("list") or []

    def _f(v) -> float:
        try:
            return float(v) if v not in (None, "") else 0.0
        except (ValueError, TypeError):
            return 0.0

    for acct in accounts:
        for coin in acct.get("coin", []):
            if coin.get("coin") != SETTLE_COIN:
                continue
            wallet      = _f(coin.get("walletBalance"))
            position_im = _f(coin.get("totalPositionIM"))
            order_im    = _f(coin.get("totalOrderIM"))
            # availableToWithdraw can be < wallet - margins (e.g. when borrows or
            # bonus credits are excluded). Prefer it if non-empty.
            avail_raw = coin.get("availableToWithdraw")
            if avail_raw not in (None, ""):
                try:
                    return float(avail_raw)
                except (ValueError, TypeError):
                    pass
            return max(0.0, wallet - position_im - order_im)
    return 0.0


def get_total_equity() -> float:
    """Total equity under UTA — sum of all coin equity values, expressed in USD."""
    data = bc.get("/v5/account/wallet-balance", params={"accountType": "UNIFIED"})
    accounts = (data.get("result") or {}).get("list") or []
    for acct in accounts:
        eq = acct.get("totalEquity") or acct.get("totalWalletBalance") or 0
        try:
            return float(eq)
        except (ValueError, TypeError):
            return 0.0
    return 0.0


def get_all_open_positions() -> dict[str, dict]:
    """Open positions keyed by symbol. Shape matches bitget.get_all_open_positions."""
    data = bc.get(
        "/v5/position/list",
        params={"category": CATEGORY, "settleCoin": SETTLE_COIN},
    )
    rows = (data.get("result") or {}).get("list") or []
    result: dict[str, dict] = {}
    for p in rows:
        size = float(p.get("size") or 0)
        if size <= 0:
            continue
        side = (p.get("side") or "").upper()  # "Buy" | "Sell" → "BUY"/"SELL"
        result[p["symbol"]] = {
            "side":           "LONG" if side == "BUY" else "SHORT",
            "entry_price":    float(p.get("avgPrice") or 0),
            "qty":            size,
            "unrealised_pnl": float(p.get("unrealisedPnl") or 0),
            "mark_price":     float(p.get("markPrice")    or 0),
            "margin":         float(p.get("positionIM")   or 0),
            "leverage":       int(float(p.get("leverage") or 0)),
        }
    return result


def get_single_position_entry(symbol: str) -> float:
    """Avg entry price of the current position, or 0."""
    try:
        data = bc.get(
            "/v5/position/list",
            params={"category": CATEGORY, "symbol": symbol},
        )
        rows = (data.get("result") or {}).get("list") or []
        for p in rows:
            if float(p.get("size") or 0) > 0:
                return float(p.get("avgPrice") or 0)
    except Exception:
        pass
    return 0.0


# ── Leverage ─────────────────────────────────────────────────────── #

def set_leverage(symbol: str, leverage: int):
    """Set leverage for both long and short sides on this symbol."""
    if _dry_run_skip("set_leverage", symbol=symbol, leverage=leverage):
        return
    try:
        bc.post("/v5/position/set-leverage", {
            "category":     CATEGORY,
            "symbol":       symbol,
            "buyLeverage":  str(leverage),
            "sellLeverage": str(leverage),
        })
        logger.info(f"[Bybit][{symbol}] Leverage set to {leverage}x")
    except RuntimeError as e:
        # 110043 = leverage not modified (same value) — safe
        if "110043" in str(e):
            logger.debug(f"[Bybit][{symbol}] Leverage already {leverage}x")
        else:
            logger.warning(f"[Bybit][{symbol}] set_leverage warn: {e}")


# ── Order execution primitives ───────────────────────────────────── #

def place_market_order(symbol: str, side: str, qty_str: str,
                       sl_trigger: float | None = None,
                       tp_trigger: float | None = None) -> dict:
    """
    Place a market order on a USDT perp.

    side: 'buy' | 'sell' (bitget-style); converted to Bybit's 'Buy' | 'Sell'.
    Optional sl_trigger / tp_trigger attach SL/TP atomically on order entry
    (Bybit V5 supports stopLoss / takeProfit fields on /v5/order/create).
    """
    if _dry_run_skip("place_market_order", symbol=symbol, side=side, qty=qty_str,
                     sl=sl_trigger, tp=tp_trigger):
        return {"retCode": 0, "result": {"orderId": f"DRY-{symbol}-{int(_time.time())}"}}
    body = {
        "category":    CATEGORY,
        "symbol":      symbol,
        "side":        "Buy" if side.lower() == "buy" else "Sell",
        "orderType":   "Market",
        "qty":         qty_str,
        "timeInForce": "IOC",
        "reduceOnly":  False,
        "positionIdx": 0,        # 0 = one-way mode
    }
    # Bybit V5 requires tpslMode whenever slOrderType / tpOrderType is set.
    # "Full" → SL/TP applies to the entire position once filled.
    if sl_trigger is not None or tp_trigger is not None:
        body["tpslMode"] = "Full"
    if sl_trigger is not None:
        body["stopLoss"]        = round_price(sl_trigger, symbol)
        body["slTriggerBy"]     = "MarkPrice"
        body["slOrderType"]     = "Market"
    if tp_trigger is not None:
        body["takeProfit"]      = round_price(tp_trigger, symbol)
        body["tpTriggerBy"]     = "MarkPrice"
        body["tpOrderType"]     = "Market"
    info = sym_info(symbol)
    logger.info(
        f"[Bybit][{symbol}] /v5/order/create market {body['side']} qty={qty_str} "
        f"(min_qty={info.get('min_trade_num')}, qty_step={info.get('qty_step')}, "
        f"min_notional={info.get('min_notional')}) sl={body.get('stopLoss')} tp={body.get('takeProfit')}"
    )
    return bc.post("/v5/order/create", body)


def place_pos_tpsl_full(symbol: str, hold_side: str,
                        tp_trig: float, tp_exec: float,
                        sl_trig: float, sl_exec: float) -> bool:
    """
    Place combined TP+SL on a position via /v5/position/trading-stop.
    `tp_exec` and `sl_exec` are unused on Bybit (it places market exits on trigger).
    Retries 3x.
    """
    if _dry_run_skip("place_pos_tpsl_full", symbol=symbol, hold=hold_side,
                     tp=tp_trig, sl=sl_trig):
        return True
    body = {
        "category":      CATEGORY,
        "symbol":        symbol,
        "positionIdx":   0,
        "takeProfit":    round_price(tp_trig, symbol),
        "stopLoss":      round_price(sl_trig, symbol),
        "tpTriggerBy":   "MarkPrice",
        "slTriggerBy":   "MarkPrice",
        "tpslMode":      "Full",
        "tpOrderType":   "Market",
        "slOrderType":   "Market",
    }
    for attempt in range(3):
        try:
            bc.post("/v5/position/trading-stop", body)
            return True
        except Exception as e:
            logger.warning(f"[Bybit][{symbol}] TP/SL attempt {attempt+1}/3: {e}")
            if attempt < 2:
                _time.sleep(1.5)
    return False


def place_pos_sl_only(symbol: str, hold_side: str, sl_trig: float, sl_exec: float) -> None:
    """Place SL-only on full position."""
    if _dry_run_skip("place_pos_sl_only", symbol=symbol, hold=hold_side, sl=sl_trig):
        return
    bc.post("/v5/position/trading-stop", {
        "category":    CATEGORY,
        "symbol":      symbol,
        "positionIdx": 0,
        "stopLoss":    round_price(sl_trig, symbol),
        "slTriggerBy": "MarkPrice",
        "tpslMode":    "Full",
        "slOrderType": "Market",
    })


def place_profit_plan(symbol: str, hold_side: str, qty_str: str,
                      trigger: float, execute: str = "0") -> None:
    """
    Partial take-profit: market reduce-only order that fires at trigger price.
    Bybit V5: conditional order via /v5/order/create with triggerPrice + reduceOnly=True.

    hold_side: "long" → side="Sell" (reduce a long); "short" → side="Buy" (reduce a short).
    """
    if _dry_run_skip("place_profit_plan", symbol=symbol, hold=hold_side,
                     qty=qty_str, trigger=trigger):
        return
    reduce_side = "Sell" if hold_side == "long" else "Buy"
    trig_dir = 1 if hold_side == "long" else 2  # 1 = trigger when mark rises; 2 = when mark falls

    bc.post("/v5/order/create", {
        "category":      CATEGORY,
        "symbol":        symbol,
        "side":          reduce_side,
        "orderType":     "Market",
        "qty":           qty_str,
        "triggerPrice":  round_price(trigger, symbol),
        "triggerBy":     "MarkPrice",
        "triggerDirection": trig_dir,
        "reduceOnly":    True,
        "timeInForce":   "IOC",
        "positionIdx":   0,
    })


def place_moving_plan(symbol: str, hold_side: str, qty_str: str,
                      trigger: float, range_rate: str, sl_price: float = 0) -> None:
    """
    Trailing stop on a position via /v5/position/trading-stop.

    Bybit's trailing stop is position-level — `qty_str` is accepted for signature
    compatibility with bitget.place_moving_plan but is not used. After a partial
    TP fires the position shrinks naturally; the trailing stop continues to apply
    to the whole remaining position. This matches the Bitget endpoint's effective
    behaviour (close the rest at trail).

    range_rate: Bitget-flavoured percentage value, always interpreted as a percent.
      Strategies pass it as the integer-percent convention from config_s*.py:
        S2_TRAILING_RANGE_PCT = 10        → range_rate="10"      → 10%
        S5_TRAIL_RANGE_PCT    = 0.05      → range_rate="0.0500"  → 5%  (decimal form)
      We normalise both forms: values ≥ 1 are integer-percent (10 → 0.10),
      values < 1 are already decimal fractions (0.10 → 0.10). Bybit's V5
      `trailingStop` field expects an absolute price distance, so we multiply
      by the trigger to get the price-units distance.

    SL preservation: Bybit V5 /v5/position/trading-stop with tpslMode="Full"
    REPLACES the entire TP/SL/TS config — fields not included are cleared.
    Without preservation, this call would wipe the position's stopLoss
    (previously set by place_pos_sl_only or update_position_sl).

    Two preservation modes:
      1. `sl_price > 0` (preferred, race-free): the caller passes the exact SL
         to re-assert. It is written in the SAME atomic trading-stop body, so
         the SL and trailing stop land together with no read-back. Used by
         refresh_plan_exits after a scale-in, where the new average-entry SL is
         already known.
      2. `sl_price == 0` (best-effort fallback): we read the position's current
         stopLoss from /v5/position/list and re-send it. Used by entry-time
         callers where the SL was just attached to the market order. If the
         read fails or returns no SL, we proceed without it (no worse than the
         pre-fix behaviour).
    """
    if _dry_run_skip("place_moving_plan", symbol=symbol, hold=hold_side,
                     trigger=trigger, range_rate=range_rate):
        return
    try:
        raw = float(range_rate)
    except (ValueError, TypeError):
        raw = 10.0  # fall back to 10% default
    pct = raw / 100.0 if raw >= 1 else raw
    trailing_distance = trigger * pct

    body = {
        "category":      CATEGORY,
        "symbol":        symbol,
        "positionIdx":   0,
        "trailingStop":  round_price(trailing_distance, symbol),
        "activePrice":   round_price(trigger, symbol),
        "tpslMode":      "Full",
    }

    if sl_price and sl_price > 0:
        # Race-free path: caller supplied the authoritative SL. Write it in the
        # same body so the Full REPLACE keeps it. No position read needed.
        body["stopLoss"]    = round_price(sl_price, symbol)
        body["slTriggerBy"] = "MarkPrice"
        body["slOrderType"] = "Market"
    else:
        # Best-effort: re-read the current stopLoss and re-include it.
        # /v5/position/list returns "stopLoss" as a string price ("0" / "" /
        # missing when unset).
        try:
            _pos = bc.get("/v5/position/list",
                          params={"category": CATEGORY, "symbol": symbol})
            for _p in (_pos.get("result") or {}).get("list") or []:
                if str(_p.get("symbol")) != symbol:
                    continue
                _sl_str = (_p.get("stopLoss") or "").strip()
                try:
                    _sl_val = float(_sl_str)
                except ValueError:
                    _sl_val = 0.0
                if _sl_val > 0:
                    body["stopLoss"]    = round_price(_sl_val, symbol)
                    body["slTriggerBy"] = "MarkPrice"
                    body["slOrderType"] = "Market"
                break
        except Exception as e:
            # Best-effort — log and proceed. Worst case is the pre-fix behaviour
            # (SL wiped); not worse than what we had before.
            logger.warning(
                f"[Bybit][{symbol}] place_moving_plan: could not read current SL "
                f"for preservation: {e}"
            )

    bc.post("/v5/position/trading-stop", body)


def refresh_plan_exits(symbol: str, hold_side: str, new_trail_trigger: float = 0,
                       sl_price: float = 0) -> bool:
    """
    Resize partial-TP conditional + trailing stop after a scale-in.

    Mirrors bitget.refresh_plan_exits semantics adapted to Bybit V5:
      - Partial TP is a *conditional reduce-only market order* (placed by
        bybit.place_profit_plan). We cancel and re-place with new total/2 qty.
      - Trailing stop is a *position-level* setting (placed by
        bybit.place_moving_plan). Calling /v5/position/trading-stop with new
        params replaces the previous trailing stop atomically — no cancel needed.

    Args:
        new_trail_trigger: if > 0, used as the new partial-TP trigger AND
            trailing-stop activePrice. If 0, the previous order's triggerPrice
            is preserved.

    SL preservation: place_moving_plan posts to /v5/position/trading-stop with
    tpslMode=Full, a REPLACE that clears any field not present. When the caller
    passes `sl_price` (> 0), we forward it to place_moving_plan so the SL is
    re-asserted in the SAME atomic body as the trailing stop — race-free, no
    separate update_position_sl call needed. When `sl_price == 0`,
    place_moving_plan falls back to its best-effort /v5/position/list read-back.

    Returns True on success. Logs and returns False if no existing partial-TP
    conditional is found (cannot infer prior trigger / range), or if all 3
    re-placement attempts fail.
    """
    if _dry_run_skip("refresh_plan_exits", symbol=symbol, hold=hold_side,
                     trigger=new_trail_trigger):
        return True
    side_to_reduce = "Sell" if hold_side == "long" else "Buy"

    # Bybit V5: untriggered conditional orders require orderFilter=StopOrder.
    # The default filter ("Order") returns only regular (non-conditional) orders,
    # so our partial-TP conditional would otherwise be invisible.
    orders: list = []
    for order_filter in ("StopOrder", "Order"):
        try:
            resp = bc.get(
                "/v5/order/realtime",
                params={"category": CATEGORY, "symbol": symbol,
                        "orderFilter": order_filter, "openOnly": "0"},
            )
            orders.extend((resp.get("result") or {}).get("list") or [])
        except Exception as e:
            logger.warning(
                f"[Bybit][{symbol}] refresh_plan_exits: fetch orderFilter={order_filter} failed: {e}"
            )

    # Find conditional reduce-only market orders matching the close side of this position.
    targets = []
    seen_order_ids: set[str] = set()
    for o in orders:
        oid = str(o.get("orderId") or "")
        if oid in seen_order_ids:
            continue   # dedupe in case both orderFilter queries returned the same order
        if not o.get("reduceOnly"):
            continue
        if o.get("side") != side_to_reduce:
            continue
        # Conditional orders have a non-zero triggerPrice
        try:
            if float(o.get("triggerPrice") or 0) <= 0:
                continue
        except (ValueError, TypeError):
            continue
        seen_order_ids.add(oid)
        targets.append(o)

    if not targets:
        logger.warning(
            f"[Bybit][{symbol}] refresh_plan_exits: no conditional reduce-only "
            f"orders found — exits unchanged"
        )
        return False

    # Preserve trigger from the existing order if caller didn't supply one.
    if new_trail_trigger > 0:
        trigger = new_trail_trigger
    else:
        try:
            trigger = float(targets[0].get("triggerPrice") or 0)
        except (ValueError, TypeError):
            trigger = 0.0
    if trigger <= 0:
        logger.error(f"[Bybit][{symbol}] refresh_plan_exits: no valid trigger price available")
        return False

    # Cancel existing conditional(s)
    for o in targets:
        try:
            bc.post("/v5/order/cancel", {
                "category": CATEGORY,
                "symbol":   symbol,
                "orderId":  o["orderId"],
            })
            _time.sleep(0.3)
        except Exception as e:
            logger.warning(f"[Bybit][{symbol}] cancel conditional {o.get('orderId')}: {e}")

    _time.sleep(0.5)

    # Read new total qty after scale-in
    positions = get_all_open_positions()
    total_qty_float = float((positions.get(symbol) or {}).get("qty", 0))
    if total_qty_float <= 0:
        logger.error(f"[Bybit][{symbol}] refresh_plan_exits: position not found after scale-in")
        return False

    half_qty = round_qty(total_qty_float / 2, symbol)
    rest_qty_str = round_qty(total_qty_float - float(half_qty), symbol)

    # Re-place: partial TP conditional + position-level trailing stop.
    # Trailing range default 10% (Bitget convention); preserved across refreshes.
    range_rate = "0.10"

    for attempt in range(3):
        try:
            place_profit_plan(symbol, hold_side, half_qty, trigger)
            _time.sleep(0.5)
            # place_moving_plan does a Full-mode trading-stop REPLACE. We pass
            # sl_price so the SL is re-asserted atomically in the same body and
            # is NOT wiped (when sl_price > 0). When sl_price == 0 it falls back
            # to the best-effort read-back preservation.
            place_moving_plan(symbol, hold_side, rest_qty_str, trigger, range_rate,
                              sl_price=sl_price)
            _sl_note = (f"SL re-asserted@{sl_price}" if sl_price and sl_price > 0
                        else "SL via best-effort read-back")
            logger.info(
                f"[Bybit][{symbol}] ✅ Plan exits refreshed after scale-in: "
                f"partial_tp={half_qty}@{trigger}, trailing_stop active@{trigger} "
                f"({_sl_note})"
            )
            return True
        except Exception as e:
            logger.warning(f"[Bybit][{symbol}] refresh_plan_exits attempt {attempt+1}/3: {e}")
            if attempt < 2:
                _time.sleep(1.5)
    return False


def update_position_sl(symbol: str, new_sl: float, hold_side: str = "long") -> bool:
    """Replace the position's SL via /v5/position/trading-stop. Returns True on success."""
    if _dry_run_skip("update_position_sl", symbol=symbol, hold=hold_side, new_sl=new_sl):
        return True
    sl_str = round_price(new_sl, symbol)
    for attempt in range(3):
        try:
            bc.post("/v5/position/trading-stop", {
                "category":    CATEGORY,
                "symbol":      symbol,
                "positionIdx": 0,
                "stopLoss":    sl_str,
                "slTriggerBy": "MarkPrice",
                "tpslMode":    "Full",
                "slOrderType": "Market",
            })
            return True
        except Exception as e:
            logger.warning(f"[Bybit][{symbol}] update_position_sl attempt {attempt+1}/3: {e}")
            if attempt < 2:
                _time.sleep(1.0)
    return False


# ── Plan (conditional / trigger) orders ──────────────────────────── #

def place_plan_order(side: str, symbol: str, trigger_price: float,
                     sl_price: float, tp_price: float, qty_str: str) -> str:
    """
    Place a conditional market entry that activates when mark reaches trigger_price.
    Bybit V5: /v5/order/create with triggerPrice + triggerDirection.

    For LONG (buy): triggerDirection=1 (fires when mark RISES above trigger)
    For SHORT (sell): triggerDirection=2 (fires when mark FALLS below trigger)
    """
    if _dry_run_skip("place_plan_order", symbol=symbol, side=side,
                     trigger=trigger_price, sl=sl_price, tp=tp_price, qty=qty_str):
        return f"DRY-{symbol}-{int(_time.time())}"
    bybit_side = "Buy" if side.lower() == "buy" else "Sell"
    trig_dir = 1 if bybit_side == "Buy" else 2

    body = {
        "category":         CATEGORY,
        "symbol":           symbol,
        "side":             bybit_side,
        "orderType":        "Market",
        "qty":              qty_str,
        "triggerPrice":     round_price(trigger_price, symbol),
        "triggerBy":        "MarkPrice",
        "triggerDirection": trig_dir,
        "timeInForce":      "IOC",
        "positionIdx":      0,
        # Bybit V5: tpslMode required when slOrderType / tpOrderType is set.
        # "Full" → SL/TP cover the entire position once the trigger fires.
        "tpslMode":         "Full",
        "stopLoss":         round_price(sl_price, symbol),
        "slTriggerBy":      "MarkPrice",
        "slOrderType":      "Market",
    }
    if tp_price and tp_price > 0:
        body["takeProfit"]   = round_price(tp_price, symbol)
        body["tpTriggerBy"]  = "MarkPrice"
        body["tpOrderType"]  = "Market"
    info = sym_info(symbol)
    logger.info(
        f"[Bybit][{symbol}] /v5/order/create plan {body['side']} qty={qty_str} "
        f"trigger={body['triggerPrice']} (min_qty={info.get('min_trade_num')}, "
        f"qty_step={info.get('qty_step')}, min_notional={info.get('min_notional')}) "
        f"sl={body.get('stopLoss')} tp={body.get('takeProfit')}"
    )
    resp = bc.post("/v5/order/create", body)
    result = resp.get("result") or {}
    order_id = result.get("orderId")
    if not order_id:
        raise RuntimeError(f"[Bybit] place_plan_order: missing orderId in response: {resp}")
    return str(order_id)


def cancel_plan_order(symbol: str, order_id: str) -> None:
    """Cancel a single conditional/plan order by id."""
    if _dry_run_skip("cancel_plan_order", symbol=symbol, orderId=order_id):
        return
    resp = bc.post("/v5/order/cancel", {
        "category": CATEGORY,
        "symbol":   symbol,
        "orderId":  order_id,
    })
    if resp.get("retCode", 0) != 0:
        raise RuntimeError(f"[Bybit] cancel_plan_order failed: {resp}")


def cancel_all_orders(symbol: str) -> None:
    """Cancel all open regular + conditional reduce-only orders for a symbol.

    Bybit V5 `/v5/order/cancel-all` filters by `orderFilter`. Without one, it
    only cancels regular orders — conditional/stop orders (where
    place_profit_plan parks the partial-TP) survive. We issue both calls so
    nothing is left dangling after a position closes.

    Used by the bot's close-detection path to prevent orphaned reduce-only
    conditionals from cluttering the Bybit UI (they cannot fill once the
    position is gone, but Bybit keeps them visible until cancelled).
    """
    if _dry_run_skip("cancel_all_orders", symbol=symbol):
        return
    for order_filter in ("Order", "StopOrder"):
        try:
            bc.post("/v5/order/cancel-all", {
                "category":    CATEGORY,
                "symbol":      symbol,
                "orderFilter": order_filter,
            })
        except Exception as e:
            # 110001 = "order does not exist" — expected when there's nothing
            # of this filter to cancel. Other errors are logged but don't stop
            # the second pass.
            logger.warning(f"[Bybit][{symbol}] cancel-all ({order_filter}) warn: {e}")


def get_order_fill(symbol: str, order_id: str) -> dict:
    """
    Poll the status of a conditional (plan) order.
    Returns {"status": "live"|"filled"|"cancelled", "fill_price": float}
    """
    # First check open orders
    try:
        resp = bc.get(
            "/v5/order/realtime",
            params={"category": CATEGORY, "symbol": symbol, "orderId": order_id},
        )
        rows = (resp.get("result") or {}).get("list") or []
        for o in rows:
            if str(o.get("orderId")) == str(order_id):
                status = (o.get("orderStatus") or "").lower()
                if status in ("untriggered", "new", "created", "active"):
                    return {"status": "live", "fill_price": 0.0}
                if status in ("filled", "partiallyfilled", "triggered"):
                    fill = float(o.get("avgPrice") or 0) or get_single_position_entry(symbol)
                    return {"status": "filled", "fill_price": fill}
                if status in ("cancelled", "rejected", "deactivated"):
                    return {"status": "cancelled", "fill_price": 0.0}
    except Exception as e:
        logger.debug(f"[Bybit][{symbol}] realtime lookup failed: {e}")

    # Fall back to order history
    try:
        hist = bc.get(
            "/v5/order/history",
            params={"category": CATEGORY, "symbol": symbol, "orderId": order_id},
        )
        rows = (hist.get("result") or {}).get("list") or []
        for o in rows:
            if str(o.get("orderId")) == str(order_id):
                status = (o.get("orderStatus") or "").lower()
                if status in ("filled", "partiallyfilled", "triggered"):
                    fill = float(o.get("avgPrice") or 0) or get_single_position_entry(symbol)
                    return {"status": "filled", "fill_price": fill}
                return {"status": "cancelled", "fill_price": 0.0}
    except Exception as e:
        logger.debug(f"[Bybit][{symbol}] history lookup failed: {e}")

    return {"status": "cancelled", "fill_price": 0.0}


# ── History / closed positions ───────────────────────────────────── #

def get_history_position(symbol: str,
                         open_time_iso: str | None = None,
                         entry_price:   float | None = None,
                         retries: int = 3,
                         retry_delay: float = 1.5) -> dict | None:
    """
    Total realized P&L for a closed position via /v5/position/closed-pnl.

    Bybit returns ONE row per close event (partial TP, trailing stop hit,
    final close, manual reduce, etc.) — so the position's lifetime P&L is the
    SUM of all rows in the trade's time window. The earlier implementation
    took only records[0] (most recent) which under-reported any trade that
    had a partial TP fire — the close row's closedPnl is just the residual
    after the partial.

    The startTime filter (set to the trade's open_time_iso) constrains the
    query to rows that belong to this trade. If multiple historical trades
    exist on the same symbol earlier, they're excluded server-side.

    Returns {pnl, exit_price, close_time} or None.
    """
    for attempt in range(retries):
        try:
            params: dict = {"category": CATEGORY, "symbol": symbol, "limit": "50"}
            if open_time_iso:
                try:
                    dt = datetime.fromisoformat(open_time_iso)
                    params["startTime"] = str(int(dt.timestamp() * 1000))
                except Exception:
                    pass
            data    = bc.get("/v5/position/closed-pnl", params=params)
            records = (data.get("result") or {}).get("list") or []
            if not records:
                return None

            # When `entry_price` is supplied, narrow to rows whose entry matches
            # this trade. Bybit reports avgEntryPrice per close row — for a
            # multi-leg close the same entry value appears on every leg, so we
            # keep all matching rows, not just the closest one.
            if entry_price is not None and entry_price > 0:
                def _entry_of(r: dict) -> float:
                    try:
                        return float(r.get("avgEntryPrice") or 0)
                    except (ValueError, TypeError):
                        return 0.0
                tol = max(entry_price * 0.005, 1e-9)   # 0.5% tolerance
                matched = [r for r in records if abs(_entry_of(r) - entry_price) <= tol]
                if matched:
                    records = matched

            total_pnl = sum(float(r.get("closedPnl") or 0) for r in records)
            if total_pnl == 0:
                if attempt < retries - 1:
                    logger.debug(f"[Bybit][{symbol}] get_history_position: pnl=0, retrying ({attempt+1}/{retries-1})")
                    _time.sleep(retry_delay)
                    continue
                logger.warning(f"[Bybit][{symbol}] get_history_position: still 0 after {retries} attempts ({len(records)} rows)")
                return None

            # Use the most recent matched row for exit_price + close_time.
            r0 = records[0]
            close_avg = r0.get("avgExitPrice")
            close_ts  = r0.get("updatedTime") or r0.get("createdTime")
            close_dt  = None
            if close_ts:
                try:
                    close_dt = datetime.fromtimestamp(int(close_ts) / 1000, tz=timezone.utc).isoformat()
                except Exception:
                    pass
            return {
                "pnl":        total_pnl,
                "exit_price": float(close_avg) if close_avg else None,
                "close_time": close_dt,
            }
        except Exception as e:
            logger.warning(f"[Bybit][{symbol}] get_history_position error: {e}")
            return None
    return None


def get_realized_pnl(symbol: str, retries: int = 3, retry_delay: float = 1.5) -> float | None:
    """Most recent closed position's realized PnL. Retries on API lag."""
    for attempt in range(retries):
        try:
            data = bc.get(
                "/v5/position/closed-pnl",
                params={"category": CATEGORY, "symbol": symbol, "limit": "1"},
            )
            records = (data.get("result") or {}).get("list") or []
            if records:
                pnl = float(records[0].get("closedPnl") or 0)
                if pnl != 0:
                    return pnl
                if attempt < retries - 1:
                    logger.debug(f"[Bybit][{symbol}] get_realized_pnl: 0, retrying ({attempt+1}/{retries-1})")
                    _time.sleep(retry_delay)
        except Exception as e:
            logger.warning(f"[Bybit][{symbol}] get_realized_pnl error: {e}")
            return None
    logger.warning(f"[Bybit][{symbol}] get_realized_pnl: still 0 after {retries} attempts")
    return None
