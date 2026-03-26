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
            logger.debug(f"[{symbol}] Leverage set to {leverage}x ({hold_side})")
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


def _place_s2_exits(symbol: str, hold_side: str, qty_str: str,
                    sl_trig: float, sl_exec: float,
                    trail_trigger: float, trail_range: float) -> bool:
    """
    S2 exit orders placed at entry:
    1. SL at box_low (place-pos-tpsl loss_plan)
    2. Partial TP — sell 50% at trail_trigger (place-tpsl-order profit_plan)
    3. Trailing stop on remaining 50% (place-plan-order moving_plan)
    """
    import time as _t
    half_qty   = str(round(float(qty_str) / 2, 4))

    for attempt in range(3):
        try:
            # 1. SL on full position
            bc.post("/api/v2/mix/order/place-pos-tpsl", {
                "symbol":               symbol,
                "productType":          PRODUCT_TYPE,
                "marginCoin":           MARGIN_COIN,
                "holdSide":             hold_side,
                "stopLossTriggerPrice": str(sl_trig),
                "stopLossTriggerType":  "mark_price",
                "stopLossExecutePrice": str(sl_exec),
            })
            _t.sleep(0.5)

            # 2. Partial TP — sell 50% when trail_trigger hit
            bc.post("/api/v2/mix/order/place-tpsl-order", {
                "symbol":       symbol,
                "productType":  PRODUCT_TYPE,
                "marginCoin":   MARGIN_COIN,
                "planType":     "profit_plan",
                "triggerPrice": str(trail_trigger),
                "triggerType":  "mark_price",
                "executePrice": "0",
                "holdSide":     hold_side,
                "size":         half_qty,
            })
            _t.sleep(0.5)

            # 3. Trailing stop on remaining 50%
            bc.post("/api/v2/mix/order/place-tpsl-order", {
                "symbol":       symbol,
                "productType":  PRODUCT_TYPE,
                "marginCoin":   MARGIN_COIN,
                "planType":     "moving_plan",
                "triggerPrice": str(trail_trigger),
                "triggerType":  "mark_price",
                "holdSide":     hold_side,
                "size":         half_qty,
                "rangeRate":    str(round(trail_range / 100, 4)),  # API expects decimal: 10% → "0.1"
            })
            return True
        except Exception as e:
            logger.warning(f"[{symbol}] S2 exits attempt {attempt+1}/3: {e}")
            if attempt < 2:
                _t.sleep(1.5)
    return False

def open_long(
    symbol: str,
    box_low: float         = 0,
    sl_floor: float        = 0,
    leverage: int          = LEVERAGE,
    trade_size_pct: float  = TRADE_SIZE_PCT,
    take_profit_pct: float = TAKE_PROFIT_PCT,
    stop_loss_pct: float   = STOP_LOSS_PCT,
    use_s2_exits: bool     = False,
) -> dict:
    """
    Opens a LONG position.
    SL = mark * (1 - stop_loss_pct)  default
    S2/S3: partial TP at +100% margin + trailing stop on remaining 50%
      S2 uses box_low for SL; S3 passes sl_floor (pre-computed pivot SL)
    """
    import time as _t
    balance  = get_usdt_balance()
    mark     = get_mark_price(symbol)
    notional = balance * trade_size_pct * leverage
    qty      = _round_qty(notional / mark, symbol)

    tp_trig  = float(_round_price(mark * (1 + take_profit_pct), symbol))
    tp_exec  = float(_round_price(tp_trig * 1.005, symbol))
    sl_trig  = float(_round_price(mark * (1 - stop_loss_pct), symbol))
    sl_exec  = float(_round_price(sl_trig * 0.995, symbol))

    set_leverage(symbol, leverage)

    bc.post("/api/v2/mix/order/place-order", {
        "symbol": symbol, "productType": PRODUCT_TYPE,
        "marginMode": "isolated", "marginCoin": MARGIN_COIN,
        "size": qty, "side": "buy", "tradeSide": "open",
        "orderType": "market", "force": "ioc",
    })

    _t.sleep(2.0)

    if use_s2_exits:
        from config_s2 import S2_TRAILING_TRIGGER_PCT, S2_TRAILING_RANGE_PCT
        trail_trig = float(_round_price(mark * (1 + S2_TRAILING_TRIGGER_PCT), symbol))
        if sl_floor > 0:
            sl_s2_trig = float(_round_price(sl_floor, symbol))
        else:
            sl_s2_trig = float(_round_price(box_low * 0.999, symbol))
        sl_s2_exec = float(_round_price(sl_s2_trig * 0.995, symbol))
        ok = _place_s2_exits(symbol, "long", qty,
                             sl_s2_trig, sl_s2_exec,
                             trail_trig, S2_TRAILING_RANGE_PCT)
    else:
        if sl_floor > 0:
            sl_trig = float(_round_price(sl_floor, symbol))
            sl_exec = float(_round_price(sl_floor * 0.995, symbol))
        ok = _place_tpsl(symbol, "long", tp_trig, tp_exec, sl_trig, sl_exec)

    if not ok:
        logger.error(f"[{symbol}] ⚠️  TP/SL failed! Set manually: SL={sl_trig}")

    result = {
        "symbol": symbol, "side": "LONG", "qty": qty,
        "entry": mark, "sl": sl_trig, "tp": tp_trig,
        "box_low": box_low, "leverage": leverage,
        "margin": round(balance * trade_size_pct, 4), "tpsl_set": ok,
    }
    logger.info(
        f"[{symbol}] 🟢 LONG {leverage}x | qty={qty} entry≈{mark:.5f} "
        f"SL={sl_trig} | {'✅ S2 exits' if use_s2_exits else 'TP='+str(tp_trig)} | {'✅' if ok else '❌ SET MANUALLY'}"
    )
    return result


