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


def _make_pair_state(s2_sr=None, s3_sr=None, s4_sr_pct=None,
                     s2_signal="LONG", s3_signal="LONG", s4_signal="SHORT"):
    return {
        "s2_sr_resistance_price": s2_sr,
        "s2_signal": s2_signal,
        "s3_sr_resistance_price": s3_sr,
        "s3_signal": s3_signal,
        "s4_sr_support_pct": s4_sr_pct,
        "s4_signal": s4_signal,
    }


def test_fire_s2_opens_long_when_sr_clear(monkeypatch):
    b = _make_bot(monkeypatch)
    monkeypatch.setattr(bot.config_s2, "S2_MIN_SR_CLEARANCE", 0.05)
    monkeypatch.setattr(bot.st, "get_pair_state",
        lambda sym: _make_pair_state(s2_sr=55000.0))  # 10% above mark=50000
    opened = {}
    monkeypatch.setattr(bot.tr, "open_long",
        lambda sym, **kw: opened.update({"sym": sym}) or
        {"symbol": sym, "side": "LONG", "entry": 50000.0, "sl": 47000.0,
         "tp": None, "qty": "0.001", "leverage": 10, "margin": 0.5})
    monkeypatch.setattr(bot.st, "add_open_trade", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "_log_trade", lambda *a, **kw: None)
    monkeypatch.setattr(bot.snapshot, "save_snapshot", lambda **kw: None)

    sig = {
        "strategy": "S2", "side": "LONG", "trigger": 50000.0,
        "s2_bh": 50000.0, "s2_bl": 47000.0,
        "snap_daily_rsi": 62.5, "snap_box_range_pct": 6.3,
        "snap_sentiment": "NEUTRAL",
        "expires": 9999999999,
    }
    b._fire_s2("BTCUSDT", sig, mark=50000.0, balance=1000.0)
    assert opened.get("sym") == "BTCUSDT"


def test_fire_s2_skips_when_sr_too_close(monkeypatch):
    b = _make_bot(monkeypatch)
    monkeypatch.setattr(bot.st, "get_pair_state",
        lambda sym: _make_pair_state(s2_sr=50200.0))  # only 0.4% clearance
    opened = {}
    monkeypatch.setattr(bot.tr, "open_long",
        lambda sym, **kw: opened.update({"sym": sym}) or {})

    sig = {"strategy": "S2", "side": "LONG", "trigger": 50000.0,
           "s2_bh": 50000.0, "s2_bl": 47000.0,
           "snap_daily_rsi": 62.5, "snap_box_range_pct": 6.3,
           "snap_sentiment": "NEUTRAL", "expires": 9999999999}
    b._fire_s2("BTCUSDT", sig, mark=50000.0, balance=1000.0)
    assert "sym" not in opened, "Must not open trade when S/R too close"


def test_fire_s3_opens_long_when_sr_clear(monkeypatch):
    b = _make_bot(monkeypatch)
    monkeypatch.setattr(bot.config_s3, "S3_MIN_SR_CLEARANCE", 0.05)
    monkeypatch.setattr(bot.st, "get_pair_state",
        lambda sym: _make_pair_state(s3_sr=2200.0))  # 10% above mark=2000
    opened = {}
    monkeypatch.setattr(bot.tr, "open_long",
        lambda sym, **kw: opened.update({"sym": sym}) or
        {"symbol": sym, "side": "LONG", "entry": 2000.0, "sl": 1900.0,
         "tp": None, "qty": "0.1", "leverage": 10, "margin": 20.0})
    monkeypatch.setattr(bot.st, "add_open_trade", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "_log_trade", lambda *a, **kw: None)
    monkeypatch.setattr(bot.snapshot, "save_snapshot", lambda **kw: None)

    sig = {"strategy": "S3", "side": "LONG", "trigger": 2000.0,
           "s3_sl": 1900.0, "snap_adx": 28.5, "snap_entry_trigger": 2000.0,
           "snap_sl": 1900.0, "snap_rr": 2.0, "snap_sentiment": "NEUTRAL",
           "snap_sr_clearance_pct": 10.0, "expires": 9999999999}
    b._fire_s3("ETHUSDT", sig, mark=2000.0, balance=1000.0)
    assert opened.get("sym") == "ETHUSDT"


