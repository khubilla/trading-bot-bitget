"""
bitget.py — Bitget USDT-Futures API operations.

Wraps the endpoint-level calls over the low-level HTTP/auth client
(bitget_client.py).  No strategy knowledge lives here — functions are
generic primitives that strategies and trader.py compose.
"""
import math
import time as _time
import logging
from datetime import datetime, timezone

import pandas as pd

import bitget_client as bc
from config import PRODUCT_TYPE, MARGIN_COIN

logger = logging.getLogger(__name__)
_sym_cache: dict[str, dict] = {}


# ── Symbol metadata / rounding ───────────────────────────────────── #

def _load_symbol_cache():
    global _sym_cache
    if _sym_cache:
        return
    data = bc.get_public(
        "/api/v2/mix/market/contracts",
        params={"productType": PRODUCT_TYPE},
    )
    for s in data.get("data", []):
        _sym_cache[s["symbol"]] = {
            "price_place":   int(s.get("pricePlace",   2)),
            "volume_place":  int(s.get("volumePlace",  3)),
            "size_mult":     float(s.get("sizeMultiplier", 0.001)),
            "min_trade_num": float(s.get("minTradeNum", 0.001)),
        }
    logger.info(f"Symbol cache loaded: {len(_sym_cache)} contracts")


def sym_info(symbol: str) -> dict:
    _load_symbol_cache()
    return _sym_cache.get(symbol, {
        "price_place": 2, "volume_place": 3,
        "size_mult": 0.001, "min_trade_num": 0.001,
    })


def round_price(price: float, symbol: str) -> str:
    return str(round(price, sym_info(symbol)["price_place"]))


def round_qty(qty: float, symbol: str) -> str:
    info = sym_info(symbol)
    mult = info["size_mult"]
    qty  = math.floor(qty / mult) * mult
    qty  = max(qty, info["min_trade_num"])
    return str(round(qty, info["volume_place"]))


# ── Market data ──────────────────────────────────────────────────── #

_ccxt_ex = None


def get_candles(symbol: str, interval: str, limit: int = 100) -> pd.DataFrame:
    if interval in ("1D", "1d"):
        return get_daily_candles_utc(symbol, limit)
    data = bc.get_public(
        "/api/v2/mix/market/candles",
        params={"symbol": symbol, "productType": PRODUCT_TYPE,
                "granularity": interval, "limit": str(limit)},
    )
    rows = data.get("data", [])
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "vol", "quote_vol"])
    df[["open", "high", "low", "close", "vol"]] = df[["open", "high", "low", "close", "vol"]].astype(float)
    df["ts"] = df["ts"].astype(int)
    return df.sort_values("ts").reset_index(drop=True)


def get_daily_candles_utc(symbol: str, limit: int = 100) -> pd.DataFrame:
    """Fetch 1D candles via ccxt — UTC midnight boundaries (TradingView parity)."""
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
    df = pd.DataFrame(rows).sort_values("ts").reset_index(drop=True)
    try:
        mark = get_mark_price(symbol)
        df.at[df.index[-1], "close"] = mark
        df.at[df.index[-1], "high"]  = max(float(df.iloc[-1]["high"]), mark)
        df.at[df.index[-1], "low"]   = min(float(df.iloc[-1]["low"]),  mark)
    except Exception:
        pass
    return df


def fetch_candles_at(symbol: str, interval: str, limit: int, end_ms: int) -> pd.DataFrame:
    """
    Fetch up to `limit` candles ending at `end_ms` (epoch milliseconds).
    Uses Bitget's endTime query param — not exposed by get_candles().
    Returns empty DataFrame on error or no data.
    """
    try:
        data = bc.get_public(
            "/api/v2/mix/market/candles",
            params={
                "symbol":      symbol,
                "productType": PRODUCT_TYPE,
                "granularity": interval,
                "limit":       str(limit),
                "endTime":     str(end_ms),
            },
        )
        rows = data.get("data", [])
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "vol", "quote_vol"])
        df[["open", "high", "low", "close", "vol"]] = (
            df[["open", "high", "low", "close", "vol"]].astype(float)
        )
        df["ts"] = df["ts"].astype(int)
        return df.sort_values("ts").reset_index(drop=True)
    except Exception as e:
        logger.warning(f"[{symbol}] fetch_candles_at error: {e}")
        return pd.DataFrame()


