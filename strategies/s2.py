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