def test_fire_s4_opens_short_when_sr_clear(monkeypatch):
    b = _make_bot(monkeypatch)
    monkeypatch.setattr(bot.config_s4, "S4_MIN_SR_CLEARANCE", 0.05)
    monkeypatch.setattr(bot.st, "get_pair_state",
        lambda sym: _make_pair_state(s4_sr_pct=8.0))  # 8% > min clearance
    opened = {}
    monkeypatch.setattr(bot.tr, "open_short",
        lambda sym, **kw: opened.update({"sym": sym}) or
        {"symbol": sym, "side": "SHORT", "entry": 95.0, "sl": 105.0,
         "tp": None, "qty": "10", "leverage": 10, "margin": 5.0})
    monkeypatch.setattr(bot.st, "add_open_trade", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "_log_trade", lambda *a, **kw: None)
    monkeypatch.setattr(bot.snapshot, "save_snapshot", lambda **kw: None)

    sig = {"strategy": "S4", "side": "SHORT", "trigger": 95.0,
           "s4_sl": 105.0, "prev_low": 100.0,
           "snap_rsi": 45.0, "snap_rsi_peak": 85.0,
           "snap_spike_body_pct": 65.0, "snap_rsi_div": True,
           "snap_rsi_div_str": "RSI divergence", "snap_sentiment": "NEUTRAL",
           "expires": 9999999999}
    b._fire_s4("SOLUSDT", sig, mark=95.0, balance=1000.0)
    assert opened.get("sym") == "SOLUSDT"


def test_fire_s6_opens_short(monkeypatch):
    b = _make_bot(monkeypatch)
    opened = {}
    monkeypatch.setattr(bot.tr, "open_short",
        lambda sym, **kw: opened.update({"sym": sym}) or
        {"symbol": sym, "side": "SHORT", "entry": 390.0, "sl": 410.0,
         "tp": None, "qty": "5", "leverage": 10, "margin": 20.0})
    monkeypatch.setattr(bot.st, "add_open_trade", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "_log_trade", lambda *a, **kw: None)
    monkeypatch.setattr(bot.snapshot, "save_snapshot", lambda **kw: None)

    sig = {"strategy": "S6", "side": "SHORT", "peak_level": 400.0,
           "sl": 420.0, "drop_pct": 0.35, "rsi_at_peak": 78.0,
           "snap_s6_peak": 400.0, "snap_s6_drop_pct": 35.0,
           "snap_s6_rsi_at_peak": 78.0, "snap_sentiment": "BEARISH",
           "expires": 9999999999}
    b._fire_s6("BNBUSDT", sig, mark=390.0, balance=1000.0)
    assert opened.get("sym") == "BNBUSDT"


# ── Entry Watcher Loop Tests (S2 / S3 / S4 / S6 dispatch) ──────────────── #

import time as _time


def _make_s2_sig(mark_at_trigger=50000.0):
    return {
        "strategy": "S2", "side": "LONG",
        "trigger": 50000.0, "s2_bh": 50000.0, "s2_bl": 47000.0,
        "priority_rank": 1, "priority_score": 35.0,
        "snap_daily_rsi": 62.5, "snap_box_range_pct": 6.3, "snap_sentiment": "NEUTRAL",
        "expires": _time.time() + 86400,
    }


def _make_s3_sig():
    return {
        "strategy": "S3", "side": "LONG",
        "trigger": 2000.0, "s3_sl": 1900.0,
        "priority_rank": 2, "priority_score": 25.0,
        "snap_adx": 28.5, "snap_entry_trigger": 2000.0, "snap_sl": 1900.0,
        "snap_rr": 2.0, "snap_sentiment": "NEUTRAL", "snap_sr_clearance_pct": 10.0,
        "expires": _time.time() + 86400,
    }


def _make_s4_sig():
    return {
        "strategy": "S4", "side": "SHORT",
        "trigger": 95.0, "s4_sl": 105.0, "prev_low": 100.0,
        "priority_rank": 3, "priority_score": 20.0,
        "snap_rsi": 45.0, "snap_rsi_peak": 85.0, "snap_spike_body_pct": 65.0,
        "snap_rsi_div": True, "snap_rsi_div_str": "RSI div", "snap_sentiment": "NEUTRAL",
        "expires": _time.time() + 86400,
    }


