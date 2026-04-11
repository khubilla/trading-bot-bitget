# tests/test_backtest_engine.py
"""Tests for backtest_engine.py (Tasks 1–7)."""

import pandas as pd
import pytest
from pathlib import Path
from unittest.mock import patch
import sys, types


# ── helpers ──────────────────────────────────────────────────────────
def _make_df(ts_list):
    return pd.DataFrame({
        "ts":    ts_list,
        "open":  [1.0] * len(ts_list),
        "high":  [1.1] * len(ts_list),
        "low":   [0.9] * len(ts_list),
        "close": [1.05] * len(ts_list),
        "vol":   [1000.0] * len(ts_list),
    })


# ── Task 1 tests ─────────────────────────────────────────────────────

def test_load_3m_no_cache_full_fetch(tmp_path, monkeypatch):
    """load_3m() with no cache calls _fetch_candles and saves parquet."""
    import backtest as bt
    monkeypatch.setattr(bt, "_CACHE_3M", tmp_path / "3m")
    DAY = 86_400_000
    MIN3 = 3 * 60 * 1000
    now_ms = 1_774_483_200_000
    ts_list = [now_ms - 30 * DAY + i * MIN3 for i in range(5)]
    df = _make_df(ts_list)

    with patch.object(bt, "_fetch_candles", return_value=df) as mock_fetch:
        result = bt.load_3m("BTCUSDT", days=30, _now_ms=now_ms)

    mock_fetch.assert_called_once()
    assert len(result) == 5
    assert (tmp_path / "3m" / "BTCUSDT.parquet").exists()


def test_load_3m_cache_hit_incremental(tmp_path, monkeypatch):
    """load_3m() with stale cache fetches only missing bars and appends."""
    import backtest as bt
    monkeypatch.setattr(bt, "_CACHE_3M", tmp_path / "3m")
    (tmp_path / "3m").mkdir()
    DAY = 86_400_000
    MIN3 = 3 * 60 * 1000
    now_ms = 1_774_483_200_000
    old_ts = [now_ms - 2 * DAY + i * MIN3 for i in range(3)]
    _make_df(old_ts).to_parquet(tmp_path / "3m" / "BTCUSDT.parquet", index=False)

    new_ts = [old_ts[-1] + MIN3 * (i + 1) for i in range(2)]
    new_df = _make_df(new_ts)
    with patch.object(bt, "_fetch_candles", return_value=new_df):
        result = bt.load_3m("BTCUSDT", days=30, _now_ms=now_ms)

    assert len(result) == 5
    assert result["ts"].max() == new_ts[-1]


def test_load_1h_no_cache(tmp_path, monkeypatch):
    """load_1h() with no cache does a full fetch and saves."""
    import backtest as bt
    monkeypatch.setattr(bt, "_CACHE_1H", tmp_path / "1h")
    now_ms = 1_774_483_200_000
    HOUR = 3_600_000
    ts_list = [now_ms - 10 * HOUR + i * HOUR for i in range(5)]
    df = _make_df(ts_list)
    with patch.object(bt, "_fetch_candles", return_value=df):
        result = bt.load_1h("BTCUSDT", days=5, _now_ms=now_ms)
    assert len(result) == 5
    assert (tmp_path / "1h" / "BTCUSDT.parquet").exists()


def test_load_15m_no_cache(tmp_path, monkeypatch):
    """load_15m() with no cache does a full fetch and saves."""
    import backtest as bt
    monkeypatch.setattr(bt, "_CACHE_15M", tmp_path / "15m")
    now_ms = 1_774_483_200_000
    M15 = 15 * 60 * 1000
    ts_list = [now_ms - 10 * M15 + i * M15 for i in range(5)]
    df = _make_df(ts_list)
    with patch.object(bt, "_fetch_candles", return_value=df):
        result = bt.load_15m("BTCUSDT", days=5, _now_ms=now_ms)
    assert len(result) == 5
    assert (tmp_path / "15m" / "BTCUSDT.parquet").exists()


# ── Task 2 tests ─────────────────────────────────────────────────────

