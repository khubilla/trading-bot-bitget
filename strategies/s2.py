"""
Strategy 2 — Daily Momentum + Daily Consolidation Breakout.

Purely daily chart. No 3m or 1H involvement.

Step 1 — Big momentum candle(s) within last 30 days
          Body ≥ S2_BIG_CANDLE_BODY_PCT (default 20%)
          Candle close must be above prior range
Step 2 — Daily RSI currently > 70
Step 3 — 1–5 tight daily candles consolidating after the big move
          All consolidation candles must have daily RSI > 70
          Range of consolidation ≤ S2_CONSOL_RANGE_PCT
Step 4 — Current daily candle is breaking out above the box
          Darvas-style: long wick → buy above body, short wick → buy above wick

SL  = bottom of the daily consolidation box * 0.999
TP  = entry * (1 + S2_TAKE_PROFIT_PCT)
"""

import logging
from typing import Literal

import pandas as pd

from indicators import calculate_rsi
from tools import body_pct, upper_wick

logger = logging.getLogger(__name__)
Signal = Literal["LONG", "SHORT", "HOLD", "PENDING_LONG", "PENDING_SHORT"]

# Default candle interval for S2 event snapshots.
SNAPSHOT_INTERVAL = "1D"


def evaluate_s2(
    symbol: str,
    daily_df: pd.DataFrame,
) -> tuple[Signal, float, float, float, str]:
    """
    Strategy 2 — purely on daily candles.
    Returns (signal, daily_rsi, entry_trigger, box_low, reason)
    Only LONG signals.
    """
    from config_s2 import (
        S2_ENABLED, S2_BIG_CANDLE_BODY_PCT, S2_BIG_CANDLE_LOOKBACK,
        S2_RSI_LONG_THRESH, S2_CONSOL_CANDLES, S2_CONSOL_RANGE_PCT,
        S2_BREAKOUT_BUFFER, S2_DARVAS_WICK_PCT,
    )

    if not S2_ENABLED:
        return "HOLD", 50.0, 0.0, 0.0, "S2 disabled"

    rsi_period  = 14
    min_candles = rsi_period + S2_BIG_CANDLE_LOOKBACK + S2_CONSOL_CANDLES + 2
    if len(daily_df) < min_candles:
        return "HOLD", 50.0, 0.0, 0.0, "Not enough daily candles"

    closes    = daily_df["close"].astype(float)
    rsi_ser   = calculate_rsi(closes, rsi_period)
    daily_rsi = float(rsi_ser.iloc[-1])

    if daily_rsi <= S2_RSI_LONG_THRESH:
        return "HOLD", daily_rsi, 0.0, 0.0, f"Daily RSI {daily_rsi:.1f} ≤ {S2_RSI_LONG_THRESH}"

    lookback_window      = daily_df.iloc[-(S2_BIG_CANDLE_LOOKBACK + 1):-1]
    big_candle_found     = False
    best_body_pct        = 0.0
    big_candle_body_top  = 0.0
    for _, row in lookback_window.iterrows():
        bp = body_pct(row)
        if bp >= S2_BIG_CANDLE_BODY_PCT:
            big_candle_found    = True
            best_body_pct       = max(best_body_pct, bp)
            big_candle_body_top = max(float(row["close"]), float(row["open"]))

    if not big_candle_found:
        return "HOLD", daily_rsi, 0.0, 0.0, (
            f"Daily RSI {daily_rsi:.1f} ✓ — no big candle ≥{S2_BIG_CANDLE_BODY_PCT*100:.0f}% in last {S2_BIG_CANDLE_LOOKBACK}d"
        )

    consol_found  = False
    box_high      = 0.0
    box_low       = 0.0
    entry_trigger = 0.0
    consol_size   = 0
    trigger_type  = ""

    for n in range(1, S2_CONSOL_CANDLES + 1):
        window = daily_df.iloc[-n - 1:-1]
        if len(window) < n:
            continue

        wh  = float(window["high"].max())
        wl  = float(window["low"].min())
        mid = (wh + wl) / 2
        if mid == 0:
            continue

        def _eff_top(r):
            bt = max(float(r["close"]), float(r["open"]))
            return bt if upper_wick(r) > S2_DARVAS_WICK_PCT * bt else float(r["high"])
        eff_tops = window.apply(_eff_top, axis=1)
        eff_h    = float(eff_tops.max())
        eff_l    = float(window.apply(lambda r: min(float(r["close"]), float(r["open"])), axis=1).min())
        if eff_h <= 0:
            continue
        range_pct = (eff_h - eff_l) / eff_h
        if range_pct > S2_CONSOL_RANGE_PCT:
            continue

        if not all(float(r["close"]) <= big_candle_body_top for _, r in window.iterrows()):
            continue

        box_top_pos = int(eff_tops.values.argmax())

        window_rsi = rsi_ser.iloc[-n - 1:-1]
        if not (window_rsi > S2_RSI_LONG_THRESH).all():
            continue

        consol_found = True
        box_high     = eff_h
        box_low      = eff_l
        consol_size  = n

        high_candle = window.iloc[box_top_pos]
        uw       = upper_wick(high_candle)
        body_top = max(float(high_candle["close"]), float(high_candle["open"]))

        if uw > S2_DARVAS_WICK_PCT * body_top:
            entry_trigger = body_top * (1 + S2_BREAKOUT_BUFFER)
            trigger_type  = "above_body (long wick — ignore wick)"
        else:
            entry_trigger = float(high_candle["high"]) * (1 + S2_BREAKOUT_BUFFER)
            trigger_type  = "above_wick (short wick — clean high)"

        big_candle_floor = big_candle_body_top
        if entry_trigger < big_candle_floor:
            entry_trigger = big_candle_floor
            trigger_type += f" [floored to big candle body {big_candle_body_top:.5f}]"
            box_high = entry_trigger
            box_low  = entry_trigger * (1 - S2_CONSOL_RANGE_PCT)

        break

    if not consol_found:
        return "HOLD", daily_rsi, 0.0, 0.0, (
            f"Big candle ✅ {best_body_pct*100:.0f}% | RSI {daily_rsi:.1f} — no tight consolidation yet (1–{S2_CONSOL_CANDLES} candles)"
        )

    current_close = float(daily_df["close"].iloc[-1])
    if current_close <= entry_trigger:
        return "HOLD", daily_rsi, box_high, box_low, (
            f"Coiling ✅ ({consol_size}d) big_candle={best_body_pct*100:.0f}% RSI={daily_rsi:.1f} — "
            f"waiting breakout {trigger_type} > {entry_trigger:.5f} (now {current_close:.5f})"
        )

    logger.info(
        f"[S2][{symbol}] ✅ LONG | RSI={daily_rsi:.1f} | "
        f"coil={consol_size}d box={box_low:.5f}–{box_high:.5f} | "
        f"{trigger_type} trigger={entry_trigger:.5f} close={current_close:.5f}"
    )
    return "LONG", daily_rsi, box_high, box_low, (
        f"S2 ✅ {consol_size}d coil | big_candle={best_body_pct*100:.0f}% | "
        f"RSI={daily_rsi:.1f} | {trigger_type}"
    )


