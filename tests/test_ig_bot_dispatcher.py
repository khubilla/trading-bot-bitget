"""Tests for the strategy adapter pattern in ig_bot."""
import ig_bot


def test_s5_adapter_exposes_required_methods():
    """_S5_ADAPTER must expose name, enabled_key, evaluate, handle_signal."""
    a = ig_bot._S5_ADAPTER
    assert a.name == "S5"
    assert a.enabled_key == "s5_enabled"
    assert callable(a.evaluate)
    assert callable(a.handle_signal)


def test_enabled_strategies_returns_s5_when_enabled():
    """_enabled_strategies returns _S5_ADAPTER when s5_enabled is True."""
    bot = ig_bot.IGBot.__new__(ig_bot.IGBot)
    adapters = bot._enabled_strategies({"s5_enabled": True})
    names = [a.name for a in adapters]
    assert "S5" in names


def test_enabled_strategies_skips_s5_when_disabled():
    """_enabled_strategies excludes _S5_ADAPTER when s5_enabled is False."""
    bot = ig_bot.IGBot.__new__(ig_bot.IGBot)
    adapters = bot._enabled_strategies({"s5_enabled": False})
    names = [a.name for a in adapters]
    assert "S5" not in names


def test_enabled_strategies_defaults_to_empty_when_keys_missing():
    """If no enable flags present in instrument dict, return [] (defensive default)."""
    bot = ig_bot.IGBot.__new__(ig_bot.IGBot)
    adapters = bot._enabled_strategies({})
    assert adapters == []


def test_adapter_for_returns_s5_by_name():
    """_adapter_for('S5') returns the S5 adapter."""
    bot = ig_bot.IGBot.__new__(ig_bot.IGBot)
    assert bot._adapter_for("S5") is ig_bot._S5_ADAPTER


def test_adapter_for_unknown_strategy_returns_s5_legacy_fallback():
    """Positions written before strategy tags exist (no 'strategy' field) should fall back to S5."""
    bot = ig_bot.IGBot.__new__(ig_bot.IGBot)
    # Unknown names fall back to S5 for legacy positions
    assert bot._adapter_for("LEGACY_UNTAGGED") is ig_bot._S5_ADAPTER


from unittest.mock import MagicMock, patch
import datetime as dt
import zoneinfo


def _make_bot():
    bot = ig_bot.IGBot.__new__(ig_bot.IGBot)
    bot._positions = {}
    bot._pending_orders = {}
    bot._candle_cache = {}
    bot._current_instrument = None
    bot.paper = True
    bot._scan_signals = {}
    bot._scan_log = []
    return bot


def _instrument(s5=True, **extra):
    base = {
        "epic": "TEST.EPIC", "display_name": "TEST",
        "s5_enabled": s5,
        "session_start": (0, 0), "session_end": (23, 59),
    }
    base.update(extra)
    return base


def test_dispatcher_calls_handle_signal_only_on_non_hold():
    """When evaluate returns HOLD, handle_signal is NOT called; update_scan_state IS."""
    bot = _make_bot()
    a = MagicMock(spec=ig_bot._StrategyAdapter)
    a.name, a.enabled_key = "S5", "s5_enabled"
    a.evaluate.return_value = {"signal": "HOLD", "reason": "x"}
    with patch.object(bot, "_enabled_strategies", return_value=[a]), \
         patch("ig_bot._in_trading_window", return_value=True), \
         patch.object(bot, "_save_state"):
        bot._tick_instrument(_instrument(), dt.datetime.now(zoneinfo.ZoneInfo("America/New_York")))
    a.evaluate.assert_called_once()
    a.update_scan_state.assert_called_once()
    a.handle_signal.assert_not_called()


def test_dispatcher_first_non_hold_wins():
    """When first adapter returns non-HOLD, second adapter never evaluates."""
    bot = _make_bot()
    a1 = MagicMock(spec=ig_bot._StrategyAdapter)
    a1.name, a1.enabled_key = "S5", "s5_enabled"
    a1.evaluate.return_value = {"signal": "PENDING_LONG", "reason": "x"}
    a2 = MagicMock(spec=ig_bot._StrategyAdapter)
    a2.name, a2.enabled_key = "S1", "s1_enabled"
    with patch.object(bot, "_enabled_strategies", return_value=[a1, a2]), \
         patch("ig_bot._in_trading_window", return_value=True), \
         patch.object(bot, "_save_state"):
        bot._tick_instrument(_instrument(), dt.datetime.now(zoneinfo.ZoneInfo("America/New_York")))
    a1.evaluate.assert_called_once()
    a1.handle_signal.assert_called_once()
    a2.evaluate.assert_not_called()
    a2.handle_signal.assert_not_called()


