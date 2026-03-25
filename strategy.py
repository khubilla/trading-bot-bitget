"""
strategy.py — Strategy Engine (Strategy 1 + Strategy 2)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STRATEGY 1 — Multi-Timeframe RSI Breakout
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Entry filters:
  1D:  ADX > 25 (trending, not sideways)
  1H:  current HIGH > previous HIGH (bull) / LOW < prev LOW (bear)
  3m:  RSI > 70 (long) or < 30 (short)
  3m:  Consolidation — AND RSI must have been in zone throughout
  3m:  Candle closes above/below box + buffer

Exit (SL placed as Bitget order at box_low / box_high):
  TP: entry ± TAKE_PROFIT_PCT placed on Bitget

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STRATEGY 2 — 30-Day Breakout + 3m Consolidation
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Entry filters:
  1D:  30-day tight consolidation then big candle(s) ≥20% body breakout
  3m:  RSI > 70
  3m:  Tight consolidation of highs (RSI must be >70 throughout)
  3m:  Body breakout above box (above wick if prev high = long wick)

Risk: 10x, 5% margin, SL=box_low(-0.1%), TP=entry+10%
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


# ════════════════════════════════════════════════════════════
#  SHARED INDICATORS
# ════════════════════════════════════════════════════════════

def calculate_ema(closes: pd.Series, period: int) -> pd.Series:
    return closes.ewm(span=period, adjust=False).mean()


def calculate_rsi(closes: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta    = closes.diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calculate_adx(df: pd.DataFrame, period: int = 14) -> dict:
    """
    Calculates ADX, +DI, -DI.
    Returns dict with keys: adx, plus_di, minus_di (all pd.Series)
    ADX > 25 = trending, < 20 = sideways.
    """
    high  = df["high"].astype(float)
    low   = df["low"].astype(float)
    close = df["close"].astype(float)

    prev_high  = high.shift(1)
    prev_low   = low.shift(1)
    prev_close = close.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    up_move   = high - prev_high
    down_move = prev_low - low

    plus_dm  = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=df.index
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=df.index
    )

    atr_smooth     = tr.ewm(span=period, adjust=False).mean()
    plus_di  = 100 * plus_dm.ewm(span=period, adjust=False).mean()  / atr_smooth.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(span=period, adjust=False).mean() / atr_smooth.replace(0, np.nan)

    dx  = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(span=period, adjust=False).mean()

    return {"adx": adx, "plus_di": plus_di, "minus_di": minus_di}


# ════════════════════════════════════════════════════════════
#  STRATEGY 1 COMPONENTS
# ════════════════════════════════════════════════════════════

# ── Daily Trend Filter (ADX-based) ────────────────────────── #

def check_daily_trend(daily_df: pd.DataFrame, direction: str) -> tuple[bool, float]:
    """
    Replaces EMA filter. Uses ADX to confirm trending (not sideways).

    Rules:
      LONG:  ADX > ADX_TREND_THRESHOLD AND last daily close > EMA20
             (trending up, not ranging)
      SHORT: ADX > ADX_TREND_THRESHOLD AND last daily close < EMA20
             (trending down, not ranging)

    Returns (passes, adx_value)
    """
    from config import ADX_TREND_THRESHOLD, DAILY_EMA_SLOW

    if len(daily_df) < 30:
        logger.debug("  Daily trend: not enough candles")
        return False, 0.0

    closes  = daily_df["close"].astype(float)
    adx_res = calculate_adx(daily_df)
    adx_val = float(adx_res["adx"].iloc[-1])
    ema20   = float(calculate_ema(closes, DAILY_EMA_SLOW).iloc[-1])
    price   = float(closes.iloc[-1])

    if direction == "LONG":
        passes = adx_val > ADX_TREND_THRESHOLD and price > ema20
    else:
        passes = adx_val > ADX_TREND_THRESHOLD and price < ema20

    logger.debug(
        f"  Daily trend [{direction}]: ADX={adx_val:.1f} "
        f"(need >{ADX_TREND_THRESHOLD}) price={'above' if price > ema20 else 'below'} EMA20 "
        f"→ {'✅' if passes else '❌'}"
    )
    return passes, adx_val


# ── HTF Check (1H) ────────────────────────────────────────── #

def check_htf(htf_df: pd.DataFrame) -> tuple[bool, bool]:
    if len(htf_df) < 2:
        return False, False
    prev    = htf_df.iloc[-2]
    current = htf_df.iloc[-1]
    bull    = float(current["high"]) > float(prev["high"])
    bear    = float(current["low"])  < float(prev["low"])
    return bull, bear


# ── Consolidation (RSI-zone aware) ────────────────────────── #

def detect_consolidation(
    ltf_df: pd.DataFrame,
    rsi_series: pd.Series | None = None,
    rsi_threshold: float | None = None,
    direction: str = "LONG",
) -> tuple[bool, float, float]:
    """
    Returns (is_consolidating, box_high, box_low).
    Only valid if price range is tight AND RSI was in the zone throughout.
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
    if range_pct > CONSOLIDATION_RANGE_PCT:
        return False, box_high, box_low

    if rsi_series is not None and rsi_threshold is not None:
        window_rsi = rsi_series.iloc[-(CONSOLIDATION_CANDLES + 1):-1]
        if direction == "LONG" and not (window_rsi > rsi_threshold).all():
            logger.debug(f"  Consolidation ❌ RSI not >  {rsi_threshold} throughout (min={window_rsi.min():.1f})")
            return False, box_high, box_low
        if direction == "SHORT" and not (window_rsi < rsi_threshold).all():
            logger.debug(f"  Consolidation ❌ RSI not < {rsi_threshold} throughout (max={window_rsi.max():.1f})")
            return False, box_high, box_low

    logger.debug(f"  Consolidation ✓ range={range_pct*100:.3f}% H={box_high} L={box_low}")
    return True, box_high, box_low


