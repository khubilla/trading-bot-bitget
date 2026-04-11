# Backtest Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `backtest_engine.py` — a unified backtesting harness that monkey-patches `trader`, `state`, `scanner`, `snapshot`, `claude_filter`, and `startup_recovery` with in-memory mocks, then runs the actual `MTFBot._tick()` loop against historical parquet candle data for all strategies (S1–S6) simultaneously.

**Architecture:** `backtest_engine.py` is a single self-contained file. It defines `MockTrader`, `BacktestState`, `MockScanner`, and `BacktestEngine`. Before importing `bot.py`, it patches `sys.modules` with these mocks. The engine builds a unified 3m timeline across all symbols, calls `mock_trader.process_bar(ts)` (exit simulation) then `bot._tick()` (entry detection) for each bar. Four parquet caches (`data/daily/`, `data/15m/`, `data/1h/`, `data/3m/`) are loaded incrementally via `load_*()` helpers added to `backtest.py`.

**Tech Stack:** Python 3.11+, pandas, pyarrow (parquet), requests, pytest

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `backtest.py` | Modify — add functions | Add `load_15m()`, `load_1h()`, `load_3m()` cache helpers (same pattern as existing `load_daily()`) |
| `backtest_engine.py` | Create | Full engine: MockTrader, BacktestState, MockScanner, BacktestEngine, main() |
| `tests/test_backtest_engine.py` | Create | Unit tests for MockTrader exit logic, BacktestState, MockScanner, time loop |

---

## Task 1: Add `load_15m()`, `load_1h()`, `load_3m()` to `backtest.py`

**Files:**
- Modify: `backtest.py` (after existing `load_daily()` function, ~line 285)
- Test: `tests/test_backtest_engine.py`

- [ ] **Step 1: Write failing tests for the three new cache loaders**

```python
# tests/test_backtest_engine.py
import pandas as pd
import pytest
from pathlib import Path
from unittest.mock import patch
import sys, types

# ── helpers ──────────────────────────────────────────────────────────
def _make_df(ts_list):
    return pd.DataFrame({
        "ts":    ts_list,
        "open":  [1.0] * len(ts_list),
        "high":  [1.1] * len(ts_list),
        "low":   [0.9] * len(ts_list),
        "close": [1.05] * len(ts_list),
        "vol":   [1000.0] * len(ts_list),
    })


# ── Task 1 tests ─────────────────────────────────────────────────────
def test_load_3m_no_cache_full_fetch(tmp_path, monkeypatch):
    """load_3m() with no cache calls _fetch_candles and saves parquet."""
    import backtest as bt
    monkeypatch.setattr(bt, "_CACHE_3M", tmp_path / "3m")
    DAY = 86_400_000
    MIN3 = 3 * 60 * 1000
    now_ms = 1_774_483_200_000
    ts_list = [now_ms - 30 * DAY + i * MIN3 for i in range(5)]
    df = _make_df(ts_list)

    with patch.object(bt, "_fetch_candles", return_value=df) as mock_fetch:
        result = bt.load_3m("BTCUSDT", days=30, _now_ms=now_ms)

    mock_fetch.assert_called_once()
    assert len(result) == 5
    assert (tmp_path / "3m" / "BTCUSDT.parquet").exists()


def test_load_3m_cache_hit_incremental(tmp_path, monkeypatch):
    """load_3m() with stale cache fetches only missing bars and appends."""
    import backtest as bt
    monkeypatch.setattr(bt, "_CACHE_3M", tmp_path / "3m")
    (tmp_path / "3m").mkdir()
    DAY = 86_400_000
    MIN3 = 3 * 60 * 1000
    now_ms = 1_774_483_200_000
    old_ts = [now_ms - 2 * DAY + i * MIN3 for i in range(3)]
    _make_df(old_ts).to_parquet(tmp_path / "3m" / "BTCUSDT.parquet", index=False)

    new_ts = [old_ts[-1] + MIN3 * (i + 1) for i in range(2)]
    new_df = _make_df(new_ts)
    with patch.object(bt, "_fetch_candles", return_value=new_df):
        result = bt.load_3m("BTCUSDT", days=30, _now_ms=now_ms)

    assert len(result) == 5
    assert result["ts"].max() == new_ts[-1]


def test_load_1h_no_cache(tmp_path, monkeypatch):
    """load_1h() with no cache does a full fetch and saves."""
    import backtest as bt
    monkeypatch.setattr(bt, "_CACHE_1H", tmp_path / "1h")
    now_ms = 1_774_483_200_000
    HOUR = 3_600_000
    ts_list = [now_ms - 10 * HOUR + i * HOUR for i in range(5)]
    df = _make_df(ts_list)
    with patch.object(bt, "_fetch_candles", return_value=df):
        result = bt.load_1h("BTCUSDT", days=5, _now_ms=now_ms)
    assert len(result) == 5
    assert (tmp_path / "1h" / "BTCUSDT.parquet").exists()


def test_load_15m_no_cache(tmp_path, monkeypatch):
    """load_15m() with no cache does a full fetch and saves."""
    import backtest as bt
    monkeypatch.setattr(bt, "_CACHE_15M", tmp_path / "15m")
    now_ms = 1_774_483_200_000
    M15 = 15 * 60 * 1000
    ts_list = [now_ms - 10 * M15 + i * M15 for i in range(5)]
    df = _make_df(ts_list)
    with patch.object(bt, "_fetch_candles", return_value=df):
        result = bt.load_15m("BTCUSDT", days=5, _now_ms=now_ms)
    assert len(result) == 5
    assert (tmp_path / "15m" / "BTCUSDT.parquet").exists()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
source venv/bin/activate && pytest tests/test_backtest_engine.py::test_load_3m_no_cache_full_fetch tests/test_backtest_engine.py::test_load_3m_cache_hit_incremental tests/test_backtest_engine.py::test_load_1h_no_cache tests/test_backtest_engine.py::test_load_15m_no_cache -v 2>&1 | tail -15
```

Expected: FAIL (AttributeError — `_fetch_candles`, `_CACHE_3M` etc. not defined)

- [ ] **Step 3: Implement the cache loaders in `backtest.py`**

Add after the existing `load_daily()` function (after line ~285):

```python
# ── Shared incremental fetch helper ──────────────────────────────── #

def _fetch_candles(sym: str, granularity: str, interval_ms: int,
                   cursor_ms: int, now_ms: int) -> pd.DataFrame:
    """
    Paginate Bitget candle API from cursor_ms to now_ms.
    Returns a DataFrame with columns [ts, open, high, low, close, vol].
    granularity: Bitget string e.g. "3m", "15m", "1H", "1Dutc"
    interval_ms: milliseconds per bar (used to advance cursor after each batch)
    """
    BASE     = "https://api.bitget.com"
    ENDPOINT = "/api/v2/mix/market/candles"
    all_rows = []
    batch    = 0
    while cursor_ms < now_ms:
        params = {
            "symbol":      sym,
            "productType": "usdt-futures",
            "granularity": granularity,
            "startTime":   str(cursor_ms),
            "limit":       "200",
        }
        try:
            resp = requests.get(BASE + ENDPOINT, params=params, timeout=15)
            data = resp.json()
        except Exception as e:
            print(f"  ❌ [{sym}] {granularity} batch {batch} error: {e}")
            break
        rows_raw = data.get("data") or []
        if data.get("code") != "00000" or not rows_raw:
            break
        parsed = []
        for r in rows_raw:
            try:
                parsed.append([int(r[0]), float(r[1]), float(r[2]),
                               float(r[3]), float(r[4]), float(r[5])])
            except (IndexError, ValueError):
                continue
        if not parsed:
            break
        all_rows.extend(parsed)
        batch += 1
        newest_ts = max(r[0] for r in parsed)
        cursor_ms = newest_ts + interval_ms
        if cursor_ms >= now_ms:
            break
        time.sleep(0.1)
    if not all_rows:
        return pd.DataFrame()
    df = pd.DataFrame(all_rows, columns=["ts", "open", "high", "low", "close", "vol"])
    return df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


def _load_or_fetch(cache_dir: Path, sym: str, granularity: str,
                   interval_ms: int, days: int,
                   _now_ms: int | None = None) -> pd.DataFrame:
    """
    Generic incremental parquet cache loader.
    Reads cache_dir/<sym>.parquet, fetches missing bars, saves back.
    Returns DataFrame trimmed to `days` lookback from _now_ms.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    path   = cache_dir / f"{sym}.parquet"
    now_ms = _now_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
    cutoff = now_ms - (days + 2) * 86_400_000

    if path.exists():
        cached = pd.read_parquet(path)
        if set(["ts", "open", "high", "low", "close", "vol"]).issubset(cached.columns) and len(cached):
            last_ts   = int(cached["ts"].max())
            cursor_ms = last_ts + interval_ms
            if cursor_ms < now_ms:
                new_df = _fetch_candles(sym, granularity, interval_ms, cursor_ms, now_ms)
                if not new_df.empty:
                    cached = pd.concat([cached, new_df], ignore_index=True)
                    cached = cached.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
                    cached.to_parquet(path, index=False)
            return cached[cached["ts"] >= cutoff].reset_index(drop=True)

    # No cache — full fetch
    start_ms = now_ms - (days + 2) * 86_400_000
    df = _fetch_candles(sym, granularity, interval_ms, start_ms, now_ms)
    if not df.empty:
        df.to_parquet(path, index=False)
    return df[df["ts"] >= cutoff].reset_index(drop=True) if not df.empty else df


_CACHE_3M  = Path("data/3m")
_CACHE_15M = Path("data/15m")
_CACHE_1H  = Path("data/1h")


def load_3m(sym: str, days: int = 365, _now_ms: int | None = None) -> pd.DataFrame:
    """Load/update 3m candle parquet cache for sym."""
    return _load_or_fetch(_CACHE_3M,  sym, "3m",    3 * 60_000, days, _now_ms)


def load_15m(sym: str, days: int = 365, _now_ms: int | None = None) -> pd.DataFrame:
    """Load/update 15m candle parquet cache for sym."""
    return _load_or_fetch(_CACHE_15M, sym, "15m",  15 * 60_000, days, _now_ms)


def load_1h(sym: str, days: int = 365, _now_ms: int | None = None) -> pd.DataFrame:
    """Load/update 1H candle parquet cache for sym."""
    return _load_or_fetch(_CACHE_1H,  sym, "1H",  60 * 60_000, days, _now_ms)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
source venv/bin/activate && pytest tests/test_backtest_engine.py::test_load_3m_no_cache_full_fetch tests/test_backtest_engine.py::test_load_3m_cache_hit_incremental tests/test_backtest_engine.py::test_load_1h_no_cache tests/test_backtest_engine.py::test_load_15m_no_cache -v 2>&1 | tail -15
```

Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add backtest.py tests/test_backtest_engine.py
git commit -m "feat(backtest): add load_3m/load_15m/load_1h incremental parquet cache helpers"
```

---

## Task 2: `BacktestState` — in-memory state.py mock

**Files:**
- Create: `backtest_engine.py` (first section)
- Test: `tests/test_backtest_engine.py`

- [ ] **Step 1: Write failing tests**

```python
# append to tests/test_backtest_engine.py

