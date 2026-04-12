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

from config_s1 import (
    RSI_PERIOD, RSI_LONG_THRESH, RSI_SHORT_THRESH,
    CONSOLIDATION_CANDLES, CONSOLIDATION_RANGE_PCT,
    BREAKOUT_BUFFER_PCT,
)

logger = logging.getLogger(__name__)
Signal   = Literal["LONG", "SHORT", "HOLD", "PENDING_LONG", "PENDING_SHORT"]
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
#  SUPPORT / RESISTANCE DETECTION
# ════════════════════════════════════════════════════════════

def find_nearest_resistance(daily_df: pd.DataFrame, entry_price: float,
                            lookback: int = 90, pivot_n: int = 3) -> float | None:
    """
    Find nearest swing high above entry_price within the last `lookback` daily candles.
    A pivot high requires being the highest among pivot_n candles on each side (7-candle window).
    Returns the closest resistance level above entry, or None if none found.
    """
    df = daily_df.iloc[-lookback:] if len(daily_df) > lookback else daily_df
    highs = df["high"].astype(float).values
    resistances = []
    for i in range(pivot_n, len(highs) - pivot_n):
        window = highs[i - pivot_n: i + pivot_n + 1]
        if highs[i] == window.max() and highs[i] > entry_price:
            resistances.append(highs[i])
    return min(resistances) if resistances else None


def find_nearest_support(daily_df: pd.DataFrame, entry_price: float,
                         lookback: int = 90, pivot_n: int = 3) -> float | None:
    """
    Find nearest swing low below entry_price within the last `lookback` daily candles.
    A pivot low requires being the lowest among pivot_n candles on each side (7-candle window).
    Returns the closest support level below entry, or None if none found.
    """
    df = daily_df.iloc[-lookback:] if len(daily_df) > lookback else daily_df
    lows = df["low"].astype(float).values
    supports = []
    for i in range(pivot_n, len(lows) - pivot_n):
        window = lows[i - pivot_n: i + pivot_n + 1]
        if lows[i] == window.min() and lows[i] < entry_price:
            supports.append(lows[i])
    return max(supports) if supports else None


def find_spike_base(daily_df: pd.DataFrame, lookback: int = 30,
                    min_body_pct: float = 0.20,
                    price_ceiling: float | None = None) -> float | None:
    """
    Find the high of the most recent spike candle (body ≥ min_body_pct) within
    the last `lookback` daily candles whose high is below price_ceiling (current
    price). Iterates newest→oldest so the most recent qualifying candle wins.
    This is the pre-pump base — the breakout high before the current pump —
    which acts as meaningful support. Candles at or above the current price are
    the pump peak itself and are skipped.
    Returns None if no qualifying spike candle is found.
    """
    df = daily_df.iloc[-(lookback + 1):-1] if len(daily_df) > lookback + 1 else daily_df.iloc[:-1]
    for _, row in df.iloc[::-1].iterrows():
        h = float(row["high"])
        if price_ceiling is not None and h >= price_ceiling:
            continue
        o, c = float(row["open"]), float(row["close"])
        bp = abs(c - o) / o if o else 0
        if bp >= min_body_pct:
            return h
    return None


def find_breakdown_ceiling(daily_df: pd.DataFrame, lookback: int = 30,
                           min_body_pct: float = 0.20,
                           price_floor: float | None = None) -> float | None:
    """
    Find the low of the most recent bearish spike candle (body ≥ min_body_pct,
    close < open) within the last `lookback` candles whose low is above
    price_floor (current price). Iterates newest→oldest.
    This is the breakdown ceiling — the broken support that is now resistance.
    Returns None if no qualifying candle is found.
    """
    df = daily_df.iloc[-(lookback + 1):-1] if len(daily_df) > lookback + 1 else daily_df.iloc[:-1]
    for _, row in df.iloc[::-1].iterrows():
        low = float(row["low"])
        if price_floor is not None and low <= price_floor:
            continue
        o, c = float(row["open"]), float(row["close"])
        if c >= o:  # must be bearish
            continue
        bp = (o - c) / o if o else 0
        if bp >= min_body_pct:
            return low
    return None


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
    from config_s1 import ADX_TREND_THRESHOLD, DAILY_EMA_SLOW

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
    from config_s1 import S1_ENABLED
    if not S1_ENABLED:
        return "HOLD", 50.0, 0.0, 0.0, 0.0

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
        S2_BREAKOUT_BUFFER, S2_DARVAS_WICK_PCT,
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
    lookback_window      = daily_df.iloc[-(S2_BIG_CANDLE_LOOKBACK + 1):-1]
    big_candle_found     = False
    best_body_pct        = 0.0
    big_candle_body_top  = 0.0   # body top of most recent big candle (floor for trigger)
    for _, row in lookback_window.iterrows():
        bp = _body_pct(row)
        if bp >= S2_BIG_CANDLE_BODY_PCT:
            big_candle_found    = True
            best_body_pct       = max(best_body_pct, bp)
            big_candle_body_top = max(float(row["close"]), float(row["open"]))

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

        # ── Consolidation box must be tight ─────────────────────────
        # Same Darvas rule as the entry trigger: wick > 5% of body top →
        # rejected high, use body; otherwise use wick high.
        def _eff_top(r):
            bt = max(float(r["close"]), float(r["open"]))
            return bt if _upper_wick(r) > S2_DARVAS_WICK_PCT * bt else float(r["high"])
        eff_tops = window.apply(_eff_top, axis=1)
        eff_h    = float(eff_tops.max())
        eff_l    = float(window.apply(lambda r: min(float(r["close"]), float(r["open"])), axis=1).min())
        if eff_h <= 0:
            continue
        range_pct = (eff_h - eff_l) / eff_h
        if range_pct > S2_CONSOL_RANGE_PCT:
            continue

        # All consolidation candles must close at or below the Darvas box top
        # (big candle's body top). A close above means price is still running.
        if not all(float(r["close"]) <= big_candle_body_top for _, r in window.iterrows()):
            continue

        # Darvas box top = highest eff_top in window (used for entry trigger)
        box_top_pos = int(eff_tops.values.argmax())

        # RSI must have been > 70 throughout this consolidation window
        window_rsi = rsi_ser.iloc[-n - 1:-1]
        if not (window_rsi > S2_RSI_LONG_THRESH).all():
            continue

        # Valid consolidation found
        consol_found = True
        box_high     = eff_h
        box_low      = eff_l
        consol_size  = n

        # Determine entry trigger: Darvas Box top rule
        # wick > 5% of body top → rejected high, buy above body
        # wick ≤ 5% of body top → clean high, buy above wick
        high_candle = window.iloc[box_top_pos]
        uw       = _upper_wick(high_candle)
        body_top = max(float(high_candle["close"]), float(high_candle["open"]))

        if uw > S2_DARVAS_WICK_PCT * body_top:
            # Wick > 5% of body top = price rejected there → buy above body
            entry_trigger = body_top * (1 + S2_BREAKOUT_BUFFER)
            trigger_type  = "above_body (long wick — ignore wick)"
        else:
            # Wick ≤ 5% = clean Darvas box top → buy above wick high
            entry_trigger = float(high_candle["high"]) * (1 + S2_BREAKOUT_BUFFER)
            trigger_type  = "above_wick (short wick — clean high)"

        # Floor the trigger against the big candle's body top — the consolidation
        # may be a doji/spinning top with a near-zero body sitting below the big
        # candle's close, which would produce a misleadingly low trigger price.
        big_candle_floor = big_candle_body_top
        if entry_trigger < big_candle_floor:
            entry_trigger = big_candle_floor
            trigger_type += f" [floored to big candle body {big_candle_body_top:.5f}]"
            # Coil boundaries must reflect the floored trigger range, not the
            # raw sub-trigger consolidation box (which would mislead the display
            # and the scale-in window check).
            box_high = entry_trigger
            box_low  = entry_trigger * (1 - S2_CONSOL_RANGE_PCT)

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


