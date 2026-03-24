"""
trader.py — Bitget USDT Futures API Wrapper

SL is placed as a real order on Bitget at:
  LONG:  box_low  (the bottom of the consolidation box)
  SHORT: box_high (the top of the consolidation box)

TP is placed at:
  LONG:  entry * (1 + TAKE_PROFIT_PCT)
  SHORT: entry * (1 - TAKE_PROFIT_PCT)
"""

import math
import logging
import pandas as pd
import bitget_client as bc
from config import (
    PRODUCT_TYPE, MARGIN_COIN, LEVERAGE,
    TRADE_SIZE_PCT, TAKE_PROFIT_PCT,
    HTF_INTERVAL, LTF_INTERVAL,
)

logger = logging.getLogger(__name__)

_sym_cache: dict[str, dict] = {}


# ── Symbol Info ───────────────────────────────────────────────────── #

def _load_symbol_cache():
    global _sym_cache
    if _sym_cache:
        return
    data = bc.get_public(
        "/api/v2/mix/market/contracts",
        params={"productType": PRODUCT_TYPE}
    )
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
    return _sym_cache.get(symbol, {
        "price_place": 2, "volume_place": 3,
        "size_mult": 0.001, "min_trade_num": 0.001
    })


def _round_price(price: float, symbol: str) -> str:
    pp = _sym_info(symbol)["price_place"]
    return str(round(price, pp))


def _round_qty(qty: float, symbol: str) -> str:
    info = _sym_info(symbol)
    mult = info["size_mult"]
    qty  = math.floor(qty / mult) * mult
    qty  = max(qty, info["min_trade_num"])
    vp   = info["volume_place"]
    return str(round(qty, vp))


# ── Market Data ───────────────────────────────────────────────────── #

def get_candles(symbol: str, interval: str, limit: int = 100) -> pd.DataFrame:
    data = bc.get_public(
        "/api/v2/mix/market/candles",
        params={
            "symbol":      symbol,
            "productType": PRODUCT_TYPE,
            "granularity": interval,
            "limit":       str(limit),
        }
    )
    rows = data.get("data", [])
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "vol", "quote_vol"])
    df[["open", "high", "low", "close", "vol"]] = \
        df[["open", "high", "low", "close", "vol"]].astype(float)
    df["ts"] = df["ts"].astype(int)
    df = df.sort_values("ts").reset_index(drop=True)
    return df


def get_mark_price(symbol: str) -> float:
    data = bc.get_public(
        "/api/v2/mix/market/symbol-price",
        params={"symbol": symbol, "productType": PRODUCT_TYPE}
    )
    return float(data["data"][0]["markPrice"])


# ── Account ───────────────────────────────────────────────────────── #

def get_usdt_balance() -> float:
    data = bc.get("/api/v2/mix/account/accounts", params={"productType": PRODUCT_TYPE})
    for acct in data.get("data", []):
        if acct.get("marginCoin") == MARGIN_COIN:
            return float(acct.get("available", 0))
    return 0.0


def get_all_open_positions() -> dict[str, dict]:
    data = bc.get(
        "/api/v2/mix/position/all-position",
        params={"productType": PRODUCT_TYPE, "marginCoin": MARGIN_COIN}
    )
    result = {}
    for p in data.get("data", []):
        total = float(p.get("total", 0))
        if total <= 0:
            continue
        side = p.get("holdSide", "long")
        result[p["symbol"]] = {
            "side":           side.upper(),
            "entry_price":    float(p.get("openPriceAvg", 0)),
            "qty":            total,
            "unrealised_pnl": float(p.get("unrealizedPL", 0)),
        }
    return result


# ── Leverage ──────────────────────────────────────────────────────── #

def set_leverage(symbol: str):
    for hold_side in ("long", "short"):
        try:
            bc.post("/api/v2/mix/account/set-leverage", {
                "symbol":      symbol,
                "productType": PRODUCT_TYPE,
                "marginCoin":  MARGIN_COIN,
                "leverage":    str(LEVERAGE),
                "holdSide":    hold_side,
            })
        except Exception as e:
            logger.warning(f"[{symbol}] set_leverage({hold_side}) warn: {e}")


# ── Order Placement ───────────────────────────────────────────────── #

def _calculate_qty(symbol: str, mark_price: float, balance: float) -> str:
    notional = balance * TRADE_SIZE_PCT * LEVERAGE
    qty      = notional / mark_price
    return _round_qty(qty, symbol)


def _place_tpsl(symbol: str, hold_side: str, tp_trig: float, tp_exec: float,
                sl_trig: float, sl_exec: float) -> bool:
    """Places TP and SL as position-level orders. Retries 3 times."""
    import time as _time
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
            logger.warning(f"[{symbol}] TP/SL attempt {attempt + 1}/3: {e}")
            if attempt < 2:
                _time.sleep(1.5)
    return False


