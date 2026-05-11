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
    data = bc.get_public(
        "/v5/market/instruments-info",
        params={"category": CATEGORY},
    )
    rows = (data.get("result") or {}).get("list") or []
    for s in rows:
        symbol = s.get("symbol")
        if not symbol:
            continue
        lot   = s.get("lotSizeFilter")  or {}
        price = s.get("priceFilter")    or {}
        qty_step  = float(lot.get("qtyStep")       or "0.001")
        min_qty   = float(lot.get("minOrderQty")   or "0.001")
        tick_size = float(price.get("tickSize")    or "0.01")
        _sym_cache[symbol] = {
            "qty_step":      qty_step,
            "min_trade_num": min_qty,
            "tick_size":     tick_size,
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
        "tick_size": 0.01, "price_place": 2, "volume_place": 3,
    })


def round_price(price: float, symbol: str) -> str:
    info = sym_info(symbol)
    tick = info["tick_size"]
    rounded = math.floor(price / tick) * tick
    return f"{rounded:.{info['price_place']}f}"


def round_qty(qty: float, symbol: str) -> str:
    info = sym_info(symbol)
    step = info["qty_step"]
    qty  = math.floor(qty / step) * step
    qty  = max(qty, info["min_trade_num"])
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


def get_last_price(symbol: str) -> float:
    data = bc.get_public("/v5/market/tickers", params={"category": CATEGORY, "symbol": symbol})
    rows = (data.get("result") or {}).get("list") or []
    if not rows:
        raise RuntimeError(f"[Bybit][{symbol}] no ticker rows in response")
    return float(rows[0].get("lastPrice") or 0)


# ── Account ──────────────────────────────────────────────────────── #

def get_usdt_balance() -> float:
    """Available USDT balance under Unified Trading Account."""
    data = bc.get("/v5/account/wallet-balance", params={"accountType": "UNIFIED"})
    accounts = (data.get("result") or {}).get("list") or []
    for acct in accounts:
        for coin in acct.get("coin", []):
            if coin.get("coin") == SETTLE_COIN:
                avail = coin.get("availableToWithdraw") or coin.get("walletBalance") or 0
                try:
                    return float(avail)
                except (ValueError, TypeError):
                    return 0.0
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
    if sl_trigger is not None:
        body["stopLoss"]        = round_price(sl_trigger, symbol)
        body["slTriggerBy"]     = "MarkPrice"
        body["slOrderType"]     = "Market"
    if tp_trigger is not None:
        body["takeProfit"]      = round_price(tp_trigger, symbol)
        body["tpTriggerBy"]     = "MarkPrice"
        body["tpOrderType"]     = "Market"
    return bc.post("/v5/order/create", body)


def place_pos_tpsl_full(symbol: str, hold_side: str,
                        tp_trig: float, tp_exec: float,
                        sl_trig: float, sl_exec: float) -> bool:
    """
    Place combined TP+SL on a position via /v5/position/trading-stop.
    `tp_exec` and `sl_exec` are unused on Bybit (it places market exits on trigger).
    Retries 3x.
    """
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
                      trigger: float, range_rate: str) -> None:
    """
    Trailing stop on a position. Bybit V5 supports it via
    /v5/position/trading-stop with `trailingStop` + `activePrice`.

    range_rate is the trailing distance — Bybit takes it as a price-distance
    (e.g. "10" = $10 trailing). The Bitget-style percentage range_rate (e.g.
    "0.1" meaning 10%) is converted to absolute price distance using trigger.
    """
    try:
        pct = float(range_rate)
    except (ValueError, TypeError):
        pct = 0.1
    # If range_rate looks like a percentage (< 1), convert to absolute distance.
    if pct < 1:
        trailing_distance = trigger * pct
    else:
        trailing_distance = pct  # already absolute

    bc.post("/v5/position/trading-stop", {
        "category":      CATEGORY,
        "symbol":        symbol,
        "positionIdx":   0,
        "trailingStop":  round_price(trailing_distance, symbol),
        "activePrice":   round_price(trigger, symbol),
        "tpslMode":      "Partial",
    })


def update_position_sl(symbol: str, new_sl: float, hold_side: str = "long") -> bool:
    """Replace the position's SL via /v5/position/trading-stop. Returns True on success."""
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
        "stopLoss":         round_price(sl_price, symbol),
        "slTriggerBy":      "MarkPrice",
        "slOrderType":      "Market",
    }
    if tp_price and tp_price > 0:
        body["takeProfit"]   = round_price(tp_price, symbol)
        body["tpTriggerBy"]  = "MarkPrice"
        body["tpOrderType"]  = "Market"

    resp = bc.post("/v5/order/create", body)
    result = resp.get("result") or {}
    order_id = result.get("orderId")
    if not order_id:
        raise RuntimeError(f"[Bybit] place_plan_order: missing orderId in response: {resp}")
    return str(order_id)


def cancel_plan_order(symbol: str, order_id: str) -> None:
    """Cancel a single conditional/plan order by id."""
    resp = bc.post("/v5/order/cancel", {
        "category": CATEGORY,
        "symbol":   symbol,
        "orderId":  order_id,
    })
    if resp.get("retCode", 0) != 0:
        raise RuntimeError(f"[Bybit] cancel_plan_order failed: {resp}")


def cancel_all_orders(symbol: str) -> None:
    """Cancel all open + conditional orders for a symbol."""
    try:
        bc.post("/v5/order/cancel-all", {
            "category": CATEGORY,
            "symbol":   symbol,
        })
    except Exception as e:
        logger.warning(f"[Bybit][{symbol}] cancel orders warn: {e}")


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
    Find a specific closed position via /v5/position/closed-pnl.
    Returns {pnl, exit_price, close_time} or None.
    """
    for attempt in range(retries):
        try:
            params: dict = {"category": CATEGORY, "symbol": symbol, "limit": "10"}
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

            def _entry_of(r: dict) -> float:
                v = r.get("avgEntryPrice") or 0
                try:
                    return float(v)
                except (ValueError, TypeError):
                    return 0.0

            if entry_price:
                records = sorted(records, key=lambda r: abs(_entry_of(r) - entry_price))

            r   = records[0]
            pnl = float(r.get("closedPnl") or 0)
            if pnl == 0:
                if attempt < retries - 1:
                    logger.debug(f"[Bybit][{symbol}] get_history_position: pnl=0, retrying ({attempt+1}/{retries-1})")
                    _time.sleep(retry_delay)
                    continue
                logger.warning(f"[Bybit][{symbol}] get_history_position: still 0 after {retries} attempts")
                return None

            close_avg = r.get("avgExitPrice")
            close_ts  = r.get("updatedTime") or r.get("createdTime")
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
