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
    mask = h1_df.index >= today_utc
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