def get_mark_price(symbol: str) -> float:
    data = bc.get_public(
        "/api/v2/mix/market/symbol-price",
        params={"symbol": symbol, "productType": PRODUCT_TYPE},
    )
    return float(data["data"][0]["markPrice"])


# ── Account ──────────────────────────────────────────────────────── #

def get_usdt_balance() -> float:
    data = bc.get("/api/v2/mix/account/accounts", params={"productType": PRODUCT_TYPE})
    for a in data.get("data", []):
        if a.get("marginCoin") == MARGIN_COIN:
            return float(a.get("available", 0))
    return 0.0


def get_total_equity() -> float:
    """Total account equity (available + locked margin + unrealized PnL) in USDT."""
    data = bc.get("/api/v2/mix/account/accounts", params={"productType": PRODUCT_TYPE})
    for a in data.get("data", []):
        if a.get("marginCoin") == MARGIN_COIN:
            return float(a.get("usdtEquity", 0) or a.get("equity", 0))
    return 0.0


def get_all_open_positions() -> dict[str, dict]:
    data = bc.get(
        "/api/v2/mix/position/all-position",
        params={"productType": PRODUCT_TYPE, "marginCoin": MARGIN_COIN},
    )
    result = {}
    for p in data.get("data", []):
        total = float(p.get("total", 0))
        if total <= 0:
            continue
        result[p["symbol"]] = {
            "side":           p.get("holdSide", "long").upper(),
            "entry_price":    float(p.get("openPriceAvg", 0)),
            "qty":            total,
            "unrealised_pnl": float(p.get("unrealizedPL", 0)),
            "mark_price":     float(p.get("markPrice", 0)),
            "margin":         float(p.get("marginSize", 0)),
            "leverage":       int(float(p.get("leverage", 0) or 0)),
        }
    return result


def get_single_position_entry(symbol: str) -> float:
    """Return avg entry of the current single-side position, or 0."""
    try:
        data = bc.get("/api/v2/mix/position/single-position", {
            "symbol": symbol, "productType": PRODUCT_TYPE, "marginCoin": MARGIN_COIN,
        })
        positions = (data.get("data") or [])
        if positions:
            return float(positions[0].get("openPriceAvg", 0) or 0)
    except Exception:
        pass
    return 0.0


# ── Leverage ─────────────────────────────────────────────────────── #

def set_leverage(symbol: str, leverage: int):
    """Sets leverage for both sides."""
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


# ── Order execution primitives ───────────────────────────────────── #

def place_market_order(symbol: str, side: str, qty_str: str) -> dict:
    """side: 'buy' | 'sell'. tradeSide=open. Returns the raw response dict."""
    return bc.post("/api/v2/mix/order/place-order", {
        "symbol":     symbol,
        "productType": PRODUCT_TYPE,
        "marginMode": "isolated",
        "marginCoin": MARGIN_COIN,
        "size":       qty_str,
        "side":       side,
        "tradeSide":  "open",
        "orderType":  "market",
        "force":      "ioc",
    })


def place_pos_tpsl_full(symbol: str, hold_side: str,
                        tp_trig: float, tp_exec: float,
                        sl_trig: float, sl_exec: float) -> bool:
    """Place combined TP+SL on a position (place-pos-tpsl). Retries 3x."""
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
                _time.sleep(1.5)
    return False


def place_pos_sl_only(symbol: str, hold_side: str, sl_trig: float, sl_exec: float) -> None:
    """Place SL-only on a full position (place-pos-tpsl, no TP)."""
    bc.post("/api/v2/mix/order/place-pos-tpsl", {
        "symbol":               symbol,
        "productType":          PRODUCT_TYPE,
        "marginCoin":           MARGIN_COIN,
        "holdSide":             hold_side,
        "stopLossTriggerPrice": str(sl_trig),
        "stopLossTriggerType":  "mark_price",
        "stopLossExecutePrice": str(sl_exec),
    })