# ── LTF Entry ─────────────────────────────────────────────── #

def check_ltf_long(ltf_df: pd.DataFrame) -> tuple[bool, float, float, float]:
    if len(ltf_df) < RSI_PERIOD + CONSOLIDATION_CANDLES + 2:
        return False, 50.0, 0.0, 0.0

    closes  = ltf_df["close"].astype(float)
    rsi_ser = calculate_rsi(closes)
    rsi_val = float(rsi_ser.iloc[-1])

    if rsi_val <= RSI_LONG_THRESH:
        return False, rsi_val, 0.0, 0.0

    is_coil, box_high, box_low = detect_consolidation(
        ltf_df, rsi_series=rsi_ser, rsi_threshold=RSI_LONG_THRESH, direction="LONG"
    )
    if not is_coil:
        return False, rsi_val, 0.0, 0.0

    close = float(ltf_df["close"].iloc[-1])
    if close > box_high * (1 + BREAKOUT_BUFFER_PCT):
        return True, rsi_val, box_high, box_low
    return False, rsi_val, box_high, box_low


def check_ltf_short(ltf_df: pd.DataFrame) -> tuple[bool, float, float, float]:
    if len(ltf_df) < RSI_PERIOD + CONSOLIDATION_CANDLES + 2:
        return False, 50.0, 0.0, 0.0

    closes  = ltf_df["close"].astype(float)
    rsi_ser = calculate_rsi(closes)
    rsi_val = float(rsi_ser.iloc[-1])

    if rsi_val >= RSI_SHORT_THRESH:
        return False, rsi_val, 0.0, 0.0

    is_coil, box_high, box_low = detect_consolidation(
        ltf_df, rsi_series=rsi_ser, rsi_threshold=RSI_SHORT_THRESH, direction="SHORT"
    )
    if not is_coil:
        return False, rsi_val, 0.0, 0.0

    close = float(ltf_df["close"].iloc[-1])
    if close < box_low * (1 - BREAKOUT_BUFFER_PCT):
        return True, rsi_val, box_high, box_low
    return False, rsi_val, box_high, box_low


# ── Dynamic Exit Check ────────────────────────────────────── #

