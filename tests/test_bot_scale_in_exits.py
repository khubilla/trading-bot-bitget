"""
Test: S2/S4/S6/S7 scale-in should update partial TP and trailing stops.

Bug: refresh_plan_exits is called outside the 'if new_avg > 0:' block,
causing it to use new_trig=0.0 when position data hasn't updated yet,
which falls back to the original TP trigger instead of the recalculated one.

Expected behavior:
- After scale-in, partial TP and trailing should use new average entry
- refresh_plan_exits should only be called AFTER new_trig is computed

Evidence from trades.csv (HUSDT 2048565b):
- Initial entry: 0.19147
- Scale-in: 0.1915
- Expected new avg: ~0.19149
- Expected new TP: 0.21063 (new_avg * 1.10)
- ACTUAL partial TP: 0.21106 (matches ORIGINAL TP of 0.21062)
"""

import time
from unittest.mock import Mock, patch, MagicMock, call
import pytest


@pytest.fixture
def mock_bot():
    """Create a minimal bot instance with required attributes."""
    from bot import MTFBot
    with patch("bot.PAPER_MODE", False), \
         patch("bot.tr"), \
         patch("bot.st"):
        bot = MTFBot.__new__(MTFBot)
        bot.active_positions = {}
        bot._trade_lock = MagicMock()
        bot._trade_lock.__enter__ = Mock(return_value=None)
        bot._trade_lock.__exit__ = Mock(return_value=None)
        bot.sentiment = Mock(direction="BULLISH")
        return bot


@pytest.fixture
def mock_s2_position():
    """Mock S2 LONG position after initial entry."""
    return {
        "side": "LONG",
        "strategy": "S2",
        "box_high": 0.19147,
        "box_low": 0.17901,
        "trade_id": "test123",
        "sl": 0.1819,
        "scale_in_pending": True,
        "scale_in_after": time.time() - 1,  # Eligible for scale-in
        "scale_in_trade_size_pct": 0.04,
        "qty": 163.0,
        "initial_qty": 163.0,
    }


@patch("bot.tr")
@patch("bot.st")
@patch("bot.logger")
def test_scale_in_updates_partial_tp_and_trailing(mock_logger, mock_st, mock_tr, mock_bot, mock_s2_position):
    """
    Test that S2 scale-in properly updates partial TP and trailing stops
    to reflect the new average entry price.

    This test verifies the fix for the bug where refresh_plan_exits was
    called with new_trig=0.0, causing it to fall back to original TP.
    """
    # Setup
    symbol = "HUSDT"
    mock_bot.active_positions[symbol] = mock_s2_position

    # Mock trader functions
    mock_tr.get_mark_price.return_value = 0.1915  # Price at scale-in
    mock_tr.get_all_open_positions.return_value = {
        symbol: {
            "qty": 326.0,  # Doubled after scale-in
            "margin": 6.265,
            "entry_price": 0.19149,  # New average: (0.19147 + 0.1915) / 2
        }
    }
    mock_tr.scale_in_long.return_value = None
    mock_tr.update_position_sl.return_value = True
    mock_tr.refresh_plan_exits.return_value = True

    # Mock strategy module
    with patch("importlib.import_module") as mock_import:
        mock_s2 = Mock()
        mock_s2.scale_in_specs.return_value = {
            "direction": "BULLISH",
            "hold_side": "long",
            "leverage": 10,
        }
        mock_s2.is_scale_in_window.return_value = True
        # This is the key calculation that should happen
        mock_s2.recompute_scale_in_sl_trigger.return_value = (
            0.18192,  # new_sl: max(box_low*0.999, new_avg*(1-0.05))
            0.21063,  # new_trig: new_avg * (1 + 0.10)
        )
        mock_import.return_value = mock_s2

        # Execute scale-in
        mock_bot._do_scale_in(symbol, mock_s2_position)

        # Verify scale-in was executed
        mock_tr.scale_in_long.assert_called_once_with(symbol, 0.02, 10)

        # CRITICAL ASSERTION: refresh_plan_exits must be called with new_trig > 0
        mock_tr.refresh_plan_exits.assert_called_once()
        call_args = mock_tr.refresh_plan_exits.call_args
        assert call_args[0][0] == symbol
        assert call_args[0][1] == "long"

        # THE BUG: new_trig would be 0.0, causing fallback to original TP
        # THE FIX: new_trig should be 0.21063 (new_avg * 1.10)
        actual_new_trig = call_args[0][2]
        expected_new_trig = 0.21063

        assert actual_new_trig > 0, \
            f"BUG: refresh_plan_exits called with new_trig={actual_new_trig}, should be {expected_new_trig}"

        assert abs(actual_new_trig - expected_new_trig) < 0.0001, \
            f"refresh_plan_exits called with wrong new_trig: {actual_new_trig}, expected {expected_new_trig}"

        # Verify SL was also updated
        mock_tr.update_position_sl.assert_called_once()
        sl_call_args = mock_tr.update_position_sl.call_args
        assert abs(sl_call_args[0][1] - 0.18192) < 0.0001


