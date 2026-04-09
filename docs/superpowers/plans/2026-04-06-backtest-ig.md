# IG S5 Backtest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `backtest_ig.py` — a walk-forward S5 backtest for IG instruments (US30, GOLD) using yfinance data cached to parquet, with a self-contained HTML report including inline candlestick charts for each completed trade.

**Architecture:** Single new file `backtest_ig.py`. No existing files modified. Imports `evaluate_s5` + `calculate_ema` from `strategy.py` and instrument configs from `config_ig.INSTRUMENTS` (read-only). Data fetched via `yfinance` (`^DJI` for US30, `GC=F` for GOLD), cached to `data/ig_cache/*.parquet`. Simulation is a 15m bar-by-bar state machine (IDLE → PENDING → IN_TRADE) matching live `ig_bot.py` OB-invalidation and 4-hour expiry logic exactly.

**Tech Stack:** Python 3.11+, yfinance, pandas, pyarrow (parquet), pytz, pytest

**Spec:** `docs/superpowers/specs/2026-04-06-backtest-ig-design.md`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `backtest_ig.py` | Create | All backtest logic: fetch, simulate, report |
| `tests/test_backtest_ig.py` | Create | Unit tests for all simulation logic |
| `data/ig_cache/` | Create (dir) | Parquet cache for yfinance candles |

---

## Task 1: Data fetch layer + parquet cache

**Files:**
- Create: `backtest_ig.py` (scaffold + fetch layer only)
- Create: `tests/test_backtest_ig.py`

- [ ] **Step 1: Install yfinance**

```bash
source venv/bin/activate && pip install yfinance pyarrow
```

Expected: `Successfully installed yfinance-...`

- [ ] **Step 2: Write failing tests for the fetch layer**

Create `tests/test_backtest_ig.py`:

```python
"""Tests for backtest_ig.py — fetch layer, session helpers, simulation."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
import types
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest


# ── Helpers ────────────────────────────────────────────────────────── #

def _make_df(rows):
    """Build a minimal OHLCV DataFrame with ts in ms."""
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "vol"])


def _dummy_instrument():
    """Minimal instrument config for testing."""
    return {
        "display_name":               "US30",
        "epic":                       "IX.D.DOW.IFD.IP",
        "session_start":              (0, 0),
        "session_end":                (23, 59),
        "daily_limit":                200,
        "htf_limit":                  50,
        "m15_limit":                  300,
        "s5_daily_ema_fast":          10,
        "s5_daily_ema_med":           20,
        "s5_daily_ema_slow":          50,
        "s5_htf_bos_lookback":        20,
        "s5_ltf_interval":            "15m",
        "s5_ob_lookback":             20,
        "s5_ob_min_impulse":          0.003,
        "s5_ob_min_range_pct":        0.001,
        "s5_choch_lookback":          10,
        "s5_max_entry_buffer":        0.003,
        "s5_sl_buffer_pct":           0.001,
        "s5_ob_invalidation_buffer_pct": 0.001,
        "s5_swing_lookback":          10,
        "s5_smc_fvg_filter":          False,
        "s5_smc_fvg_lookback":        5,
        "s5_leverage":                5,
        "s5_trade_size_pct":          0.02,
        "s5_min_rr":                  2.0,
        "s5_trail_range_pct":         0.02,
        "s5_use_candle_stops":        False,
        "s5_min_sr_clearance":        0.0,
        "s5_enabled":                 True,
        "contract_size":              1.0,
        "partial_size":               0.5,
        "point_value":                1.0,
        "currency":                   "USD",
    }


# ── Fetch layer tests ──────────────────────────────────────────────── #

def test_load_candles_fetches_and_writes_parquet(tmp_path, monkeypatch):
    """load_candles() fetches via yfinance and saves parquet on first call."""
    import backtest_ig as bt
    monkeypatch.setattr(bt, "_CACHE_DIR", tmp_path)

    sample = _make_df([[1_700_000_000_000, 40000, 40100, 39900, 40050, 1000]])
    monkeypatch.setattr(bt, "_fetch_yf", lambda name, interval: sample.copy())

    result = bt.load_candles("US30", "15m", no_fetch=False)

    parquet = tmp_path / "US30_15m.parquet"
    assert parquet.exists(), "Parquet not written"
    assert list(result.columns) == ["ts", "open", "high", "low", "close", "vol"]
    assert len(result) == 1


def test_load_candles_reads_cache_without_fetching(tmp_path, monkeypatch):
    """load_candles(no_fetch=True) reads parquet without calling _fetch_yf."""
    import backtest_ig as bt
    monkeypatch.setattr(bt, "_CACHE_DIR", tmp_path)

    sample = _make_df([[1_700_000_000_000, 40000, 40100, 39900, 40050, 1000]])
    sample.to_parquet(tmp_path / "US30_15m.parquet", index=False)

    fetch_called = []
    monkeypatch.setattr(bt, "_fetch_yf", lambda *a: fetch_called.append(1) or pd.DataFrame())

    result = bt.load_candles("US30", "15m", no_fetch=True)
    assert len(fetch_called) == 0, "_fetch_yf should not be called with no_fetch=True"
    assert len(result) == 1


def test_load_candles_no_fetch_missing_cache_raises(tmp_path, monkeypatch):
    """load_candles(no_fetch=True) raises FileNotFoundError when cache missing."""
    import backtest_ig as bt
    monkeypatch.setattr(bt, "_CACHE_DIR", tmp_path)
    with pytest.raises(FileNotFoundError):
        bt.load_candles("US30", "15m", no_fetch=True)
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
source venv/bin/activate && pytest tests/test_backtest_ig.py::test_load_candles_fetches_and_writes_parquet tests/test_backtest_ig.py::test_load_candles_reads_cache_without_fetching tests/test_backtest_ig.py::test_load_candles_no_fetch_missing_cache_raises -v 2>&1 | tail -10
```

Expected: `ModuleNotFoundError: No module named 'backtest_ig'`

- [ ] **Step 4: Create `backtest_ig.py` with scaffold + fetch layer**

```python
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
    raw["ts"] = raw[ts_col].apply(lambda x: int(x.timestamp() * 1000))
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
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
source venv/bin/activate && pytest tests/test_backtest_ig.py::test_load_candles_fetches_and_writes_parquet tests/test_backtest_ig.py::test_load_candles_reads_cache_without_fetching tests/test_backtest_ig.py::test_load_candles_no_fetch_missing_cache_raises -v 2>&1 | tail -10
```

Expected: `3 passed`

- [ ] **Step 6: Commit**

```bash
git add backtest_ig.py tests/test_backtest_ig.py
git commit -m "feat(backtest-ig): scaffold + yfinance parquet cache layer"
```

---

## Task 2: Session window helpers

**Files:**
- Modify: `backtest_ig.py` (add `_bar_et`, `_in_session`, `_is_session_end`)
- Modify: `tests/test_backtest_ig.py` (add session tests)

- [ ] **Step 1: Add failing session tests**

Append to `tests/test_backtest_ig.py`:

```python
# ── Session helper tests ───────────────────────────────────────────── #

def _ts_et(year, month, day, hour, minute, tz_str="America/New_York"):
    """Return Unix ms for a given ET wall-clock time."""
    import pytz as _pytz
    et = _pytz.timezone(tz_str)
    dt = et.localize(datetime(year, month, day, hour, minute))
    return int(dt.timestamp() * 1000)


def test_in_session_weekday_within_window():
    import backtest_ig as bt
    inst = _dummy_instrument()
    inst["session_start"] = (9, 30)
    inst["session_end"]   = (16, 0)
    ts = _ts_et(2026, 3, 10, 10, 0)  # Monday 10:00 ET
    assert bt._in_session(ts, inst) is True


def test_not_in_session_before_open():
    import backtest_ig as bt
    inst = _dummy_instrument()
    inst["session_start"] = (9, 30)
    inst["session_end"]   = (16, 0)
    ts = _ts_et(2026, 3, 10, 8, 0)  # Monday 08:00 ET — before open
    assert bt._in_session(ts, inst) is False


def test_not_in_session_weekend():
    import backtest_ig as bt
    inst = _dummy_instrument()
    inst["session_start"] = (9, 30)
    inst["session_end"]   = (16, 0)
    ts = _ts_et(2026, 3, 7, 10, 0)  # Saturday
    assert bt._in_session(ts, inst) is False


def test_is_session_end_at_boundary():
    import backtest_ig as bt
    inst = _dummy_instrument()
    inst["session_end"] = (23, 59)
    ts = _ts_et(2026, 3, 10, 23, 59)
    assert bt._is_session_end(ts, inst) is True


def test_not_session_end_before_boundary():
    import backtest_ig as bt
    inst = _dummy_instrument()
    inst["session_end"] = (23, 59)
    ts = _ts_et(2026, 3, 10, 10, 0)
    assert bt._is_session_end(ts, inst) is False
```

