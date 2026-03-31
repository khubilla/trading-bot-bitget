import sys, os, csv, io
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

def test_get_last_close_returns_exit_price(tmp_path, monkeypatch):
    """get_last_close must return exit_price from paper_trader history."""
    import paper_trader
    fake_state = {
        "balance": 1000.0,
        "positions": {},
        "history": [
            {
                "symbol": "BTCUSDT",
                "pnl": 4.2,
                "pnl_pct": 2.1,
                "reason": "TP",
                "exit": 42680.0,   # already stored, not yet surfaced
            }
        ],
        "total_pnl": 4.2,
        "partial_closes": [],
    }
    monkeypatch.setattr(paper_trader, "_load", lambda: dict(fake_state))
    result = paper_trader.get_last_close("BTCUSDT")
    assert result is not None
    assert result["exit_price"] == 42680.0, f"expected 42680.0, got {result.get('exit_price')}"


def _make_csv(rows: list) -> str:
    """Helper: render a list of dicts to CSV string using bot's _TRADE_FIELDS order."""
    fields = [
        "timestamp", "trade_id", "action", "symbol", "side", "qty", "entry", "sl", "tp",
        "box_low", "box_high", "leverage", "margin", "tpsl_set", "strategy",
        "snap_rsi", "snap_adx", "snap_htf", "snap_coil", "snap_box_range_pct", "snap_sentiment",
        "snap_daily_rsi", "snap_entry_trigger", "snap_sl", "snap_rr",
        "snap_rsi_peak", "snap_spike_body_pct", "snap_rsi_div", "snap_rsi_div_str",
        "snap_s5_ob_low", "snap_s5_ob_high", "snap_s5_tp", "snap_sr_clearance_pct",
        "result", "pnl", "pnl_pct", "exit_reason", "exit_price",
    ]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, restval="", extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def test_load_csv_history_enriches_with_open_row(tmp_path):
    """_load_csv_history must match CLOSE rows to their OPEN row via trade_id."""
    import dashboard
    csv_rows = [
        {
            "timestamp": "2026-03-29T14:22:00+00:00",
            "trade_id": "abc123", "action": "S5_LONG",
            "symbol": "BTCUSDT", "side": "LONG",
            "entry": "42100.0", "sl": "41800.0", "tp": "43200.0",
        },
        {
            "timestamp": "2026-03-29T16:05:00+00:00",
            "trade_id": "abc123", "action": "S5_CLOSE",
            "symbol": "BTCUSDT", "side": "LONG",
            "pnl": "4.2", "result": "WIN", "pnl_pct": "2.1",
            "exit_reason": "TP", "exit_price": "42680.0",
        },
    ]
    csv_file = tmp_path / "trades.csv"
    csv_file.write_text(_make_csv(csv_rows))

    hist = dashboard._load_csv_history(str(csv_file), limit=10)
    assert len(hist) == 1
    t = hist[0]
    assert t["entry"] == 42100.0,      f"entry: {t['entry']}"
    assert t["sl"] == 41800.0,         f"sl: {t['sl']}"
    assert t["tp"] == 43200.0,         f"tp: {t['tp']}"
    assert t["exit_price"] == 42680.0, f"exit_price: {t['exit_price']}"
    assert t["open_at"] == "2026-03-29T14:22:00+00:00"
    assert t["interval"] == "15m",     f"interval: {t['interval']}"
    assert t["events"] == []


def test_load_csv_history_includes_scale_in_and_partial_events(tmp_path):
    """events list must include scale_in and partial rows in order."""
    import dashboard
    csv_rows = [
        {
            "timestamp": "2026-03-29T10:00:00+00:00",
            "trade_id": "xyz", "action": "S2_LONG",
            "symbol": "ETHUSDT", "side": "LONG",
            "entry": "3000.0", "sl": "2900.0", "tp": "3300.0",
        },
        {
            "timestamp": "2026-03-29T11:00:00+00:00",
            "trade_id": "xyz", "action": "S2_SCALE_IN",
            "symbol": "ETHUSDT", "side": "LONG", "entry": "3050.0",
        },
        {
            "timestamp": "2026-03-29T12:00:00+00:00",
            "trade_id": "xyz", "action": "S2_PARTIAL",
            "symbol": "ETHUSDT", "side": "LONG",
            "exit_price": "3200.0", "pnl": "1.5",
        },
        {
            "timestamp": "2026-03-29T13:00:00+00:00",
            "trade_id": "xyz", "action": "S2_CLOSE",
            "symbol": "ETHUSDT", "side": "LONG",
            "pnl": "3.0", "result": "WIN", "exit_price": "3250.0",
        },
    ]
    csv_file = tmp_path / "trades.csv"
    csv_file.write_text(_make_csv(csv_rows))

    hist = dashboard._load_csv_history(str(csv_file), limit=10)
    assert len(hist) == 1
    evts = hist[0]["events"]
    assert len(evts) == 2
    assert evts[0]["type"] == "scale_in"
    assert evts[0]["price"] == 3050.0
    assert evts[1]["type"] == "partial"
    assert evts[1]["price"] == 3200.0