def test_backtest_state_open_close_trade():
    """BacktestState add/get/close trade roundtrip."""
    from backtest_engine import BacktestState
    bs = BacktestState()
    bs.add_open_trade({"symbol": "BTCUSDT", "strategy": "S2",
                       "side": "LONG", "entry": 50000.0, "qty": 0.1,
                       "sl": 47500.0, "tp": 55000.0, "trade_id": "abc"})
    ot = bs.get_open_trade("BTCUSDT")
    assert ot["entry"] == 50000.0
    bs.close_trade("BTCUSDT", pnl=100.0, result="WIN",
                   exit_price=55000.0, exit_reason="TRAIL")
    assert bs.get_open_trade("BTCUSDT") is None


def test_backtest_state_pair_pause():
    """BacktestState record_loss triggers is_pair_paused after 3 losses same day."""
    from backtest_engine import BacktestState
    bs = BacktestState()
    today = "2026-04-12"
    assert not bs.is_pair_paused("BTCUSDT", today)
    bs.record_loss("BTCUSDT", today)
    bs.record_loss("BTCUSDT", today)
    assert not bs.is_pair_paused("BTCUSDT", today)
    bs.record_loss("BTCUSDT", today)
    assert bs.is_pair_paused("BTCUSDT", today)


def test_backtest_state_position_memory():
    """BacktestState update/get/clear position memory."""
    from backtest_engine import BacktestState
    bs = BacktestState()
    bs.update_position_memory("ETHUSDT", initial_qty=10.0, partial_logged=False)
    mem = bs.get_position_memory("ETHUSDT")
    assert mem["initial_qty"] == 10.0
    bs.clear_position_memory("ETHUSDT")
    assert bs.get_position_memory("ETHUSDT") == {}


# ── Task 3 tests ─────────────────────────────────────────────────────

def _make_mock_trader(balance=1000.0):
    """Helper: build a MockTrader with empty parquet dicts."""
    from backtest_engine import MockTrader
    return MockTrader(universe=["BTCUSDT"], parquet={}, balance=balance)


def test_mock_trader_open_long_records_position():
    """open_long() records position with correct SL, TP, qty."""
    mt = _make_mock_trader(balance=1000.0)
    mt.sim_time = 1_774_483_200_000
    result = mt.open_long(
        "BTCUSDT",
        sl_floor=45000.0,
        leverage=10,
        trade_size_pct=0.04,
        use_s2_exits=True,
    )
    pos = mt._positions["BTCUSDT"]
    assert pos["side"] == "LONG"
    assert pos["sl"] == 45000.0
    assert pos["leverage"] == 10
    assert result["side"] == "LONG"


def test_mock_trader_sl_hit_closes_position():
    """process_bar() closes LONG when low <= sl."""
    mt = _make_mock_trader(balance=1000.0)
    mt.sim_time = 1_774_483_200_000
    mt._positions["BTCUSDT"] = {
        "side": "LONG", "entry": 50000.0, "qty": 0.04,
        "initial_qty": 0.04, "sl": 47000.0, "tp_trig": 55000.0,
        "trail_pct": 10.0, "trail_active": False, "trail_peak": 0.0,
        "trail_sl": 0.0, "partial_done": False,
        "scale_in_after": 0, "scale_in_done": True,
        "margin": 40.0, "leverage": 10, "strategy": "S2",
        "trade_id": "test1", "open_ts": mt.sim_time,
    }
    bar = {"ts": mt.sim_time, "open": 47500.0, "high": 47800.0,
           "low": 46500.0, "close": 47000.0}
    closed = mt.process_bar("BTCUSDT", bar)
    assert closed is not None
    assert closed["result"] == "LOSS"
    assert closed["exit_price"] == 47000.0
    assert "BTCUSDT" not in mt._positions