def place_profit_plan(symbol: str, hold_side: str, qty_str: str,
                      trigger: float, execute: str = "0") -> None:
    """place-tpsl-order planType=profit_plan (absolute-price partial TP)."""
    bc.post("/api/v2/mix/order/place-tpsl-order", {
        "symbol":       symbol,
        "productType":  PRODUCT_TYPE,
        "marginCoin":   MARGIN_COIN,
        "planType":     "profit_plan",
        "triggerPrice": str(trigger),
        "triggerType":  "mark_price",
        "executePrice": execute,
        "holdSide":     hold_side,
        "size":         qty_str,
    })


def place_moving_plan(symbol: str, hold_side: str, qty_str: str,
                      trigger: float, range_rate: str) -> None:
    """place-tpsl-order planType=moving_plan (trailing stop)."""
    bc.post("/api/v2/mix/order/place-tpsl-order", {
        "symbol":       symbol,
        "productType":  PRODUCT_TYPE,
        "marginCoin":   MARGIN_COIN,
        "planType":     "moving_plan",
        "triggerPrice": str(trigger),
        "triggerType":  "mark_price",
        "holdSide":     hold_side,
        "size":         qty_str,
        "rangeRate":    range_rate,
    })


def update_position_sl(symbol: str, new_sl: float, hold_side: str = "long") -> bool:
    """
    Replace the position's SL via place-pos-tpsl (SL-only, no TP).
    Returns True on success.
    """
    sl_trig = float(round_price(new_sl, symbol))
    if hold_side == "long":
        sl_exec = float(round_price(sl_trig * 0.995, symbol))
    else:
        sl_exec = float(round_price(sl_trig * 1.005, symbol))
    for attempt in range(3):
        try:
            place_pos_sl_only(symbol, hold_side, sl_trig, sl_exec)
            return True
        except Exception as e:
            logger.warning(f"[{symbol}] update_position_sl attempt {attempt+1}/3: {e}")
            if attempt < 2:
                _time.sleep(1.0)
    return False


# ── Plan (trigger) orders ────────────────────────────────────────── #

