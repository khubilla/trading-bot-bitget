"""
Strategy 7 — Post-Pump 1H Darvas Breakdown Short.

Setup gates mirror S4 (spike body ≥ 20% within last 30D, RSI peak ≥ 75 within
last 10D, RSI still hot ≥ 70). Entry trigger is a confirmed 1H close below a
locked Darvas-box low formed within the current UTC day.

Sentiment gate: BEARISH only (gated upstream in bot.py).
"""

import logging
from typing import Literal

import pandas as pd

logger = logging.getLogger(__name__)
Signal = Literal["LONG", "SHORT", "HOLD", "PENDING_LONG", "PENDING_SHORT"]

SNAPSHOT_INTERVAL = "1D"


def _utcnow() -> pd.Timestamp:
    """Wrapper for monkeypatch-friendly current-UTC timestamp."""
    return pd.Timestamp.utcnow()


def today_h1_slice(h1_df: pd.DataFrame) -> pd.DataFrame:
    """Closed 1H candles since the most recent UTC midnight (forming hour excluded)."""
    if h1_df.empty:
        return h1_df
    today_utc = _utcnow().floor("1D")
    if today_utc.tzinfo is None:
        today_utc = today_utc.tz_localize("UTC")
    if isinstance(h1_df.index, pd.DatetimeIndex):
        ts = h1_df.index
    else:
        # Production shape from tr.get_candles: RangeIndex + int-ms `ts` column.
        ts = pd.to_datetime(h1_df["ts"], unit="ms", utc=True)
    mask = ts >= today_utc
    return h1_df[mask].iloc[:-1]


def detect_darvas_box(
    h1_slice: pd.DataFrame,
    confirm: int = 2,
) -> tuple[bool, float, float, int, int, str]:
    """
    Walk the 1H slice forward and lock a top-box high then a low-box low using
    classic Darvas mechanics: each new lower-low / higher-high resets the
    confirmation counter; the box locks once `confirm` consecutive candles
    hold above/below the establishing candle.

    Returns (locked, top_high, low_low, top_idx, low_idx, reason).
    """
    min_needed = 2 * confirm + 2
    if len(h1_slice) < min_needed:
        return (False, 0.0, 0.0, -1, -1,
                f"Need ≥ {min_needed} 1H candles since UTC midnight (have {len(h1_slice)})")

    rows = list(h1_slice.itertuples())

    # --- top-box pass ---
    top_high, top_idx, conf, top_locked = float("-inf"), -1, 0, False
    for i, row in enumerate(rows):
        if row.high > top_high:
            top_high, top_idx, conf = float(row.high), i, 0
        else:
            conf += 1
            if conf >= confirm:
                top_locked = True
                break
    if not top_locked:
        return (False, top_high, 0.0, top_idx, -1,
                "Top box not yet confirmed (running high still pushing)")

    # --- low-box pass over rows after top_idx ---
    low_low, low_off, conf, low_locked = float("+inf"), -1, 0, False
    for j, row in enumerate(rows[top_idx + 1:]):
        if row.low < low_low:
            low_low, low_off, conf = float(row.low), j, 0
        else:
            conf += 1
            if conf >= confirm:
                low_locked = True
                break
    if not low_locked:
        return (False, top_high, low_low, top_idx, -1,
                "Low box not yet confirmed (running low still falling)")

    if low_low >= top_high:
        return (False, top_high, low_low, top_idx, top_idx + 1 + low_off,
                f"Sanity: low_low {low_low} >= top_high {top_high}")

    low_idx = top_idx + 1 + low_off
    return (True, top_high, low_low, top_idx, low_idx,
            f"Darvas box ✅ top={top_high} low={low_low}")


