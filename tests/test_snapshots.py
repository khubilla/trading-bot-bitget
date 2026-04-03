import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

FAKE_CANDLES = [
    {"t": 1743296100000, "o": 15.71, "h": 15.82, "l": 15.68, "c": 15.80, "v": 12340.5},
    {"t": 1743297000000, "o": 15.80, "h": 15.95, "l": 15.75, "c": 15.90, "v": 9800.0},
]


def test_save_and_load_snapshot(tmp_path, monkeypatch):
    import snapshot
    monkeypatch.setattr(snapshot, "_SNAP_DIR", tmp_path)

    snapshot.save_snapshot(
        trade_id="abc123",
        event="open",
        symbol="RIVERUSDT",
        interval="15m",
        candles=FAKE_CANDLES,
        event_price=15.756,
        captured_at="2026-03-30T18:21:18+00:00",
    )

    result = snapshot.load_snapshot("abc123", "open")
    assert result is not None
    assert result["trade_id"] == "abc123"
    assert result["event"] == "open"
    assert result["symbol"] == "RIVERUSDT"
    assert result["interval"] == "15m"
    assert result["event_price"] == 15.756
    assert len(result["candles"]) == 2
    assert result["candles"][0]["t"] == 1743296100000


def test_load_missing_snapshot_returns_none(tmp_path, monkeypatch):
    import snapshot
    monkeypatch.setattr(snapshot, "_SNAP_DIR", tmp_path)
    assert snapshot.load_snapshot("nonexistent", "open") is None


def test_list_snapshots(tmp_path, monkeypatch):
    import snapshot
    monkeypatch.setattr(snapshot, "_SNAP_DIR", tmp_path)

    for event in ("open", "scale_in", "partial", "close"):
        snapshot.save_snapshot(
            trade_id="tid1", event=event, symbol="BTCUSDT",
            interval="15m", candles=FAKE_CANDLES, event_price=42000.0,
        )

    snaps = snapshot.list_snapshots("tid1")
    assert set(snaps) == {"open", "scale_in", "partial", "close"}


def test_save_overwrites_existing(tmp_path, monkeypatch):
    import snapshot
    monkeypatch.setattr(snapshot, "_SNAP_DIR", tmp_path)

    snapshot.save_snapshot("tid2", "open", "BTCUSDT", "15m", FAKE_CANDLES, 42000.0)
    snapshot.save_snapshot("tid2", "open", "BTCUSDT", "15m", FAKE_CANDLES[:1], 42100.0)

    result = snapshot.load_snapshot("tid2", "open")
    assert len(result["candles"]) == 1
    assert result["event_price"] == 42100.0


def test_bot_saves_open_snapshot_s3(tmp_path, monkeypatch):
    """_execute_s3 must save an 'open' snapshot after opening a trade."""
    import snapshot
    import pandas as pd

    monkeypatch.setattr(snapshot, "_SNAP_DIR", tmp_path)

    import bot
    import config_s3

    # Build minimal m15_df with "ts" column (25 rows, same values for simplicity)
    df = pd.DataFrame([
        {"ts": 1743296100000 + i * 900_000,
         "open": 15.71, "high": 15.82, "low": 15.68,
         "close": 15.80, "vol": 12340.5}
        for i in range(25)
    ])

    monkeypatch.setattr(bot.tr, "open_long", lambda *a, **kw: {
        "symbol": "RIVERUSDT", "side": "LONG", "qty": 4.0,
        "entry": 15.756, "sl": 14.99, "tp": 17.332,
        "margin": 6.3385, "leverage": 10,
    })
    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: 15.756)
    monkeypatch.setattr(bot.st, "add_open_trade", lambda t: None)
    monkeypatch.setattr(bot.st, "add_scan_log", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "_log_trade", lambda action, details: None)
    monkeypatch.setattr(bot, "find_nearest_resistance", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "claude_approve", lambda *a, **kw: {"approved": True})

    b = object.__new__(bot.MTFBot)
    b.active_positions = {}
    b.sentiment = type("S", (), {"direction": "BULLISH"})()

    c = {
        "symbol": "RIVERUSDT",
        "s3_trigger": 15.756,
        "m15_df": df,
        "s3_adx": 46.6,
        "s3_sl": 14.99,
        "s3_reason": "test",
        "s3_sr_resistance_pct": 11.6,
        "priority_rank": 1,
    }
    b._execute_s3(c, 1000.0)

    # Snapshot must be saved — find any file in tmp_path
    files = list(tmp_path.glob("*_open.json"))
    assert len(files) == 1, f"Expected 1 open snapshot, found: {files}"
    import json
    snap = json.loads(files[0].read_text(encoding="utf-8"))
    assert snap["event"] == "open"
    assert snap["interval"] == config_s3.S3_LTF_INTERVAL
    assert snap["symbol"] == "RIVERUSDT"
    assert len(snap["candles"]) == 25
    assert snap["event_price"] == 15.756


