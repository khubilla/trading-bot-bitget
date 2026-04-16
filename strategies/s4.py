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