def place_plan_order(side: str, symbol: str, trigger_price: float,
                     sl_price: float, tp_price: float, qty_str: str) -> str:
    """
    Place a trigger (plan) entry order. Returns orderId.

    For LONG (buy), activates when mark RISES to trigger_price.
    For SHORT (sell), activates when mark FALLS to trigger_price.
    Preset SL/TP are applied immediately on fill if supported.
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
        "triggerPrice":          round_price(trigger_price, symbol),
        "triggerType":           "mark_price",
        "planType":              "normal_plan",
        "presetStopLossPrice":   round_price(sl_price, symbol),
        "presetTakeProfitPrice": round_price(tp_price, symbol),
    })
    if resp.get("code") != "00000":
        raise RuntimeError(f"place_plan_order failed: {resp}")
    return str(resp["data"]["orderId"])


def cancel_plan_order(symbol: str, order_id: str) -> None:
    """Cancel an open plan (trigger) order by order_id."""
    resp = bc.post("/api/v2/mix/order/cancel-plan-order", {
        "symbol":      symbol,
        "productType": PRODUCT_TYPE,
        "orderId":     order_id,
    })
    if resp.get("code") != "00000":
        raise RuntimeError(f"cancel_plan_order failed: {resp}")


def cancel_all_orders(symbol: str) -> None:
    try:
        bc.post("/api/v2/mix/order/cancel-all-orders", {
            "symbol":      symbol,
            "productType": PRODUCT_TYPE,
            "marginCoin":  MARGIN_COIN,
        })
    except Exception as e:
        logger.warning(f"[{symbol}] cancel orders warn: {e}")


def get_order_fill(symbol: str, order_id: str) -> dict:
    """
    Poll the status of a plan (trigger) order.
    Returns {"status": "live"|"filled"|"cancelled", "fill_price": float}
    """
    resp = bc.get(
        "/api/v2/mix/order/orders-plan-pending",
        params={"symbol": symbol, "productType": PRODUCT_TYPE, "planType": "normal_plan"},
    )
    if resp.get("code") != "00000":
        raise RuntimeError(f"get_order_fill (plan) failed: {resp}")
    orders = (resp.get("data") or {}).get("entrustedList", [])
    for o in orders:
        if str(o.get("orderId")) == str(order_id):
            plan_status = o.get("planStatus", o.get("status", ""))
            if plan_status in ("not_trigger",):
                return {"status": "live", "fill_price": 0.0}
            if plan_status in ("triggered", "executed"):
                return {"status": "filled", "fill_price": get_single_position_entry(symbol)}
            return {"status": "cancelled", "fill_price": 0.0}
    # Order not in active plan list — check history
    hist = bc.get(
        "/api/v2/mix/order/orders-plan-history",
        params={"symbol": symbol, "productType": PRODUCT_TYPE, "planType": "normal_plan"},
    )
    if hist.get("code") == "00000":
        for o in (hist.get("data") or {}).get("entrustedList", []):
            if str(o.get("orderId")) == str(order_id):
                plan_status = o.get("planStatus", o.get("status", ""))
                if plan_status in ("triggered", "executed"):
                    return {"status": "filled", "fill_price": get_single_position_entry(symbol)}
                return {"status": "cancelled", "fill_price": 0.0}
    return {"status": "cancelled", "fill_price": 0.0}


def refresh_plan_exits(symbol: str, hold_side: str, new_trail_trigger: float = 0) -> bool:
    """
    Resize profit_plan + moving_plan for the current total position qty.
    Called after a scale-in. SL (place-pos-tpsl) auto-scales — untouched.

    new_trail_trigger: if > 0, re-place with this trigger; else preserve the
    existing profit_plan trigger.
    """
    data    = bc.get("/api/v2/mix/order/orders-plan-pending", {"symbol": symbol, "productType": PRODUCT_TYPE})
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
            _time.sleep(0.3)
        except Exception as e:
            logger.warning(f"[{symbol}] cancel plan order {o['orderId']}: {e}")

    _time.sleep(0.5)

    positions       = get_all_open_positions()
    total_qty_float = float((positions.get(symbol) or {}).get("qty", 0))
    if total_qty_float <= 0:
        logger.error(f"[{symbol}] refresh_plan_exits: position not found after scale-in")
        return False

    half_qty = round_qty(total_qty_float / 2, symbol)
    rest_qty = round_qty(total_qty_float - float(half_qty), symbol)

    for attempt in range(3):
        try:
            place_profit_plan(symbol, hold_side, half_qty, trail_trigger)
            _time.sleep(0.5)
            place_moving_plan(symbol, hold_side, rest_qty, trail_trigger, trail_range)
            logger.info(
                f"[{symbol}] ✅ Plan exits refreshed after scale-in: "
                f"profit_plan={half_qty}@{trail_trigger}, moving_plan={rest_qty}"
            )
            return True
        except Exception as e:
            logger.warning(f"[{symbol}] refresh_plan_exits attempt {attempt+1}/3: {e}")
            if attempt < 2:
                _time.sleep(1.5)
    return False


# ── History / closed positions ───────────────────────────────────── #

def get_history_position(symbol: str,
                         open_time_iso: str | None = None,
                         entry_price:   float | None = None,
                         retries: int = 3,
                         retry_delay: float = 1.5) -> dict | None:
    """
    Find a specific closed position in Bitget history-position API.
    When open_time_iso + entry_price are provided, prefers the record with
    closest openAvgPrice match. Retries when achievedProfits == 0 (API lag).
    """
    for attempt in range(retries):
        try:
            params: dict = {"productType": PRODUCT_TYPE, "symbol": symbol, "limit": "10"}
            if open_time_iso:
                try:
                    dt = datetime.fromisoformat(open_time_iso)
                    params["startTime"] = str(int(dt.timestamp() * 1000))
                except Exception:
                    pass
            data    = bc.get("/api/v2/mix/position/history-position", params=params)
            records = data.get("data", {}).get("list") or data.get("data", [])
            if not records:
                return None

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
    """Most recent closed position's realized PnL. Retries on API lag."""
    for attempt in range(retries):
        try:
            data = bc.get(
                "/api/v2/mix/position/history-position",
                params={"productType": PRODUCT_TYPE, "symbol": symbol, "limit": "1"},
            )
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
    logger.warning(f"[{symbol}] get_realized_pnl: still 0 after {retries} attempts")
    return None
