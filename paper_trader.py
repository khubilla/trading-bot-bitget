"""
paper_trader.py — Paper Trading Simulator

Mirrors the trader.py public interface exactly.
Real market data (candles, prices) fetched from Bitget.
Order execution is simulated locally in paper_state.json.

Usage:
    python bot.py --paper
"""

import json, logging
from datetime import datetime, timezone
from pathlib import Path

# ── Real market data (read-only) ─────────────────────────────────── #
from trader import get_candles, get_mark_price  # noqa: F401  (re-exported)

logger = logging.getLogger(__name__)

PAPER_STATE_FILE    = "paper_state.json"
PAPER_START_BALANCE = 1000.0   # starting paper USDT balance


# ── State I/O ────────────────────────────────────────────────────── #

def _load() -> dict:
    p = Path(PAPER_STATE_FILE)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {
        "balance":    PAPER_START_BALANCE,
        "positions":  {},
        "history":    [],
        "total_pnl":  0.0,
    }


def _save(state: dict):
    Path(PAPER_STATE_FILE).write_text(json.dumps(state, indent=2, default=str))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Public interface ─────────────────────────────────────────────── #

def get_usdt_balance() -> float:
    return _load()["balance"]


def set_leverage(symbol: str, leverage: int):
    pass  # no-op for paper trading


def tag_strategy(symbol: str, strategy: str):
    """Store the strategy name in paper_state so it survives restarts."""
    state = _load()
    if symbol in state["positions"]:
        state["positions"][symbol]["strategy"] = strategy
        _save(state)


def get_all_open_positions() -> dict:
    """
    Returns open paper positions in the same format as trader.py.
    Also checks each position for SL / TP / trailing stop before returning.
    """
    state = _load()
    for sym in list(state["positions"].keys()):
        try:
            mark = get_mark_price(sym)
            _check_exit(state, sym, mark)
        except Exception as e:
            logger.warning(f"[PAPER] Price check failed for {sym}: {e}")
    _save(state)

    result = {}
    for sym, pos in state["positions"].items():
        mark   = pos.get("last_mark", pos["entry"])
        entry  = pos["entry"]
        qty    = pos["qty"]
        side   = pos["side"]
        upnl   = (mark - entry) * qty if side == "LONG" else (entry - mark) * qty
        result[sym] = {
            "side":           side,
            "qty":            qty,
            "entry_price":    entry,
            "unrealised_pnl": round(upnl, 6),
            # Extra fields for dashboard resume (paper only)
            "sl":       pos.get("sl"),
            "tp":       pos.get("tp"),
            "margin":   pos.get("margin"),
            "leverage": pos.get("leverage"),
            "strategy": pos.get("strategy", "UNKNOWN"),
        }
    return result


def open_long(
    symbol: str,
    box_low: float         = 0,
    sl_floor: float        = 0,
    leverage: int          = 10,
    trade_size_pct: float  = 0.25,
    take_profit_pct: float = 0.05,
    stop_loss_pct: float   = 0.015,
    use_s2_exits: bool     = False,
) -> dict:
    state  = _load()
    mark   = get_mark_price(symbol)
    margin = state["balance"] * trade_size_pct
    qty    = (margin * leverage) / mark

    sl_price = sl_floor if sl_floor > 0 else mark * (1 - stop_loss_pct)

    use_trailing  = False
    trail_trigger = None
    trail_range   = None
    tp_price      = mark * (1 + take_profit_pct)

    if use_s2_exits:
        from config_s2 import S2_TRAILING_TRIGGER_PCT, S2_TRAILING_RANGE_PCT
        trail_trigger = mark * (1 + S2_TRAILING_TRIGGER_PCT)
        trail_range   = S2_TRAILING_RANGE_PCT   # stored as int e.g. 10 = 10%
        tp_price      = trail_trigger
        use_trailing  = True

    state["balance"] -= margin
    state["positions"][symbol] = {
        "symbol":          symbol,
        "side":            "LONG",
        "entry":           mark,
        "qty":             qty,
        "original_qty":    qty,
        "margin":          margin,
        "original_margin": margin,
        "leverage":        leverage,
        "sl":              sl_price,
        "tp":              tp_price,
        "use_trailing":    use_trailing,
        "trail_trigger":   trail_trigger,
        "trail_range":     trail_range,
        "trail_peak":      mark,
        "trail_active":    False,
        "partial_closed":  False,
        "last_mark":       mark,
        "opened_at":       _now(),
    }
    _save(state)
    logger.info(
        f"[PAPER][{symbol}] 🟢 LONG {leverage}x | entry={mark:.5f} | "
        f"SL={sl_price:.5f} | "
        f"{'trailing @+{:.0f}%'.format((trail_trigger/mark-1)*100) if use_trailing else 'TP={:.5f}'.format(tp_price)} | "
        f"margin=${margin:.2f}"
    )
    return {
        "symbol": symbol, "side": "LONG", "qty": round(qty, 4),
        "entry": mark, "sl": sl_price, "tp": tp_price,
        "leverage": leverage, "margin": round(margin, 4), "tpsl_set": True,
    }


