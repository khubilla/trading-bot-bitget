"""
trader.py — Bitget USDT Futures API Wrapper

open_long() and open_short() now accept explicit leverage, trade_size_pct,
and take_profit_pct so Strategy 1 and Strategy 2 can use different risk params.
"""

import math
import logging
import pandas as pd
import bitget_client as bc
from config import PRODUCT_TYPE, MARGIN_COIN
from config_s1 import (
    LEVERAGE, TRADE_SIZE_PCT, TAKE_PROFIT_PCT,
    HTF_INTERVAL, LTF_INTERVAL, STOP_LOSS_PCT,
)

logger = logging.getLogger(__name__)
_sym_cache: dict[str, dict] = {}


# ── Symbol Info ───────────────────────────────────────────────────── #

def _load_symbol_cache():
    global _sym_cache
    if _sym_cache:
        return
    data = bc.get_public("/api/v2/mix/market/contracts", params={"productType": PRODUCT_TYPE})
    for s in data.get("data", []):
        _sym_cache[s["symbol"]] = {
            "price_place":   int(s.get("pricePlace",   2)),
            "volume_place":  int(s.get("volumePlace",  3)),
            "size_mult":     float(s.get("sizeMultiplier", 0.001)),
            "min_trade_num": float(s.get("minTradeNum", 0.001)),
        }
    logger.info(f"Symbol cache loaded: {len(_sym_cache)} contracts")


def _sym_info(symbol: str) -> dict:
    _load_symbol_cache()
    return _sym_cache.get(symbol, {"price_place": 2, "volume_place": 3,
                                    "size_mult": 0.001, "min_trade_num": 0.001})


def _round_price(price: float, symbol: str) -> str:
    return str(round(price, _sym_info(symbol)["price_place"]))


def _round_qty(qty: float, symbol: str) -> str:
    info = _sym_info(symbol)
    mult = info["size_mult"]
    qty  = math.floor(qty / mult) * mult
    qty  = max(qty, info["min_trade_num"])
    return str(round(qty, info["volume_place"]))


# ── Market Data ───────────────────────────────────────────────────── #

def get_candles(symbol: str, interval: str, limit: int = 100) -> pd.DataFrame:
    if interval in ("1D", "1d"):
        return get_daily_candles_utc(symbol, limit)
    data = bc.get_public(
        "/api/v2/mix/market/candles",
        params={"symbol": symbol, "productType": PRODUCT_TYPE,
                "granularity": interval, "limit": str(limit)}
    )
    rows = data.get("data", [])
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["ts","open","high","low","close","vol","quote_vol"])
    df[["open","high","low","close","vol"]] = df[["open","high","low","close","vol"]].astype(float)
    df["ts"] = df["ts"].astype(int)
    return df.sort_values("ts").reset_index(drop=True)


_ccxt_ex = None

def get_daily_candles_utc(symbol: str, limit: int = 100) -> pd.DataFrame:
    """
    Fetch 1D candles via ccxt — matches TradingView exactly (UTC midnight boundaries).
    Uses cached exchange instance to avoid reloading markets on every call.
    """
    import ccxt
    global _ccxt_ex
    if _ccxt_ex is None:
        _ccxt_ex = ccxt.bitget({"options": {"defaultType": "swap"}})
        _ccxt_ex.load_markets()

    base = symbol.replace("USDT", "")
    ccxt_symbol = f"{base}/USDT:USDT"
    ohlcv = _ccxt_ex.fetch_ohlcv(ccxt_symbol, "1d", limit=limit)
    if not ohlcv:
        return pd.DataFrame()
    rows = [{"ts": c[0], "open": float(c[1]), "high": float(c[2]),
             "low": float(c[3]), "close": float(c[4]), "vol": float(c[5])}
            for c in ohlcv]
    df = pd.DataFrame(rows)
    df = df.sort_values("ts").reset_index(drop=True)
    # Patch today's candle close with live mark price
    try:
        mark = get_mark_price(symbol)
        df.at[df.index[-1], "close"] = mark
        df.at[df.index[-1], "high"]  = max(float(df.iloc[-1]["high"]), mark)
        df.at[df.index[-1], "low"]   = min(float(df.iloc[-1]["low"]),  mark)
    except Exception:
        pass
    return df


