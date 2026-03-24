"""
strategy.py — Multi-Timeframe Breakout Strategy Engine

ENTRY CONDITIONS
─────────────────
LONG:
  1D:  price > 10 EMA > 20 EMA          (daily momentum filter)
  1H:  current HIGH > previous HIGH      (HTF bull break)
  3m:  RSI > 70                          (momentum confirmed)
  3m:  consolidation in last N candles   (coiling)
  3m:  close breaks ABOVE box + buffer   (entry trigger)

SHORT:
  1D:  price < 10 EMA < 20 EMA          (daily momentum filter)
  1H:  current LOW  < previous LOW      (HTF bear break)
  3m:  RSI < 30                          (momentum confirmed)
  3m:  consolidation in last N candles   (coiling)
  3m:  close breaks BELOW box - buffer   (entry trigger)

EXIT CONDITIONS (dynamic SL — monitored each tick)
────────────────────────────────────────────────────
LONG exit when ANY of:
  - 3m candle CLOSES below the consolidation box_low
  - 3m RSI drops below 70

SHORT exit when ANY of:
  - 3m candle CLOSES above the consolidation box_high
  - 3m RSI rises above 30
"""

import logging
import numpy as np
import pandas as pd
from typing import Literal

from config import (
    RSI_PERIOD, RSI_LONG_THRESH, RSI_SHORT_THRESH,
    CONSOLIDATION_CANDLES, CONSOLIDATION_RANGE_PCT,
    BREAKOUT_BUFFER_PCT,
)

logger = logging.getLogger(__name__)
Signal   = Literal["LONG", "SHORT", "HOLD"]
ExitFlag = Literal["EXIT", "HOLD"]


# ── EMA ───────────────────────────────────────────────────────────── #

def calculate_ema(closes: pd.Series, period: int) -> pd.Series:
    return closes.ewm(span=period, adjust=False).mean()


# ── RSI ───────────────────────────────────────────────────────────── #