def test_backtest_state_open_close_trade():
    """BacktestState add/get/close trade roundtrip."""
    from backtest_engine import BacktestState
    bs = BacktestState()
    bs.add_open_trade({"symbol": "BTCUSDT", "strategy": "S2",
                       "side": "LONG", "entry": 50000.0, "qty": 0.1,
                       "sl": 47500.0, "tp": 55000.0, "trade_id": "abc"})
    ot = bs.get_open_trade("BTCUSDT")
    assert ot["entry"] == 50000.0
    bs.close_trade("BTCUSDT", pnl=100.0, result="WIN",
                   exit_price=55000.0, exit_reason="TRAIL")
    assert bs.get_open_trade("BTCUSDT") is None


def test_backtest_state_pair_pause():
    """BacktestState record_loss triggers is_pair_paused after 3 losses same day."""
    from backtest_engine import BacktestState
    bs = BacktestState()
    today = "2026-04-12"
    assert not bs.is_pair_paused("BTCUSDT", today)
    bs.record_loss("BTCUSDT", today)
    bs.record_loss("BTCUSDT", today)
    assert not bs.is_pair_paused("BTCUSDT", today)
    bs.record_loss("BTCUSDT", today)
    assert bs.is_pair_paused("BTCUSDT", today)


def test_backtest_state_position_memory():
    """BacktestState update/get/clear position memory."""
    from backtest_engine import BacktestState
    bs = BacktestState()
    bs.update_position_memory("ETHUSDT", initial_qty=10.0, partial_logged=False)
    mem = bs.get_position_memory("ETHUSDT")
    assert mem["initial_qty"] == 10.0
    bs.clear_position_memory("ETHUSDT")
    assert bs.get_position_memory("ETHUSDT") == {}
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
source venv/bin/activate && pytest tests/test_backtest_engine.py::test_backtest_state_open_close_trade tests/test_backtest_engine.py::test_backtest_state_pair_pause tests/test_backtest_engine.py::test_backtest_state_position_memory -v 2>&1 | tail -15
```

Expected: FAIL (ImportError — `backtest_engine` not found)

- [ ] **Step 3: Create `backtest_engine.py` with `BacktestState`**

```python
"""
backtest_engine.py — Unified backtesting harness for S1–S6

Monkey-patches trader/state/scanner/snapshot/claude_filter/startup_recovery
with in-memory mocks, then runs MTFBot._tick() against historical parquet data.
"""

import sys
import uuid
import time as _time_mod
import logging
import argparse
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# BacktestState — in-memory replacement for state.py
# ═══════════════════════════════════════════════════════════════════

class BacktestState:
    """Drop-in mock for state.py — all writes go to in-memory dicts."""

    def __init__(self):
        self._open_trades: dict[str, dict] = {}
        self._position_memory: dict[str, dict] = {}
        self._pair_states: dict[str, dict] = {}
        self._stats = {"wins": 0, "losses": 0, "total_pnl": 0.0}
        self._loss_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._pending_signals: dict = {}
        self.qualified_pairs: list = []
        self.sentiment = None
        self.balance: float = 0.0
        self.closed_trades: list[dict] = []

    # ── Trade management ──────────────────────────────────────────── #

    def add_open_trade(self, trade: dict) -> None:
        self._open_trades[trade["symbol"]] = dict(trade)

    def get_open_trade(self, symbol: str) -> dict | None:
        return self._open_trades.get(symbol)

    def get_open_trades(self) -> list[dict]:
        return list(self._open_trades.values())

    def close_trade(self, symbol: str, pnl: float, result: str,
                    exit_price: float, exit_reason: str) -> None:
        ot = self._open_trades.pop(symbol, None)
        if ot:
            ot.update({"pnl": pnl, "result": result,
                        "exit_price": exit_price, "exit_reason": exit_reason})
            self.closed_trades.append(ot)

    def update_open_trade_margin(self, symbol: str, margin: float) -> None:
        if symbol in self._open_trades:
            self._open_trades[symbol]["margin"] = margin

    def update_open_trade_pnl(self, symbol: str, pnl: float) -> None:
        if symbol in self._open_trades:
            self._open_trades[symbol]["unrealised_pnl"] = pnl

    def update_open_trade_mark_price(self, symbol: str, price: float) -> None:
        if symbol in self._open_trades:
            self._open_trades[symbol]["mark_price"] = price

    def update_open_trade_sl(self, symbol: str, sl: float) -> None:
        if symbol in self._open_trades:
            self._open_trades[symbol]["sl"] = sl

    def update_open_trade_leverage(self, symbol: str, lev: int) -> None:
        if symbol in self._open_trades:
            self._open_trades[symbol]["leverage"] = lev

    def patch_pair_state(self, symbol: str, **kwargs) -> None:
        self._pair_states.setdefault(symbol, {}).update(kwargs)

    def update_pair_state(self, symbol: str, data: dict) -> None:
        self._pair_states.setdefault(symbol, {}).update(data)

    def get_pair_state(self, symbol: str) -> dict:
        return self._pair_states.get(symbol, {})

    # ── Stats / pause ─────────────────────────────────────────────── #

    def set_stats(self, wins: int, losses: int, total_pnl: float,
                  pnl_pct: float = 0.0) -> None:
        self._stats = {"wins": wins, "losses": losses, "total_pnl": total_pnl}

    def record_loss(self, symbol: str, day_str: str) -> None:
        self._loss_counts[symbol][day_str] += 1

    def is_pair_paused(self, symbol: str, day_str: str | None = None) -> bool:
        if day_str is None:
            day_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self._loss_counts[symbol][day_str] >= 3

    # ── Position memory ───────────────────────────────────────────── #

    def update_position_memory(self, symbol: str, **kwargs) -> None:
        self._position_memory.setdefault(symbol, {}).update(kwargs)

    def get_position_memory(self, symbol: str) -> dict:
        return self._position_memory.get(symbol, {})

    def clear_position_memory(self, symbol: str) -> None:
        self._position_memory.pop(symbol, None)

    # ── Pending signals ───────────────────────────────────────────── #

    def load_pending_signals(self) -> dict:
        return {}

    def save_pending_signals(self, signals: dict) -> None:
        self._pending_signals = signals

    # ── Bot infra (all no-ops or trivial) ────────────────────────── #

    def reset(self) -> None:
        pass

    def set_status(self, status: str) -> None:
        pass

    def set_file(self, path: str) -> None:
        pass

    def add_scan_log(self, msg: str, level: str = "INFO") -> None:
        pass

    def update_balance(self, balance: float) -> None:
        self.balance = balance

    def update_sentiment(self, sentiment) -> None:
        self.sentiment = sentiment

    def update_qualified_pairs(self, pairs: list) -> None:
        self.qualified_pairs = pairs

    def _read(self) -> dict:
        return {"open_trades": list(self._open_trades.values())}

    def _write(self, data: dict) -> None:
        pass
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
source venv/bin/activate && pytest tests/test_backtest_engine.py::test_backtest_state_open_close_trade tests/test_backtest_engine.py::test_backtest_state_pair_pause tests/test_backtest_engine.py::test_backtest_state_position_memory -v 2>&1 | tail -15
```

Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add backtest_engine.py tests/test_backtest_engine.py
git commit -m "feat(backtest-engine): add BacktestState in-memory state.py mock"
```

---

## Task 3: `MockTrader` — position management and exit simulation

**Files:**
- Modify: `backtest_engine.py` (append after BacktestState)
- Test: `tests/test_backtest_engine.py`

- [ ] **Step 1: Write failing tests**

