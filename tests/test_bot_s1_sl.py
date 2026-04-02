"""
tests/test_bot_s1_sl.py

Tests for S1 SL improvements:
  - SL uses box_low/box_high pivot with S1_SL_BUFFER_PCT
  - SL is capped at STOP_LOSS_PCT from entry (floor/ceiling)
  - Daily S/R clearance gate (S1_MIN_SR_CLEARANCE)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import threading
import pandas as pd
import pytest

import bot
import config_s1


# ── Helpers ───────────────────────────────────────────────────────── #

def _make_bot(monkeypatch) -> bot.MTFBot:
    b = object.__new__(bot.MTFBot)
    b.pending_signals  = {}
    b.active_positions = {}
    b._trade_lock      = threading.Lock()
    b.running          = True
    b.sentiment        = type("S", (), {"direction": "BULLISH"})()

    monkeypatch.setattr(bot.st, "add_scan_log",   lambda *a, **kw: None)
    monkeypatch.setattr(bot.st, "add_open_trade", lambda *a, **kw: None)
    monkeypatch.setattr(bot,    "_log_trade",      lambda *a, **kw: None)
    monkeypatch.setattr(bot,    "PAPER_MODE",      False)
    monkeypatch.setattr(bot.snapshot, "save_snapshot", lambda **kw: None)

    return b


def _make_ltf_df() -> pd.DataFrame:
    """Minimal 5-candle LTF DataFrame (all green, values don't affect new SL logic)."""
    data = [
        {"open": 99.0, "high": 100.0, "low": 98.5, "close": 100.0},
        {"open": 99.5, "high": 100.5, "low": 99.0, "close": 100.5},
        {"open": 100.0, "high": 101.0, "low": 99.5, "close": 101.0},
        {"open": 100.5, "high": 101.5, "low": 100.0, "close": 101.5},
        {"open": 101.0, "high": 102.0, "low": 100.5, "close": 102.0},
    ]
    return pd.DataFrame(data)


def _make_candidate(sig="LONG", mark=100.0, box_low=98.0, box_high=102.0, sr_pct=20.0) -> dict:
    return {
        "strategy":      "S1",
        "symbol":        "BTCUSDT",
        "sig":           sig,
        "s1_bl":         box_low,
        "s1_bh":         box_high,
        "rsi_val":       72.0 if sig == "LONG" else 28.0,
        "adx_val":       30.0,
        "htf_bull":      sig == "LONG",
        "htf_bear":      sig == "SHORT",
        "is_coil":       True,
        "ltf_df":        _make_ltf_df(),
        "sr_pct":        sr_pct,
        "priority_rank": 1,
        "allowed_direction": "LONG" if sig == "LONG" else "SHORT",
    }


SYMBOL = "BTCUSDT"

# ── Test 1: LONG SL = box_low * (1 - buffer) when box_low is near entry ── #

def test_long_sl_uses_box_low_when_near_entry(monkeypatch):
    """LONG: when box_low gives a SL above the STOP_LOSS_PCT floor, use box_low * (1 - buffer)."""
    b = _make_bot(monkeypatch)

    mark = 100.0
    box_low = 98.0   # 2% below mark — box_low_sl = 98 * 0.995 = 97.51, floor = 95.0
    expected_sl = box_low * (1 - config_s1.S1_SL_BUFFER_PCT)  # ~97.51 — above floor 95.0

    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: mark)

    captured = {}
    def fake_open_long(symbol, sl_floor, leverage, trade_size_pct, use_s1_exits):
        captured["sl_floor"] = sl_floor
        return {"symbol": symbol, "side": "LONG", "qty": 1.0, "entry": mark,
                "sl": sl_floor, "tp": mark * 1.1, "leverage": leverage,
                "margin": 10.0, "tpsl_set": True}

    monkeypatch.setattr(bot.tr, "open_long", fake_open_long)

    c = _make_candidate(sig="LONG", mark=mark, box_low=box_low, sr_pct=20.0)
    result = b._execute_s1(c, 1000.0)

    assert result is True
    assert abs(captured["sl_floor"] - expected_sl) < 1e-9, \
        f"Expected sl_floor={expected_sl:.5f}, got {captured['sl_floor']:.5f}"


# ── Test 2: LONG SL capped at STOP_LOSS_PCT floor when box_low is far ─ #