- [ ] **Step 2: Run to verify they fail**

```bash
source venv/bin/activate && pytest tests/test_backtest_ig.py -k "session" -v 2>&1 | tail -10
```

Expected: `AttributeError: module 'backtest_ig' has no attribute '_in_session'`

- [ ] **Step 3: Add session helpers to `backtest_ig.py`**

Add after the `load_candles` function:

```python
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
    return now.hour == eh and now.minute >= em
```

- [ ] **Step 4: Run session tests**

```bash
source venv/bin/activate && pytest tests/test_backtest_ig.py -k "session" -v 2>&1 | tail -10
```

Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add backtest_ig.py tests/test_backtest_ig.py
git commit -m "feat(backtest-ig): session window helpers (ET timezone)"
```

---

## Task 3: PENDING state transitions

**Files:**
- Modify: `backtest_ig.py` (add `_check_pending`)
- Modify: `tests/test_backtest_ig.py`

- [ ] **Step 1: Add failing PENDING tests**

Append to `tests/test_backtest_ig.py`:

```python
# ── PENDING state tests ────────────────────────────────────────────── #

def _make_pending(side="LONG", trigger=40500.0, ob_low=40000.0, ob_high=40500.0,
                  sl=39500.0, tp=42000.0, expires_offset_ms=4*3600*1000,
                  base_ts=1_700_000_000_000):
    return {
        "side":    side,
        "trigger": trigger,
        "sl":      sl,
        "tp":      tp,
        "ob_low":  ob_low,
        "ob_high": ob_high,
        "expires": base_ts + expires_offset_ms,
    }


def _make_bar(ts=1_700_000_000_000, lo=40200.0, hi=40600.0, op=40300.0, cl=40400.0):
    return {"ts": ts, "open": op, "high": hi, "low": lo, "close": cl, "vol": 100}


def test_pending_fill_long():
    import backtest_ig as bt
    inst    = _dummy_instrument()
    pending = _make_pending(side="LONG", trigger=40500.0)
    bar     = _make_bar(ts=pending["expires"] - 1000, lo=40490.0, hi=40600.0)  # low <= trigger
    action, price = bt._check_pending(bar, pending, inst)
    assert action == "fill"
    assert price == 40500.0


def test_pending_no_fill_long_bar_above_trigger():
    import backtest_ig as bt
    inst    = _dummy_instrument()
    pending = _make_pending(side="LONG", trigger=40500.0)
    bar     = _make_bar(ts=pending["expires"] - 1000, lo=40510.0, hi=40600.0)  # low > trigger
    action, _ = bt._check_pending(bar, pending, inst)
    assert action == "hold"


def test_pending_fill_short():
    import backtest_ig as bt
    inst    = _dummy_instrument()
    pending = _make_pending(side="SHORT", trigger=40000.0)
    bar     = _make_bar(ts=pending["expires"] - 1000, lo=39900.0, hi=40010.0)  # high >= trigger
    action, price = bt._check_pending(bar, pending, inst)
    assert action == "fill"
    assert price == 40000.0


def test_pending_ob_invalid_long():
    import backtest_ig as bt
    inst    = _dummy_instrument()
    inst["s5_ob_invalidation_buffer_pct"] = 0.001
    pending = _make_pending(side="LONG", ob_low=40000.0)
    # bar low < ob_low * (1 - 0.001) = 39960
    bar = _make_bar(ts=pending["expires"] - 1000, lo=39950.0, hi=40200.0)
    action, _ = bt._check_pending(bar, pending, inst)
    assert action == "ob_invalid"


def test_pending_ob_invalid_short():
    import backtest_ig as bt
    inst    = _dummy_instrument()
    inst["s5_ob_invalidation_buffer_pct"] = 0.001
    pending = _make_pending(side="SHORT", ob_high=40500.0)
    # bar high > ob_high * (1 + 0.001) = 40540.5
    bar = _make_bar(ts=pending["expires"] - 1000, lo=40400.0, hi=40550.0)
    action, _ = bt._check_pending(bar, pending, inst)
    assert action == "ob_invalid"


def test_pending_expired():
    import backtest_ig as bt
    inst    = _dummy_instrument()
    pending = _make_pending(base_ts=1_700_000_000_000, expires_offset_ms=0)
    # bar ts > expires
    bar = _make_bar(ts=pending["expires"] + 1, lo=40200.0, hi=40400.0)
    action, _ = bt._check_pending(bar, pending, inst)
    assert action == "expired"


def test_pending_session_end_cancels(monkeypatch):
    import backtest_ig as bt
    inst    = _dummy_instrument()
    pending = _make_pending()
    bar     = _make_bar(ts=pending["expires"] - 1000, lo=40200.0, hi=40400.0)
    monkeypatch.setattr(bt, "_is_session_end", lambda ts, i: True)
    action, _ = bt._check_pending(bar, pending, inst)
    assert action == "session_end"
```

- [ ] **Step 2: Run to verify they fail**

```bash
source venv/bin/activate && pytest tests/test_backtest_ig.py -k "pending" -v 2>&1 | tail -10
```

Expected: `AttributeError: module 'backtest_ig' has no attribute '_check_pending'`

- [ ] **Step 3: Add `_check_pending` to `backtest_ig.py`**

Add after the session helpers:

```python
# ── Simulation: PENDING state ──────────────────────────────────────── #

def _check_pending(bar: dict, pending: dict, instrument: dict) -> tuple[str, float]:
    """
    Evaluate one 15m bar against a pending S5 signal.

    Returns (action, fill_price):
      action: "fill" | "ob_invalid" | "expired" | "session_end" | "hold"
      fill_price: trigger price if action=="fill", else 0.0
    """
    ts   = int(bar["ts"])
    lo   = float(bar["low"])
    hi   = float(bar["high"])
    buf  = instrument["s5_ob_invalidation_buffer_pct"]
    side = pending["side"]

    if _is_session_end(ts, instrument):
        return "session_end", 0.0

    if side == "LONG" and lo < pending["ob_low"] * (1 - buf):
        return "ob_invalid", 0.0
    if side == "SHORT" and hi > pending["ob_high"] * (1 + buf):
        return "ob_invalid", 0.0

    if ts > pending["expires"]:
        return "expired", 0.0

    if side == "LONG" and lo <= pending["trigger"]:
        return "fill", pending["trigger"]
    if side == "SHORT" and hi >= pending["trigger"]:
        return "fill", pending["trigger"]

    return "hold", 0.0
```

- [ ] **Step 4: Run PENDING tests**

```bash
source venv/bin/activate && pytest tests/test_backtest_ig.py -k "pending" -v 2>&1 | tail -15
```

Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add backtest_ig.py tests/test_backtest_ig.py
git commit -m "feat(backtest-ig): PENDING state transitions"
```

---

## Task 4: IN_TRADE state transitions

**Files:**
- Modify: `backtest_ig.py` (add `_check_trade`)
- Modify: `tests/test_backtest_ig.py`

- [ ] **Step 1: Add failing IN_TRADE tests**

Append to `tests/test_backtest_ig.py`:

```python
# ── IN_TRADE state tests ───────────────────────────────────────────── #

def _make_trade(side="LONG", entry=40500.0, sl=39500.0, tp=42500.0):
    tp1 = (entry + (entry - sl)) if side == "LONG" else (entry - (sl - entry))
    return {
        "side":        side,
        "entry":       entry,
        "sl":          sl,
        "tp":          tp,
        "tp1":         tp1,
        "sl_current":  sl,
        "partial_hit": False,
    }


def test_trade_partial_tp_long():
    import backtest_ig as bt
    inst  = _dummy_instrument()
    trade = _make_trade(side="LONG", entry=40500.0, sl=39500.0)
    # tp1 = 40500 + (40500 - 39500) = 41500
    bar = _make_bar(ts=1_700_001_000_000, lo=40400.0, hi=41600.0)
    action, price = bt._check_trade(bar, trade, inst)
    assert action == "partial_tp"
    assert price == trade["tp1"]


def test_trade_partial_tp_short():
    import backtest_ig as bt
    inst  = _dummy_instrument()
    trade = _make_trade(side="SHORT", entry=40500.0, sl=41500.0, tp=38500.0)
    # tp1 = 40500 - (41500 - 40500) = 39500
    bar = _make_bar(ts=1_700_001_000_000, lo=39400.0, hi=40600.0)
    action, price = bt._check_trade(bar, trade, inst)
    assert action == "partial_tp"
    assert price == trade["tp1"]


def test_trade_sl_long():
    import backtest_ig as bt
    inst  = _dummy_instrument()
    trade = _make_trade(side="LONG", entry=40500.0, sl=39500.0)
    bar   = _make_bar(ts=1_700_001_000_000, lo=39400.0, hi=40200.0)
    action, price = bt._check_trade(bar, trade, inst)
    assert action == "sl"
    assert price == 39500.0


def test_trade_sl_short():
    import backtest_ig as bt
    inst  = _dummy_instrument()
    trade = _make_trade(side="SHORT", entry=40500.0, sl=41500.0, tp=38500.0)
    bar   = _make_bar(ts=1_700_001_000_000, lo=40400.0, hi=41600.0)
    action, price = bt._check_trade(bar, trade, inst)
    assert action == "sl"
    assert price == 41500.0


def test_trade_tp_long():
    import backtest_ig as bt
    inst  = _dummy_instrument()
    trade = _make_trade(side="LONG", entry=40500.0, sl=39500.0, tp=42500.0)
    trade["partial_hit"] = True  # partial already taken
    trade["sl_current"]  = trade["entry"]
    bar = _make_bar(ts=1_700_001_000_000, lo=41000.0, hi=42600.0)
    action, price = bt._check_trade(bar, trade, inst)
    assert action == "tp"
    assert price == 42500.0


def test_trade_tp_short():
    import backtest_ig as bt
    inst  = _dummy_instrument()
    trade = _make_trade(side="SHORT", entry=40500.0, sl=41500.0, tp=38500.0)
    trade["partial_hit"] = True
    trade["sl_current"]  = trade["entry"]
    bar = _make_bar(ts=1_700_001_000_000, lo=38400.0, hi=39000.0)
    action, price = bt._check_trade(bar, trade, inst)
    assert action == "tp"
    assert price == 38500.0


def test_trade_breakeven_sl_after_partial():
    """After partial TP, SL moves to entry. A bar touching entry triggers SL."""
    import backtest_ig as bt
    inst  = _dummy_instrument()
    trade = _make_trade(side="LONG", entry=40500.0, sl=39500.0, tp=42500.0)
    trade["partial_hit"] = True
    trade["sl_current"]  = trade["entry"]  # break-even
    # bar low touches entry price
    bar = _make_bar(ts=1_700_001_000_000, lo=40500.0, hi=40800.0)
    action, price = bt._check_trade(bar, trade, inst)
    assert action == "sl"
    assert price == 40500.0


def test_trade_session_end_closes(monkeypatch):
    import backtest_ig as bt
    inst  = _dummy_instrument()
    trade = _make_trade()
    bar   = _make_bar(ts=1_700_001_000_000, lo=40200.0, hi=40600.0, cl=40400.0)
    monkeypatch.setattr(bt, "_is_session_end", lambda ts, i: True)
    action, price = bt._check_trade(bar, trade, inst)
    assert action == "session_end"
    assert price == 40400.0  # close price


def test_trade_hold_when_no_level_hit():
    import backtest_ig as bt
    inst  = _dummy_instrument()
    trade = _make_trade(side="LONG", entry=40500.0, sl=39500.0, tp=42500.0)
    # bar range doesn't hit tp1, sl, or tp
    bar = _make_bar(ts=1_700_001_000_000, lo=40200.0, hi=40800.0)
    action, _ = bt._check_trade(bar, trade, inst)
    assert action == "hold"
```

- [ ] **Step 2: Run to verify they fail**

```bash
source venv/bin/activate && pytest tests/test_backtest_ig.py -k "trade" -v 2>&1 | tail -10
```

Expected: `AttributeError: module 'backtest_ig' has no attribute '_check_trade'`

- [ ] **Step 3: Add `_check_trade` to `backtest_ig.py`**

Add after `_check_pending`:

```python
# ── Simulation: IN_TRADE state ─────────────────────────────────────── #

def _check_trade(bar: dict, trade: dict, instrument: dict) -> tuple[str, float]:
    """
    Evaluate one 15m bar against an open trade.

    Returns (action, price):
      action: "partial_tp" | "sl" | "tp" | "session_end" | "hold"
      price:  the level that was hit (or bar close for session_end)
    """
    ts   = int(bar["ts"])
    lo   = float(bar["low"])
    hi   = float(bar["high"])
    cl   = float(bar["close"])
    side = trade["side"]
    sl   = trade.get("sl_current", trade["sl"])

    if _is_session_end(ts, instrument):
        return "session_end", cl

    # Partial TP check (1:1 R:R) — takes priority before SL
    if not trade.get("partial_hit"):
        if side == "LONG"  and hi >= trade["tp1"]:
            return "partial_tp", trade["tp1"]
        if side == "SHORT" and lo <= trade["tp1"]:
            return "partial_tp", trade["tp1"]

    # SL check (uses sl_current which may be break-even after partial)
    if side == "LONG"  and lo <= sl:
        return "sl", sl
    if side == "SHORT" and hi >= sl:
        return "sl", sl

    # Full TP check
    if side == "LONG"  and hi >= trade["tp"]:
        return "tp", trade["tp"]
    if side == "SHORT" and lo <= trade["tp"]:
        return "tp", trade["tp"]

    return "hold", 0.0
```

- [ ] **Step 4: Run IN_TRADE tests**

```bash
source venv/bin/activate && pytest tests/test_backtest_ig.py -k "trade" -v 2>&1 | tail -15
```

Expected: `9 passed`

- [ ] **Step 5: Commit**

```bash
git add backtest_ig.py tests/test_backtest_ig.py
git commit -m "feat(backtest-ig): IN_TRADE state transitions with partial TP + break-even SL"
```

---

## Task 5: Window slicing + PnL helpers

**Files:**
- Modify: `backtest_ig.py` (add `_slice_windows`, `_calc_pnl`, `_collect_candles`)
- Modify: `tests/test_backtest_ig.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_backtest_ig.py`:

```python
# ── Window slicing + PnL tests ─────────────────────────────────────── #

def _make_ts_series(n, start_ms=1_700_000_000_000, step_ms=900_000):
    """Generate n timestamps spaced step_ms apart."""
    return [start_ms + i * step_ms for i in range(n)]


def test_slice_windows_caps_to_limits():
    import backtest_ig as bt
    inst = _dummy_instrument()
    inst["daily_limit"] = 5
    inst["htf_limit"]   = 3
    inst["m15_limit"]   = 10

    n1d   = 20
    n1h   = 15
    n15m  = 50
    step_day  = 86_400_000
    step_1h   = 3_600_000
    step_15m  = 900_000

    df_1d  = _make_df([[1_699_000_000_000 + i * step_day,  100,110,90,100,0] for i in range(n1d)])
    df_1h  = _make_df([[1_699_000_000_000 + i * step_1h,   100,110,90,100,0] for i in range(n1h)])
    df_15m = _make_df([[1_700_000_000_000 + i * step_15m,  100,110,90,100,0] for i in range(n15m)])

    # Use bar index i=40 (well inside all windows)
    daily, htf, m15 = bt._slice_windows(40, df_1d, df_1h, df_15m, inst)
    assert len(daily) <= inst["daily_limit"]
    assert len(htf)   <= inst["htf_limit"]
    assert len(m15)   <= inst["m15_limit"]


def test_calc_pnl_long_full_tp():
    import backtest_ig as bt
    trade = _make_trade(side="LONG", entry=40500.0, sl=39500.0, tp=42500.0)
    trade["partial_hit"] = False
    trade["exit_price"]  = 42500.0
    pnl = bt._calc_pnl(trade)
    assert pnl == pytest.approx(2000.0)


def test_calc_pnl_long_partial_then_tp():
    import backtest_ig as bt
    trade = _make_trade(side="LONG", entry=40500.0, sl=39500.0, tp=42500.0)
    # tp1 = 40500 + 1000 = 41500
    trade["partial_hit"]   = True
    trade["partial_price"] = trade["tp1"]   # 41500
    trade["exit_price"]    = 42500.0        # remainder hits full TP
    pnl = bt._calc_pnl(trade)
    # half at +1000pts, half at +2000pts → avg 1500
    assert pnl == pytest.approx(1500.0)


def test_calc_pnl_short_sl():
    import backtest_ig as bt
    trade = _make_trade(side="SHORT", entry=40500.0, sl=41500.0, tp=38500.0)
    trade["partial_hit"] = False
    trade["exit_price"]  = 41500.0   # SL hit
    pnl = bt._calc_pnl(trade)
    assert pnl == pytest.approx(-1000.0)


def test_collect_candles_returns_correct_window():
    import backtest_ig as bt
    n = 200
    df = _make_df([[1_700_000_000_000 + i * 900_000, 100, 110, 90, 100, 0] for i in range(n)])
    entry_i = 100
    exit_i  = 120
    candles = bt._collect_candles(df, entry_i, exit_i, before=50)
    assert len(candles) == (exit_i + 5) - (entry_i - 50)
    assert "t" in candles[0]
    assert "o" in candles[0]
```

- [ ] **Step 2: Run to verify they fail**

```bash
source venv/bin/activate && pytest tests/test_backtest_ig.py -k "slice or pnl or collect" -v 2>&1 | tail -10
```

Expected: `AttributeError`

- [ ] **Step 3: Add helpers to `backtest_ig.py`**

Add after `_check_trade`:

```python
# ── Simulation helpers ─────────────────────────────────────────────── #

def _slice_windows(i: int, df_1d: pd.DataFrame, df_1h: pd.DataFrame,
                   df_15m: pd.DataFrame, instrument: dict) -> tuple:
    """Slice daily/1H/15m DataFrames to the view available at bar i."""
    bar_ts = int(df_15m.iloc[i]["ts"])
    daily  = df_1d[df_1d["ts"] <= bar_ts].tail(instrument["daily_limit"])
    htf    = df_1h[df_1h["ts"] <= bar_ts].tail(instrument["htf_limit"])
    m15    = df_15m.iloc[max(0, i - instrument["m15_limit"] + 1): i + 1]
    return (
        daily.reset_index(drop=True),
        htf.reset_index(drop=True),
        m15.reset_index(drop=True),
    )


def _calc_pnl(trade: dict) -> float:
    """PnL in points. Accounts for 2-leg structure (partial + remainder)."""
    side   = trade["side"]
    entry  = trade["entry"]
    exit_p = trade["exit_price"]
    sign   = 1 if side == "LONG" else -1

    if trade.get("partial_hit"):
        partial_pts = sign * (trade["tp1"] - entry)
        remain_pts  = sign * (exit_p - entry)
        return partial_pts * 0.5 + remain_pts * 0.5
    return sign * (exit_p - entry)


def _collect_candles(df_15m: pd.DataFrame, entry_i: int, exit_i: int,
                     before: int = 50) -> list[dict]:
    """Return ~100 15m candles centred around the trade for the chart."""
    start = max(0, entry_i - before)
    end   = min(len(df_15m), exit_i + 5)
    rows  = df_15m.iloc[start:end]
    return [
        {
            "t": int(r["ts"]),
            "o": round(float(r["open"]),  2),
            "h": round(float(r["high"]),  2),
            "l": round(float(r["low"]),   2),
            "c": round(float(r["close"]), 2),
        }
        for _, r in rows.iterrows()
    ]
```

- [ ] **Step 4: Run tests**

```bash
source venv/bin/activate && pytest tests/test_backtest_ig.py -k "slice or pnl or collect" -v 2>&1 | tail -10
```

Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add backtest_ig.py tests/test_backtest_ig.py
git commit -m "feat(backtest-ig): window slicing, PnL calc, candle collection helpers"
```

---

## Task 6: Full bar loop (`run_instrument`) + stats

**Files:**
- Modify: `backtest_ig.py` (add `run_instrument`, `_compute_stats`)
- Modify: `tests/test_backtest_ig.py`

- [ ] **Step 1: Add failing integration tests**

Append to `tests/test_backtest_ig.py`:

```python
# ── run_instrument integration tests ──────────────────────────────── #

def _make_candle_series(n, start_ms, step_ms, base_price=40000.0):
    rows = []
    for i in range(n):
        rows.append([
            start_ms + i * step_ms,
            base_price, base_price + 100, base_price - 100, base_price, 100,
        ])
    return _make_df(rows)


def test_run_instrument_no_trades_when_evaluate_holds(monkeypatch):
    """When evaluate_s5 always returns HOLD, no trades or cancels are produced."""
    import backtest_ig as bt

    monkeypatch.setattr(bt, "evaluate_s5",
        lambda *a, **kw: ("HOLD", 0.0, 0.0, 0.0, 0.0, 0.0, "no signal"))
    monkeypatch.setattr(bt, "_in_session", lambda ts, inst: True)
    monkeypatch.setattr(bt, "_is_session_end", lambda ts, inst: False)

    inst   = _dummy_instrument()
    n      = inst["daily_limit"] + 50
    step   = 900_000
    start  = 1_700_000_000_000
    df_1d  = _make_candle_series(300, start - 300 * 86_400_000, 86_400_000)
    df_1h  = _make_candle_series(300, start - 300 * 3_600_000,  3_600_000)
    df_15m = _make_candle_series(n,   start,                    step)

    result = bt.run_instrument(inst, df_1d, df_1h, df_15m)
    assert result["trades"]    == []
    assert result["cancelled"] == []