```python
# append to tests/test_backtest_engine.py

def _make_mock_trader(balance=1000.0):
    """Helper: build a MockTrader with empty parquet dicts."""
    from backtest_engine import MockTrader
    return MockTrader(universe=["BTCUSDT"], parquet={}, balance=balance)


def test_mock_trader_open_long_records_position():
    """open_long() records position with correct SL, TP, qty."""
    mt = _make_mock_trader(balance=1000.0)
    mt.sim_time = 1_774_483_200_000
    result = mt.open_long(
        "BTCUSDT",
        sl_floor=45000.0,
        leverage=10,
        trade_size_pct=0.04,
        use_s2_exits=True,
    )
    pos = mt._positions["BTCUSDT"]
    assert pos["side"] == "LONG"
    assert pos["sl"] == 45000.0
    assert pos["leverage"] == 10
    assert result["side"] == "LONG"


def test_mock_trader_sl_hit_closes_position():
    """process_bar() closes LONG when low <= sl."""
    mt = _make_mock_trader(balance=1000.0)
    mt.sim_time = 1_774_483_200_000
    mt._positions["BTCUSDT"] = {
        "side": "LONG", "entry": 50000.0, "qty": 0.04,
        "initial_qty": 0.04, "sl": 47000.0, "tp_trig": 55000.0,
        "trail_pct": 10.0, "trail_active": False, "trail_peak": 0.0,
        "trail_sl": 0.0, "partial_done": False,
        "scale_in_after": 0, "scale_in_done": True,
        "margin": 40.0, "leverage": 10, "strategy": "S2",
        "trade_id": "test1", "open_ts": mt.sim_time,
    }
    bar = {"ts": mt.sim_time, "open": 47500.0, "high": 47800.0,
           "low": 46500.0, "close": 47000.0}
    closed = mt.process_bar("BTCUSDT", bar)
    assert closed is not None
    assert closed["result"] == "LOSS"
    assert closed["exit_price"] == 47000.0
    assert "BTCUSDT" not in mt._positions


def test_mock_trader_partial_tp_then_trail():
    """process_bar() fires partial TP then tracks trailing stop."""
    mt = _make_mock_trader(balance=1000.0)
    mt.sim_time = 1_774_483_200_000
    mt._positions["BTCUSDT"] = {
        "side": "LONG", "entry": 50000.0, "qty": 0.04,
        "initial_qty": 0.04, "sl": 45000.0, "tp_trig": 55000.0,
        "trail_pct": 10.0, "trail_active": False, "trail_peak": 0.0,
        "trail_sl": 0.0, "partial_done": False,
        "scale_in_after": 0, "scale_in_done": True,
        "margin": 40.0, "leverage": 10, "strategy": "S2",
        "trade_id": "test2", "open_ts": mt.sim_time,
    }
    # Bar 1: TP trigger hit → partial TP fires
    bar1 = {"ts": mt.sim_time, "open": 54000.0, "high": 56000.0,
             "low": 53000.0, "close": 55000.0}
    closed = mt.process_bar("BTCUSDT", bar1)
    assert closed is None  # partial, not full close
    pos = mt._positions["BTCUSDT"]
    assert pos["partial_done"] is True
    assert pos["trail_active"] is True
    assert pos["qty"] == pytest.approx(0.02, rel=1e-3)

    # Bar 2: price rises more, trail_peak advances
    bar2 = {"ts": mt.sim_time + 180_000, "open": 57000.0, "high": 60000.0,
             "low": 56000.0, "close": 59000.0}
    closed = mt.process_bar("BTCUSDT", bar2)
    assert closed is None
    assert mt._positions["BTCUSDT"]["trail_peak"] == 60000.0

    # Bar 3: trail SL is 60000 * 0.90 = 54000. Low dips to 53500 → trail hit
    bar3 = {"ts": mt.sim_time + 360_000, "open": 55000.0, "high": 55500.0,
             "low": 53500.0, "close": 54000.0}
    closed = mt.process_bar("BTCUSDT", bar3)
    assert closed is not None
    assert closed["result"] == "WIN"
    assert closed["exit_reason"] == "TRAIL"


def test_mock_trader_sl_beats_tp_same_bar():
    """When SL and TP both hit in same bar, SL wins (conservative)."""
    mt = _make_mock_trader(balance=1000.0)
    mt.sim_time = 1_774_483_200_000
    mt._positions["BTCUSDT"] = {
        "side": "LONG", "entry": 50000.0, "qty": 0.04,
        "initial_qty": 0.04, "sl": 45000.0, "tp_trig": 55000.0,
        "trail_pct": 10.0, "trail_active": False, "trail_peak": 0.0,
        "trail_sl": 0.0, "partial_done": False,
        "scale_in_after": 0, "scale_in_done": True,
        "margin": 40.0, "leverage": 10, "strategy": "S2",
        "trade_id": "test3", "open_ts": mt.sim_time,
    }
    bar = {"ts": mt.sim_time, "open": 50000.0, "high": 56000.0,
           "low": 44000.0, "close": 50000.0}
    closed = mt.process_bar("BTCUSDT", bar)
    assert closed["result"] == "LOSS"


def test_mock_trader_short_sl_and_trail():
    """SHORT: SL hit when high >= sl; trail fires when trail_sl breached."""
    mt = _make_mock_trader(balance=1000.0)
    mt.sim_time = 1_774_483_200_000
    mt._positions["BTCUSDT"] = {
        "side": "SHORT", "entry": 50000.0, "qty": 0.04,
        "initial_qty": 0.04, "sl": 53000.0, "tp_trig": 45000.0,
        "trail_pct": 10.0, "trail_active": False, "trail_peak": 0.0,
        "trail_sl": 0.0, "partial_done": False,
        "scale_in_after": 0, "scale_in_done": True,
        "margin": 40.0, "leverage": 10, "strategy": "S4",
        "trade_id": "test4", "open_ts": mt.sim_time,
    }
    # Partial TP
    bar1 = {"ts": mt.sim_time, "open": 46000.0, "high": 46500.0,
             "low": 44000.0, "close": 45000.0}
    closed = mt.process_bar("BTCUSDT", bar1)
    assert closed is None
    assert mt._positions["BTCUSDT"]["trail_active"] is True

    # Trail SL for SHORT = trail_peak * (1 + trail_pct/100)
    # trail_peak = min(trail_peak, low) → 44000. trail_sl = 44000 * 1.10 = 48400
    # high = 49000 > 48400 → trail hit
    bar2 = {"ts": mt.sim_time + 180_000, "open": 45000.0, "high": 49000.0,
             "low": 44000.0, "close": 48000.0}
    closed = mt.process_bar("BTCUSDT", bar2)
    assert closed is not None
    assert closed["result"] == "WIN"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
source venv/bin/activate && pytest tests/test_backtest_engine.py::test_mock_trader_open_long_records_position tests/test_backtest_engine.py::test_mock_trader_sl_hit_closes_position tests/test_backtest_engine.py::test_mock_trader_partial_tp_then_trail tests/test_backtest_engine.py::test_mock_trader_sl_beats_tp_same_bar tests/test_backtest_engine.py::test_mock_trader_short_sl_and_trail -v 2>&1 | tail -15
```

Expected: FAIL (ImportError — MockTrader not defined)

- [ ] **Step 3: Implement `MockTrader` in `backtest_engine.py`**

Append to `backtest_engine.py`:

