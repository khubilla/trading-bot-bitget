"""
Tests for IGBot streaming integration.
Covers _on_stream_event() dispatch and _tick() pause/reauth behaviour.
"""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import ig_bot
import ig_client as ig
import config_ig


def _make_bot(monkeypatch, tmp_path):
    """Return an IGBot in paper mode with no external calls."""
    state_file = str(tmp_path / "test_state.json")
    monkeypatch.setattr(config_ig, "STATE_FILE", state_file)
    bot = ig_bot.IGBot(paper=True)
    return bot


# ── _on_stream_event ──────────────────────────────────────────── #

def test_on_stream_event_wou_fill_calls_handle_pending_filled(monkeypatch, tmp_path):
    """WOU_FILL event for known deal_id calls _handle_pending_filled and clears pending."""
    bot = _make_bot(monkeypatch, tmp_path)
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


def test_on_stream_event_wou_fill_ignores_unknown_deal_id(monkeypatch, tmp_path):
    """WOU_FILL for unknown deal_id causes no error and no state mutation."""
    bot = _make_bot(monkeypatch, tmp_path)
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


def test_on_stream_event_opu_close_calls_handle_position_closed(monkeypatch, tmp_path):
    """OPU_CLOSE event for known deal_id calls _handle_position_closed."""
    bot = _make_bot(monkeypatch, tmp_path)
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
    assert closed_calls[0][0] == 4620.0   # mark price passed through correctly


# ── _tick() stream-guard tests ────────────────────────────────── #

import ig_stream


def test_tick_pauses_when_stream_disconnected_live_mode(monkeypatch, tmp_path):
    """_tick() returns early without calling _tick_instrument when stream is down (live, non-paper)."""
    bot = _make_bot(monkeypatch, tmp_path)
    bot.paper = False  # pretend live mode

    monkeypatch.setattr(ig_stream, "_connected", False)
    monkeypatch.setattr(ig_stream, "_needs_reauth", False)

    called = []
    monkeypatch.setattr(bot, "_tick_instrument", lambda inst, now: called.append(inst))

    bot._tick()

    assert called == [], "_tick_instrument must not be called when stream is disconnected"


def test_tick_runs_normally_in_paper_mode_regardless_of_stream(monkeypatch, tmp_path):
    """_tick() in paper mode ignores stream state entirely."""
    bot = _make_bot(monkeypatch, tmp_path)
    assert bot.paper is True

    monkeypatch.setattr(ig_stream, "_connected", False)  # stream "down"

    called = []
    monkeypatch.setattr(bot, "_tick_instrument", lambda inst, now: called.append(inst))

    bot._tick()

    assert len(called) == len(config_ig.INSTRUMENTS), "all instruments should tick in paper mode"


def test_tick_triggers_reauth_when_needs_reauth(monkeypatch, tmp_path):
    """When needs_reauth() is True, _tick() refreshes session and restarts stream."""
    bot = _make_bot(monkeypatch, tmp_path)
    bot.paper = False

    monkeypatch.setattr(ig_stream, "_needs_reauth", True)
    monkeypatch.setattr(ig_stream, "_connected", False)

    refresh_called = []
    start_called   = []
    monkeypatch.setattr(ig, "_refresh_session", lambda: refresh_called.append(1))
    monkeypatch.setattr(ig_stream, "stop", lambda: None)
    monkeypatch.setattr(ig_stream, "start", lambda **kw: start_called.append(kw))

    # Provide fake session credentials
    monkeypatch.setattr(ig, "get_stream_credentials", lambda: {
        "account_id": "ACC1", "cst": "cst1", "xst": "xst1",
        "ls_endpoint": "https://ls.ig.com",
    })

    tick_instrument_calls = []
    monkeypatch.setattr(bot, "_tick_instrument", lambda inst, now: tick_instrument_calls.append(inst))

    bot._tick()

    assert refresh_called == [1], "_refresh_session must be called"
    assert len(start_called) == 1, "ig_stream.start must be called"
    assert tick_instrument_calls == [], "_tick_instrument must NOT run this tick"