# ════════════════════════════════════════════════════════════
#  STRATEGY 3 — 15m Swing Pullback (Long-only)
#
#  All indicators on 15m chart:
#  ─────────────────────────────────────────────────────────
#  1. EMA10 > EMA20 > EMA50 > EMA200  (golden alignment)
#  2. ADX > S3_ADX_MIN                (strong trend)
#  3. Slow Stochastics (5,3) recently oversold (< 30)
#     — confirms the pullback has happened
#  4. First green candle after the oversold = uptick signal
#  5. Current 15m candle closes above that candle's high
#  6. MACD (12,26,9) line > signal line  (momentum turning)
#
#  SL  = lowest low of oversold period * (1 - SL_BUFFER)
#  TP  = entry + max(S3_MIN_RR × risk, S3_TAKE_PROFIT_PCT × entry)
#  Leverage / margin from config_s3.py
# ════════════════════════════════════════════════════════════

def calculate_stoch(
    df: pd.DataFrame,
    k_period: int = 5,
    d_smooth: int = 3,
) -> tuple[pd.Series, pd.Series]:
    """
    Slow Stochastics.
      Fast %K  = (close − lowest_low_k) / (highest_high_k − lowest_low_k) × 100
      Slow %K  = SMA(d_smooth) of Fast %K
      Slow %D  = SMA(d_smooth) of Slow %K
    Returns (slow_k, slow_d) with same index as df.
    """
    high  = df["high"].astype(float)
    low   = df["low"].astype(float)
    close = df["close"].astype(float)

    lowest_low   = low.rolling(window=k_period).min()
    highest_high = high.rolling(window=k_period).max()
    denom        = (highest_high - lowest_low).replace(0, np.nan)
    fast_k       = 100.0 * (close - lowest_low) / denom

    slow_k = fast_k.rolling(window=d_smooth).mean()
    slow_d = slow_k.rolling(window=d_smooth).mean()
    return slow_k, slow_d