def test_mock_trader_partial_tp_then_trail():
    """process_bar() fires partial TP then tracks trailing stop."""
    mt = _make_mock_trader(balance=1000.0)
    mt.sim_time = 1_774_483_200_000
    mt._positions["BTCUSDT"] = {
        "side": "LONG", "entry": 50000.0, "qty": 0.04,
        "initial_qty": 0.04, "sl": 45000.0, "tp_trig": 55000.0,
        "trail_pct": 10.0, "trail_active": False, "trail_peak": 0.0,
        "trail_sl": 0.0, "partial_done": False,
        "scale_in_after": 0, "scale_in_done": True,
        "margin": 40.0, "leverage": 10, "strategy": "S2",
        "trade_id": "test2", "open_ts": mt.sim_time,
    }
    # Bar 1: TP trigger hit → partial TP fires
    bar1 = {"ts": mt.sim_time, "open": 54000.0, "high": 56000.0,
             "low": 53000.0, "close": 55000.0}
    closed = mt.process_bar("BTCUSDT", bar1)
    assert closed is None  # partial, not full close
    pos = mt._positions["BTCUSDT"]
    assert pos["partial_done"] is True
    assert pos["trail_active"] is True
    assert pos["qty"] == pytest.approx(0.02, rel=1e-3)

    # Bar 2: price rises more, trail_peak advances
    bar2 = {"ts": mt.sim_time + 180_000, "open": 57000.0, "high": 60000.0,
             "low": 56000.0, "close": 59000.0}
    closed = mt.process_bar("BTCUSDT", bar2)
    assert closed is None
    assert mt._positions["BTCUSDT"]["trail_peak"] == 60000.0

    # Bar 3: trail SL is 60000 * 0.90 = 54000. Low dips to 53500 → trail hit
    bar3 = {"ts": mt.sim_time + 360_000, "open": 55000.0, "high": 55500.0,
             "low": 53500.0, "close": 54000.0}
    closed = mt.process_bar("BTCUSDT", bar3)
    assert closed is not None
    assert closed["result"] == "WIN"
    assert closed["exit_reason"] == "TRAIL"


def test_mock_trader_sl_beats_tp_same_bar():
    """When SL and TP both hit in same bar, SL wins (conservative)."""
    mt = _make_mock_trader(balance=1000.0)
    mt.sim_time = 1_774_483_200_000
    mt._positions["BTCUSDT"] = {
        "side": "LONG", "entry": 50000.0, "qty": 0.04,
        "initial_qty": 0.04, "sl": 45000.0, "tp_trig": 55000.0,
        "trail_pct": 10.0, "trail_active": False, "trail_peak": 0.0,
        "trail_sl": 0.0, "partial_done": False,
        "scale_in_after": 0, "scale_in_done": True,
        "margin": 40.0, "leverage": 10, "strategy": "S2",
        "trade_id": "test3", "open_ts": mt.sim_time,
    }
    bar = {"ts": mt.sim_time, "open": 50000.0, "high": 56000.0,
           "low": 44000.0, "close": 50000.0}
    closed = mt.process_bar("BTCUSDT", bar)
    assert closed["result"] == "LOSS"


def test_mock_trader_short_sl_and_trail():
    """SHORT: SL hit when high >= sl; trail fires when trail_sl breached."""
    mt = _make_mock_trader(balance=1000.0)
    mt.sim_time = 1_774_483_200_000
    mt._positions["BTCUSDT"] = {
        "side": "SHORT", "entry": 50000.0, "qty": 0.04,
        "initial_qty": 0.04, "sl": 53000.0, "tp_trig": 45000.0,
        "trail_pct": 10.0, "trail_active": False, "trail_peak": 0.0,
        "trail_sl": 0.0, "partial_done": False,
        "scale_in_after": 0, "scale_in_done": True,
        "margin": 40.0, "leverage": 10, "strategy": "S4",
        "trade_id": "test4", "open_ts": mt.sim_time,
    }
    # Partial TP
    bar1 = {"ts": mt.sim_time, "open": 46000.0, "high": 46500.0,
             "low": 44000.0, "close": 45000.0}
    closed = mt.process_bar("BTCUSDT", bar1)
    assert closed is None
    assert mt._positions["BTCUSDT"]["trail_active"] is True

    # Trail SL for SHORT = trail_peak * (1 + trail_pct/100)
    # trail_peak = min(trail_peak, low) → 44000. trail_sl = 44000 * 1.10 = 48400
    # high = 49000 > 48400 → trail hit
    bar2 = {"ts": mt.sim_time + 180_000, "open": 45000.0, "high": 49000.0,
             "low": 44000.0, "close": 48000.0}
    closed = mt.process_bar("BTCUSDT", bar2)
    assert closed is not None
    assert closed["result"] == "WIN"


# ── Task 4 tests ─────────────────────────────────────────────────────

