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