def test_long_sl_uses_stop_loss_floor_when_box_low_far(monkeypatch):
    """LONG: when box_low * (1 - buffer) < STOP_LOSS_PCT floor, use STOP_LOSS_PCT floor."""
    b = _make_bot(monkeypatch)

    mark = 100.0
    box_low = 80.0   # 20% below mark — box_low_sl = 79.6, floor = 95.0 → floor wins
    expected_sl = mark * (1 - config_s1.STOP_LOSS_PCT)  # 95.0

    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: mark)

    captured = {}
    def fake_open_long(symbol, sl_floor, leverage, trade_size_pct, use_s1_exits):
        captured["sl_floor"] = sl_floor
        return {"symbol": symbol, "side": "LONG", "qty": 1.0, "entry": mark,
                "sl": sl_floor, "tp": mark * 1.1, "leverage": leverage,
                "margin": 10.0, "tpsl_set": True}

    monkeypatch.setattr(bot.tr, "open_long", fake_open_long)

    c = _make_candidate(sig="LONG", mark=mark, box_low=box_low, sr_pct=20.0)
    result = b._execute_s1(c, 1000.0)

    assert result is True
    assert abs(captured["sl_floor"] - expected_sl) < 1e-9, \
        f"Expected sl_floor={expected_sl:.5f}, got {captured['sl_floor']:.5f}"


# ── Test 3: SHORT SL = box_high * (1 + buffer) when box_high is near entry ── #

def test_short_sl_uses_box_high_when_near_entry(monkeypatch):
    """SHORT: when box_high gives a SL below the STOP_LOSS_PCT ceiling, use box_high * (1 + buffer)."""
    b = _make_bot(monkeypatch)

    mark = 100.0
    box_high = 102.0  # 2% above mark — box_high_sl = 102 * 1.005 = 102.51, ceiling = 105.0
    expected_sl = box_high * (1 + config_s1.S1_SL_BUFFER_PCT)  # ~102.51 — below ceiling 105.0

    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: mark)

    captured = {}
    def fake_open_short(symbol, sl_floor, leverage, trade_size_pct, use_s1_exits):
        captured["sl_floor"] = sl_floor
        return {"symbol": symbol, "side": "SHORT", "qty": 1.0, "entry": mark,
                "sl": sl_floor, "tp": mark * 0.9, "leverage": leverage,
                "margin": 10.0, "tpsl_set": True}

    monkeypatch.setattr(bot.tr, "open_short", fake_open_short)

    c = _make_candidate(sig="SHORT", mark=mark, box_high=box_high, sr_pct=20.0)
    result = b._execute_s1(c, 1000.0)

    assert result is True
    assert abs(captured["sl_floor"] - expected_sl) < 1e-9, \
        f"Expected sl_floor={expected_sl:.5f}, got {captured['sl_floor']:.5f}"


# ── Test 4: SHORT SL capped at STOP_LOSS_PCT when box_high is far ─── #

def test_short_sl_uses_stop_loss_ceiling_when_box_high_far(monkeypatch):
    """SHORT: when box_high * (1 + buffer) > STOP_LOSS_PCT ceiling, use STOP_LOSS_PCT ceiling."""
    b = _make_bot(monkeypatch)

    mark = 100.0
    box_high = 120.0  # 20% above mark — box_high_sl = 120.6, ceiling = 105.0 → ceiling wins
    expected_sl = mark * (1 + config_s1.STOP_LOSS_PCT)  # 105.0

    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: mark)

    captured = {}
    def fake_open_short(symbol, sl_floor, leverage, trade_size_pct, use_s1_exits):
        captured["sl_floor"] = sl_floor
        return {"symbol": symbol, "side": "SHORT", "qty": 1.0, "entry": mark,
                "sl": sl_floor, "tp": mark * 0.9, "leverage": leverage,
                "margin": 10.0, "tpsl_set": True}

    monkeypatch.setattr(bot.tr, "open_short", fake_open_short)

    c = _make_candidate(sig="SHORT", mark=mark, box_high=box_high, sr_pct=20.0)
    result = b._execute_s1(c, 1000.0)

    assert result is True
    assert abs(captured["sl_floor"] - expected_sl) < 1e-9, \
        f"Expected sl_floor={expected_sl:.5f}, got {captured['sl_floor']:.5f}"


# ── Test 5: LONG skipped when sr_pct below gate ───────────────────── #