def evaluate_s7(
    symbol: str,
    daily_df: pd.DataFrame,
    h1_df: pd.DataFrame | None = None,
) -> tuple[Signal, float, float, float, float, float, bool, str, str]:
    """
    Strategy 7 — post-pump 1H Darvas breakdown short.

    Returns (signal, daily_rsi, box_top, box_low, body_pct, rsi_peak,
             rsi_div, rsi_div_str, reason).
    """
    from indicators import calculate_rsi
    from tools import body_pct as _body_pct
    from config_s7 import (
        S7_ENABLED, S7_BIG_CANDLE_BODY_PCT, S7_BIG_CANDLE_LOOKBACK,
        S7_RSI_PEAK_THRESH, S7_RSI_PEAK_LOOKBACK, S7_RSI_DIV_MIN_DROP,
        S7_RSI_STILL_HOT_THRESH, S7_BOX_CONFIRM_COUNT,
    )

    if not S7_ENABLED:
        return "HOLD", 50.0, 0.0, 0.0, 0.0, 0.0, False, "", "S7 disabled"

    rsi_period = 14
    min_candles = rsi_period + S7_BIG_CANDLE_LOOKBACK + 2
    if len(daily_df) < min_candles:
        return "HOLD", 50.0, 0.0, 0.0, 0.0, 0.0, False, "", "Not enough daily candles"

    closes    = daily_df["close"].astype(float)
    rsi_ser   = calculate_rsi(closes, rsi_period)
    daily_rsi = float(rsi_ser.iloc[-1])

    # --- spike detection ---
    lookback = daily_df.iloc[-(S7_BIG_CANDLE_LOOKBACK + 1):-1]
    spike_found, best_body, spike_high = False, 0.0, 0.0
    for _, row in lookback.iterrows():
        bp = _body_pct(row)
        if bp >= S7_BIG_CANDLE_BODY_PCT:
            spike_found = True
            if bp > best_body:
                best_body = bp
        if spike_found:
            spike_high = max(spike_high, float(row["high"]))
    if not spike_found:
        return "HOLD", daily_rsi, 0.0, 0.0, 0.0, 0.0, False, "", (
            f"No spike candle ≥{S7_BIG_CANDLE_BODY_PCT*100:.0f}% body in last {S7_BIG_CANDLE_LOOKBACK}d"
        )

    # --- RSI peak gate ---
    rsi_window = rsi_ser.iloc[-S7_RSI_PEAK_LOOKBACK - 1:-1]
    rsi_peak   = float(rsi_window.max())
    if rsi_peak < S7_RSI_PEAK_THRESH:
        return "HOLD", daily_rsi, 0.0, 0.0, best_body, rsi_peak, False, "", (
            f"Spike ✅ body={best_body*100:.0f}% | RSI peak={rsi_peak:.1f} < {S7_RSI_PEAK_THRESH}"
        )

    # --- RSI still hot ---
    prev_rsi = float(rsi_ser.iloc[-2])
    if prev_rsi < S7_RSI_STILL_HOT_THRESH:
        return "HOLD", daily_rsi, 0.0, 0.0, best_body, rsi_peak, False, "", (
            f"Spike ✅ RSI peak={rsi_peak:.1f} | prev RSI={prev_rsi:.1f} < {S7_RSI_STILL_HOT_THRESH} (faded)"
        )

    # --- RSI divergence (informational) ---
    rsi_div, rsi_div_str, div_note = False, "", ""
    if len(rsi_window) >= 4:
        mid      = len(rsi_window) // 2
        first_h  = float(rsi_window.iloc[:mid].max())
        second_h = float(rsi_window.iloc[mid:].max())
        rsi_div_str = f"{first_h:.1f}→{second_h:.1f}"
        if first_h > 0 and (first_h - second_h) >= S7_RSI_DIV_MIN_DROP:
            rsi_div, div_note = True, f" | RSI div ✅ ({rsi_div_str})"
        else:
            div_note = f" | RSI div ❌ ({rsi_div_str})"

    # --- 1H Darvas detector ---
    if h1_df is None or h1_df.empty:
        return "HOLD", daily_rsi, 0.0, 0.0, best_body, rsi_peak, rsi_div, rsi_div_str, (
            f"S7 daily ✅ spike={best_body*100:.0f}% | RSI peak={rsi_peak:.1f}{div_note} | 1H Darvas ❌ no H1 data"
        )
    today_slice = today_h1_slice(h1_df)
    locked, box_top, box_low, _, _, det_reason = detect_darvas_box(today_slice, confirm=S7_BOX_CONFIRM_COUNT)
    if not locked:
        return "HOLD", daily_rsi, 0.0, 0.0, best_body, rsi_peak, rsi_div, rsi_div_str, (
            f"S7 daily ✅ spike={best_body*100:.0f}% | RSI peak={rsi_peak:.1f}{div_note} | 1H Darvas ❌ {det_reason}"
        )

    logger.info(
        f"[S7][{symbol}] ✅ SHORT setup | spike={best_body*100:.0f}% | "
        f"RSI peak={rsi_peak:.1f} now={daily_rsi:.1f}{div_note} | "
        f"Darvas top={box_top:.5f} low={box_low:.5f}"
    )
    return "SHORT", daily_rsi, box_top, box_low, best_body, rsi_peak, rsi_div, rsi_div_str, (
        f"S7 ✅ spike={best_body*100:.0f}% | RSI peak={rsi_peak:.1f}{div_note} | "
        f"Darvas top={box_top:.5f} low={box_low:.5f}"
    )


# ── S7 DNA Snapshot Fields ────────────────────────────────── #

def dna_fields(candles: dict) -> dict:
    """S7 trade fingerprint: daily EMA/RSI, optional H1 EMA. Mirrors S4."""
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


