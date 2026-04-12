"""Tests for analytics.py — trade history aggregation module."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import analytics


def test_module_imports_and_exposes_constants():
    assert analytics.STRATEGIES == ("S1", "S2", "S3", "S4", "S5", "S6")
    assert "snap_rsi" in analytics.STRATEGY_SNAP_FIELDS["S1"]
    assert analytics.SHARED_SNAP == ("snap_sr_clearance_pct",)
    assert "pnl" in analytics.COMMON_FIELDS


# ── helpers ────────────────────────────────────────────────────────
_CSV_HEADER = (
    "timestamp,trade_id,action,symbol,side,qty,entry,sl,tp,"
    "box_low,box_high,leverage,margin,tpsl_set,strategy,"
    "snap_rsi,snap_adx,snap_htf,snap_coil,snap_box_range_pct,snap_sentiment,"
    "snap_daily_rsi,"
    "snap_entry_trigger,snap_sl,snap_rr,"
    "snap_rsi_peak,snap_spike_body_pct,snap_rsi_div,snap_rsi_div_str,"
    "snap_s5_ob_low,snap_s5_ob_high,snap_s5_tp,"
    "snap_s6_peak,snap_s6_drop_pct,snap_s6_rsi_at_peak,"
    "snap_sr_clearance_pct,"
    "result,pnl,pnl_pct,exit_reason,exit_price"
)


def _write_csv(path: Path, rows: list[dict]) -> None:
    """Write a list of dicts as a trades.csv-format file."""
    cols = _CSV_HEADER.split(",")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, restval="")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def _row(**kwargs) -> dict:
    """Build one CSV row dict (missing fields default to '')."""
    return kwargs


def test_load_closed_trades_missing_file_returns_empty(tmp_path):
    result = analytics.load_closed_trades(str(tmp_path / "nope.csv"))
    assert result == []


def test_load_closed_trades_empty_csv_returns_empty(tmp_path):
    path = tmp_path / "empty.csv"
    _write_csv(path, [])
    assert analytics.load_closed_trades(str(path)) == []


def test_load_closed_trades_pairs_open_and_close(tmp_path):
    path = tmp_path / "trades.csv"
    _write_csv(path, [
        _row(timestamp="2026-04-01T10:00:00+00:00", trade_id="t1",
             action="S3_LONG", symbol="BTCUSDT", side="LONG",
             entry="100.0", sl="95.0", tp="110.0",
             snap_entry_trigger="100.5", snap_sl="95.0", snap_rr="2.0"),
        _row(timestamp="2026-04-01T11:00:00+00:00", trade_id="t1",
             action="S3_CLOSE", symbol="BTCUSDT", side="LONG",
             pnl="10.0", pnl_pct="10.0", result="WIN",
             exit_reason="TP", exit_price="110.0"),
    ])
    trades = analytics.load_closed_trades(str(path))
    assert len(trades) == 1
    t = trades[0]
    assert t["trade_id"] == "t1"
    assert t["strategy"] == "S3"
    assert t["symbol"] == "BTCUSDT"
    assert t["side"] == "LONG"
    assert t["entry"] == 100.0
    assert t["exit_price"] == 110.0
    assert t["pnl"] == 10.0
    assert t["pnl_pct"] == 10.0
    assert t["result"] == "WIN"
    assert t["exit_reason"] == "TP"
    # snap_* from open row is carried onto output
    assert t["snap_entry_trigger"] == "100.5"
    assert t["snap_rr"] == "2.0"
    # close timestamp is the primary timestamp
    assert t["timestamp"] == "2026-04-01T11:00:00+00:00"


def test_load_closed_trades_sums_partial_pnl(tmp_path):
    path = tmp_path / "trades.csv"
    _write_csv(path, [
        _row(timestamp="2026-04-01T10:00:00+00:00", trade_id="t1",
             action="S3_LONG", symbol="BTCUSDT", side="LONG", entry="100"),
        _row(timestamp="2026-04-01T10:30:00+00:00", trade_id="t1",
             action="S3_PARTIAL", symbol="BTCUSDT", side="LONG",
             pnl="4.0", exit_price="105.0"),
        _row(timestamp="2026-04-01T11:00:00+00:00", trade_id="t1",
             action="S3_CLOSE", symbol="BTCUSDT", side="LONG",
             pnl="6.0", result="WIN", exit_price="110.0"),
    ])
    trades = analytics.load_closed_trades(str(path))
    assert len(trades) == 1
    assert trades[0]["pnl"] == 10.0   # 4 + 6


def test_load_closed_trades_skips_orphan_close(tmp_path):
    path = tmp_path / "trades.csv"
    _write_csv(path, [
        _row(timestamp="2026-04-01T11:00:00+00:00", trade_id="ghost",
             action="S3_CLOSE", symbol="BTCUSDT", side="LONG",
             pnl="6.0", result="WIN", exit_price="110.0"),
    ])
    assert analytics.load_closed_trades(str(path)) == []


def test_load_closed_trades_ignores_open_without_close(tmp_path):
    path = tmp_path / "trades.csv"
    _write_csv(path, [
        _row(timestamp="2026-04-01T10:00:00+00:00", trade_id="live",
             action="S3_LONG", symbol="BTCUSDT", side="LONG", entry="100"),
    ])
    assert analytics.load_closed_trades(str(path)) == []


def test_load_closed_trades_skips_unknown_strategy(tmp_path):
    path = tmp_path / "trades.csv"
    _write_csv(path, [
        _row(timestamp="2026-04-01T10:00:00+00:00", trade_id="t1",
             action="S99_LONG", symbol="BTCUSDT", side="LONG", entry="100"),
        _row(timestamp="2026-04-01T11:00:00+00:00", trade_id="t1",
             action="S99_CLOSE", symbol="BTCUSDT", side="LONG",
             pnl="6.0", result="WIN"),
    ])
    assert analytics.load_closed_trades(str(path)) == []


def test_load_closed_trades_skips_malformed_pnl(tmp_path):
    path = tmp_path / "trades.csv"
    _write_csv(path, [
        _row(timestamp="2026-04-01T10:00:00+00:00", trade_id="t1",
             action="S3_LONG", symbol="BTCUSDT", side="LONG", entry="100"),
        _row(timestamp="2026-04-01T11:00:00+00:00", trade_id="t1",
             action="S3_CLOSE", symbol="BTCUSDT", side="LONG",
             pnl="not-a-number", result="WIN", exit_price="110"),
    ])
    # Malformed pnl coerces to 0.0 — row is kept but pnl is 0
    trades = analytics.load_closed_trades(str(path))
    assert len(trades) == 1
    assert trades[0]["pnl"] == 0.0


def test_group_by_strategy_all_six_keys_always_present():
    result = analytics.group_by_strategy([])
    assert set(result.keys()) == set(analytics.STRATEGIES)
    assert all(result[k] == [] for k in analytics.STRATEGIES)


def test_group_by_strategy_buckets_correctly():
    trades = [
        {"strategy": "S1", "trade_id": "a"},
        {"strategy": "S3", "trade_id": "b"},
        {"strategy": "S1", "trade_id": "c"},
    ]
    result = analytics.group_by_strategy(trades)
    assert [t["trade_id"] for t in result["S1"]] == ["a", "c"]
    assert [t["trade_id"] for t in result["S3"]] == ["b"]
    assert result["S2"] == []


def test_group_by_strategy_ignores_unknown_strategy():
    trades = [{"strategy": "S99", "trade_id": "x"}]
    result = analytics.group_by_strategy(trades)
    assert all(result[k] == [] for k in analytics.STRATEGIES)


def _mk_trade(ts: str, tid: str = "t", pnl: float = 1.0) -> dict:
    return {"timestamp": ts, "trade_id": tid, "pnl": pnl, "strategy": "S1"}


def test_filter_range_all_returns_everything():
    trades = [_mk_trade("2025-01-01T00:00:00+00:00"),
              _mk_trade("2026-04-01T00:00:00+00:00")]
    assert analytics.filter_range(trades, "all") == trades


def test_filter_range_30d_keeps_recent():
    now = datetime(2026, 4, 12, tzinfo=timezone.utc)
    old = _mk_trade("2026-03-01T00:00:00+00:00")
    mid = _mk_trade("2026-03-20T00:00:00+00:00")
    new = _mk_trade("2026-04-10T00:00:00+00:00")
    result = analytics.filter_range([old, mid, new], "30d", now=now)
    assert result == [mid, new]


def test_filter_range_90d_keeps_recent():
    now = datetime(2026, 4, 12, tzinfo=timezone.utc)
    very_old = _mk_trade("2025-11-01T00:00:00+00:00")
    old = _mk_trade("2026-02-15T00:00:00+00:00")
    result = analytics.filter_range([very_old, old], "90d", now=now)
    assert result == [old]


def test_filter_range_int_keeps_last_n():
    trades = [_mk_trade(f"2026-04-0{i}T00:00:00+00:00", tid=f"t{i}")
              for i in range(1, 6)]
    result = analytics.filter_range(trades, 3)
    assert [t["trade_id"] for t in result] == ["t3", "t4", "t5"]


def test_filter_range_int_larger_than_len_keeps_all():
    trades = [_mk_trade("2026-04-01T00:00:00+00:00", tid="a"),
              _mk_trade("2026-04-02T00:00:00+00:00", tid="b")]
    result = analytics.filter_range(trades, 100)
    assert result == trades


def test_filter_range_empty_input_returns_empty():
    assert analytics.filter_range([], "all") == []
    assert analytics.filter_range([], "30d") == []
    assert analytics.filter_range([], 10) == []


def test_filter_range_skips_unparseable_timestamps_for_time_ranges():
    now = datetime(2026, 4, 12, tzinfo=timezone.utc)
    good = _mk_trade("2026-04-10T00:00:00+00:00", tid="good")
    bad = _mk_trade("not-a-date", tid="bad")
    result = analytics.filter_range([good, bad], "30d", now=now)
    assert [t["trade_id"] for t in result] == ["good"]


def test_build_series_trade_mode_indices_start_at_one():
    trades = [{"pnl": 1.0, "timestamp": "2026-04-01T00:00:00+00:00"},
              {"pnl": 2.0, "timestamp": "2026-04-02T00:00:00+00:00"}]
    result = analytics.build_series(trades, "trade")
    assert [p["x"] for p in result["cum_pnl"]] == [1, 2]
    assert [p["x"] for p in result["bars"]] == [1, 2]


def test_build_series_cum_pnl_is_running_sum():
    trades = [{"pnl": 1.0, "timestamp": "2026-04-01T00:00:00+00:00"},
              {"pnl": 2.5, "timestamp": "2026-04-02T00:00:00+00:00"},
              {"pnl": -1.0, "timestamp": "2026-04-03T00:00:00+00:00"}]
    result = analytics.build_series(trades, "trade")
    assert [p["y"] for p in result["cum_pnl"]] == [1.0, 3.5, 2.5]
    assert [p["y"] for p in result["bars"]] == [1.0, 2.5, -1.0]


def test_build_series_bar_colors_match_sign():
    trades = [{"pnl": 1.0, "timestamp": "2026-04-01T00:00:00+00:00"},
              {"pnl": -0.5, "timestamp": "2026-04-02T00:00:00+00:00"},
              {"pnl": 0.0, "timestamp": "2026-04-03T00:00:00+00:00"}]
    result = analytics.build_series(trades, "trade")
    colors = [p["color"] for p in result["bars"]]
    assert colors == ["green", "red", "green"]


def test_build_series_time_mode_uses_iso_timestamp():
    trades = [{"pnl": 1.0, "timestamp": "2026-04-01T10:00:00+00:00"}]
    result = analytics.build_series(trades, "time")
    assert result["cum_pnl"][0]["x"] == "2026-04-01T10:00:00+00:00"
    assert result["bars"][0]["x"] == "2026-04-01T10:00:00+00:00"


def test_build_series_empty_input():
    result = analytics.build_series([], "trade")
    assert result == {"cum_pnl": [], "bars": []}