def check_exit(
    ltf_df: pd.DataFrame,
    side: str,
    box_high: float,
    box_low: float,
) -> tuple[ExitFlag, str]:
    """
    Advisory check — warns when last closed 3m candle broke box.
    Primary exit is handled by Bitget SL/TP orders.
    Uses iloc[-2] = last FULLY CLOSED candle.
    """
    if len(ltf_df) < 3:
        return "HOLD", ""

    last_closed = float(ltf_df["close"].iloc[-2])

    if side == "LONG" and box_low > 0 and last_closed < box_low:
        return "EXIT", f"Last closed 3m ({last_closed:.6f}) < box_low ({box_low:.6f})"
    if side == "SHORT" and box_high > 0 and last_closed > box_high:
        return "EXIT", f"Last closed 3m ({last_closed:.6f}) > box_high ({box_high:.6f})"

    return "HOLD", ""


# ── Strategy 1 Master Evaluator ───────────────────────────── #

def evaluate_s1(
    symbol: str,
    htf_df: pd.DataFrame,
    ltf_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    allowed_direction: str,
) -> tuple[Signal, float, float, float, float]:
    """
    Returns (signal, rsi, box_high, box_low, adx)
    allowed_direction: "BULLISH" | "BEARISH"
    """
    bull_htf, bear_htf = check_htf(htf_df)

    if bull_htf and allowed_direction == "BULLISH":
        trend_ok, adx = check_daily_trend(daily_df, "LONG")
        if not trend_ok:
            return "HOLD", 50.0, 0.0, 0.0, adx
        valid, rsi, bh, bl = check_ltf_long(ltf_df)
        if valid:
            logger.info(f"[S1][{symbol}] ✅ LONG | RSI={rsi:.1f} ADX={adx:.1f}")
            return "LONG", rsi, bh, bl, adx
        return "HOLD", rsi, bh, bl, adx

    if bear_htf and allowed_direction == "BEARISH":
        trend_ok, adx = check_daily_trend(daily_df, "SHORT")
        if not trend_ok:
            return "HOLD", 50.0, 0.0, 0.0, adx
        valid, rsi, bh, bl = check_ltf_short(ltf_df)
        if valid:
            logger.info(f"[S1][{symbol}] ✅ SHORT | RSI={rsi:.1f} ADX={adx:.1f}")
            return "SHORT", rsi, bh, bl, adx
        return "HOLD", rsi, bh, bl, adx

    return "HOLD", 50.0, 0.0, 0.0, 0.0


# Backwards-compatible alias used in bot.py
def evaluate_pair(symbol, htf_df, ltf_df, daily_df, allowed_direction):
    sig, rsi, bh, bl, adx = evaluate_s1(symbol, htf_df, ltf_df, daily_df, allowed_direction)
    return sig, rsi, bh, bl


# ════════════════════════════════════════════════════════════
#  STRATEGY 2 — Daily Momentum + Daily Consolidation Breakout
#  Purely daily chart. No 3m or 1H involvement.
#
#  Logic (from chart examples BRUSDT / ARIAUSDT):
#  ─────────────────────────────────────────────
#  Step 1 — Big momentum candle(s) within last 30 days
#            Body ≥ S2_BIG_CANDLE_BODY_PCT (default 20%)
#            Candle close must be above prior range
#
#  Step 2 — Daily RSI currently > 70
#
#  Step 3 — 1–5 tight daily candles consolidating after the big move
#            All consolidation candles must have daily RSI > 70
#            Range of consolidation ≤ S2_CONSOL_RANGE_PCT
#
#  Step 4 — Current daily candle is breaking out above the box
#            If box_high candle had a LONG upper wick:
#              → entry above the wick high
#            If box_high candle had a SHORT upper wick:
#              → entry above the body close
#
#  SL  = bottom of the daily consolidation box * 0.999
#  TP  = entry * (1 + S2_TAKE_PROFIT_PCT)
#  Leverage / margin from config_s2.py
# ════════════════════════════════════════════════════════════

def _body_pct(row: pd.Series) -> float:
    """Body size as fraction of open price."""
    o = float(row["open"])
    return abs(float(row["close"]) - o) / o if o > 0 else 0.0


def _upper_wick(row: pd.Series) -> float:
    """Upper wick size."""
    return float(row["high"]) - max(float(row["close"]), float(row["open"]))


