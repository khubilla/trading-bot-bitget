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