def test_run_instrument_pending_fills_and_wins(monkeypatch):
    """
    evaluate_s5 returns PENDING_LONG once; trigger fills; full TP hit → 1 WIN trade.
    """
    import backtest_ig as bt

    call_count = {"n": 0}

    def mock_evaluate(*a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return ("PENDING_LONG", 40500.0, 39500.0, 42500.0, 40000.0, 40500.0, "OB ok")
        return ("HOLD", 0.0, 0.0, 0.0, 0.0, 0.0, "hold")

    monkeypatch.setattr(bt, "evaluate_s5", mock_evaluate)
    monkeypatch.setattr(bt, "_in_session",    lambda ts, inst: True)
    monkeypatch.setattr(bt, "_is_session_end", lambda ts, inst: False)
    monkeypatch.setattr(bt, "calculate_ema",
        lambda series, period: pd.Series([100.0] * len(series)))

    inst  = _dummy_instrument()
    start = 1_700_000_000_000
    step  = 900_000

    df_1d  = _make_candle_series(300, start - 300 * 86_400_000, 86_400_000)
    df_1h  = _make_candle_series(300, start - 300 * 3_600_000,  3_600_000)

    # Warm-up bars, then: bar hits trigger (low=40490), bar hits TP (high=42600)
    n_warmup = inst["daily_limit"] + 10
    rows = []
    base = start
    for i in range(n_warmup):
        rows.append([base + i * step, 40000, 40100, 39900, 40050, 100])
    # evaluate_s5 fires at index n_warmup → PENDING set
    rows.append([base + n_warmup       * step, 40000, 40100, 39900, 40050, 100])
    # next bar: low <= trigger (40500) → fill
    rows.append([base + (n_warmup + 1) * step, 40400, 40600, 40490, 40500, 100])
    # subsequent bars hold in trade
    for j in range(5):
        rows.append([base + (n_warmup + 2 + j) * step, 41000, 41200, 40800, 41100, 100])
    # TP bar: high >= tp (42500)
    rows.append([base + (n_warmup + 8) * step, 42000, 42600, 41800, 42500, 100])

    df_15m = _make_df(rows)
    result = bt.run_instrument(inst, df_1d, df_1h, df_15m)

    assert len(result["trades"]) == 1
    t = result["trades"][0]
    assert t["side"]        == "LONG"
    assert t["exit_reason"] == "TP"
    assert t["pnl_pts"]     > 0


def test_compute_stats_basic():
    import backtest_ig as bt

    trades = [
        {"pnl_pts": 2000.0, "partial_hit": True,  "exit_reason": "TP"},
        {"pnl_pts": -1000.0,"partial_hit": False, "exit_reason": "SL"},
    ]
    cancelled = [
        {"reason": "OB_INVALID"},
        {"reason": "EXPIRED"},
        {"reason": "OB_INVALID"},
    ]
    result = {"instrument": "US30", "trades": trades, "cancelled": cancelled}
    stats  = bt._compute_stats(result)

    assert stats["signals"]         == 5
    assert stats["filled"]          == 2
    assert stats["wins"]            == 1
    assert stats["losses"]          == 1
    assert stats["win_rate"]        == 50.0
    assert stats["partial_rate"]    == 50.0
    assert stats["cancelled"]["OB_INVALID"] == 2
    assert stats["cancelled"]["EXPIRED"]    == 1
    assert stats["total_pnl_pts"]   == pytest.approx(1000.0)
```

- [ ] **Step 2: Run to verify they fail**

```bash
source venv/bin/activate && pytest tests/test_backtest_ig.py -k "run_instrument or compute_stats" -v 2>&1 | tail -10
```

Expected: `AttributeError: module 'backtest_ig' has no attribute 'run_instrument'`

- [ ] **Step 3: Add `run_instrument` and `_compute_stats` to `backtest_ig.py`**

Add after `_collect_candles`:

```python
# ── Simulation: full bar loop ──────────────────────────────────────── #

def run_instrument(instrument: dict,
                   df_1d: pd.DataFrame, df_1h: pd.DataFrame,
                   df_15m: pd.DataFrame) -> dict:
    """
    Walk every 15m bar in chronological order.
    Returns {"instrument": name, "trades": [...], "cancelled": [...]}.
    """
    name   = instrument["display_name"]
    epic   = instrument["epic"]
    trades:    list[dict] = []
    cancelled: list[dict] = []

    state   = "IDLE"
    pending: dict | None = None
    trade:   dict | None = None

    # Skip first daily_limit+10 bars so EMA calculations have enough history
    min_i = instrument["daily_limit"] + 10

    for i in range(min_i, len(df_15m)):
        bar = df_15m.iloc[i].to_dict()
        ts  = int(bar["ts"])

        # ── IN_TRADE ──────────────────────────────────────────────── #
        if state == "IN_TRADE":
            action, price = _check_trade(bar, trade, instrument)

            if action == "partial_tp":
                trade["partial_hit"]   = True
                trade["partial_price"] = price
                trade["sl_current"]    = trade["entry"]   # break-even

            elif action in ("sl", "tp", "session_end"):
                trade["exit_reason"] = action.upper()
                trade["exit_price"]  = price
                trade["exit_dt"]     = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                trade["pnl_pts"]     = _calc_pnl(trade)
                trade["candles"]     = _collect_candles(df_15m, trade["entry_i"], i)
                trades.append(trade)
                state = "IDLE"
                trade = None

        # ── PENDING ───────────────────────────────────────────────── #
        elif state == "PENDING":
            action, fill_price = _check_pending(bar, pending, instrument)

            if action == "fill":
                entry = fill_price
                sl    = pending["sl"]
                tp    = pending["tp"]
                side  = pending["side"]
                tp1   = (entry + (entry - sl)) if side == "LONG" else (entry - (sl - entry))
                trade = {
                    "instrument":  name,
                    "side":        side,
                    "entry_dt":    datetime.fromtimestamp(ts / 1000, tz=timezone.utc),
                    "entry_i":     i,
                    "trigger":     pending["trigger"],
                    "entry":       entry,
                    "sl":          sl,
                    "tp":          tp,
                    "tp1":         tp1,
                    "ob_low":      pending["ob_low"],
                    "ob_high":     pending["ob_high"],
                    "partial_hit": False,
                    "sl_current":  sl,
                }
                state   = "IN_TRADE"
                pending = None

            elif action in ("ob_invalid", "expired", "session_end"):
                cancelled.append({
                    "instrument": name,
                    "reason":     action.upper(),
                    "dt":         datetime.fromtimestamp(ts / 1000, tz=timezone.utc),
                    "side":       pending["side"],
                })
                state   = "IDLE"
                pending = None

        # ── IDLE ──────────────────────────────────────────────────── #
        else:
            if not _in_session(ts, instrument):
                continue

            daily_df, htf_df, m15_df = _slice_windows(i, df_1d, df_1h, df_15m, instrument)

            if len(daily_df) < instrument["s5_daily_ema_slow"] + 5:
                continue
            if htf_df.empty or m15_df.empty:
                continue

            ema_fast = float(calculate_ema(
                daily_df["close"].astype(float), instrument["s5_daily_ema_fast"]
            ).iloc[-1])
            ema_slow = float(calculate_ema(
                daily_df["close"].astype(float), instrument["s5_daily_ema_slow"]
            ).iloc[-1])
            allowed = "BULLISH" if ema_fast > ema_slow else "BEARISH"

            try:
                sig, trigger, sl, tp, ob_low, ob_high, _ = evaluate_s5(
                    epic, daily_df, htf_df, m15_df, allowed, cfg=instrument
                )
            except Exception:
                continue

            if sig in ("PENDING_LONG", "PENDING_SHORT"):
                pending = {
                    "side":    "LONG" if sig == "PENDING_LONG" else "SHORT",
                    "trigger": trigger,
                    "sl":      sl,
                    "tp":      tp,
                    "ob_low":  ob_low,
                    "ob_high": ob_high,
                    "expires": ts + 4 * 3_600_000,   # 4h in ms
                }
                state = "PENDING"

    return {"instrument": name, "trades": trades, "cancelled": cancelled}


# ── Stats aggregation ──────────────────────────────────────────────── #

def _compute_stats(result: dict) -> dict:
    trades    = result["trades"]
    cancelled = result["cancelled"]
    wins      = [t for t in trades if t["pnl_pts"] > 0]
    losses    = [t for t in trades if t["pnl_pts"] <= 0]
    partials  = [t for t in trades if t.get("partial_hit")]

    cancel_counts = {
        "OB_INVALID":  sum(1 for c in cancelled if c["reason"] == "OB_INVALID"),
        "EXPIRED":     sum(1 for c in cancelled if c["reason"] == "EXPIRED"),
        "SESSION_END": sum(1 for c in cancelled if c["reason"] == "SESSION_END"),
    }
    total_signals = len(trades) + len(cancelled)
    gross_win     = sum(t["pnl_pts"] for t in wins)  if wins   else 0.0
    gross_loss    = abs(sum(t["pnl_pts"] for t in losses)) if losses else 0.0

    return {
        "name":          result["instrument"],
        "signals":       total_signals,
        "filled":        len(trades),
        "fill_rate":     round(len(trades) / total_signals * 100, 1) if total_signals else 0.0,
        "cancelled":     cancel_counts,
        "wins":          len(wins),
        "losses":        len(losses),
        "win_rate":      round(len(wins) / len(trades) * 100, 1) if trades else 0.0,
        "partial_rate":  round(len(partials) / len(trades) * 100, 1) if trades else 0.0,
        "avg_win_pts":   round(gross_win  / len(wins),   1) if wins   else 0.0,
        "avg_loss_pts":  round(-gross_loss / len(losses), 1) if losses else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else float("inf"),
        "total_pnl_pts": round(sum(t["pnl_pts"] for t in trades), 1),
        "trades":        trades,
        "cancelled_list":cancelled,
    }
```

- [ ] **Step 4: Run integration tests**

```bash
source venv/bin/activate && pytest tests/test_backtest_ig.py -k "run_instrument or compute_stats" -v 2>&1 | tail -15
```

Expected: `4 passed`

- [ ] **Step 5: Run full test suite so far**

```bash
source venv/bin/activate && pytest tests/test_backtest_ig.py -v 2>&1 | tail -15
```

Expected: All tests pass (no failures)

- [ ] **Step 6: Commit**

```bash
git add backtest_ig.py tests/test_backtest_ig.py
git commit -m "feat(backtest-ig): full bar loop state machine + stats aggregation"
```

---

## Task 7: HTML report builder

**Files:**
- Modify: `backtest_ig.py` (add `build_report`)

No separate tests for the report builder — smoke-tested via main() in Task 8.

- [ ] **Step 1: Add `build_report` to `backtest_ig.py`**

Add after `_compute_stats`:

```python
# ── Report builder ─────────────────────────────────────────────────── #

def build_report(all_stats: list[dict], run_time: str) -> str:
    """Build a self-contained dark-theme HTML report with inline chart data."""

    def col(v):
        if isinstance(v, (int, float)):
            return "#00d68f" if v > 0 else "#ff4d6a" if v < 0 else "#8899aa"
        return "#c9d8e8"

    def card(label, val, sfx=""):
        return (f'<div class="stat"><div class="stat-label">{label}</div>'
                f'<div class="stat-val" style="color:{col(val)}">{val}{sfx}</div></div>')

    def stats_grid(s):
        return (
            f'<div class="grid">'
            f'{card("Signals",       s["signals"])}'
            f'{card("Filled",        s["filled"])}'
            f'{card("Fill Rate",     s["fill_rate"], "%")}'
            f'{card("Win Rate",      s["win_rate"],  "%")}'
            f'{card("Partial Rate",  s["partial_rate"], "%")}'
            f'{card("Total PnL",     s["total_pnl_pts"], " pts")}'
            f'{card("Avg Win",       s["avg_win_pts"],  " pts")}'
            f'{card("Avg Loss",      s["avg_loss_pts"], " pts")}'
            f'{card("Profit Factor", s["profit_factor"])}'
            f'<div class="stat"><div class="stat-label">Cancelled</div>'
            f'<div style="font-size:11px;color:#8899aa;padding-top:4px">'
            f'OB: {s["cancelled"]["OB_INVALID"]}<br>'
            f'Exp: {s["cancelled"]["EXPIRED"]}<br>'
            f'Sess: {s["cancelled"]["SESSION_END"]}'
            f'</div></div>'
            f'</div>'
        )

    def trade_table(trades, inst_id):
        if not trades:
            return '<p style="color:#8899aa;padding:20px">No completed trades</p>'
        rows = ""
        for idx, t in enumerate(sorted(trades, key=lambda x: x["entry_dt"], reverse=True)):
            rc  = "#00d68f" if t["pnl_pts"] > 0 else "#ff4d6a"
            pc  = col(t["pnl_pts"])
            sid = "LONG" if t["side"] == "LONG" else "SHORT"
            sc  = "#00d68f" if sid == "LONG" else "#ff4d6a"
            prt = "✓" if t.get("partial_hit") else "—"
            edt = t["exit_dt"].strftime("%Y-%m-%d %H:%M") if t.get("exit_dt") else "—"
            edt_entry = t["entry_dt"].strftime("%Y-%m-%d %H:%M") if t.get("entry_dt") else "—"
            chart_btn = ""
            if t.get("candles"):
                cdata = json.dumps(t["candles"])
                meta  = json.dumps({
                    "side":    t["side"],
                    "entry":   t["entry"],
                    "sl":      t["sl"],
                    "tp":      t["tp"],
                    "tp1":     t["tp1"],
                    "ob_low":  t["ob_low"],
                    "ob_high": t["ob_high"],
                    "exit_price":  t.get("exit_price", 0),
                    "exit_reason": t.get("exit_reason", ""),
                    "partial_hit": t.get("partial_hit", False),
                    "partial_price": t.get("partial_price", 0),
                })
                chart_btn = (
                    f'<button class="chart-btn" '
                    f'onclick=\'openChart({inst_id},{idx},{cdata},{meta})\'>Chart</button>'
                )
            rows += (
                f'<tr>'
                f'<td>{edt_entry}</td>'
                f'<td style="color:{sc}">{sid}</td>'
                f'<td>{t["entry"]:.1f}</td>'
                f'<td>{t["sl"]:.1f}</td>'
                f'<td>{t["tp"]:.1f}</td>'
                f'<td>{prt}</td>'
                f'<td>{t.get("exit_reason","—")}</td>'
                f'<td>{edt}</td>'
                f'<td>{t.get("exit_price",0):.1f}</td>'
                f'<td style="color:{pc}">{t["pnl_pts"]:+.1f}</td>'
                f'<td>{chart_btn}</td>'
                f'</tr>'
            )
        return (
            f'<div style="overflow-x:auto"><table><thead><tr>'
            f'<th>Entry</th><th>Side</th><th>Entry$</th><th>SL</th><th>TP</th>'
            f'<th>Partial</th><th>Exit Reason</th><th>Exit Time</th>'
            f'<th>Exit$</th><th>PnL (pts)</th><th></th>'
            f'</tr></thead><tbody>{rows}</tbody></table></div>'
        )

    # Build per-instrument sections
    inst_sections = ""
    tab_headers   = '<div class="tab active" onclick="sw(\'overall\')">Overall</div>'
    for s in all_stats:
        iid = s["name"].lower()
        tab_headers += f'<div class="tab" onclick="sw(\'{iid}\')">{s["name"]} ({s["filled"]})</div>'

    overall_trades = []
    for s in all_stats:
        overall_trades.extend(s["trades"])
    ovr_pnl  = round(sum(t["pnl_pts"] for t in overall_trades), 1)
    ovr_wins = sum(1 for t in overall_trades if t["pnl_pts"] > 0)
    ovr_wr   = round(ovr_wins / len(overall_trades) * 100, 1) if overall_trades else 0

    for s in all_stats:
        iid = s["name"].lower()
        inst_sections += (
            f'<div id="t{iid}" class="tc">'
            f'<h2>{s["name"]}</h2>'
            f'{stats_grid(s)}'
            f'{trade_table(s["trades"], iid)}'
            f'</div>'
        )

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>IG Backtest Report — {run_time}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0d1117;color:#c9d8e8;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:13px;padding:24px}}
h1{{font-size:22px;color:#e8f0f8;margin-bottom:4px}}
h2{{font-size:15px;color:#8899aa;margin:28px 0 14px;border-bottom:1px solid #1e2d3d;padding-bottom:8px}}
.meta{{color:#8899aa;font-size:12px;margin-bottom:28px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:12px;margin-bottom:20px}}
.stat{{background:#111827;border:1px solid #1e2d3d;border-radius:8px;padding:14px}}
.stat-label{{font-size:10px;color:#8899aa;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}}
.stat-val{{font-size:20px;font-weight:700}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{background:#0d1117;color:#8899aa;padding:8px 12px;text-align:left;font-size:11px;text-transform:uppercase;position:sticky;top:0}}
td{{padding:8px 12px;border-bottom:1px solid #1a2535}}
tr:hover td{{background:#1a2535}}
.tabs{{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap}}
.tab{{padding:8px 20px;border-radius:8px;cursor:pointer;border:1px solid #1e2d3d;background:#111827;color:#8899aa;font-size:13px}}
.tab.active{{background:#1e3a5f;border-color:#60a5fa;color:#60a5fa}}
.tc{{display:none}}.tc.active{{display:block}}
.chart-btn{{background:#1e2d3d;border:1px solid #334155;color:#60a5fa;padding:3px 10px;border-radius:5px;cursor:pointer;font-size:11px}}
.chart-btn:hover{{background:#1e3a5f}}
.overlay{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:100;align-items:center;justify-content:center}}
.overlay.open{{display:flex}}
.modal{{background:#111827;border:1px solid #1e2d3d;border-radius:12px;padding:20px;max-width:900px;width:95%;position:relative}}
.modal-header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}}
.modal-title{{font-size:14px;font-weight:600;color:#e8f0f8}}
.close-btn{{background:none;border:1px solid #334155;color:#8899aa;border-radius:6px;padding:4px 12px;cursor:pointer;font-size:12px}}
.close-btn:hover{{border-color:#f85149;color:#f85149}}
canvas{{display:block;width:100%;height:380px}}
</style></head><body>
<h1>IG S5 Backtest Report</h1>
<div class="meta">Run: {run_time} | Instruments: {", ".join(s["name"] for s in all_stats)}</div>

<h2>Overall</h2>
<div class="grid">
{card("Total Trades", len(overall_trades))}
{card("Win Rate", ovr_wr, "%")}
{card("Total PnL", ovr_pnl, " pts")}
</div>

<div class="tabs">{tab_headers}</div>

<div id="toverall" class="tc active">
<p style="color:#8899aa;font-size:12px;padding:4px 0 16px">Combined across all instruments</p>
{"".join(f'<h3 style="color:#8899aa;font-size:13px;margin:16px 0 8px">{s["name"]}</h3>{stats_grid(s)}' for s in all_stats)}
</div>
{inst_sections}

<!-- Chart modal -->
<div class="overlay" id="chartOverlay" onclick="closeChart(event)">
  <div class="modal" id="chartModal" onclick="event.stopPropagation()">
    <div class="modal-header">
      <div class="modal-title" id="chartTitle">Chart</div>
      <button class="close-btn" onclick="closeChart()">Close</button>
    </div>
    <div id="chartLegend" style="font-size:11px;color:#8899aa;margin-bottom:8px;display:flex;gap:16px;flex-wrap:wrap"></div>
    <canvas id="chartCanvas"></canvas>
  </div>
</div>

<script>
function sw(t){{
  document.querySelectorAll('.tab').forEach(e=>e.classList.remove('active'));
  document.querySelectorAll('.tc').forEach(e=>e.classList.remove('active'));
  document.querySelector('.tab[onclick="sw(\\''+t+'\\')"]').classList.add('active');
  document.getElementById('t'+t).classList.add('active');
}}

function closeChart(e){{
  if(!e||e.target===document.getElementById('chartOverlay'))
    document.getElementById('chartOverlay').classList.remove('open');
}}

function openChart(instId, tradeIdx, candles, meta){{
  const side = meta.side;
  document.getElementById('chartTitle').textContent =
    instId.toUpperCase()+' · '+side+' · Entry '+meta.entry.toFixed(1);
  const items=[
    ['#3fb950','Entry '+meta.entry.toFixed(1)],
    ['#ff4d6a','SL '+meta.sl.toFixed(1)],
    ['#60a5fa','TP '+meta.tp.toFixed(1)],
    ['#a371f7','TP1 '+meta.tp1.toFixed(1)],
  ];
  if(meta.partial_hit) items.push(['#e3b341','Partial '+meta.partial_price.toFixed(1)]);
  items.push([meta.exit_reason==='TP'?'#00d68f':'#ff4d6a',
    meta.exit_reason+' '+meta.exit_price.toFixed(1)]);
  document.getElementById('chartLegend').innerHTML=items.map(([c,l])=>
    `<span style="display:flex;align-items:center;gap:4px">
      <span style="width:8px;height:8px;background:${{c}};border-radius:2px;display:inline-block"></span>
      <span>${{l}}</span></span>`).join('');
  document.getElementById('chartOverlay').classList.add('open');
  requestAnimationFrame(()=>_drawBacktestChart(
    document.getElementById('chartCanvas'), candles, meta));
}}

function _drawBacktestChart(canvas, candles, meta){{
  const dpr=window.devicePixelRatio||1;
  const W=canvas.offsetWidth||800, H=canvas.offsetHeight||380;
  canvas.width=W*dpr; canvas.height=H*dpr;
  const ctx=canvas.getContext('2d');
  ctx.scale(dpr,dpr);
  const PAD_L=10,PAD_R=10,PAD_T=20,PAD_B=20;
  const chartW=W-PAD_L-PAD_R, chartH=H-PAD_T-PAD_B;

  const levels=[meta.sl,meta.tp,meta.tp1,meta.entry,
    meta.exit_price,(meta.partial_hit?meta.partial_price:null)].filter(Boolean);
  const allPrices=[...candles.flatMap(c=>[c.h,c.l]),...levels].filter(Boolean);
  const priceMin=Math.min(...allPrices)*0.9995;
  const priceMax=Math.max(...allPrices)*1.0005;
  const priceRange=priceMax-priceMin||1;

  function yp(p){{return PAD_T+chartH*(1-(p-priceMin)/priceRange);}}
  function xp(i){{return PAD_L+i*(chartW/candles.length);}}
  const candleW=Math.max(1,chartW/candles.length-1);

  ctx.fillStyle='#0d1117';
  ctx.fillRect(0,0,W,H);

  // OB zone
  if(meta.ob_low&&meta.ob_high){{
    ctx.fillStyle='rgba(96,165,250,0.08)';
    ctx.fillRect(PAD_L,yp(meta.ob_high),chartW,yp(meta.ob_low)-yp(meta.ob_high));
  }}

  // Levels
  const lvls=[
    {{p:meta.sl,       c:'#ff4d6a', dash:[4,3]}},
    {{p:meta.tp,       c:'#60a5fa', dash:[4,3]}},
    {{p:meta.tp1,      c:'#a371f7', dash:[3,3]}},
    {{p:meta.entry,    c:'#3fb950', dash:[]}},
    {{p:meta.exit_price,c:meta.exit_reason==='TP'?'#00d68f':'#ff4d6a',dash:[2,2]}},
  ];
  if(meta.partial_hit)lvls.push({{p:meta.partial_price,c:'#e3b341',dash:[3,3]}});
  lvls.forEach(l=>{{
    if(!l.p)return;
    ctx.strokeStyle=l.c; ctx.lineWidth=1;
    ctx.setLineDash(l.dash);
    ctx.beginPath();
    ctx.moveTo(PAD_L,yp(l.p)); ctx.lineTo(W-PAD_R,yp(l.p));
    ctx.stroke();
  }});
  ctx.setLineDash([]);

  // Candles
  candles.forEach((c,i)=>{{
    const x=xp(i);
    const isGreen=c.c>=c.o;
    const bodyColor=isGreen?'#3fb950':'#f85149';
    ctx.strokeStyle=bodyColor; ctx.lineWidth=1;
    ctx.beginPath();
    ctx.moveTo(x+candleW/2,yp(c.h));
    ctx.lineTo(x+candleW/2,yp(c.l));
    ctx.stroke();
    const bTop=yp(Math.max(c.o,c.c));
    const bH=Math.max(1,Math.abs(yp(c.o)-yp(c.c)));
    ctx.fillStyle=bodyColor;
    ctx.fillRect(x,bTop,candleW,bH);
  }});

  // Entry marker
  const entryTs=meta.entry;
  const entryIdx=candles.findIndex(c=>Math.abs(c.c-entryTs)<entryTs*0.001);
  if(entryIdx>=0){{
    ctx.fillStyle='rgba(63,185,80,0.15)';
    ctx.fillRect(xp(entryIdx),PAD_T,candleW,chartH);
  }}
}}
</script>
<script>
// Tab switch fix for overall
document.querySelectorAll('.tab').forEach((el,i)=>{{
  const fn=el.getAttribute('onclick');
  if(fn)el.addEventListener('click',function(){{
    document.querySelectorAll('.tab').forEach(e=>e.classList.remove('active'));
    this.classList.add('active');
  }},true);
}});
</script>
</body></html>"""
```

- [ ] **Step 2: Commit**

```bash
git add backtest_ig.py
git commit -m "feat(backtest-ig): HTML report builder with inline candlestick charts"
```

---

## Task 8: CLI + main() + end-to-end smoke test

**Files:**
- Modify: `backtest_ig.py` (add `main()`)
- Modify: `tests/test_backtest_ig.py` (add smoke test)

- [ ] **Step 1: Add failing smoke test**

Append to `tests/test_backtest_ig.py`:

```python
# ── main() smoke test ──────────────────────────────────────────────── #

def test_main_no_fetch_produces_html(tmp_path, monkeypatch):
    """main() with mocked data returns without error and writes HTML."""
    import backtest_ig as bt

    # Provide pre-cached parquet for all timeframes
    start  = 1_700_000_000_000
    inst   = _dummy_instrument()
    n_warmup = inst["daily_limit"] + 20

    df_1d  = _make_candle_series(300, start - 300 * 86_400_000, 86_400_000)
    df_1h  = _make_candle_series(300, start - 300 * 3_600_000,  3_600_000)
    df_15m = _make_candle_series(n_warmup + 10, start, 900_000)

    cache = tmp_path / "cache"
    cache.mkdir()
    for interval, df in [("1D", df_1d), ("1H", df_1h), ("15m", df_15m)]:
        df.to_parquet(cache / f"US30_{interval}.parquet", index=False)
        df.to_parquet(cache / f"GOLD_{interval}.parquet", index=False)

    monkeypatch.setattr(bt, "_CACHE_DIR", cache)
    monkeypatch.setattr(bt, "evaluate_s5",
        lambda *a, **kw: ("HOLD", 0.0, 0.0, 0.0, 0.0, 0.0, "hold"))
    monkeypatch.setattr(bt, "_in_session",    lambda ts, inst: True)
    monkeypatch.setattr(bt, "_is_session_end", lambda ts, inst: False)
    monkeypatch.setattr(bt, "calculate_ema",
        lambda series, period: pd.Series([100.0] * len(series)))

    out = tmp_path / "report.html"
    import sys
    monkeypatch.setattr(sys, "argv",
        ["backtest_ig.py", "--no-fetch", "--output", str(out)])

    bt.main()
    assert out.exists()
    content = out.read_text()
    assert "IG S5 Backtest Report" in content
    assert "US30" in content
```

- [ ] **Step 2: Run to verify it fails**

```bash
source venv/bin/activate && pytest tests/test_backtest_ig.py::test_main_no_fetch_produces_html -v 2>&1 | tail -10
```

Expected: `AttributeError: module 'backtest_ig' has no attribute 'main'`

- [ ] **Step 3: Add `main()` to `backtest_ig.py`**

Add at the end of the file:

```python
# ── CLI ────────────────────────────────────────────────────────────── #

def main():
    parser = argparse.ArgumentParser(description="IG S5 walk-forward backtest")
    parser.add_argument("--no-fetch",    action="store_true",
                        help="Use cached parquet only (skip yfinance)")
    parser.add_argument("--instrument",  default=None,
                        help="Run single instrument only (e.g. US30)")
    parser.add_argument("--output",      default="backtest_ig_report.html",
                        help="Output HTML file path")
    args = parser.parse_args()

    instruments = [
        inst for inst in INSTRUMENTS
        if args.instrument is None or inst["display_name"] == args.instrument
    ]
    if not instruments:
        print(f"No instruments matched '{args.instrument}'. Available: "
              f"{[i['display_name'] for i in INSTRUMENTS]}")
        return

    all_stats = []
    for instrument in instruments:
        name = instrument["display_name"]
        print(f"\n[{name}] Loading candles...")
        try:
            df_1d  = load_candles(name, "1D",  no_fetch=args.no_fetch)
            df_1h  = load_candles(name, "1H",  no_fetch=args.no_fetch)
            df_15m = load_candles(name, "15m", no_fetch=args.no_fetch)
        except Exception as e:
            print(f"  ❌ Failed to load candles: {e}")
            continue

        print(f"  1D:  {len(df_1d)} bars")
        print(f"  1H:  {len(df_1h)} bars")
        print(f"  15m: {len(df_15m)} bars")

        if df_1d.empty or df_1h.empty or df_15m.empty:
            print(f"  ⚠️  Empty data — skipping {name}")
            continue

        print(f"[{name}] Running simulation...")
        result = run_instrument(instrument, df_1d, df_1h, df_15m)
        stats  = _compute_stats(result)
        all_stats.append(stats)

        print(f"  Signals:    {stats['signals']}")
        print(f"  Filled:     {stats['filled']}  ({stats['fill_rate']}%)")
        print(f"  Win rate:   {stats['win_rate']}%")
        print(f"  Total PnL:  {stats['total_pnl_pts']:+.1f} pts")
        print(f"  Cancelled:  OB={stats['cancelled']['OB_INVALID']}  "
              f"Exp={stats['cancelled']['EXPIRED']}  "
              f"Sess={stats['cancelled']['SESSION_END']}")

    if not all_stats:
        print("\n❌ No results to report.")
        return

    run_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html     = build_report(all_stats, run_time)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n✅ Report written → {args.output}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run smoke test**

```bash
source venv/bin/activate && pytest tests/test_backtest_ig.py::test_main_no_fetch_produces_html -v 2>&1 | tail -10
```

Expected: `1 passed`

- [ ] **Step 5: Run full test suite**

```bash
source venv/bin/activate && pytest tests/test_backtest_ig.py -v 2>&1 | tail -20
```

Expected: All tests pass

- [ ] **Step 6: Run qa-trading-bot skill to confirm no regressions**

```bash
source venv/bin/activate && pytest --tb=short 2>&1 | tail -20
```

Expected: All pre-existing tests still pass

- [ ] **Step 7: Commit**

```bash
git add backtest_ig.py tests/test_backtest_ig.py
git commit -m "feat(backtest-ig): CLI main() + smoke test — backtest_ig.py complete"
```

---

## Task 9: Live fetch smoke test

This task is run manually (requires network + yfinance data). Not part of automated test suite.

- [ ] **Step 1: Fetch and cache live data**

```bash
source venv/bin/activate && python backtest_ig.py --output backtest_ig_report.html
```

Expected output:
```
[US30] Loading candles...
  1D:  2514 bars
  1H:  3462 bars
  15m: 1502 bars
[US30] Running simulation...
  Signals:    N
  Filled:     N  (N%)
  ...
[GOLD] Loading candles...
  ...
✅ Report written → backtest_ig_report.html
```

- [ ] **Step 2: Verify report opens correctly**

Open `backtest_ig_report.html` in a browser. Verify:
- Dark theme renders
- Tabs switch between Overall / US30 / GOLD
- Trade rows show (or "No completed trades" if no signals fired)
- Chart button opens modal with candlestick chart (if trades exist)

- [ ] **Step 3: Verify cache was created**

```bash
ls -lh data/ig_cache/
```

Expected:
```
US30_15m.parquet
US30_1D.parquet
US30_1H.parquet
GOLD_15m.parquet
GOLD_1D.parquet
GOLD_1H.parquet
```

- [ ] **Step 4: Verify cached run is fast**

```bash
time python backtest_ig.py --no-fetch
```

Expected: completes in under 30 seconds

- [ ] **Step 5: Final commit**

```bash
git add data/ig_cache/.gitkeep 2>/dev/null; git add backtest_ig_report.html 2>/dev/null
git add docs/superpowers/specs/2026-04-06-backtest-ig-design.md
git commit -m "feat(backtest-ig): live fetch verified, report generated"
```
