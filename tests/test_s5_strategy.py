import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pandas as pd
import numpy as np
import pytest


# ── DataFrame builders ────────────────────────────────────── #

def _make_daily_bullish(n=120) -> pd.DataFrame:
    """120 daily candles with EMA10 > EMA20 > EMA50 (rising trend)."""
    closes = [100.0 + i * 0.1 for i in range(n)]
    return pd.DataFrame({
        "open":  [c - 0.05 for c in closes],
        "high":  [c + 0.1  for c in closes],
        "low":   [c - 0.1  for c in closes],
        "close": closes,
        "vol":   [1000.0]  * n,
    })


def _make_htf_bos(prior_swing_high=1.0, current_close=1.05, n=14) -> pd.DataFrame:
    """1H candles with current close > prior_swing_high (BOS confirmed)."""
    rows = []
    for i in range(n - 1):
        rows.append({"open": 0.99, "high": prior_swing_high - 0.01,
                     "low": 0.95, "close": 0.98, "vol": 500.0})
    # Last candle: close above prior swing high
    rows.append({"open": 1.00, "high": current_close + 0.01,
                 "low": 0.99, "close": current_close, "vol": 500.0})
    return pd.DataFrame(rows)


def _make_m15_ob_touched_no_choch(
    ob_high=1.000, ob_low=0.983, n=80
) -> pd.DataFrame:
    """
    15m candles with:
    - A bullish OB at index n-40 (bearish candle before +1.5% impulse)
    - OB touched within last 20 candles (current candle low <= ob_high * 1.002)
    - NO ChoCH: last completed candle (index -2) close <= ob_high
    - Current price (index -1) close <= ob_high (not stale)
    """
    rows = []
    # Indices 0 to n-41: neutral, all below ob_high
    for _ in range(n - 40):
        rows.append({"open": 0.97, "high": 0.98, "low": 0.96, "close": 0.975, "vol": 200.0})

    # Index n-40: bearish OB candle (open=ob_high, close=ob_low+0.002)
    # high is exactly ob_high so it does NOT qualify as a swing target above entry_trigger
    rows.append({
        "open": ob_high, "high": ob_high,
        "low": ob_low, "close": ob_low + 0.002, "vol": 500.0
    })

    # Indices n-39 to n-38: bullish impulse (+1.5%)
    rows.append({"open": ob_low + 0.002, "high": ob_high - 0.005,
                 "low": ob_low + 0.001, "close": ob_high - 0.005, "vol": 600.0})
    rows.append({"open": ob_high - 0.005, "high": ob_high + 0.015,
                 "low": ob_high - 0.005, "close": ob_high + 0.015, "vol": 700.0})

    # Indices n-37 to n-3: price ranging above OB with a clear swing high pivot for TP target.
    # We insert a swing high at ob_high + 0.060 (~6%) to give R:R >= 2.0 (need ~4% above entry).
    # Swing pivot requires candle[i].high > candle[i-1].high and candle[i].high > candle[i+1].high.
    for i in range(35):
        if i == 15:
            # Swing high pivot candle — high well above neighbours
            rows.append({"open": ob_high + 0.020, "high": ob_high + 0.060,
                         "low": ob_high + 0.010, "close": ob_high + 0.025, "vol": 400.0})
        else:
            rows.append({"open": ob_high + 0.010, "high": ob_high + 0.020,
                         "low": ob_high + 0.005, "close": ob_high + 0.012, "vol": 300.0})

    # Index n-2 (last completed): close BELOW ob_high — no ChoCH
    rows.append({"open": ob_high + 0.005, "high": ob_high + 0.006,
                 "low": ob_high - 0.008, "close": ob_high - 0.002, "vol": 400.0})

    # Index n-1 (current): low touches OB zone, close still below ob_high
    rows.append({"open": ob_high - 0.002, "high": ob_high + 0.001,
                 "low": ob_high - 0.006, "close": ob_high - 0.001, "vol": 350.0})

    assert len(rows) == n
    return pd.DataFrame(rows)


# ── Tests ─────────────────────────────────────────────────── #

def test_pending_long_fires_without_choch():
    """PENDING_LONG fires when OB is touched even though no candle closed above ob_high."""
    from strategy import evaluate_s5
    daily = _make_daily_bullish()
    htf   = _make_htf_bos()
    m15   = _make_m15_ob_touched_no_choch()

    sig, trigger, sl, tp, ob_low, ob_high, reason = evaluate_s5(
        "TESTUSDT", daily, htf, m15, "BULLISH"
    )

    assert sig == "PENDING_LONG", f"Expected PENDING_LONG, got {sig}: {reason}"
    assert trigger == ob_high, f"entry_trigger should be ob_high={ob_high}, got {trigger}"


def test_entry_trigger_equals_ob_high():
    """entry_trigger must be ob_high exactly (no buffer added)."""
    from strategy import evaluate_s5
    daily = _make_daily_bullish()
    htf   = _make_htf_bos()
    m15   = _make_m15_ob_touched_no_choch(ob_high=1.000)

    sig, trigger, sl, tp, ob_low, ob_high_ret, reason = evaluate_s5(
        "TESTUSDT", daily, htf, m15, "BULLISH"
    )
    if sig == "PENDING_LONG":
        assert trigger == ob_high_ret
        assert trigger == 1.000


def test_stale_ob_returns_hold():
    """HOLD when current price is already >4% above ob_high (stale OB)."""
    from strategy import evaluate_s5
    daily = _make_daily_bullish()
    htf   = _make_htf_bos()

    ob_high = 1.000
    m15 = _make_m15_ob_touched_no_choch(ob_high=ob_high)
    # Override last candle so current close is 5% above ob_high
    m15_stale = m15.copy()
    m15_stale.iloc[-1] = {"open": 1.050, "high": 1.060, "low": 1.045, "close": 1.055, "vol": 300.0}

    sig, *_, reason = evaluate_s5("TESTUSDT", daily, htf, m15_stale, "BULLISH")
    assert sig == "HOLD", f"Expected HOLD for stale OB, got {sig}: {reason}"
    assert "stale" in reason.lower() or "far" in reason.lower() or "above" in reason.lower()


def test_return_tuple_has_7_elements():
    """evaluate_s5 always returns a 7-tuple regardless of signal."""
    from strategy import evaluate_s5
    daily = _make_daily_bullish()
    htf   = _make_htf_bos()
    m15   = _make_m15_ob_touched_no_choch()

    result = evaluate_s5("TESTUSDT", daily, htf, m15, "BULLISH")
    assert len(result) == 7


def test_no_immediate_long_signal():
    """evaluate_s5 never returns LONG — only PENDING_LONG or HOLD."""
    from strategy import evaluate_s5
    daily = _make_daily_bullish()
    htf   = _make_htf_bos()
    m15   = _make_m15_ob_touched_no_choch()

    sig, *_ = evaluate_s5("TESTUSDT", daily, htf, m15, "BULLISH")
    assert sig != "LONG", "evaluate_s5 should never return immediate LONG in new design"