@patch("bot.tr")
@patch("bot.st")
@patch("bot.logger")
def test_scale_in_passes_new_sl_to_refresh_plan_exits(mock_logger, mock_st, mock_tr, mock_bot, mock_s2_position):
    """
    Regression (HUSDT 5ddb3c71): after scale-in, the recomputed SL must be
    forwarded to refresh_plan_exits so Bybit can re-assert it atomically with
    the trailing stop. Without this, the Full-mode trading-stop REPLACE wiped
    the SL and the position ran to liquidation.
    """
    symbol = "HUSDT"
    mock_bot.active_positions[symbol] = mock_s2_position

    mock_tr.get_mark_price.return_value = 0.1915
    mock_tr.get_all_open_positions.return_value = {
        symbol: {"qty": 326.0, "margin": 6.265, "entry_price": 0.19149}
    }
    mock_tr.scale_in_long.return_value = None
    mock_tr.update_position_sl.return_value = True
    mock_tr.refresh_plan_exits.return_value = True

    with patch("importlib.import_module") as mock_import:
        mock_s2 = Mock()
        mock_s2.scale_in_specs.return_value = {
            "direction": "BULLISH", "hold_side": "long", "leverage": 10,
        }
        mock_s2.is_scale_in_window.return_value = True
        mock_s2.recompute_scale_in_sl_trigger.return_value = (0.18192, 0.21063)
        mock_import.return_value = mock_s2

        mock_bot._do_scale_in(symbol, mock_s2_position)

        mock_tr.refresh_plan_exits.assert_called_once()
        _, kwargs = mock_tr.refresh_plan_exits.call_args
        assert "sl_price" in kwargs, "refresh_plan_exits must receive sl_price"
        assert abs(kwargs["sl_price"] - 0.18192) < 1e-6, \
            f"sl_price forwarded wrong: {kwargs.get('sl_price')}"


@patch("bot.tr")
@patch("bot.st")
@patch("bot.logger")
def test_scale_in_defers_refresh_when_fill_not_reflected(mock_logger, mock_st, mock_tr, mock_bot, mock_s2_position):
    """
    AMDUSDT bug (2026-06-03): when the scale-in fill hasn't reflected in the
    position API yet, exits must NOT be refreshed against the stale (pre-scale)
    qty. Instead the scale-in is marked done (order already placed) and a
    `scale_in_refresh_pending` flag is set so a later tick refreshes once the
    fill is confirmed.
    """
    symbol = "HUSDT"
    mock_bot.active_positions[symbol] = mock_s2_position

    # Position API still shows the pre-scale qty (fill lag), no avg yet.
    mock_tr.get_mark_price.return_value = 0.1915
    mock_tr.get_all_open_positions.return_value = {
        symbol: {"qty": 163.0, "margin": 3.1325, "entry_price": 0}
    }
    mock_tr.scale_in_long.return_value = None
    mock_tr.refresh_plan_exits.return_value = True

    with patch("importlib.import_module") as mock_import:
        mock_s2 = Mock()
        mock_s2.scale_in_specs.return_value = {
            "direction": "BULLISH", "hold_side": "long", "leverage": 10,
        }
        mock_s2.is_scale_in_window.return_value = True
        mock_s2.recompute_scale_in_sl_trigger.return_value = (0.18192, 0.21063)
        mock_import.return_value = mock_s2

        mock_bot._do_scale_in(symbol, mock_s2_position)

        # Order placed exactly once
        mock_tr.scale_in_long.assert_called_once()
        # Exits NOT refreshed against the stale qty
        assert not mock_tr.refresh_plan_exits.called, \
            "must not refresh exits before the scale-in fill is confirmed"
        # Deferred for a later tick; placement phase is complete
        assert mock_s2_position.get("scale_in_refresh_pending") is True
        assert mock_s2_position.get("scale_in_pending") is False


