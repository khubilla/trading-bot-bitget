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
