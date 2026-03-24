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

    rsi_val = float(calculate_rsi(ltf_df["close"].astype(float)).iloc[-1])

    if rsi_val <= RSI_LONG_THRESH:
        return False, rsi_val, 0.0, 0.0

    is_coil, box_high, box_low = detect_consolidation(ltf_df)
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

    rsi_val = float(calculate_rsi(ltf_df["close"].astype(float)).iloc[-1])

    if rsi_val >= RSI_SHORT_THRESH:
        return False, rsi_val, 0.0, 0.0

    is_coil, box_high, box_low = detect_consolidation(ltf_df)
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
    Checks if the dynamic SL conditions are met.

    LONG exit:
      - Current 3m candle CLOSED below box_low
      - RSI dropped below 70

    SHORT exit:
      - Current 3m candle CLOSED above box_high
      - RSI rose above 30

    Returns (flag, reason)
    """
    if len(ltf_df) < RSI_PERIOD + 2:
        return "HOLD", ""

    closes  = ltf_df["close"].astype(float)
    rsi_val = float(calculate_rsi(closes).iloc[-1])
    close   = float(closes.iloc[-1])

    if side == "LONG":
        if box_low > 0 and close < box_low:
            return "EXIT", f"Candle closed below box_low ({close:.5f} < {box_low:.5f})"
        if rsi_val < RSI_LONG_THRESH:
            return "EXIT", f"RSI dropped below {RSI_LONG_THRESH} (RSI={rsi_val:.1f})"

    elif side == "SHORT":
        if box_high > 0 and close > box_high:
            return "EXIT", f"Candle closed above box_high ({close:.5f} > {box_high:.5f})"
        if rsi_val > RSI_SHORT_THRESH:
            return "EXIT", f"RSI rose above {RSI_SHORT_THRESH} (RSI={rsi_val:.1f})"

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