def test_dispatcher_continues_when_first_returns_hold():
    """When first adapter returns HOLD, second adapter IS evaluated."""
    bot = _make_bot()
    a1 = MagicMock(spec=ig_bot._StrategyAdapter)
    a1.name, a1.enabled_key = "S5", "s5_enabled"
    a1.evaluate.return_value = {"signal": "HOLD", "reason": "no setup"}
    a2 = MagicMock(spec=ig_bot._StrategyAdapter)
    a2.name, a2.enabled_key = "S1", "s1_enabled"
    a2.evaluate.return_value = {"signal": "HOLD", "reason": "no setup"}
    with patch.object(bot, "_enabled_strategies", return_value=[a1, a2]), \
         patch("ig_bot._in_trading_window", return_value=True), \
         patch.object(bot, "_save_state"):
        bot._tick_instrument(_instrument(), dt.datetime.now(zoneinfo.ZoneInfo("America/New_York")))
    a1.evaluate.assert_called_once()
    a2.evaluate.assert_called_once()
    a1.handle_signal.assert_not_called()
    a2.handle_signal.assert_not_called()


def test_monitor_dispatches_via_pos_strategy_tag():
    """When pos has strategy='S5', _S5_ADAPTER.monitor_position is called via the adapter."""
    bot = _make_bot()
    bot._positions["TEST"] = {"strategy": "S5", "side": "LONG", "deal_id": "x"}
    # Patch _monitor_position so we can verify it's called via the adapter delegation
    with patch.object(bot, "_monitor_position") as mock_mon, \
         patch("ig_bot._in_trading_window", return_value=True), \
         patch.object(bot, "_save_state"):
        bot._tick_instrument(_instrument(), dt.datetime.now(zoneinfo.ZoneInfo("America/New_York")))
    mock_mon.assert_called_once()


def test_monitor_dispatches_legacy_untagged_position_to_s5():
    """A position without 'strategy' field falls back to S5 monitor dispatch."""
    bot = _make_bot()
    bot._positions["TEST"] = {"side": "LONG", "deal_id": "x"}    # no strategy tag
    with patch.object(bot, "_monitor_position") as mock_mon, \
         patch("ig_bot._in_trading_window", return_value=True), \
         patch.object(bot, "_save_state"):
        bot._tick_instrument(_instrument(), dt.datetime.now(zoneinfo.ZoneInfo("America/New_York")))
    mock_mon.assert_called_once()


def test_s1_adapter_exists_and_named():
    a = ig_bot._S1_ADAPTER
    assert a.name == "S1"
    assert a.enabled_key == "s1_enabled"
    for m in ("evaluate", "handle_signal", "monitor_position", "update_scan_state"):
        assert callable(getattr(a, m))


def test_enabled_strategies_includes_s1_when_flag_true():
    bot = ig_bot.IGBot.__new__(ig_bot.IGBot)
    names = [a.name for a in bot._enabled_strategies({"s5_enabled": False, "s1_enabled": True})]
    assert names == ["S1"]


def test_enabled_strategies_returns_both_when_both_true():
    bot = ig_bot.IGBot.__new__(ig_bot.IGBot)
    names = [a.name for a in bot._enabled_strategies({"s5_enabled": True, "s1_enabled": True})]
    assert names == ["S5", "S1"]   # S5 first per CONFIG dispatch order


def test_adapter_for_s1_returns_s1_adapter():
    bot = ig_bot.IGBot.__new__(ig_bot.IGBot)
    assert bot._adapter_for("S1") is ig_bot._S1_ADAPTER


def test_s1_evaluate_returns_hold_on_empty_daily():
    """When daily df is empty, S1 returns HOLD without trying 3m fetch."""
    bot = ig_bot.IGBot.__new__(ig_bot.IGBot)
    bot._candle_cache = {}
    bot._current_instrument = None
    instrument = {
        "epic": "TEST.EPIC", "display_name": "TEST",
        "s1_enabled": True, "s1_daily_ema_slow": 20,
        "daily_limit": 100, "htf_limit": 50, "m3_limit": 30,
    }
    import pandas as pd
    from unittest.mock import patch
    with patch.object(bot, "_get_candles", return_value=pd.DataFrame()):
        result = ig_bot._S1_ADAPTER.evaluate(bot, instrument)
    assert result["signal"] == "HOLD"
    assert "candle fetch empty" in result["reason"]


def test_update_scan_state_s1_writes_under_s1_key():
    bot = ig_bot.IGBot.__new__(ig_bot.IGBot)
    bot._scan_signals = {}; bot._scan_log = []
    result = {"signal": "HOLD", "reason": "no setup", "rsi": 55.0, "adx": 22.0,
              "box_high": 0.0, "box_low": 0.0, "atr": 1.2}
    bot._update_scan_state_s1("US100", result)
    assert "S1" in bot._scan_signals["US100"]
    assert bot._scan_signals["US100"]["S1"]["signal"] == "HOLD"
    assert bot._scan_signals["US100"]["S1"]["rsi"] == 55.0
