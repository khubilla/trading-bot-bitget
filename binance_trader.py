"""
binance_trader.py — Binance USDT-M Futures high-level trader.

Mirrors the public surface of trader.py so binance_bot.py can be a structural
clone of bot.py. open_long() / open_short() honour DRY_RUN from config_binance.

When `config_binance.DRY_RUN` is True, open_long/open_short LOG the intended
order and return a simulated fill dict without hitting /fapi/v1/order.

Strategy modules are reused unchanged. binance_bot.py installs sys.modules
aliases at startup (`config_s1` → `config_binance_s1`, `bitget` → `binance`,
`trader` → `binance_trader`) so when strategies do `from config_s1 import X`
or `import bitget as bg` they transparently get Binance equivalents.
"""

import logging
import time as _t
import pandas as pd

import binance as bn
from config_binance import DRY_RUN
from config_binance_s1 import (
    LEVERAGE, TRADE_SIZE_PCT, TAKE_PROFIT_PCT, STOP_LOSS_PCT,
)

logger = logging.getLogger(__name__)


# ── Symbol info / rounding — delegate to binance module ─────────── #

def _sym_info(symbol: str) -> dict:
    return bn.sym_info(symbol)


def _round_price(price: float, symbol: str) -> str:
    return bn.round_price(price, symbol)


def _round_qty(qty: float, symbol: str, mark_price: float | None = None) -> str:
    return bn.round_qty(qty, symbol, mark_price=mark_price)


# ── Market data passthroughs ────────────────────────────────────── #

def get_candles(symbol: str, interval: str, limit: int = 100) -> pd.DataFrame:
    return bn.get_candles(symbol, interval, limit)


def get_mark_price(symbol: str) -> float:
    return bn.get_mark_price(symbol)


def get_funding_rate(symbol: str) -> float | None:
    return bn.get_funding_rate(symbol)


# ── Account passthroughs ────────────────────────────────────────── #

def get_usdt_balance() -> float:
    return bn.get_usdt_balance()


def _get_total_equity() -> float:
    return bn.get_total_equity()


def get_all_open_positions() -> dict[str, dict]:
    return bn.get_all_open_positions()


def get_realized_pnl(symbol: str, retries: int = 3, retry_delay: float = 1.5) -> float | None:
    return bn.get_realized_pnl(symbol, retries=retries, retry_delay=retry_delay)


def get_history_position(symbol: str,
                         open_time_iso: str | None = None,
                         entry_price:   float | None = None,
                         retries: int = 3,
                         retry_delay: float = 1.5) -> dict | None:
    return bn.get_history_position(symbol, open_time_iso, entry_price, retries, retry_delay)


# ── Leverage ────────────────────────────────────────────────────── #

def set_leverage(symbol: str, leverage: int):
    if DRY_RUN:
        logger.info(f"[Binance][DRY_RUN][{symbol}] set_leverage({leverage}x) — skipped")
        return
    bn.set_leverage(symbol, leverage)


# ── TP/SL placement (delegated) ─────────────────────────────────── #

def _place_tpsl(symbol: str, hold_side: str,
                tp_trig: float, tp_exec: float,
                sl_trig: float, sl_exec: float) -> bool:
    if DRY_RUN:
        logger.info(f"[Binance][DRY_RUN][{symbol}] place TP/SL hold={hold_side} "
                    f"TP={tp_trig:.5f} SL={sl_trig:.5f}")
        return True
    return bn.place_pos_tpsl_full(symbol, hold_side, tp_trig, tp_exec, sl_trig, sl_exec)


def update_position_sl(symbol: str, new_sl: float, hold_side: str = "long") -> bool:
    if DRY_RUN:
        logger.info(f"[Binance][DRY_RUN][{symbol}] update_position_sl hold={hold_side} → {new_sl:.5f}")
        return True
    return bn.update_position_sl(symbol, new_sl, hold_side)


def cancel_all_orders(symbol: str):
    if DRY_RUN:
        logger.info(f"[Binance][DRY_RUN][{symbol}] cancel_all_orders — skipped")
        return
    bn.cancel_all_orders(symbol)


def refresh_plan_exits(symbol: str, hold_side: str, new_trail_trigger: float = 0,
                       sl_price: float = 0) -> bool:
    """Resize partial-TP + trailing stop after scale-in. Delegates to binance.refresh_plan_exits.

    sl_price: accepted for signature parity with bybit_trader.refresh_plan_exits
    (alias swap requires matching signatures). Binance SL is a standalone
    STOP_MARKET order, so it is forwarded but ignored downstream.
    """
    if DRY_RUN:
        logger.info(f"[Binance][DRY_RUN][{symbol}] refresh_plan_exits hold={hold_side} "
                    f"trigger={new_trail_trigger} sl_price={sl_price}")
        return True
    return bn.refresh_plan_exits(symbol, hold_side, new_trail_trigger, sl_price=sl_price)


