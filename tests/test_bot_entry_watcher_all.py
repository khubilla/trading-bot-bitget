import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import threading
import pytest
import bot
import state as st


def _make_bot(monkeypatch) -> bot.MTFBot:
    b = object.__new__(bot.MTFBot)
    b.pending_signals = {}
    b.active_positions = {}
    b._trade_lock = threading.Lock()
    b.running = True
    b.sentiment = type("S", (), {"direction": "NEUTRAL"})()
    monkeypatch.setattr(bot.st, "add_scan_log", lambda *a, **kw: None)
    monkeypatch.setattr(bot.st, "save_pending_signals", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "PAPER_MODE", False)
    return b


def _make_s2_candidate():
    return {
        "symbol": "BTCUSDT", "strategy": "S2", "sig": "LONG",
        "s2_bh": 50000.0, "s2_bl": 47000.0,
        "s2_rsi": 62.5, "s2_reason": "breakout",
        "daily_df": None,
        "priority_rank": 1, "priority_score": 35.0,
        "rr": 2.5, "sr_pct": 10.0,
    }


def _make_s3_candidate():
    return {
        "symbol": "ETHUSDT", "strategy": "S3", "sig": "LONG",
        "s3_trigger": 2000.0, "s3_sl": 1900.0,
        "s3_adx": 28.5, "s3_reason": "pullback",
        "s3_sr_resistance_pct": 8.0, "m15_df": None,
        "priority_rank": 2, "priority_score": 28.0,
        "rr": 2.0, "sr_pct": 8.0,
    }


def _make_s4_candidate():
    return {
        "symbol": "SOLUSDT", "strategy": "S4", "sig": "SHORT",
        "s4_trigger": 95.0, "s4_sl": 105.0,
        "s4_rsi": 45.0, "s4_rsi_peak": 85.0,
        "s4_body_pct": 0.65, "s4_div": True, "s4_div_str": "RSI divergence",
        "daily_df": None,
        "priority_rank": 3, "priority_score": 22.0,
        "rr": 2.0, "sr_pct": 6.0,
    }


def _make_s6_candidate():
    return {
        "symbol": "BNBUSDT", "strategy": "S6", "sig": "PENDING_SHORT",
        "s6_peak_level": 400.0, "s6_sl": 420.0,
        "s6_drop_pct": 0.35, "s6_rsi_at_peak": 78.0,
        "s6_reason": "V-formation",
        "daily_df": None,
        "priority_rank": 4, "priority_score": 18.0,
        "rr": 2.0, "sr_pct": 5.0,
    }


def test_queue_s2_pending_stores_payload(monkeypatch):
    b = _make_bot(monkeypatch)
    c = _make_s2_candidate()
    b._queue_s2_pending(c)
    assert "BTCUSDT" in b.pending_signals
    sig = b.pending_signals["BTCUSDT"]
    assert sig["strategy"] == "S2"
    assert sig["side"] == "LONG"
    assert sig["trigger"] == 50000.0
    assert sig["s2_bh"] == 50000.0
    assert sig["s2_bl"] == 47000.0
    assert sig["priority_rank"] == 1
    assert "expires" in sig


def test_queue_s3_pending_stores_payload(monkeypatch):
    b = _make_bot(monkeypatch)
    c = _make_s3_candidate()
    b._queue_s3_pending(c)
    assert "ETHUSDT" in b.pending_signals
    sig = b.pending_signals["ETHUSDT"]
    assert sig["strategy"] == "S3"
    assert sig["trigger"] == 2000.0
    assert sig["s3_sl"] == 1900.0


def test_queue_s4_pending_stores_payload(monkeypatch):
    b = _make_bot(monkeypatch)
    c = _make_s4_candidate()
    b._queue_s4_pending(c)
    assert "SOLUSDT" in b.pending_signals
    sig = b.pending_signals["SOLUSDT"]
    assert sig["strategy"] == "S4"
    assert sig["side"] == "SHORT"
    assert sig["trigger"] == 95.0
    assert sig["s4_sl"] == 105.0


def test_queue_s6_pending_stores_payload(monkeypatch):
    b = _make_bot(monkeypatch)
    c = _make_s6_candidate()
    b._queue_s6_pending(c)
    assert "BNBUSDT" in b.pending_signals
    sig = b.pending_signals["BNBUSDT"]
    assert sig["strategy"] == "S6"
    assert sig["peak_level"] == 400.0
    assert sig["fakeout_seen"] == False


def test_queue_s6_pending_patches_pair_state(monkeypatch):
    b = _make_bot(monkeypatch)
    patched = {}
    monkeypatch.setattr(bot.st, "patch_pair_state",
                        lambda sym, d: patched.update({sym: d}))
    b._queue_s6_pending(_make_s6_candidate())
    assert patched.get("BNBUSDT", {}).get("s6_fakeout_seen") == False


def test_queue_saves_pending_signals(monkeypatch):
    saved = {}
    b = _make_bot(monkeypatch)
    # Override the no-op set by _make_bot so we can capture the call
    monkeypatch.setattr(bot.st, "save_pending_signals",
                        lambda signals: saved.update(signals))
    b._queue_s2_pending(_make_s2_candidate())
    assert "BTCUSDT" in saved
