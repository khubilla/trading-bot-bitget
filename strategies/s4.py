"""
Strategy 4 — Post-Pump RSI Divergence Short.

Daily candles only.
  1. Big momentum spike (≥20% body) in last 30 daily candles
  2. RSI peaked above 75 within last 10 candles (overbought)
  3. (Optional) RSI bearish divergence — 2nd RSI push lower
  4. Entry: intraday breach of previous day's low

SL  = spike_high * (1 + S4_SL_BUFFER)
Exit: 50% close at −10%, trailing stop on remainder (same as S2)
Sentiment gate: only fires when NOT BULLISH
"""

import logging
from typing import Literal

import pandas as pd

from indicators import calculate_rsi
from tools import body_pct

logger = logging.getLogger(__name__)
Signal = Literal["LONG", "SHORT", "HOLD", "PENDING_LONG", "PENDING_SHORT"]

# Default candle interval for S4 event snapshots.
SNAPSHOT_INTERVAL = "1D"


def evaluate_s4(
    symbol: str,
    daily_df: pd.DataFrame,
    htf_df: pd.DataFrame | None = None,
) -> tuple[Signal, float, float, float, float, float, bool, str, str]:
    """
    Strategy 4 — Post-pump RSI divergence short.
    Returns (signal, daily_rsi, entry_trigger, sl_price, spike_body_pct, rsi_peak, rsi_div, rsi_div_str, reason)
    """
    from config_s4 import (
        S4_ENABLED, S4_BIG_CANDLE_BODY_PCT, S4_BIG_CANDLE_LOOKBACK,
        S4_RSI_PEAK_THRESH, S4_RSI_PEAK_LOOKBACK, S4_RSI_DIV_MIN_DROP,
        S4_RSI_STILL_HOT_THRESH, S4_LOW_LOOKBACK,
    )

    if not S4_ENABLED:
        return "HOLD", 50.0, 0.0, 0.0, 0.0, 0.0, False, "", "S4 disabled"

    rsi_period  = 14
    min_candles = rsi_period + S4_BIG_CANDLE_LOOKBACK + 2
    if len(daily_df) < min_candles:
        return "HOLD", 50.0, 0.0, 0.0, 0.0, 0.0, False, "", "Not enough daily candles"

    closes    = daily_df["close"].astype(float)
    rsi_ser   = calculate_rsi(closes, rsi_period)
    daily_rsi = float(rsi_ser.iloc[-1])

    lookback     = daily_df.iloc[-(S4_BIG_CANDLE_LOOKBACK + 1):-1]
    spike_found  = False
    best_body_pct = 0.0
    spike_high   = 0.0
    for _, row in lookback.iterrows():
        bp = body_pct(row)
        if bp >= S4_BIG_CANDLE_BODY_PCT:
            spike_found = True
            if bp > best_body_pct:
                best_body_pct = bp
        if spike_found:
            spike_high = max(spike_high, float(row["high"]))

    if not spike_found:
        return "HOLD", daily_rsi, 0.0, 0.0, 0.0, 0.0, False, "", (
            f"No spike candle ≥{S4_BIG_CANDLE_BODY_PCT*100:.0f}% body in last {S4_BIG_CANDLE_LOOKBACK}d"
        )

    rsi_window = rsi_ser.iloc[-S4_RSI_PEAK_LOOKBACK - 1:-1]
    rsi_peak   = float(rsi_window.max())
    if rsi_peak < S4_RSI_PEAK_THRESH:
        return "HOLD", daily_rsi, 0.0, 0.0, best_body_pct, rsi_peak, False, "", (
            f"Spike ✅ body={best_body_pct*100:.0f}% | "
            f"RSI peak={rsi_peak:.1f} < {S4_RSI_PEAK_THRESH} (not overbought)"
        )

    prev_rsi = float(rsi_ser.iloc[-2])
    if prev_rsi < S4_RSI_STILL_HOT_THRESH:
        return "HOLD", daily_rsi, 0.0, 0.0, best_body_pct, rsi_peak, False, "", (
            f"Spike ✅ RSI peaked={rsi_peak:.1f} | "
            f"Setup invalidated — prev candle RSI={prev_rsi:.1f} < {S4_RSI_STILL_HOT_THRESH}"
        )

    div_note    = ""
    rsi_div     = False
    rsi_div_str = ""
    if len(rsi_window) >= 4:
        mid      = len(rsi_window) // 2
        first_h  = float(rsi_window.iloc[:mid].max())
        second_h = float(rsi_window.iloc[mid:].max())
        if first_h > 0 and (first_h - second_h) >= S4_RSI_DIV_MIN_DROP:
            rsi_div     = True
            rsi_div_str = f"{first_h:.1f}→{second_h:.1f}"
            div_note    = f" | RSI div ✅ ({rsi_div_str})"
        else:
            rsi_div_str = f"{first_h:.1f}→{second_h:.1f}"
            div_note    = f" | RSI div ❌ ({rsi_div_str})"

    from config_s4 import S4_LEVERAGE, S4_ENTRY_BUFFER
    entry_trigger = float(daily_df.iloc[-2]["low"]) * (1 - S4_ENTRY_BUFFER)
    sl_price      = entry_trigger * (1 + 0.50 / S4_LEVERAGE)

    if htf_df is not None and len(htf_df) >= S4_LOW_LOOKBACK + 1:
        min_htf_low = float(htf_df["low"].iloc[-(S4_LOW_LOOKBACK + 1):-1].min())
        if entry_trigger > min_htf_low:
            return "HOLD", daily_rsi, entry_trigger, sl_price, best_body_pct, rsi_peak, rsi_div, rsi_div_str, (
                f"S4 setup ✅ spike={best_body_pct*100:.0f}% | RSI peak={rsi_peak:.1f}{div_note} | "
                f"1H low filter ❌ entry {entry_trigger:.5f} > {S4_LOW_LOOKBACK}-candle 1H low {min_htf_low:.5f}"
            )

    logger.info(
        f"[S4][{symbol}] ✅ SHORT setup | "
        f"spike={best_body_pct*100:.0f}% | RSI peak={rsi_peak:.1f} now={daily_rsi:.1f}{div_note} | "
        f"entry≤{entry_trigger:.5f} | SL={sl_price:.5f} (-50% P/L @ {S4_LEVERAGE}x)"
    )
    return "SHORT", daily_rsi, entry_trigger, sl_price, best_body_pct, rsi_peak, rsi_div, rsi_div_str, (
        f"S4 ✅ spike={best_body_pct*100:.0f}% | RSI peak={rsi_peak:.1f}{div_note} | "
        f"entry≤{entry_trigger:.5f}"
    )


