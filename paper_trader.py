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
        "balance":       PAPER_START_BALANCE,
        "positions":     {},
        "history":       [],
        "total_pnl":     0.0,
        "partial_closes": [],
    }


def _save(state: dict):
    Path(PAPER_STATE_FILE).write_text(json.dumps(state, indent=2, default=str))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Public interface ─────────────────────────────────────────────── #

def get_usdt_balance() -> float:
    return _load()["balance"]


def _total_equity(state: dict) -> float:
    """Free balance + all locked margins = total portfolio value."""
    locked = sum(p.get("margin", 0) for p in state["positions"].values())
    return state["balance"] + locked


def set_leverage(symbol: str, leverage: int):
    pass  # no-op for paper trading


def tag_strategy(symbol: str, strategy: str):
    """Store the strategy name in paper_state so it survives restarts."""
    state = _load()
    if symbol in state["positions"]:
        state["positions"][symbol]["strategy"] = strategy
        _save(state)


def get_last_close(symbol: str) -> dict | None:
    """
    Returns the most recent closed trade entry for a symbol from paper history.
    Used by bot.py to get exact pnl, pnl_pct, and exit reason after a close is detected.
    Returns None if no history found for the symbol.
    """
    state = _load()
    history = state.get("history", [])
    for entry in reversed(history):
        if entry.get("symbol") == symbol:
            return {
                "pnl":     entry.get("pnl", 0),
                "pnl_pct": entry.get("pnl_pct"),
                "reason":  entry.get("reason", ""),
            }
    return None


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
    use_s5_exits: bool     = False,
    tp_price_abs: float    = 0,
) -> dict:
    state  = _load()
    mark   = get_mark_price(symbol)
    margin = _total_equity(state) * trade_size_pct
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

    breakeven_after_partial = False
    if use_s5_exits:
        from config_s5 import S5_TRAIL_RANGE_PCT
        one_r         = mark - sl_price          # risk distance
        trail_trigger = mark + one_r             # 1:1 R:R level
        trail_range   = S5_TRAIL_RANGE_PCT       # tight trailing after partial
        tp_price      = tp_price_abs if tp_price_abs > mark else trail_trigger
        use_trailing  = True
        breakeven_after_partial = True

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
        "breakeven_after_partial": breakeven_after_partial,
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
    use_s4_exits: bool     = False,
    use_s5_exits: bool     = False,
    tp_price_abs: float    = 0,
) -> dict:
    state  = _load()
    mark   = get_mark_price(symbol)
    margin = _total_equity(state) * trade_size_pct
    qty    = (margin * leverage) / mark

    if sl_floor > 0:
        sl_price = sl_floor
    elif box_high > 0:
        sl_price = box_high * 1.001
    else:
        sl_price = mark * 1.015

    use_trailing  = False
    trail_trigger = None
    trail_range   = None
    tp_price      = mark * (1 - take_profit_pct)

    if use_s4_exits:
        from config_s4 import S4_TRAILING_TRIGGER_PCT, S4_TRAILING_RANGE_PCT
        trail_trigger = mark * (1 - S4_TRAILING_TRIGGER_PCT)  # price target for partial TP
        trail_range   = S4_TRAILING_RANGE_PCT                  # callback % for trailing stop
        tp_price      = trail_trigger
        use_trailing  = True

    breakeven_after_partial = False
    if use_s5_exits:
        from config_s5 import S5_TRAIL_RANGE_PCT
        one_r         = sl_price - mark             # risk distance
        trail_trigger = mark - one_r                # 1:1 R:R level (below entry)
        trail_range   = S5_TRAIL_RANGE_PCT
        tp_price      = tp_price_abs if 0 < tp_price_abs < mark else trail_trigger
        use_trailing  = True
        breakeven_after_partial = True

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
        "use_trailing":    use_trailing,
        "trail_trigger":   trail_trigger,
        "trail_range":     trail_range,
        "trail_peak":      mark,   # for SHORT: tracks lowest mark seen
        "trail_active":    False,
        "partial_closed":  False,
        "breakeven_after_partial": breakeven_after_partial,
        "last_mark":       mark,
        "opened_at":       _now(),
    }
    _save(state)
    logger.info(
        f"[PAPER][{symbol}] 🔴 SHORT {leverage}x | entry={mark:.5f} | "
        f"SL={sl_price:.5f} | "
        f"{'trailing @-{:.0f}%'.format((1 - trail_trigger/mark)*100) if use_trailing else 'TP={:.5f}'.format(tp_price)} | "
        f"margin=${margin:.2f}"
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

            if pos["partial_closed"] and pos["trail_active"]:
                # 3a. Hard TP at structural swing target (S5 only)
                if pos.get("breakeven_after_partial") and mark >= pos["tp"]:
                    _close_full(state, sym, mark, "TP")
                    return
                # 3b. Trailing stop on remaining 50%
                trail_stop = pos["trail_peak"] * (1 - pos["trail_range"] / 100)
                if mark <= trail_stop:
                    _close_full(state, sym, mark, "TRAIL_STOP")
                    return
        else:
            # Standard TP
            if mark >= pos["tp"]:
                _close_full(state, sym, mark, "TP")

    else:  # SHORT
        # Track the lowest mark (trail_peak used as trough for shorts)
        if pos["use_trailing"]:
            if mark < pos.get("trail_peak", mark):
                pos["trail_peak"] = mark

        # 1. SL check (full position)
        if mark >= pos["sl"]:
            _close_full(state, sym, mark, "SL")
            return

        if pos["use_trailing"]:
            # 2. Partial TP — close 50% and activate trailing when trail_trigger hit
            if not pos["partial_closed"] and mark <= pos["trail_trigger"]:
                _close_half_short(state, sym, mark)
                return   # position still open with 50% remaining

            if pos["partial_closed"] and pos["trail_active"]:
                # 3a. Hard TP at structural swing target (S5 only)
                if pos.get("breakeven_after_partial") and mark <= pos["tp"]:
                    _close_full(state, sym, mark, "TP")
                    return
                # 3b. Trailing stop on remaining 50%
                trail_stop = pos["trail_peak"] * (1 + pos["trail_range"] / 100)
                if mark >= trail_stop:
                    _close_full(state, sym, mark, "TRAIL_STOP")
                    return
        else:
            # Standard TP
            if mark <= pos["tp"]:
                _close_full(state, sym, mark, "TP")


def _close_half_short(state: dict, sym: str, exit_price: float):
    """Close 50% of SHORT at partial TP, activate trailing on remaining 50%."""
    pos  = state["positions"][sym]
    half = pos["original_qty"] / 2
    _record_partial(state, sym, exit_price, half)

    pos["qty"]            = pos["original_qty"] / 2
    pos["partial_closed"] = True
    pos["trail_active"]   = True
    pos["trail_peak"]     = exit_price  # lowest price seen so far
    if pos.get("breakeven_after_partial"):
        pos["sl"] = pos["entry"]   # move SL to breakeven

    logger.info(
        f"[PAPER][{sym}] 📉 Partial TP 50% @{exit_price:.5f} | "
        f"{'SL → breakeven ' + str(round(pos['entry'], 5)) + ' | ' if pos.get('breakeven_after_partial') else ''}"
        f"trailing stop activated (callback {pos['trail_range']}%)"
    )


def _close_half(state: dict, sym: str, exit_price: float):
    """Close 50% at partial TP, activate trailing on remaining 50%."""
    pos  = state["positions"][sym]
    half = pos["original_qty"] / 2
    _record_partial(state, sym, exit_price, half)

    pos["qty"]            = pos["original_qty"] / 2
    pos["partial_closed"] = True
    pos["trail_active"]   = True
    pos["trail_peak"]     = exit_price
    if pos.get("breakeven_after_partial"):
        pos["sl"] = pos["entry"]   # move SL to breakeven

    logger.info(
        f"[PAPER][{sym}] 📈 Partial TP 50% @{exit_price:.5f} | "
        f"{'SL → breakeven ' + str(round(pos['entry'], 5)) + ' | ' if pos.get('breakeven_after_partial') else ''}"
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

    # Combined P/L including any partial close that happened earlier
    partial_pnl  = pos.get("partial_pnl", 0.0)
    total_pnl_trade = pnl + partial_pnl
    orig_margin  = pos["original_margin"]
    combined_pct = round(total_pnl_trade / orig_margin * 100, 2) if orig_margin else round(price_chg * pos["leverage"] * 100, 2)

    state["history"].append({
        "symbol":    sym,
        "side":      side,
        "entry":     entry,
        "exit":      exit_price,
        "qty":       round(qty, 6),
        "margin":    round(orig_margin, 4),
        "pnl":       round(total_pnl_trade, 4),
        "pnl_pct":   combined_pct,
        "reason":    reason,
        "opened_at": pos["opened_at"],
        "closed_at": _now(),
    })
    del state["positions"][sym]

    emoji = "✅" if total_pnl_trade >= 0 else "❌"
    logger.info(
        f"[PAPER][{sym}] {emoji} {side} CLOSED @{exit_price:.5f} | "
        f"reason={reason} | PnL={total_pnl_trade:+.4f} USDT ({combined_pct:+.1f}%) | "
        f"balance=${state['balance']:.2f}"
    )


def drain_partial_closes() -> list:
    """Return and clear any partial-close events since the last call."""
    state = _load()
    closes = state.pop("partial_closes", [])
    if closes:
        _save(state)
    return closes


def scale_in_long(symbol: str, additional_trade_size_pct: float, leverage: int) -> None:
    """Add to an existing LONG paper position, updating average entry."""
    state = _load()
    pos   = state["positions"].get(symbol)
    if not pos:
        logger.warning(f"[PAPER][{symbol}] scale_in_long: no open position found")
        return
    mark              = get_mark_price(symbol)
    additional_margin = _total_equity(state) * additional_trade_size_pct
    additional_qty    = (additional_margin * leverage) / mark
    total_qty         = pos["qty"] + additional_qty
    pos["entry"]           = (pos["qty"] * pos["entry"] + additional_qty * mark) / total_qty
    pos["qty"]             = total_qty
    pos["original_qty"]    += additional_qty
    pos["margin"]          += additional_margin
    pos["original_margin"] += additional_margin
    state["balance"] -= additional_margin
    _save(state)
    logger.info(
        f"[PAPER][{symbol}] ➕ Scale-in LONG +{additional_margin:.2f} margin | "
        f"avg_entry={pos['entry']:.5f} | total_qty={total_qty:.4f}"
    )


def scale_in_short(symbol: str, additional_trade_size_pct: float, leverage: int) -> None:
    """Add to an existing SHORT paper position, updating average entry."""
    state = _load()
    pos   = state["positions"].get(symbol)
    if not pos:
        logger.warning(f"[PAPER][{symbol}] scale_in_short: no open position found")
        return
    mark              = get_mark_price(symbol)
    additional_margin = _total_equity(state) * additional_trade_size_pct
    additional_qty    = (additional_margin * leverage) / mark
    total_qty         = pos["qty"] + additional_qty
    pos["entry"]           = (pos["qty"] * pos["entry"] + additional_qty * mark) / total_qty
    pos["qty"]             = total_qty
    pos["original_qty"]    += additional_qty
    pos["margin"]          += additional_margin
    pos["original_margin"] += additional_margin
    state["balance"] -= additional_margin
    _save(state)
    logger.info(
        f"[PAPER][{symbol}] ➕ Scale-in SHORT +{additional_margin:.2f} margin | "
        f"avg_entry={pos['entry']:.5f} | total_qty={total_qty:.4f}"
    )


def is_partial_closed(symbol: str) -> bool:
    """Returns True if the paper position has had its first partial TP closed."""
    state = _load()
    pos   = state["positions"].get(symbol)
    return bool(pos and pos.get("partial_closed"))


def update_position_sl(symbol: str, new_sl: float, hold_side: str = "long") -> bool:
    """
    Move SL to new_sl only if it improves the position (LONG: higher, SHORT: lower).
    Returns True if the SL was actually updated.
    """
    state = _load()
    pos   = state["positions"].get(symbol)
    if not pos:
        return False
    current_sl = pos.get("sl", 0)
    if hold_side == "long":
        if new_sl <= current_sl:
            return False     # not an improvement
        pos["sl"] = new_sl
    else:  # short
        if new_sl >= current_sl or current_sl == 0:
            return False
        pos["sl"] = new_sl
    _save(state)
    return True


def _record_partial(state: dict, sym: str, exit_price: float, qty: float):
    pos        = state["positions"][sym]
    side       = pos["side"]
    qty_ratio  = qty / pos["original_qty"]
    margin_used= pos["original_margin"] * qty_ratio
    price_chg  = (exit_price - pos["entry"]) / pos["entry"] if side == "LONG" else (pos["entry"] - exit_price) / pos["entry"]
    pnl        = price_chg * margin_used * pos["leverage"]

    state["balance"]   += margin_used + pnl
    state["total_pnl"] += pnl

    # Store partial pnl on the position so _close_full can compute combined P/L
    pos["partial_pnl"] = pos.get("partial_pnl", 0.0) + pnl

    state.setdefault("partial_closes", []).append({
        "symbol":   sym,
        "side":     side,
        "entry":    pos["entry"],
        "exit":     exit_price,
        "qty":      round(qty, 6),
        "margin":   round(margin_used, 4),
        "pnl":      round(pnl, 4),
        "pnl_pct":  round(price_chg * pos["leverage"] * 100, 2),
        "strategy": pos.get("strategy", ""),
        "reason":   "PARTIAL_TP",
        "opened_at": pos["opened_at"],
        "closed_at": _now(),
    })
    logger.info(
        f"[PAPER][{sym}] 50% closed @{exit_price:.5f} | "
        f"PnL={pnl:+.4f} USDT | balance=${state['balance']:.2f}"
    )