# ── Strategy exit dispatchers ───────────────────────────────────── #
#
# Called by open_long / open_short after the entry market order fills.
# Strategy modules import `bitget`/`trader` internally; the sys.modules
# aliasing installed by binance_bot.py at startup redirects those imports
# to `binance` / `binance_trader`, so the strategy code runs unchanged.

def _place_s1_exits(symbol, hold_side, qty_str, sl_trig, sl_exec, trail_trigger, trail_range):
    from strategies.s1 import _place_exits
    return _place_exits(symbol, hold_side, qty_str, sl_trig, sl_exec,
                        trail_trigger, trail_range)


def _place_s2_exits(symbol, hold_side, qty_str, sl_trig, sl_exec, trail_trigger, trail_range):
    from strategies.s2 import _place_partial_trail_exits
    return _place_partial_trail_exits(symbol, hold_side, qty_str, sl_trig, sl_exec,
                                       trail_trigger, trail_range)


def _place_s5_exits(symbol, hold_side, qty_str, sl_trig, sl_exec,
                    partial_trig, tp_target, trail_range_pct):
    from strategies.s5 import _place_exits
    return _place_exits(symbol, hold_side, qty_str, sl_trig, sl_exec,
                        partial_trig, tp_target, trail_range_pct)


# ── Open long / short ───────────────────────────────────────────── #

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
    use_s7_exits: bool     = False,
    strategy: str | None   = None,
    tp_price_abs: float    = 0,
) -> dict:
    if strategy == "S1": use_s1_exits = True
    elif strategy == "S2": use_s2_exits = True
    elif strategy == "S3": use_s3_exits = True
    elif strategy == "S5": use_s5_exits = True
    elif strategy == "S7": use_s7_exits = True

    balance  = get_usdt_balance()
    equity   = _get_total_equity() or balance
    mark     = get_mark_price(symbol)
    notional = equity * trade_size_pct * leverage
    qty      = _round_qty(notional / mark, symbol, mark_price=mark)

    if sl_floor > 0:
        sl_trig_preset = float(_round_price(sl_floor, symbol))
    else:
        sl_trig_preset = float(_round_price(mark * (1 - stop_loss_pct), symbol))
    sl_exec_preset = float(_round_price(sl_trig_preset * 0.995, symbol))

    set_leverage(symbol, leverage)

    logger.info(f"[Binance][{symbol}] 📤 Market BUY: qty={qty} @ mark≈{mark:.5f} "
                f"| preset SL={sl_trig_preset:.5f}")

    if DRY_RUN:
        logger.info(f"[Binance][DRY_RUN][{symbol}] open_long would call /fapi/v1/order")
        fill = mark
    else:
        bn.place_market_order(symbol, "buy", qty, sl_trigger=sl_trig_preset)
        _t.sleep(2.0)
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
    elif use_s7_exits:
        from strategies.s7 import compute_and_place_long_exits as _s7_long_exits
        ok, sl_trig, tp_trig = _s7_long_exits(symbol, qty, fill, sl_floor, 0)
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
        logger.error(f"[Binance][{symbol}] ⚠️  TP/SL failed! Set manually: SL={sl_trig}")

    result = {
        "symbol": symbol, "side": "LONG", "qty": qty,
        "entry": fill, "sl": sl_trig, "tp": tp_trig,
        "box_low": box_low, "leverage": leverage,
        "margin": round(equity * trade_size_pct, 4), "tpsl_set": ok,
    }
    logger.info(
        f"[Binance][{symbol}] 🟢 LONG {leverage}x | qty={qty} entry≈{fill:.5f} "
        f"SL={sl_trig} TP={tp_trig} | {'✅' if ok else '❌ SET MANUALLY'}"
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
    use_s7_exits: bool     = False,
    strategy: str | None   = None,
    tp_price_abs: float    = 0,
) -> dict:
    if strategy == "S1": use_s1_exits = True
    elif strategy == "S4": use_s4_exits = True
    elif strategy == "S5": use_s5_exits = True
    elif strategy == "S6": use_s6_exits = True
    elif strategy == "S7": use_s7_exits = True

    balance  = get_usdt_balance()
    equity   = _get_total_equity() or balance
    mark     = get_mark_price(symbol)
    notional = equity * trade_size_pct * leverage
    qty      = _round_qty(notional / mark, symbol, mark_price=mark)

    if sl_floor > 0:
        sl_trig = float(_round_price(sl_floor, symbol))
    else:
        sl_trig = float(_round_price(box_high * 1.001, symbol))
    sl_exec = float(_round_price(sl_trig * 1.005, symbol))

    set_leverage(symbol, leverage)

    logger.info(f"[Binance][{symbol}] 📤 Market SELL: qty={qty} @ mark≈{mark:.5f} "
                f"| preset SL={sl_trig:.5f}")

    if DRY_RUN:
        logger.info(f"[Binance][DRY_RUN][{symbol}] open_short would call /fapi/v1/order")
        fill = mark
    else:
        bn.place_market_order(symbol, "sell", qty, sl_trigger=sl_trig)
        _t.sleep(2.0)
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
    elif use_s7_exits:
        from strategies.s7 import compute_and_place_short_exits as _s7_short_exits
        ok, sl_trig, tp_trig = _s7_short_exits(symbol, qty, fill, sl_trig, sl_exec)
    else:
        tp_trig = float(_round_price(fill * (1 - take_profit_pct), symbol))
        tp_exec = float(_round_price(tp_trig * 0.995, symbol))
        ok = _place_tpsl(symbol, "short", tp_trig, tp_exec, sl_trig, sl_exec)

    if not ok:
        logger.error(f"[Binance][{symbol}] ⚠️  TP/SL failed! Set manually: SL={sl_trig} TP={tp_trig}")

    result = {
        "symbol": symbol, "side": "SHORT", "qty": qty,
        "entry": fill, "sl": sl_trig, "tp": tp_trig,
        "box_high": box_high, "leverage": leverage,
        "margin": round(equity * trade_size_pct, 4), "tpsl_set": ok,
    }
    logger.info(
        f"[Binance][{symbol}] 🔴 SHORT {leverage}x | qty={qty} entry≈{fill:.5f} "
        f"SL={sl_trig} TP={tp_trig} | {'✅' if ok else '❌ SET MANUALLY'}"
    )
    return result