# ── S4 Exit Placement ─────────────────────────────────────── #

def _place_partial_trail_exits(symbol: str, hold_side: str, qty_str: str,
                               sl_trig: float, sl_exec: float,
                               trail_trigger: float, trail_range: float) -> bool:
    """3-leg S4 exits: full SL, 50% partial at trail_trigger, trailing stop on 50%."""
    import time as _t
    import trader
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
            logger.warning(f"[{symbol}] S4 exits attempt {attempt+1}/3: {e}")
            if attempt < 2:
                _t.sleep(1.5)
    return False


def compute_and_place_short_exits(symbol: str, qty_str: str, fill: float,
                                  sl_trig: float, sl_exec: float) -> tuple[bool, float, float]:
    """
    Compute S4 short-side trail level and place exits.
    Returns (ok, sl_trig, trail_trig).
    """
    import trader
    from config_s4 import S4_TRAILING_TRIGGER_PCT, S4_TRAILING_RANGE_PCT

    trail_trig = float(trader._round_price(fill * (1 - S4_TRAILING_TRIGGER_PCT), symbol))
    ok = _place_partial_trail_exits(symbol, "short", qty_str, sl_trig, sl_exec, trail_trig, S4_TRAILING_RANGE_PCT)
    return ok, sl_trig, trail_trig


# ── S4 Swing Trail ────────────────────────────────────────── #