# ── S2 Exit Placement ─────────────────────────────────────── #

def _place_partial_trail_exits(symbol: str, hold_side: str, qty_str: str,
                               sl_trig: float, sl_exec: float,
                               trail_trigger: float, trail_range: float) -> bool:
    """
    3-leg exits: full-qty SL + 50% partial TP at trail_trigger + 50% trailing
    stop on the remainder. Shared by S2/S3/S4/S6 wrappers.
    """
    import time as _t
    import trader  # late import — respects test patches of trader._sym_info
    import bitget as bg

    half_qty   = trader._round_qty(float(qty_str) / 2, symbol)
    rest_qty   = trader._round_qty(float(qty_str) - float(half_qty), symbol)
    range_rate = str(round(trail_range, 4))

    for attempt in range(3):
        try:
            bg.place_pos_sl_only(symbol, hold_side, sl_trig, sl_exec)
            _t.sleep(0.5)
            bg.place_profit_plan(symbol, hold_side, half_qty, trail_trigger)
            _t.sleep(0.5)
            bg.place_moving_plan(symbol, hold_side, rest_qty, trail_trigger, range_rate)
            return True
        except Exception as e:
            logger.warning(f"[{symbol}] S2 exits attempt {attempt+1}/3: {e}")
            if attempt < 2:
                _t.sleep(1.5)
    return False


