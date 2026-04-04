"""Tests for evaluate_s6 — V-Formation Liquidity Sweep Short."""
import pytest
import pandas as pd
import numpy as np


# ── Fixtures ──────────────────────────────────────────────── #

# Peak candle index in _make_v_df — used by tests that inspect the raw df
_PEAK_IDX  = 24
_SPIKE_IDX = 25
_PIVOT_IDX = 26


def _make_v_df():
    """
    Daily df with a clean V-formation (55 candles total ≥ 46 min_rows).

    Structure:
      - candles 0-9  : zigzag warmup (+4%/-2% alternating) so RSI is non-NaN
      - candles 10-24: strong uptrend (+4%/day) → RSI well above 70
      - candle 24    : swing-high (local max, RSI > 70) → peak_level = this candle's high
      - candle 25    : spike down 38% from peak
      - candle 26    : bullish pivot (close > open, close > spike close)
      - candles 27-54: slow recovery (+0.5%/day, 28 candles)
    """
    # Zigzag warmup (10 candles, indices 0-9)
    closes = [100.0]
    for i in range(9):
        closes.append(closes[-1] * (1.04 if i % 2 == 0 else 0.98))

    # Strong uptrend (15 candles, indices 10-24)
    for _ in range(15):
        closes.append(closes[-1] * 1.04)

    peak_high   = closes[_PEAK_IDX] * 1.005  # swing-high candle's high
    spike_close = peak_high * 0.62            # 38% drop
    pivot_close = spike_close * 1.14          # bullish pivot

    closes.append(spike_close)                                       # index 25
    closes.append(pivot_close)                                       # index 26
    closes += [pivot_close * (1.005 ** i) for i in range(1, 29)]    # indices 27-54

    n = len(closes)  # 55
    df = pd.DataFrame({
        "open":   [c * 0.998 for c in closes],
        "high":   [c * 1.003 for c in closes],
        "low":    [c * 0.997 for c in closes],
        "close":  closes,
        "volume": [1000.0] * n,
    })

    # Ensure candle 24 is a strict local maximum
    df.loc[_PEAK_IDX,     "high"] = peak_high
    df.loc[_PEAK_IDX - 1, "high"] = peak_high * 0.994    # just below peak
    df.loc[_SPIKE_IDX,    "high"] = spike_close * 1.002  # spike high < peak

    # Spike candle
    df.loc[_SPIKE_IDX, "open"]  = closes[_PEAK_IDX] * 0.99
    df.loc[_SPIKE_IDX, "close"] = spike_close
    df.loc[_SPIKE_IDX, "low"]   = spike_close * 0.995

    # Pivot candle: bullish (close > open, close > spike close)
    df.loc[_PIVOT_IDX, "open"]  = spike_close * 0.999
    df.loc[_PIVOT_IDX, "close"] = pivot_close
    df.loc[_PIVOT_IDX, "high"]  = pivot_close * 1.003
    df.loc[_PIVOT_IDX, "low"]   = spike_close * 0.997

    return df


def _make_shallow_drop_df():
    """Like _make_v_df but spike is only 18% — below S6_MIN_DROP_PCT (30%)."""
    df = _make_v_df()
    peak_high     = df.loc[_PEAK_IDX, "high"]
    shallow_close = peak_high * 0.82   # only 18% drop
    df.loc[_SPIKE_IDX, "close"] = shallow_close
    df.loc[_SPIKE_IDX, "low"]   = shallow_close * 0.995
    pivot_close = shallow_close * 1.05
    df.loc[_PIVOT_IDX, "open"]  = shallow_close * 0.999
    df.loc[_PIVOT_IDX, "close"] = pivot_close
    return df


def _make_no_pivot_df():
    """Like _make_v_df but spike is the last candle — no pivot candle yet."""
    df = _make_v_df()
    return df.iloc[:_SPIKE_IDX + 1].reset_index(drop=True)


def _make_bearish_pivot_df():
    """Like _make_v_df but pivot candle is bearish (close < open)."""
    df = _make_v_df()
    spike_close = df.loc[_SPIKE_IDX, "close"]
    df.loc[_PIVOT_IDX, "open"]  = spike_close * 1.10
    df.loc[_PIVOT_IDX, "close"] = spike_close * 1.02  # close < open → bearish
    return df


# ── Tests ─────────────────────────────────────────────────── #

def test_hold_when_direction_not_bearish():
    from strategy import evaluate_s6
    df = _make_v_df()
    sig, *_ = evaluate_s6("TEST", df, "BULLISH")
    assert sig == "HOLD"


def test_hold_when_s6_disabled(monkeypatch):
    import config_s6
    monkeypatch.setattr(config_s6, "S6_ENABLED", False)
    from strategy import evaluate_s6
    df = _make_v_df()
    sig, *_ = evaluate_s6("TEST", df, "BEARISH")
    assert sig == "HOLD"


def test_pending_short_on_valid_v_formation():
    from strategy import evaluate_s6
    df = _make_v_df()
    sig, peak_level, sl_price, drop_pct, rsi_at_peak, reason = evaluate_s6("TEST", df, "BEARISH")
    assert sig == "PENDING_SHORT"
    assert peak_level > 0
    assert sl_price > peak_level  # SL is above entry for a short
    assert drop_pct >= 0.30
    assert rsi_at_peak > 70.0
    assert "V-formation" in reason


def test_peak_level_equals_swing_high_candle_high():
    from strategy import evaluate_s6
    df = _make_v_df()
    _, peak_level, _, _, _, _ = evaluate_s6("TEST", df, "BEARISH")
    # peak_level must match the high of candle 24 (the swing high)
    assert abs(peak_level - df.loc[_PEAK_IDX, "high"]) < 1e-6


def test_sl_price_is_peak_times_sl_pct():
    import config_s6
    from strategy import evaluate_s6
    df = _make_v_df()
    _, peak_level, sl_price, _, _, _ = evaluate_s6("TEST", df, "BEARISH")
    expected_sl = peak_level * (1 + config_s6.S6_SL_PCT)
    assert abs(sl_price - expected_sl) < 1e-6


def test_hold_when_drop_below_threshold():
    from strategy import evaluate_s6
    df = _make_shallow_drop_df()
    sig, *_ = evaluate_s6("TEST", df, "BEARISH")
    assert sig == "HOLD"


def test_hold_when_no_pivot_candle():
    from strategy import evaluate_s6
    df = _make_no_pivot_df()
    sig, *_ = evaluate_s6("TEST", df, "BEARISH")
    assert sig == "HOLD"


def test_hold_when_pivot_candle_bearish():
    from strategy import evaluate_s6
    df = _make_bearish_pivot_df()
    sig, *_ = evaluate_s6("TEST", df, "BEARISH")
    assert sig == "HOLD"


def test_hold_when_rsi_at_swing_high_below_threshold(monkeypatch):
    """When RSI at the swing-high candle is < 70, no signal."""
    import strategy as strat
    # Return constant RSI of 60 everywhere
    monkeypatch.setattr(strat, "calculate_rsi", lambda closes, period=14: pd.Series([60.0] * len(closes), index=closes.index))
    from strategy import evaluate_s6
    df = _make_v_df()
    sig, *_ = evaluate_s6("TEST", df, "BEARISH")
    assert sig == "HOLD"
