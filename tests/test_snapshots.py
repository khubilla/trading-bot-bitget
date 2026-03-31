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

    for event in ("open", "partial", "close"):
        snapshot.save_snapshot(
            trade_id="tid1", event=event, symbol="BTCUSDT",
            interval="15m", candles=FAKE_CANDLES, event_price=42000.0,
        )

    snaps = snapshot.list_snapshots("tid1")
    assert set(snaps) == {"open", "partial", "close"}


def test_save_overwrites_existing(tmp_path, monkeypatch):
    import snapshot
    monkeypatch.setattr(snapshot, "_SNAP_DIR", tmp_path)

    snapshot.save_snapshot("tid2", "open", "BTCUSDT", "15m", FAKE_CANDLES, 42000.0)
    snapshot.save_snapshot("tid2", "open", "BTCUSDT", "15m", FAKE_CANDLES[:1], 42100.0)

    result = snapshot.load_snapshot("tid2", "open")
    assert len(result["candles"]) == 1
    assert result["event_price"] == 42100.0
