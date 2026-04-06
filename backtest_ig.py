"""
backtest_ig.py — Walk-forward backtest for IG S5 strategy.

Data source: yfinance (^DJI for US30, GC=F for GOLD)
Cache: data/ig_cache/<NAME>_<INTERVAL>.parquet

Usage:
    python backtest_ig.py                    # fetch + run all instruments
    python backtest_ig.py --no-fetch         # use cached parquet only
    python backtest_ig.py --instrument US30  # single instrument
    python backtest_ig.py --output my.html
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytz
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent))

from strategy import evaluate_s5, calculate_ema
from config_ig import INSTRUMENTS

# ── Constants ──────────────────────────────────────────────────────── #

_CACHE_DIR  = Path("data/ig_cache")
_ET         = pytz.timezone("America/New_York")
_YF_SYMBOLS = {
    "US30": "^DJI",
    "GOLD": "GC=F",
}
_YF_PERIODS   = {"1D": "10y",  "1H": "2y",  "15m": "60d"}
_YF_INTERVALS = {"1D": "1d",   "1H": "1h",  "15m": "15m"}


# ── Data fetch ─────────────────────────────────────────────────────── #

def _cache_path(name: str, interval: str) -> Path:
    return _CACHE_DIR / f"{name}_{interval}.parquet"


def _fetch_yf(name: str, interval: str) -> pd.DataFrame:
    import yfinance as yf
    yf_sym = _YF_SYMBOLS[name]
    ticker = yf.Ticker(yf_sym)
    raw = ticker.history(
        period=_YF_PERIODS[interval],
        interval=_YF_INTERVALS[interval],
    )
    if raw is None or raw.empty:
        return pd.DataFrame()
    raw = raw.reset_index()
    ts_col = "Datetime" if "Datetime" in raw.columns else "Date"
    raw["ts"] = pd.to_datetime(raw[ts_col], utc=True).dt.as_unit("ms").astype("int64")
    raw = raw.rename(columns={"Open": "open", "High": "high",
                               "Low": "low", "Close": "close", "Volume": "vol"})
    df = raw[["ts", "open", "high", "low", "close", "vol"]].copy()
    df = df.dropna().sort_values("ts").reset_index(drop=True)
    return df


def load_candles(name: str, interval: str, no_fetch: bool = False) -> pd.DataFrame:
    """Load candles from parquet cache or fetch from yfinance."""
    path = _cache_path(name, interval)
    if no_fetch:
        if path.exists():
            return pd.read_parquet(path)
        raise FileNotFoundError(
            f"No cache at {path}. Run without --no-fetch first."
        )
    df = _fetch_yf(name, interval)
    if not df.empty:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, index=False)
    return df


# ── Session helpers ────────────────────────────────────────────────── #

def _bar_et(ts_ms: int) -> datetime:
    """Convert Unix ms timestamp to ET-aware datetime."""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).astimezone(_ET)


def _in_session(ts_ms: int, instrument: dict) -> bool:
    """True if this bar's timestamp falls within the instrument's trading window."""
    now = _bar_et(ts_ms)
    if now.weekday() >= 5:          # Saturday=5, Sunday=6
        return False
    sh, sm = instrument["session_start"]
    eh, em = instrument["session_end"]
    start = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end   = now.replace(hour=eh, minute=em, second=0, microsecond=0)
    return start <= now < end


def _is_session_end(ts_ms: int, instrument: dict) -> bool:
    """True if this bar's timestamp is at or past the session_end hour:minute."""
    now = _bar_et(ts_ms)
    if now.weekday() >= 5:
        return False
    eh, em = instrument["session_end"]
    return now.hour > eh or (now.hour == eh and now.minute >= em)
