"""
Tests for IGBot pending order state (Task 7).

Covers:
  1. _tick() places limit order when PENDING_LONG signal fires
  2. _tick() places limit order when PENDING_SHORT signal fires
  3. _tick() skips evaluate_s5 when pending_order is already set
  4. _check_pending_order() handles fill → _handle_pending_filled + clears pending
  5. _check_pending_order() cancels on OB invalidation (LONG — mark drops below ob_low)
  6. _check_pending_order() cancels on OB invalidation (SHORT — mark rises above ob_high)
  7. _check_pending_order() cancels on expiry
  8. _session_end_close() cancels pending order if one exists
  9. pending_order round-trips through _save_state / _sync_live_position (load)
"""
import sys
import os
import json
import time
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import ig_bot
import ig_client as ig
import config_ig

# ── Fixtures / helpers ─────────────────────────────────────────────── #

def _make_bot(monkeypatch, paper=True):
    """Return an IGBot in paper mode with external calls patched out."""
    # Prevent actual session / state file interaction
    monkeypatch.setattr(ig_bot, "_in_trading_window", lambda now: True)
    monkeypatch.setattr(ig_bot, "_is_session_end",    lambda now: False)

    # Use a temp state file
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    monkeypatch.setattr(config_ig, "STATE_FILE", tmp.name)

    # Paper PaperState — prevent file I/O noise
    bot = ig_bot.IGBot(paper=paper)
    return bot


def _pending_order(side="LONG", expires_offset=3600):
    return {
        "deal_id":  "DEAL_PENDING_001",
        "side":     side,
        "ob_low":   40000.0,
        "ob_high":  40500.0,
        "sl":       39500.0,
        "tp":       42000.0,
        "trigger":  40500.0 if side == "LONG" else 40000.0,
        "size":     1,
        "expires":  time.time() + expires_offset,
    }


# ── 1. test_tick_places_limit_on_pending_long ─────────────────────── #

def test_tick_places_limit_on_pending_long(monkeypatch):
    """When evaluate_s5 returns PENDING_LONG, place_limit_long is called and pending_order is set."""
    bot = _make_bot(monkeypatch)

    # Patch candle fetches to return non-empty DataFrames
    import pandas as pd
    dummy_df = pd.DataFrame({"ts": [1], "open": [40000], "high": [40100],
                              "low": [39900], "close": [40050], "vol": [100]})
    monkeypatch.setattr(bot, "_get_candles", lambda interval, limit: dummy_df)

    # Patch calculate_ema so ema_fast > ema_slow (BULLISH)
    monkeypatch.setattr(ig_bot, "calculate_ema", lambda series, period: pd.Series([100.0]))

    # evaluate_s5 returns PENDING_LONG
    monkeypatch.setattr(
        ig_bot, "evaluate_s5",
        lambda *a, **kw: ("PENDING_LONG", 40500.0, 39500.0, 42000.0, 40000.0, 40500.0, "OB hit"),
    )

    # get_mark_price returns above trigger so BUY LIMIT at 40500 is valid (price pulling back down)
    monkeypatch.setattr(ig, "get_mark_price", lambda epic: 40600.0)

    placed_calls = []
    monkeypatch.setattr(ig, "place_limit_long",
                        lambda epic, lp, sl, tp, size, **kwargs: (placed_calls.append((lp, sl, tp, size)), "DEAL001")[1])
    monkeypatch.setattr(config_ig, "INSTRUMENTS", [config_ig.INSTRUMENTS[0]])

    bot._tick()

    assert len(placed_calls) == 1, "place_limit_long should be called once"
    assert bot.pending_order is not None
    assert bot.pending_order["deal_id"] == "DEAL001"
    assert bot.pending_order["side"] == "LONG"


# ── 2. test_tick_places_limit_on_pending_short ────────────────────── #

def test_tick_places_limit_on_pending_short(monkeypatch):
    """When evaluate_s5 returns PENDING_SHORT, place_limit_short is called and pending_order is set."""
    bot = _make_bot(monkeypatch)

    import pandas as pd
    dummy_df = pd.DataFrame({"ts": [1], "open": [40000], "high": [40100],
                              "low": [39900], "close": [40050], "vol": [100]})
    monkeypatch.setattr(bot, "_get_candles", lambda interval, limit: dummy_df)
    monkeypatch.setattr(ig_bot, "calculate_ema", lambda series, period: pd.Series([100.0]))

    monkeypatch.setattr(
        ig_bot, "evaluate_s5",
        lambda *a, **kw: ("PENDING_SHORT", 40000.0, 40500.0, 38000.0, 40000.0, 40500.0, "OB hit short"),
    )
    # get_mark_price returns below trigger so SELL LIMIT at 40000 is valid (price pulling up)
    monkeypatch.setattr(ig, "get_mark_price", lambda epic: 39900.0)

    placed_calls = []
    monkeypatch.setattr(ig, "place_limit_short",
                        lambda epic, lp, sl, tp, size, **kwargs: (placed_calls.append((lp, sl, tp, size)), "DEAL002")[1])
    monkeypatch.setattr(config_ig, "INSTRUMENTS", [config_ig.INSTRUMENTS[0]])

    bot._tick()

    assert len(placed_calls) == 1, "place_limit_short should be called once"
    assert bot.pending_order is not None
    assert bot.pending_order["deal_id"] == "DEAL002"
    assert bot.pending_order["side"] == "SHORT"


