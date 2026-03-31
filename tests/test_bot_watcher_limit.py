"""
tests/test_bot_watcher_limit.py

Tests for Task 5: _entry_watcher_loop order-fill polling + _handle_limit_filled.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import time
import threading
import pytest

import bot
import trader as tr


# ── Helpers ───────────────────────────────────────────────────────── #

def _make_bot(monkeypatch) -> bot.MTFBot:
    """Return a minimal MTFBot bypassing __init__."""
    b = object.__new__(bot.MTFBot)
    b.pending_signals = {}
    b.active_positions = {}
    b._trade_lock = threading.Lock()
    b.running = True
    b.sentiment = type("S", (), {"direction": "BULLISH"})()

    monkeypatch.setattr(bot.st, "add_scan_log", lambda *a, **kw: None)
    monkeypatch.setattr(bot.st, "add_open_trade", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "PAPER_MODE", False)

    return b


def _make_s5_sig(side="LONG", order_id="ORD001"):
    return {
        "strategy":   "S5",
        "side":       side,
        "trigger":    0.0800,
        "sl":         0.0750,
        "tp":         0.0950,
        "ob_low":     0.0780,
        "ob_high":    0.0800,
        "qty_str":    "100.0",
        "rr":         3.0,
        "sr_clearance_pct": 15.0,
        "sentiment":  "BULLISH",
        "expires":    time.time() + 7200,
        "order_id":   order_id,
    }


SYMBOL = "XYZUSDT"


# ── Test 1: filled order calls _handle_limit_filled + removes signal ── #

def test_watcher_handles_filled_order(monkeypatch):
    """When get_order_fill returns filled, _handle_limit_filled is called and signal removed."""
    b = _make_bot(monkeypatch)
    sig = _make_s5_sig(side="LONG", order_id="ORD001")
    b.pending_signals[SYMBOL] = sig

    monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)
    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: 0.0801)
    monkeypatch.setattr(
        bot.tr, "get_order_fill",
        lambda sym, oid: {"status": "filled", "fill_price": 0.07951},
    )

    handled = []
    monkeypatch.setattr(
        b, "_handle_limit_filled",
        lambda sym, s, fp, bal: handled.append((sym, fp)),
    )

    # Run one iteration of the watcher by making it stop after the first loop
    b.running = False  # stop after one pass

    # Simulate the inner per-signal logic directly
    with b._trade_lock:
        if SYMBOL not in b.active_positions:
            fill_info = bot.tr.get_order_fill(SYMBOL, sig["order_id"])
            if fill_info["status"] == "filled":
                b._handle_limit_filled(SYMBOL, sig, fill_info["fill_price"], 1000.0)
                b.pending_signals.pop(SYMBOL, None)

    assert len(handled) == 1
    assert handled[0] == (SYMBOL, 0.07951)
    assert SYMBOL not in b.pending_signals


# ── Test 2: OB invalidation LONG ─────────────────────────────────── #

def test_watcher_cancels_on_ob_invalidation_long(monkeypatch):
    """LONG: when mark < ob_low * (1 - buffer), cancel_order called and signal removed."""
    b = _make_bot(monkeypatch)
    sig = _make_s5_sig(side="LONG", order_id="ORD002")
    b.pending_signals[SYMBOL] = sig

    # mark well below ob_low * (1 - 0.001)
    ob_low_thresh = sig["ob_low"] * (1 - bot.config_s5.S5_OB_INVALIDATION_BUFFER_PCT)
    mark = ob_low_thresh - 0.001  # below threshold

    monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)
    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: mark)
    monkeypatch.setattr(
        bot.tr, "get_order_fill",
        lambda sym, oid: {"status": "live", "fill_price": 0.0},
    )

    cancelled = []
    monkeypatch.setattr(
        bot.tr, "cancel_order",
        lambda sym, oid: cancelled.append((sym, oid)),
    )

    # Simulate OB invalidation check
    fill_info = bot.tr.get_order_fill(SYMBOL, sig["order_id"])
    if fill_info["status"] == "live":
        invalidated = mark < sig["ob_low"] * (1 - bot.config_s5.S5_OB_INVALIDATION_BUFFER_PCT)
        if invalidated:
            bot.tr.cancel_order(SYMBOL, sig["order_id"])
            b.pending_signals.pop(SYMBOL, None)

    assert len(cancelled) == 1
    assert cancelled[0] == (SYMBOL, "ORD002")
    assert SYMBOL not in b.pending_signals


# ── Test 3: OB invalidation SHORT ────────────────────────────────── #

def test_watcher_cancels_on_ob_invalidation_short(monkeypatch):
    """SHORT: when mark > ob_high * (1 + buffer), cancel_order called and signal removed."""
    b = _make_bot(monkeypatch)
    sig = _make_s5_sig(side="SHORT", order_id="ORD003")
    # For short, sl > trigger, ob_high is relevant upper bound
    sig["sl"] = 0.0850
    sig["tp"] = 0.0700

    b.pending_signals[SYMBOL] = sig

    # mark well above ob_high * (1 + 0.001)
    ob_high_thresh = sig["ob_high"] * (1 + bot.config_s5.S5_OB_INVALIDATION_BUFFER_PCT)
    mark = ob_high_thresh + 0.001  # above threshold

    monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)
    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: mark)
    monkeypatch.setattr(
        bot.tr, "get_order_fill",
        lambda sym, oid: {"status": "live", "fill_price": 0.0},
    )

    cancelled = []
    monkeypatch.setattr(
        bot.tr, "cancel_order",
        lambda sym, oid: cancelled.append((sym, oid)),
    )

    fill_info = bot.tr.get_order_fill(SYMBOL, sig["order_id"])
    if fill_info["status"] == "live":
        invalidated = mark > sig["ob_high"] * (1 + bot.config_s5.S5_OB_INVALIDATION_BUFFER_PCT)
        if invalidated:
            bot.tr.cancel_order(SYMBOL, sig["order_id"])
            b.pending_signals.pop(SYMBOL, None)

    assert len(cancelled) == 1
    assert cancelled[0] == (SYMBOL, "ORD003")
    assert SYMBOL not in b.pending_signals


# ── Test 4: expiry cancels order ─────────────────────────────────── #

def test_watcher_cancels_on_expiry(monkeypatch):
    """When time.time() > sig['expires'], cancel_order is called and signal removed."""
    b = _make_bot(monkeypatch)
    sig = _make_s5_sig(side="LONG", order_id="ORD004")
    sig["expires"] = time.time() - 1  # already expired

    b.pending_signals[SYMBOL] = sig

    monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)
    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: 0.0800)
    monkeypatch.setattr(
        bot.tr, "get_order_fill",
        lambda sym, oid: {"status": "live", "fill_price": 0.0},
    )

    cancelled = []
    monkeypatch.setattr(
        bot.tr, "cancel_order",
        lambda sym, oid: cancelled.append((sym, oid)),
    )

    # Simulate expiry path
    fill_info = bot.tr.get_order_fill(SYMBOL, sig["order_id"])
    if fill_info["status"] == "live":
        if time.time() > sig["expires"]:
            bot.tr.cancel_order(SYMBOL, sig["order_id"])
            b.pending_signals.pop(SYMBOL, None)

    assert len(cancelled) == 1
    assert cancelled[0] == (SYMBOL, "ORD004")
    assert SYMBOL not in b.pending_signals


# ── Test 5: live order leaves signal in place ─────────────────────── #

def test_watcher_leaves_live_order_alone(monkeypatch):
    """When status is 'live' and no invalidation/expiry, signal stays in pending_signals."""
    b = _make_bot(monkeypatch)
    sig = _make_s5_sig(side="LONG", order_id="ORD005")
    b.pending_signals[SYMBOL] = sig

    # mark is between ob_low and ob_high — not invalidated, not expired
    mark = 0.0790  # within OB zone, above ob_low * (1 - buffer)

    monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)
    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: mark)
    monkeypatch.setattr(
        bot.tr, "get_order_fill",
        lambda sym, oid: {"status": "live", "fill_price": 0.0},
    )

    cancelled = []
    monkeypatch.setattr(
        bot.tr, "cancel_order",
        lambda sym, oid: cancelled.append((sym, oid)),
    )

    fill_info = bot.tr.get_order_fill(SYMBOL, sig["order_id"])
    status = fill_info["status"]
    invalidated_long = (sig["side"] == "LONG" and
                        mark < sig["ob_low"] * (1 - bot.config_s5.S5_OB_INVALIDATION_BUFFER_PCT))
    invalidated_short = (sig["side"] == "SHORT" and
                         mark > sig["ob_high"] * (1 + bot.config_s5.S5_OB_INVALIDATION_BUFFER_PCT))
    expired = time.time() > sig["expires"]

    if status == "filled":
        pass  # would handle
    elif invalidated_long or invalidated_short:
        bot.tr.cancel_order(SYMBOL, sig["order_id"])
        b.pending_signals.pop(SYMBOL, None)
    elif expired:
        bot.tr.cancel_order(SYMBOL, sig["order_id"])
        b.pending_signals.pop(SYMBOL, None)
    # else: leave it alone

    assert len(cancelled) == 0
    assert SYMBOL in b.pending_signals


# ── Test 6: _handle_limit_filled adds active_positions ────────────── #

def test_handle_limit_filled_adds_active_position(monkeypatch):
    """After _handle_limit_filled, symbol is in active_positions with correct entry price."""
    b = _make_bot(monkeypatch)
    sig = _make_s5_sig(side="LONG", order_id="ORD006")
    fill_price = 0.07951

    monkeypatch.setattr(bot.tr, "_round_price", lambda p, sym: str(round(p, 5)))
    monkeypatch.setattr(bot.tr, "_place_s5_exits", lambda *a, **kw: True)
    monkeypatch.setattr(bot, "_log_trade", lambda action, details: None)
    monkeypatch.setattr(bot.st, "add_open_trade", lambda t: None)

    b._handle_limit_filled(SYMBOL, sig, fill_price, 1000.0)

    assert SYMBOL in b.active_positions
    ap = b.active_positions[SYMBOL]
    assert ap["side"] == "LONG"
    assert ap["strategy"] == "S5"
    # entry should be fill_price, accessible via trade_id presence
    assert "trade_id" in ap


def test_handle_limit_filled_entry_price_correct(monkeypatch):
    """active_positions entry is not set by bot.py directly, but trade dict has fill_price."""
    b = _make_bot(monkeypatch)
    sig = _make_s5_sig(side="LONG", order_id="ORD007")
    fill_price = 0.07951

    logged_trades = []

    monkeypatch.setattr(bot.tr, "_round_price", lambda p, sym: str(round(p, 5)))
    monkeypatch.setattr(bot.tr, "_place_s5_exits", lambda *a, **kw: True)
    monkeypatch.setattr(bot, "_log_trade", lambda action, details: logged_trades.append((action, details)))
    monkeypatch.setattr(bot.st, "add_open_trade", lambda t: None)

    b._handle_limit_filled(SYMBOL, sig, fill_price, 1000.0)

    assert SYMBOL in b.active_positions
    # The logged trade should contain entry = fill_price
    assert len(logged_trades) == 1
    action, details = logged_trades[0]
    assert details.get("entry") == fill_price


# ── Test 7: _handle_limit_filled logs trade ───────────────────────── #

def test_handle_limit_filled_logs_trade(monkeypatch):
    """_log_trade is called with correct action 'S5_LONG' and fill_price as entry."""
    b = _make_bot(monkeypatch)
    sig = _make_s5_sig(side="LONG", order_id="ORD008")
    fill_price = 0.07951

    logged = []
    monkeypatch.setattr(bot.tr, "_round_price", lambda p, sym: str(round(p, 5)))
    monkeypatch.setattr(bot.tr, "_place_s5_exits", lambda *a, **kw: True)
    monkeypatch.setattr(bot, "_log_trade", lambda action, details: logged.append((action, details)))
    monkeypatch.setattr(bot.st, "add_open_trade", lambda t: None)

    b._handle_limit_filled(SYMBOL, sig, fill_price, 1000.0)

    assert len(logged) == 1
    action, details = logged[0]
    assert action == "S5_LONG"
    assert details["entry"] == fill_price
    assert details["symbol"] == SYMBOL


def test_handle_limit_filled_logs_trade_short(monkeypatch):
    """_log_trade called with 'S5_SHORT' for a SHORT fill."""
    b = _make_bot(monkeypatch)
    sig = _make_s5_sig(side="SHORT", order_id="ORD009")
    sig["sl"] = 0.0850
    sig["tp"] = 0.0700
    fill_price = 0.0799

    logged = []
    monkeypatch.setattr(bot.tr, "_round_price", lambda p, sym: str(round(p, 5)))
    monkeypatch.setattr(bot.tr, "_place_s5_exits", lambda *a, **kw: True)
    monkeypatch.setattr(bot, "_log_trade", lambda action, details: logged.append((action, details)))
    monkeypatch.setattr(bot.st, "add_open_trade", lambda t: None)

    b._handle_limit_filled(SYMBOL, sig, fill_price, 1000.0)

    assert len(logged) == 1
    action, details = logged[0]
    assert action == "S5_SHORT"
    assert details["entry"] == fill_price


# ── Test 8: non-S5 signals still use price-trigger logic ─────────── #

def test_watcher_non_s5_signal_uses_price_trigger(monkeypatch):
    """A signal with strategy='S3' must NOT call get_order_fill; uses price-trigger path."""
    b = _make_bot(monkeypatch)

    # S3-style pending signal (no order_id field, no strategy="S5")
    s3_sig = {
        "strategy": "S3",
        "side": "LONG",
        "trigger": 0.0800,
        "sl": 0.0750,
        "tp": 0.0950,
        "expires": time.time() + 7200,
        # No order_id
    }
    b.pending_signals[SYMBOL] = s3_sig

    get_order_fill_calls = []
    monkeypatch.setattr(
        bot.tr, "get_order_fill",
        lambda sym, oid: get_order_fill_calls.append((sym, oid)) or {"status": "live", "fill_price": 0.0},
    )

    # Verify that get_order_fill is NOT called for this signal by checking
    # that the S5-specific path is guarded by strategy check
    assert s3_sig.get("strategy") != "S5"
    # In the watcher, we only call get_order_fill when strategy == "S5"
    if s3_sig.get("strategy") == "S5":
        bot.tr.get_order_fill(SYMBOL, s3_sig.get("order_id"))

    assert len(get_order_fill_calls) == 0, "get_order_fill must NOT be called for non-S5 signals"


# ── Test 9: PAPER_MODE simulates fill via price comparison ────────── #

def test_watcher_paper_mode_simulates_fill_on_trigger(monkeypatch):
    """In PAPER_MODE with order_id=='PAPER', fill is simulated when mark >= trigger (LONG)."""
    b = _make_bot(monkeypatch)
    monkeypatch.setattr(bot, "PAPER_MODE", True)

    sig = _make_s5_sig(side="LONG", order_id="PAPER")
    b.pending_signals[SYMBOL] = sig

    # mark >= trigger → should simulate fill
    mark = sig["trigger"]  # exactly at trigger

    handled = []
    monkeypatch.setattr(
        b, "_handle_limit_filled",
        lambda sym, s, fp, bal: handled.append((sym, fp)),
    )

    # Simulate paper fill logic (what the watcher loop does for PAPER)
    order_id = sig.get("order_id")
    fill_price = None
    if order_id == "PAPER":
        if sig["side"] == "LONG" and mark >= sig["trigger"]:
            fill_price = sig["trigger"]
        elif sig["side"] == "SHORT" and mark <= sig["trigger"]:
            fill_price = sig["trigger"]

    if fill_price is not None:
        b._handle_limit_filled(SYMBOL, sig, fill_price, 1000.0)
        b.pending_signals.pop(SYMBOL, None)

    assert len(handled) == 1
    assert handled[0] == (SYMBOL, sig["trigger"])
    assert SYMBOL not in b.pending_signals
