import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import time
import pytest
import pandas as pd

import bot
import trader as tr


# ── Shared helpers ────────────────────────────────────────────────── #

def _make_m15_df(n=30) -> pd.DataFrame:
    """Minimal m15 DataFrame with no S/R levels near the trigger price."""
    closes = [1.000 + i * 0.0001 for i in range(n)]
    return pd.DataFrame({
        "open":  [c - 0.0001 for c in closes],
        "high":  [c + 0.0005 for c in closes],
        "low":   [c - 0.0005 for c in closes],
        "close": closes,
        "vol":   [1000.0] * n,
    })


def _make_bot(monkeypatch) -> bot.MTFBot:
    """Return a minimal MTFBot bypassing __init__."""
    b = object.__new__(bot.MTFBot)
    b.pending_signals = {}
    b.sentiment = type("S", (), {"direction": "BULLISH"})()

    # Stub out side-effects
    monkeypatch.setattr(bot.st, "add_scan_log", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "find_nearest_resistance", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "find_nearest_support", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "claude_approve", lambda *a, **kw: {"approved": True})
    monkeypatch.setattr(bot, "PAPER_MODE", False)

    # Stub trader helpers used inside _queue_s5_pending
    monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)
    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: 1.0100)
    monkeypatch.setattr(bot.tr, "_get_total_equity", lambda: 1000.0)
    monkeypatch.setattr(bot.tr, "_round_qty", lambda qty, sym: str(round(qty, 4)))

    return b


# ── Tests ─────────────────────────────────────────────────────────── #

SYMBOL  = "XYZUSDT"
TRIGGER = 1.0100
SL      = 0.9900
TP      = 1.0500
OB_LOW  = 0.9950
OB_HIGH = 1.0000


def test_queue_s5_pending_stores_signal(monkeypatch):
    """After calling _queue_s5_pending, pending_signals[symbol] contains expected fields."""
    b = _make_bot(monkeypatch)
    monkeypatch.setattr(bot.tr, "place_limit_long", lambda *a, **kw: "ORD001")

    df = _make_m15_df()
    b._queue_s5_pending(SYMBOL, "PENDING_LONG", TRIGGER, SL, TP, OB_LOW, OB_HIGH, df)

    assert SYMBOL in b.pending_signals
    sig = b.pending_signals[SYMBOL]
    assert sig["side"] == "LONG"
    assert sig["trigger"] == TRIGGER
    assert sig["sl"] == SL
    assert sig["tp"] == TP
    assert sig["order_id"] == "ORD001"


def test_queue_s5_pending_long_calls_place_limit_long(monkeypatch):
    """place_limit_long called with (symbol, trigger, sl, tp, qty_str); order_id stored."""
    b = _make_bot(monkeypatch)
    calls = []

    def fake_place_long(symbol, limit_price, sl_price, tp_price, qty_str):
        calls.append((symbol, limit_price, sl_price, tp_price, qty_str))
        return "ORD999"

    monkeypatch.setattr(bot.tr, "place_limit_long", fake_place_long)

    df = _make_m15_df()
    b._queue_s5_pending(SYMBOL, "PENDING_LONG", TRIGGER, SL, TP, OB_LOW, OB_HIGH, df)

    assert len(calls) == 1
    sym, lp, sl_p, tp_p, qty = calls[0]
    assert sym == SYMBOL
    assert lp  == TRIGGER
    assert sl_p == SL
    assert tp_p == TP
    assert isinstance(qty, str)
    # equity=1000, notional=1000*0.04*10=400, mark=1.0100 → qty=400/1.0100 rounded to 4dp
    assert qty == "396.0396"
    assert b.pending_signals[SYMBOL]["order_id"] == "ORD999"


def test_queue_s5_pending_short_calls_place_limit_short(monkeypatch):
    """place_limit_short called with correct args for SHORT side; order_id stored."""
    b = _make_bot(monkeypatch)
    # For SHORT, SL is above trigger, TP is below
    sl_short = 1.0300
    tp_short  = 0.9700
    calls = []

    def fake_place_short(symbol, limit_price, sl_price, tp_price, qty_str):
        calls.append((symbol, limit_price, sl_price, tp_price, qty_str))
        return "ORD888"

    monkeypatch.setattr(bot.tr, "place_limit_short", fake_place_short)
    monkeypatch.setattr(bot, "find_nearest_support", lambda *a, **kw: None)

    df = _make_m15_df()
    b._queue_s5_pending(SYMBOL, "PENDING_SHORT", TRIGGER, sl_short, tp_short,
                        OB_LOW, OB_HIGH, df)

    assert len(calls) == 1
    sym, lp, sl_p, tp_p, qty = calls[0]
    assert sym  == SYMBOL
    assert lp   == TRIGGER
    assert sl_p == sl_short
    assert tp_p == tp_short
    assert isinstance(qty, str)
    assert b.pending_signals[SYMBOL]["order_id"] == "ORD888"


def test_queue_s5_pending_order_id_in_pending_signals(monkeypatch):
    """order_id returned by place_limit_long is accessible via pending_signals[symbol]."""
    b = _make_bot(monkeypatch)
    monkeypatch.setattr(bot.tr, "place_limit_long", lambda *a, **kw: "ORDABC")

    df = _make_m15_df()
    b._queue_s5_pending(SYMBOL, "PENDING_LONG", TRIGGER, SL, TP, OB_LOW, OB_HIGH, df)

    assert b.pending_signals[SYMBOL]["order_id"] == "ORDABC"


def test_queue_s5_pending_paper_mode_sets_paper_order_id(monkeypatch):
    """In PAPER_MODE, order_id is set to 'PAPER' without calling place_limit_long."""
    b = _make_bot(monkeypatch)
    monkeypatch.setattr(bot, "PAPER_MODE", True)

    called = []
    monkeypatch.setattr(bot.tr, "place_limit_long",
                        lambda *a, **kw: called.append(1) or "SHOULD_NOT")

    df = _make_m15_df()
    b._queue_s5_pending(SYMBOL, "PENDING_LONG", TRIGGER, SL, TP, OB_LOW, OB_HIGH, df)

    assert called == [], "place_limit_long must NOT be called in PAPER_MODE"
    assert b.pending_signals[SYMBOL]["order_id"] == "PAPER"


def test_queue_s5_pending_pops_signal_on_place_error(monkeypatch):
    """If place_limit_long raises, the pending signal is removed (not left dangling)."""
    b = _make_bot(monkeypatch)
    monkeypatch.setattr(bot.tr, "place_limit_long",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("API error")))

    df = _make_m15_df()
    b._queue_s5_pending(SYMBOL, "PENDING_LONG", TRIGGER, SL, TP, OB_LOW, OB_HIGH, df)

    assert SYMBOL not in b.pending_signals, (
        "Failed limit order placement must remove the pending signal"
    )