@patch("bot.tr")
@patch("bot.st")
@patch("bot.logger")
def test_refresh_retries_after_fill_without_reordering(mock_logger, mock_st, mock_tr, mock_bot, mock_s2_position):
    """
    Once the fill is confirmed on a later tick, `_refresh_scale_in_exits` resizes
    the exits against the NEW total qty and clears the flag — and must NEVER
    re-place the scale-in market order (no double scale-in).
    """
    symbol = "HUSDT"
    ap = dict(mock_s2_position)
    ap["scale_in_pending"] = False
    ap["scale_in_refresh_pending"] = True
    ap["scale_in_pre_qty"] = 163.0
    ap["scale_in_refresh_deadline"] = time.time() + 300
    mock_bot.active_positions[symbol] = ap

    # Fill now reflected: qty doubled, new avg available.
    mock_tr.get_all_open_positions.return_value = {
        symbol: {"qty": 326.0, "margin": 6.265, "entry_price": 0.19149}
    }
    mock_tr.update_position_sl.return_value = True
    mock_tr.refresh_plan_exits.return_value = True

    with patch("importlib.import_module") as mock_import:
        mock_s2 = Mock()
        mock_s2.scale_in_specs.return_value = {
            "direction": "BULLISH", "hold_side": "long", "leverage": 10,
        }
        mock_s2.recompute_scale_in_sl_trigger.return_value = (0.18192, 0.21063)
        mock_import.return_value = mock_s2

        mock_bot._refresh_scale_in_exits(symbol, ap)

        # No re-order on the retry path
        assert not mock_tr.scale_in_long.called, "must not re-place the scale-in order"
        # Refresh ran against the new qty with recomputed trigger + SL
        mock_tr.refresh_plan_exits.assert_called_once()
        args, kwargs = mock_tr.refresh_plan_exits.call_args
        assert abs(args[2] - 0.21063) < 1e-4
        assert abs(kwargs["sl_price"] - 0.18192) < 1e-6
        # Flag cleared so it won't run again
        assert ap.get("scale_in_refresh_pending") is False


@patch("bot.tr")
@patch("bot.st")
@patch("bot.logger")
def test_refresh_keeps_pending_when_still_lagging(mock_logger, mock_st, mock_tr, mock_bot, mock_s2_position):
    """`_refresh_scale_in_exits` keeps the flag set (and refreshes nothing) while
    the fill is still not reflected and the deadline has not passed."""
    symbol = "HUSDT"
    ap = dict(mock_s2_position)
    ap["scale_in_pending"] = False
    ap["scale_in_refresh_pending"] = True
    ap["scale_in_pre_qty"] = 163.0
    ap["scale_in_refresh_deadline"] = time.time() + 300
    mock_bot.active_positions[symbol] = ap

    mock_tr.get_all_open_positions.return_value = {
        symbol: {"qty": 163.0, "margin": 3.1325, "entry_price": 0}
    }

    with patch("importlib.import_module") as mock_import:
        mock_s2 = Mock()
        mock_s2.scale_in_specs.return_value = {
            "direction": "BULLISH", "hold_side": "long", "leverage": 10,
        }
        mock_s2.recompute_scale_in_sl_trigger.return_value = (0.18192, 0.21063)
        mock_import.return_value = mock_s2

        mock_bot._refresh_scale_in_exits(symbol, ap)

        assert not mock_tr.refresh_plan_exits.called
        assert not mock_tr.scale_in_long.called
        assert ap.get("scale_in_refresh_pending") is True