def get_mark_price(symbol: str) -> float:
    data = bc.get_public("/api/v2/mix/market/symbol-price",
                         params={"symbol": symbol, "productType": PRODUCT_TYPE})
    return float(data["data"][0]["markPrice"])


# ── Account ───────────────────────────────────────────────────────── #

def get_usdt_balance() -> float:
    data = bc.get("/api/v2/mix/account/accounts", params={"productType": PRODUCT_TYPE})
    for a in data.get("data", []):
        if a.get("marginCoin") == MARGIN_COIN:
            return float(a.get("available", 0))
    return 0.0


def _get_total_equity() -> float:
    """Total account equity (available + locked margin + unrealized PnL) in USDT."""
    data = bc.get("/api/v2/mix/account/accounts", params={"productType": PRODUCT_TYPE})
    for a in data.get("data", []):
        if a.get("marginCoin") == MARGIN_COIN:
            return float(a.get("usdtEquity", 0) or a.get("equity", 0))
    return 0.0


def get_all_open_positions() -> dict[str, dict]:
    data = bc.get("/api/v2/mix/position/all-position",
                  params={"productType": PRODUCT_TYPE, "marginCoin": MARGIN_COIN})
    result = {}
    for p in data.get("data", []):
        total = float(p.get("total", 0))
        if total <= 0:
            continue
        result[p["symbol"]] = {
            "side":           p.get("holdSide","long").upper(),
            "entry_price":    float(p.get("openPriceAvg", 0)),
            "qty":            total,
            "unrealised_pnl": float(p.get("unrealizedPL", 0)),
            "mark_price":     float(p.get("markPrice", 0)),
            "margin":         float(p.get("marginSize", 0)),
            "leverage":       int(float(p.get("leverage", 0) or 0)),
        }
    return result


# ── Leverage ──────────────────────────────────────────────────────── #

def set_leverage(symbol: str, leverage: int):
    """Sets leverage for both sides. Always uses the passed leverage value."""
    for hold_side in ("long", "short"):
        try:
            bc.post("/api/v2/mix/account/set-leverage", {
                "symbol":      symbol,
                "productType": PRODUCT_TYPE,
                "marginCoin":  MARGIN_COIN,
                "leverage":    str(leverage),
                "holdSide":    hold_side,
            })
            logger.info(f"[{symbol}] Leverage set to {leverage}x ({hold_side})")
        except Exception as e:
            logger.warning(f"[{symbol}] set_leverage({hold_side}) warn: {e}")


# ── TP/SL Placement ───────────────────────────────────────────────── #

def _place_tpsl(symbol: str, hold_side: str,
                tp_trig: float, tp_exec: float,
                sl_trig: float, sl_exec: float) -> bool:
    import time as _t
    for attempt in range(3):
        try:
            bc.post("/api/v2/mix/order/place-pos-tpsl", {
                "symbol":                  symbol,
                "productType":             PRODUCT_TYPE,
                "marginCoin":              MARGIN_COIN,
                "holdSide":                hold_side,
                "stopSurplusTriggerPrice": str(tp_trig),
                "stopSurplusTriggerType":  "mark_price",
                "stopSurplusExecutePrice": str(tp_exec),
                "stopLossTriggerPrice":    str(sl_trig),
                "stopLossTriggerType":     "mark_price",
                "stopLossExecutePrice":    str(sl_exec),
            })
            return True
        except Exception as e:
            logger.warning(f"[{symbol}] TP/SL attempt {attempt+1}/3: {e}")
            if attempt < 2:
                _t.sleep(1.5)
    return False