def test_bot_saves_scale_in_snapshot(tmp_path, monkeypatch):
    """_do_scale_in must save a 'scale_in' snapshot after executing scale-in."""
    import snapshot, pandas as pd, bot

    monkeypatch.setattr(snapshot, "_SNAP_DIR", tmp_path)

    fake_df = pd.DataFrame([
        {"ts": 1743296100000 + i * 900_000,
         "open": 15.71, "high": 15.82, "low": 15.68, "close": 15.80, "vol": 100.0}
        for i in range(25)
    ])
    monkeypatch.setattr(bot.tr, "get_candles", lambda sym, interval, limit=100: fake_df)
    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: 15.80)
    monkeypatch.setattr(bot.tr, "scale_in_long", lambda *a, **kw: None)
    monkeypatch.setattr(bot.tr, "get_all_open_positions", lambda: {"RIVERUSDT": {"margin": 6.0}})
    monkeypatch.setattr(bot.st, "add_scan_log", lambda *a, **kw: None)
    monkeypatch.setattr(bot.st, "update_open_trade_margin", lambda *a: None)
    monkeypatch.setattr(bot, "_log_trade", lambda action, details: None)
    monkeypatch.setattr(bot, "PAPER_MODE", False)

    b = object.__new__(bot.MTFBot)
    ap = {
        "side": "LONG", "strategy": "S2", "trade_id": "tid99",
        "box_high": 15.80, "box_low": 14.99,
        "scale_in_pending": True, "scale_in_after": 0,
        "scale_in_trade_size_pct": 0.02,
    }
    b._do_scale_in("RIVERUSDT", ap)

    import json
    files = list(tmp_path.glob("*_scale_in.json"))
    assert len(files) == 1, f"Expected 1 scale_in snapshot, found: {files}"
    snap = json.loads(files[0].read_text(encoding="utf-8"))
    assert snap["event"] == "scale_in"
    assert snap["interval"] == "1D"   # S2 uses daily
    assert snap["event_price"] == 15.80
    assert len(snap["candles"]) == 25


def test_do_scale_in_passes_new_trail_trigger_to_refresh(monkeypatch):
    """After S2 scale-in, refresh_plan_exits must receive a trail trigger derived
    from the new average entry price, not the old profit_plan trigger price."""
    import bot, config_s2

    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: 15.80)
    monkeypatch.setattr(bot.tr, "scale_in_long", lambda *a, **kw: None)
    monkeypatch.setattr(bot.tr, "get_candles",
        lambda sym, interval, limit=100: __import__("pandas").DataFrame())
    monkeypatch.setattr(bot.st, "add_scan_log", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "_log_trade", lambda action, details: None)
    monkeypatch.setattr(bot, "PAPER_MODE", False)

    # avg entry after scale-in is 15.50; S2_TRAILING_TRIGGER_PCT=0.10
    # expected new_trail_trigger = 15.50 * 1.10 = 17.05
    monkeypatch.setattr(config_s2, "S2_TRAILING_TRIGGER_PCT", 0.10)
    monkeypatch.setattr(bot.tr, "get_all_open_positions",
        lambda: {"RIVERUSDT": {"entry_price": 15.50, "qty": 200.0, "margin": 6.0}})

    captured = {}

    def fake_refresh(symbol, hold_side, new_trail_trigger=0):
        captured["new_trail_trigger"] = new_trail_trigger
        return True

    monkeypatch.setattr(bot.tr, "refresh_plan_exits", fake_refresh)
    monkeypatch.setattr(bot.tr, "update_position_sl", lambda *a, **kw: True)
    monkeypatch.setattr(bot.st, "update_open_trade_sl", lambda *a: None)

    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)

    b = object.__new__(bot.MTFBot)
    ap = {
        "side": "LONG", "strategy": "S2", "trade_id": "tid_s2",
        "box_high": 15.80, "box_low": 14.99,
        "sl": 13.50,
        "scale_in_pending": True, "scale_in_after": 0,
        "scale_in_trade_size_pct": 0.02,
    }
    b._do_scale_in("RIVERUSDT", ap)

    assert "new_trail_trigger" in captured, "refresh_plan_exits was not called"
    expected = round(15.50 * 1.10, 8)
    assert abs(captured["new_trail_trigger"] - expected) < 1e-6, (
        f"Expected {expected}, got {captured['new_trail_trigger']}"
    )