# ── 3. test_tick_skips_evaluation_when_pending_order_exists ──────── #

def test_tick_skips_evaluation_when_pending_order_exists(monkeypatch):
    """When pending_order is set, _tick() should NOT call evaluate_s5 or place any order."""
    monkeypatch.setattr(config_ig, "INSTRUMENTS", [config_ig.INSTRUMENTS[0]])
    bot = _make_bot(monkeypatch)
    bot.pending_order = _pending_order()

    evaluate_called = []
    monkeypatch.setattr(ig_bot, "evaluate_s5",
                        lambda *a, **kw: (evaluate_called.append(True), ("PENDING_LONG", 1, 1, 1, 1, 1, ""))[1])
    monkeypatch.setattr(ig, "get_mark_price", lambda epic: 40300.0)

    # Patch _check_pending_order to do nothing (just return True to indicate handled)
    monkeypatch.setattr(bot, "_check_pending_order", lambda mark: True)

    bot._tick()

    assert len(evaluate_called) == 0, "evaluate_s5 must not be called when pending_order exists"


# ── 4. test_check_pending_handles_fill ───────────────────────────── #

def test_check_pending_handles_fill(monkeypatch):
    """When get_working_order_status returns filled, _handle_pending_filled is called and pending cleared."""
    bot = _make_bot(monkeypatch)
    bot.pending_order = _pending_order()

    monkeypatch.setattr(ig, "get_working_order_status",
                        lambda deal_id: {"status": "filled", "fill_price": 40505.0})

    filled_calls = []
    monkeypatch.setattr(bot, "_handle_pending_filled",
                        lambda fill_price: filled_calls.append(fill_price))

    result = bot._check_pending_order(40505.0)

    assert result is True
    assert len(filled_calls) == 1
    assert filled_calls[0] == 40505.0
    assert bot.pending_order is None


# ── 5. test_check_pending_cancels_on_ob_invalidation_long ────────── #

def test_check_pending_cancels_on_ob_invalidation_long(monkeypatch):
    """LONG: mark drops well below ob_low → cancel + clear pending_order."""
    bot = _make_bot(monkeypatch)
    po = _pending_order(side="LONG")
    bot.pending_order = po

    monkeypatch.setattr(ig, "get_working_order_status",
                        lambda deal_id: {"status": "open", "fill_price": None})

    cancelled = []
    monkeypatch.setattr(ig, "cancel_working_order",
                        lambda deal_id: cancelled.append(deal_id))

    buf = config_ig.INSTRUMENTS[0]["s5_ob_invalidation_buffer_pct"]
    # Mark below ob_low * (1 - buffer) = 40000 * (1 - 0.001) = 39960
    invalidating_mark = po["ob_low"] * (1 - buf) - 1.0  # well below

    result = bot._check_pending_order(invalidating_mark)

    assert result is True
    assert len(cancelled) == 1
    assert cancelled[0] == "DEAL_PENDING_001"
    assert bot.pending_order is None


# ── 6. test_check_pending_cancels_on_ob_invalidation_short ───────── #

def test_check_pending_cancels_on_ob_invalidation_short(monkeypatch):
    """SHORT: mark rises well above ob_high → cancel + clear pending_order."""
    bot = _make_bot(monkeypatch)
    po = _pending_order(side="SHORT")
    bot.pending_order = po

    monkeypatch.setattr(ig, "get_working_order_status",
                        lambda deal_id: {"status": "open", "fill_price": None})

    cancelled = []
    monkeypatch.setattr(ig, "cancel_working_order",
                        lambda deal_id: cancelled.append(deal_id))

    buf = config_ig.INSTRUMENTS[0]["s5_ob_invalidation_buffer_pct"]
    # Mark above ob_high * (1 + buffer) = 40500 * 1.001 = 40540.5
    invalidating_mark = po["ob_high"] * (1 + buf) + 1.0

    result = bot._check_pending_order(invalidating_mark)

    assert result is True
    assert len(cancelled) == 1
    assert bot.pending_order is None