def _body_size(row: pd.Series) -> float:
    return abs(float(row["close"]) - float(row["open"]))


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
        S2_BREAKOUT_BUFFER, S2_LONG_WICK_RATIO,
    )

    if not S2_ENABLED:
        return "HOLD", 50.0, 0.0, 0.0, "S2 disabled"

    # Need enough candles: lookback for big candle + consolidation + RSI warmup
    min_candles = RSI_PERIOD + S2_BIG_CANDLE_LOOKBACK + S2_CONSOL_CANDLES + 2
    if len(daily_df) < min_candles:
        return "HOLD", 50.0, 0.0, 0.0, "Not enough daily candles"

    closes      = daily_df["close"].astype(float)
    rsi_ser     = calculate_rsi(closes)
    daily_rsi   = float(rsi_ser.iloc[-1])

    # ── Step 2: Daily RSI must be > 70 right now ─────────────── #
    if daily_rsi <= S2_RSI_LONG_THRESH:
        return "HOLD", daily_rsi, 0.0, 0.0, f"Daily RSI {daily_rsi:.1f} ≤ {S2_RSI_LONG_THRESH}"

    # ── Step 1: Big momentum candle anywhere in last 30 days ─── #
    # Search the entire lookback window — includes consolidation candles too
    lookback_window  = daily_df.iloc[-(S2_BIG_CANDLE_LOOKBACK + 1):-1]
    big_candle_found = False
    best_body_pct    = 0.0
    for _, row in lookback_window.iterrows():
        bp = _body_pct(row)
        if bp >= S2_BIG_CANDLE_BODY_PCT:
            big_candle_found = True
            best_body_pct = max(best_body_pct, bp)

    if not big_candle_found:
        return "HOLD", daily_rsi, 0.0, 0.0, (
            f"Daily RSI {daily_rsi:.1f} ✓ — no big candle ≥{S2_BIG_CANDLE_BODY_PCT*100:.0f}% in last {S2_BIG_CANDLE_LOOKBACK}d"
        )

    # ── Step 3: Find 1–5 tight consolidation candles ─────────── #
    # These are the most recent completed candles (exclude current forming one)
    # Try from 1 candle up to S2_CONSOL_CANDLES to find the tightest valid window
    consol_found  = False
    box_high      = 0.0
    box_low       = 0.0
    entry_trigger = 0.0
    consol_size   = 0
    trigger_type  = ""

    for n in range(1, S2_CONSOL_CANDLES + 1):
        # Window: last n completed candles (iloc[-n-1:-1] excludes current candle)
        window = daily_df.iloc[-n - 1:-1]
        if len(window) < n:
            continue

        wh  = float(window["high"].max())
        wl  = float(window["low"].min())
        mid = (wh + wl) / 2
        if mid == 0:
            continue

        range_pct = (wh - wl) / mid
        if range_pct > S2_CONSOL_RANGE_PCT:
            continue  # too wide — try fewer candles

        # RSI must have been > 70 throughout this consolidation window
        window_rsi = rsi_ser.iloc[-n - 1:-1]
        if not (window_rsi > S2_RSI_LONG_THRESH).all():
            continue

        # Valid consolidation found
        consol_found = True
        box_high     = wh
        box_low      = wl
        consol_size  = n

        # Determine entry trigger: above wick or above body of the highest candle
        high_candle = window.loc[window["high"].idxmax()]
        uw   = _upper_wick(high_candle)
        body = _body_size(high_candle)

        if uw > S2_LONG_WICK_RATIO * body:
            # Long wick = price was rejected there → only need body breakout
            body_top      = max(float(high_candle["close"]), float(high_candle["open"]))
            entry_trigger = body_top * (1 + S2_BREAKOUT_BUFFER)
            trigger_type  = "above_body (long wick — ignore wick)"
        else:
            # Short wick = clean high → need to break above the full candle high (wick)
            entry_trigger = float(high_candle["high"]) * (1 + S2_BREAKOUT_BUFFER)
            trigger_type  = "above_wick (short wick — clean high)"

        break  # Use smallest valid window (tightest)

    if not consol_found:
        return "HOLD", daily_rsi, 0.0, 0.0, (
            f"Big candle ✅ {best_body_pct*100:.0f}% | RSI {daily_rsi:.1f} — no tight consolidation yet (1–{S2_CONSOL_CANDLES} candles)"
        )

    # ── Step 4: Current daily candle breaking above entry trigger ─ #
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