def test_do_scale_in_s4_short_passes_new_trail_trigger(monkeypatch):
    """After S4 SHORT scale-in, trail trigger is new_avg * (1 - S4_TRAILING_TRIGGER_PCT)."""
    import bot, config_s4

    # S4 window: prev_low*(1-MAX_ENTRY_BUFFER) <= mark <= prev_low*(1-ENTRY_BUFFER)
    # = 0.340*0.99=0.3366 to 0.340*0.998=0.33932 — mark=0.338 is in window
    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: 0.338)
    monkeypatch.setattr(bot.tr, "scale_in_short", lambda *a, **kw: None)
    monkeypatch.setattr(bot.tr, "get_candles",
        lambda sym, interval, limit=100: __import__("pandas").DataFrame())
    monkeypatch.setattr(bot.st, "add_scan_log", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "_log_trade", lambda action, details: None)
    monkeypatch.setattr(bot, "PAPER_MODE", False)

    monkeypatch.setattr(config_s4, "S4_TRAILING_TRIGGER_PCT", 0.10)
    monkeypatch.setattr(config_s4, "S4_MAX_ENTRY_BUFFER", 0.01)
    monkeypatch.setattr(config_s4, "S4_ENTRY_BUFFER", 0.002)
    monkeypatch.setattr(bot.tr, "get_all_open_positions",
        lambda: {"STOUSDT": {"entry_price": 0.345, "qty": 200.0, "margin": 3.0}})

    captured = {}

    def fake_refresh(symbol, hold_side, new_trail_trigger=0):
        captured["new_trail_trigger"] = new_trail_trigger
        return True

    monkeypatch.setattr(bot.tr, "refresh_plan_exits", fake_refresh)

    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)

    b = object.__new__(bot.MTFBot)
    # S4 scale-in window: prev_low * (1 - MAX_ENTRY_BUFFER=0.01) <= mark <= prev_low * (1 - ENTRY_BUFFER=0.002)
    # = 0.340 * 0.99 = 0.3366 to 0.340 * 0.998 = 0.33932 — use 0.338
    prev_low = 0.340
    ap = {
        "side": "SHORT", "strategy": "S4", "trade_id": "tid_s4",
        "s4_prev_low": prev_low,
        "scale_in_pending": True, "scale_in_after": 0,
        "scale_in_trade_size_pct": 0.02,
    }
    b._do_scale_in("STOUSDT", ap)

    assert "new_trail_trigger" in captured, "refresh_plan_exits was not called"
    expected = round(0.345 * (1 - 0.10), 8)
    assert abs(captured["new_trail_trigger"] - expected) < 1e-6, (
        f"Expected {expected}, got {captured['new_trail_trigger']}"
    )


def test_do_scale_in_s2_updates_sl_from_new_avg(monkeypatch):
    """After S2 LONG scale-in, SL must be recomputed from new avg entry and pushed to exchange.
    Expected new SL = max(box_low * 0.999, new_avg * (1 - S2_STOP_LOSS_PCT))
    """
    import bot, config_s2

    # S2 window: box_high=0.160 <= mark <= 0.160*1.01=0.1616 — use 0.161
    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: 0.161)
    monkeypatch.setattr(bot.tr, "scale_in_long", lambda *a, **kw: None)
    monkeypatch.setattr(bot.tr, "get_candles",
        lambda sym, interval, limit=100: __import__("pandas").DataFrame())
    monkeypatch.setattr(bot.st, "add_scan_log", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "_log_trade", lambda action, details: None)
    monkeypatch.setattr(bot, "PAPER_MODE", False)

    monkeypatch.setattr(config_s2, "S2_STOP_LOSS_PCT", 0.05)
    monkeypatch.setattr(config_s2, "S2_TRAILING_TRIGGER_PCT", 0.10)
    monkeypatch.setattr(config_s2, "S2_MAX_ENTRY_BUFFER", 0.01)

    # S2 window: box_high <= mark <= box_high*(1+MAX_ENTRY_BUFFER)
    # = 0.160 to 0.160*1.01=0.1616 — mark=0.161 is in window
    # new_avg=0.158, box_low=0.140
    # new sl_cap = 0.158 * 0.95 = 0.1501
    # box_low * 0.999 = 0.13986
    # expected new SL = max(0.13986, 0.1501) = 0.1501
    new_avg = 0.158
    monkeypatch.setattr(bot.tr, "get_all_open_positions",
        lambda: {"STOUSDT": {"entry_price": new_avg, "qty": 200.0, "margin": 6.0}})

    monkeypatch.setattr(bot.tr, "refresh_plan_exits", lambda *a, **kw: True)

    sl_update_calls = []
    monkeypatch.setattr(bot.tr, "update_position_sl",
        lambda sym, new_sl, hold_side="long": sl_update_calls.append(new_sl) or True)

    state_sl_updates = []
    monkeypatch.setattr(bot.st, "update_open_trade_sl",
        lambda sym, new_sl: state_sl_updates.append(new_sl))

    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)

    b = object.__new__(bot.MTFBot)
    ap = {
        "side": "LONG", "strategy": "S2", "trade_id": "tid_s2_sl",
        "box_high": 0.160, "box_low": 0.140,
        "sl": 0.132,
        "scale_in_pending": True, "scale_in_after": 0,
        "scale_in_trade_size_pct": 0.02,
    }
    b._do_scale_in("STOUSDT", ap)

    expected_new_sl = max(0.140 * 0.999, new_avg * (1 - 0.05))
    assert len(sl_update_calls) == 1, "update_position_sl should be called once"
    assert abs(sl_update_calls[0] - expected_new_sl) < 1e-6, (
        f"SL sent to exchange {sl_update_calls[0]} != expected {expected_new_sl}"
    )
    assert len(state_sl_updates) == 1, "update_open_trade_sl (state) should be called once"
    assert abs(ap["sl"] - expected_new_sl) < 1e-6, "ap['sl'] should be updated in-memory"


