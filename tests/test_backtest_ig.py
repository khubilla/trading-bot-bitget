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


# ── Session helper tests ───────────────────────────────────────────── #

def _ts_et(year, month, day, hour, minute, tz_str="America/New_York"):
    """Return Unix ms for a given ET wall-clock time."""
    import pytz as _pytz
    et = _pytz.timezone(tz_str)
    dt = et.localize(datetime(year, month, day, hour, minute))
    return int(dt.timestamp() * 1000)


def test_in_session_weekday_within_window():
    import backtest_ig as bt
    inst = _dummy_instrument()
    inst["session_start"] = (9, 30)
    inst["session_end"]   = (16, 0)
    ts = _ts_et(2026, 3, 10, 10, 0)  # Monday 10:00 ET
    assert bt._in_session(ts, inst) is True


def test_not_in_session_before_open():
    import backtest_ig as bt
    inst = _dummy_instrument()
    inst["session_start"] = (9, 30)
    inst["session_end"]   = (16, 0)
    ts = _ts_et(2026, 3, 10, 8, 0)  # Monday 08:00 ET — before open
    assert bt._in_session(ts, inst) is False


def test_not_in_session_weekend():
    import backtest_ig as bt
    inst = _dummy_instrument()
    inst["session_start"] = (9, 30)
    inst["session_end"]   = (16, 0)
    ts = _ts_et(2026, 3, 7, 10, 0)  # Saturday
    assert bt._in_session(ts, inst) is False


def test_is_session_end_at_boundary():
    import backtest_ig as bt
    inst = _dummy_instrument()
    inst["session_end"] = (23, 59)
    ts = _ts_et(2026, 3, 10, 23, 59)
    assert bt._is_session_end(ts, inst) is True


def test_not_session_end_before_boundary():
    import backtest_ig as bt
    inst = _dummy_instrument()
    inst["session_end"] = (23, 59)
    ts = _ts_et(2026, 3, 10, 10, 0)
    assert bt._is_session_end(ts, inst) is False


def test_not_in_session_at_session_end_boundary():
    """A bar at exactly session_end time is outside the session (half-open interval)."""
    import backtest_ig as bt
    inst = _dummy_instrument()
    inst["session_start"] = (9, 30)
    inst["session_end"]   = (16, 0)
    ts = _ts_et(2026, 3, 10, 16, 0)  # Monday 16:00 ET — exactly at session_end
    assert bt._in_session(ts, inst) is False


def test_is_session_end_weekend_returns_false():
    """_is_session_end returns False on weekends even if time matches."""
    import backtest_ig as bt
    inst = _dummy_instrument()
    inst["session_end"] = (23, 59)
    ts = _ts_et(2026, 3, 7, 23, 59)  # Saturday
    assert bt._is_session_end(ts, inst) is False


# ── PENDING state tests ────────────────────────────────────────────── #

def _make_pending(side="LONG", trigger=40500.0, ob_low=40000.0, ob_high=40500.0,
                  sl=39500.0, tp=42000.0, expires_offset_ms=4*3600*1000,
                  base_ts=1_700_000_000_000):
    return {
        "side":    side,
        "trigger": trigger,
        "sl":      sl,
        "tp":      tp,
        "ob_low":  ob_low,
        "ob_high": ob_high,
        "expires": base_ts + expires_offset_ms,
    }


def _make_bar(ts=1_700_000_000_000, lo=40200.0, hi=40600.0, op=40300.0, cl=40400.0):
    return {"ts": ts, "open": op, "high": hi, "low": lo, "close": cl, "vol": 100}


def test_pending_fill_long():
    import backtest_ig as bt
    inst    = _dummy_instrument()
    pending = _make_pending(side="LONG", trigger=40500.0)
    bar     = _make_bar(ts=pending["expires"] - 1000, lo=40490.0, hi=40600.0)  # low <= trigger
    action, price = bt._check_pending(bar, pending, inst)
    assert action == "fill"
    assert price == 40500.0


def test_pending_no_fill_long_bar_above_trigger():
    import backtest_ig as bt
    inst    = _dummy_instrument()
    pending = _make_pending(side="LONG", trigger=40500.0)
    bar     = _make_bar(ts=pending["expires"] - 1000, lo=40510.0, hi=40600.0)  # low > trigger
    action, _ = bt._check_pending(bar, pending, inst)
    assert action == "hold"


def test_pending_fill_short():
    import backtest_ig as bt
    inst    = _dummy_instrument()
    pending = _make_pending(side="SHORT", trigger=40000.0)
    bar     = _make_bar(ts=pending["expires"] - 1000, lo=39900.0, hi=40010.0)  # high >= trigger
    action, price = bt._check_pending(bar, pending, inst)
    assert action == "fill"
    assert price == 40000.0


def test_pending_ob_invalid_long():
    import backtest_ig as bt
    inst    = _dummy_instrument()
    inst["s5_ob_invalidation_buffer_pct"] = 0.001
    pending = _make_pending(side="LONG", ob_low=40000.0)
    # bar low < ob_low * (1 - 0.001) = 39960
    bar = _make_bar(ts=pending["expires"] - 1000, lo=39950.0, hi=40200.0)
    action, _ = bt._check_pending(bar, pending, inst)
    assert action == "ob_invalid"


def test_pending_ob_invalid_short():
    import backtest_ig as bt
    inst    = _dummy_instrument()
    inst["s5_ob_invalidation_buffer_pct"] = 0.001
    pending = _make_pending(side="SHORT", ob_high=40500.0)
    # bar high > ob_high * (1 + 0.001) = 40540.5
    bar = _make_bar(ts=pending["expires"] - 1000, lo=40400.0, hi=40550.0)
    action, _ = bt._check_pending(bar, pending, inst)
    assert action == "ob_invalid"


def test_pending_expired():
    import backtest_ig as bt
    inst    = _dummy_instrument()
    pending = _make_pending(base_ts=1_700_000_000_000, expires_offset_ms=0)
    # bar ts > expires
    bar = _make_bar(ts=pending["expires"] + 1, lo=40200.0, hi=40400.0)
    action, _ = bt._check_pending(bar, pending, inst)
    assert action == "expired"


def test_pending_session_end_cancels(monkeypatch):
    import backtest_ig as bt
    inst    = _dummy_instrument()
    pending = _make_pending()
    bar     = _make_bar(ts=pending["expires"] - 1000, lo=40200.0, hi=40400.0)
    monkeypatch.setattr(bt, "_is_session_end", lambda ts, i: True)
    action, _ = bt._check_pending(bar, pending, inst)
    assert action == "session_end"
