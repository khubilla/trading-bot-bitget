"""Tests for backtest_ig.py — fetch layer, session helpers, simulation."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
import types
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest


# ── Helpers ────────────────────────────────────────────────────────── #

def _make_df(rows):
    """Build a minimal OHLCV DataFrame with ts in ms."""
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "vol"])


def _dummy_instrument():
    """Minimal instrument config for testing."""
    return {
        "display_name":               "US30",
        "epic":                       "IX.D.DOW.IFD.IP",
        "session_start":              (0, 0),
        "session_end":                (23, 59),
        "daily_limit":                200,
        "htf_limit":                  50,
        "m15_limit":                  300,
        "s5_daily_ema_fast":          10,
        "s5_daily_ema_med":           20,
        "s5_daily_ema_slow":          50,
        "s5_htf_bos_lookback":        20,
        "s5_ltf_interval":            "15m",
        "s5_ob_lookback":             20,
        "s5_ob_min_impulse":          0.003,
        "s5_ob_min_range_pct":        0.001,
        "s5_choch_lookback":          10,
        "s5_max_entry_buffer":        0.003,
        "s5_sl_buffer_pct":           0.001,
        "s5_ob_invalidation_buffer_pct": 0.001,
        "s5_swing_lookback":          10,
        "s5_smc_fvg_filter":          False,
        "s5_smc_fvg_lookback":        5,
        "s5_leverage":                5,
        "s5_trade_size_pct":          0.02,
        "s5_min_rr":                  2.0,
        "s5_trail_range_pct":         0.02,
        "s5_use_candle_stops":        False,
        "s5_min_sr_clearance":        0.0,
        "s5_enabled":                 True,
        "contract_size":              1.0,
        "partial_size":               0.5,
        "point_value":                1.0,
        "currency":                   "USD",
    }


# ── Fetch layer tests ──────────────────────────────────────────────── #

def test_load_candles_fetches_and_writes_parquet(tmp_path, monkeypatch):
    """load_candles() fetches via yfinance and saves parquet on first call."""
    import backtest_ig as bt
    monkeypatch.setattr(bt, "_CACHE_DIR", tmp_path)

    sample = _make_df([[1_700_000_000_000, 40000, 40100, 39900, 40050, 1000]])
    monkeypatch.setattr(bt, "_fetch_yf", lambda name, interval: sample.copy())

    result = bt.load_candles("US30", "15m", no_fetch=False)

    parquet = tmp_path / "US30_15m.parquet"
    assert parquet.exists(), "Parquet not written"
    assert list(result.columns) == ["ts", "open", "high", "low", "close", "vol"]
    assert len(result) == 1


def test_load_candles_reads_cache_without_fetching(tmp_path, monkeypatch):
    """load_candles(no_fetch=True) reads parquet without calling _fetch_yf."""
    import backtest_ig as bt
    monkeypatch.setattr(bt, "_CACHE_DIR", tmp_path)

    sample = _make_df([[1_700_000_000_000, 40000, 40100, 39900, 40050, 1000]])
    sample.to_parquet(tmp_path / "US30_15m.parquet", index=False)

    fetch_called = []
    monkeypatch.setattr(bt, "_fetch_yf", lambda *a: fetch_called.append(1) or pd.DataFrame())

    result = bt.load_candles("US30", "15m", no_fetch=True)
    assert len(fetch_called) == 0, "_fetch_yf should not be called with no_fetch=True"
    assert len(result) == 1


def test_load_candles_no_fetch_missing_cache_raises(tmp_path, monkeypatch):
    """load_candles(no_fetch=True) raises FileNotFoundError when cache missing."""
    import backtest_ig as bt
    monkeypatch.setattr(bt, "_CACHE_DIR", tmp_path)
    with pytest.raises(FileNotFoundError):
        bt.load_candles("US30", "15m", no_fetch=True)


def test_fetch_yf_normalises_columns(monkeypatch):
    """_fetch_yf returns ts/open/high/low/close/vol with ts as int ms."""
    import backtest_ig as bt
    import pandas as pd

    # Simulate daily bar (yfinance returns 'Date' as datetime.date)
    import datetime
    fake_daily = pd.DataFrame({
        "Date": [datetime.date(2024, 1, 2)],
        "Open": [38000.0],
        "High": [38500.0],
        "Low":  [37500.0],
        "Close":[38200.0],
        "Volume":[100000],
    })
    fake_daily["Date"] = pd.to_datetime(fake_daily["Date"])

    # Simulate intraday bar (yfinance returns 'Datetime' as tz-aware Timestamp)
    fake_intra = pd.DataFrame({
        "Datetime": pd.to_datetime(["2024-01-02 10:00:00+00:00"]),
        "Open":  [38000.0],
        "High":  [38500.0],
        "Low":   [37500.0],
        "Close": [38200.0],
        "Volume":[5000],
    })

    class FakeTicker:
        def __init__(self, sym): pass
        def history(self, period, interval):
            return fake_daily if interval == "1d" else fake_intra

    import yfinance as yf
    monkeypatch.setattr(yf, "Ticker", FakeTicker)

    # Test daily
    df_1d = bt._fetch_yf("US30", "1D")
    assert list(df_1d.columns) == ["ts", "open", "high", "low", "close", "vol"]
    assert isinstance(int(df_1d.iloc[0]["ts"]), int)
    assert df_1d.iloc[0]["ts"] > 1_000_000_000_000  # sanity: ms since epoch

    # Test intraday
    df_15m = bt._fetch_yf("US30", "15m")
    assert list(df_15m.columns) == ["ts", "open", "high", "low", "close", "vol"]
    assert df_15m.iloc[0]["ts"] > 1_000_000_000_000
