"""
binance.py — Binance USDT-M Futures API operations.

Wraps the FAPI endpoint-level calls over the low-level HTTP/auth client
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


# ── DRY_RUN guard ─────────────────────────────────────────────────── #
#
# binance_trader.open_long/short already intercept the top-level entry call
# when config_binance.DRY_RUN is True, but strategies/* exit helpers reach into
# binance.place_pos_sl_only / place_profit_plan / place_moving_plan directly
# via `import bitget as bg` (aliased to binance). Without a guard here those
# calls would hit the real Binance API and fail — there's no position to attach
# SL/TP to. This helper short-circuits every write at the lowest level so
# DRY_RUN is truly read-only.

def _dry_run_active() -> bool:
    """Re-read DRY_RUN every call so a config flip takes effect on next call."""
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


# ── Interval mapping ──────────────────────────────────────────────── #
#   Bitget / strategy:  "1m"  "3m"  "5m"  "15m"  "1H"  "4H"  "1D"
#   Binance:            "1m"  "3m"  "5m"  "15m"  "1h"  "4h"  "1d"   (lowercase)
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


# ── Symbol metadata / rounding ────────────────────────────────────── #

def _load_symbol_cache():
    global _sym_cache
    if _sym_cache:
        return
    data = bc.get_public("/fapi/v1/exchangeInfo")
    symbols = (data or {}).get("symbols") or []
    for s in symbols:
        symbol = s.get("symbol")
        status = s.get("status")
        contract_type = s.get("contractType")
        if not symbol or status != "TRADING" or contract_type != "PERPETUAL":
            continue
        if s.get("quoteAsset") != SETTLE_COIN:
            continue
        qty_step    = 0.001
        min_qty     = 0.001
        tick_size   = 0.01
        min_notional = 0.0
        for f in s.get("filters") or []:
            ft = f.get("filterType")
            if ft == "LOT_SIZE":
                qty_step = float(f.get("stepSize") or qty_step)
                min_qty  = float(f.get("minQty")   or min_qty)
            elif ft == "PRICE_FILTER":
                tick_size = float(f.get("tickSize") or tick_size)
            elif ft == "MIN_NOTIONAL":
                min_notional = float(f.get("notional") or min_notional)
        _sym_cache[symbol] = {
            "qty_step":      qty_step,
            "min_trade_num": min_qty,
            "tick_size":     tick_size,
            "min_notional":  min_notional,
            "price_place":   _decimals(tick_size),
            "volume_place":  _decimals(qty_step),
        }
    logger.info(f"[Binance] Symbol cache loaded: {len(_sym_cache)} contracts")


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
    Floor qty to qty_step, enforce min_trade_num. When `mark_price` is provided
    AND the symbol has a min_notional, also bump qty so qty × mark_price ≥
    min_notional (Binance USDT-M Futures rejects orders below ~$5 notional).

    Callers SPLITTING an existing position qty (e.g. half / rest for partial
    TP + trailing stop) should NOT pass mark_price — the notional requirement
    was already satisfied by the original entry order.
    """
    info = sym_info(symbol)
    step = info["qty_step"]
    qty  = math.floor(qty / step) * step
    qty  = max(qty, info["min_trade_num"])
    min_notional = info.get("min_notional", 0.0)
    if mark_price is not None and mark_price > 0 and min_notional > 0:
        min_qty_notional = math.ceil((min_notional / mark_price) / step) * step
        qty = max(qty, min_qty_notional)
    return f"{qty:.{info['volume_place']}f}"


# ── Market data ──────────────────────────────────────────────────── #

