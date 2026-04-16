"""
indicators.py — Pure numeric indicators (shared tool functions).

These functions contain no strategy rules and no config dependencies.
Every strategy file imports what it needs from here.
"""

import numpy as np
import pandas as pd


def calculate_ema(closes: pd.Series, period: int) -> pd.Series:
    return closes.ewm(span=period, adjust=False).mean()


def calculate_rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    delta    = closes.diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calculate_adx(df: pd.DataFrame, period: int = 14) -> dict:
    """
    Calculates ADX, +DI, -DI.
    Returns dict with keys: adx, plus_di, minus_di (all pd.Series)
    ADX > 25 = trending, < 20 = sideways.
    """
    high  = df["high"].astype(float)
    low   = df["low"].astype(float)
    close = df["close"].astype(float)

    prev_high  = high.shift(1)
    prev_low   = low.shift(1)
    prev_close = close.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    up_move   = high - prev_high
    down_move = prev_low - low

    plus_dm  = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=df.index
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=df.index
    )

    atr_smooth = tr.ewm(span=period, adjust=False).mean()
    plus_di  = 100 * plus_dm.ewm(span=period, adjust=False).mean()  / atr_smooth.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(span=period, adjust=False).mean() / atr_smooth.replace(0, np.nan)

    dx  = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(span=period, adjust=False).mean()

    return {"adx": adx, "plus_di": plus_di, "minus_di": minus_di}


def calculate_stoch(
    df: pd.DataFrame,
    k_period: int = 5,
    d_smooth: int = 3,
) -> tuple[pd.Series, pd.Series]:
    """
    Slow Stochastics.
      Fast %K  = (close − lowest_low_k) / (highest_high_k − lowest_low_k) × 100
      Slow %K  = SMA(d_smooth) of Fast %K
      Slow %D  = SMA(d_smooth) of Slow %K
    Returns (slow_k, slow_d) with same index as df.
    """
    high  = df["high"].astype(float)
    low   = df["low"].astype(float)
    close = df["close"].astype(float)

    lowest_low   = low.rolling(window=k_period).min()
    highest_high = high.rolling(window=k_period).max()
    denom        = (highest_high - lowest_low).replace(0, np.nan)
    fast_k       = 100.0 * (close - lowest_low) / denom

    slow_k = fast_k.rolling(window=d_smooth).mean()
    slow_d = slow_k.rolling(window=d_smooth).mean()
    return slow_k, slow_d


def calculate_macd(
    closes: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (macd_line, signal_line, histogram)."""
    ema_fast    = closes.ewm(span=fast,   adjust=False).mean()
    ema_slow    = closes.ewm(span=slow,   adjust=False).mean()
    macd_line   = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line, macd_line - signal_line
