"""
Regression: snap_trend_h1_* DNA fields were always blank because the entry
sites passed a candles dict to dna_snapshot() WITHOUT an "h1" key, even though
dna_fields() for S1/S4/S5/S7 computes H1 trend when candles["h1"] is present.

These tests pin the contract: the fire path must hand dna_snapshot() a non-empty
"h1" DataFrame so the H1 trend fingerprint actually records.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pandas as pd
import pytest


def _fake_h1_df(n=48):
    return pd.DataFrame([
        {"ts": 1743296100000 + i * 3600_000,
         "open": 15.71, "high": 15.82, "low": 15.68,
         "close": 15.50 + i * 0.01, "vol": 100.0}
        for i in range(n)
    ])


@pytest.mark.parametrize("strategy,expected", [
    ("S1", {"daily", "h1", "m3"}),
    ("S2", {"daily"}),
    ("S3", {"m15"}),
    ("S4", {"daily", "h1"}),
    ("S5", {"daily", "h1", "m15"}),
    ("S6", {"daily"}),
    ("S7", {"daily", "h1"}),
])
def test_dna_candles_fetches_all_needed_timeframes(monkeypatch, strategy, expected):
    """With nothing in scope (watcher-fired entry), _dna_candles fetches every
    timeframe the strategy's dna_fields needs — except m3, which is never fetched."""
    import bot
    fetched = []

    def fake_get_candles(sym, interval, limit=100):
        fetched.append(interval)
        return _fake_h1_df()
    monkeypatch.setattr(bot.tr, "get_candles", fake_get_candles)

    out = bot._dna_candles("FOOUSDT", strategy)
    # m3 is never fetched (S1 only, always in scope); everything else must be present.
    want_fetched = expected - {"m3"}
    assert set(out) >= want_fetched, f"{strategy}: missing {want_fetched - set(out)}"
    for tf in want_fetched:
        assert out[tf] is not None and not out[tf].empty, f"{strategy}: {tf} empty"
    assert "m3" not in fetched  # never network-fetch m3


def test_dna_candles_reuses_in_scope_dataframe(monkeypatch):
    """An in-scope DataFrame hint is reused, not refetched."""
    import bot
    calls = []
    monkeypatch.setattr(bot.tr, "get_candles",
                        lambda s, i, limit=100: calls.append(i) or _fake_h1_df())

    daily_hint = _fake_h1_df()
    out = bot._dna_candles("FOOUSDT", "S2", daily=daily_hint)
    assert out["daily"] is daily_hint            # reused exactly
    assert "1D" not in calls                      # no daily fetch happened


def test_fire_s7_passes_h1_to_dna_snapshot(monkeypatch):
    """_fire_s7 must include a non-empty 'h1' DataFrame in the dna_snapshot candles dict."""
    import snapshot
    import bot

    monkeypatch.setattr(bot.tr, "get_candles",
                        lambda sym, interval, limit=100: _fake_h1_df())

    def mock_open_short(*a, **kw):
        return {"symbol": "RIVERUSDT", "side": "SHORT", "qty": 4.0,
                "entry": 15.756, "sl": 16.50, "tp": 14.50,
                "margin": 6.3385, "leverage": 10}
    monkeypatch.setattr(bot.tr, "open_short", mock_open_short)
    if hasattr(bot.tr, "tag_strategy"):
        monkeypatch.setattr(bot.tr, "tag_strategy", lambda *a: None)
    monkeypatch.setattr(bot.st, "add_open_trade", lambda t: None)
    monkeypatch.setattr(bot.st, "add_scan_log", lambda *a, **kw: None)
    monkeypatch.setattr(bot.st, "get_pair_state",
                        lambda sym: {"s7_sr_support_pct": 20.0})
    monkeypatch.setattr(bot.st, "save_pending_signals", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "_log_trade", lambda action, details: None)
    monkeypatch.setattr(bot, "claude_approve", lambda *a, **kw: {"approved": True})
    monkeypatch.setattr(bot, "PAPER_MODE", False)
    monkeypatch.setattr(bot, "get_position_size_multiplier", lambda: 1.0)
    monkeypatch.setattr(snapshot, "save_snapshot", lambda *a, **kw: None)

    # Spy on dna_snapshot to capture the candles dict it receives.
    captured = {}
    real_dna = bot.dna_snapshot

    def spy(strategy, symbol, candles):
        captured["candles"] = candles
        return real_dna(strategy, symbol, candles)
    monkeypatch.setattr(bot, "dna_snapshot", spy)

    b = object.__new__(bot.MTFBot)
    b.active_positions = {}
    b.pending_signals = {}
    b.sentiment = type("S", (), {"direction": "BEARISH"})()

    sig = {
        "side": "SHORT", "trigger": 15.756, "s7_sl": 16.50,
        "box_low": 15.70, "box_top": 16.00,
        "snap_rsi": 72.5, "snap_rsi_peak": 78.0, "snap_spike_body_pct": 25.0,
        "snap_rsi_div": True, "snap_rsi_div_str": "78.0->72.0",
        "snap_box_top": 16.00, "snap_box_low_initial": 15.70,
        "snap_sentiment": "BEARISH",
    }

    b._fire_s7("RIVERUSDT", sig, 15.756, 1000.0)

    assert "candles" in captured, "dna_snapshot was never called"
    candles = captured["candles"]
    assert "h1" in candles, "fire path did not pass an 'h1' key to dna_snapshot"
    h1 = candles["h1"]
    assert h1 is not None and not h1.empty, "'h1' DataFrame is empty/None"

    # And the resulting fingerprint must actually carry an H1 field.
    out = real_dna("S7", "RIVERUSDT", candles)
    assert out.get("snap_trend_h1_ema_slope") in ("rising", "falling", "flat"), \
        f"snap_trend_h1_ema_slope not populated: {out.get('snap_trend_h1_ema_slope')!r}"