def test_load_csv_history_graceful_no_open_row(tmp_path):
    """CLOSE row with no matching OPEN row must still emit with None chart fields."""
    import dashboard
    csv_rows = [
        {
            "timestamp": "2026-03-29T16:05:00+00:00",
            "trade_id": "orphan", "action": "S5_CLOSE",
            "symbol": "BTCUSDT", "side": "LONG",
            "pnl": "2.0", "result": "WIN", "pnl_pct": "1.0",
            "exit_reason": "TP", "exit_price": "43000.0",
        },
    ]
    csv_file = tmp_path / "trades.csv"
    csv_file.write_text(_make_csv(csv_rows))

    hist = dashboard._load_csv_history(str(csv_file), limit=10)
    assert len(hist) == 1
    t = hist[0]
    assert t["entry"] is None,      f"entry should be None, got {t['entry']}"
    assert t["sl"] is None,         f"sl should be None, got {t['sl']}"
    assert t["tp"] is None,         f"tp should be None, got {t['tp']}"
    assert t["open_at"] is None,    f"open_at should be None, got {t['open_at']}"
    assert t["interval"] is None,   f"interval should be None, got {t['interval']}"
    assert t["events"] == [],       f"events should be [], got {t['events']}"
    # existing fields must still be present
    assert t["symbol"] == "BTCUSDT"
    assert t["result"] == "WIN"
    assert t["exit_price"] == 43000.0


def test_load_csv_history_includes_trade_id(tmp_path):
    """trade_id must be present in history entries so the dashboard can send it to /api/entry-chart."""
    import dashboard
    csv_rows = [
        {
            "timestamp": "2026-03-29T14:22:00+00:00",
            "trade_id": "abc123", "action": "S5_LONG",
            "symbol": "BTCUSDT", "side": "LONG",
            "entry": "42100.0", "sl": "41800.0", "tp": "43200.0",
        },
        {
            "timestamp": "2026-03-29T16:05:00+00:00",
            "trade_id": "abc123", "action": "S5_CLOSE",
            "symbol": "BTCUSDT", "side": "LONG",
            "pnl": "4.2", "result": "WIN", "pnl_pct": "2.1",
            "exit_reason": "TP", "exit_price": "42680.0",
        },
    ]
    # Reuse the _make_csv helper from this file
    csv_file = tmp_path / "trades.csv"
    csv_file.write_text(_make_csv(csv_rows))

    hist = dashboard._load_csv_history(str(csv_file), limit=10)
    assert len(hist) == 1
    assert hist[0].get("trade_id") == "abc123", f"trade_id missing or wrong: {hist[0].get('trade_id')}"


def test_trade_chart_missing_trade_id(monkeypatch):
    """Missing trade_id must return 400."""
    from fastapi.testclient import TestClient
    import dashboard
    client = TestClient(dashboard.app)
    resp = client.get("/api/trade-chart")
    assert resp.status_code == 400
    assert resp.json()["error"] == "trade_id required"


def test_trade_chart_no_snapshots(tmp_path, monkeypatch):
    """No snapshots for trade_id must return 404."""
    import snapshot
    from fastapi.testclient import TestClient
    import dashboard
    monkeypatch.setattr(snapshot, "_SNAP_DIR", tmp_path)
    client = TestClient(dashboard.app)
    resp = client.get("/api/trade-chart", params={"trade_id": "nosuchid"})
    assert resp.status_code == 404
    assert resp.json()["error"] == "no snapshots found"