```python
# ═══════════════════════════════════════════════════════════════════
# MockTrader — replacement for trader.py
# ═══════════════════════════════════════════════════════════════════

class MockTrader:
    """
    Fake trader.py. Implements the full public interface used by bot.py.
    Feeds candle data from parquet dicts, simulates positions in memory.

    parquet: dict[symbol] = {
        "3m":  pd.DataFrame,
        "15m": pd.DataFrame,
        "1h":  pd.DataFrame,
        "1d":  pd.DataFrame,
    }
    """

    def __init__(self, universe: list[str], parquet: dict[str, dict],
                 balance: float = 1000.0):
        self.universe    = universe
        self.parquet     = parquet   # sym → {"3m": df, "15m": df, "1h": df, "1d": df}
        self._balance    = balance
        self.sim_time: int = 0       # current simulation epoch ms

        self._positions: dict[str, dict]  = {}
        self._pending_orders: dict[str, dict] = {}   # order_id → order
        self._closed_trades: list[dict]   = []
        self._partial_events: list[dict]  = []        # drained by bot._tick() (PAPER path, kept for symmetry)

    # ── Time-sliced candle access ─────────────────────────────────── #

    def _slice(self, symbol: str, tf: str, limit: int) -> pd.DataFrame:
        """Return last `limit` candles for symbol/timeframe up to sim_time."""
        df = self.parquet.get(symbol, {}).get(tf)
        if df is None or df.empty:
            return pd.DataFrame()
        sliced = df[df["ts"] <= self.sim_time]
        return sliced.tail(limit).reset_index(drop=True)

    def get_candles(self, symbol: str, interval: str, limit: int = 100) -> pd.DataFrame:
        tf_map = {
            "3m": "3m", "15m": "15m",
            "1H": "1h", "1h": "1h",
            "1D": "1d", "1d": "1d", "1Dutc": "1d",
        }
        tf = tf_map.get(interval, interval.lower())
        return self._slice(symbol, tf, limit)

    def get_daily_candles_utc(self, symbol: str, limit: int = 100) -> pd.DataFrame:
        return self._slice(symbol, "1d", limit)

    def get_mark_price(self, symbol: str) -> float:
        df = self._slice(symbol, "3m", 1)
        if df.empty:
            return 0.0
        return float(df.iloc[-1]["close"])

    # ── Account ───────────────────────────────────────────────────── #

    def get_usdt_balance(self) -> float:
        return self._balance

    def _get_total_equity(self) -> float:
        return self._balance

    def _update_balance(self, pnl: float) -> None:
        self._balance += pnl

    # ── Rounding helpers (passthrough — no symbol precision needed in sim) #

    def _round_price(self, price: float, symbol: str) -> float:
        return round(price, 6)

    def _round_qty(self, qty: float, symbol: str) -> float:
        return round(qty, 4)

    # ── Positions ─────────────────────────────────────────────────── #

    def get_all_open_positions(self) -> dict[str, dict]:
        """Return exchange-style position dict for each open position."""
        result = {}
        for sym, pos in self._positions.items():
            result[sym] = {
                "side":           pos["side"],
                "entry_price":    pos["entry"],
                "qty":            pos["qty"],
                "unrealised_pnl": self._unrealised_pnl(pos),
                "mark_price":     self.get_mark_price(sym),
                "margin":         pos["margin"],
                "leverage":       pos["leverage"],
            }
        return result

    def _unrealised_pnl(self, pos: dict) -> float:
        mark = self.get_mark_price(pos.get("_symbol", ""))
        if mark == 0:
            return 0.0
        direction = 1 if pos["side"] == "LONG" else -1
        return direction * (mark - pos["entry"]) / pos["entry"] * pos["margin"] * pos["leverage"]

    def _calc_qty(self, symbol: str, trade_size_pct: float, leverage: int) -> tuple[float, float]:
        """Returns (qty, margin)."""
        mark   = self.get_mark_price(symbol)
        margin = self._balance * trade_size_pct
        qty    = self._round_qty((margin * leverage) / mark, symbol) if mark > 0 else 0.0
        return qty, margin

    def _open_position(self, symbol: str, side: str,
                       sl: float, tp_trig: float, trail_pct: float,
                       leverage: int, trade_size_pct: float,
                       size_multiplier: float = 1.0,
                       scale_in: bool = False) -> dict:
        """Core position-open logic. size_multiplier=0.5 for scale-in initial."""
        mark   = self.get_mark_price(symbol)
        qty, margin = self._calc_qty(symbol, trade_size_pct * size_multiplier, leverage)
        trade_id = uuid.uuid4().hex[:8]

        self._positions[symbol] = {
            "_symbol":       symbol,
            "side":          side,
            "entry":         mark,
            "qty":           qty,
            "initial_qty":   qty,
            "sl":            sl,
            "tp_trig":       tp_trig,
            "trail_pct":     trail_pct,
            "trail_active":  False,
            "trail_peak":    0.0,
            "trail_sl":      0.0,
            "partial_done":  False,
            "scale_in_after": self.sim_time + 3_600_000 if scale_in else 0,
            "scale_in_done": not scale_in,
            "margin":        margin,
            "leverage":      leverage,
            "strategy":      "",
            "trade_id":      trade_id,
            "open_ts":       self.sim_time,
        }
        return {
            "symbol": symbol, "side": side, "qty": str(qty),
            "entry":  mark,   "sl":   sl,   "tp":   tp_trig,
            "box_low": 0.0, "leverage": leverage,
            "margin": margin, "tpsl_set": True,
        }

    def open_long(self, symbol: str, box_low: float = 0, sl_floor: float = 0,
                  leverage: int = 10, trade_size_pct: float = 0.04,
                  take_profit_pct: float = 0.10, stop_loss_pct: float = 0.05,
                  use_s1_exits: bool = False, use_s2_exits: bool = False,
                  use_s3_exits: bool = False, use_s5_exits: bool = False,
                  tp_price_abs: float = 0) -> dict:
        mark = self.get_mark_price(symbol)
        # Determine SL
        if sl_floor > 0:
            sl = sl_floor
        elif box_low > 0:
            sl = max(box_low * 0.999, mark * (1 - stop_loss_pct))
        else:
            sl = mark * (1 - stop_loss_pct)

        # Determine TP trigger and trail pct
        if use_s5_exits:
            from config_s5 import S5_TRAIL_RANGE_PCT
            one_r    = mark - sl
            tp_trig  = mark + one_r
            trail_pct = S5_TRAIL_RANGE_PCT
        elif use_s1_exits:
            from config_s1 import S1_TRAIL_RANGE_PCT, TAKE_PROFIT_PCT
            tp_trig  = mark * (1 + TAKE_PROFIT_PCT)
            trail_pct = S1_TRAIL_RANGE_PCT
        elif use_s2_exits:
            from config_s2 import S2_TRAILING_TRIGGER_PCT, S2_TRAILING_RANGE_PCT
            tp_trig  = mark * (1 + S2_TRAILING_TRIGGER_PCT)
            trail_pct = S2_TRAILING_RANGE_PCT
        elif use_s3_exits:
            from config_s3 import S3_TRAILING_TRIGGER_PCT, S3_TRAILING_RANGE_PCT
            tp_trig  = mark * (1 + S3_TRAILING_TRIGGER_PCT)
            trail_pct = S3_TRAILING_RANGE_PCT
        else:
            tp_trig  = tp_price_abs if tp_price_abs > mark else mark * (1 + take_profit_pct)
            trail_pct = 10.0

        # S2/S4/S6 start at 50% size with scale-in queued
        needs_scale_in = use_s2_exits
        size_mult = 0.5 if needs_scale_in else 1.0
        return self._open_position(symbol, "LONG", sl, tp_trig, trail_pct,
                                   leverage, trade_size_pct, size_mult, needs_scale_in)

    def open_short(self, symbol: str, box_high: float = 0, sl_floor: float = 0,
                   leverage: int = 10, trade_size_pct: float = 0.04,
                   take_profit_pct: float = 0.10,
                   use_s1_exits: bool = False, use_s4_exits: bool = False,
                   use_s5_exits: bool = False, use_s6_exits: bool = False,
                   tp_price_abs: float = 0) -> dict:
        mark = self.get_mark_price(symbol)
        # SL
        if sl_floor > 0:
            sl = sl_floor
        elif box_high > 0:
            sl = box_high * 1.001
        else:
            sl = mark * (1 + 0.05)

        # TP trigger and trail pct
        if use_s5_exits:
            from config_s5 import S5_TRAIL_RANGE_PCT
            one_r    = sl - mark
            tp_trig  = mark - one_r
            trail_pct = S5_TRAIL_RANGE_PCT
        elif use_s1_exits:
            from config_s1 import S1_TRAIL_RANGE_PCT, TAKE_PROFIT_PCT
            tp_trig  = mark * (1 - TAKE_PROFIT_PCT)
            trail_pct = S1_TRAIL_RANGE_PCT
        elif use_s4_exits:
            from config_s4 import S4_TRAILING_TRIGGER_PCT, S4_TRAILING_RANGE_PCT
            tp_trig  = mark * (1 - S4_TRAILING_TRIGGER_PCT)
            trail_pct = S4_TRAILING_RANGE_PCT
        elif use_s6_exits:
            from config_s6 import S6_TRAILING_TRIGGER_PCT, S6_TRAIL_RANGE_PCT
            tp_trig  = mark * (1 - S6_TRAILING_TRIGGER_PCT)
            trail_pct = S6_TRAIL_RANGE_PCT
        else:
            tp_trig  = tp_price_abs if 0 < tp_price_abs < mark else mark * (1 - take_profit_pct)
            trail_pct = 10.0

        needs_scale_in = use_s4_exits or use_s6_exits
        size_mult = 0.5 if needs_scale_in else 1.0
        return self._open_position(symbol, "SHORT", sl, tp_trig, trail_pct,
                                   leverage, trade_size_pct, size_mult, needs_scale_in)

    def scale_in_long(self, symbol: str, additional_trade_size_pct: float,
                      leverage: int) -> None:
        if symbol not in self._positions:
            return
        pos    = self._positions[symbol]
        mark   = self.get_mark_price(symbol)
        extra_qty, extra_margin = self._calc_qty(symbol, additional_trade_size_pct, leverage)
        old_qty = pos["qty"]
        new_qty = old_qty + extra_qty
        avg_entry = (pos["entry"] * old_qty + mark * extra_qty) / new_qty
        pos["qty"]          = new_qty
        pos["initial_qty"]  = new_qty
        pos["entry"]        = avg_entry
        pos["margin"]      += extra_margin
        pos["scale_in_done"] = True
        # Recalculate TP trigger from new avg entry
        trail_trig = avg_entry * (1 + pos["tp_trig"] / pos["entry"] - 1)
        pos["tp_trig"] = avg_entry * (pos["tp_trig"] / pos["entry"])

    def scale_in_short(self, symbol: str, additional_trade_size_pct: float,
                       leverage: int) -> None:
        if symbol not in self._positions:
            return
        pos    = self._positions[symbol]
        mark   = self.get_mark_price(symbol)
        extra_qty, extra_margin = self._calc_qty(symbol, additional_trade_size_pct, leverage)
        old_qty = pos["qty"]
        new_qty = old_qty + extra_qty
        avg_entry = (pos["entry"] * old_qty + mark * extra_qty) / new_qty
        pos["qty"]          = new_qty
        pos["initial_qty"]  = new_qty
        pos["entry"]        = avg_entry
        pos["margin"]      += extra_margin
        pos["scale_in_done"] = True
        pos["tp_trig"] = avg_entry * (pos["tp_trig"] / pos["entry"])

    # ── Exit order management ─────────────────────────────────────── #

    def refresh_plan_exits(self, symbol: str, hold_side: str,
                           new_trail_trigger: float = 0) -> bool:
        if symbol in self._positions and new_trail_trigger > 0:
            self._positions[symbol]["tp_trig"] = new_trail_trigger
        return True

    def update_position_sl(self, symbol: str, new_sl: float,
                           hold_side: str = "long") -> bool:
        if symbol in self._positions:
            pos = self._positions[symbol]
            if pos["side"] == "LONG" and new_sl > pos["sl"]:
                pos["sl"] = new_sl
            elif pos["side"] == "SHORT" and new_sl < pos["sl"]:
                pos["sl"] = new_sl
            return True
        return False

    def cancel_all_orders(self, symbol: str) -> None:
        pass

    # ── Limit orders (S5) ─────────────────────────────────────────── #

    def place_limit_long(self, symbol: str, limit_price: float,
                         sl_price: float, tp_price: float,
                         qty_str: str) -> str:
        order_id = uuid.uuid4().hex[:8]
        self._pending_orders[order_id] = {
            "symbol": symbol, "side": "LONG",
            "limit_price": limit_price, "sl": sl_price,
            "tp": tp_price, "qty_str": qty_str,
            "placed_ts": self.sim_time,
        }
        return order_id

    def place_limit_short(self, symbol: str, limit_price: float,
                          sl_price: float, tp_price: float,
                          qty_str: str) -> str:
        order_id = uuid.uuid4().hex[:8]
        self._pending_orders[order_id] = {
            "symbol": symbol, "side": "SHORT",
            "limit_price": limit_price, "sl": sl_price,
            "tp": tp_price, "qty_str": qty_str,
            "placed_ts": self.sim_time,
        }
        return order_id

    def cancel_order(self, symbol: str, order_id: str) -> None:
        self._pending_orders.pop(order_id, None)

    def get_order_fill(self, symbol: str, order_id: str) -> dict:
        """Check if current bar crossed the limit price."""
        order = self._pending_orders.get(order_id)
        if not order:
            return {"status": "cancelled", "fill_price": 0.0}
        mark = self.get_mark_price(symbol)
        lp   = order["limit_price"]
        if order["side"] == "LONG" and mark <= lp:
            return {"status": "filled", "fill_price": lp}
        if order["side"] == "SHORT" and mark >= lp:
            return {"status": "filled", "fill_price": lp}
        return {"status": "live", "fill_price": 0.0}

    # ── S5 exits (called by _handle_limit_filled in non-paper path) ── #

    def _place_s5_exits(self, symbol: str, hold_side: str, qty_str: str,
                        sl_trig: float, sl_exec: float,
                        part_trig: float, tp_targ: float,
                        trail_range_pct: float) -> bool:
        """Store S5 exit params into the position after limit fill."""
        if symbol in self._positions:
            pos = self._positions[symbol]
            pos["sl"]       = sl_trig
            pos["tp_trig"]  = part_trig
            pos["trail_pct"] = trail_range_pct
        return True

    # ── History (used by bot.py close detection in non-paper path) ── #

    def get_history_position(self, symbol: str,
                              open_time_iso: str | None = None,
                              entry_price: float | None = None,
                              retries: int = 1,
                              retry_delay: float = 0) -> dict | None:
        """Return last closed trade's PnL for symbol."""
        for t in reversed(self._closed_trades):
            if t.get("symbol") == symbol:
                return {"pnl": t.get("total_pnl", 0.0),
                        "exit_price": t.get("exit_price"),
                        "close_time": t.get("exit_date")}
        return None

    def get_realized_pnl(self, symbol: str, retries: int = 1,
                         retry_delay: float = 0) -> float | None:
        for t in reversed(self._closed_trades):
            if t.get("symbol") == symbol:
                return t.get("total_pnl", 0.0)
        return None

    def is_partial_closed(self, symbol: str) -> bool:
        return False

    def set_leverage(self, symbol: str, leverage: int) -> None:
        pass

    def drain_partial_closes(self) -> list[dict]:
        """PAPER_MODE path — not called in non-paper backtest mode."""
        return []

    def get_last_close(self, symbol: str) -> dict | None:
        """PAPER_MODE path — not called in non-paper backtest mode."""
        return None

    def tag_strategy(self, symbol: str, strategy: str) -> None:
        """PAPER_MODE path — not called in non-paper backtest mode."""
        pass

    # ── Exit simulation (called per 3m bar, before bot._tick()) ──── #

    def process_bar(self, symbol: str, bar: dict) -> dict | None:
        """
        Simulate exits for symbol against the given OHLCV bar.
        Returns a closed-trade dict if the position closed, else None.
        Also fires partial TP (modifies position in-place, returns None).
        """
        pos = self._positions.get(symbol)
        if pos is None:
            return None

        o = float(bar["open"])
        h = float(bar["high"])
        l = float(bar["low"])
        c = float(bar["close"])
        side = pos["side"]

        def _close(exit_price: float, result: str, reason: str) -> dict:
            pnl_dir = 1 if side == "LONG" else -1
            close_pct  = (exit_price - pos["entry"]) / pos["entry"] * pnl_dir
            close_pnl  = round(close_pct * pos["margin"] * 0.5 * pos["leverage"], 4)
            partial_pnl = pos.get("_partial_pnl", 0.0)
            total_pnl  = round(close_pnl + partial_pnl, 4)
            self._update_balance(total_pnl)
            trade = {
                "symbol":       symbol,
                "strategy":     pos["strategy"],
                "side":         side,
                "entry_price":  pos["entry"],
                "exit_price":   exit_price,
                "sl":           pos["sl"],
                "tp_trig":      pos["tp_trig"],
                "result":       result,
                "exit_reason":  reason,
                "partial_pnl":  partial_pnl,
                "close_pnl":    close_pnl,
                "total_pnl":    total_pnl,
                "margin_pnl_pct": round(total_pnl / pos["margin"] * 100, 2) if pos["margin"] else 0,
                "scale_in":     pos["scale_in_done"] and not pos.get("_no_scale"),
                "candles_held": (bar["ts"] - pos["open_ts"]) // (3 * 60_000),
                "entry_date":   datetime.fromtimestamp(pos["open_ts"] / 1000, tz=timezone.utc).isoformat(),
                "exit_date":    datetime.fromtimestamp(bar["ts"] / 1000, tz=timezone.utc).isoformat(),
                "margin":       pos["margin"],
                "leverage":     pos["leverage"],
                "trade_id":     pos["trade_id"],
            }
            self._closed_trades.append(trade)
            del self._positions[symbol]
            return trade

        if side == "LONG":
            # SL and TP same bar → SL wins
            sl_hit = l <= pos["sl"]
            tp_hit = h >= pos["tp_trig"] and not pos["partial_done"]

            if sl_hit:
                return _close(pos["sl"], "LOSS", "SL")

            if tp_hit:
                # Partial TP: close 50%, activate trail
                partial_exit = pos["tp_trig"]
                pct = (partial_exit - pos["entry"]) / pos["entry"]
                partial_pnl = round(pct * pos["margin"] * 0.5 * pos["leverage"], 4)
                self._update_balance(partial_pnl)
                pos["_partial_pnl"] = partial_pnl
                pos["partial_done"] = True
                pos["trail_active"] = True
                pos["trail_peak"]   = partial_exit
                pos["trail_sl"]     = partial_exit * (1 - pos["trail_pct"] / 100)
                pos["qty"]          = round(pos["qty"] * 0.5, 6)
                pos["margin"]       = pos["margin"] * 0.5
                return None  # partial — position still open

            if pos["trail_active"]:
                pos["trail_peak"] = max(pos["trail_peak"], h)
                pos["trail_sl"]   = pos["trail_peak"] * (1 - pos["trail_pct"] / 100)
                if l <= pos["trail_sl"]:
                    return _close(pos["trail_sl"], "WIN", "TRAIL")

        else:  # SHORT
            sl_hit = h >= pos["sl"]
            tp_hit = l <= pos["tp_trig"] and not pos["partial_done"]

            if sl_hit:
                return _close(pos["sl"], "LOSS", "SL")

            if tp_hit:
                partial_exit = pos["tp_trig"]
                pct = (pos["entry"] - partial_exit) / pos["entry"]
                partial_pnl = round(pct * pos["margin"] * 0.5 * pos["leverage"], 4)
                self._update_balance(partial_pnl)
                pos["_partial_pnl"] = partial_pnl
                pos["partial_done"] = True
                pos["trail_active"] = True
                pos["trail_peak"]   = partial_exit
                pos["trail_sl"]     = partial_exit * (1 + pos["trail_pct"] / 100)
                pos["qty"]          = round(pos["qty"] * 0.5, 6)
                pos["margin"]       = pos["margin"] * 0.5
                return None

            if pos["trail_active"]:
                pos["trail_peak"] = min(pos["trail_peak"], l)
                pos["trail_sl"]   = pos["trail_peak"] * (1 + pos["trail_pct"] / 100)
                if h >= pos["trail_sl"]:
                    return _close(pos["trail_sl"], "WIN", "TRAIL")

        return None
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
source venv/bin/activate && pytest tests/test_backtest_engine.py::test_mock_trader_open_long_records_position tests/test_backtest_engine.py::test_mock_trader_sl_hit_closes_position tests/test_backtest_engine.py::test_mock_trader_partial_tp_then_trail tests/test_backtest_engine.py::test_mock_trader_sl_beats_tp_same_bar tests/test_backtest_engine.py::test_mock_trader_short_sl_and_trail -v 2>&1 | tail -15
```

Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
git add backtest_engine.py tests/test_backtest_engine.py
git commit -m "feat(backtest-engine): add MockTrader with position management and exit simulation"
```

---

## Task 4: `MockScanner` and module stubs

**Files:**
- Modify: `backtest_engine.py` (append)
- Test: `tests/test_backtest_engine.py`

- [ ] **Step 1: Write failing tests**

```python
# append to tests/test_backtest_engine.py