def refresh_plan_exits(symbol: str, hold_side: str, new_trail_trigger: float = 0) -> bool:
    """
    Called after a scale-in to resize profit_plan and moving_plan orders to the
    current total position qty.  The SL (place-pos-tpsl) is position-level on
    Bitget and auto-scales — this function only touches plan orders.

    new_trail_trigger: if > 0, re-placed orders use this trigger price instead of
    preserving the existing profit_plan trigger (used after scale-in changes avg entry).

    Steps:
    1. Fetch pending profit_plan + moving_plan for this hold_side.
    2. Cancel them.
    3. Read current total position qty from the exchange.
    4. Re-place both orders — at new_trail_trigger if provided, else original trigger.
    """
    import time as _t

    data    = bc.get("/api/v2/mix/order/plan-orders", {"symbol": symbol, "productType": PRODUCT_TYPE})
    orders  = (data.get("data") or {}).get("entrustedList", [])
    targets = [o for o in orders if o.get("holdSide") == hold_side
               and o.get("planType") in ("profit_plan", "moving_plan")]

    profit = next((o for o in targets if o["planType"] == "profit_plan"), None)
    moving = next((o for o in targets if o["planType"] == "moving_plan"), None)

    if not profit or not moving:
        logger.warning(f"[{symbol}] refresh_plan_exits: profit_plan or moving_plan not found — exits unchanged")
        return False

    trail_trigger = new_trail_trigger if new_trail_trigger > 0 else float(profit["triggerPrice"])
    trail_range   = str(moving.get("rangeRate", "10"))

    for o in [profit, moving]:
        try:
            bc.post("/api/v2/mix/order/cancel-plan-order",
                    {"symbol": symbol, "productType": PRODUCT_TYPE, "orderId": o["orderId"]})
            _t.sleep(0.3)
        except Exception as e:
            logger.warning(f"[{symbol}] cancel plan order {o['orderId']}: {e}")

    _t.sleep(0.5)

    positions        = get_all_open_positions()
    total_qty_float  = float((positions.get(symbol) or {}).get("qty", 0))
    if total_qty_float <= 0:
        logger.error(f"[{symbol}] refresh_plan_exits: position not found after scale-in")
        return False

    half_qty = _round_qty(total_qty_float / 2, symbol)
    rest_qty = _round_qty(total_qty_float - float(half_qty), symbol)

    for attempt in range(3):
        try:
            bc.post("/api/v2/mix/order/place-tpsl-order", {
                "symbol": symbol, "productType": PRODUCT_TYPE, "marginCoin": MARGIN_COIN,
                "planType": "profit_plan", "triggerPrice": str(trail_trigger),
                "triggerType": "mark_price", "executePrice": "0",
                "holdSide": hold_side, "size": half_qty,
            })
            _t.sleep(0.5)
            bc.post("/api/v2/mix/order/place-tpsl-order", {
                "symbol": symbol, "productType": PRODUCT_TYPE, "marginCoin": MARGIN_COIN,
                "planType": "moving_plan", "triggerPrice": str(trail_trigger),
                "triggerType": "mark_price", "holdSide": hold_side,
                "size": rest_qty, "rangeRate": trail_range,
            })
            logger.info(
                f"[{symbol}] ✅ Plan exits refreshed after scale-in: "
                f"profit_plan={half_qty}@{trail_trigger}, moving_plan={rest_qty}"
            )
            return True
        except Exception as e:
            logger.warning(f"[{symbol}] refresh_plan_exits attempt {attempt+1}/3: {e}")
            if attempt < 2:
                _t.sleep(1.5)
    return False


def _place_s2_exits(symbol: str, hold_side: str, qty_str: str,
                    sl_trig: float, sl_exec: float,
                    trail_trigger: float, trail_range: float) -> bool:
    """Delegate to strategies.s2._place_partial_trail_exits (qty rounded via trader._round_qty)."""
    from strategies.s2 import _place_partial_trail_exits
    return _place_partial_trail_exits(symbol, hold_side, qty_str,
                                       sl_trig, sl_exec, trail_trigger, trail_range)


def _place_s1_exits(symbol: str, hold_side: str, qty_str: str,
                    sl_trig: float, sl_exec: float,
                    trail_trigger: float, trail_range: float) -> bool:
    """Delegate to strategies.s1._place_exits."""
    from strategies.s1 import _place_exits
    return _place_exits(symbol, hold_side, qty_str,
                        sl_trig, sl_exec, trail_trigger, trail_range)


def _place_s5_exits(symbol: str, hold_side: str, qty_str: str,
                    sl_trig: float, sl_exec: float,
                    partial_trig: float, tp_target: float,
                    trail_range_pct: float) -> bool:
    """Delegate to strategies.s5._place_exits."""
    from strategies.s5 import _place_exits
    return _place_exits(symbol, hold_side, qty_str,
                        sl_trig, sl_exec, partial_trig, tp_target, trail_range_pct)