@patch("bot.tr")
@patch("bot.st")
def test_scale_in_all_strategies_affected(mock_st, mock_tr, mock_bot):
    """
    Verify that all strategies using scale-in (S2, S4, S6, S7) share the same bug.
    """
    for strategy in ["S2", "S4", "S6", "S7"]:
        symbol = f"TEST{strategy}USDT"
        ap = {
            "side": "LONG" if strategy != "S4" else "SHORT",
            "strategy": strategy,
            "box_high": 100.0,
            "box_low": 95.0,
            "trade_id": f"test_{strategy}",
            "sl": 96.0,
            "scale_in_pending": True,
            "scale_in_after": time.time() - 1,
            "scale_in_trade_size_pct": 0.04,
            "qty": 100.0,
        }
        mock_bot.active_positions[symbol] = ap

        # Mock position API returns entry_price = 0 (API lag)
        mock_tr.get_mark_price.return_value = 100.0
        mock_tr.get_all_open_positions.return_value = {
            symbol: {"qty": 100.0, "margin": 10.0, "entry_price": 0.0}
        }
        mock_tr.scale_in_long.return_value = None
        mock_tr.scale_in_short.return_value = None
        mock_tr.refresh_plan_exits.return_value = True

        with patch("importlib.import_module") as mock_import:
            mock_strat = Mock()
            mock_strat.scale_in_specs.return_value = {
                "direction": "BULLISH" if strategy != "S4" else "BEARISH",
                "hold_side": "long" if strategy != "S4" else "short",
                "leverage": 10,
            }
            mock_strat.is_scale_in_window.return_value = True
            mock_import.return_value = mock_strat

            # Reset mocks for this iteration
            mock_tr.refresh_plan_exits.reset_mock()

            # Execute
            mock_bot._do_scale_in(symbol, ap)

            # The bug affects ALL strategies
            if mock_tr.refresh_plan_exits.called:
                call_args = mock_tr.refresh_plan_exits.call_args
                actual_new_trig = call_args[0][2]
                assert actual_new_trig > 0, \
                    f"{strategy} BUG: refresh_plan_exits called with new_trig=0.0"


# ── Seam / integration tests ──────────────────────────────────────── #
# These exercise the REAL trader.refresh_plan_exits / update_position_sl through
# bot._refresh_scale_in_exits, with only the EXCHANGE boundary faked. The prior
# tests mocked refresh_plan_exits, so they could verify the call but never the
# actual exit SIZES against the post-scale qty — which is exactly where the
# AMDUSDT bug (2026-06-03) lived. These close that gap.

def _amd_sym_info(symbol):
    """AMDUSDT-like: size_mult 0.01, 2-dp qty/price."""
    return {"price_place": 2, "volume_place": 2, "size_mult": 0.01, "min_trade_num": 0.01}


@patch("bot.st")
def test_seam_real_refresh_sizes_against_true_total_under_lag(mock_st, mock_bot, monkeypatch):
    """
    SEAM: with the REAL refresh_plan_exits and a position API that returns the
    stale pre-scale qty first and the true total only on a later read, the final
    placed profit_plan/moving_plan sizes must equal HALF OF THE TRUE TOTAL
    (0.18 → 0.09/0.09) — never half of the stale qty (0.09 → 0.04/0.04, the bug).
    """
    import trader
    import bitget_client as bc

    symbol = "AMDUSDT"
    ap = {
        "side": "LONG", "strategy": "S7", "sl": 508.0,
        "scale_in_refresh_pending": True,
        "scale_in_pre_qty": 0.09,
        "scale_in_refresh_deadline": time.time() + 300,
        "qty": 0.09, "trade_id": "seam1",
    }
    mock_bot.active_positions[symbol] = ap

    monkeypatch.setattr(trader, "_sym_info", _amd_sym_info)

    reads = {"n": 0}
    def fake_positions():
        reads["n"] += 1
        if reads["n"] == 1:  # exchange REST lag — fill not reflected yet
            return {symbol: {"side": "LONG", "qty": 0.09, "entry_price": 520.0}}
        return {symbol: {"side": "LONG", "qty": 0.18, "entry_price": 535.49, "margin": 9.6}}
    monkeypatch.setattr(trader, "get_all_open_positions", fake_positions)

    # Isolate sizing — Bug B (loss_plan) has its own seam test below.
    monkeypatch.setattr(trader, "update_position_sl", lambda *a, **k: True)

    def fake_get(path, params=None):
        if "orders-plan-pending" in path and (params or {}).get("planType") == "profit_loss":
            return {"data": {"entrustedList": [
                {"orderId": "PP", "planType": "profit_plan", "posSide": "long",
                 "triggerPrice": "590.27", "size": "0.04"},
                {"orderId": "MP", "planType": "moving_plan", "posSide": "long",
                 "triggerPrice": "590.27", "callbackRatio": "10", "size": "0.04"},
            ]}}
        return {"data": {"entrustedList": []}}
    monkeypatch.setattr(bc, "get", fake_get)

    posts = []
    monkeypatch.setattr(bc, "post", lambda path, payload: posts.append((path, payload)) or {})
    monkeypatch.setattr(time, "sleep", lambda s: None)

    with patch("importlib.import_module") as mock_import:
        m = Mock()
        m.scale_in_specs.return_value = {"direction": "BULLISH", "hold_side": "long", "leverage": 10}
        m.recompute_scale_in_sl_trigger.return_value = (508.0, 590.27)
        mock_import.return_value = m

        # Tick 1 — fill not reflected: must defer, place nothing.
        mock_bot._refresh_scale_in_exits(symbol, ap)
        assert [p for path, p in posts if "place-tpsl-order" in path] == [], \
            "must not size/place exits while the position qty is still stale"
        assert ap["scale_in_refresh_pending"] is True

        # Tick 2 — fill reflected: real refresh sizes against the TRUE total.
        mock_bot._refresh_scale_in_exits(symbol, ap)

    placed = {p["planType"]: p["size"] for path, p in posts if "place-tpsl-order" in path}
    assert placed.get("profit_plan") == "0.09", f"profit_plan sized wrong: {placed}"
    assert placed.get("moving_plan") == "0.09", f"moving_plan sized wrong: {placed}"
    assert ap["scale_in_refresh_pending"] is False


