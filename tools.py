"""
tools.py — Shared tool functions used across strategies.

These are generic structure/pattern-finding utilities (swing highs/lows, order
blocks, FVGs, nearest support/resistance, HTF direction check). No strategy
rules live here. Any strategy file is free to import what it needs.
"""

import pandas as pd


# ════════════════════════════════════════════════════════════
#  SUPPORT / RESISTANCE
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
    """
    df = daily_df.iloc[-(lookback + 1):-1] if len(daily_df) > lookback + 1 else daily_df.iloc[:-1]
    for _, row in df.iloc[::-1].iterrows():
        low = float(row["low"])
        if price_floor is not None and low <= price_floor:
            continue
        o, c = float(row["open"]), float(row["close"])
        if c >= o:
            continue
        bp = (o - c) / o if o else 0
        if bp >= min_body_pct:
            return low
    return None


# ════════════════════════════════════════════════════════════
#  HTF DIRECTION
# ════════════════════════════════════════════════════════════

def check_htf(htf_df: pd.DataFrame) -> tuple[bool, bool]:
    """Return (bull, bear): did current HTF candle break prev high / prev low?"""
    if len(htf_df) < 2:
        return False, False
    prev    = htf_df.iloc[-2]
    current = htf_df.iloc[-1]
    bull    = float(current["high"]) > float(prev["high"])
    bear    = float(current["low"])  < float(prev["low"])
    return bull, bear


# ════════════════════════════════════════════════════════════
#  CANDLE GEOMETRY
# ════════════════════════════════════════════════════════════

def body_pct(row: pd.Series) -> float:
    """Body size as fraction of open price."""
    o = float(row["open"])
    return abs(float(row["close"]) - o) / o if o > 0 else 0.0


def upper_wick(row: pd.Series) -> float:
    """Upper wick size."""
    return float(row["high"]) - max(float(row["close"]), float(row["open"]))


def body_size(row: pd.Series) -> float:
    return abs(float(row["close"]) - float(row["open"]))


# ════════════════════════════════════════════════════════════
#  SWING TARGETS
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


# ════════════════════════════════════════════════════════════
#  FAIR VALUE GAPS + ORDER BLOCKS
# ════════════════════════════════════════════════════════════

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
        if float(c["close"]) <= float(c["open"]):
            i -= 1
            continue
        run_end = i
        run_start = i
        while (run_start > 0 and
               float(candles.iloc[run_start - 1]["close"]) > float(candles.iloc[run_start - 1]["open"])):
            run_start -= 1
        if run_end - run_start < 1:
            i = run_start - 1
            continue
        first_open  = float(candles.iloc[run_start]["open"])
        last_close  = float(candles.iloc[run_end]["close"])
        if first_open <= 0 or (last_close - first_open) / first_open < min_impulse_pct:
            i = run_start - 1
            continue
        ob_idx = run_start - 1
        while ob_idx >= 0:
            ob_c = candles.iloc[ob_idx]
            if float(ob_c["close"]) < float(ob_c["open"]):
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
        if float(c["close"]) >= float(c["open"]):
            i -= 1
            continue
        run_end = i
        run_start = i
        while (run_start > 0 and
               float(candles.iloc[run_start - 1]["close"]) < float(candles.iloc[run_start - 1]["open"])):
            run_start -= 1
        if run_end - run_start < 1:
            i = run_start - 1
            continue
        first_open  = float(candles.iloc[run_start]["open"])
        last_close  = float(candles.iloc[run_end]["close"])
        if first_open <= 0 or (first_open - last_close) / first_open < min_impulse_pct:
            i = run_start - 1
            continue
        ob_idx = run_start - 1
        while ob_idx >= 0:
            ob_c = candles.iloc[ob_idx]
            if float(ob_c["close"]) > float(ob_c["open"]):
                return float(ob_c["open"]), float(ob_c["high"])
            ob_idx -= 1
        i = run_start - 1
    return None
