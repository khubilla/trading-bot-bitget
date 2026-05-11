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
def test_scale_in_skips_refresh_when_api_lags(mock_logger, mock_st, mock_tr, mock_bot, mock_s2_position):
    """
    BUGFIX VERIFICATION: refresh_plan_exits is NOT called when API lags.

    After the fix, refresh_plan_exits() is moved inside the 'if new_avg > 0:' block,
    so when the position API returns entry_price=0 (due to API lag),
    refresh_plan_exits is NOT called, preventing fallback to the original TP trigger.

    The bot logs a warning and will retry on the next tick.
    """
    # Setup
    symbol = "HUSDT"
    mock_bot.active_positions[symbol] = mock_s2_position

    # Mock trader: position API returns stale data (no new_avg yet)
    # This simulates API lag - a common real-world scenario
    mock_tr.get_mark_price.return_value = 0.1915
    mock_tr.get_all_open_positions.return_value = {
        symbol: {
            "qty": 163.0,  # Still old qty (scale-in not reflected)
            "margin": 3.1325,
            "entry_price": 0,  # API hasn't updated yet
        }
    }
    mock_tr.scale_in_long.return_value = None
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
        # This would NOT be called because new_avg=0
        mock_s2.recompute_scale_in_sl_trigger.return_value = (0.18192, 0.21063)
        mock_import.return_value = mock_s2

        # Execute scale-in
        mock_bot._do_scale_in(symbol, mock_s2_position)

        # AFTER FIX: refresh_plan_exits should NOT be called when new_avg=0
        assert not mock_tr.refresh_plan_exits.called, \
            "BUGFIX VERIFIED: refresh_plan_exits NOT called when entry_price=0"

        # Verify warning was logged
        warning_calls = [call for call in mock_logger.warning.call_args_list
                        if "entry_price not available" in str(call)]
        assert len(warning_calls) > 0, "Expected warning about entry_price not available"


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