def compute_and_place_long_exits(symbol: str, qty_str: str, fill: float, stop_loss_pct: float) -> tuple[bool, float, float]:
    """
    Compute S2 long-side SL/trail levels and place exits.
    Returns (ok, sl_trig, trail_trig).
    """
    import trader
    from config_s2 import S2_TRAILING_TRIGGER_PCT, S2_TRAILING_RANGE_PCT

    trail_trig = float(trader._round_price(fill * (1 + S2_TRAILING_TRIGGER_PCT), symbol))
    sl_trig    = float(trader._round_price(fill * (1 - stop_loss_pct), symbol))
    sl_exec    = float(trader._round_price(sl_trig * 0.995, symbol))
    ok = _place_partial_trail_exits(symbol, "long", qty_str, sl_trig, sl_exec, trail_trig, S2_TRAILING_RANGE_PCT)
    return ok, sl_trig, trail_trig


# ── S2 Swing Trail ────────────────────────────────────────── #

def maybe_trail_sl(symbol: str, ap: dict, tr_mod, st_mod, partial_done: bool) -> None:
    """
    Structural swing trail for S2 LONG: only active after the partial has fired.
    Pulls SL up to the 1D swing-low after price exceeds the prior swing-high.
    """
    import config_s2
    from tools import find_swing_high_target, find_swing_low_after_ref

    if not getattr(config_s2, "S2_USE_SWING_TRAIL", False):
        return
    if ap.get("side") != "LONG" or not partial_done:
        return
    try:
        lb    = config_s2.S2_SWING_LOOKBACK
        cs_df = tr_mod.get_candles(symbol, "1D", limit=lb + 5)
        mark  = tr_mod.get_mark_price(symbol)
        if cs_df.empty or len(cs_df) < 3:
            return
        ref = ap.get("swing_trail_ref")
        if ref is None:
            ap["swing_trail_ref"] = find_swing_high_target(cs_df, mark, lookback=lb)
            return
        if mark >= ref:
            raw = find_swing_low_after_ref(cs_df, mark, ref, lookback=lb)
            if raw:
                swing_sl = raw * (1 - config_s2.S2_STOP_LOSS_PCT)
                if swing_sl > ap.get("sl", 0) and tr_mod.update_position_sl(symbol, swing_sl, hold_side="long"):
                    ap["sl"] = swing_sl
                    st_mod.update_open_trade_sl(symbol, swing_sl)
                    ap["swing_trail_ref"] = find_swing_high_target(cs_df, mark, lookback=lb)
                    logger.info(f"[S2][{symbol}] 📍 Swing trail: SL → {swing_sl:.5f} (daily swing low after ref high {ref:.5f})")
    except Exception as e:
        logger.error(f"S2 swing trail error [{symbol}]: {e}")


# ── S2 DNA Snapshot Fields ────────────────────────────────── #

def dna_fields(candles: dict) -> dict:
    """S2 trade fingerprint: daily EMA slope, price vs EMA, RSI bucket."""
    from indicators import calculate_ema, calculate_rsi
    from trade_dna import ema_slope, price_vs_ema, rsi_bucket, _is_empty, _closes_from

    out = {}
    daily = candles.get("daily")
    if _is_empty(daily):
        return out
    closes_d = _closes_from(daily)
    ema_d    = calculate_ema(closes_d, 20)
    rsi_d    = calculate_rsi(closes_d)
    out["snap_trend_daily_ema_slope"]    = ema_slope(closes_d, 20)
    out["snap_trend_daily_price_vs_ema"] = price_vs_ema(float(closes_d.iloc[-1]), float(ema_d.iloc[-1]))
    out["snap_trend_daily_rsi_bucket"]   = rsi_bucket(float(rsi_d.iloc[-1]))
    return out


# ── S2 Pending-Signal Queue ───────────────────────────────── #

def queue_pending(bot, c: dict) -> None:
    """Queue an S2 LONG breakout on bot.pending_signals for the entry watcher."""
    import time as _t
    import state as st

    symbol = c["symbol"]
    bot.pending_signals[symbol] = {
        "strategy":           "S2",
        "side":               "LONG",
        "trigger":            c["s2_bh"],
        "s2_bh":              c["s2_bh"],
        "s2_bl":              c["s2_bl"],
        "priority_rank":      c.get("priority_rank", 999),
        "priority_score":     c.get("priority_score", 0.0),
        "snap_daily_rsi":     round(c["s2_rsi"], 1),
        "snap_box_range_pct": round((c["s2_bh"] - c["s2_bl"]) / c["s2_bl"] * 100, 3)
                              if c["s2_bh"] and c["s2_bl"] else None,
        "snap_sentiment":     bot.sentiment.direction if bot.sentiment else "?",
        "expires":            _t.time() + 86400,
    }
    st.save_pending_signals(bot.pending_signals)
    logger.info(
        f"[S2][{symbol}] 🕐 PENDING LONG queued | "
        f"trigger={c['s2_bh']:.5f} | SL={c['s2_bl']:.5f}"
    )
    st.add_scan_log(
        f"[S2][{symbol}] 🕐 PENDING LONG | trigger={c['s2_bh']:.5f}", "SIGNAL"
    )