# ── Scale-ins ───────────────────────────────────────────────────── #

def scale_in_long(symbol: str, additional_trade_size_pct: float, leverage: int) -> None:
    equity = _get_total_equity() or get_usdt_balance()
    mark   = get_mark_price(symbol)
    qty    = _round_qty((equity * additional_trade_size_pct * leverage) / mark, symbol,
                        mark_price=mark)
    if DRY_RUN:
        logger.info(f"[Binance][DRY_RUN][{symbol}] ➕ scale_in_long qty={qty}")
        return
    bn.place_market_order(symbol, "buy", qty)
    logger.info(f"[Binance][{symbol}] ➕ Scale-in LONG qty={qty} @ mark≈{mark:.5f}")


def scale_in_short(symbol: str, additional_trade_size_pct: float, leverage: int) -> None:
    equity = _get_total_equity() or get_usdt_balance()
    mark   = get_mark_price(symbol)
    qty    = _round_qty((equity * additional_trade_size_pct * leverage) / mark, symbol,
                        mark_price=mark)
    if DRY_RUN:
        logger.info(f"[Binance][DRY_RUN][{symbol}] ➕ scale_in_short qty={qty}")
        return
    bn.place_market_order(symbol, "sell", qty)
    logger.info(f"[Binance][{symbol}] ➕ Scale-in SHORT qty={qty} @ mark≈{mark:.5f}")


# ── Limit / plan orders for S5 ──────────────────────────────────── #

def place_limit_long(symbol: str, limit_price: float, sl_price: float,
                     tp_price: float, qty_str: str) -> str:
    if DRY_RUN:
        fake_id = f"DRY-LONG-{symbol}-{int(_t.time())}"
        logger.info(f"[Binance][DRY_RUN][{symbol}] place_limit_long → {fake_id}")
        return fake_id
    return bn.place_plan_order("buy", symbol, limit_price, sl_price, tp_price, qty_str)


def place_limit_short(symbol: str, limit_price: float, sl_price: float,
                      tp_price: float, qty_str: str) -> str:
    if DRY_RUN:
        fake_id = f"DRY-SHORT-{symbol}-{int(_t.time())}"
        logger.info(f"[Binance][DRY_RUN][{symbol}] place_limit_short → {fake_id}")
        return fake_id
    return bn.place_plan_order("sell", symbol, limit_price, sl_price, tp_price, qty_str)


def cancel_order(symbol: str, order_id: str) -> None:
    if DRY_RUN:
        logger.info(f"[Binance][DRY_RUN][{symbol}] cancel_order {order_id}")
        return
    bn.cancel_plan_order(symbol, order_id)


def get_order_fill(symbol: str, order_id: str) -> dict:
    if DRY_RUN and str(order_id).startswith("DRY-"):
        return {"status": "live", "fill_price": 0.0}
    return bn.get_order_fill(symbol, order_id)


def is_partial_closed(symbol: str) -> bool:
    """Mirror trader.is_partial_closed — live mode tracks via state, always False here."""
    return False