def open_long(
    symbol: str,
    box_low: float         = 0,
    sl_floor: float        = 0,
    leverage: int          = LEVERAGE,
    trade_size_pct: float  = TRADE_SIZE_PCT,
    take_profit_pct: float = TAKE_PROFIT_PCT,
    stop_loss_pct: float   = STOP_LOSS_PCT,
    use_s1_exits: bool     = False,
    use_s2_exits: bool     = False,
    use_s3_exits: bool     = False,
    use_s5_exits: bool     = False,
    strategy: str | None   = None,
    tp_price_abs: float    = 0,
) -> dict:
    """
    Opens a LONG position.
    Exit levels (SL, TP, trail trigger) are computed from the actual fill price
    fetched after the market order fills (falls back to pre-order mark if the
    position is not yet visible).
    S2: fixed SL at fill * (1 - stop_loss_pct); partial TP + trailing stop.
    S3: structural SL at max(sl_floor, fill * (1 - stop_loss_pct)); same TP/trail.
    """
    # Map `strategy=` string to the legacy use_sN_exits kwargs (string dispatch is preferred
    # externally; bool kwargs preserved for tests that still call with them).
    if strategy == "S1": use_s1_exits = True
    elif strategy == "S2": use_s2_exits = True
    elif strategy == "S3": use_s3_exits = True
    elif strategy == "S5": use_s5_exits = True
    import time as _t
    balance  = get_usdt_balance()
    equity   = _get_total_equity() or balance
    mark     = get_mark_price(symbol)
    notional = equity * trade_size_pct * leverage
    qty      = _round_qty(notional / mark, symbol)

    set_leverage(symbol, leverage)

    bc.post("/api/v2/mix/order/place-order", {
        "symbol": symbol, "productType": PRODUCT_TYPE,
        "marginMode": "isolated", "marginCoin": MARGIN_COIN,
        "size": qty, "side": "buy", "tradeSide": "open",
        "orderType": "market", "force": "ioc",
    })

    _t.sleep(2.0)

    # Re-fetch actual fill price from exchange; fall back to pre-order mark if
    # the position is not yet visible (e.g. very fast execution or API lag).
    _pos_after = get_all_open_positions()
    fill = _pos_after.get(symbol, {}).get("entry_price", 0) or mark

    if use_s5_exits:
        from strategies.s5 import compute_and_place_long_exits as _s5_long_exits
        ok, sl_trig, tp_trig = _s5_long_exits(symbol, qty, fill, sl_floor, tp_price_abs)
    elif use_s1_exits:
        from strategies.s1 import compute_and_place_long_exits as _s1_long_exits
        ok, sl_trig, tp_trig = _s1_long_exits(symbol, qty, fill, sl_floor)
    elif use_s2_exits:
        from strategies.s2 import compute_and_place_long_exits as _s2_long_exits
        ok, sl_trig, tp_trig = _s2_long_exits(symbol, qty, fill, stop_loss_pct)
    elif use_s3_exits:
        from strategies.s3 import compute_and_place_long_exits as _s3_long_exits
        ok, sl_trig, tp_trig = _s3_long_exits(symbol, qty, fill, sl_floor, box_low, stop_loss_pct)
    else:
        tp_trig = float(_round_price(fill * (1 + take_profit_pct), symbol))
        tp_exec = float(_round_price(tp_trig * 1.005, symbol))
        if sl_floor > 0:
            sl_trig = float(_round_price(sl_floor, symbol))
            sl_exec = float(_round_price(sl_floor * 0.995, symbol))
        else:
            sl_trig = float(_round_price(fill * (1 - stop_loss_pct), symbol))
            sl_exec = float(_round_price(sl_trig * 0.995, symbol))
        ok = _place_tpsl(symbol, "long", tp_trig, tp_exec, sl_trig, sl_exec)

    if not ok:
        logger.error(f"[{symbol}] ⚠️  TP/SL failed! Set manually: SL={sl_trig}")

    result = {
        "symbol": symbol, "side": "LONG", "qty": qty,
        "entry": fill, "sl": sl_trig, "tp": tp_trig,
        "box_low": box_low, "leverage": leverage,
        "margin": round(equity * trade_size_pct, 4), "tpsl_set": ok,
    }
    logger.info(
        f"[{symbol}] 🟢 LONG {leverage}x | qty={qty} entry≈{fill:.5f} "
        f"SL={sl_trig} | {'✅ S1 exits' if use_s1_exits else '✅ S2 exits' if use_s2_exits else '✅ S3 exits' if use_s3_exits else 'TP='+str(tp_trig)} | {'✅' if ok else '❌ SET MANUALLY'}"
    )
    return result