# ── S2 Entry Watcher (pending tick) ───────────────────────── #

def handle_pending_tick(bot, symbol: str, sig: dict, balance: float,
                        paper_mode: bool | None = None) -> str | None:
    """S2 breakout trigger + invalidation check. Return 'break' to stop outer loop."""
    import state as st
    import trader as tr
    import config, config_s2

    ps = st.get_pair_state(symbol)
    if ps.get("s2_signal", "HOLD") not in ("LONG",):
        logger.info(f"[S2][{symbol}] 🚫 Signal gone — cancelling pending")
        st.add_scan_log(f"[S2][{symbol}] 🚫 Pending cancelled (signal gone)", "INFO")
        bot.pending_signals.pop(symbol, None)
        st.save_pending_signals(bot.pending_signals)
        return None
    try:
        mark = tr.get_mark_price(symbol)
    except Exception:
        return None
    s2_bh = sig["s2_bh"]
    s2_bl = sig["s2_bl"]
    if mark < s2_bl:
        logger.info(f"[S2][{symbol}] ❌ Invalidated — mark {mark:.5f} < box_low {s2_bl:.5f}")
        st.add_scan_log(f"[S2][{symbol}] ❌ Pending cancelled (price below box)", "INFO")
        bot.pending_signals.pop(symbol, None)
        st.save_pending_signals(bot.pending_signals)
        return None
    in_window = s2_bh <= mark <= s2_bh * (1 + config_s2.S2_MAX_ENTRY_BUFFER)
    if in_window:
        with bot._trade_lock:
            if symbol in bot.active_positions:
                bot.pending_signals.pop(symbol, None)
                st.save_pending_signals(bot.pending_signals)
                return None
            if len(bot.active_positions) >= config.MAX_CONCURRENT_TRADES:
                return "break"
            if st.is_pair_paused(symbol):
                return None
            bot._fire_s2(symbol, sig, mark, balance)
        bot.pending_signals.pop(symbol, None)
        st.save_pending_signals(bot.pending_signals)
    return None


# ── S2 Paper Trail Setup ──────────────────────────────────── #

def compute_paper_trail_long(mark: float, sl_price: float, tp_price_abs: float = 0,
                             take_profit_pct: float = 0.05) -> tuple[bool, float, float, float, bool]:
    """Paper-trader LONG trail setup for S2. Returns (use_trailing, trail_trigger, trail_range, tp_price, breakeven_after_partial)."""
    from config_s2 import S2_TRAILING_TRIGGER_PCT, S2_TRAILING_RANGE_PCT
    trail_trigger = mark * (1 + S2_TRAILING_TRIGGER_PCT)
    trail_range   = S2_TRAILING_RANGE_PCT
    return True, trail_trigger, trail_range, trail_trigger, False


# ── S2 Scale-In Helpers ───────────────────────────────────── #

def scale_in_specs() -> dict:
    """Per-strategy scale-in orchestration constants for S2 (LONG)."""
    import config_s2
    return {
        "direction": "BULLISH",
        "hold_side": "long",
        "leverage":  config_s2.S2_LEVERAGE,
    }


def is_scale_in_window(ap: dict, mark_now: float) -> bool:
    """True when price is re-entering the S2 box_high zone (retest)."""
    import config_s2
    return ap["box_high"] <= mark_now <= ap["box_high"] * (1 + config_s2.S2_MAX_ENTRY_BUFFER)


def recompute_scale_in_sl_trigger(ap: dict, new_avg: float) -> tuple[float, float]:
    """S2 post-scale-in: SL at max(box_low*0.999, new_avg*(1-SL_PCT)), trail at new_avg*(1+TRIG_PCT)."""
    import config_s2
    new_sl = max(
        ap.get("box_low", 0) * 0.999,
        new_avg * (1 - config_s2.S2_STOP_LOSS_PCT),
    )
    new_trig = new_avg * (1 + config_s2.S2_TRAILING_TRIGGER_PCT)
    return new_sl, new_trig