def open_short(
    symbol: str,
    box_high: float        = 0,
    sl_floor: float        = 0,
    leverage: int          = LEVERAGE,
    trade_size_pct: float  = TRADE_SIZE_PCT,
    take_profit_pct: float = TAKE_PROFIT_PCT,
) -> dict:
    """
    Opens a SHORT position.
    SL = sl_floor if provided, else box_high * 1.001 (just above the consolidation box)
    TP = entry * (1 - take_profit_pct)
    """
    import time as _t
    balance  = get_usdt_balance()
    mark     = get_mark_price(symbol)
    notional = balance * trade_size_pct * leverage
    qty      = _round_qty(notional / mark, symbol)

    tp_trig  = float(_round_price(mark * (1 - take_profit_pct), symbol))
    tp_exec  = float(_round_price(tp_trig * 0.995, symbol))
    if sl_floor > 0:
        sl_trig = float(_round_price(sl_floor, symbol))
    else:
        sl_trig = float(_round_price(box_high * 1.001, symbol))
    sl_exec  = float(_round_price(sl_trig * 1.005, symbol))

    set_leverage(symbol, leverage)

    bc.post("/api/v2/mix/order/place-order", {
        "symbol": symbol, "productType": PRODUCT_TYPE,
        "marginMode": "isolated", "marginCoin": MARGIN_COIN,
        "size": qty, "side": "sell", "tradeSide": "open",
        "orderType": "market", "force": "ioc",
    })

    _t.sleep(2.0)

    ok = _place_tpsl(symbol, "short", tp_trig, tp_exec, sl_trig, sl_exec)
    if not ok:
        logger.error(f"[{symbol}] ⚠️  TP/SL failed! Set manually: SL={sl_trig} TP={tp_trig}")

    result = {
        "symbol": symbol, "side": "SHORT", "qty": qty,
        "entry": mark, "sl": sl_trig, "tp": tp_trig,
        "box_high": box_high, "leverage": leverage,
        "margin": round(balance * trade_size_pct, 4), "tpsl_set": ok,
    }
    logger.info(
        f"[{symbol}] 🔴 SHORT {leverage}x | qty={qty} entry≈{mark:.5f} "
        f"SL={sl_trig} TP={tp_trig} | {'✅' if ok else '❌ SET MANUALLY'}"
    )
    return result


def cancel_all_orders(symbol: str):
    try:
        bc.post("/api/v2/mix/order/cancel-all-orders", {
            "symbol": symbol, "productType": PRODUCT_TYPE, "marginCoin": MARGIN_COIN,
        })
    except Exception as e:
        logger.warning(f"[{symbol}] cancel orders warn: {e}")