def open_short(
    symbol: str,
    box_high: float        = 0,
    sl_floor: float        = 0,
    leverage: int          = LEVERAGE,
    trade_size_pct: float  = TRADE_SIZE_PCT,
    take_profit_pct: float = TAKE_PROFIT_PCT,
    use_s1_exits: bool     = False,
    use_s4_exits: bool     = False,
    use_s5_exits: bool     = False,
    use_s6_exits: bool     = False,
    strategy: str | None   = None,
    tp_price_abs: float    = 0,
) -> dict:
    """
    Opens a SHORT position.
    SL = sl_floor if provided, else box_high * 1.001 (structural level, not fill-relative).
    TP/trail trigger computed from actual fill price fetched after the market order fills
    (falls back to pre-order mark if position not yet visible).
    use_s4_exits: trailing stop — 50% close at -10%, trailing stop on remainder.
    """
    if strategy == "S1": use_s1_exits = True
    elif strategy == "S4": use_s4_exits = True
    elif strategy == "S5": use_s5_exits = True
    elif strategy == "S6": use_s6_exits = True
    import time as _t
    balance  = get_usdt_balance()
    equity   = _get_total_equity() or balance
    mark     = get_mark_price(symbol)
    notional = equity * trade_size_pct * leverage
    qty      = _round_qty(notional / mark, symbol)

    # SL is structural (box_high or sl_floor), not entry-relative — compute before order
    if sl_floor > 0:
        sl_trig = float(_round_price(sl_floor, symbol))
    else:
        sl_trig = float(_round_price(box_high * 1.001, symbol))
    sl_exec = float(_round_price(sl_trig * 1.005, symbol))

    set_leverage(symbol, leverage)

    bc.post("/api/v2/mix/order/place-order", {
        "symbol": symbol, "productType": PRODUCT_TYPE,
        "marginMode": "isolated", "marginCoin": MARGIN_COIN,
        "size": qty, "side": "sell", "tradeSide": "open",
        "orderType": "market", "force": "ioc",
    })

    _t.sleep(2.0)

    # Re-fetch actual fill price; fall back to pre-order mark if position not yet visible.
    _pos_after = get_all_open_positions()
    fill = _pos_after.get(symbol, {}).get("entry_price", 0) or mark

    if use_s5_exits:
        from strategies.s5 import compute_and_place_short_exits as _s5_short_exits
        ok, sl_trig, tp_trig = _s5_short_exits(symbol, qty, fill, sl_trig, sl_exec, tp_price_abs)
    elif use_s1_exits:
        from strategies.s1 import compute_and_place_short_exits as _s1_short_exits
        ok, sl_trig, tp_trig = _s1_short_exits(symbol, qty, fill, sl_trig, sl_exec)
    elif use_s4_exits:
        from strategies.s4 import compute_and_place_short_exits as _s4_short_exits
        ok, sl_trig, tp_trig = _s4_short_exits(symbol, qty, fill, sl_trig, sl_exec)
    elif use_s6_exits:
        from strategies.s6 import compute_and_place_short_exits as _s6_short_exits
        ok, sl_trig, tp_trig = _s6_short_exits(symbol, qty, fill, sl_trig, sl_exec)
    else:
        tp_trig = float(_round_price(fill * (1 - take_profit_pct), symbol))
        tp_exec = float(_round_price(tp_trig * 0.995, symbol))
        ok = _place_tpsl(symbol, "short", tp_trig, tp_exec, sl_trig, sl_exec)

    if not ok:
        logger.error(f"[{symbol}] ⚠️  TP/SL failed! Set manually: SL={sl_trig} TP={tp_trig}")

    result = {
        "symbol": symbol, "side": "SHORT", "qty": qty,
        "entry": fill, "sl": sl_trig, "tp": tp_trig,
        "box_high": box_high, "leverage": leverage,
        "margin": round(equity * trade_size_pct, 4), "tpsl_set": ok,
    }
    logger.info(
        f"[{symbol}] 🔴 SHORT {leverage}x | qty={qty} entry≈{fill:.5f} "
        f"SL={sl_trig} | {'✅ S5 exits' if use_s5_exits else '✅ S6 exits' if use_s6_exits else '✅ S4 exits' if use_s4_exits else '✅ S1 exits' if use_s1_exits else 'TP='+str(tp_trig)} | {'✅' if ok else '❌ SET MANUALLY'}"
    )
    return result