def open_long(symbol: str, box_low: float) -> dict:
    """
    Opens a LONG position.
    SL = box_low (bottom of consolidation box, with small buffer)
    TP = entry * (1 + TAKE_PROFIT_PCT)
    """
    import time as _time
    balance = get_usdt_balance()
    mark    = get_mark_price(symbol)
    qty     = _calculate_qty(symbol, mark, balance)

    # TP above entry
    tp_trig = float(_round_price(mark * (1 + TAKE_PROFIT_PCT), symbol))
    tp_exec = float(_round_price(tp_trig * 1.005, symbol))

    # SL = box_low with 0.1% buffer below (to avoid noise triggers right at the line)
    sl_trig = float(_round_price(box_low * 0.999, symbol))
    sl_exec = float(_round_price(sl_trig * 0.995, symbol))  # market fill buffer

    set_leverage(symbol)

    # 1. Market buy
    bc.post("/api/v2/mix/order/place-order", {
        "symbol":      symbol,
        "productType": PRODUCT_TYPE,
        "marginMode":  "isolated",
        "marginCoin":  MARGIN_COIN,
        "size":        qty,
        "side":        "buy",
        "tradeSide":   "open",
        "orderType":   "market",
        "force":       "ioc",
    })

    # 2. Wait for position to register on Bitget's side
    _time.sleep(2.0)

    # 3. Place TP + SL
    tpsl_ok = _place_tpsl(symbol, "long", tp_trig, tp_exec, sl_trig, sl_exec)
    if not tpsl_ok:
        logger.error(f"[{symbol}] ⚠️  TP/SL failed — SET MANUALLY on Bitget! SL={sl_trig} TP={tp_trig}")

    result = {
        "symbol": symbol, "side": "LONG", "qty": qty,
        "entry": mark, "sl": sl_trig, "tp": tp_trig,
        "box_low": box_low,
        "margin": round(balance * TRADE_SIZE_PCT, 4),
        "tpsl_set": tpsl_ok,
    }
    logger.info(
        f"[{symbol}] 🟢 LONG | qty={qty} entry≈{mark} "
        f"SL={sl_trig} (box_low={box_low}) TP={tp_trig} | "
        f"tpsl={'✅' if tpsl_ok else '❌ SET MANUALLY'}"
    )
    return result


def open_short(symbol: str, box_high: float) -> dict:
    """
    Opens a SHORT position.
    SL = box_high (top of consolidation box, with small buffer)
    TP = entry * (1 - TAKE_PROFIT_PCT)
    """
    import time as _time
    balance = get_usdt_balance()
    mark    = get_mark_price(symbol)
    qty     = _calculate_qty(symbol, mark, balance)

    # TP below entry
    tp_trig = float(_round_price(mark * (1 - TAKE_PROFIT_PCT), symbol))
    tp_exec = float(_round_price(tp_trig * 0.995, symbol))

    # SL = box_high with 0.1% buffer above
    sl_trig = float(_round_price(box_high * 1.001, symbol))
    sl_exec = float(_round_price(sl_trig * 1.005, symbol))

    set_leverage(symbol)

    # 1. Market sell
    bc.post("/api/v2/mix/order/place-order", {
        "symbol":      symbol,
        "productType": PRODUCT_TYPE,
        "marginMode":  "isolated",
        "marginCoin":  MARGIN_COIN,
        "size":        qty,
        "side":        "sell",
        "tradeSide":   "open",
        "orderType":   "market",
        "force":       "ioc",
    })

    # 2. Wait for position to register
    _time.sleep(2.0)

    # 3. Place TP + SL
    tpsl_ok = _place_tpsl(symbol, "short", tp_trig, tp_exec, sl_trig, sl_exec)
    if not tpsl_ok:
        logger.error(f"[{symbol}] ⚠️  TP/SL failed — SET MANUALLY on Bitget! SL={sl_trig} TP={tp_trig}")

    result = {
        "symbol": symbol, "side": "SHORT", "qty": qty,
        "entry": mark, "sl": sl_trig, "tp": tp_trig,
        "box_high": box_high,
        "margin": round(balance * TRADE_SIZE_PCT, 4),
        "tpsl_set": tpsl_ok,
    }
    logger.info(
        f"[{symbol}] 🔴 SHORT | qty={qty} entry≈{mark} "
        f"SL={sl_trig} (box_high={box_high}) TP={tp_trig} | "
        f"tpsl={'✅' if tpsl_ok else '❌ SET MANUALLY'}"
    )
    return result


def cancel_all_orders(symbol: str):
    try:
        bc.post("/api/v2/mix/order/cancel-all-orders", {
            "symbol":      symbol,
            "productType": PRODUCT_TYPE,
            "marginCoin":  MARGIN_COIN,
        })
    except Exception as e:
        logger.warning(f"[{symbol}] cancel_all_orders warn: {e}")
