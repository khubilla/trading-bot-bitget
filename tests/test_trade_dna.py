import pandas as pd
import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Helpers are module-private but we test them via their public names below.
# We'll import the module once it exists.
# ---------------------------------------------------------------------------

def _make_closes(values: list[float]) -> pd.Series:
    return pd.Series(values, dtype=float)


class TestEmaSlopeHelper:
    """Tests for ema_slope()"""

    def test_rising(self):
        from trade_dna import ema_slope
        # 30 candles steadily climbing — EMA[-1] clearly above EMA[-10]
        closes = _make_closes([float(i) for i in range(1, 31)])
        assert ema_slope(closes, period=10) == "rising"

    def test_falling(self):
        from trade_dna import ema_slope
        closes = _make_closes([float(i) for i in range(30, 0, -1)])
        assert ema_slope(closes, period=10) == "falling"

    def test_flat(self):
        from trade_dna import ema_slope
        closes = _make_closes([100.0] * 30)
        assert ema_slope(closes, period=10) == "flat"

    def test_too_short_returns_empty(self):
        from trade_dna import ema_slope
        closes = _make_closes([1.0, 2.0, 3.0])   # shorter than period+n
        assert ema_slope(closes, period=10) == ""


class TestPriceVsEma:
    def test_above(self):
        from trade_dna import price_vs_ema
        assert price_vs_ema(105.0, 100.0) == "above"

    def test_below(self):
        from trade_dna import price_vs_ema
        assert price_vs_ema(95.0, 100.0) == "below"

    def test_equal_is_below(self):
        from trade_dna import price_vs_ema
        assert price_vs_ema(100.0, 100.0) == "below"


class TestRsiBucket:
    def test_buckets(self):
        from trade_dna import rsi_bucket
        assert rsi_bucket(45.0) == "<50"
        assert rsi_bucket(50.0) == "50-60"
        assert rsi_bucket(59.9) == "50-60"
        assert rsi_bucket(60.0) == "60-65"
        assert rsi_bucket(65.0) == "65-70"
        assert rsi_bucket(70.0) == "70-75"
        assert rsi_bucket(75.0) == "75-80"
        assert rsi_bucket(80.0) == ">80"
        assert rsi_bucket(95.0) == ">80"


class TestAdxState:
    def test_rising(self):
        from trade_dna import adx_state
        # ADX climbing from 20 to 30 over 10 candles
        series = _make_closes([20.0 + i for i in range(11)])
        assert adx_state(series) == "rising"

    def test_falling(self):
        from trade_dna import adx_state
        series = _make_closes([30.0 - i for i in range(11)])
        assert adx_state(series) == "falling"

    def test_flat(self):
        from trade_dna import adx_state
        series = _make_closes([25.0] * 11)
        assert adx_state(series) == "flat"

    def test_too_short_returns_empty(self):
        from trade_dna import adx_state
        series = _make_closes([25.0, 26.0])
        assert adx_state(series) == ""


class TestSnapshot:
    """snapshot() returns correct keys per strategy and handles errors gracefully."""

    def _daily_df(self, n: int = 40) -> pd.DataFrame:
        """Minimal OHLCV DataFrame with rising closes."""
        closes = [100.0 + i * 0.5 for i in range(n)]
        return pd.DataFrame({
            "open":  [c - 0.1 for c in closes],
            "high":  [c + 0.5 for c in closes],
            "low":   [c - 0.5 for c in closes],
            "close": closes,
            "vol":   [1000.0] * n,
        })

    def _closes(self, n: int = 40, start: float = 100.0, step: float = 0.5) -> pd.Series:
        return pd.Series([start + i * step for i in range(n)], dtype=float)

    # ── S2 ──────────────────────────────────────────────────────────────── #
    def test_s2_returns_expected_keys(self):
        from trade_dna import snapshot
        result = snapshot("S2", "BTCUSDT", {"daily": self._daily_df()})
        assert "snap_trend_daily_ema_slope" in result
        assert "snap_trend_daily_price_vs_ema" in result
        assert "snap_trend_daily_rsi_bucket" in result
        # S2 does not use h1/m15/m3
        assert "snap_trend_h1_ema_slope" not in result
        assert "snap_trend_m15_ema_slope" not in result

    def test_s2_values_are_valid_strings(self):
        from trade_dna import snapshot
        result = snapshot("S2", "BTCUSDT", {"daily": self._daily_df()})
        assert result["snap_trend_daily_ema_slope"] in ("rising", "falling", "flat", "")
        assert result["snap_trend_daily_price_vs_ema"] in ("above", "below", "")
        assert result["snap_trend_daily_rsi_bucket"] in (
            "<50", "50-60", "60-65", "65-70", "70-75", "75-80", ">80", ""
        )

    # ── S3 ──────────────────────────────────────────────────────────────── #
    def test_s3_returns_expected_keys(self):
        from trade_dna import snapshot
        result = snapshot("S3", "ETHUSDT", {"m15": self._daily_df()})
        assert "snap_trend_m15_ema_slope" in result
        assert "snap_trend_m15_price_vs_ema" in result
        assert "snap_trend_m15_adx_state" in result
        assert "snap_trend_daily_ema_slope" not in result

    # ── S1 ──────────────────────────────────────────────────────────────── #
    def test_s1_returns_expected_keys(self):
        from trade_dna import snapshot
        result = snapshot("S1", "BTCUSDT", {
            "daily": self._daily_df(),
            "h1": self._daily_df(),
            "m3": self._daily_df(),
        })
        for key in [
            "snap_trend_daily_ema_slope", "snap_trend_daily_price_vs_ema",
            "snap_trend_daily_adx_state",
            "snap_trend_h1_ema_slope", "snap_trend_h1_price_vs_ema",
            "snap_trend_m3_price_vs_ema",
        ]:
            assert key in result

    # ── Error path ──────────────────────────────────────────────────────── #
    def test_empty_candles_returns_empty_dict(self):
        from trade_dna import snapshot
        result = snapshot("S2", "BTCUSDT", {"daily": pd.DataFrame()})
        assert result == {}

    def test_missing_candles_key_returns_empty_dict(self):
        from trade_dna import snapshot
        result = snapshot("S2", "BTCUSDT", {})
        assert result == {}

    def test_unknown_strategy_returns_empty_dict(self):
        from trade_dna import snapshot
        result = snapshot("S99", "BTCUSDT", {"daily": self._daily_df()})
        assert result == {}
