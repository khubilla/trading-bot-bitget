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
