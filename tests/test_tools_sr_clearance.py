"""Tests for nearest_daily_sr_clearance — distance (price units) from current price
to the nearest swing high (LONG resistance) or swing low (SHORT support)."""
import pandas as pd
from tools import nearest_daily_sr_clearance


def _df(highs, lows, closes):
    return pd.DataFrame({"high": highs, "low": lows, "close": closes,
                        "open": closes, "volume": [100]*len(closes)})


def test_long_clearance_returns_distance_to_nearest_swing_high_above():
    """For LONG, nearest swing high > current close defines resistance."""
    highs  = [10, 12, 15, 13, 14, 16, 18, 17, 15, 14]
    lows   = [ 9, 10, 13, 11, 13, 14, 16, 15, 13, 12]
    closes = [ 9, 11, 14, 12, 13, 15, 17, 16, 14, 13]
    df = _df(highs, lows, closes)
    clearance = nearest_daily_sr_clearance(df, direction="LONG")
    # Current close = 13; only qualifying swing high above is 18 (idx 6, swing_window=3) → clearance = 5.0
    assert clearance == 5.0


def test_short_clearance_returns_distance_to_nearest_swing_low_below():
    highs  = [20, 22, 25, 23, 24, 26, 28, 27, 25, 24]
    lows   = [19, 20, 23, 21, 23, 24, 26, 25, 23, 22]
    closes = [19, 21, 24, 22, 23, 25, 27, 26, 24, 23]
    df = _df(highs, lows, closes)
    clearance = nearest_daily_sr_clearance(df, direction="SHORT")
    # Current close = 23; nearest swing low below = 19 → clearance = 4.0
    assert clearance == 4.0


def test_no_swing_returns_large_clearance():
    """When no qualifying swing is found, return float('inf') so gates never block."""
    df = _df([10]*5, [10]*5, [10]*5)
    assert nearest_daily_sr_clearance(df, direction="LONG") == float("inf")