def calculate_rsi(closes: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta    = closes.diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


# ── Daily EMA Filter ──────────────────────────────────────────────── #

def check_daily_ema(daily_df: pd.DataFrame, direction: str) -> tuple[bool, float, float, float]:
    """
    Checks daily EMA momentum filter.

    Returns (passes, price, ema10, ema20)

    LONG:  price > ema10 > ema20
    SHORT: price < ema10 < ema20
    """
    if len(daily_df) < 21:
        logger.debug("  Daily EMA: not enough candles")
        return False, 0.0, 0.0, 0.0

    closes = daily_df["close"].astype(float)
    ema10  = float(calculate_ema(closes, 10).iloc[-1])
    ema20  = float(calculate_ema(closes, 20).iloc[-1])
    price  = float(closes.iloc[-1])

    if direction == "LONG":
        passes = price > ema10 > ema20
    else:
        passes = price < ema10 < ema20

    logger.debug(
        f"  Daily EMA [{direction}]: price={price:.4f} "
        f"ema10={ema10:.4f} ema20={ema20:.4f} → {'✅' if passes else '❌'}"
    )
    return passes, price, ema10, ema20


# ── Consolidation ─────────────────────────────────────────────────── #

def detect_consolidation(
    ltf_df: pd.DataFrame,
    rsi_series: pd.Series | None = None,
    rsi_threshold: float | None = None,
    direction: str = "LONG",
) -> tuple[bool, float, float]:
    """
    Returns (is_consolidating, box_high, box_low).

    Consolidation is only valid if:
    1. Price range is tight (within CONSOLIDATION_RANGE_PCT)
    2. RSI was in the correct zone during ALL candles in the window:
       LONG:  RSI > rsi_threshold (>70) throughout the window
       SHORT: RSI < rsi_threshold (<30) throughout the window

    This prevents false consolidation detections at RSI 50-60.
    """
    window = ltf_df.iloc[-(CONSOLIDATION_CANDLES + 1):-1]
    if len(window) < CONSOLIDATION_CANDLES:
        return False, 0.0, 0.0

    box_high = float(window["high"].max())
    box_low  = float(window["low"].min())
    mid      = (box_high + box_low) / 2
    if mid == 0:
        return False, 0.0, 0.0

    range_pct = (box_high - box_low) / mid
    is_tight  = range_pct <= CONSOLIDATION_RANGE_PCT

    if not is_tight:
        return False, box_high, box_low

    # ── RSI zone check: was RSI in the zone for the whole window? ── #
    if rsi_series is not None and rsi_threshold is not None:
        window_rsi = rsi_series.iloc[-(CONSOLIDATION_CANDLES + 1):-1]
        if direction == "LONG":
            if not (window_rsi > rsi_threshold).all():
                logger.debug(
                    f"  Consolidation ❌ RSI not consistently > {rsi_threshold} "
                    f"during window (min={window_rsi.min():.1f})"
                )
                return False, box_high, box_low
        elif direction == "SHORT":
            if not (window_rsi < rsi_threshold).all():
                logger.debug(
                    f"  Consolidation ❌ RSI not consistently < {rsi_threshold} "
                    f"during window (max={window_rsi.max():.1f})"
                )
                return False, box_high, box_low

    logger.debug(f"  Consolidation ✓ range={range_pct*100:.3f}% H={box_high} L={box_low}")
    return True, box_high, box_low


# ── HTF Check ─────────────────────────────────────────────────────── #

def check_htf(htf_df: pd.DataFrame) -> tuple[bool, bool]:
    if len(htf_df) < 2:
        return False, False
    prev    = htf_df.iloc[-2]
    current = htf_df.iloc[-1]
    bull    = float(current["high"]) > float(prev["high"])
    bear    = float(current["low"])  < float(prev["low"])
    return bull, bear


# ── LTF Entry Checks ──────────────────────────────────────────────── #

def check_ltf_long(ltf_df: pd.DataFrame) -> tuple[bool, float, float, float]:
    """Returns (valid, rsi, box_high, box_low)"""
    if len(ltf_df) < RSI_PERIOD + CONSOLIDATION_CANDLES + 2:
        return False, 50.0, 0.0, 0.0

    closes  = ltf_df["close"].astype(float)
    rsi_ser = calculate_rsi(closes)
    rsi_val = float(rsi_ser.iloc[-1])

    if rsi_val <= RSI_LONG_THRESH:
        return False, rsi_val, 0.0, 0.0

    # Pass RSI series so consolidation only counts when RSI was > 70 throughout
    is_coil, box_high, box_low = detect_consolidation(
        ltf_df, rsi_series=rsi_ser,
        rsi_threshold=RSI_LONG_THRESH, direction="LONG"
    )
    if not is_coil:
        return False, rsi_val, 0.0, 0.0

    close = float(ltf_df["close"].iloc[-1])
    if close > box_high * (1 + BREAKOUT_BUFFER_PCT):
        return True, rsi_val, box_high, box_low

    return False, rsi_val, box_high, box_low


def check_ltf_short(ltf_df: pd.DataFrame) -> tuple[bool, float, float, float]:
    """Returns (valid, rsi, box_high, box_low)"""
    if len(ltf_df) < RSI_PERIOD + CONSOLIDATION_CANDLES + 2:
        return False, 50.0, 0.0, 0.0

    closes  = ltf_df["close"].astype(float)
    rsi_ser = calculate_rsi(closes)
    rsi_val = float(rsi_ser.iloc[-1])

    if rsi_val >= RSI_SHORT_THRESH:
        return False, rsi_val, 0.0, 0.0

    # Pass RSI series so consolidation only counts when RSI was < 30 throughout
    is_coil, box_high, box_low = detect_consolidation(
        ltf_df, rsi_series=rsi_ser,
        rsi_threshold=RSI_SHORT_THRESH, direction="SHORT"
    )
    if not is_coil:
        return False, rsi_val, 0.0, 0.0

    close = float(ltf_df["close"].iloc[-1])
    if close < box_low * (1 - BREAKOUT_BUFFER_PCT):
        return True, rsi_val, box_high, box_low

    return False, rsi_val, box_high, box_low


# ── Dynamic Exit Check ────────────────────────────────────────────── #

def check_exit(
    ltf_df: pd.DataFrame,
    side: str,
    box_high: float,
    box_low: float,
) -> tuple[ExitFlag, str]:
    """
    Called every tick while a position is open.
    Uses iloc[-2] = last FULLY CLOSED 3m candle (not the forming one).

    LONG  exit: last closed candle closed BELOW box_low
    SHORT exit: last closed candle closed ABOVE box_high

    Note: The fixed SL/TP orders on Bitget act as the primary protection.
    This is an additional early-exit check for when the setup invalidates.
    """
    if len(ltf_df) < 3:
        return "HOLD", ""

    # iloc[-1] is still forming — use iloc[-2] for the last confirmed close
    last_closed = float(ltf_df["close"].iloc[-2])

    if side == "LONG" and box_low > 0:
        if last_closed < box_low:
            return "EXIT", (
                f"Last closed 3m candle ({last_closed:.6f}) "
                f"closed below box_low ({box_low:.6f})"
            )

    elif side == "SHORT" and box_high > 0:
        if last_closed > box_high:
            return "EXIT", (
                f"Last closed 3m candle ({last_closed:.6f}) "
                f"closed above box_high ({box_high:.6f})"
            )

    return "HOLD", ""


# ── Master Entry Evaluator ────────────────────────────────────────── #

def evaluate_pair(
    symbol: str,
    htf_df: pd.DataFrame,
    ltf_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    allowed_direction: str,
) -> tuple[Signal, float, float, float]:
    """
    Full entry evaluation. Returns (signal, rsi, box_high, box_low).
    allowed_direction: "BULLISH" | "BEARISH"
    """
    bull_htf, bear_htf = check_htf(htf_df)

    if bull_htf and allowed_direction == "BULLISH":
        # Daily EMA filter
        ema_ok, price, ema10, ema20 = check_daily_ema(daily_df, "LONG")
        if not ema_ok:
            logger.debug(
                f"[{symbol}] LONG blocked by daily EMA: "
                f"price={price:.4f} ema10={ema10:.4f} ema20={ema20:.4f}"
            )
            return "HOLD", 50.0, 0.0, 0.0

        valid, rsi, box_high, box_low = check_ltf_long(ltf_df)
        if valid:
            logger.info(f"[{symbol}] ✅ LONG | RSI={rsi:.1f} | box={box_low:.5f}–{box_high:.5f}")
            return "LONG", rsi, box_high, box_low
        return "HOLD", rsi, box_high, box_low

    if bear_htf and allowed_direction == "BEARISH":
        # Daily EMA filter
        ema_ok, price, ema10, ema20 = check_daily_ema(daily_df, "SHORT")
        if not ema_ok:
            logger.debug(
                f"[{symbol}] SHORT blocked by daily EMA: "
                f"price={price:.4f} ema10={ema10:.4f} ema20={ema20:.4f}"
            )
            return "HOLD", 50.0, 0.0, 0.0

        valid, rsi, box_high, box_low = check_ltf_short(ltf_df)
        if valid:
            logger.info(f"[{symbol}] ✅ SHORT | RSI={rsi:.1f} | box={box_low:.5f}–{box_high:.5f}")
            return "SHORT", rsi, box_high, box_low
        return "HOLD", rsi, box_high, box_low

    return "HOLD", 50.0, 0.0, 0.0