def test_trade_chart_single_snapshot_active_trade(tmp_path, monkeypatch):
    """Active trade with only open snapshot → 200 with 1 event, candles present."""
    import snapshot
    from fastapi.testclient import TestClient
    import dashboard

    monkeypatch.setattr(snapshot, "_SNAP_DIR", tmp_path)

    candles = [
        {"t": i * 900_000, "o": 1.0, "h": 1.1, "l": 0.9, "c": 1.05, "v": 100.0}
        for i in range(25)
    ]
    snapshot.save_snapshot(
        "active01", "open", "ARCUSDT", "15m", candles, 1.0,
        captured_at="1970-01-01T02:00:00+00:00",  # t=7200000ms = index 8
    )

    client = TestClient(dashboard.app)
    resp = client.get("/api/trade-chart", params={
        "trade_id": "active01", "side": "LONG",
        "sl": 0.9, "tp": 1.15, "strategy": "S3",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["symbol"] == "ARCUSDT"
    assert data["interval"] == "15m"
    assert data["side"] == "LONG"
    assert data["strategy"] == "S3"
    assert len(data["candles"]) == 25
    assert len(data["events"]) == 1
    ev = data["events"][0]
    assert ev["type"] == "open"
    assert ev["candle_idx"] == 8
    assert ev["price"] == 1.0
    assert ev["sl"] == 0.9
    assert ev["tp"] == 1.15


def test_trade_chart_merges_multiple_snapshots(tmp_path, monkeypatch):
    """Open + close snapshots → merged candle array with correct candle_idx values."""
    import snapshot
    from fastapi.testclient import TestClient
    import dashboard

    monkeypatch.setattr(snapshot, "_SNAP_DIR", tmp_path)

    # open: candles t=0..18*900000 (19 candles), captured at t=8*900000=7200000ms
    candles_open = [
        {"t": i * 900_000, "o": 1.0, "h": 1.1, "l": 0.9, "c": 1.05, "v": 100.0}
        for i in range(19)
    ]
    # close: candles t=8..26*900000 (19 candles, overlap with open), captured at t=26*900000=23400000ms
    candles_close = [
        {"t": (8 + i) * 900_000, "o": 1.2, "h": 1.3, "l": 1.1, "c": 1.25, "v": 200.0}
        for i in range(19)
    ]
    snapshot.save_snapshot(
        "t1", "open", "BTCUSDT", "15m", candles_open, 1.0,
        captured_at="1970-01-01T02:00:00+00:00",  # 7200s = 7200000ms = t=8*900000
    )
    snapshot.save_snapshot(
        "t1", "close", "BTCUSDT", "15m", candles_close, 1.25,
        captured_at="1970-01-01T06:30:00+00:00",  # 23400s = 23400000ms = t=26*900000
    )

    client = TestClient(dashboard.app)
    resp = client.get("/api/trade-chart", params={
        "trade_id": "t1", "side": "LONG", "sl": 0.85, "tp": 1.4, "strategy": "S3",
    })
    assert resp.status_code == 200
    data = resp.json()
    # union: 0..18 from open + 8..26 from close → 0..26 = 27 unique timestamps
    assert len(data["candles"]) == 27
    assert data["events"][0]["type"] == "open"
    assert data["events"][0]["candle_idx"] == 8
    assert data["events"][1]["type"] == "close"
    assert data["events"][1]["candle_idx"] == 26


def test_trade_chart_later_snapshot_wins_on_overlap(tmp_path, monkeypatch):
    """When two snapshots share a timestamp, the later snapshot's candle is used."""
    import snapshot
    from fastapi.testclient import TestClient
    import dashboard

    monkeypatch.setattr(snapshot, "_SNAP_DIR", tmp_path)

    # Both have t=0 candle; close snapshot should overwrite open snapshot's version
    candles_open  = [{"t": 0, "o": 1.0, "h": 1.1, "l": 0.9, "c": 1.05, "v": 100.0}]
    candles_close = [{"t": 0, "o": 2.0, "h": 2.1, "l": 1.9, "c": 2.05, "v": 999.0}]

    snapshot.save_snapshot("dup", "open",  "BTCUSDT", "15m", candles_open,  1.0,
                           captured_at="1970-01-01T00:00:00+00:00")
    snapshot.save_snapshot("dup", "close", "BTCUSDT", "15m", candles_close, 2.0,
                           captured_at="1970-01-01T00:00:00+00:00")

    client = TestClient(dashboard.app)
    resp = client.get("/api/trade-chart", params={"trade_id": "dup", "side": "LONG"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["candles"]) == 1
    assert data["candles"][0]["v"] == 999.0  # close snapshot overwrote open