def scale_in_long(symbol: str, additional_trade_size_pct: float, leverage: int) -> None:
    """Add to an existing LONG position via a new market buy order."""
    equity   = _get_total_equity() or get_usdt_balance()
    mark     = get_mark_price(symbol)
    qty      = _round_qty((equity * additional_trade_size_pct * leverage) / mark, symbol)
    bc.post("/api/v2/mix/order/place-order", {
        "symbol": symbol, "productType": PRODUCT_TYPE,
        "marginMode": "isolated", "marginCoin": MARGIN_COIN,
        "size": qty, "side": "buy", "tradeSide": "open",
        "orderType": "market", "force": "ioc",
    })
    logger.info(f"[{symbol}] ➕ Scale-in LONG qty={qty} @ mark≈{mark:.5f}")


def scale_in_short(symbol: str, additional_trade_size_pct: float, leverage: int) -> None:
    """Add to an existing SHORT position via a new market sell order."""
    equity   = _get_total_equity() or get_usdt_balance()
    mark     = get_mark_price(symbol)
    qty      = _round_qty((equity * additional_trade_size_pct * leverage) / mark, symbol)
    bc.post("/api/v2/mix/order/place-order", {
        "symbol": symbol, "productType": PRODUCT_TYPE,
        "marginMode": "isolated", "marginCoin": MARGIN_COIN,
        "size": qty, "side": "sell", "tradeSide": "open",
        "orderType": "market", "force": "ioc",
    })
    logger.info(f"[{symbol}] ➕ Scale-in SHORT qty={qty} @ mark≈{mark:.5f}")


def get_history_position(symbol: str,
                         open_time_iso: str | None = None,
                         entry_price:   float | None = None,
                         retries: int = 3,
                         retry_delay: float = 1.5) -> dict | None:
    """
    Find a specific closed position in Bitget history-position API.

    When open_time_iso + entry_price are provided, queries from that open
    time and matches the record whose openAvgPrice is closest to entry_price.
    This handles multiple successive closes on the same symbol during a
    disconnect. Falls back to the most recent record if no match found.

    Retries up to `retries` times when achievedProfits == 0 (API not settled).
    Returns {pnl, exit_price, close_time} or None on error / no record.
    """
    import time as _time
    for attempt in range(retries):
        try:
            params: dict = {"productType": PRODUCT_TYPE, "symbol": symbol, "limit": "10"}
            if open_time_iso:
                try:
                    from datetime import datetime, timezone
                    dt = datetime.fromisoformat(open_time_iso)
                    params["startTime"] = str(int(dt.timestamp() * 1000))
                except Exception:
                    pass
            data    = bc.get("/api/v2/mix/position/history-position", params=params)
            records = data.get("data", {}).get("list") or data.get("data", [])
            if not records:
                return None

            # Match by entry price when provided; otherwise take the first record
            def _entry_of(r: dict) -> float:
                v = r.get("openAvgPrice") or r.get("openAveragePrice") or 0
                return float(v)

            if entry_price:
                records = sorted(records, key=lambda r: abs(_entry_of(r) - entry_price))

            r   = records[0]
            pnl = float(r.get("achievedProfits") or r.get("pnl") or 0)
            if pnl == 0:
                if attempt < retries - 1:
                    logger.debug(f"[{symbol}] get_history_position: pnl=0, retrying ({attempt+1}/{retries-1})")
                    _time.sleep(retry_delay)
                    continue
                logger.warning(f"[{symbol}] get_history_position: still 0 after {retries} attempts")
                return None

            close_avg = r.get("closeAvgPrice") or r.get("closeAveragePrice")
            close_ts  = r.get("closeTime") or r.get("updateTime")
            close_dt  = None
            if close_ts:
                try:
                    from datetime import datetime, timezone
                    close_dt = datetime.fromtimestamp(int(close_ts) / 1000, tz=timezone.utc).isoformat()
                except Exception:
                    pass
            return {
                "pnl":        pnl,
                "exit_price": float(close_avg) if close_avg else None,
                "close_time": close_dt,
            }
        except Exception as e:
            logger.warning(f"[{symbol}] get_history_position error: {e}")
            return None
    return None