def test_startup_reconcile_partial_saves_snapshot(tmp_path, monkeypatch):
    """
    Validates the snapshot module contract used by Pass A startup reconciliation:
    get_candles + save_snapshot with partial event produces a valid snapshot file.
    Note: does not exercise the bot.__init__ code path directly.
    """
    import snapshot, pandas as pd

    monkeypatch.setattr(snapshot, "_SNAP_DIR", tmp_path)

    fake_df = pd.DataFrame([
        {"ts": 1743296100000 + i * 900_000,
         "open": 15.71, "high": 15.82, "low": 15.68, "close": 15.80, "vol": 100.0}
        for i in range(50)
    ])

    import bot
    monkeypatch.setattr(bot.tr, "get_candles", lambda sym, interval, limit=100: fake_df)

    # Simulate the Pass A snapshot call
    trade_id = "reco01"
    sym = "RIVERUSDT"
    strategy = "S3"
    exit_p = 17.332

    _si = bot._STRATEGY_CANDLE_INTERVAL.get(strategy, "15m")
    _sdf = bot.tr.get_candles(sym, _si, limit=100)
    snapshot.save_snapshot(
        trade_id=trade_id, event="partial",
        symbol=sym, interval=_si,
        candles=bot._df_to_candles(_sdf),
        event_price=round(exit_p, 8),
    )
    result = snapshot.load_snapshot(trade_id, "partial")
    assert result is not None
    assert result["event"] == "partial"
    assert result["interval"] == "15m"
    assert result["event_price"] == 17.332
    assert len(result["candles"]) == 50


def test_entry_chart_uses_snapshot_when_available(tmp_path, monkeypatch):
    """
    /api/entry-chart must return snapshot candles without hitting the exchange
    when a snapshot file exists for the given trade_id.
    """
    import snapshot
    from fastapi.testclient import TestClient
    import dashboard

    monkeypatch.setattr(snapshot, "_SNAP_DIR", tmp_path)

    # Save a snapshot with known candles
    candles = [
        {"t": 1743296100000 + i * 900_000,
         "o": 15.71, "h": 15.82, "l": 15.68, "c": 15.80, "v": 100.0}
        for i in range(25)
    ]
    snapshot.save_snapshot(
        trade_id="snap01", event="open",
        symbol="RIVERUSDT", interval="15m",
        candles=candles, event_price=15.756,
        captured_at="2025-03-30T05:55:00+00:00",
    )

    # Ensure bc.get_public is NOT called
    exchange_called = []
    import bitget_client as bc
    monkeypatch.setattr(bc, "get_public", lambda *a, **kw: exchange_called.append(1) or {"data": []})

    # Clear API key so auth middleware doesn't block the TestClient request
    monkeypatch.delenv("DASHBOARD_API_KEY", raising=False)

    client = TestClient(dashboard.app)
    resp = client.get("/api/entry-chart", params={
        "symbol": "RIVERUSDT",
        "open_at": "2025-03-30T05:55:00+00:00",
        "strategy": "S3",
        "entry": 15.756,
        "trade_id": "snap01",
    })

    assert resp.status_code == 200, f"status: {resp.status_code}, body: {resp.text}"
    data = resp.json()
    assert "candles" in data, f"missing candles key: {data}"
    assert len(data["candles"]) == 25
    assert data.get("from_snapshot") is True, "should be from snapshot"
    assert exchange_called == [], f"exchange must NOT be called when snapshot exists, was called {len(exchange_called)} times"
