"""Tests for calculate_atr in indicators.py."""
import pandas as pd
import pytest
from indicators import calculate_atr


def _df(highs, lows, closes):
    return pd.DataFrame({"high": highs, "low": lows, "close": closes})


def test_atr_basic_shape():
    """ATR returns a pd.Series of same length as input."""
    df = _df([10, 11, 12, 13, 14], [9, 10, 11, 12, 13], [9.5, 10.5, 11.5, 12.5, 13.5])
    atr = calculate_atr(df, period=3)
    assert isinstance(atr, pd.Series)
    assert len(atr) == 5


def test_atr_positive_when_data_varies():
    """ATR > 0 on a normal trending series."""
    df = _df(
        highs=[10, 12, 14, 13, 15, 16, 14, 17, 18, 19, 20, 21, 22, 23, 24],
        lows= [9,  10, 11, 11, 13, 14, 12, 15, 16, 17, 18, 19, 20, 21, 22],
        closes=[9.5, 11, 13, 12, 14, 15, 13, 16, 17, 18, 19, 20, 21, 22, 23],
    )
    atr = calculate_atr(df, period=14)
    assert atr.iloc[-1] > 0


def test_atr_zero_on_flat_market():
    """ATR collapses to 0 when high==low==close on every bar."""
    df = _df([100]*15, [100]*15, [100]*15)
    atr = calculate_atr(df, period=14)
    assert atr.iloc[-1] == 0


def test_atr_period_argument_changes_result():
    """Different periods yield different ATR values on the same data."""
    df = _df(
        highs=[10, 12, 14, 13, 15, 16, 14, 17, 18, 19, 20, 21, 22, 23, 24],
        lows= [9,  10, 11, 11, 13, 14, 12, 15, 16, 17, 18, 19, 20, 21, 22],
        closes=[9.5, 11, 13, 12, 14, 15, 13, 16, 17, 18, 19, 20, 21, 22, 23],
    )
    atr_short = calculate_atr(df, period=3)
    atr_long  = calculate_atr(df, period=14)
    assert atr_short.iloc[-1] != atr_long.iloc[-1]