def get_realized_pnl(symbol: str, retries: int = 3, retry_delay: float = 1.5) -> float | None:
    """
    Query the most recent closed position's realized PnL from Bitget.
    Used after a trailing stop fires to get accurate combined P/L.
    Retries up to `retries` times if achievedProfits is 0 (API not settled yet).
    Returns None on error or if still unsettled after all retries.
    """
    import time as _time
    for attempt in range(retries):
        try:
            data = bc.get("/api/v2/mix/position/history-position",
                          params={"productType": PRODUCT_TYPE, "symbol": symbol, "limit": "1"})
            records = data.get("data", {}).get("list") or data.get("data", [])
            if records:
                profits = float(records[0].get("achievedProfits", 0) or 0)
                if profits != 0:
                    return profits
                if attempt < retries - 1:
                    logger.debug(f"[{symbol}] get_realized_pnl: achievedProfits=0, retrying ({attempt+1}/{retries-1})")
                    _time.sleep(retry_delay)
        except Exception as e:
            logger.warning(f"[{symbol}] get_realized_pnl error: {e}")
            return None
    logger.warning(f"[{symbol}] get_realized_pnl: still 0 after {retries} attempts, falling back to unrealised_pnl")
    return None


def cancel_all_orders(symbol: str):
    try:
        bc.post("/api/v2/mix/order/cancel-all-orders", {
            "symbol": symbol, "productType": PRODUCT_TYPE, "marginCoin": MARGIN_COIN,
        })
    except Exception as e:
        logger.warning(f"[{symbol}] cancel orders warn: {e}")


def _place_plan_order(side: str, symbol: str, trigger_price: float,
                      sl_price: float, tp_price: float, qty_str: str) -> str:
    """
    Place a trigger (plan) entry order via Bitget's place-plan-order endpoint.

    For a LONG (buy), the order activates when mark price RISES to trigger_price —
    i.e. price must be below trigger at placement time. This is a stop-buy trigger,
    not a limit order, so it will NOT fill immediately if mark is already at or above
    the trigger.

    For a SHORT (sell), the order activates when mark price FALLS to trigger_price.

    sl_price, tp_price: passed to Bitget as presetStopLossPrice/presetTakeProfitPrice.
    If supported, the SL will be active immediately on fill (zero unprotected window).
    _place_s5_exits() still runs after fill to set partial TP and trailing stop.

    triggerType "mark_price" means Bitget uses the mark price for activation.
    orderType "market" executes at market once triggered (avoids a second limit slip).
    """
    resp = bc.post("/api/v2/mix/order/place-plan-order", {
        "symbol":                symbol,
        "productType":           PRODUCT_TYPE,
        "marginMode":            "isolated",
        "marginCoin":            MARGIN_COIN,
        "side":                  side,
        "tradeSide":             "open",
        "orderType":             "market",
        "size":                  qty_str,
        "triggerPrice":          _round_price(trigger_price, symbol),
        "triggerType":           "mark_price",
        "planType":              "normal_plan",
        "presetStopLossPrice":   _round_price(sl_price, symbol),
        "presetTakeProfitPrice": _round_price(tp_price, symbol),
    })
    # Note: If Bitget accepts presetStopLossPrice/presetTakeProfitPrice on plan orders,
    # the SL will be active immediately on fill (no unprotected window).
    # _place_s5_exits() in bot.py will still run to set partial TP and trailing stop.
    if resp.get("code") != "00000":
        raise RuntimeError(f"place_plan_order failed: {resp}")
    return str(resp["data"]["orderId"])


def place_limit_long(symbol: str, limit_price: float, sl_price: float,
                     tp_price: float, qty_str: str) -> str:
    """Place a trigger (plan) buy order that activates when mark rises to limit_price. Returns order_id."""
    return _place_plan_order("buy", symbol, limit_price, sl_price, tp_price, qty_str)


def place_limit_short(symbol: str, limit_price: float, sl_price: float,
                      tp_price: float, qty_str: str) -> str:
    """Place a trigger (plan) sell order that activates when mark falls to limit_price. Returns order_id."""
    return _place_plan_order("sell", symbol, limit_price, sl_price, tp_price, qty_str)


def cancel_order(symbol: str, order_id: str) -> None:
    """Cancel an open plan (trigger) order by order_id."""
    resp = bc.post("/api/v2/mix/order/cancel-plan-order", {
        "symbol":      symbol,
        "productType": PRODUCT_TYPE,
        "orderId":     order_id,
    })
    if resp.get("code") != "00000":
        raise RuntimeError(f"cancel_order failed: {resp}")