# ── S7 Paper Trail Setup ──────────────────────────────────── #

def compute_paper_trail_short(mark: float, sl_price: float, tp_price_abs: float = 0,
                              take_profit_pct: float = 0.05) -> tuple[bool, float, float, float, bool]:
    """Paper-trader SHORT trail setup for S7. Returns (use_trailing, trail_trigger, trail_range, tp_price, breakeven_after_partial)."""
    from config_s7 import S7_TRAILING_TRIGGER_PCT, S7_TRAILING_RANGE_PCT
    trail_trigger = mark * (1 - S7_TRAILING_TRIGGER_PCT)
    trail_range   = S7_TRAILING_RANGE_PCT
    return True, trail_trigger, trail_range, trail_trigger, False


# ── S7 Scale-In Helpers ───────────────────────────────────── #

def scale_in_specs() -> dict:
    """Per-strategy scale-in orchestration constants for S7 (SHORT)."""
    import config_s7
    return {
        "direction": "BEARISH",
        "hold_side": "short",
        "leverage":  config_s7.S7_LEVERAGE,
    }


def is_scale_in_window(ap: dict, mark_now: float) -> bool:
    """True when price is retesting the S7 box-low breakdown level."""
    import config_s7
    bl = ap["s7_box_low"]
    return (bl * (1 - config_s7.S7_MAX_ENTRY_BUFFER)
            <= mark_now
            <= bl * (1 - config_s7.S7_ENTRY_BUFFER))


def recompute_scale_in_sl_trigger(ap: dict, new_avg: float) -> tuple[float, float]:
    """S7 post-scale-in: SL at new_avg*(1+0.50/LEVERAGE), trail at new_avg*(1-TRIG_PCT)."""
    import config_s7
    new_sl   = new_avg * (1 + 0.50 / config_s7.S7_LEVERAGE)
    new_trig = new_avg * (1 - config_s7.S7_TRAILING_TRIGGER_PCT)
    return new_sl, new_trig


# ── S7 Exit Placement ─────────────────────────────────────── #

def compute_and_place_short_exits(symbol: str, qty_str: str, fill: float,
                                  sl_trig: float, sl_exec: float) -> tuple[bool, float, float]:
    """
    Compute S7 short-side trail level and place the 3-leg exits
    (SL + 50% partial at trail_trigger + trailing stop on remainder).
    Returns (ok, sl_trig, trail_trig).
    """
    import trader
    from strategies.s4 import _place_partial_trail_exits
    from config_s7 import S7_TRAILING_TRIGGER_PCT, S7_TRAILING_RANGE_PCT

    trail_trig = float(trader._round_price(fill * (1 - S7_TRAILING_TRIGGER_PCT), symbol))
    ok = _place_partial_trail_exits(symbol, "short", qty_str, sl_trig, sl_exec,
                                    trail_trig, S7_TRAILING_RANGE_PCT)
    return ok, sl_trig, trail_trig


# ── S7 Swing Trail ────────────────────────────────────────── #

def maybe_trail_sl(symbol: str, ap: dict, tr_mod, st_mod, partial_done: bool) -> None:
    """
    Structural swing trail for S7 SHORT: after partial fires, pull SL down to the
    nearest daily swing-high above entry. Mirrors strategies.s4.maybe_trail_sl.
    """
    import config_s7
    from tools import find_swing_low_target, find_swing_high_after_ref

    if not config_s7.S7_USE_SWING_TRAIL:
        return
    if ap.get("side") != "SHORT" or not partial_done:
        return
    try:
        lb    = config_s7.S7_SWING_LOOKBACK
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
                swing_sl = raw * (1 + config_s7.S7_ENTRY_BUFFER)
                if swing_sl < ap.get("sl", float("inf")) and tr_mod.update_position_sl(symbol, swing_sl, hold_side="short"):
                    ap["sl"] = swing_sl
                    st_mod.update_open_trade_sl(symbol, swing_sl)
                    ap["swing_trail_ref"] = find_swing_low_target(cs_df, mark, lookback=lb)
                    logger.info(f"[S7][{symbol}] 📍 Swing trail: SL → {swing_sl:.5f} (daily swing high after ref low {ref:.5f})")
    except Exception as e:
        logger.error(f"S7 swing trail error [{symbol}]: {e}")


# ── S7 Pending-Signal Queue ───────────────────────────────── #