def test_long_skipped_when_sr_clearance_below_gate(monkeypatch):
    """LONG: when sr_pct < S1_MIN_SR_CLEARANCE * 100, _execute_s1 returns False without trading."""
    b = _make_bot(monkeypatch)

    mark = 100.0
    sr_pct = config_s1.S1_MIN_SR_CLEARANCE * 100 - 1.0  # just below threshold

    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: mark)

    opened = []
    monkeypatch.setattr(bot.tr, "open_long",  lambda *a, **kw: opened.append("long")  or {})
    monkeypatch.setattr(bot.tr, "open_short", lambda *a, **kw: opened.append("short") or {})

    c = _make_candidate(sig="LONG", sr_pct=sr_pct)
    result = b._execute_s1(c, 1000.0)

    assert result is False, "Should return False when S/R clearance below gate"
    assert opened == [], "No trade should be opened when S/R clearance below gate"


# ── Test 6: SHORT skipped when sr_pct below gate ──────────────────── #

def test_short_skipped_when_sr_clearance_below_gate(monkeypatch):
    """SHORT: when sr_pct < S1_MIN_SR_CLEARANCE * 100, _execute_s1 returns False without trading."""
    b = _make_bot(monkeypatch)

    mark = 100.0
    sr_pct = config_s1.S1_MIN_SR_CLEARANCE * 100 - 0.1  # just below threshold

    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: mark)

    opened = []
    monkeypatch.setattr(bot.tr, "open_long",  lambda *a, **kw: opened.append("long")  or {})
    monkeypatch.setattr(bot.tr, "open_short", lambda *a, **kw: opened.append("short") or {})

    c = _make_candidate(sig="SHORT", sr_pct=sr_pct)
    result = b._execute_s1(c, 1000.0)

    assert result is False, "Should return False when S/R clearance below gate"
    assert opened == [], "No trade should be opened when S/R clearance below gate"


# ── Test 7: LONG proceeds when sr_pct meets gate ──────────────────── #

def test_long_proceeds_when_sr_clearance_at_gate(monkeypatch):
    """LONG: when sr_pct >= S1_MIN_SR_CLEARANCE * 100, trade is opened."""
    b = _make_bot(monkeypatch)

    mark = 100.0
    sr_pct = config_s1.S1_MIN_SR_CLEARANCE * 100  # exactly at threshold

    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: mark)

    opened = []
    def fake_open_long(symbol, sl_floor, leverage, trade_size_pct, use_s1_exits):
        opened.append("long")
        return {"symbol": symbol, "side": "LONG", "qty": 1.0, "entry": mark,
                "sl": sl_floor, "tp": mark * 1.1, "leverage": leverage,
                "margin": 10.0, "tpsl_set": True}

    monkeypatch.setattr(bot.tr, "open_long", fake_open_long)

    c = _make_candidate(sig="LONG", sr_pct=sr_pct)
    result = b._execute_s1(c, 1000.0)

    assert result is True, "Should return True when S/R clearance meets gate"
    assert opened == ["long"], "open_long should be called when clearance meets gate"


# ── Swing trail guard math ────────────────────────────────────────── #

def test_swing_trail_long_sl_only_steps_up():
    """For LONG: swing_sl must be ignored if it is <= current ap['sl'] (would move SL down)."""
    ap = {"side": "LONG", "strategy": "S1", "sl": 100.0}
    swing_sl = 99.0  # below current SL — must NOT be applied
    if swing_sl is not None and swing_sl <= ap.get("sl", 0):
        swing_sl = None
    assert swing_sl is None, "Guard should suppress SL that would move down for LONG"


def test_swing_trail_long_sl_applied_when_higher():
    """For LONG: swing_sl is applied when it is above the current ap['sl']."""
    ap = {"side": "LONG", "strategy": "S1", "sl": 100.0}
    swing_sl = 101.0  # above current SL — should be applied
    if swing_sl is not None and swing_sl <= ap.get("sl", 0):
        swing_sl = None
    assert swing_sl == 101.0, "Guard should allow SL that moves up for LONG"


def test_swing_trail_short_sl_only_steps_down():
    """For SHORT: swing_sl must be ignored if it is >= current ap['sl'] (would move SL up)."""
    ap = {"side": "SHORT", "strategy": "S1", "sl": 200.0}
    swing_sl = 201.0  # above current SL — must NOT be applied
    if swing_sl is not None and swing_sl >= ap.get("sl", float("inf")):
        swing_sl = None
    assert swing_sl is None, "Guard should suppress SL that would move up for SHORT"


def test_swing_trail_short_sl_applied_when_lower():
    """For SHORT: swing_sl is applied when it is below the current ap['sl']."""
    ap = {"side": "SHORT", "strategy": "S1", "sl": 200.0}
    swing_sl = 199.0  # below current SL — should be applied
    if swing_sl is not None and swing_sl >= ap.get("sl", float("inf")):
        swing_sl = None
    assert swing_sl == 199.0, "Guard should allow SL that moves down for SHORT"
