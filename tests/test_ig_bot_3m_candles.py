"""Tests that ig_bot._get_candles supports 3m interval (needed for S1)."""
from unittest.mock import patch
import pandas as pd
import ig_bot


def test_3m_interval_in_map():
    """_get_candles must accept '3m' and pass it through to ig.get_candles."""
    bot = ig_bot.IGBot.__new__(ig_bot.IGBot)
    bot._candle_cache = {}
    bot._current_instrument = {"epic": "TEST.EPIC"}
    with patch("ig_client.get_candles", return_value=pd.DataFrame({
        "ts":   [1_000_000_000_000],
        "open": [100.0], "high": [101.0], "low": [99.0], "close": [100.5],
    })) as mock_get:
        df = bot._get_candles("3m", limit=30)
    assert not df.empty
    mock_get.assert_called_once_with("TEST.EPIC", "3m", 30)


def test_3m_uses_180000_ms_window():
    """The interval_ms lookup for 3m is 180_000 (3 minutes), not the 60_000 fallback."""
    bot = ig_bot.IGBot.__new__(ig_bot.IGBot)
    bot._candle_cache = {("TEST.EPIC", "3m"): pd.DataFrame({
        "ts":   [int(__import__("time").time() * 1000) - 60_000],  # 60s ago — within a 3m window
        "open": [100.0], "high": [101.0], "low": [99.0], "close": [100.5],
    })}
    bot._current_instrument = {"epic": "TEST.EPIC"}
    with patch("ig_client.get_candles") as mock_get:
        df = bot._get_candles("3m", limit=30)
    # Cached candle is 60s old; 3m window is 180s. Should return cache without fetching.
    mock_get.assert_not_called()
    assert not df.empty