def _make_s6_sig(fakeout_seen=False):
    return {
        "strategy": "S6", "side": "SHORT",
        "peak_level": 400.0, "sl": 420.0,
        "drop_pct": 0.35, "rsi_at_peak": 78.0,
        "fakeout_seen": fakeout_seen,
        "detected_at": _time.time(),
        "snap_s6_peak": 400.0, "snap_s6_drop_pct": 35.0,
        "snap_s6_rsi_at_peak": 78.0, "snap_sentiment": "BEARISH",
        "expires": _time.time() + 86400,
    }


def _run_one_iteration(b, monkeypatch):
    """Run _entry_watcher_loop for exactly one iteration by stopping it after time.sleep."""
    def _stop_after_sleep(_duration):
        b.running = False
    monkeypatch.setattr(bot.time, "sleep", _stop_after_sleep)
    b.running = True
    b._entry_watcher_loop()


def test_watcher_fires_s2_when_price_at_trigger(monkeypatch):
    """When mark is in S2 trigger window, _fire_s2 is called and signal removed."""
    b = _make_bot(monkeypatch)
    b.pending_signals = {"BTCUSDT": _make_s2_sig()}
    monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)
    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: 50000.0)
    monkeypatch.setattr(bot.st, "get_pair_state",
        lambda sym: {"s2_signal": "LONG"})
    fired = []
    monkeypatch.setattr(b, "_fire_s2",
        lambda sym, sig, mark, bal: fired.append(sym) or
        b.active_positions.update({sym: {"strategy": "S2"}}))
    monkeypatch.setattr(bot.st, "save_pending_signals", lambda *a: None)
    monkeypatch.setattr(bot.st, "is_pair_paused", lambda sym: False)
    monkeypatch.setattr(bot.config, "MAX_CONCURRENT_TRADES", 5)

    _run_one_iteration(b, monkeypatch)

    assert "BTCUSDT" in fired
    assert "BTCUSDT" not in b.pending_signals


def test_watcher_cancels_s2_when_signal_gone(monkeypatch):
    """When pair_states shows s2_signal='HOLD', pending is cancelled."""
    b = _make_bot(monkeypatch)
    b.pending_signals = {"BTCUSDT": _make_s2_sig()}
    monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)
    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: 50000.0)
    monkeypatch.setattr(bot.st, "get_pair_state",
        lambda sym: {"s2_signal": "HOLD"})
    monkeypatch.setattr(bot.st, "save_pending_signals", lambda *a: None)
    _run_one_iteration(b, monkeypatch)
    assert "BTCUSDT" not in b.pending_signals


def test_watcher_cancels_s2_when_price_below_invalidation(monkeypatch):
    """When mark < s2_bl, pending S2 is cancelled regardless of signal."""
    b = _make_bot(monkeypatch)
    b.pending_signals = {"BTCUSDT": _make_s2_sig()}
    monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)
    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: 46000.0)  # below s2_bl=47000
    monkeypatch.setattr(bot.st, "get_pair_state",
        lambda sym: {"s2_signal": "LONG"})
    monkeypatch.setattr(bot.st, "save_pending_signals", lambda *a: None)
    _run_one_iteration(b, monkeypatch)
    assert "BTCUSDT" not in b.pending_signals


def test_watcher_s6_phase1_sets_fakeout_seen(monkeypatch):
    """When mark > peak_level and fakeout_seen=False, fakeout_seen becomes True."""
    b = _make_bot(monkeypatch)
    b.pending_signals = {"BNBUSDT": _make_s6_sig(fakeout_seen=False)}
    monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)
    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: 410.0)  # above peak 400
    monkeypatch.setattr(bot.st, "get_pair_state",
        lambda sym: {"s6_signal": "PENDING_SHORT"})
    patched = {}
    monkeypatch.setattr(bot.st, "patch_pair_state",
        lambda sym, d: patched.update({sym: d}))
    monkeypatch.setattr(bot.st, "save_pending_signals", lambda *a: None)
    _run_one_iteration(b, monkeypatch)
    assert b.pending_signals["BNBUSDT"]["fakeout_seen"] == True
    assert patched.get("BNBUSDT", {}).get("s6_fakeout_seen") == True


