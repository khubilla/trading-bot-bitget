"""
strategy.py — Multi-Timeframe Breakout Strategy Engine
(Exchange-agnostic — works with any OHLCV DataFrame)

LONG setup:
  1H:  current HIGH > previous HIGH  (bullish HTF break)
  3m:  RSI > 70                      (momentum confirmed)
  3m:  consolidation detected         (tight coiling range)
  3m:  close BREAKS ABOVE box        (entry trigger)

SHORT setup:
  1H:  current LOW  < previous LOW   (bearish HTF break)
  3m:  RSI < 30                      (momentum confirmed)
  3m:  consolidation detected         (tight coiling range)
  3m:  close BREAKS BELOW box        (entry trigger)
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
Signal = Literal["LONG", "SHORT", "HOLD"]


# ── RSI ───────────────────────────────────────────────────────────── #

def calculate_rsi(closes: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta    = closes.diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


# ── Consolidation ─────────────────────────────────────────────────── #

def detect_consolidation(ltf_df: pd.DataFrame) -> tuple[bool, float, float]:
    """
    Returns (is_consolidating, box_high, box_low) for the last N completed candles.
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

    if is_tight:
        logger.debug(f"  Consolidation: range={range_pct*100:.3f}% H={box_high} L={box_low}")

    return is_tight, box_high, box_low


# ── HTF Check ────────────────────────────────────────────────────── #

def check_htf(htf_df: pd.DataFrame) -> tuple[bool, bool]:
    """
    Returns (bullish_break, bearish_break) by comparing last two 1H candles.
    """
    if len(htf_df) < 2:
        return False, False

    prev    = htf_df.iloc[-2]
    current = htf_df.iloc[-1]
    bull    = float(current["high"]) > float(prev["high"])
    bear    = float(current["low"])  < float(prev["low"])
    return bull, bear


# ── LTF Signal Checks ─────────────────────────────────────────────── #

def check_ltf_long(ltf_df: pd.DataFrame) -> tuple[bool, float]:
    if len(ltf_df) < RSI_PERIOD + CONSOLIDATION_CANDLES + 2:
        return False, 50.0

    rsi_val = float(calculate_rsi(ltf_df["close"].astype(float)).iloc[-1])

    if rsi_val <= RSI_LONG_THRESH:
        logger.debug(f"  LONG invalid: RSI={rsi_val:.1f} ≤ {RSI_LONG_THRESH}")
        return False, rsi_val

    is_coil, box_high, _ = detect_consolidation(ltf_df)
    if not is_coil:
        return False, rsi_val

    close = float(ltf_df["close"].iloc[-1])
    if close > box_high * (1 + BREAKOUT_BUFFER_PCT):
        logger.debug(f"  LONG breakout: close={close:.4f} > box_high={box_high:.4f}")
        return True, rsi_val

    return False, rsi_val


def check_ltf_short(ltf_df: pd.DataFrame) -> tuple[bool, float]:
    if len(ltf_df) < RSI_PERIOD + CONSOLIDATION_CANDLES + 2:
        return False, 50.0

    rsi_val = float(calculate_rsi(ltf_df["close"].astype(float)).iloc[-1])

    if rsi_val >= RSI_SHORT_THRESH:
        logger.debug(f"  SHORT invalid: RSI={rsi_val:.1f} ≥ {RSI_SHORT_THRESH}")
        return False, rsi_val

    is_coil, _, box_low = detect_consolidation(ltf_df)
    if not is_coil:
        return False, rsi_val

    close = float(ltf_df["close"].iloc[-1])
    if close < box_low * (1 - BREAKOUT_BUFFER_PCT):
        logger.debug(f"  SHORT breakout: close={close:.4f} < box_low={box_low:.4f}")
        return True, rsi_val

    return False, rsi_val


# ── Master Evaluator ──────────────────────────────────────────────── #

def evaluate_pair(symbol: str, htf_df: pd.DataFrame, ltf_df: pd.DataFrame) -> tuple[Signal, float]:
    bull_htf, bear_htf = check_htf(htf_df)

    if bull_htf:
        valid, rsi = check_ltf_long(ltf_df)
        if valid:
            logger.info(f"[{symbol}] ✅ LONG signal | RSI={rsi:.1f}")
            return "LONG", rsi
        return "HOLD", rsi if 'rsi' in dir() else 50.0

    if bear_htf:
        valid, rsi = check_ltf_short(ltf_df)
        if valid:
            logger.info(f"[{symbol}] ✅ SHORT signal | RSI={rsi:.1f}")
            return "SHORT", rsi
        return "HOLD", rsi if 'rsi' in dir() else 50.0

    return "HOLD", 50.0