def maybe_trail_sl(symbol: str, ap: dict, tr_mod, st_mod, partial_done: bool) -> None:
    """
    Structural swing trail for S4 SHORT: after partial fires, pull SL down to
    the nearest daily swing-high above entry.
    """
    import config_s4
    from tools import find_swing_low_target, find_swing_high_after_ref

    if not config_s4.S4_USE_SWING_TRAIL:
        return
    if ap.get("side") != "SHORT" or not partial_done:
        return
    try:
        lb    = config_s4.S4_SWING_LOOKBACK
        cs_df = tr_mod.get_candles(symbol, "1D", limit=lb + 5)
        mark  = tr_mod.get_mark_price(symbol)
        if cs_df.empty or len(cs_df) < 3:
            return
        ref = ap.get("swing_trail_ref")
        if ref is None:
            ap["swing_trail_ref"] = find_swing_low_target(cs_df, mark, lookback=lb)
            return
        if mark <= ref:
            raw = find_swing_high_after_ref(cs_df, mark, ref, lookback=lb)
            if raw:
                swing_sl = raw * (1 + config_s4.S4_ENTRY_BUFFER)
                if swing_sl < ap.get("sl", float("inf")) and tr_mod.update_position_sl(symbol, swing_sl, hold_side="short"):
                    ap["sl"] = swing_sl
                    st_mod.update_open_trade_sl(symbol, swing_sl)
                    ap["swing_trail_ref"] = find_swing_low_target(cs_df, mark, lookback=lb)
                    logger.info(f"[S4][{symbol}] 📍 Swing trail: SL → {swing_sl:.5f} (daily swing high after ref low {ref:.5f})")
    except Exception as e:
        logger.error(f"S4 swing trail error [{symbol}]: {e}")


# ── S4 DNA Snapshot Fields ────────────────────────────────── #

def dna_fields(candles: dict) -> dict:
    """S4 trade fingerprint: daily EMA/RSI, optional H1 EMA."""
    from indicators import calculate_ema, calculate_rsi
    from trade_dna import ema_slope, price_vs_ema, rsi_bucket, _is_empty, _closes_from

    out = {}
    daily = candles.get("daily")
    h1    = candles.get("h1")
    if _is_empty(daily):
        return out
    closes_d = _closes_from(daily)
    ema_d    = calculate_ema(closes_d, 20)
    rsi_d    = calculate_rsi(closes_d)
    out["snap_trend_daily_ema_slope"]    = ema_slope(closes_d, 20)
    out["snap_trend_daily_price_vs_ema"] = price_vs_ema(float(closes_d.iloc[-1]), float(ema_d.iloc[-1]))
    out["snap_trend_daily_rsi_bucket"]   = rsi_bucket(float(rsi_d.iloc[-1]))
    if not _is_empty(h1):
        closes_h = _closes_from(h1)
        ema_h    = calculate_ema(closes_h, 20)
        out["snap_trend_h1_ema_slope"]    = ema_slope(closes_h, 20)
        out["snap_trend_h1_price_vs_ema"] = price_vs_ema(float(closes_h.iloc[-1]), float(ema_h.iloc[-1]))
    return out


# ── S4 Pending-Signal Queue ───────────────────────────────── #

def queue_pending(bot, c: dict) -> None:
    """Queue an S4 SHORT spike-reversal on bot.pending_signals for the entry watcher."""
    import time as _t
    import state as st
    import config_s4

    symbol = c["symbol"]
    s4_trigger = c["s4_trigger"]
    s4_sl      = c["s4_sl"]
    prev_low_approx = s4_trigger / (1 - config_s4.S4_ENTRY_BUFFER)
    bot.pending_signals[symbol] = {
        "strategy":             "S4",
        "side":                 "SHORT",
        "trigger":              s4_trigger,
        "s4_sl":                s4_sl,
        "prev_low":             prev_low_approx,
        "priority_rank":        c.get("priority_rank", 999),
        "priority_score":       c.get("priority_score", 0.0),
        "snap_rsi":             round(c["s4_rsi"], 1),
        "snap_rsi_peak":        round(c["s4_rsi_peak"], 1),
        "snap_spike_body_pct":  round(c["s4_body_pct"] * 100, 1),
        "snap_rsi_div":         c["s4_div"],
        "snap_rsi_div_str":     c["s4_div_str"],
        "snap_sentiment":       bot.sentiment.direction if bot.sentiment else "?",
        "expires":              _t.time() + 86400,
    }
    st.save_pending_signals(bot.pending_signals)
    logger.info(
        f"[S4][{symbol}] 🕐 PENDING SHORT queued | "
        f"trigger≤{s4_trigger:.5f} | SL={s4_sl:.5f}"
    )
    st.add_scan_log(
        f"[S4][{symbol}] 🕐 PENDING SHORT | trigger≤{s4_trigger:.5f}", "SIGNAL"
    )