def test_watcher_s6_phase2_fires_when_below_peak(monkeypatch):
    """When fakeout_seen=True and mark < peak_level, _fire_s6 is called."""
    b = _make_bot(monkeypatch)
    b.pending_signals = {"BNBUSDT": _make_s6_sig(fakeout_seen=True)}
    monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)
    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: 390.0)  # below peak 400
    monkeypatch.setattr(bot.st, "get_pair_state",
        lambda sym: {"s6_signal": "PENDING_SHORT"})
    fired = []
    monkeypatch.setattr(b, "_fire_s6",
        lambda sym, sig, mark, bal: fired.append(sym) or
        b.active_positions.update({sym: {"strategy": "S6"}}))
    monkeypatch.setattr(bot.st, "save_pending_signals", lambda *a: None)
    monkeypatch.setattr(bot.st, "is_pair_paused", lambda sym: False)
    monkeypatch.setattr(bot.config, "MAX_CONCURRENT_TRADES", 5)
    _run_one_iteration(b, monkeypatch)
    assert "BNBUSDT" in fired
    assert "BNBUSDT" not in b.pending_signals


def test_watcher_s6_cancels_on_bullish_sentiment(monkeypatch):
    """S6 watcher cancels when sentiment is BULLISH."""
    b = _make_bot(monkeypatch)
    b.sentiment = type("S", (), {"direction": "BULLISH"})()
    b.pending_signals = {"BNBUSDT": _make_s6_sig(fakeout_seen=True)}
    monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)
    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: 390.0)
    monkeypatch.setattr(bot.st, "get_pair_state",
        lambda sym: {"s6_signal": "PENDING_SHORT"})
    monkeypatch.setattr(bot.st, "save_pending_signals", lambda *a: None)
    _run_one_iteration(b, monkeypatch)
    assert "BNBUSDT" not in b.pending_signals


# ── _execute_best_candidate queuing tests (S2 / S3 / S4 / S6) ──────────── #

def _make_full_bot(monkeypatch) -> bot.MTFBot:
    b = _make_bot(monkeypatch)
    b.candidates = []
    b.sentiment  = type("S", (), {"direction": "NEUTRAL"})()
    monkeypatch.setattr(bot.st, "is_pair_paused", lambda sym: False)
    monkeypatch.setattr(bot.st, "patch_pair_state", lambda *a, **kw: None)
    return b


def test_execute_best_candidate_queues_s2(monkeypatch):
    """S2 LONG candidate is queued (not executed immediately) via _execute_best_candidate."""
    b = _make_full_bot(monkeypatch)
    queued = []
    monkeypatch.setattr(b, "_queue_s2_pending", lambda c: queued.append(c["symbol"]))
    b.candidates = [dict(_make_s2_candidate(), sig="LONG")]
    b._execute_best_candidate("BULLISH", 1000.0)
    assert "BTCUSDT" in queued


def test_execute_best_candidate_does_not_requeue_existing_s2(monkeypatch):
    """If S2 already in pending_signals, _execute_best_candidate skips re-queuing."""
    b = _make_full_bot(monkeypatch)
    b.pending_signals["BTCUSDT"] = _make_s2_sig()
    queued = []
    monkeypatch.setattr(b, "_queue_s2_pending", lambda c: queued.append(c["symbol"]))
    b.candidates = [dict(_make_s2_candidate(), sig="LONG")]
    b._execute_best_candidate("BULLISH", 1000.0)
    assert queued == [], "Should not re-queue if already pending"


def test_execute_best_candidate_queues_s6(monkeypatch):
    """S6 PENDING_SHORT uses _queue_s6_pending (not _queue_s6_watcher)."""
    b = _make_full_bot(monkeypatch)
    queued = []
    monkeypatch.setattr(b, "_queue_s6_pending", lambda c: queued.append(c["symbol"]))
    b.candidates = [dict(_make_s6_candidate(), sig="PENDING_SHORT")]
    b._execute_best_candidate("BEARISH", 1000.0)
    assert "BNBUSDT" in queued
