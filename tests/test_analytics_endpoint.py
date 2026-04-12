"""Tests for the /api/analytics endpoint in dashboard.py."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import dashboard


_CSV_HEADER = (
    "timestamp,trade_id,action,symbol,side,qty,entry,sl,tp,"
    "box_low,box_high,leverage,margin,tpsl_set,strategy,"
    "snap_rsi,snap_adx,snap_htf,snap_coil,snap_box_range_pct,snap_sentiment,"
    "snap_daily_rsi,snap_entry_trigger,snap_sl,snap_rr,"
    "snap_rsi_peak,snap_spike_body_pct,snap_rsi_div,snap_rsi_div_str,"
    "snap_s5_ob_low,snap_s5_ob_high,snap_s5_tp,"
    "snap_s6_peak,snap_s6_drop_pct,snap_s6_rsi_at_peak,"
    "snap_sr_clearance_pct,result,pnl,pnl_pct,exit_reason,exit_price"
)


def _write_csv(path: Path, rows: list[dict]) -> None:
    cols = _CSV_HEADER.split(",")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, restval="")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


@pytest.fixture
def client_with_csv(tmp_path, monkeypatch):
    """Point dashboard at a temp trades.csv and return a TestClient."""
    csv_path = tmp_path / "trades.csv"
    _write_csv(csv_path, [
        {"timestamp": "2026-04-01T10:00:00+00:00", "trade_id": "t1",
         "action": "S3_LONG", "symbol": "BTCUSDT", "side": "LONG",
         "entry": "100", "snap_rr": "2.0"},
        {"timestamp": "2026-04-01T11:00:00+00:00", "trade_id": "t1",
         "action": "S3_CLOSE", "symbol": "BTCUSDT", "side": "LONG",
         "pnl": "5.0", "result": "WIN", "exit_price": "105"},
    ])
    state_path = tmp_path / "state.json"
    state_path.write_text("{}")
    monkeypatch.setattr(dashboard, "STATE_FILE", str(state_path))
    return TestClient(dashboard.app)


def test_analytics_endpoint_returns_shape(client_with_csv):
    r = client_with_csv.get("/api/analytics")
    assert r.status_code == 200
    data = r.json()
    assert "strategies" in data
    assert set(data["strategies"].keys()) == {
        "S1", "S2", "S3", "S4", "S5", "S6"
    }
    s3 = data["strategies"]["S3"]
    assert s3["summary"]["count"] == 1
    assert s3["summary"]["total_pnl"] == 5.0


def test_analytics_endpoint_accepts_range_30d(client_with_csv):
    r = client_with_csv.get("/api/analytics?range=30d&x=trade")
    assert r.status_code == 200


def test_analytics_endpoint_accepts_lastN(client_with_csv):
    r = client_with_csv.get("/api/analytics?range=lastN&n=10")
    assert r.status_code == 200


def test_analytics_endpoint_rejects_unknown_range(client_with_csv):
    r = client_with_csv.get("/api/analytics?range=bogus")
    assert r.status_code == 400


def test_analytics_endpoint_rejects_unknown_x_mode(client_with_csv):
    r = client_with_csv.get("/api/analytics?x=nope")
    assert r.status_code == 400


def test_analytics_endpoint_rejects_lastN_without_valid_n(client_with_csv):
    assert client_with_csv.get("/api/analytics?range=lastN").status_code == 400
    assert client_with_csv.get("/api/analytics?range=lastN&n=0").status_code == 400
    assert client_with_csv.get("/api/analytics?range=lastN&n=10001").status_code == 400


def test_analytics_endpoint_returns_empty_when_csv_missing(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    state_path.write_text("{}")
    monkeypatch.setattr(dashboard, "STATE_FILE", str(state_path))
    client = TestClient(dashboard.app)
    r = client.get("/api/analytics")
    assert r.status_code == 200
    data = r.json()
    assert all(data["strategies"][k]["summary"]["count"] == 0
               for k in ("S1", "S2", "S3", "S4", "S5", "S6"))