def open_short(
    symbol: str,
    box_high: float        = 0,
    sl_floor: float        = 0,
    leverage: int          = 10,
    trade_size_pct: float  = 0.25,
    take_profit_pct: float = 0.05,
) -> dict:
    state  = _load()
    mark   = get_mark_price(symbol)
    margin = state["balance"] * trade_size_pct
    qty    = (margin * leverage) / mark

    if sl_floor > 0:
        sl_price = sl_floor
    elif box_high > 0:
        sl_price = box_high * 1.001
    else:
        sl_price = mark * 1.015

    tp_price = mark * (1 - take_profit_pct)

    state["balance"] -= margin
    state["positions"][symbol] = {
        "symbol":          symbol,
        "side":            "SHORT",
        "entry":           mark,
        "qty":             qty,
        "original_qty":    qty,
        "margin":          margin,
        "original_margin": margin,
        "leverage":        leverage,
        "sl":              sl_price,
        "tp":              tp_price,
        "use_trailing":    False,
        "trail_trigger":   None,
        "trail_range":     None,
        "trail_peak":      mark,
        "trail_active":    False,
        "partial_closed":  False,
        "last_mark":       mark,
        "opened_at":       _now(),
    }
    _save(state)
    logger.info(
        f"[PAPER][{symbol}] 🔴 SHORT {leverage}x | entry={mark:.5f} | "
        f"SL={sl_price:.5f} | TP={tp_price:.5f} | margin=${margin:.2f}"
    )
    return {
        "symbol": symbol, "side": "SHORT", "qty": round(qty, 4),
        "entry": mark, "sl": sl_price, "tp": tp_price,
        "leverage": leverage, "margin": round(margin, 4), "tpsl_set": True,
    }


# ── Exit simulation ──────────────────────────────────────────────── #

def _check_exit(state: dict, sym: str, mark: float):
    """Called with the latest mark price — simulates SL / TP / trailing exit."""
    pos = state["positions"].get(sym)
    if not pos:
        return

    pos["last_mark"] = mark
    side = pos["side"]

    if side == "LONG":
        # Update trailing peak whenever price goes higher
        if pos["use_trailing"]:
            if mark > pos.get("trail_peak", 0):
                pos["trail_peak"] = mark

        # 1. SL check (full position)
        if mark <= pos["sl"]:
            _close_full(state, sym, mark, "SL")
            return

        if pos["use_trailing"]:
            # 2. Partial TP — close 50% and activate trailing when trail_trigger hit
            if not pos["partial_closed"] and mark >= pos["trail_trigger"]:
                _close_half(state, sym, mark)
                return   # position still open with 50% remaining

            # 3. Trailing stop on remaining 50%
            if pos["partial_closed"] and pos["trail_active"]:
                trail_stop = pos["trail_peak"] * (1 - pos["trail_range"] / 100)
                if mark <= trail_stop:
                    _close_full(state, sym, mark, "TRAIL_STOP")
                    return
        else:
            # Standard TP
            if mark >= pos["tp"]:
                _close_full(state, sym, mark, "TP")

    else:  # SHORT
        if mark >= pos["sl"]:
            _close_full(state, sym, mark, "SL")
            return
        if mark <= pos["tp"]:
            _close_full(state, sym, mark, "TP")


def _close_half(state: dict, sym: str, exit_price: float):
    """Close 50% at partial TP, activate trailing on remaining 50%."""
    pos  = state["positions"][sym]
    half = pos["original_qty"] / 2
    _record_partial(state, sym, exit_price, half)

    pos["qty"]           = pos["original_qty"] / 2
    pos["partial_closed"] = True
    pos["trail_active"]  = True
    pos["trail_peak"]    = exit_price

    logger.info(
        f"[PAPER][{sym}] 📈 Partial TP 50% @{exit_price:.5f} | "
        f"trailing stop activated (callback {pos['trail_range']}%)"
    )


def _close_full(state: dict, sym: str, exit_price: float, reason: str):
    pos    = state["positions"][sym]
    entry  = pos["entry"]
    side   = pos["side"]
    qty    = pos["qty"]

    qty_ratio   = qty / pos["original_qty"]
    margin_used = pos["original_margin"] * qty_ratio

    price_chg = (exit_price - entry) / entry if side == "LONG" else (entry - exit_price) / entry
    pnl       = price_chg * margin_used * pos["leverage"]

    state["balance"]   += margin_used + pnl
    state["total_pnl"] += pnl

    state["history"].append({
        "symbol":    sym,
        "side":      side,
        "entry":     entry,
        "exit":      exit_price,
        "qty":       round(qty, 6),
        "margin":    round(margin_used, 4),
        "pnl":       round(pnl, 4),
        "pnl_pct":   round(price_chg * pos["leverage"] * 100, 2),
        "reason":    reason,
        "opened_at": pos["opened_at"],
        "closed_at": _now(),
    })
    del state["positions"][sym]

    emoji = "✅" if pnl >= 0 else "❌"
    logger.info(
        f"[PAPER][{sym}] {emoji} {side} CLOSED @{exit_price:.5f} | "
        f"reason={reason} | PnL={pnl:+.4f} USDT ({price_chg*pos['leverage']*100:+.1f}%) | "
        f"balance=${state['balance']:.2f}"
    )


def _record_partial(state: dict, sym: str, exit_price: float, qty: float):
    pos        = state["positions"][sym]
    qty_ratio  = qty / pos["original_qty"]
    margin_used= pos["original_margin"] * qty_ratio
    price_chg  = (exit_price - pos["entry"]) / pos["entry"]
    pnl        = price_chg * margin_used * pos["leverage"]

    state["balance"]   += margin_used + pnl
    state["total_pnl"] += pnl

    logger.info(
        f"[PAPER][{sym}] 50% closed @{exit_price:.5f} | "
        f"PnL={pnl:+.4f} USDT | balance=${state['balance']:.2f}"
    )
