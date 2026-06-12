"""
Strategy 8 — Post-S2 Bounce: breakout-retest at tri-confluence support.

Phase: after an S2-style daily breakout that failed to continue upward.
Price retraces to a zone where three supports cluster:
  1. The Darvas coil box top that formed BEFORE the S2 breakout
  2. The daily 20MA
  3. The 61.8% fib retracement of the impulse leg (coil box low → post-breakout swing high)
A small green daily candle resting on the zone arms a stop-buy above its high.

LONG only. Daily candles only. Exits copy S2 (preset SL, 50% partial TP at +10%,
10% trailing callback on the remainder). Single full-size entry — NO scale-in.
"""

import logging
from typing import Literal

import pandas as pd

from indicators import calculate_rsi
from tools import body_pct, upper_wick

logger = logging.getLogger(__name__)
Signal = Literal["LONG", "HOLD"]

# Default candle interval for S8 event snapshots.
SNAPSHOT_INTERVAL = "1D"

_HOLD = ("HOLD", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def _eff_top(row, wick_pct: float) -> float:
    """Darvas effective top: body top when the upper wick is rejected, else wick high."""
    bt = max(float(row["close"]), float(row["open"]))
    return bt if upper_wick(row) > wick_pct * bt else float(row["high"])


def _coil_before(daily_df: pd.DataFrame, b: int, big_top: float, cfg) -> tuple[float, float] | None:
    """
    Tight Darvas coil (1..S8_CONSOL_CANDLES candles) ending right before index b.
    Same effective-top/body-bottom math as S2, including S2's containment rule:
    every coil candle must close at/below the big momentum candle's body top —
    this is what stops impulse-continuation candles from registering as B.
    Returns (box_top, box_low) or None.
    """
    for n in range(1, cfg.S8_CONSOL_CANDLES + 1):
        if b - n < 0:
            return None
        window = daily_df.iloc[b - n:b]
        if not all(float(r["close"]) <= big_top for _, r in window.iterrows()):
            continue
        eff_tops = window.apply(lambda r: _eff_top(r, cfg.S8_DARVAS_WICK_PCT), axis=1)
        eff_h = float(eff_tops.max())
        eff_l = float(window.apply(
            lambda r: min(float(r["close"]), float(r["open"])), axis=1).min())
        if eff_h <= 0:
            continue
        if (eff_h - eff_l) / eff_h > cfg.S8_CONSOL_RANGE_PCT:
            continue
        return eff_h, eff_l
    return None


def _find_structure(daily_df: pd.DataFrame, rsi_ser: pd.Series, cfg) -> dict | None:
    """
    Most recent S2-like breakout day B within the last S8_PHASE_LOOKBACK completed
    candles: big momentum candle within S8_BIG_CANDLE_LOOKBACK days before B,
    tight coil right before B (all coil closes ≤ big candle body top),
    RSI > S8_RSI_THRESH on B, and B's close above the coil box top.
    iloc[-1] is the live forming candle and is never B.
    """
    last_completed = len(daily_df) - 2
    earliest = max(cfg.S8_CONSOL_CANDLES + 1,
                   last_completed - cfg.S8_PHASE_LOOKBACK + 1)
    for b in range(last_completed, earliest - 1, -1):
        if float(rsi_ser.iloc[b]) <= cfg.S8_RSI_THRESH:
            continue
        lb_start = max(0, b - cfg.S8_BIG_CANDLE_LOOKBACK)
        big_top = 0.0
        for _, row in daily_df.iloc[lb_start:b].iterrows():
            if body_pct(row) >= cfg.S8_BIG_CANDLE_BODY_PCT:
                big_top = max(big_top, float(row["close"]), float(row["open"]))
        if big_top <= 0:
            continue
        coil = _coil_before(daily_df, b, big_top, cfg)
        if coil is None:
            continue
        box_top, box_low = coil
        if float(daily_df["close"].iloc[b]) <= box_top:
            continue
        return {"b": b, "box_top": box_top, "box_low": box_low,
                "rsi_b": float(rsi_ser.iloc[b])}
    return None


def evaluate_s8(
    symbol: str,
    daily_df: pd.DataFrame,
) -> tuple[Signal, float, float, float, float, float, float, float, float, str]:
    """
    Strategy 8 — purely on daily candles. LONG only.
    Returns (signal, rsi_at_breakout, entry_trigger, green_low,
             zone_low, zone_high, box_top, ma, fib, reason)
    """
    import config_s8 as cfg

    if not cfg.S8_ENABLED:
        return (*_HOLD, "S8 disabled")

    rsi_period = 14
    min_candles = max(
        rsi_period + cfg.S8_BIG_CANDLE_LOOKBACK + cfg.S8_PHASE_LOOKBACK
        + cfg.S8_CONSOL_CANDLES + 2,
        cfg.S8_MA_PERIOD + 2,
    )
    if daily_df is None or len(daily_df) < min_candles:
        return (*_HOLD, "Not enough daily candles")

    closes = daily_df["close"].astype(float)
    rsi_ser = calculate_rsi(closes, rsi_period)

    s = _find_structure(daily_df, rsi_ser, cfg)
    if s is None:
        return (*_HOLD,
                f"No post-S2 structure (big candle + coil + RSI>{cfg.S8_RSI_THRESH} "
                f"breakout) in last {cfg.S8_PHASE_LOOKBACK}d")

    b, box_top, box_low, rsi_b = s["b"], s["box_top"], s["box_low"], s["rsi_b"]

    # Impulse leg: breakout day B through the last completed candle
    swing_high = float(daily_df["high"].iloc[b:-1].max())
    if swing_high <= box_top * (1 + cfg.S8_MIN_EXTENSION):
        return (*_HOLD,
                f"Breakout leg too small — swing high {swing_high:.5f} "
                f"< box_top +{cfg.S8_MIN_EXTENSION*100:.0f}%")

    fib = swing_high - cfg.S8_FIB_RETRACE * (swing_high - box_low)

    closes_completed = closes.iloc[:-1]
    if cfg.S8_MA_TYPE.upper() == "EMA":
        from indicators import calculate_ema
        ma = float(calculate_ema(closes_completed, cfg.S8_MA_PERIOD).iloc[-1])
    else:
        ma = float(closes_completed.rolling(cfg.S8_MA_PERIOD).mean().iloc[-1])

    levels = sorted([box_top, ma, fib])
    zone_low, zone_high = levels[0], levels[2]
    width = (zone_high - zone_low) / zone_high
    if width > cfg.S8_CONFLUENCE_TOL:
        return ("HOLD", rsi_b, 0.0, 0.0, 0.0, 0.0, box_top, ma, fib,
                f"No tri-confluence — box_top={box_top:.5f} ma={ma:.5f} "
                f"fib={fib:.5f} spread {width*100:.1f}% > {cfg.S8_CONFLUENCE_TOL*100:.0f}%")

    green = daily_df.iloc[-2]   # last COMPLETED daily candle
    g_open, g_close = float(green["open"]), float(green["close"])
    g_low, g_high = float(green["low"]), float(green["high"])
    if g_close <= g_open:
        return ("HOLD", rsi_b, 0.0, 0.0, zone_low, zone_high, box_top, ma, fib,
                "Confluence ✅ — last completed candle is red, waiting green bounce")
    gb = body_pct(green)
    if gb > cfg.S8_SMALL_BODY_PCT:
        return ("HOLD", rsi_b, 0.0, 0.0, zone_low, zone_high, box_top, ma, fib,
                f"Confluence ✅ — green candle body {gb*100:.1f}% "
                f"> {cfg.S8_SMALL_BODY_PCT*100:.0f}% (not small)")
    if not (zone_low <= g_low <= zone_high * (1 + cfg.S8_PROXIMITY)):
        return ("HOLD", rsi_b, 0.0, 0.0, zone_low, zone_high, box_top, ma, fib,
                f"Confluence ✅ — green candle low {g_low:.5f} not sitting on "
                f"zone [{zone_low:.5f}, {zone_high:.5f}]")

    trigger = g_high * (1 + cfg.S8_BREAKOUT_BUFFER)
    logger.info(
        f"[S8][{symbol}] ✅ LONG | box_top={box_top:.5f} ma={ma:.5f} fib={fib:.5f} "
        f"zone=[{zone_low:.5f},{zone_high:.5f}] | green low={g_low:.5f} "
        f"body={gb*100:.1f}% | trigger={trigger:.5f}"
    )
    return ("LONG", rsi_b, trigger, g_low, zone_low, zone_high, box_top, ma, fib,
            f"S8 ✅ tri-confluence bounce | zone {zone_low:.5f}–{zone_high:.5f} "
            f"(width {width*100:.1f}%) | green body {gb*100:.1f}% | "
            f"buy > {trigger:.5f}")