# ── S4 Entry Watcher (pending tick) ───────────────────────── #

def handle_pending_tick(bot, symbol: str, sig: dict, balance: float,
                        paper_mode: bool | None = None) -> str | None:
    """S4 spike-reversal trigger + invalidation check. Return 'break' to stop outer loop."""
    import state as st
    import trader as tr
    import config, config_s4

    ps = st.get_pair_state(symbol)
    if ps.get("s4_signal", "HOLD") not in ("SHORT",):
        logger.info(f"[S4][{symbol}] 🚫 Signal gone — cancelling pending")
        st.add_scan_log(f"[S4][{symbol}] 🚫 Pending cancelled (signal gone)", "INFO")
        bot.pending_signals.pop(symbol, None)
        st.save_pending_signals(bot.pending_signals)
        return None
    try:
        mark = tr.get_mark_price(symbol)
    except Exception:
        return None
    s4_sl = sig["s4_sl"]
    if mark > s4_sl:
        logger.info(f"[S4][{symbol}] ❌ Invalidated — mark {mark:.5f} > SL {s4_sl:.5f}")
        st.add_scan_log(f"[S4][{symbol}] ❌ Pending cancelled (price above SL)", "INFO")
        bot.pending_signals.pop(symbol, None)
        st.save_pending_signals(bot.pending_signals)
        return None
    s4_trigger = sig["trigger"]
    prev_low   = sig["prev_low"]
    in_window  = (mark <= s4_trigger and
                  mark >= prev_low * (1 - config_s4.S4_MAX_ENTRY_BUFFER))
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
            bot._fire_s4(symbol, sig, mark, balance)
        bot.pending_signals.pop(symbol, None)
        st.save_pending_signals(bot.pending_signals)
    return None


# ── S4 Paper Trail Setup ──────────────────────────────────── #

def compute_paper_trail_short(mark: float, sl_price: float, tp_price_abs: float = 0,
                              take_profit_pct: float = 0.05) -> tuple[bool, float, float, float, bool]:
    """Paper-trader SHORT trail setup for S4. Returns (use_trailing, trail_trigger, trail_range, tp_price, breakeven_after_partial)."""
    from config_s4 import S4_TRAILING_TRIGGER_PCT, S4_TRAILING_RANGE_PCT
    trail_trigger = mark * (1 - S4_TRAILING_TRIGGER_PCT)
    trail_range   = S4_TRAILING_RANGE_PCT
    return True, trail_trigger, trail_range, trail_trigger, False


# ── S4 Scale-In Helpers ───────────────────────────────────── #

def scale_in_specs() -> dict:
    """Per-strategy scale-in orchestration constants for S4 (SHORT)."""
    import config_s4
    return {
        "direction": "BEARISH",
        "hold_side": "short",
        "leverage":  config_s4.S4_LEVERAGE,
    }


def is_scale_in_window(ap: dict, mark_now: float) -> bool:
    """True when price is bouncing back toward the S4 prev_low zone (retest)."""
    import config_s4
    pl = ap["s4_prev_low"]
    return pl * (1 - config_s4.S4_MAX_ENTRY_BUFFER) <= mark_now <= pl * (1 - config_s4.S4_ENTRY_BUFFER)


def recompute_scale_in_sl_trigger(ap: dict, new_avg: float) -> tuple[float, float]:
    """S4 post-scale-in: SL at new_avg*(1+0.50/LEVERAGE), trail at new_avg*(1-TRIG_PCT)."""
    import config_s4
    new_sl   = new_avg * (1 + 0.50 / config_s4.S4_LEVERAGE)
    new_trig = new_avg * (1 - config_s4.S4_TRAILING_TRIGGER_PCT)
    return new_sl, new_trig
