"""
trader.py — Bitget USDT Futures API Wrapper

Endpoints used (Bitget API v2):
  Market data  : GET  /api/v2/mix/market/candles
  Tickers      : GET  /api/v2/mix/market/tickers
  Mark price   : GET  /api/v2/mix/market/symbol-price
  Contract info: GET  /api/v2/mix/market/contracts
  Balance      : GET  /api/v2/mix/account/accounts
  Positions    : GET  /api/v2/mix/position/all-position
  Set leverage : POST /api/v2/mix/account/set-leverage
  Place order  : POST /api/v2/mix/order/place-order
  Place TP/SL  : POST /api/v2/mix/order/place-pos-tpsl
  Cancel orders: POST /api/v2/mix/order/cancel-all-orders
"""

import math
import logging
import pandas as pd
import bitget_client as bc
from config import (
    PRODUCT_TYPE, MARGIN_COIN, LEVERAGE,
    TRADE_SIZE_PCT, STOP_LOSS_PCT, TAKE_PROFIT_PCT,
    HTF_INTERVAL, LTF_INTERVAL,
)

logger = logging.getLogger(__name__)

# Symbol info cache: { symbol: {price_place, volume_place, min_trade_num, size_multiplier} }
_sym_cache: dict[str, dict] = {}


# ── Symbol Info ───────────────────────────────────────────────────── #

def _load_symbol_cache():
    """Loads contract specs for all USDT-FUTURES symbols into cache."""
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
    # Round DOWN to nearest valid size step
    qty  = math.floor(qty / mult) * mult
    qty  = max(qty, info["min_trade_num"])
    vp   = info["volume_place"]
    return str(round(qty, vp))


# ── Market Data ───────────────────────────────────────────────────── #

def get_candles(symbol: str, interval: str, limit: int = 100) -> pd.DataFrame:
    """
    Fetches OHLCV candles from Bitget.
    Bitget candle response: [ts, open, high, low, close, vol, quoteVol]
    """
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
    # Always sort ascending by timestamp — Bitget order varies by symbol
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
    """Returns available USDT balance in the USDT-FUTURES wallet."""
    data = bc.get("/api/v2/mix/account/accounts", params={"productType": PRODUCT_TYPE})
    for acct in data.get("data", []):
        if acct.get("marginCoin") == MARGIN_COIN:
            return float(acct.get("available", 0))
    return 0.0


def get_all_open_positions() -> dict[str, dict]:
    """
    Returns { symbol: position_info } for all open positions.
    Bitget position fields: symbol, holdSide (long/short), total (size),
    openPriceAvg, unrealizedPL
    """
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
    """Sets leverage for both LONG and SHORT sides (Bitget requires separate calls)."""
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
    """5% of balance × leverage ÷ mark price, rounded to exchange precision."""
    notional = balance * TRADE_SIZE_PCT * LEVERAGE
    qty      = notional / mark_price
    return _round_qty(qty, symbol)