def queue_pending(bot, c: dict) -> None:
    """Queue an S7 SHORT spike-reversal pending signal for the entry watcher."""
    import time as _t
    import state as st
    import config_s7

    symbol      = c["symbol"]
    s7_trigger  = c["s7_trigger"]
    s7_sl       = c["s7_sl"]
    box_top     = c["s7_box_top"]
    box_low     = c["s7_box_low"]
    bot.pending_signals[symbol] = {
        "strategy":             "S7",
        "side":                 "SHORT",
        "trigger":              s7_trigger,
        "s7_sl":                s7_sl,
        "box_low":              box_low,
        "box_top":              box_top,
        "priority_rank":        c.get("priority_rank", 999),
        "priority_score":       c.get("priority_score", 0.0),
        "snap_rsi":             round(c["s7_rsi"], 1),
        "snap_rsi_peak":        round(c["s7_rsi_peak"], 1),
        "snap_spike_body_pct":  round(c["s7_body_pct"] * 100, 1),
        "snap_rsi_div":         c["s7_div"],
        "snap_rsi_div_str":     c["s7_div_str"],
        "snap_box_top":         box_top,
        "snap_box_low_initial": box_low,
        "snap_sentiment":       bot.sentiment.direction if bot.sentiment else "?",
        "expires":              _t.time() + 86400,
    }
    st.save_pending_signals(bot.pending_signals)
    logger.info(
        f"[S7][{symbol}] 🕐 PENDING SHORT queued | trigger≤{s7_trigger:.5f} | "
        f"box top={box_top:.5f} low={box_low:.5f} | SL={s7_sl:.5f}"
    )
    st.add_scan_log(
        f"[S7][{symbol}] 🕐 PENDING SHORT | trigger≤{s7_trigger:.5f}", "SIGNAL"
    )


# ── S7 Entry Watcher (pending tick) ───────────────────────── #

def handle_pending_tick(bot, symbol: str, sig: dict, balance: float,
                        paper_mode: bool | None = None) -> str | None:
    """
    S7 entry-watcher tick. Returns 'break' to stop outer loop (concurrency cap),
    None otherwise. Fires SHORT only on a confirmed 1H *close* below box_low.
    """
    import state as st
    import trader as tr
    import config, config_s7

    ps = st.get_pair_state(symbol)
    if ps.get("s7_signal", "HOLD") not in ("SHORT",):
        logger.info(f"[S7][{symbol}] 🚫 Signal gone — cancelling pending")
        st.add_scan_log(f"[S7][{symbol}] 🚫 Pending cancelled (signal gone)", "INFO")
        bot.pending_signals.pop(symbol, None)
        st.save_pending_signals(bot.pending_signals)
        return None

    # Re-run Darvas detector on fresh 1H candles
    try:
        h1_df = tr.get_candles(symbol, "1H", limit=48)
    except Exception:
        return None
    today_slice = today_h1_slice(h1_df)
    locked, box_top, box_low, _, _, _ = detect_darvas_box(
        today_slice, confirm=config_s7.S7_BOX_CONFIRM_COUNT
    )
    if not locked:
        return None  # still pending; do not cancel

    # Update pending fields if box has expanded (wick-and-reclaim)
    if box_low != sig["box_low"] or box_top != sig.get("box_top"):
        sig["box_low"] = box_low
        sig["box_top"] = box_top
        sig["trigger"] = box_low * (1 - config_s7.S7_ENTRY_BUFFER)
        st.save_pending_signals(bot.pending_signals)

    try:
        mark = tr.get_mark_price(symbol)
    except Exception:
        return None

    # SL invalidation
    if mark > sig["s7_sl"]:
        logger.info(f"[S7][{symbol}] ❌ Invalidated — mark {mark:.5f} > SL {sig['s7_sl']:.5f}")
        st.add_scan_log(f"[S7][{symbol}] ❌ Pending cancelled (price above SL)", "INFO")
        bot.pending_signals.pop(symbol, None)
        st.save_pending_signals(bot.pending_signals)
        return None

    # Confirmed-close trigger: latest CLOSED 1H must close below box_low
    if len(h1_df) < 2:
        return None
    last_closed_close = float(h1_df.iloc[-2]["close"])
    if last_closed_close >= box_low:
        return None  # not yet broken on close

    # Stale-entry guard
    s7_trigger = sig["trigger"]
    in_window  = (mark <= s7_trigger and
                  mark >= box_low * (1 - config_s7.S7_MAX_ENTRY_BUFFER))
    if not in_window:
        return None  # don't chase

    with bot._trade_lock:
        if symbol in bot.active_positions:
            bot.pending_signals.pop(symbol, None)
            st.save_pending_signals(bot.pending_signals)
            return None
        if len(bot.active_positions) >= config.MAX_CONCURRENT_TRADES:
            return "break"
        if st.is_pair_paused(symbol):
            return None
        bot._fire_s7(symbol, sig, mark, balance)
    bot.pending_signals.pop(symbol, None)
    st.save_pending_signals(bot.pending_signals)
    return None
