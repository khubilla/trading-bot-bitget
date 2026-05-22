"""Tests for backtest_ig.py — S1 strategy support, grid search, CLI flags."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import subprocess
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest


# ── Helpers ────────────────────────────────────────────────────────── #

def _make_df(rows):
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "vol"])


def _make_candle_series(n, start_ms, step_ms, base_price=40000.0):
    return _make_df([
        [start_ms + i * step_ms, base_price, base_price + 100, base_price - 100, base_price, 100]
        for i in range(n)
    ])


def _dummy_instrument():
    """Minimal instrument config for S1+S5 testing."""
    return {
        "display_name":               "US30",
        "epic":                       "IX.D.DOW.IFD.IP",
        "session_start":              (0, 0),
        "session_end":                (23, 59),
        "daily_limit":                200,
        "htf_limit":                  50,
        "m15_limit":                  300,
        # S5 keys
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
        # S1 keys
        "s1_enabled":                 False,
        "s1_adx_trend_threshold":     25,
        "s1_daily_ema_slow":          20,
        "s1_daily_rsi_long_thresh":   60,
        "s1_daily_rsi_short_thresh":  40,
        "s1_rsi_period":              14,
        "s1_rsi_long_thresh":         65,
        "s1_rsi_short_thresh":        35,
        "s1_consolidation_candles":   2,
        "s1_consolidation_range_pct": 0.003,
        "s1_breakout_buffer_pct":     0.0005,
        "s1_atr_period":              14,
        "s1_sl_atr_mult":             1.5,
        "s1_tp_atr_mult":             3.0,
        "s1_sl_buffer_pct":           0.001,
        "s1_sr_clearance_atr_mult":   0.0,   # disabled for tests
        "s1_contract_size":           0.04,
        "s1_partial_size":            0.02,
        "s1_use_swing_trail":         False,
        "s1_swing_lookback":          20,
        "pending_expiry_hours":       4,
        # shared
        "contract_size":              1.0,
        "partial_size":               0.5,
        "point_value":                1.0,
        "currency":                   "USD",
    }


# ── T19: CLI flags ─────────────────────────────────────────────────── #

def test_help_lists_strategy_and_grid_flags():
    """--help output mentions both --strategy and --grid flags."""
    result = subprocess.run(
        [sys.executable, "backtest_ig.py", "--help"],
        capture_output=True, text=True,
        cwd=os.path.dirname(os.path.dirname(__file__))
    )
    output = result.stdout + result.stderr
    assert "--strategy" in output, "--strategy flag missing from --help"
    assert "--grid"     in output, "--grid flag missing from --help"
    assert "s1"         in output
    assert "s5"         in output


# ── T20: strategy mode routing ────────────────────────────────────── #

def test_strategy_s5_only_walks_15m(monkeypatch):
    """
    When --strategy s5, run_instrument should not load 3m candles.
    Verified by ensuring df_3m is never passed / used when mode=s5.
    """
    import backtest_ig as bt

    load_calls = []
    _orig_load = bt.load_candles

    def _track_load(name, interval, no_fetch=False):
        load_calls.append(interval)
        return _make_candle_series(300, 1_700_000_000_000, 900_000)

    monkeypatch.setattr(bt, "load_candles", _track_load)
    monkeypatch.setattr(bt, "INSTRUMENTS", [_dummy_instrument()])
    monkeypatch.setattr(bt, "evaluate_s5",
        lambda *a, **kw: ("HOLD", 0.0, 0.0, 0.0, 0.0, 0.0, "hold"))
    monkeypatch.setattr(bt, "_in_session",    lambda ts, inst: True)
    monkeypatch.setattr(bt, "_is_session_end", lambda ts, inst: False)
    monkeypatch.setattr(bt, "calculate_ema",
        lambda series, period: pd.Series([100.0] * len(series)))

    import sys, tempfile, pathlib
    out = pathlib.Path(tempfile.mktemp(suffix=".html"))
    monkeypatch.setattr(sys, "argv",
        ["backtest_ig.py", "--no-fetch", "--strategy", "s5", "--output", str(out)])

    # IMPORTANT: no-fetch will hit FileNotFoundError unless we monkeypatch load_candles
    # already done above. The tracked intervals tell us if "3m" was requested.
    try:
        bt.main()
    except SystemExit:
        pass

    assert "3m" not in load_calls, f"3m candles were loaded in s5 mode: {load_calls}"


def test_s1_eval_helper_returns_pending_or_hold(monkeypatch):
    """
    _eval_s1_at_idle returns a pending dict or None.
    When evaluate_s1 returns LONG, we get a pending dict with strategy=S1.
    """
    import backtest_ig as bt

    inst = _dummy_instrument()
    inst["s1_enabled"] = True
    inst["_last_atr"]  = 500.0   # pre-set so compute_s1_sl/tp can run

    start  = 1_700_000_000_000
    step3  = 180_000
    n      = 300
    df_1d  = _make_candle_series(300, start - 300 * 86_400_000, 86_400_000)
    df_1h  = _make_candle_series(300, start - 300 * 3_600_000,  3_600_000)
    df_3m  = _make_candle_series(n,   start, step3)

    # Mock evaluate_s1 to return a LONG signal
    monkeypatch.setattr(bt, "evaluate_s1",
        lambda *a, **kw: ("LONG", 70.0, 40500.0, 40000.0, 30.0, 65.0))
    monkeypatch.setattr(bt, "calculate_ema",
        lambda series, period: pd.Series([100.0] * len(series)))

    i = 250
    bar_ts = int(df_3m.iloc[i]["ts"])

    result = bt._eval_s1_at_idle(i, bar_ts, df_1d, df_1h, df_3m, inst)

    assert result is not None, "_eval_s1_at_idle returned None on LONG signal"
    assert result["strategy"] == "S1"
    assert result["side"]     == "LONG"
    assert result["sl"]       < result["entry"]   # SL below entry for LONG
    assert result["tp"]       > result["entry"]   # TP above entry for LONG


def test_s1_eval_helper_returns_none_when_disabled():
    """_eval_s1_at_idle returns None when s1_enabled=False."""
    import backtest_ig as bt

    inst  = _dummy_instrument()
    # s1_enabled=False is default in _dummy_instrument()

    start = 1_700_000_000_000
    df_1d = _make_candle_series(300, start - 300 * 86_400_000, 86_400_000)
    df_1h = _make_candle_series(300, start - 300 * 3_600_000,  3_600_000)
    df_3m = _make_candle_series(300, start, 180_000)

    result = bt._eval_s1_at_idle(250, int(df_3m.iloc[250]["ts"]), df_1d, df_1h, df_3m, inst)
    assert result is None


def test_run_instrument_s5_mode_no_3m(monkeypatch):
    """run_instrument with strategy_mode='s5' and no df_3m returns the same shape as before."""
    import backtest_ig as bt

    monkeypatch.setattr(bt, "evaluate_s5",
        lambda *a, **kw: ("HOLD", 0.0, 0.0, 0.0, 0.0, 0.0, "hold"))
    monkeypatch.setattr(bt, "_in_session",    lambda ts, inst: True)
    monkeypatch.setattr(bt, "_is_session_end", lambda ts, inst: False)
    monkeypatch.setattr(bt, "calculate_ema",
        lambda series, period: pd.Series([100.0] * len(series)))

    inst  = _dummy_instrument()
    start = 1_700_000_000_000
    step  = 900_000
    n     = inst["daily_limit"] + 50
    df_1d  = _make_candle_series(300, start - 300 * 86_400_000, 86_400_000)
    df_1h  = _make_candle_series(300, start - 300 * 3_600_000,  3_600_000)
    df_15m = _make_candle_series(n,   start, step)

    result = bt.run_instrument(inst, df_1d, df_1h, df_15m, df_3m=None, strategy_mode="s5")

    assert "instrument" in result
    assert "trades"     in result
    assert "cancelled"  in result
    assert result["instrument"] == "US30"


def test_run_instrument_s1_mode_with_no_3m_returns_empty():
    """run_instrument with strategy_mode='s1' and df_3m=None returns empty trades."""
    import backtest_ig as bt

    inst  = _dummy_instrument()
    start = 1_700_000_000_000
    df_1d  = _make_candle_series(300, start - 300 * 86_400_000, 86_400_000)
    df_1h  = _make_candle_series(300, start - 300 * 3_600_000,  3_600_000)
    df_15m = _make_candle_series(300, start, 900_000)

    result = bt.run_instrument(inst, df_1d, df_1h, df_15m, df_3m=None, strategy_mode="s1")
    assert result["trades"]    == []
    assert result["cancelled"] == []


# ── T21: grid search ──────────────────────────────────────────────── #

def test_grid_search_produces_ranked_combos(monkeypatch):
    """
    _run_grid_s1 returns a list of dicts with expected keys, sorted by total_pnl desc.
    """
    import backtest_ig as bt

    inst  = _dummy_instrument()
    inst["s1_enabled"] = True

    start = 1_700_000_000_000
    step3 = 180_000
    df_1d  = _make_candle_series(300, start - 300 * 86_400_000, 86_400_000)
    df_1h  = _make_candle_series(300, start - 300 * 3_600_000,  3_600_000)
    df_15m = _make_candle_series(300, start, 900_000)
    df_3m  = _make_candle_series(300, start, step3)

    # Stub out evaluate_s1 to always return HOLD to keep it fast
    monkeypatch.setattr(bt, "evaluate_s1",
        lambda *a, **kw: ("HOLD", 50.0, 0.0, 0.0, 0.0, 0.0))
    monkeypatch.setattr(bt, "evaluate_s5",
        lambda *a, **kw: ("HOLD", 0.0, 0.0, 0.0, 0.0, 0.0, "hold"))
    monkeypatch.setattr(bt, "_in_session",    lambda ts, inst: True)
    monkeypatch.setattr(bt, "_is_session_end", lambda ts, inst: False)
    monkeypatch.setattr(bt, "calculate_ema",
        lambda series, period: pd.Series([100.0] * len(series)))
    monkeypatch.setattr(bt, "calculate_atr",
        lambda df, period=14: pd.Series([500.0] * len(df)))

    rows = bt._run_grid_s1(inst, df_1d, df_1h, df_15m, df_3m)

    expected_combos = (
        len(bt.S1_GRID_PARAMS["s1_sl_atr_mult"]) *
        len(bt.S1_GRID_PARAMS["s1_tp_atr_mult"]) *
        len(bt.S1_GRID_PARAMS["s1_consolidation_range_pct"]) *
        len(bt.S1_GRID_PARAMS["s1_breakout_buffer_pct"])
    )
    assert len(rows) == expected_combos, (
        f"Expected {expected_combos} grid rows, got {len(rows)}"
    )

    # Check required keys
    required_keys = {
        "s1_sl_atr_mult", "s1_tp_atr_mult",
        "s1_consolidation_range_pct", "s1_breakout_buffer_pct",
        "trade_count", "win_rate", "total_pnl", "max_drawdown",
    }
    for r in rows:
        missing = required_keys - set(r.keys())
        assert not missing, f"Grid row missing keys: {missing}"

    # Verify sorted by total_pnl descending
    pnls = [r["total_pnl"] for r in rows]
    assert pnls == sorted(pnls, reverse=True), "Grid rows not sorted by total_pnl desc"


# ── T22: report HTML ──────────────────────────────────────────────── #

def test_report_includes_strategy_column():
    """build_report HTML includes 'Strategy' column header."""
    import backtest_ig as bt

    stats = {
        "name":          "US30",
        "signals":       2,
        "filled":        2,
        "fill_rate":     100.0,
        "wins":          1,
        "losses":        1,
        "win_rate":      50.0,
        "partial_rate":  0.0,
        "avg_win_pts":   200.0,
        "avg_loss_pts":  -100.0,
        "profit_factor": 2.0,
        "total_pnl_pts": 100.0,
        "cancelled":     {"OB_INVALID": 0, "EXPIRED": 0, "SESSION_END": 0},
        "s1_stats":      {"signals": 0, "filled": 0, "fill_rate": 0,
                          "wins": 0, "losses": 0, "win_rate": 0,
                          "partial_rate": 0, "avg_win_pts": 0, "avg_loss_pts": 0,
                          "profit_factor": 0, "total_pnl_pts": 0,
                          "cancelled": {"OB_INVALID": 0, "EXPIRED": 0, "SESSION_END": 0}},
        "s5_stats":      {"signals": 2, "filled": 2, "fill_rate": 100,
                          "wins": 1, "losses": 1, "win_rate": 50,
                          "partial_rate": 0, "avg_win_pts": 200, "avg_loss_pts": -100,
                          "profit_factor": 2.0, "total_pnl_pts": 100,
                          "cancelled": {"OB_INVALID": 0, "EXPIRED": 0, "SESSION_END": 0}},
        "trades": [
            {
                "strategy": "S5", "side": "LONG", "entry": 40000.0, "sl": 39000.0,
                "tp": 42000.0, "tp1": 41000.0, "ob_low": 39000.0, "ob_high": 40000.0,
                "entry_dt": datetime(2024, 1, 2, 10, 0, tzinfo=timezone.utc),
                "exit_dt":  datetime(2024, 1, 2, 14, 0, tzinfo=timezone.utc),
                "entry_i":  10, "trigger": 40000.0, "partial_hit": False,
                "sl_current": 39000.0, "exit_reason": "TP", "exit_price": 42000.0,
                "pnl_pts": 200.0, "pnl_pct": 0.5, "candles": [],
            },
        ],
        "cancelled_list": [],
        "grid_rows": [],
    }

    html = bt.build_report([stats], "2024-01-02 00:00 UTC")
    assert "Strategy" in html or "strategy" in html.lower(), "Strategy column missing from HTML"


def test_report_includes_per_strategy_summary():
    """build_report HTML includes per-strategy summary when S5 trades exist."""
    import backtest_ig as bt

    stats = {
        "name":          "US30",
        "signals":       1, "filled": 1, "fill_rate": 100.0,
        "wins": 1, "losses": 0, "win_rate": 100.0, "partial_rate": 0.0,
        "avg_win_pts": 200.0, "avg_loss_pts": 0.0, "profit_factor": float("inf"),
        "total_pnl_pts": 200.0,
        "cancelled":     {"OB_INVALID": 0, "EXPIRED": 0, "SESSION_END": 0},
        "s1_stats":      {"signals": 0, "filled": 0, "fill_rate": 0,
                          "wins": 0, "losses": 0, "win_rate": 0,
                          "partial_rate": 0, "avg_win_pts": 0, "avg_loss_pts": 0,
                          "profit_factor": 0, "total_pnl_pts": 0,
                          "cancelled": {"OB_INVALID": 0, "EXPIRED": 0, "SESSION_END": 0}},
        "s5_stats":      {"signals": 1, "filled": 1, "fill_rate": 100,
                          "wins": 1, "losses": 0, "win_rate": 100,
                          "partial_rate": 0, "avg_win_pts": 200, "avg_loss_pts": 0,
                          "profit_factor": float("inf"), "total_pnl_pts": 200,
                          "cancelled": {"OB_INVALID": 0, "EXPIRED": 0, "SESSION_END": 0}},
        "trades": [
            {
                "strategy": "S5", "side": "LONG", "entry": 40000.0, "sl": 39000.0,
                "tp": 42000.0, "tp1": 41000.0, "ob_low": 39000.0, "ob_high": 40000.0,
                "entry_dt": datetime(2024, 1, 2, 10, 0, tzinfo=timezone.utc),
                "exit_dt":  datetime(2024, 1, 2, 14, 0, tzinfo=timezone.utc),
                "entry_i":  10, "trigger": 40000.0, "partial_hit": False,
                "sl_current": 39000.0, "exit_reason": "TP", "exit_price": 42000.0,
                "pnl_pts": 200.0, "pnl_pct": 0.5, "candles": [],
            },
        ],
        "cancelled_list": [],
        "grid_rows": [],
    }

    html = bt.build_report([stats], "2024-01-02 00:00 UTC")
    assert "Per-Strategy Summary" in html, "Per-Strategy Summary panel missing from HTML"


def test_report_includes_grid_table_when_grid_rows_present():
    """build_report HTML includes Grid Search Results section when grid_rows provided."""
    import backtest_ig as bt

    stats = {
        "name":          "US30",
        "signals":       0, "filled": 0, "fill_rate": 0,
        "wins": 0, "losses": 0, "win_rate": 0, "partial_rate": 0,
        "avg_win_pts": 0, "avg_loss_pts": 0, "profit_factor": 0,
        "total_pnl_pts": 0,
        "cancelled":     {"OB_INVALID": 0, "EXPIRED": 0, "SESSION_END": 0},
        "s1_stats":      {"signals": 0, "filled": 0, "fill_rate": 0,
                          "wins": 0, "losses": 0, "win_rate": 0,
                          "partial_rate": 0, "avg_win_pts": 0, "avg_loss_pts": 0,
                          "profit_factor": 0, "total_pnl_pts": 0,
                          "cancelled": {"OB_INVALID": 0, "EXPIRED": 0, "SESSION_END": 0}},
        "s5_stats":      {"signals": 0, "filled": 0, "fill_rate": 0,
                          "wins": 0, "losses": 0, "win_rate": 0,
                          "partial_rate": 0, "avg_win_pts": 0, "avg_loss_pts": 0,
                          "profit_factor": 0, "total_pnl_pts": 0,
                          "cancelled": {"OB_INVALID": 0, "EXPIRED": 0, "SESSION_END": 0}},
        "trades":        [],
        "cancelled_list": [],
        "grid_rows": [
            {
                "s1_sl_atr_mult": 1.5, "s1_tp_atr_mult": 3.0,
                "s1_consolidation_range_pct": 0.003, "s1_breakout_buffer_pct": 0.0005,
                "trade_count": 5, "win_rate": 60.0, "total_pnl": 500.0,
                "max_drawdown": 100.0,
            }
        ],
    }

    html = bt.build_report([stats], "2024-01-02 00:00 UTC")
    assert "Grid Search Results" in html, "Grid Search Results section missing from HTML"


# ── _compute_stats per-strategy breakdown ─────────────────────────── #

def test_compute_stats_includes_per_strategy_breakdown():
    """_compute_stats includes s1_stats and s5_stats sub-dicts."""
    import backtest_ig as bt

    trades = [
        {"strategy": "S5", "pnl_pts":  200.0, "partial_hit": False, "exit_reason": "TP",
         "entry_dt": datetime(2024, 1, 2, tzinfo=timezone.utc)},
        {"strategy": "S1", "pnl_pts": -100.0, "partial_hit": False, "exit_reason": "SL",
         "entry_dt": datetime(2024, 1, 3, tzinfo=timezone.utc)},
    ]
    cancelled = [
        {"strategy": "S5", "reason": "EXPIRED"},
    ]
    result = {"instrument": "US30", "trades": trades, "cancelled": cancelled}
    stats  = bt._compute_stats(result)

    assert "s1_stats" in stats
    assert "s5_stats" in stats

    # S5: 1 trade (win) + 1 cancelled
    s5 = stats["s5_stats"]
    assert s5["filled"] == 1
    assert s5["wins"]   == 1

    # S1: 1 trade (loss), no cancels
    s1 = stats["s1_stats"]
    assert s1["filled"] == 1
    assert s1["losses"] == 1