def get_order_fill(symbol: str, order_id: str) -> dict:
    """
    Poll the status of an S5 plan (trigger) order.
    Returns {"status": "live"|"filled"|"cancelled", "fill_price": float}

    Plan order statuses (Bitget):
      "not_trigger"  → waiting for trigger price — still live
      "triggered"    → trigger hit, child market order placed — treat as filled
                       (fill_price approximated from the position's avg entry)
      "cancel"       → cancelled externally
    """
    resp = bc.get("/api/v2/mix/order/plan-orders",
                  params={"symbol": symbol, "productType": PRODUCT_TYPE,
                          "planType": "normal_plan", "isPlan": "plan"})
    if resp.get("code") != "00000":
        raise RuntimeError(f"get_order_fill (plan) failed: {resp}")
    orders = (resp.get("data") or {}).get("entrustedList", [])
    for o in orders:
        if str(o.get("orderId")) == str(order_id):
            plan_status = o.get("planStatus", o.get("status", ""))
            if plan_status in ("not_trigger",):
                return {"status": "live", "fill_price": 0.0}
            if plan_status in ("triggered", "executed"):
                # Plan fired — get fill price from current position avg entry
                fill_price = 0.0
                try:
                    pos_data = bc.get("/api/v2/mix/position/single-position", {
                        "symbol": symbol, "productType": PRODUCT_TYPE, "marginCoin": MARGIN_COIN,
                    })
                    positions = (pos_data.get("data") or [])
                    if positions:
                        fill_price = float(positions[0].get("openPriceAvg", 0) or 0)
                except Exception:
                    pass
                return {"status": "filled", "fill_price": fill_price}
            return {"status": "cancelled", "fill_price": 0.0}
    # Order not found in active plan list — check history (triggered orders move to history)
    hist = bc.get("/api/v2/mix/order/plan-orders",
                  params={"symbol": symbol, "productType": PRODUCT_TYPE,
                          "planType": "normal_plan", "isPlan": "history"})
    if hist.get("code") == "00000":
        for o in (hist.get("data") or {}).get("entrustedList", []):
            if str(o.get("orderId")) == str(order_id):
                plan_status = o.get("planStatus", o.get("status", ""))
                if plan_status in ("triggered", "executed"):
                    fill_price = 0.0
                    try:
                        pos_data = bc.get("/api/v2/mix/position/single-position", {
                            "symbol": symbol, "productType": PRODUCT_TYPE, "marginCoin": MARGIN_COIN,
                        })
                        positions = (pos_data.get("data") or [])
                        if positions:
                            fill_price = float(positions[0].get("openPriceAvg", 0) or 0)
                    except Exception:
                        pass
                    return {"status": "filled", "fill_price": fill_price}
                return {"status": "cancelled", "fill_price": 0.0}
    # Not found anywhere — treat as cancelled
    return {"status": "cancelled", "fill_price": 0.0}


def is_partial_closed(symbol: str) -> bool:
    """Live mode: bot.py tracks partial via ap['partial_logged']; always returns False here."""
    return False


def update_position_sl(symbol: str, new_sl: float, hold_side: str = "long") -> bool:
    """
    Replace the position's SL via place-pos-tpsl (SL-only, no TP).
    Returns True on success.
    """
    import time as _t
    sl_trig = float(_round_price(new_sl, symbol))
    if hold_side == "long":
        sl_exec = float(_round_price(sl_trig * 0.995, symbol))
    else:
        sl_exec = float(_round_price(sl_trig * 1.005, symbol))
    for attempt in range(3):
        try:
            bc.post("/api/v2/mix/order/place-pos-tpsl", {
                "symbol":               symbol,
                "productType":          PRODUCT_TYPE,
                "marginCoin":           MARGIN_COIN,
                "holdSide":             hold_side,
                "stopLossTriggerPrice": str(sl_trig),
                "stopLossTriggerType":  "mark_price",
                "stopLossExecutePrice": str(sl_exec),
            })
            return True
        except Exception as e:
            logger.warning(f"[{symbol}] update_position_sl attempt {attempt+1}/3: {e}")
            if attempt < 2:
                _t.sleep(1.0)
    return False