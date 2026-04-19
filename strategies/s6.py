"""
Strategy 6 — V-Formation Liquidity Sweep Short.

Scans the last S6_SPIKE_LOOKBACK daily candles for a V-formation:
  1. Swing high: local maximum with RSI > S6_OVERBOUGHT_RSI
  2. Spike low : price drops >= S6_MIN_DROP_PCT from swing-high's high
  3. V-pivot   : candle immediately after spike low is bullish
                 (close > open AND close > spike_low_candle.close)
"""

import logging
from typing import Literal

import pandas as pd

from indicators import calculate_rsi

logger = logging.getLogger(__name__)
Signal = Literal["LONG", "SHORT", "HOLD", "PENDING_LONG", "PENDING_SHORT"]

# Default candle interval for S6 event snapshots.
SNAPSHOT_INTERVAL = "1D"


def evaluate_s6(
    symbol: str,
    daily_df: pd.DataFrame,
    allowed_direction: str,
) -> tuple[Signal, float, float, float, float, str]:
    """
    Returns (signal, peak_level, sl_price, drop_pct, rsi_at_peak, reason).
    signal is PENDING_SHORT when a valid V is found in a BEARISH market.
    """
    from config_s6 import (
        S6_ENABLED, S6_RSI_LOOKBACK, S6_SPIKE_LOOKBACK,
        S6_OVERBOUGHT_RSI, S6_MIN_DROP_PCT, S6_SL_PCT,
        S6_MIN_RECOVERY_RATIO,
    )

    _hold = lambda msg: ("HOLD", 0.0, 0.0, 0.0, 0.0, msg)

    if not S6_ENABLED:
        return _hold("S6 disabled")

    if allowed_direction != "BEARISH":
        return _hold(f"Direction {allowed_direction!r} — S6 requires BEARISH")

    min_rows = S6_SPIKE_LOOKBACK + S6_RSI_LOOKBACK + 2
    if len(daily_df) < min_rows:
        return _hold(f"Insufficient daily candles ({len(daily_df)} < {min_rows})")

    rsi_series = calculate_rsi(daily_df["close"], S6_RSI_LOOKBACK)

    window  = daily_df.iloc[-(S6_SPIKE_LOOKBACK + 2):].reset_index(drop=True)
    rsi_win = rsi_series.iloc[-(S6_SPIKE_LOOKBACK + 2):].reset_index(drop=True)
    n       = len(window)

    for i in range(n - 5, 0, -1):
        if not (window["high"].iloc[i] > window["high"].iloc[i - 1] and
                window["high"].iloc[i] > window["high"].iloc[i + 1]):
            continue
        if pd.isna(rsi_win.iloc[i]) or rsi_win.iloc[i] <= S6_OVERBOUGHT_RSI:
            continue

        peak_level  = float(window["high"].iloc[i])
        rsi_at_peak = float(rsi_win.iloc[i])

        after_high = window.iloc[i + 1:]
        spike_abs  = int(after_high["low"].idxmin())
        spike_candle = window.iloc[spike_abs]
        spike_low    = float(spike_candle["low"])

        drop_pct = (peak_level - spike_low) / peak_level
        if drop_pct < S6_MIN_DROP_PCT:
            continue

        between = window.iloc[i + 1: spike_abs]
        if not between.empty and float(between["high"].max()) > peak_level:
            continue

        if spike_abs + 1 >= n:
            continue

        pivot = window.iloc[spike_abs + 1]
        if not (pivot["close"] > pivot["open"] and
                pivot["close"] > spike_candle["close"]):
            continue

        post_pivot = window.iloc[spike_abs + 2:]
        if not post_pivot.empty and float(post_pivot["high"].max()) > peak_level:
            continue

        current_close   = float(window.iloc[-1]["close"])
        recovery_ratio  = (current_close - spike_low) / (peak_level - spike_low)
        if recovery_ratio < S6_MIN_RECOVERY_RATIO:
            continue

        sl_price = peak_level * (1 + S6_SL_PCT)
        reason   = (
            f"V-formation ✅ | RSI at peak {rsi_at_peak:.1f} | "
            f"Drop {drop_pct * 100:.1f}% | Peak {peak_level:.5f} | "
            f"SL {sl_price:.5f}"
        )
        return "PENDING_SHORT", peak_level, sl_price, drop_pct, rsi_at_peak, reason

    return _hold(f"No V-formation in last {S6_SPIKE_LOOKBACK} days")


# ── S6 Exit Placement ─────────────────────────────────────── #

def _place_partial_trail_exits(symbol: str, hold_side: str, qty_str: str,
                               sl_trig: float, sl_exec: float,
                               trail_trigger: float, trail_range: float) -> bool:
    """3-leg S6 exits: full SL, 50% partial at trail_trigger, trailing stop on 50%."""
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
            logger.warning(f"[{symbol}] S6 exits attempt {attempt+1}/3: {e}")
            if attempt < 2:
                _t.sleep(1.5)
    return False


def compute_and_place_short_exits(symbol: str, qty_str: str, fill: float,
                                  sl_trig: float, sl_exec: float) -> tuple[bool, float, float]:
    """
    Compute S6 short-side trail level and place exits.
    Returns (ok, sl_trig, trail_trig).
    """
    import trader
    from config_s6 import S6_TRAILING_TRIGGER_PCT, S6_TRAIL_RANGE_PCT

    trail_trig = float(trader._round_price(fill * (1 - S6_TRAILING_TRIGGER_PCT), symbol))
    ok = _place_partial_trail_exits(symbol, "short", qty_str, sl_trig, sl_exec, trail_trig, S6_TRAIL_RANGE_PCT)
    return ok, sl_trig, trail_trig