@patch("bot.st")
def test_seam_scale_in_refresh_cancels_stale_loss_plan(mock_st, mock_bot, monkeypatch):
    """
    SEAM: driving the REAL trader.update_position_sl through the scale-in refresh
    asserts the position-level pos_loss is placed AND the stale preset loss_plan
    is cancelled (Bitget Bug B) — verified at the place-order boundary.
    """
    import trader
    import bitget_client as bc

    symbol = "AMDUSDT"
    ap = {
        "side": "LONG", "strategy": "S7", "sl": 508.0,
        "scale_in_refresh_pending": True,
        "scale_in_pre_qty": 0.09,
        "scale_in_refresh_deadline": time.time() + 300,
        "qty": 0.09, "trade_id": "seam2",
    }
    mock_bot.active_positions[symbol] = ap

    monkeypatch.setattr(trader, "_sym_info", _amd_sym_info)
    monkeypatch.setattr(trader, "get_all_open_positions",
        lambda: {symbol: {"side": "LONG", "qty": 0.18, "entry_price": 535.49, "margin": 9.6}})
    # Isolate Bug B — sizing has its own seam test above.
    monkeypatch.setattr(trader, "refresh_plan_exits", lambda *a, **k: True)

    def fake_get(path, params=None):
        if "orders-plan-pending" in path and (params or {}).get("planType") == "profit_loss":
            return {"data": {"entrustedList": [
                {"orderId": "LP", "planType": "loss_plan",   "posSide": "long"},
                {"orderId": "PP", "planType": "profit_plan", "posSide": "long"},
            ]}}
        return {"data": {"entrustedList": []}}
    monkeypatch.setattr(bc, "get", fake_get)

    posts = []
    monkeypatch.setattr(bc, "post", lambda path, payload: posts.append((path, payload)) or {})
    monkeypatch.setattr(time, "sleep", lambda s: None)

    with patch("importlib.import_module") as mock_import:
        m = Mock()
        m.scale_in_specs.return_value = {"direction": "BULLISH", "hold_side": "long", "leverage": 10}
        m.recompute_scale_in_sl_trigger.return_value = (508.0, 590.27)
        mock_import.return_value = m

        mock_bot._refresh_scale_in_exits(symbol, ap)

    assert any("place-pos-tpsl" in path for path, _ in posts), "position SL (pos_loss) not placed"
    cancelled = {p.get("orderId") for path, p in posts if "cancel-plan-order" in path}
    assert "LP" in cancelled, "stale loss_plan not cancelled during scale-in refresh"
    assert "PP" not in cancelled, "profit_plan must not be cancelled by SL update"