def calculate_macd(
    closes: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (macd_line, signal_line, histogram)."""
    ema_fast    = closes.ewm(span=fast,   adjust=False).mean()
    ema_slow    = closes.ewm(span=slow,   adjust=False).mean()
    macd_line   = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line, macd_line - signal_line


def evaluate_s3(
    symbol: str,
    m15_df: pd.DataFrame,
    d1_df: pd.DataFrame,
) -> tuple[Signal, float, float, float, str]:
    """
    Strategy 3 — 15m Swing Pullback (Long-only). All indicators on 15m.
    Returns (signal, adx, entry_trigger, sl_price, reason).
    """
    from config_s3 import (
        S3_ENABLED,
        S3_EMA_FAST, S3_EMA_MED, S3_EMA_SLOW, S3_EMA_TREND,
        S3_ADX_MIN, S3_ADX_MAX,
        S3_STOCH_K_PERIOD, S3_STOCH_D_SMOOTH, S3_STOCH_OVERSOLD, S3_STOCH_LOOKBACK,
        S3_MACD_FAST, S3_MACD_SLOW, S3_MACD_SIGNAL,
        S3_ENTRY_BUFFER_PCT, S3_SL_BUFFER_PCT, S3_MIN_RR, S3_TRAILING_TRIGGER_PCT,
        S3_MIN_SR_CLEARANCE, S3_DAILY_GAIN_MIN,
    )

    if not S3_ENABLED:
        return "HOLD", 0.0, 0.0, 0.0, "S3 disabled"

    # ── 15m prerequisites ─────────────────────────────────── #
    min_15m = max(210, S3_STOCH_K_PERIOD + S3_STOCH_D_SMOOTH + S3_STOCH_LOOKBACK + S3_MACD_SLOW + 10)
    if len(m15_df) < min_15m:
        return "HOLD", 0.0, 0.0, 0.0, f"Not enough 15m candles (need {min_15m})"

    closes_15 = m15_df["close"].astype(float)
    ema10  = float(calculate_ema(closes_15, S3_EMA_FAST).iloc[-1])
    ema20  = float(calculate_ema(closes_15, S3_EMA_MED).iloc[-1])
    ema50  = float(calculate_ema(closes_15, S3_EMA_SLOW).iloc[-1])
    ema200 = float(calculate_ema(closes_15, S3_EMA_TREND).iloc[-1])

    if not (ema10 > ema20 > ema50 > ema200):
        return "HOLD", 0.0, 0.0, 0.0, "15m EMA not aligned (need 10>20>50>200)"

    adx_res = calculate_adx(m15_df)
    adx_val = float(adx_res["adx"].iloc[-1])
    if adx_val < S3_ADX_MIN:
        return "HOLD", adx_val, 0.0, 0.0, (
            f"15m ADX={adx_val:.1f} < {S3_ADX_MIN} (not trending)"
        )
    if adx_val > S3_ADX_MAX:
        return "HOLD", adx_val, 0.0, 0.0, (
            f"15m ADX={adx_val:.1f} > {S3_ADX_MAX} (overextended momentum)"
        )

    # Daily momentum gate: price must be ≥10% above today's daily open
    current_close = float(m15_df["close"].iloc[-1])
    daily_open    = float(d1_df["open"].iloc[-1])
    daily_gain    = (current_close - daily_open) / daily_open
    if daily_gain < S3_DAILY_GAIN_MIN:
        return "HOLD", adx_val, 0.0, 0.0, (
            f"S3: daily gain {daily_gain * 100:.1f}% < {S3_DAILY_GAIN_MIN * 100:.0f}% "
            f"(need ≥10% above daily open {daily_open:.5f})"
        )

    # Slow Stochastics and MACD
    slow_k, _    = calculate_stoch(m15_df, S3_STOCH_K_PERIOD, S3_STOCH_D_SMOOTH)
    macd_line, sig_line, _ = calculate_macd(closes_15, S3_MACD_FAST, S3_MACD_SLOW, S3_MACD_SIGNAL)
    stoch_now = float(slow_k.iloc[-1])
    macd_ok   = float(macd_line.iloc[-1]) > float(sig_line.iloc[-1])

    # Look for oversold in last S3_STOCH_LOOKBACK completed candles (exclude current)
    lookback_k = slow_k.iloc[-S3_STOCH_LOOKBACK - 1:-1]
    oversold_positions = [i for i, v in enumerate(lookback_k) if not np.isnan(v) and v < S3_STOCH_OVERSOLD]

    if not oversold_positions:
        return "HOLD", adx_val, 0.0, 0.0, (
            f"15m ✅ ADX={adx_val:.1f} EMA aligned | "
            f"Stoch={stoch_now:.1f} — no oversold (<{S3_STOCH_OVERSOLD}) in last {S3_STOCH_LOOKBACK} candles"
        )

    # Absolute positions from end of m15_df
    last_os_rel  = oversold_positions[-1]
    first_os_rel = oversold_positions[0]
    abs_last_os  = -(S3_STOCH_LOOKBACK + 1) + last_os_rel
    abs_first_os = -(S3_STOCH_LOOKBACK + 1) + first_os_rel

    # Pivot low = min low across the oversold period
    os_period_df = m15_df.iloc[abs_first_os : abs_last_os + 1]
    pivot_low    = float(os_period_df["low"].min())
    sl_price     = pivot_low * (1 - S3_SL_BUFFER_PCT)

    # Candles completed AFTER the last oversold (exclude current forming candle)
    after_os_df = m15_df.iloc[abs_last_os + 1 : -1].reset_index(drop=True)

    if after_os_df.empty:
        return "HOLD", adx_val, 0.0, sl_price, (
            f"15m ✅ ADX={adx_val:.1f} | "
            f"Stoch oversold ✅ ({len(oversold_positions)} bars) | "
            f"Waiting for first green uptick candle | MACD={'✅' if macd_ok else '❌'}"
        )

    # Uptick adjacency rule: the immediately completed candle must be the green uptick.
    # [last oversold candle] → [green uptick = m15_df.iloc[-2]] → [current forming candle]
    uptick_candle = m15_df.iloc[-2]
    if float(uptick_candle["close"]) <= float(uptick_candle["open"]):
        return "HOLD", adx_val, 0.0, sl_price, (
            f"15m ✅ ADX={adx_val:.1f} | "
            f"Stoch oversold ✅ | Last candle not green — uptick must be immediately before entry | "
            f"MACD={'✅' if macd_ok else '❌'}"
        )
    last_green = uptick_candle

    entry_trigger = float(last_green["high"]) * (1 + S3_ENTRY_BUFFER_PCT)

    # Resistance clearance: skip if resistance is too close above entry trigger.
    # S3 is designed to enter on a breakout — if resistance is right above the
    # trigger there is no room to run.
    _s3_peak = float(m15_df["high"].iloc[-50:].max())
    _s3_res  = find_nearest_resistance(m15_df, max(entry_trigger, _s3_peak) * 1.01,
                                       lookback=300)
    if _s3_res is not None:
        _res_clearance = (_s3_res - entry_trigger) / entry_trigger
        if _res_clearance < S3_MIN_SR_CLEARANCE:
            return "HOLD", adx_val, 0.0, sl_price, (
                f"S3 setup ✅ | Resistance {_s3_res:.5f} too close to entry trigger "
                f"({_res_clearance * 100:.1f}% < {S3_MIN_SR_CLEARANCE * 100:.0f}% min)"
            )

    if current_close <= entry_trigger:
        return "HOLD", adx_val, entry_trigger, sl_price, (
            f"15m ✅ ADX={adx_val:.1f} | EMA aligned | "
            f"Stoch oversold ✅ | Green uptick ✅ | "
            f"Waiting breakout > {entry_trigger:.5f} (now {current_close:.5f}) | "
            f"MACD={'✅' if macd_ok else '❌'}"
        )

    # Breakout confirmed — validate R:R
    risk = current_close - sl_price
    if risk <= 0:
        return "HOLD", adx_val, entry_trigger, sl_price, "SL >= entry — invalid setup"

    reward = S3_TRAILING_TRIGGER_PCT * current_close
    rr     = reward / risk
    if rr < S3_MIN_RR:
        return "HOLD", adx_val, entry_trigger, sl_price, (
            f"S3 breakout but R:R={rr:.1f} < {S3_MIN_RR} minimum — skip"
        )

    if not macd_ok:
        return "HOLD", adx_val, entry_trigger, sl_price, (
            f"S3 breakout ✅ R:R={rr:.1f} but MACD bearish — skip"
        )

    logger.info(
        f"[S3][{symbol}] ✅ LONG | 15m EMA aligned | ADX={adx_val:.1f} | "
        f"Stoch oversold | Uptick breakout | SL={sl_price:.5f} | R:R={rr:.1f}"
    )
    return "LONG", adx_val, entry_trigger, sl_price, (
        f"S3 ✅ | ADX={adx_val:.1f} | EMA10>20>50>200 | "
        f"Stoch oversold | MACD ✅ | Uptick breakout | R:R={rr:.1f}"
    )


# ════════════════════════════════════════════════════════════
#  STRATEGY 4 — Post-Pump RSI Divergence Short
#
#  Daily candles only.
#  ─────────────────────────────────────────────────────────
#  1. Big momentum spike (≥20% body) in last 30 daily candles
#  2. RSI peaked above 75 within last 10 candles (overbought)
#  3. (Optional) RSI bearish divergence — 2nd RSI push lower
#  4. Entry: intraday breach of previous day's low
#
#  SL  = spike_high * (1 + S4_SL_BUFFER)
#  Exit: 50% close at −10%, trailing stop on remainder (same as S2)
#  Sentiment gate: only fires when NOT BULLISH
# ════════════════════════════════════════════════════════════

def evaluate_s4(
    symbol: str,
    daily_df: pd.DataFrame,
    htf_df: pd.DataFrame | None = None,
) -> tuple[Signal, float, float, float, float, float, bool, str, str]:
    """
    Strategy 4 — Post-pump RSI divergence short.
    Returns (signal, daily_rsi, entry_trigger, sl_price, spike_body_pct, rsi_peak, rsi_div, rsi_div_str, reason)
      signal        : "SHORT" or "HOLD"
      daily_rsi     : current RSI value
      entry_trigger : previous day's low (intraday breach fires SHORT)
      sl_price      : spike high * (1 + SL_BUFFER)
      spike_body_pct: biggest candle body % found (for logging)
      rsi_peak      : peak RSI seen in lookback window
      rsi_div       : True if bearish RSI divergence detected
      rsi_div_str   : e.g. "82.3→74.1" (first peak → second peak RSI)
      reason        : debug string
    """
    from config_s4 import (
        S4_ENABLED, S4_BIG_CANDLE_BODY_PCT, S4_BIG_CANDLE_LOOKBACK,
        S4_RSI_PEAK_THRESH, S4_RSI_PEAK_LOOKBACK, S4_RSI_DIV_MIN_DROP,
        S4_RSI_STILL_HOT_THRESH, S4_LOW_LOOKBACK,
    )

    if not S4_ENABLED:
        return "HOLD", 50.0, 0.0, 0.0, 0.0, 0.0, False, "", "S4 disabled"

    min_candles = RSI_PERIOD + S4_BIG_CANDLE_LOOKBACK + 2
    if len(daily_df) < min_candles:
        return "HOLD", 50.0, 0.0, 0.0, 0.0, 0.0, False, "", "Not enough daily candles"

    closes    = daily_df["close"].astype(float)
    rsi_ser   = calculate_rsi(closes)
    daily_rsi = float(rsi_ser.iloc[-1])

    # ── Step 1: Big spike candle in lookback ─────────────────── #
    lookback     = daily_df.iloc[-(S4_BIG_CANDLE_LOOKBACK + 1):-1]
    spike_found  = False
    best_body_pct = 0.0
    spike_high   = 0.0
    for _, row in lookback.iterrows():
        bp = _body_pct(row)
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

    # ── Step 2: RSI peaked above threshold recently ───────────── #
    rsi_window = rsi_ser.iloc[-S4_RSI_PEAK_LOOKBACK - 1:-1]
    rsi_peak   = float(rsi_window.max())
    if rsi_peak < S4_RSI_PEAK_THRESH:
        return "HOLD", daily_rsi, 0.0, 0.0, best_body_pct, rsi_peak, False, "", (
            f"Spike ✅ body={best_body_pct*100:.0f}% | "
            f"RSI peak={rsi_peak:.1f} < {S4_RSI_PEAK_THRESH} (not overbought)"
        )

    # ── Step 2b: Previous candle RSI must still be hot (≥70) ───── #
    prev_rsi = float(rsi_ser.iloc[-2])
    if prev_rsi < S4_RSI_STILL_HOT_THRESH:
        return "HOLD", daily_rsi, 0.0, 0.0, best_body_pct, rsi_peak, False, "", (
            f"Spike ✅ RSI peaked={rsi_peak:.1f} | "
            f"Setup invalidated — prev candle RSI={prev_rsi:.1f} < {S4_RSI_STILL_HOT_THRESH}"
        )

    # ── Step 3: Optional RSI bearish divergence ───────────────── #
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

    # ── Step 4: Entry trigger = previous day's low ────────────── #
    from config_s4 import S4_LEVERAGE, S4_ENTRY_BUFFER
    entry_trigger = float(daily_df.iloc[-2]["low"]) * (1 - S4_ENTRY_BUFFER)
    sl_price      = entry_trigger * (1 + 0.50 / S4_LEVERAGE)   # -50% P/L

    # ── Step 5: 1H low filter ─────────────────────────────────── #
    # Entry trigger must be ≤ the lowest low of the last S4_LOW_LOOKBACK 1H candles.
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


# ════════════════════════════════════════════════════════════
#  STRATEGY 5 — SMC Order Block Pullback with ChoCH Entry
#
#  Multi-timeframe Smart Money Concept strategy.
#  ─────────────────────────────────────────────────────────
#  1D:  EMA10 > EMA20 > EMA50 (bullish) / reverse (bearish)
#  1H:  Break of Structure — close above prior swing high (LONG)
#       or below prior swing low (SHORT)
#  15m: Find Order Block (last opposing candle before impulse)
#  15m: Pullback touches OB zone
#  15m: Change of Character (ChoCH) — candle closes back through
#       OB boundary → entry trigger confirmed
#
#  LONG:  OB = last red candle before bullish impulse
#         Entry  = ob_high (red candle open) + buffer
#         SL     = ob_low  (red candle low)  − buffer
#
#  SHORT: OB = last green candle before bearish impulse
#         Entry  = ob_low  (green candle open) − buffer
#         SL     = ob_high (green candle high) + buffer
#
#  Exit mirrors S3 (LONG) / S4 (SHORT):
#    50% close at ±10% move, 10% trailing stop on remainder
# ════════════════════════════════════════════════════════════

def find_swing_high_target(
    df: pd.DataFrame,
    above_price: float,
    lookback: int = 50,
) -> float | None:
    """
    Finds the nearest prior swing high above `above_price` on the chart.
    A swing high = candle whose high is greater than both its neighbours.
    Returns the lowest qualifying swing high (nearest resistance / liquidity pool),
    or None if not found.
    """
    candles = df.iloc[-lookback:].reset_index(drop=True)
    n = len(candles)
    candidates = []
    for i in range(1, n - 1):
        h = float(candles.iloc[i]["high"])
        if h > float(candles.iloc[i - 1]["high"]) and h > float(candles.iloc[i + 1]["high"]):
            if h > above_price:
                candidates.append(h)
    return min(candidates) if candidates else None


def find_swing_low_target(
    df: pd.DataFrame,
    below_price: float,
    lookback: int = 50,
) -> float | None:
    """
    Finds the nearest prior swing low below `below_price` on the chart.
    A swing low = candle whose low is less than both its neighbours.
    Returns the highest qualifying swing low (nearest support / liquidity pool),
    or None if not found.
    """
    candles = df.iloc[-lookback:].reset_index(drop=True)
    n = len(candles)
    candidates = []
    for i in range(1, n - 1):
        lo = float(candles.iloc[i]["low"])
        if lo < float(candles.iloc[i - 1]["low"]) and lo < float(candles.iloc[i + 1]["low"]):
            if lo < below_price:
                candidates.append(lo)
    return max(candidates) if candidates else None


def find_swing_low_after_ref(
    df: pd.DataFrame,
    below_price: float,
    ref_high: float,
    lookback: int = 50,
) -> float | None:
    """
    Finds a confirmed swing low that formed AFTER the candle containing `ref_high`.
    Used for LONG swing trail: after the ref high is broken, wait for a new swing
    low to form — only then advance the SL.
    Returns the highest qualifying swing low, or None if none has formed yet.
    """
    candles = df.iloc[-lookback:].reset_index(drop=True)
    n = len(candles)
    ref_idx = None
    for i in range(n - 1, -1, -1):
        if abs(float(candles.iloc[i]["high"]) - ref_high) < 1e-9:
            ref_idx = i
            break
    if ref_idx is None:
        return None
    candidates = []
    for i in range(max(ref_idx + 1, 1), n - 1):
        lo = float(candles.iloc[i]["low"])
        if lo < float(candles.iloc[i - 1]["low"]) and lo < float(candles.iloc[i + 1]["low"]):
            if lo < below_price:
                candidates.append(lo)
    return max(candidates) if candidates else None


def find_swing_high_after_ref(
    df: pd.DataFrame,
    above_price: float,
    ref_low: float,
    lookback: int = 50,
) -> float | None:
    """
    Symmetric for SHORT: finds a confirmed swing high that formed AFTER the candle
    containing `ref_low`. After the ref low is broken downward, wait for a new swing
    high to form — only then advance the SL.
    Returns the lowest qualifying swing high, or None if none has formed yet.
    """
    candles = df.iloc[-lookback:].reset_index(drop=True)
    n = len(candles)
    ref_idx = None
    for i in range(n - 1, -1, -1):
        if abs(float(candles.iloc[i]["low"]) - ref_low) < 1e-9:
            ref_idx = i
            break
    if ref_idx is None:
        return None
    candidates = []
    for i in range(max(ref_idx + 1, 1), n - 1):
        hi = float(candles.iloc[i]["high"])
        if hi > float(candles.iloc[i - 1]["high"]) and hi > float(candles.iloc[i + 1]["high"]):
            if hi > above_price:
                candidates.append(hi)
    return min(candidates) if candidates else None


def find_fvg(
    df: pd.DataFrame,
    direction: str = "BULL",
    lookback: int = 15,
) -> tuple[float, float] | None:
    """
    Most recent Fair Value Gap in the last `lookback` candles.
    BULL FVG: c3.low > c1.high  →  returns (gap_low=c1.high, gap_high=c3.low)
    BEAR FVG: c3.high < c1.low  →  returns (gap_low=c3.high, gap_high=c1.low)
    Returns None if no FVG found.
    """
    candles = df.iloc[-lookback:].reset_index(drop=True)
    for i in range(len(candles) - 1, 1, -1):
        c1 = candles.iloc[i - 2]
        c3 = candles.iloc[i]
        if direction == "BULL" and float(c3["low"]) > float(c1["high"]):
            return float(c1["high"]), float(c3["low"])
        if direction == "BEAR" and float(c3["high"]) < float(c1["low"]):
            return float(c3["high"]), float(c1["low"])
    return None


def find_bullish_ob(
    df: pd.DataFrame,
    lookback: int = 50,
    min_impulse_pct: float = 0.01,
) -> tuple[float, float] | None:
    """
    Find the most recent 15m Bullish Order Block.
    OB = last bearish (red) candle before a bullish impulse of
    ≥2 consecutive green candles moving price up ≥ min_impulse_pct.

    Returns (ob_low, ob_high) where:
        ob_low  = red candle's low   (outer SL boundary)
        ob_high = red candle's open  (top of body = ChoCH trigger level)
    Returns None if no valid impulse found.
    """
    candles = df.iloc[-lookback:].reset_index(drop=True)
    n = len(candles)

    i = n - 1
    while i >= 1:
        c = candles.iloc[i]
        if float(c["close"]) <= float(c["open"]):   # not green
            i -= 1
            continue
        # Find start of this green run
        run_end = i
        run_start = i
        while (run_start > 0 and
               float(candles.iloc[run_start - 1]["close"]) > float(candles.iloc[run_start - 1]["open"])):
            run_start -= 1
        if run_end - run_start < 1:   # need at least 2 green candles
            i = run_start - 1
            continue
        # Check impulse size
        first_open  = float(candles.iloc[run_start]["open"])
        last_close  = float(candles.iloc[run_end]["close"])
        if first_open <= 0 or (last_close - first_open) / first_open < min_impulse_pct:
            i = run_start - 1
            continue
        # Valid impulse — find last red candle immediately before run_start
        ob_idx = run_start - 1
        while ob_idx >= 0:
            ob_c = candles.iloc[ob_idx]
            if float(ob_c["close"]) < float(ob_c["open"]):   # red
                return float(ob_c["low"]), float(ob_c["open"])
            ob_idx -= 1
        i = run_start - 1
    return None


def find_bearish_ob(
    df: pd.DataFrame,
    lookback: int = 50,
    min_impulse_pct: float = 0.01,
) -> tuple[float, float] | None:
    """
    Find the most recent 15m Bearish Order Block.
    OB = last bullish (green) candle before a bearish impulse of
    ≥2 consecutive red candles moving price down ≥ min_impulse_pct.

    Returns (ob_low, ob_high) where:
        ob_low  = green candle's open  (bottom of body = ChoCH trigger level)
        ob_high = green candle's high  (outer SL boundary)
    Returns None if no valid impulse found.
    """
    candles = df.iloc[-lookback:].reset_index(drop=True)
    n = len(candles)

    i = n - 1
    while i >= 1:
        c = candles.iloc[i]
        if float(c["close"]) >= float(c["open"]):   # not red
            i -= 1
            continue
        # Find start of this red run
        run_end = i
        run_start = i
        while (run_start > 0 and
               float(candles.iloc[run_start - 1]["close"]) < float(candles.iloc[run_start - 1]["open"])):
            run_start -= 1
        if run_end - run_start < 1:   # need at least 2 red candles
            i = run_start - 1
            continue
        # Check impulse size
        first_open  = float(candles.iloc[run_start]["open"])
        last_close  = float(candles.iloc[run_end]["close"])
        if first_open <= 0 or (first_open - last_close) / first_open < min_impulse_pct:
            i = run_start - 1
            continue
        # Valid impulse — find last green candle immediately before run_start
        ob_idx = run_start - 1
        while ob_idx >= 0:
            ob_c = candles.iloc[ob_idx]
            if float(ob_c["close"]) > float(ob_c["open"]):   # green
                return float(ob_c["open"]), float(ob_c["high"])
            ob_idx -= 1
        i = run_start - 1
    return None


def evaluate_s5(
    symbol: str,
    daily_df: pd.DataFrame,
    htf_df: pd.DataFrame,
    m15_df: pd.DataFrame,
    allowed_direction: str,
    cfg=None,   # instrument config dict; None = Bitget path (config_s5 module)
) -> tuple[Signal, float, float, float, float, float, str]:
    """
    Strategy 5 — SMC Order Block Pullback.
    Returns (signal, entry_trigger, sl_price, tp_price, ob_low, ob_high, reason).
      signal        : "LONG" | "SHORT" | "HOLD"
      entry_trigger : price level that must be crossed to fire the trade
      sl_price      : stop-loss (OB outer edge + buffer)
      tp_price      : structural swing target (next swing high/low)
      ob_low        : Order Block lower bound (for chart display)
      ob_high       : Order Block upper bound (for chart display)
      reason        : debug string
    """
    if cfg is not None:
        S5_ENABLED                    = cfg["s5_enabled"]
        S5_DAILY_EMA_FAST             = cfg["s5_daily_ema_fast"]
        S5_DAILY_EMA_MED              = cfg["s5_daily_ema_med"]
        S5_DAILY_EMA_SLOW             = cfg["s5_daily_ema_slow"]
        S5_HTF_BOS_LOOKBACK           = cfg["s5_htf_bos_lookback"]
        S5_OB_LOOKBACK                = cfg["s5_ob_lookback"]
        S5_OB_MIN_IMPULSE             = cfg["s5_ob_min_impulse"]
        S5_OB_MIN_RANGE_PCT           = cfg["s5_ob_min_range_pct"]
        S5_CHOCH_LOOKBACK             = cfg["s5_choch_lookback"]
        S5_MAX_ENTRY_BUFFER           = cfg["s5_max_entry_buffer"]
        S5_SL_BUFFER_PCT              = cfg["s5_sl_buffer_pct"]
        S5_MIN_RR                     = cfg["s5_min_rr"]
        S5_SWING_LOOKBACK             = cfg["s5_swing_lookback"]
        S5_SMC_FVG_FILTER             = cfg["s5_smc_fvg_filter"]
        S5_SMC_FVG_LOOKBACK           = cfg["s5_smc_fvg_lookback"]
    else:
        from config_s5 import (          # noqa: PLC0415
            S5_ENABLED,
            S5_DAILY_EMA_FAST, S5_DAILY_EMA_MED, S5_DAILY_EMA_SLOW,
            S5_HTF_BOS_LOOKBACK,
            S5_OB_LOOKBACK, S5_OB_MIN_IMPULSE, S5_CHOCH_LOOKBACK,
            S5_MAX_ENTRY_BUFFER, S5_SL_BUFFER_PCT,
            S5_MIN_RR, S5_SWING_LOOKBACK,
            S5_OB_MIN_RANGE_PCT, S5_SMC_FVG_FILTER, S5_SMC_FVG_LOOKBACK,
        )

    if not S5_ENABLED:
        return "HOLD", 0.0, 0.0, 0.0, 0.0, 0.0, "S5 disabled"

    go_long  = allowed_direction == "BULLISH" 
    go_short = allowed_direction == "BEARISH"

    if len(daily_df) < RSI_PERIOD + 50 or len(htf_df) < S5_HTF_BOS_LOOKBACK + 2:
        return "HOLD", 0.0, 0.0, 0.0, 0.0, 0.0, "Not enough candles"
    if m15_df is None or len(m15_df) < S5_OB_LOOKBACK + S5_CHOCH_LOOKBACK + 10:
        return "HOLD", 0.0, 0.0, 0.0, 0.0, 0.0, "Not enough 15m candles"

    # ── 1. Daily EMA bias ─────────────────────────────────── #
    daily_closes = daily_df["close"].astype(float)
    ema_fast = float(calculate_ema(daily_closes, S5_DAILY_EMA_FAST).iloc[-1])
    ema_med  = float(calculate_ema(daily_closes, S5_DAILY_EMA_MED).iloc[-1])
    ema_slow = float(calculate_ema(daily_closes, S5_DAILY_EMA_SLOW).iloc[-1])

    ema_bull = ema_fast > ema_med > ema_slow
    ema_bear = ema_slow > ema_med > ema_fast

    if go_long and not ema_bull:
        return "HOLD", 0.0, 0.0, 0.0, 0.0, 0.0, (
            f"Daily EMA not bullish (EMA{S5_DAILY_EMA_FAST}<EMA{S5_DAILY_EMA_SLOW})"
        )
    if go_short and not ema_bear:
        return "HOLD", 0.0, 0.0, 0.0, 0.0, 0.0, (
            f"Daily EMA not bearish (EMA{S5_DAILY_EMA_FAST}>EMA{S5_DAILY_EMA_SLOW})"
        )

    # ── 2. 1H Break of Structure ───────────────────────────── #
    # BOS = a past swing high/low was broken by a subsequent bar in the window.
    # We scan most-recent → oldest for a pivot, then check if ANY bar after
    # that pivot (within htf_win) broke above/below it.  current_htf is NOT
    # used in the condition — entries happen during pullbacks, when price is
    # below the broken swing high (LONG) or above the broken swing low (SHORT).
    htf_win = htf_df.iloc[-(S5_HTF_BOS_LOOKBACK + 2):-1].reset_index(drop=True)
    n_htf   = len(htf_win)
    bos_high = None   # most recent 1H swing high that was subsequently broken (BOS confirmed for LONG)
    bos_low  = None   # most recent 1H swing low that was subsequently broken (BOS confirmed for SHORT)

    for k in range(n_htf - 2, 0, -1):
        if bos_high is None:
            h = float(htf_win.iloc[k]["high"])
            if (h > float(htf_win.iloc[k - 1]["high"]) and
                    h > float(htf_win.iloc[k + 1]["high"])):
                # Check if any bar after this pivot closed above it (confirmed break)
                post_closes = htf_win.iloc[k + 1:]["close"].astype(float)
                if any(c > h for c in post_closes):
                    bos_high = h
        if bos_low is None:
            lo = float(htf_win.iloc[k]["low"])
            if (lo < float(htf_win.iloc[k - 1]["low"]) and
                    lo < float(htf_win.iloc[k + 1]["low"])):
                # Check if any bar after this pivot closed below it (confirmed break)
                post_closes = htf_win.iloc[k + 1:]["close"].astype(float)
                if any(c < lo for c in post_closes):
                    bos_low = lo
        if bos_high is not None and bos_low is not None:
            break

    if go_long and bos_high is None:
        return "HOLD", 0.0, 0.0, 0.0, 0.0, 0.0, (
            f"Daily EMA bullish ✅ | 1H BOS not confirmed "
            f"(no swing high with subsequent close-above in last {S5_HTF_BOS_LOOKBACK} bars)"
        )
    if go_short and bos_low is None:
        return "HOLD", 0.0, 0.0, 0.0, 0.0, 0.0, (
            f"Daily EMA bearish ✅ | 1H BOS not confirmed "
            f"(no swing low with subsequent close-below in last {S5_HTF_BOS_LOOKBACK} bars)"
        )

    # ── 3 + 4 + 5. OB → Pullback touch → ChoCH ───────────── #
    if go_long:
        ob = find_bullish_ob(m15_df, lookback=S5_OB_LOOKBACK, min_impulse_pct=S5_OB_MIN_IMPULSE)
        if ob is None:
            return "HOLD", 0.0, 0.0, 0.0, 0.0, 0.0, (
                f"Daily EMA ✅ | 1H BOS ✅ | No 15m Bullish OB found (lookback={S5_OB_LOOKBACK})"
            )
        ob_low, ob_high = ob

        # Minimum OB range — reject flat/narrow candles that would put SL too close to entry
        ob_range = (ob_high - ob_low) / ob_low if ob_low > 0 else 0
        if ob_range < S5_OB_MIN_RANGE_PCT:
            return "HOLD", 0.0, 0.0, 0.0, ob_low, ob_high, (
                f"Daily EMA ✅ | 1H BOS ✅ | Bullish OB too narrow "
                f"({ob_range*100:.2f}% < {S5_OB_MIN_RANGE_PCT*100:.1f}%)"
            )

        # FVG confluence (opt-in): require an unfilled bullish FVG near the OB
        if S5_SMC_FVG_FILTER:
            fvg = find_fvg(m15_df, direction="BULL", lookback=S5_SMC_FVG_LOOKBACK)
            if fvg is None or fvg[0] < ob_low:
                return "HOLD", 0.0, 0.0, 0.0, ob_low, ob_high, (
                    f"Daily EMA ✅ | 1H BOS ✅ | Bullish OB ✅ | No BULL FVG above OB — skipping"
                )

        # OB touch: any recent candle's low dipped into or through the OB
        recent = m15_df.iloc[-S5_CHOCH_LOOKBACK:]
        ob_touched = any(float(r["low"]) <= ob_high * 1.002 for _, r in recent.iterrows())
        if not ob_touched:
            return "HOLD", 0.0, 0.0, 0.0, ob_low, ob_high, (
                f"Daily EMA ✅ | 1H BOS ✅ | Bullish OB {ob_low:.5f}–{ob_high:.5f} | "
                f"Waiting pullback touch"
            )

        # SMC entry: limit order at ob_high — no ChoCH candle close needed.
        # The limit fills only if price dips below ob_high and bounces back — that IS confirmation.
        entry_trigger = ob_high
        sl_price      = ob_low * (1 - S5_SL_BUFFER_PCT)
        current_close = float(m15_df["close"].iloc[-1])

        # Stale OB guard: if price has already moved too far above ob_high, skip
        if current_close > ob_high * (1 + S5_MAX_ENTRY_BUFFER):
            return "HOLD", entry_trigger, sl_price, 0.0, ob_low, ob_high, (
                f"Daily EMA ✅ | 1H BOS ✅ | Bullish OB ✅ | OB touched ✅ | "
                f"Stale — price {current_close:.5f} already >{S5_MAX_ENTRY_BUFFER*100:.2g}% above ob_high {ob_high:.5f}"
            )

        # Structural TP — compute before entry-trigger check so PENDING signals carry full data
        tp_price = find_swing_high_target(m15_df, entry_trigger, lookback=S5_SWING_LOOKBACK)
        if tp_price is None:
            return "HOLD", entry_trigger, sl_price, 0.0, ob_low, ob_high, (
                f"S5 OB ✅ but no swing high found above {entry_trigger:.5f} — skip"
            )

        risk   = entry_trigger - sl_price
        reward = tp_price - entry_trigger
        rr     = reward / risk if risk > 0 else 0
        if rr < S5_MIN_RR:
            return "HOLD", entry_trigger, sl_price, tp_price, ob_low, ob_high, (
                f"S5 OB ✅ but R:R={rr:.1f} < {S5_MIN_RR} (TP={tp_price:.5f}) — skip"
            )

        logger.info(
            f"[S5][{symbol}] 🕐 PENDING_LONG | Bullish OB {ob_low:.5f}–{ob_high:.5f} | "
            f"limit@{entry_trigger:.5f} | TP={tp_price:.5f} | R:R={rr:.1f}"
        )
        return "PENDING_LONG", entry_trigger, sl_price, tp_price, ob_low, ob_high, (
            f"S5 OB {ob_low:.5f}–{ob_high:.5f} | Limit@{entry_trigger:.5f} | TP={tp_price:.5f} R:R={rr:.1f}"
        )

    else:  # go_short
        ob = find_bearish_ob(m15_df, lookback=S5_OB_LOOKBACK, min_impulse_pct=S5_OB_MIN_IMPULSE)
        if ob is None:
            return "HOLD", 0.0, 0.0, 0.0, 0.0, 0.0, (
                f"Daily EMA ✅ | 1H BOS ✅ | No 15m Bearish OB found (lookback={S5_OB_LOOKBACK})"
            )
        ob_low, ob_high = ob

        # Minimum OB range — reject flat/narrow candles that would put SL too close to entry
        ob_range = (ob_high - ob_low) / ob_low if ob_low > 0 else 0
        if ob_range < S5_OB_MIN_RANGE_PCT:
            return "HOLD", 0.0, 0.0, 0.0, ob_low, ob_high, (
                f"Daily EMA ✅ | 1H BOS ✅ | Bearish OB too narrow "
                f"({ob_range*100:.2f}% < {S5_OB_MIN_RANGE_PCT*100:.1f}%)"
            )

        # FVG confluence (opt-in): require an unfilled bearish FVG near the OB
        if S5_SMC_FVG_FILTER:
            fvg = find_fvg(m15_df, direction="BEAR", lookback=S5_SMC_FVG_LOOKBACK)
            if fvg is None or fvg[1] > ob_high:
                return "HOLD", 0.0, 0.0, 0.0, ob_low, ob_high, (
                    f"Daily EMA ✅ | 1H BOS ✅ | Bearish OB ✅ | No BEAR FVG below OB — skipping"
                )

        # OB touch: any recent candle's high rallied into the OB zone
        recent = m15_df.iloc[-S5_CHOCH_LOOKBACK:]
        ob_touched = any(float(r["high"]) >= ob_low * 0.998 for _, r in recent.iterrows())
        if not ob_touched:
            return "HOLD", 0.0, 0.0, 0.0, ob_low, ob_high, (
                f"Daily EMA ✅ | 1H BOS ✅ | Bearish OB {ob_low:.5f}–{ob_high:.5f} | "
                f"Waiting pullback touch"
            )

        # SMC entry: limit order at ob_low — no ChoCH candle close needed.
        # The limit fills only if price rallies back to ob_low and reverses — that IS confirmation.
        entry_trigger = ob_low
        sl_price      = ob_high * (1 + S5_SL_BUFFER_PCT)
        current_close = float(m15_df["close"].iloc[-1])

        # Stale OB guard: if price has already moved too far below ob_low, skip
        if current_close < ob_low * (1 - S5_MAX_ENTRY_BUFFER):
            return "HOLD", entry_trigger, sl_price, 0.0, ob_low, ob_high, (
                f"Daily EMA ✅ | 1H BOS ✅ | Bearish OB ✅ | OB touched ✅ | "
                f"Stale — price {current_close:.5f} already >{S5_MAX_ENTRY_BUFFER*100:.2g}% below ob_low {ob_low:.5f}"
            )

        # Structural TP — compute before entry-trigger check so PENDING signals carry full data
        tp_price = find_swing_low_target(m15_df, entry_trigger, lookback=S5_SWING_LOOKBACK)
        if tp_price is None:
            return "HOLD", entry_trigger, sl_price, 0.0, ob_low, ob_high, (
                f"S5 OB ✅ but no swing low found below {entry_trigger:.5f} — skip"
            )

        risk   = sl_price - entry_trigger
        reward = entry_trigger - tp_price
        rr     = reward / risk if risk > 0 else 0
        if rr < S5_MIN_RR:
            return "HOLD", entry_trigger, sl_price, tp_price, ob_low, ob_high, (
                f"S5 OB ✅ but R:R={rr:.1f} < {S5_MIN_RR} (TP={tp_price:.5f}) — skip"
            )

        logger.info(
            f"[S5][{symbol}] 🕐 PENDING_SHORT | Bearish OB {ob_low:.5f}–{ob_high:.5f} | "
            f"limit@{entry_trigger:.5f} | TP={tp_price:.5f} | R:R={rr:.1f}"
        )
        return "PENDING_SHORT", entry_trigger, sl_price, tp_price, ob_low, ob_high, (
            f"S5 OB {ob_low:.5f}–{ob_high:.5f} | Limit@{entry_trigger:.5f} | TP={tp_price:.5f} R:R={rr:.1f}"
        )


# ── S6: V-Formation Liquidity Sweep Short ─────────────────── #

def evaluate_s6(
    symbol: str,
    daily_df: pd.DataFrame,
    allowed_direction: str,
) -> tuple[Signal, float, float, float, float, str]:
    """
    Scans the last S6_SPIKE_LOOKBACK daily candles for a V-formation:
      1. Swing high: local maximum with RSI > S6_OVERBOUGHT_RSI
      2. Spike low : price drops >= S6_MIN_DROP_PCT from swing-high's high
      3. V-pivot   : candle immediately after spike low is bullish
                     (close > open AND close > spike_low_candle.close)

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

    # Work over the lookback window (reset index for safe iloc arithmetic)
    window  = daily_df.iloc[-(S6_SPIKE_LOOKBACK + 2):].reset_index(drop=True)
    rsi_win = rsi_series.iloc[-(S6_SPIKE_LOOKBACK + 2):].reset_index(drop=True)
    n       = len(window)

    # Scan swing highs from most recent to oldest.
    # i must have at least 1 candle before (i-1) and 2 after (spike + pivot).
    # Peak must also be at least 4 candles before the current candle (i <= n-5).
    for i in range(n - 5, 0, -1):
        # ── Swing-high check ─────────────────────────────── #
        if not (window["high"].iloc[i] > window["high"].iloc[i - 1] and
                window["high"].iloc[i] > window["high"].iloc[i + 1]):
            continue
        if pd.isna(rsi_win.iloc[i]) or rsi_win.iloc[i] <= S6_OVERBOUGHT_RSI:
            continue

        peak_level  = float(window["high"].iloc[i])
        rsi_at_peak = float(rsi_win.iloc[i])

        # ── Spike low: minimum low after swing high ────────── #
        after_high = window.iloc[i + 1:]
        spike_abs  = int(after_high["low"].idxmin())   # absolute index in window
        spike_candle = window.iloc[spike_abs]
        spike_low    = float(spike_candle["low"])

        # ── Drop magnitude ────────────────────────────────── #
        drop_pct = (peak_level - spike_low) / peak_level
        if drop_pct < S6_MIN_DROP_PCT:
            continue

        # ── Clean downward spike: no candle between high and spike ─ #
        # exceeds peak_level (ensures price didn't retest peak before dropping)
        between = window.iloc[i + 1: spike_abs]
        if not between.empty and float(between["high"].max()) > peak_level:
            continue

        # ── Pivot candle must exist (spike cannot be the last row) ─ #
        if spike_abs + 1 >= n:
            continue

        # ── Direct V-pivot: immediate bullish candle ──────── #
        pivot = window.iloc[spike_abs + 1]
        if not (pivot["close"] > pivot["open"] and
                pivot["close"] > spike_candle["close"]):
            continue

        # ── Post-pivot guard: fakeout sweep must not have occurred yet ─ #
        # If any candle after the pivot already exceeded peak_level, the
        # two-phase watcher handles Phase 2 — evaluate_s6 returns HOLD.
        post_pivot = window.iloc[spike_abs + 2:]
        if not post_pivot.empty and float(post_pivot["high"].max()) > peak_level:
            continue

        # ── Recovery ratio guard: rejects U-bottoms ───────────────── #
        # Price must have recovered >= S6_MIN_RECOVERY_RATIO of the range
        # from spike_low back toward peak_level. Pairs still consolidating
        # at the bottom score near 0%; a clean V scores 20–50%+.
        current_close   = float(window.iloc[-1]["close"])
        recovery_ratio  = (current_close - spike_low) / (peak_level - spike_low)
        if recovery_ratio < S6_MIN_RECOVERY_RATIO:
            continue

        # ── Valid V-formation found ────────────────────────── #
        sl_price = peak_level * (1 + S6_SL_PCT)
        reason   = (
            f"V-formation ✅ | RSI at peak {rsi_at_peak:.1f} | "
            f"Drop {drop_pct * 100:.1f}% | Peak {peak_level:.5f} | "
            f"SL {sl_price:.5f}"
        )
        return "PENDING_SHORT", peak_level, sl_price, drop_pct, rsi_at_peak, reason

    return _hold(f"No V-formation in last {S6_SPIKE_LOOKBACK} days")