# ── 7. test_check_pending_cancels_on_expiry ──────────────────────── #

def test_check_pending_cancels_on_expiry(monkeypatch):
    """When expires is in the past, cancel and clear pending_order."""
    bot = _make_bot(monkeypatch)
    po = _pending_order(expires_offset=-1)   # already expired
    bot.pending_order = po

    monkeypatch.setattr(ig, "get_working_order_status",
                        lambda deal_id: {"status": "open", "fill_price": None})

    cancelled = []
    monkeypatch.setattr(ig, "cancel_working_order",
                        lambda deal_id: cancelled.append(deal_id))

    result = bot._check_pending_order(40300.0)  # mark is fine, but order expired

    assert result is True
    assert len(cancelled) == 1
    assert bot.pending_order is None


# ── 8. test_session_end_cancels_pending_order ─────────────────────── #

def test_session_end_cancels_pending_order(monkeypatch):
    """_session_end_close() cancels the pending order if one exists (no open position)."""
    bot = _make_bot(monkeypatch)
    bot.pending_order = _pending_order()
    # No open position
    bot.position = None

    cancelled = []
    monkeypatch.setattr(ig, "cancel_working_order",
                        lambda deal_id: cancelled.append(deal_id))
    monkeypatch.setattr(ig, "get_mark_price", lambda epic: 40300.0)

    # _session_end_close currently requires self.position to be set to close it.
    # We test the pending branch independently: call _session_end_close() with no
    # live position but pending_order set.
    bot._session_end_close()

    assert len(cancelled) == 1
    assert cancelled[0] == "DEAL_PENDING_001"
    assert bot.pending_order is None


# ── 9. test_session_end_order_fills_during_cancel ─────────────────── #

def test_session_end_order_fills_during_cancel(monkeypatch):
    """If cancel attempt coincides with a fill, position is set and then closed."""
    bot = _make_bot(monkeypatch)
    bot.pending_order = _pending_order()
    bot.position = None

    # cancel_working_order succeeds (no raise)
    cancelled = []
    monkeypatch.setattr(ig, "cancel_working_order",
                        lambda deal_id: cancelled.append(deal_id))

    # get_working_order_status reports the order filled during the cancel window
    monkeypatch.setattr(ig, "get_working_order_status",
                        lambda deal_id: {"status": "filled", "fill_price": 40505.0})

    # For the position close path, provide a valid mark price
    monkeypatch.setattr(ig, "get_mark_price", lambda epic: 40505.0)

    bot._session_end_close()

    # cancel was attempted
    assert len(cancelled) == 1
    assert cancelled[0] == "DEAL_PENDING_001"
    # position was created by _handle_pending_filled and then closed by session-end logic
    assert bot.position is None
    # pending_order was cleared
    assert bot.pending_order is None


# ── 10. test_check_pending_status_exception_retries ──────────────────── #

def test_check_pending_status_exception_retries(monkeypatch):
    """When get_working_order_status raises, _check_pending_order returns True (retry next tick)."""
    bot = _make_bot(monkeypatch)
    bot.pending_order = _pending_order()

    monkeypatch.setattr(ig, "get_working_order_status",
                        lambda deal_id: (_ for _ in ()).throw(RuntimeError("network error")))

    result = bot._check_pending_order(40300.0)

    assert result is True
    # pending_order should NOT be cleared — we retry next tick
    assert bot.pending_order is not None


# ── 11. test_pending_order_persisted_in_state ──────────────────────── #

def test_pending_order_persisted_in_state(monkeypatch):
    """pending_order round-trips through _save_state and _sync_live_position (load)."""
    import tempfile, os, json

    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    monkeypatch.setattr(config_ig, "STATE_FILE", tmp.name)

    bot = ig_bot.IGBot(paper=True)
    po = _pending_order()
    bot.pending_order = po
    bot.position = None

    bot._save_state()

    # Read raw JSON to verify it's there (new format uses pending_orders dict)
    with open(tmp.name) as f:
        raw = json.load(f)
    assert raw.get("pending_orders") is not None
    assert raw["pending_orders"]["US30"]["deal_id"] == "DEAL_PENDING_001"

    # Now simulate a fresh bot loading from that state file
    bot2 = ig_bot.IGBot(paper=True)
    assert bot2.pending_order is not None
    assert bot2.pending_order["deal_id"] == "DEAL_PENDING_001"
    assert bot2.pending_order["side"] == "LONG"

    os.unlink(tmp.name)
