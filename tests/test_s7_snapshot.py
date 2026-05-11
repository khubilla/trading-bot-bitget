import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def test_bot_saves_open_snapshot_s7_with_1h_candles(tmp_path, monkeypatch):
    """_fire_s7 must save an 'open' snapshot with 1H candles after opening a trade."""
    import snapshot
    import pandas as pd

    monkeypatch.setattr(snapshot, "_SNAP_DIR", tmp_path)

    import bot
    import config_s7

    # Mock 1H candles that would be fetched
    fake_h1_df = pd.DataFrame([
        {"ts": 1743296100000 + i * 3600_000,
         "open": 15.71, "high": 15.82, "low": 15.68, "close": 15.80, "vol": 100.0}
        for i in range(24)
    ])

    monkeypatch.setattr(bot.tr, "get_candles", lambda sym, interval, limit=100: fake_h1_df)

    open_short_calls = []
    def mock_open_short(*a, **kw):
        open_short_calls.append((a, kw))
        return {
            "symbol": "RIVERUSDT", "side": "SHORT", "qty": 4.0,
            "entry": 15.756, "sl": 16.50, "tp": 14.50,
            "margin": 6.3385, "leverage": 10,
        }
    monkeypatch.setattr(bot.tr, "open_short", mock_open_short)
    # Mock trader functions
    if hasattr(bot.tr, 'tag_strategy'):
        monkeypatch.setattr(bot.tr, "tag_strategy", lambda *a: None)
    monkeypatch.setattr(bot.st, "add_open_trade", lambda t: None)
    monkeypatch.setattr(bot.st, "add_scan_log", lambda *a, **kw: None)
    monkeypatch.setattr(bot.st, "get_pair_state", lambda sym: {
        "s7_sr_support_pct": 20.0,  # Must be > S7_MIN_SR_CLEARANCE (15%)
    })
    monkeypatch.setattr(bot.st, "save_pending_signals", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "_log_trade", lambda action, details: None)
    monkeypatch.setattr(bot, "claude_approve", lambda *a, **kw: {"approved": True})
    monkeypatch.setattr(bot, "dna_snapshot", lambda *a, **kw: {})
    monkeypatch.setattr(bot, "PAPER_MODE", False)  # Avoid tr.tag_strategy call
    monkeypatch.setattr(bot, "get_position_size_multiplier", lambda: 1.0)

    # Mock logger to capture warnings
    import logging
    logger_warnings = []
    def mock_warning(msg, *args):
        logger_warnings.append(msg % args if args else msg)
    monkeypatch.setattr(logging.getLogger("bot"), "warning", mock_warning)

    # No more debug wrapping - snapshot module is already patched with tmp_path

    b = object.__new__(bot.MTFBot)
    b.active_positions = {}
    b.pending_signals = {}
    b.sentiment = type("S", (), {"direction": "BEARISH"})()

    sig = {
        "side": "SHORT",
        "trigger": 15.756,
        "s7_sl": 16.50,
        "box_low": 15.70,
        "box_top": 16.00,
        "snap_rsi": 72.5,
        "snap_rsi_peak": 78.0,
        "snap_spike_body_pct": 25.0,
        "snap_rsi_div": True,
        "snap_rsi_div_str": "78.0→72.0",
        "snap_box_top": 16.00,
        "snap_box_low_initial": 15.70,
        "snap_sentiment": "BEARISH",
    }

    b._fire_s7("RIVERUSDT", sig, 15.756, 1000.0)

    # Snapshot must be saved with 1H interval and actual candles
    import json
    files = list(tmp_path.glob("*_open.json"))
    assert len(files) == 1, f"Expected 1 open snapshot, found: {files}"

    snap = json.loads(files[0].read_text(encoding="utf-8"))
    assert snap["event"] == "open"
    assert snap["interval"] == "1H", f"Expected interval='1H', got '{snap['interval']}'"
    assert snap["symbol"] == "RIVERUSDT"
    assert snap["event_price"] == 15.756
    assert len(snap["candles"]) > 0, "Expected 1H candles, got empty list"
    # Verify candle structure
    assert "t" in snap["candles"][0]
    assert "o" in snap["candles"][0]
    assert "h" in snap["candles"][0]
    assert "l" in snap["candles"][0]
    assert "c" in snap["candles"][0]
    assert "v" in snap["candles"][0]