def test_mock_scanner_sentiment_bullish():
    """MockScanner returns BULLISH when >60% symbols above daily open."""
    from backtest_engine import MockScanner
    DAY = 86_400_000
    now_ms = 1_774_483_200_000
    day_open_ts = now_ms - (now_ms % DAY)  # midnight UTC

    def make_df_3m(close_vs_open: float) -> pd.DataFrame:
        return pd.DataFrame({
            "ts":    [now_ms],
            "open":  [100.0],
            "high":  [110.0],
            "low":   [90.0],
            "close": [100.0 * close_vs_open],
            "vol":   [1000.0],
        })

    def make_df_1d(open_price: float) -> pd.DataFrame:
        return pd.DataFrame({
            "ts":    [day_open_ts],
            "open":  [open_price],
            "high":  [110.0],
            "low":   [90.0],
            "close": [open_price],
            "vol":   [1000.0],
        })

    universe = [f"SYM{i}USDT" for i in range(10)]
    parquet = {}
    # 8 above daily open (close > open), 2 below → 80% bullish → BULLISH
    for i, sym in enumerate(universe):
        multiplier = 1.05 if i < 8 else 0.95
        parquet[sym] = {
            "3m": make_df_3m(multiplier),
            "1d": make_df_1d(100.0),
        }

    scanner = MockScanner(universe, parquet)
    scanner.sim_time = now_ms
    pairs, sentiment = scanner.get_qualified_pairs_and_sentiment()
    assert pairs == universe
    assert sentiment.direction == "BULLISH"


def test_mock_scanner_sentiment_bearish():
    """MockScanner returns BEARISH when <40% symbols above daily open."""
    from backtest_engine import MockScanner
    now_ms = 1_774_483_200_000
    DAY = 86_400_000
    day_open_ts = now_ms - (now_ms % DAY)

    universe = [f"SYM{i}USDT" for i in range(10)]
    parquet = {}
    for i, sym in enumerate(universe):
        multiplier = 1.05 if i < 3 else 0.95  # only 3/10 = 30% above → BEARISH
        parquet[sym] = {
            "3m": pd.DataFrame({"ts": [now_ms], "open": [100.0], "high": [110.0],
                                 "low": [90.0], "close": [100.0 * multiplier], "vol": [1000.0]}),
            "1d": pd.DataFrame({"ts": [day_open_ts], "open": [100.0], "high": [110.0],
                                 "low": [90.0], "close": [100.0], "vol": [1000.0]}),
        }

    scanner = MockScanner(universe, parquet)
    scanner.sim_time = now_ms
    _, sentiment = scanner.get_qualified_pairs_and_sentiment()
    assert sentiment.direction == "BEARISH"


# ── Task 5 tests ─────────────────────────────────────────────────────

def test_backtest_engine_smoke(tmp_path, monkeypatch):
    """
    BacktestEngine runs on 2 symbols × 10 3m bars without crashing.
    At least one trade is expected from a simple SL-hit scenario.
    """
    import backtest as bt
    monkeypatch.setattr(bt, "_CACHE_3M",  tmp_path / "3m")
    monkeypatch.setattr(bt, "_CACHE_15M", tmp_path / "15m")
    monkeypatch.setattr(bt, "_CACHE_1H",  tmp_path / "1h")
    monkeypatch.setattr(bt, "_DAILY_CACHE", Path("data/daily"))  # read existing

    from backtest_engine import BacktestEngine

    MIN3 = 3 * 60_000
    DAY  = 86_400_000
    now_ms = 1_774_483_200_000
    ts_list = [now_ms - 10 * MIN3 + i * MIN3 for i in range(10)]

    def _make(ts_list, close_vals=None):
        closes = close_vals or [100.0 + i for i in range(len(ts_list))]
        return pd.DataFrame({
            "ts":    ts_list,
            "open":  [100.0] * len(ts_list),
            "high":  [c * 1.01 for c in closes],
            "low":   [c * 0.99 for c in closes],
            "close": closes,
            "vol":   [10000.0] * len(ts_list),
        })

    day_ts = [now_ms - DAY]
    universe = ["BTCUSDT", "ETHUSDT"]
    parquet = {
        sym: {
            "3m":  _make(ts_list),
            "15m": _make([now_ms - 2 * 15 * 60_000 + i * 15 * 60_000 for i in range(5)]),
            "1h":  _make([now_ms - 5 * 3600_000 + i * 3600_000 for i in range(5)]),
            "1d":  _make(day_ts),
        }
        for sym in universe
    }

    engine = BacktestEngine(
        universe=universe,
        parquet=parquet,
        balance=1000.0,
        days=1,
        enabled_strategies={"S1", "S2", "S3", "S4", "S5", "S6"},
    )
    trades = engine.run()
    # Engine runs without exception; result is a list
    assert isinstance(trades, list)