# ── S6 Pending-Signal Queue ───────────────────────────────── #

def queue_pending(bot, candidate: dict) -> None:
    """Queue an S6 V-formation watcher on bot.pending_signals."""
    import time as _t
    import state as st

    symbol = candidate["symbol"]
    bot.pending_signals[symbol] = {
        "strategy":           "S6",
        "side":               "SHORT",
        "peak_level":         candidate["s6_peak_level"],
        "sl":                 candidate["s6_sl"],
        "drop_pct":           candidate["s6_drop_pct"],
        "rsi_at_peak":        candidate["s6_rsi_at_peak"],
        "fakeout_seen":       False,
        "detected_at":        _t.time(),
        "snap_s6_peak":       round(candidate["s6_peak_level"], 8),
        "snap_s6_drop_pct":   round(candidate["s6_drop_pct"] * 100, 2),
        "snap_s6_rsi_at_peak": round(candidate["s6_rsi_at_peak"], 1),
        "snap_sentiment":     bot.sentiment.direction if bot.sentiment else "?",
        "priority_rank":      candidate.get("priority_rank", 999),
        "priority_score":     candidate.get("priority_score", 0.0),
        "expires":            _t.time() + 30 * 86400,
    }
    st.patch_pair_state(symbol, {"s6_fakeout_seen": False})
    st.save_pending_signals(bot.pending_signals)
    logger.info(
        f"[S6][{symbol}] 🕐 V-formation watcher queued | "
        f"peak={candidate['s6_peak_level']:.5f} | SL={candidate['s6_sl']:.5f}"
    )
    st.add_scan_log(
        f"[S6][{symbol}] 🕐 V-formation watcher | peak={candidate['s6_peak_level']:.5f}",
        "SIGNAL"
    )


# ── S6 Entry Watcher (pending tick) ───────────────────────── #

def handle_pending_tick(bot, symbol: str, sig: dict, balance: float,
                        paper_mode: bool | None = None) -> str | None:
    """S6 two-phase V-formation check. Return 'break' to stop outer loop."""
    import state as st
    import trader as tr
    import config

    if bot.sentiment and bot.sentiment.direction == "BULLISH":
        logger.info(f"[S6][{symbol}] 🚫 Cancelled — sentiment BULLISH")
        st.add_scan_log(f"[S6][{symbol}] 🚫 Cancelled (BULLISH)", "WARN")
        bot.pending_signals.pop(symbol, None)
        st.save_pending_signals(bot.pending_signals)
        return None
    ps = st.get_pair_state(symbol)
    if ps.get("s6_signal", "HOLD") not in ("PENDING_SHORT",):
        logger.info(f"[S6][{symbol}] 🚫 Signal gone — cancelling watcher")
        bot.pending_signals.pop(symbol, None)
        st.save_pending_signals(bot.pending_signals)
        return None
    try:
        mark = tr.get_mark_price(symbol)
    except Exception:
        return None
    peak = sig["peak_level"]
    if not sig.get("fakeout_seen"):
        if mark > peak:
            sig["fakeout_seen"] = True
            st.patch_pair_state(symbol, {"s6_fakeout_seen": True})
            st.save_pending_signals(bot.pending_signals)
            logger.info(f"[S6][{symbol}] 🚀 Phase 1 — fakeout above peak {peak:.5f}")
            st.add_scan_log(f"[S6][{symbol}] Phase 1 fakeout above {peak:.5f}", "INFO")
    else:
        if mark < peak:
            with bot._trade_lock:
                if symbol in bot.active_positions:
                    bot.pending_signals.pop(symbol, None)
                    st.save_pending_signals(bot.pending_signals)
                    return None
                if len(bot.active_positions) >= config.MAX_CONCURRENT_TRADES:
                    return "break"
                if st.is_pair_paused(symbol):
                    return None
                bot._fire_s6(symbol, sig, mark, balance)
            bot.pending_signals.pop(symbol, None)
            st.save_pending_signals(bot.pending_signals)
    return None


# ── S6 DNA Snapshot Fields ────────────────────────────────── #

def dna_fields(candles: dict) -> dict:
    """S6 trade fingerprint: daily EMA slope, price vs EMA, RSI bucket."""
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


# ── S6 Scale-In Helpers ───────────────────────────────────── #

def scale_in_specs() -> dict:
    """Per-strategy scale-in orchestration constants for S6 (SHORT)."""
    import config_s6
    return {
        "direction": "BEARISH",
        "hold_side": "short",
        "leverage":  config_s6.S6_LEVERAGE,
    }


def is_scale_in_window(ap: dict, mark_now: float) -> bool:
    """True while price is still below peak_level (fakeout reversal still valid)."""
    return mark_now < ap["box_low"]


def recompute_scale_in_sl_trigger(ap: dict, new_avg: float) -> tuple[float, float]:
    """S6 post-scale-in: SL at new_avg*(1+SL_PCT/LEVERAGE), trail at new_avg*(1-TRIG_PCT)."""
    import config_s6
    new_sl   = new_avg * (1 + config_s6.S6_SL_PCT / config_s6.S6_LEVERAGE)
    new_trig = new_avg * (1 - config_s6.S6_TRAILING_TRIGGER_PCT)
    return new_sl, new_trig
