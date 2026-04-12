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