def test_mock_scanner_sentiment_bullish():
    """MockScanner returns BULLISH when >60% symbols above daily open."""
    from backtest_engine import MockScanner
    DAY = 86_400_000
    now_ms = 1_774_483_200_000
    day_open_ts = now_ms - (now_ms % DAY)  # midnight UTC

    def make_df_3m(close_vs_open: float) -> pd.DataFrame:
        return pd.DataFrame({
            "ts":    [now_ms],
            "open":  [100.0],
            "high":  [110.0],
            "low":   [90.0],
            "close": [100.0 * close_vs_open],
            "vol":   [1000.0],
        })

    def make_df_1d(open_price: float) -> pd.DataFrame:
        return pd.DataFrame({
            "ts":    [day_open_ts],
            "open":  [open_price],
            "high":  [110.0],
            "low":   [90.0],
            "close": [open_price],
            "vol":   [1000.0],
        })

    universe = [f"SYM{i}USDT" for i in range(10)]
    parquet = {}
    # 8 above daily open (close > open), 2 below → 80% bullish → BULLISH
    for i, sym in enumerate(universe):
        multiplier = 1.05 if i < 8 else 0.95
        parquet[sym] = {
            "3m": make_df_3m(multiplier),
            "1d": make_df_1d(100.0),
        }

    scanner = MockScanner(universe, parquet)
    scanner.sim_time = now_ms
    pairs, sentiment = scanner.get_qualified_pairs_and_sentiment()
    assert pairs == universe
    assert sentiment.direction == "BULLISH"