def open_long(symbol: str) -> dict:
    """
    Opens a LONG position with market order, then places TP/SL.
    Execute prices must be real values (not 0) — Bitget rejects 0.
    Long SL execute = slightly below trigger (slippage buffer)
    Long TP execute = slightly above trigger (ensure fill)
    """
    import time as _time
    balance = get_usdt_balance()
    mark    = get_mark_price(symbol)
    qty     = _calculate_qty(symbol, mark, balance)
    sl_trig = float(_round_price(mark * (1 - STOP_LOSS_PCT),   symbol))
    tp_trig = float(_round_price(mark * (1 + TAKE_PROFIT_PCT), symbol))
    # Execute prices: SL slightly below trigger, TP slightly above trigger
    sl_exec = float(_round_price(sl_trig * 0.995, symbol))  # 0.5% slippage buffer
    tp_exec = float(_round_price(tp_trig * 1.005, symbol))

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

    # 2. Wait for position to register on Bitget's side before placing TP/SL
    _time.sleep(2.0)

    # 3. Place TP/SL with retry
    tpsl_placed = False
    for attempt in range(3):
        try:
            bc.post("/api/v2/mix/order/place-pos-tpsl", {
                "symbol":                  symbol,
                "productType":             PRODUCT_TYPE,
                "marginCoin":              MARGIN_COIN,
                "holdSide":                "long",
                "stopSurplusTriggerPrice": str(tp_trig),
                "stopSurplusTriggerType":  "mark_price",
                "stopSurplusExecutePrice": str(tp_exec),
                "stopLossTriggerPrice":    str(sl_trig),
                "stopLossTriggerType":     "mark_price",
                "stopLossExecutePrice":    str(sl_exec),
            })
            tpsl_placed = True
            break
        except Exception as e:
            logger.warning(f"[{symbol}] TP/SL attempt {attempt + 1}/3 failed: {e}")
            if attempt < 2:
                _time.sleep(1.5)

    if not tpsl_placed:
        logger.error(f"[{symbol}] ⚠️  COULD NOT SET TP/SL AFTER 3 ATTEMPTS — SET MANUALLY ON BITGET!")

    result = {
        "symbol": symbol, "side": "LONG", "qty": qty,
        "entry": mark, "sl": sl_trig, "tp": tp_trig,
        "margin": round(balance * TRADE_SIZE_PCT, 4),
        "tpsl_set": tpsl_placed,
    }
    logger.info(
        f"[{symbol}] 🟢 LONG opened | qty={qty} entry≈{mark} "
        f"SL={sl_trig} TP={tp_trig} tpsl={'✅' if tpsl_placed else '❌ MANUAL NEEDED'}"
    )
    return result


def open_short(symbol: str) -> dict:
    """
    Opens a SHORT position with market order, then places TP/SL.
    Short SL execute = slightly above trigger (slippage buffer)
    Short TP execute = slightly below trigger (ensure fill)
    """
    import time as _time
    balance = get_usdt_balance()
    mark    = get_mark_price(symbol)
    qty     = _calculate_qty(symbol, mark, balance)
    sl_trig = float(_round_price(mark * (1 + STOP_LOSS_PCT),   symbol))
    tp_trig = float(_round_price(mark * (1 - TAKE_PROFIT_PCT), symbol))
    sl_exec = float(_round_price(sl_trig * 1.005, symbol))  # 0.5% slippage buffer
    tp_exec = float(_round_price(tp_trig * 0.995, symbol))

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

    # 3. Place TP/SL with retry
    tpsl_placed = False
    for attempt in range(3):
        try:
            bc.post("/api/v2/mix/order/place-pos-tpsl", {
                "symbol":                  symbol,
                "productType":             PRODUCT_TYPE,
                "marginCoin":              MARGIN_COIN,
                "holdSide":                "short",
                "stopSurplusTriggerPrice": str(tp_trig),
                "stopSurplusTriggerType":  "mark_price",
                "stopSurplusExecutePrice": str(tp_exec),
                "stopLossTriggerPrice":    str(sl_trig),
                "stopLossTriggerType":     "mark_price",
                "stopLossExecutePrice":    str(sl_exec),
            })
            tpsl_placed = True
            break
        except Exception as e:
            logger.warning(f"[{symbol}] TP/SL attempt {attempt + 1}/3 failed: {e}")
            if attempt < 2:
                _time.sleep(1.5)

    if not tpsl_placed:
        logger.error(f"[{symbol}] ⚠️  COULD NOT SET TP/SL AFTER 3 ATTEMPTS — SET MANUALLY ON BITGET!")

    result = {
        "symbol": symbol, "side": "SHORT", "qty": qty,
        "entry": mark, "sl": sl_trig, "tp": tp_trig,
        "margin": round(balance * TRADE_SIZE_PCT, 4),
        "tpsl_set": tpsl_placed,
    }
    logger.info(
        f"[{symbol}] 🔴 SHORT opened | qty={qty} entry≈{mark} "
        f"SL={sl_trig} TP={tp_trig} tpsl={'✅' if tpsl_placed else '❌ MANUAL NEEDED'}"
    )
    return result


def cancel_all_orders(symbol: str):
    """Cancels all open plan (TP/SL) orders for a symbol."""
    try:
        bc.post("/api/v2/mix/order/cancel-all-orders", {
            "symbol":      symbol,
            "productType": PRODUCT_TYPE,
            "marginCoin":  MARGIN_COIN,
        })
    except Exception as e:
        logger.warning(f"[{symbol}] cancel_all_orders warn: {e}")