def get_candles(symbol: str, interval: str, limit: int = 100) -> pd.DataFrame:
    """
    Fetch klines via /fapi/v1/klines. Binance returns oldest-first already.

    Returns DataFrame with columns: ts, open, high, low, close, vol, quote_vol
    """
    try:
        rows = bc.get_public(
            "/fapi/v1/klines",
            params={
                "symbol":   symbol,
                "interval": _map_interval(interval),
                "limit":    str(limit),
            },
        )
        if not rows:
            return pd.DataFrame()
        # Binance row: [openTime, open, high, low, close, volume, closeTime,
        #               quoteAssetVolume, trades, takerBuyBase, takerBuyQuote, ignore]
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
            logger.warning(f"[Binance][{symbol}] candles: invalid/delisted, skipping: {e}")
        else:
            logger.warning(f"[Binance][{symbol}] get_candles error: {e}")
        return pd.DataFrame()


def fetch_candles_at(symbol: str, interval: str, limit: int, end_ms: int) -> pd.DataFrame:
    """Fetch up to `limit` candles ending at `end_ms` (epoch ms). Binance uses `endTime`."""
    try:
        rows = bc.get_public(
            "/fapi/v1/klines",
            params={
                "symbol":   symbol,
                "interval": _map_interval(interval),
                "limit":    str(limit),
                "endTime":  str(end_ms),
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
    """Mark price from /fapi/v1/premiumIndex."""
    data = bc.get_public("/fapi/v1/premiumIndex", params={"symbol": symbol})
    if isinstance(data, list):
        # Defensive: endpoint can return list if symbol param is dropped.
        data = data[0] if data else {}
    return float((data or {}).get("markPrice") or 0)


def get_last_price(symbol: str) -> float:
    data = bc.get_public("/fapi/v1/ticker/price", params={"symbol": symbol})
    if isinstance(data, list):
        data = data[0] if data else {}
    return float((data or {}).get("price") or 0)


# ── Account ───────────────────────────────────────────────────────── #

def get_usdt_balance() -> float:
    """
    *Free* USDT availableBalance — matches Bitget's `available` semantic
    (excludes locked position/order margin AND unrealised PnL). Used by the
    dashboard formula:

        total_equity = balance + sum(open_trade.margin) + sum(open_trade.upnl)

    so balance must NOT include locked margin.
    """
    rows = bc.get("/fapi/v2/balance")
    if not isinstance(rows, list):
        return 0.0
    for r in rows:
        if r.get("asset") != SETTLE_COIN:
            continue
        try:
            return float(r.get("availableBalance") or 0)
        except (ValueError, TypeError):
            return 0.0
    return 0.0


def get_total_equity() -> float:
    """Total margin balance under USDT-M futures account (wallet + unrealised PnL)."""
    data = bc.get("/fapi/v2/account")
    try:
        return float((data or {}).get("totalMarginBalance") or 0)
    except (ValueError, TypeError):
        return 0.0


def get_all_open_positions() -> dict[str, dict]:
    """Open positions keyed by symbol. Shape matches bitget.get_all_open_positions."""
    rows = bc.get("/fapi/v2/positionRisk")
    if not isinstance(rows, list):
        return {}
    result: dict[str, dict] = {}
    for p in rows:
        try:
            amt = float(p.get("positionAmt") or 0)
        except (ValueError, TypeError):
            amt = 0.0
        if amt == 0:
            continue
        symbol = p.get("symbol")
        if not symbol:
            continue
        side  = "LONG" if amt > 0 else "SHORT"
        qty   = abs(amt)
        try:
            entry = float(p.get("entryPrice") or 0)
            mark  = float(p.get("markPrice")  or 0)
            upnl  = float(p.get("unRealizedProfit") or 0)
            lev   = int(float(p.get("leverage") or 0))
        except (ValueError, TypeError):
            continue
        # Binance doesn't return positionIM directly; approximate as notional / leverage.
        notional = qty * mark
        margin   = (notional / lev) if lev > 0 else 0.0
        result[symbol] = {
            "side":           side,
            "entry_price":    entry,
            "qty":            qty,
            "unrealised_pnl": upnl,
            "mark_price":     mark,
            "margin":         margin,
            "leverage":       lev,
        }
    return result


def get_single_position_entry(symbol: str) -> float:
    """Avg entry price of the current position, or 0."""
    try:
        rows = bc.get("/fapi/v2/positionRisk", params={"symbol": symbol})
        if not isinstance(rows, list):
            return 0.0
        for p in rows:
            amt = float(p.get("positionAmt") or 0)
            if amt != 0:
                return float(p.get("entryPrice") or 0)
    except Exception:
        pass
    return 0.0


# ── Leverage ──────────────────────────────────────────────────────── #

def set_leverage(symbol: str, leverage: int):
    """Set leverage for the symbol. Idempotent."""
    if _dry_run_skip("set_leverage", symbol=symbol, leverage=leverage):
        return
    try:
        bc.post("/fapi/v1/leverage", {"symbol": symbol, "leverage": str(leverage)})
        logger.info(f"[Binance][{symbol}] Leverage set to {leverage}x")
    except RuntimeError as e:
        if "-4061" in str(e):
            logger.debug(f"[Binance][{symbol}] Leverage already {leverage}x")
        else:
            logger.warning(f"[Binance][{symbol}] set_leverage warn: {e}")


# ── Position mode ─────────────────────────────────────────────────── #

def ensure_one_way_mode() -> None:
    """Force the account into one-way mode. Idempotent."""
    if _dry_run_skip("ensure_one_way_mode"):
        return
    try:
        bc.post("/fapi/v1/positionSide/dual", {"dualSidePosition": "false"})
        logger.info("[Binance] Position mode = ONE-WAY")
    except RuntimeError as e:
        if "-4059" in str(e):
            logger.debug("[Binance] Position mode already ONE-WAY")
        else:
            logger.warning(f"[Binance] ensure_one_way_mode warn: {e}")


# ── Helpers ──────────────────────────────────────────────────────── #

def _cancel_position_close_orders(symbol: str, close_side: str, types: tuple[str, ...]) -> None:
    """Cancel any existing closePosition orders matching given types/side. Used before re-attaching."""
    try:
        rows = bc.get("/fapi/v1/openOrders", params={"symbol": symbol})
    except Exception as e:
        logger.warning(f"[Binance][{symbol}] list openOrders warn: {e}")
        return
    if not isinstance(rows, list):
        return
    for o in rows:
        if o.get("side") != close_side:
            continue
        if o.get("type") not in types:
            continue
        if not o.get("closePosition"):
            continue
        try:
            bc.delete("/fapi/v1/order",
                      params={"symbol": symbol, "orderId": o["orderId"]})
        except Exception as e:
            logger.warning(f"[Binance][{symbol}] cancel close-order {o.get('orderId')} warn: {e}")


# ── Order execution primitives ───────────────────────────────────── #

def place_market_order(symbol: str, side: str, qty_str: str,
                       sl_trigger: float | None = None,
                       tp_trigger: float | None = None) -> dict:
    """
    Place a market order on a USDT-M perp.

    side: 'buy' | 'sell' (bitget-style); converted to Binance's 'BUY' | 'SELL'.

    Unlike Bybit, Binance does NOT support atomic SL/TP fields on /fapi/v1/order.
    When sl_trigger / tp_trigger are supplied we issue follow-up STOP_MARKET /
    TAKE_PROFIT_MARKET orders with closePosition=true so they auto-resize with
    the position (no need to track qty across partial closes).
    """
    if _dry_run_skip("place_market_order", symbol=symbol, side=side, qty=qty_str,
                     sl=sl_trigger, tp=tp_trigger):
        return {"orderId": f"DRY-{symbol}-{int(_time.time())}"}
    binance_side = "BUY" if side.lower() == "buy" else "SELL"
    body = {
        "symbol":   symbol,
        "side":     binance_side,
        "type":     "MARKET",
        "quantity": qty_str,
    }
    info = sym_info(symbol)
    logger.info(
        f"[Binance][{symbol}] /fapi/v1/order MARKET {binance_side} qty={qty_str} "
        f"(min_qty={info.get('min_trade_num')}, qty_step={info.get('qty_step')}, "
        f"min_notional={info.get('min_notional')}) sl={sl_trigger} tp={tp_trigger}"
    )
    resp = bc.post("/fapi/v1/order", body)

    close_side = "SELL" if binance_side == "BUY" else "BUY"
    if sl_trigger is not None:
        try:
            bc.post("/fapi/v1/order", {
                "symbol":         symbol,
                "side":           close_side,
                "type":           "STOP_MARKET",
                "stopPrice":      round_price(sl_trigger, symbol),
                "closePosition":  "true",
                "workingType":    "MARK_PRICE",
                "priceProtect":   "true",
            })
        except RuntimeError as e:
            logger.warning(f"[Binance][{symbol}] preset SL attach failed: {e}")
    if tp_trigger is not None:
        try:
            bc.post("/fapi/v1/order", {
                "symbol":        symbol,
                "side":          close_side,
                "type":          "TAKE_PROFIT_MARKET",
                "stopPrice":     round_price(tp_trigger, symbol),
                "closePosition": "true",
                "workingType":   "MARK_PRICE",
                "priceProtect":  "true",
            })
        except RuntimeError as e:
            logger.warning(f"[Binance][{symbol}] preset TP attach failed: {e}")
    return resp if isinstance(resp, dict) else {}


def place_pos_tpsl_full(symbol: str, hold_side: str,
                        tp_trig: float, tp_exec: float,
                        sl_trig: float, sl_exec: float) -> bool:
    """
    Place combined TP+SL on a position. Binance: cancels any existing TP/SL
    closePosition orders on the close side, then posts fresh ones.
    `tp_exec` and `sl_exec` are unused on Binance (closePosition=true).
    """
    if _dry_run_skip("place_pos_tpsl_full", symbol=symbol, hold=hold_side,
                     tp=tp_trig, sl=sl_trig):
        return True
    close_side = "SELL" if hold_side == "long" else "BUY"
    _cancel_position_close_orders(symbol, close_side, ("STOP_MARKET", "TAKE_PROFIT_MARKET"))
    for attempt in range(3):
        try:
            bc.post("/fapi/v1/order", {
                "symbol":        symbol,
                "side":          close_side,
                "type":          "STOP_MARKET",
                "stopPrice":     round_price(sl_trig, symbol),
                "closePosition": "true",
                "workingType":   "MARK_PRICE",
                "priceProtect":  "true",
            })
            bc.post("/fapi/v1/order", {
                "symbol":        symbol,
                "side":          close_side,
                "type":          "TAKE_PROFIT_MARKET",
                "stopPrice":     round_price(tp_trig, symbol),
                "closePosition": "true",
                "workingType":   "MARK_PRICE",
                "priceProtect":  "true",
            })
            return True
        except Exception as e:
            logger.warning(f"[Binance][{symbol}] TP/SL attempt {attempt+1}/3: {e}")
            if attempt < 2:
                _time.sleep(1.5)
    return False


def place_pos_sl_only(symbol: str, hold_side: str, sl_trig: float, sl_exec: float) -> None:
    """Place SL-only closePosition order. Cancels any existing STOP_MARKET on this side first."""
    if _dry_run_skip("place_pos_sl_only", symbol=symbol, hold=hold_side, sl=sl_trig):
        return
    close_side = "SELL" if hold_side == "long" else "BUY"
    _cancel_position_close_orders(symbol, close_side, ("STOP_MARKET",))
    bc.post("/fapi/v1/order", {
        "symbol":        symbol,
        "side":          close_side,
        "type":          "STOP_MARKET",
        "stopPrice":     round_price(sl_trig, symbol),
        "closePosition": "true",
        "workingType":   "MARK_PRICE",
        "priceProtect":  "true",
    })


def place_profit_plan(symbol: str, hold_side: str, qty_str: str,
                      trigger: float, execute: str = "0") -> None:
    """
    Partial take-profit: reduce-only TAKE_PROFIT_MARKET with explicit qty
    (NOT closePosition=true — we only want to close `qty_str`, not the full
    position).
    """
    if _dry_run_skip("place_profit_plan", symbol=symbol, hold=hold_side,
                     qty=qty_str, trigger=trigger):
        return
    close_side = "SELL" if hold_side == "long" else "BUY"
    bc.post("/fapi/v1/order", {
        "symbol":       symbol,
        "side":         close_side,
        "type":         "TAKE_PROFIT_MARKET",
        "stopPrice":    round_price(trigger, symbol),
        "quantity":     qty_str,
        "reduceOnly":   "true",
        "workingType":  "MARK_PRICE",
        "priceProtect": "true",
    })


def place_moving_plan(symbol: str, hold_side: str, qty_str: str,
                      trigger: float, range_rate: str) -> None:
    """
    Trailing stop via Binance TRAILING_STOP_MARKET (reduce-only).

    range_rate: Bitget-flavoured percentage value. We normalise both forms:
      - values ≥ 1 are integer-percent (10 → 10%)
      - values < 1 are decimal fractions (0.05 → 5%)
    Binance's `callbackRate` field expects an integer-percent string with
    1 decimal place, hard-clamped to the range [0.1, 5.0].

    NOTE: Binance trailing stops are order-level, not position-level. After a
    partial-TP fires the trailing order remains (`reduceOnly`+`quantity` will
    fill whatever's left up to qty_str).
    """
    if _dry_run_skip("place_moving_plan", symbol=symbol, hold=hold_side,
                     trigger=trigger, range_rate=range_rate):
        return
    try:
        raw = float(range_rate)
    except (ValueError, TypeError):
        raw = 10.0
    pct = raw if raw >= 1 else raw * 100.0
    pct = max(0.1, min(5.0, pct))
    if pct < raw and raw > 5:
        logger.warning(
            f"[Binance][{symbol}] trailing callbackRate clamped to 5.0% (requested {raw}%)"
        )
    close_side = "SELL" if hold_side == "long" else "BUY"
    bc.post("/fapi/v1/order", {
        "symbol":          symbol,
        "side":            close_side,
        "type":            "TRAILING_STOP_MARKET",
        "quantity":        qty_str,
        "activationPrice": round_price(trigger, symbol),
        "callbackRate":    f"{pct:.1f}",
        "reduceOnly":      "true",
        "workingType":     "MARK_PRICE",
    })


def refresh_plan_exits(symbol: str, hold_side: str, new_trail_trigger: float = 0) -> bool:
    """
    Resize partial-TP + trailing stop after a scale-in. Cancels the existing
    reduce-only TAKE_PROFIT_MARKET + TRAILING_STOP_MARKET orders on the close
    side and re-places them at new total/2 qty.

    Args:
        new_trail_trigger: if > 0, used as the new partial-TP trigger AND
            trailing-stop activationPrice. If 0, the previous TP order's
            stopPrice is preserved.

    Returns True on success.
    """
    if _dry_run_skip("refresh_plan_exits", symbol=symbol, hold=hold_side,
                     trigger=new_trail_trigger):
        return True
    close_side = "SELL" if hold_side == "long" else "BUY"
    try:
        rows = bc.get("/fapi/v1/openOrders", params={"symbol": symbol})
    except Exception as e:
        logger.error(f"[Binance][{symbol}] refresh_plan_exits openOrders failed: {e}")
        return False
    if not isinstance(rows, list):
        rows = []
    existing_tp = None
    existing_trail = None
    for o in rows:
        if o.get("side") != close_side or not o.get("reduceOnly"):
            continue
        otype = o.get("type")
        if otype == "TAKE_PROFIT_MARKET" and not o.get("closePosition"):
            existing_tp = o
        elif otype == "TRAILING_STOP_MARKET":
            existing_trail = o
    if not existing_tp and not existing_trail:
        logger.warning(
            f"[Binance][{symbol}] refresh_plan_exits: no reduce-only TP / trailing "
            f"found — exits unchanged"
        )
        return False
    trigger = new_trail_trigger
    if trigger <= 0 and existing_tp:
        try:
            trigger = float(existing_tp.get("stopPrice") or 0)
        except (ValueError, TypeError):
            trigger = 0.0
    if trigger <= 0:
        logger.error(f"[Binance][{symbol}] refresh_plan_exits: no valid trigger")
        return False
    for o in (existing_tp, existing_trail):
        if not o:
            continue
        try:
            bc.delete("/fapi/v1/order",
                      params={"symbol": symbol, "orderId": o["orderId"]})
            _time.sleep(0.3)
        except Exception as e:
            logger.warning(f"[Binance][{symbol}] cancel {o.get('type')} warn: {e}")
    _time.sleep(0.5)
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
    """Replace the position SL: cancel existing closePosition STOP_MARKET, place a fresh one."""
    if _dry_run_skip("update_position_sl", symbol=symbol, hold=hold_side, new_sl=new_sl):
        return True
    close_side = "SELL" if hold_side == "long" else "BUY"
    _cancel_position_close_orders(symbol, close_side, ("STOP_MARKET",))
    sl_str = round_price(new_sl, symbol)
    for attempt in range(3):
        try:
            bc.post("/fapi/v1/order", {
                "symbol":        symbol,
                "side":          close_side,
                "type":          "STOP_MARKET",
                "stopPrice":     sl_str,
                "closePosition": "true",
                "workingType":   "MARK_PRICE",
                "priceProtect":  "true",
            })
            return True
        except Exception as e:
            logger.warning(f"[Binance][{symbol}] update_position_sl attempt {attempt+1}/3: {e}")
            if attempt < 2:
                _time.sleep(1.0)
    return False


# ── Plan (conditional / trigger) orders ──────────────────────────── #

def place_plan_order(side: str, symbol: str, trigger_price: float,
                     sl_price: float, tp_price: float, qty_str: str) -> str:
    """
    Place a conditional market entry: STOP_MARKET that activates when mark
    reaches trigger_price.

    Note: Binance does not support atomic SL/TP on a conditional entry. SL and
    TP closePosition orders are attached immediately; they are inert until the
    entry fills (and they only close *open* positions, so they sit dormant if
    the conditional entry never fires).
    """
    if _dry_run_skip("place_plan_order", symbol=symbol, side=side,
                     trigger=trigger_price, sl=sl_price, tp=tp_price, qty=qty_str):
        return f"DRY-{symbol}-{int(_time.time())}"
    binance_side = "BUY" if side.lower() == "buy" else "SELL"
    body = {
        "symbol":       symbol,
        "side":         binance_side,
        "type":         "STOP_MARKET",
        "stopPrice":    round_price(trigger_price, symbol),
        "quantity":     qty_str,
        "workingType":  "MARK_PRICE",
        "priceProtect": "true",
    }
    info = sym_info(symbol)
    logger.info(
        f"[Binance][{symbol}] /fapi/v1/order STOP_MARKET {binance_side} qty={qty_str} "
        f"trigger={body['stopPrice']} (min_qty={info.get('min_trade_num')}, "
        f"qty_step={info.get('qty_step')}, min_notional={info.get('min_notional')}) "
        f"sl={sl_price} tp={tp_price}"
    )
    resp = bc.post("/fapi/v1/order", body)
    order_id = (resp or {}).get("orderId") if isinstance(resp, dict) else None
    if not order_id:
        raise RuntimeError(f"[Binance] place_plan_order: missing orderId in response: {resp}")

    close_side = "SELL" if binance_side == "BUY" else "BUY"
    if sl_price and sl_price > 0:
        try:
            bc.post("/fapi/v1/order", {
                "symbol":        symbol,
                "side":          close_side,
                "type":          "STOP_MARKET",
                "stopPrice":     round_price(sl_price, symbol),
                "closePosition": "true",
                "workingType":   "MARK_PRICE",
                "priceProtect":  "true",
            })
        except RuntimeError as e:
            logger.warning(f"[Binance][{symbol}] plan SL attach warn: {e}")
    if tp_price and tp_price > 0:
        try:
            bc.post("/fapi/v1/order", {
                "symbol":        symbol,
                "side":          close_side,
                "type":          "TAKE_PROFIT_MARKET",
                "stopPrice":     round_price(tp_price, symbol),
                "closePosition": "true",
                "workingType":   "MARK_PRICE",
                "priceProtect":  "true",
            })
        except RuntimeError as e:
            logger.warning(f"[Binance][{symbol}] plan TP attach warn: {e}")
    return str(order_id)


def cancel_plan_order(symbol: str, order_id: str) -> None:
    """Cancel a single conditional/plan order by id."""
    if _dry_run_skip("cancel_plan_order", symbol=symbol, orderId=order_id):
        return
    bc.delete("/fapi/v1/order", params={"symbol": symbol, "orderId": order_id})


def cancel_all_orders(symbol: str) -> None:
    """Cancel all open orders for a symbol (regular + conditional + closePosition)."""
    if _dry_run_skip("cancel_all_orders", symbol=symbol):
        return
    try:
        bc.delete("/fapi/v1/allOpenOrders", params={"symbol": symbol})
    except Exception as e:
        if "-2011" in str(e):
            logger.debug(f"[Binance][{symbol}] cancel_all_orders: nothing to cancel")
        else:
            logger.warning(f"[Binance][{symbol}] cancel_all_orders warn: {e}")


def get_order_fill(symbol: str, order_id: str) -> dict:
    """
    Poll a single order's status. Returns {"status": "live"|"filled"|"cancelled",
    "fill_price": float}.
    """
    try:
        o = bc.get("/fapi/v1/order", params={"symbol": symbol, "orderId": order_id})
        if isinstance(o, dict):
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


# ── History / closed positions ───────────────────────────────────── #

def get_history_position(symbol: str,
                         open_time_iso: str | None = None,
                         entry_price:   float | None = None,
                         retries: int = 3,
                         retry_delay: float = 1.5) -> dict | None:
    """
    Total realized P&L for a closed position via /fapi/v1/income?incomeType=
    REALIZED_PNL. Binance returns ONE income row per close event (partial TP,
    trailing stop hit, final close, etc.) so position lifetime P&L is the SUM
    of all rows within the trade's time window.

    Returns {pnl, exit_price, close_time} or None.

    Note: `entry_price` is accepted for signature compatibility with the
    Bybit/Bitget implementations but is not used — Binance income rows don't
    expose avgEntryPrice. Callers narrow by `open_time_iso`'s startTime filter
    instead.
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
            if not isinstance(rows, list) or not rows:
                return None
            total_pnl = sum(float(r.get("income") or 0) for r in rows)
            if total_pnl == 0:
                if attempt < retries - 1:
                    _time.sleep(retry_delay)
                    continue
                logger.warning(
                    f"[Binance][{symbol}] get_history_position: 0 pnl after {retries} attempts ({len(rows)} rows)"
                )
                return None
            exit_price = None
            close_dt   = None
            try:
                trades = bc.get(
                    "/fapi/v1/userTrades",
                    params={"symbol": symbol, "limit": "20"},
                )
                if isinstance(trades, list) and trades:
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
    """Most recent closed position's realized PnL. Retries on API lag."""
    for attempt in range(retries):
        try:
            rows = bc.get(
                "/fapi/v1/income",
                params={"symbol": symbol, "incomeType": "REALIZED_PNL", "limit": "1"},
            )
            if isinstance(rows, list) and rows:
                pnl = float(rows[0].get("income") or 0)
                if pnl != 0:
                    return pnl
                if attempt < retries - 1:
                    _time.sleep(retry_delay)
        except Exception as e:
            logger.warning(f"[Binance][{symbol}] get_realized_pnl error: {e}")
            return None
    logger.warning(f"[Binance][{symbol}] get_realized_pnl: still 0 after {retries} attempts")
    return None