def test_mock_scanner_sentiment_bearish():
    """MockScanner returns BEARISH when <40% symbols above daily open."""
    from backtest_engine import MockScanner
    now_ms = 1_774_483_200_000
    DAY = 86_400_000
    day_open_ts = now_ms - (now_ms % DAY)

    universe = [f"SYM{i}USDT" for i in range(10)]
    parquet = {}
    for i, sym in enumerate(universe):
        multiplier = 1.05 if i < 3 else 0.95  # only 3/10 = 30% above → BEARISH
        parquet[sym] = {
            "3m": pd.DataFrame({"ts": [now_ms], "open": [100.0], "high": [110.0],
                                 "low": [90.0], "close": [100.0 * multiplier], "vol": [1000.0]}),
            "1d": pd.DataFrame({"ts": [day_open_ts], "open": [100.0], "high": [110.0],
                                 "low": [90.0], "close": [100.0], "vol": [1000.0]}),
        }

    scanner = MockScanner(universe, parquet)
    scanner.sim_time = now_ms
    _, sentiment = scanner.get_qualified_pairs_and_sentiment()
    assert sentiment.direction == "BEARISH"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
source venv/bin/activate && pytest tests/test_backtest_engine.py::test_mock_scanner_sentiment_bullish tests/test_backtest_engine.py::test_mock_scanner_sentiment_bearish -v 2>&1 | tail -15
```

Expected: FAIL (ImportError — MockScanner not defined)

- [ ] **Step 3: Implement `MockScanner` and module stubs in `backtest_engine.py`**

Append to `backtest_engine.py`:

```python
# ═══════════════════════════════════════════════════════════════════
# MockScanner — replacement for scanner.py
# ═══════════════════════════════════════════════════════════════════

class _Sentiment:
    """Mimics the Sentiment namedtuple returned by the real scanner."""
    def __init__(self, direction: str, green_count: int, red_count: int,
                 total_pairs: int, bullish_weight: float):
        self.direction      = direction
        self.green_count    = green_count
        self.red_count      = red_count
        self.total_pairs    = total_pairs
        self.bullish_weight = bullish_weight


class MockScanner:
    """
    Fake scanner.py. Returns fixed universe + synthetic sentiment.
    Sentiment is derived from how many symbols have 3m close > 1D open at sim_time.
    """

    def __init__(self, universe: list[str], parquet: dict[str, dict]):
        self.universe  = universe
        self.parquet   = parquet
        self.sim_time: int = 0

    def get_qualified_pairs_and_sentiment(self):
        DAY_MS = 86_400_000
        day_open_ts = self.sim_time - (self.sim_time % DAY_MS)
        green = 0
        for sym in self.universe:
            p = self.parquet.get(sym, {})
            df_3m = p.get("3m")
            df_1d = p.get("1d")
            if df_3m is None or df_1d is None:
                continue
            cur_3m = df_3m[df_3m["ts"] <= self.sim_time]
            cur_1d = df_1d[df_1d["ts"] <= day_open_ts]
            if cur_3m.empty or cur_1d.empty:
                continue
            close_3m   = float(cur_3m.iloc[-1]["close"])
            daily_open = float(cur_1d.iloc[-1]["open"])
            if daily_open > 0 and close_3m > daily_open:
                green += 1

        n     = len(self.universe)
        ratio = green / n if n > 0 else 0.5
        if ratio > 0.60:
            direction = "BULLISH"
        elif ratio < 0.40:
            direction = "BEARISH"
        else:
            direction = "NEUTRAL"

        return self.universe, _Sentiment(
            direction      = direction,
            green_count    = green,
            red_count      = n - green,
            total_pairs    = n,
            bullish_weight = ratio,
        )


# ═══════════════════════════════════════════════════════════════════
# Lightweight module stubs (no-ops)
# ═══════════════════════════════════════════════════════════════════

class _MockSnapshot:
    def save_snapshot(self, *a, **kw): pass


class _MockClaudeFilter:
    def claude_approve(self, *a, **kw): return True


class _MockStartupRecovery:
    def fetch_candles_at(self, *a, **kw):
        return pd.DataFrame()
    def estimate_sl_tp(self, entry, side):
        sl = entry * 0.95 if side == "LONG" else entry * 1.05
        tp = entry * 1.10 if side == "LONG" else entry * 0.90
        return sl, tp, 0.0, 0.0
    def attempt_s5_recovery(self, *a, **kw):
        return None
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
source venv/bin/activate && pytest tests/test_backtest_engine.py::test_mock_scanner_sentiment_bullish tests/test_backtest_engine.py::test_mock_scanner_sentiment_bearish -v 2>&1 | tail -15
```

Expected: 2 PASSED

- [ ] **Step 5: Commit**

```bash
git add backtest_engine.py tests/test_backtest_engine.py
git commit -m "feat(backtest-engine): add MockScanner with synthetic sentiment and module stubs"
```

---

## Task 5: `BacktestEngine` — time loop and module patching

**Files:**
- Modify: `backtest_engine.py` (append)
- Test: `tests/test_backtest_engine.py`

- [ ] **Step 1: Write failing tests**

```python
# append to tests/test_backtest_engine.py

def test_backtest_engine_smoke(tmp_path, monkeypatch):
    """
    BacktestEngine runs on 2 symbols × 10 3m bars without crashing.
    At least one trade is expected from a simple SL-hit scenario.
    """
    import backtest as bt
    monkeypatch.setattr(bt, "_CACHE_3M",  tmp_path / "3m")
    monkeypatch.setattr(bt, "_CACHE_15M", tmp_path / "15m")
    monkeypatch.setattr(bt, "_CACHE_1H",  tmp_path / "1h")
    monkeypatch.setattr(bt, "_CACHE_DAILY", Path("data/daily"))  # read existing

    from backtest_engine import BacktestEngine

    MIN3 = 3 * 60_000
    DAY  = 86_400_000
    now_ms = 1_774_483_200_000
    ts_list = [now_ms - 10 * MIN3 + i * MIN3 for i in range(10)]

    def _make(ts_list, close_vals=None):
        closes = close_vals or [100.0 + i for i in range(len(ts_list))]
        return pd.DataFrame({
            "ts":    ts_list,
            "open":  [100.0] * len(ts_list),
            "high":  [c * 1.01 for c in closes],
            "low":   [c * 0.99 for c in closes],
            "close": closes,
            "vol":   [10000.0] * len(ts_list),
        })

    day_ts = [now_ms - DAY]
    universe = ["BTCUSDT", "ETHUSDT"]
    parquet = {
        sym: {
            "3m":  _make(ts_list),
            "15m": _make([now_ms - 2 * 15 * 60_000 + i * 15 * 60_000 for i in range(5)]),
            "1h":  _make([now_ms - 5 * 3600_000 + i * 3600_000 for i in range(5)]),
            "1d":  _make(day_ts),
        }
        for sym in universe
    }

    engine = BacktestEngine(
        universe=universe,
        parquet=parquet,
        balance=1000.0,
        days=1,
        enabled_strategies={"S1", "S2", "S3", "S4", "S5", "S6"},
    )
    trades = engine.run()
    # Engine runs without exception; result is a list
    assert isinstance(trades, list)
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
source venv/bin/activate && pytest tests/test_backtest_engine.py::test_backtest_engine_smoke -v 2>&1 | tail -15
```

Expected: FAIL (ImportError — BacktestEngine not defined)

- [ ] **Step 3: Implement `BacktestEngine` in `backtest_engine.py`**

Append to `backtest_engine.py`:

```python
# ═══════════════════════════════════════════════════════════════════
# BacktestEngine — time loop and module patching
# ═══════════════════════════════════════════════════════════════════

