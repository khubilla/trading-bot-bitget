"""
Tests for IGBot streaming integration.
Covers _on_stream_event() dispatch and _tick() pause/reauth behaviour.
"""
import sys, os, time, tempfile, json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import ig_bot
import ig_client as ig
import config_ig


def _make_bot(monkeypatch):
    """Return an IGBot in paper mode with no external calls."""
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    monkeypatch.setattr(config_ig, "STATE_FILE", tmp.name)
    bot = ig_bot.IGBot(paper=True)
    return bot


# ── _on_stream_event ──────────────────────────────────────────── #

def test_on_stream_event_wou_fill_calls_handle_pending_filled(monkeypatch):
    """WOU_FILL event for known deal_id calls _handle_pending_filled and clears pending."""
    bot = _make_bot(monkeypatch)
    inst = config_ig.INSTRUMENTS[0]
    name = inst["display_name"]

    bot._pending_orders[name] = {
        "deal_id":  "DEAL_WOU_001",
        "side":     "SHORT",
        "ob_low":   4600.0,
        "ob_high":  4650.0,
        "sl":       4700.0,
        "tp":       4500.0,
        "trigger":  4640.0,
        "size":     0.1,
        "expires":  time.time() + 3600,
    }

    filled_calls = []
    monkeypatch.setattr(bot, "_handle_pending_filled", lambda fp: filled_calls.append(fp))

    bot._on_stream_event("WOU_FILL", "DEAL_WOU_001", 4640.0)

    assert filled_calls == [4640.0]
    assert bot._pending_orders[name] is None


def test_on_stream_event_wou_fill_ignores_unknown_deal_id(monkeypatch):
    """WOU_FILL for unknown deal_id causes no error and no state mutation."""
    bot = _make_bot(monkeypatch)
    inst = config_ig.INSTRUMENTS[0]
    name = inst["display_name"]

    bot._pending_orders[name] = {
        "deal_id": "DEAL_KNOWN",
        "side": "LONG", "ob_low": 100.0, "ob_high": 110.0,
        "sl": 90.0, "tp": 130.0, "trigger": 105.0, "size": 1, "expires": time.time() + 3600,
    }

    filled_calls = []
    monkeypatch.setattr(bot, "_handle_pending_filled", lambda fp: filled_calls.append(fp))

    bot._on_stream_event("WOU_FILL", "DEAL_UNKNOWN_XYZ", 105.0)

    assert filled_calls == []  # not called
    assert bot._pending_orders[name]["deal_id"] == "DEAL_KNOWN"  # unchanged


def test_on_stream_event_opu_close_calls_handle_position_closed(monkeypatch):
    """OPU_CLOSE event for known deal_id calls _handle_position_closed."""
    bot = _make_bot(monkeypatch)
    inst = config_ig.INSTRUMENTS[0]
    name = inst["display_name"]

    bot._positions[name] = {
        "deal_id":    "POS_OPU_001",
        "side":       "LONG",
        "entry":      4600.0,
        "sl":         4550.0,
        "tp1":        4650.0,
        "tp":         4700.0,
        "initial_qty":  0.1,
        "current_qty":  0.1,
        "partial_done": False,
        "trade_id":   "abc123",
        "opened_at":  "2026-04-07T10:00:00",
        "ob_low":     4580.0,
        "ob_high":    4610.0,
    }

    closed_calls = []
    monkeypatch.setattr(
        bot, "_handle_position_closed",
        lambda mark, inst, exit_reason=None: closed_calls.append((mark, exit_reason))
    )
    monkeypatch.setattr(ig, "get_mark_price", lambda epic: 4620.0)

    bot._on_stream_event("OPU_CLOSE", "POS_OPU_001", None)

    assert len(closed_calls) == 1
    assert closed_calls[0][1] == "SL_OR_TP"