class BacktestEngine:
    """
    Orchestrates the backtest:
    1. Patches sys.modules with mocks before importing bot
    2. Builds a unified 3m timeline
    3. Per bar: runs exit simulation then bot._tick()
    """

    def __init__(self, universe: list[str], parquet: dict[str, dict],
                 balance: float = 1000.0, days: int = 365,
                 enabled_strategies: set | None = None):
        self.universe   = universe
        self.parquet    = parquet
        self.balance    = balance
        self.days       = days
        self.enabled    = enabled_strategies or {"S1","S2","S3","S4","S5","S6"}
        self._trades: list[dict] = []

    def _patch_modules(self, mock_trader: "MockTrader",
                       bs: "BacktestState",
                       mock_scanner: "MockScanner") -> None:
        """Install mocks into sys.modules before bot.py is imported."""
        sys.modules["trader"]           = mock_trader          # type: ignore[assignment]
        sys.modules["state"]            = bs                   # type: ignore[assignment]
        sys.modules["snapshot"]         = _MockSnapshot()      # type: ignore[assignment]
        sys.modules["claude_filter"]    = _MockClaudeFilter()  # type: ignore[assignment]
        sys.modules["startup_recovery"] = _MockStartupRecovery()  # type: ignore[assignment]

        # Patch scanner module's function directly
        import scanner as _scanner_mod
        _scanner_mod.get_qualified_pairs_and_sentiment = \
            mock_scanner.get_qualified_pairs_and_sentiment

    def _build_timeline(self) -> list[int]:
        """Sorted unique 3m timestamps across all symbols."""
        all_ts: set[int] = set()
        for sym in self.universe:
            df = self.parquet.get(sym, {}).get("3m")
            if df is not None and not df.empty:
                all_ts.update(df["ts"].tolist())
        return sorted(all_ts)

    def run(self) -> list[dict]:
        mock_trader  = MockTrader(self.universe, self.parquet, self.balance)
        bs           = BacktestState()
        mock_scanner = MockScanner(self.universe, self.parquet)

        self._patch_modules(mock_trader, bs, mock_scanner)

        # Import bot AFTER patching
        # Remove cached bot module if present (test isolation)
        sys.modules.pop("bot", None)
        import bot as _bot_mod
        # Suppress disclaimer
        _bot_mod._check_disclaimer = lambda: None

        bot_instance = _bot_mod.MTFBot()
        # Disable strategy flags not in self.enabled
        import config as _cfg
        _orig_s1 = getattr(__import__("config_s1"), "S1_ENABLED", True)
        # Override enabled flags — patch config_sN modules in-place
        for s_num in range(1, 7):
            mod_name = f"config_s{s_num}"
            try:
                mod = __import__(mod_name)
                attr = f"S{s_num}_ENABLED"
                if hasattr(mod, attr):
                    setattr(mod, attr, f"S{s_num}" in self.enabled)
            except ImportError:
                pass

        timeline = self._build_timeline()
        total    = len(timeline)
        print(f"\n📊 Backtest: {len(self.universe)} symbols | {total} 3m bars")

        for idx, ts in enumerate(timeline):
            mock_trader.sim_time  = ts
            mock_scanner.sim_time = ts

            # ── Exit simulation: check each open position ──────── #
            for sym in list(mock_trader._positions.keys()):
                df_3m = self.parquet.get(sym, {}).get("3m")
                if df_3m is None:
                    continue
                bar_rows = df_3m[df_3m["ts"] == ts]
                if bar_rows.empty:
                    continue
                bar = bar_rows.iloc[0].to_dict()
                closed = mock_trader.process_bar(sym, bar)
                if closed:
                    # Record loss for pair-pause rule
                    if closed["result"] == "LOSS":
                        day_str = datetime.fromtimestamp(
                            ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                        bs.record_loss(sym, day_str)
                    self._trades.append(closed)

            # ── Check pending limit orders (S5) ───────────────── #
            for order_id, order in list(mock_trader._pending_orders.items()):
                sym  = order["symbol"]
                fill = mock_trader.get_order_fill(sym, order_id)
                if fill["status"] == "filled":
                    bal = mock_trader.get_usdt_balance()
                    bot_instance._handle_limit_filled(sym, {
                        **order,
                        "side":         order["side"],
                        "trigger":      order["limit_price"],
                        "sl":           order["sl"],
                        "tp":           order["tp"],
                        "qty_str":      order["qty_str"],
                        "ob_low":       0.0,
                        "ob_high":      0.0,
                        "rr":           0.0,
                        "sentiment":    mock_scanner.get_qualified_pairs_and_sentiment()[1].direction,
                        "sr_clearance_pct": None,
                    }, fill["fill_price"], bal)
                    mock_trader._pending_orders.pop(order_id, None)

            # ── Force scan every tick ──────────────────────────── #
            bot_instance.last_scan_time = 0

            # ── Pair pause gate ────────────────────────────────── #
            day_str = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            bot_instance.qualified_pairs = [
                s for s in self.universe
                if not bs.is_pair_paused(s, day_str)
            ]

            # ── Run bot tick ───────────────────────────────────── #
            try:
                bot_instance._tick()
            except Exception as e:
                logger.debug(f"[{ts}] tick error: {e}")

            # ── Strategy tag on newly opened positions ─────────── #
            for sym, pos in mock_trader._positions.items():
                if not pos.get("strategy"):
                    ot = bs.get_open_trade(sym)
                    if ot:
                        pos["strategy"] = ot.get("strategy", "")

            if idx % 5000 == 0 and idx > 0:
                pct = idx / total * 100
                print(f"  {pct:.0f}% | bar {idx}/{total} | "
                      f"open={len(mock_trader._positions)} trades={len(self._trades)}")

        # Close any still-open positions at last bar close price (timeout)
        for sym, pos in list(mock_trader._positions.items()):
            mark = mock_trader.get_mark_price(sym)
            if mark == 0:
                continue
            direction = 1 if pos["side"] == "LONG" else -1
            pct = (mark - pos["entry"]) / pos["entry"] * direction
            total_pnl = round(pct * pos["margin"] * pos["leverage"], 4)
            partial_pnl = pos.get("_partial_pnl", 0.0)
            t = {
                "symbol":       sym,
                "strategy":     pos["strategy"],
                "side":         pos["side"],
                "entry_price":  pos["entry"],
                "exit_price":   mark,
                "sl":           pos["sl"],
                "tp_trig":      pos["tp_trig"],
                "result":       "WIN" if total_pnl >= 0 else "LOSS",
                "exit_reason":  "TIMEOUT",
                "partial_pnl":  partial_pnl,
                "close_pnl":    round(total_pnl - partial_pnl, 4),
                "total_pnl":    total_pnl,
                "margin_pnl_pct": round(total_pnl / pos["margin"] * 100, 2) if pos["margin"] else 0,
                "scale_in":     pos["scale_in_done"],
                "candles_held": (timeline[-1] - pos["open_ts"]) // (3 * 60_000),
                "entry_date":   datetime.fromtimestamp(pos["open_ts"] / 1000, tz=timezone.utc).isoformat(),
                "exit_date":    datetime.fromtimestamp(timeline[-1] / 1000, tz=timezone.utc).isoformat(),
                "margin":       pos["margin"],
                "leverage":     pos["leverage"],
                "trade_id":     pos["trade_id"],
            }
            self._trades.append(t)

        print(f"\n✅ Done | {len(self._trades)} trades | "
              f"final balance: {mock_trader._balance:.2f} USDT")
        return self._trades
```

- [ ] **Step 4: Run smoke test**

```bash
source venv/bin/activate && pytest tests/test_backtest_engine.py::test_backtest_engine_smoke -v 2>&1 | tail -20
```

Expected: PASSED

- [ ] **Step 5: Commit**

```bash
git add backtest_engine.py tests/test_backtest_engine.py
git commit -m "feat(backtest-engine): add BacktestEngine time loop with module patching"
```

---

## Task 6: `main()`, HTML report, and CLI

**Files:**
- Modify: `backtest_engine.py` (append)
- Test: manual smoke run

- [ ] **Step 1: Implement `main()` in `backtest_engine.py`**

Append to `backtest_engine.py`:

```python
# ═══════════════════════════════════════════════════════════════════
# HTML report (reuses backtest.py's build_html_report)
# ═══════════════════════════════════════════════════════════════════

def _build_report(trades: list[dict], run_time: str, balance_start: float,
                  balance_end: float) -> str:
    """Build a simple HTML report from closed trade list."""
    if not trades:
        return "<html><body><h1>No trades</h1></body></html>"

    df = pd.DataFrame(trades)

    def stats(tlist):
        if not tlist:
            return dict(count=0, wins=0, losses=0, win_rate=0,
                        total_pnl=0, avg_win=0, avg_loss=0, best=0, worst=0)
        t  = pd.DataFrame(tlist)
        w  = t[t["result"] == "WIN"]
        l  = t[t["result"] == "LOSS"]
        return dict(
            count   = len(t),
            wins    = len(w),
            losses  = len(l),
            win_rate= round(len(w) / len(t) * 100, 1),
            total_pnl    = round(t["total_pnl"].sum(), 2),
            avg_win  = round(w["total_pnl"].mean(), 2) if len(w) else 0,
            avg_loss = round(l["total_pnl"].mean(), 2) if len(l) else 0,
            best     = round(t["total_pnl"].max(), 2),
            worst    = round(t["total_pnl"].min(), 2),
        )

    rows_html = ""
    for _, row in df.sort_values("entry_date").iterrows():
        colour = "#2ecc71" if row["result"] == "WIN" else "#e74c3c"
        rows_html += (
            f"<tr style='color:{colour}'>"
            f"<td>{row['strategy']}</td><td>{row['symbol']}</td>"
            f"<td>{row['side']}</td><td>{row['entry_date'][:10]}</td>"
            f"<td>{row['exit_date'][:10]}</td>"
            f"<td>{row['entry_price']:.4f}</td><td>{row['exit_price']:.4f}</td>"
            f"<td>{row['result']}</td><td>{row['exit_reason']}</td>"
            f"<td>{row['total_pnl']:+.2f}</td>"
            f"<td>{row['margin_pnl_pct']:+.1f}%</td>"
            f"</tr>\n"
        )

    by_strategy = {}
    for s in ["S1","S2","S3","S4","S5","S6"]:
        by_strategy[s] = stats([t for t in trades if t["strategy"] == s])
    overall = stats(trades)

    summary = "".join(
        f"<tr><td>{s}</td><td>{v['count']}</td><td>{v['wins']}</td>"
        f"<td>{v['losses']}</td><td>{v['win_rate']}%</td>"
        f"<td>{v['total_pnl']:+.2f}</td>"
        f"<td>{v['avg_win']:+.2f}</td><td>{v['avg_loss']:+.2f}</td></tr>\n"
        for s, v in by_strategy.items() if v["count"] > 0
    )

    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<title>Backtest Engine Report</title>
<style>body{{font-family:monospace;background:#111;color:#eee;padding:20px}}
table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #333;padding:4px 8px;text-align:left}}
th{{background:#222}}h1,h2{{color:#f39c12}}</style></head><body>
<h1>Backtest Engine Report</h1>
<p>Run: {run_time} | Balance: {balance_start:.0f} → {balance_end:.2f} USDT
({(balance_end - balance_start) / balance_start * 100:+.1f}%)</p>
<h2>Summary by Strategy</h2>
<table><tr><th>Strategy</th><th>Trades</th><th>Wins</th><th>Losses</th>
<th>WR%</th><th>Total PnL</th><th>Avg Win</th><th>Avg Loss</th></tr>
{summary}
<tr style='font-weight:bold'><td>TOTAL</td><td>{overall['count']}</td>
<td>{overall['wins']}</td><td>{overall['losses']}</td>
<td>{overall['win_rate']}%</td><td>{overall['total_pnl']:+.2f}</td>
<td>{overall['avg_win']:+.2f}</td><td>{overall['avg_loss']:+.2f}</td></tr>
</table>
<h2>All Trades</h2>
<table><tr><th>Strat</th><th>Symbol</th><th>Side</th><th>Entry</th><th>Exit</th>
<th>Entry $</th><th>Exit $</th><th>Result</th><th>Reason</th>
<th>PnL (USDT)</th><th>Margin PnL%</th></tr>
{rows_html}</table></body></html>"""


# ═══════════════════════════════════════════════════════════════════
# CLI entry point
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Unified S1–S6 backtest engine using real bot.py tick loop"
    )
    parser.add_argument("--days",     type=int,   default=365,
                        help="Lookback window in days (default 365)")
    parser.add_argument("--balance",  type=float, default=1000.0,
                        help="Starting USDT balance (default 1000)")
    parser.add_argument("--symbols",  nargs="*",
                        help="Override symbol universe (default: all data/daily/*.parquet)")
    parser.add_argument("--no-fetch", action="store_true",
                        help="Skip parquet cache updates, use existing data only")
    parser.add_argument("--output",   default="backtest_engine_report.html",
                        help="Output HTML report filename")
    for s in range(1, 7):
        parser.add_argument(f"--s{s}-only", action="store_true",
                            help=f"Run S{s} strategy only")
    args = parser.parse_args()

    # Determine enabled strategies
    only_flags = [f"s{i}_only" for i in range(1, 7) if getattr(args, f"s{i}_only", False)]
    if only_flags:
        enabled = {f"S{f[1]}" for f in only_flags}
    else:
        enabled = {"S1", "S2", "S3", "S4", "S5", "S6"}

    # Build symbol universe
    if args.symbols:
        universe = args.symbols
    else:
        universe = sorted(p.stem for p in Path("data/daily").glob("*.parquet"))

    if not universe:
        print("❌ No symbols found in data/daily/. Run with --no-fetch to use existing cache.")
        return

    print(f"📦 Universe: {len(universe)} symbols | {args.days} days | "
          f"balance={args.balance} | strategies={enabled}")

    # Load / update parquet caches
    import backtest as bt
    parquet: dict[str, dict] = {}
    print("\n⬇️  Loading candle data...")
    for sym in universe:
        p: dict = {}
        try:
            if args.no_fetch:
                for tf, cache_dir, col in [
                    ("1d",  bt._DAILY_CACHE, None),
                    ("15m", bt._CACHE_15M,   None),
                    ("1h",  bt._CACHE_1H,    None),
                    ("3m",  bt._CACHE_3M,    None),
                ]:
                    path = cache_dir / f"{sym}.parquet"
                    if path.exists():
                        p[tf] = pd.read_parquet(path)
            else:
                p["1d"]  = bt.load_daily(sym, days=args.days)
                p["15m"] = bt.load_15m(sym,   days=args.days)
                p["1h"]  = bt.load_1h(sym,    days=args.days)
                p["3m"]  = bt.load_3m(sym,    days=args.days)
        except Exception as e:
            print(f"  ⚠️  {sym}: data load error: {e}")
            continue

        if p.get("3m") is None or p["3m"].empty:
            print(f"  ⚠️  {sym}: no 3m data — skipping")
            continue
        parquet[sym] = p

    valid_universe = [s for s in universe if s in parquet]
    print(f"✅ Loaded {len(valid_universe)}/{len(universe)} symbols with 3m data\n")

    # Run engine
    engine = BacktestEngine(
        universe           = valid_universe,
        parquet            = parquet,
        balance            = args.balance,
        days               = args.days,
        enabled_strategies = enabled,
    )
    run_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    trades   = engine.run()

    # Write report
    report = _build_report(
        trades,
        run_time      = run_time,
        balance_start = args.balance,
        balance_end   = engine.balance,  # final balance from MockTrader
    )
    Path(args.output).write_text(report, encoding="utf-8")
    print(f"\n📄 Report → {args.output}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    main()
```

- [ ] **Step 2: Fix `engine.balance` reference** — expose final balance from `BacktestEngine.run()`

In the `BacktestEngine.run()` method, before `return self._trades`, add:

```python
        self.balance = mock_trader._balance   # expose for report
        return self._trades
```

- [ ] **Step 3: Run a quick smoke test on 2 symbols with --no-fetch**

```bash
source venv/bin/activate && python backtest_engine.py \
  --symbols BTCUSDT ETHUSDT --days 30 --no-fetch --s2-only \
  --output /tmp/test_report.html 2>&1 | tail -20
```

Expected: completes without exception, prints trade count, writes `/tmp/test_report.html`

- [ ] **Step 4: Commit**

```bash
git add backtest_engine.py
git commit -m "feat(backtest-engine): add BacktestEngine.run(), HTML report, and CLI main()"
```

---

## Task 7: Run all tests and verify full suite passes

**Files:**
- No new files

- [ ] **Step 1: Run all backtest engine tests**

```bash
source venv/bin/activate && pytest tests/test_backtest_engine.py -v 2>&1 | tail -30
```

Expected: all tests PASSED (no failures, no errors)

- [ ] **Step 2: Run full project test suite**

```bash
source venv/bin/activate && pytest --tb=short -q 2>&1 | tail -20
```

Expected: all existing tests still pass (backtest_engine changes must not break anything)

- [ ] **Step 3: Run a real backtest on a larger symbol set (manual validation)**

```bash
source venv/bin/activate && python backtest_engine.py \
  --days 90 --no-fetch --s2-only \
  --output backtest_engine_report.html 2>&1 | tail -30
```

Verify:
- Prints bar progress every 5000 bars
- Final balance printed
- Report HTML opens in browser and shows a trade table with entry/exit dates, PnL, strategies

- [ ] **Step 4: Final commit**

```bash
git add backtest_engine.py tests/test_backtest_engine.py backtest.py
git commit -m "feat(backtest-engine): complete unified S1-S6 backtest engine

- MockTrader: position/SL/partial-TP/trail/scale-in/limit-order simulation
- BacktestState: in-memory state.py replacement (all methods bot.py calls)
- MockScanner: synthetic sentiment from price vs daily open ratio
- BacktestEngine: unified 3m timeline loop, sys.modules patching, MTFBot._tick()
- load_3m/load_15m/load_1h: incremental parquet cache helpers in backtest.py
- HTML report with per-strategy stats and full trade table
- CLI: --days, --balance, --symbols, --s1-only..--s6-only, --no-fetch, --output"
```

---

## Self-Review

**Spec coverage check:**

| Spec section | Task |
|---|---|
| §1 Goal: all strategies, 3m clock, native timeframes | Task 5 (BacktestEngine time loop) |
| §2 Architecture: monkey-patching list | Task 5 (`_patch_modules`) |
| §3 Data layer: 4 caches, incremental | Task 1 (`load_3m/15m/1h`) |
| §4 MockTrader: full position state, exit logic | Task 3 |
| §4 Scale-in simulation | Task 3 (`scale_in_long/short`) |
| §4 Limit orders S5 | Task 3 (`place_limit_long/short`, `get_order_fill`) |
| §5 BacktestState: all methods | Task 2 |
| §6 MockScanner: synthetic sentiment thresholds | Task 4 |
| §7 Time loop: exit-first then tick | Task 5 |
| §7 SCAN_INTERVAL bypass | Task 5 (`last_scan_time = 0`) |
| §8 Balance: compound, partial update | Task 3 (`_update_balance`, `_close`) |
| §9 Trade log: all fields | Task 3 (`_close` dict) |
| §10 CLI flags | Task 6 |
| §11 No modifications to bot/trader/strategy | All tasks (confirmed) |
| §12 SL beats TP same bar | Task 3 test + implementation |
| §12 Pair pause rule | Task 5 (record_loss + qualified_pairs gate) |
