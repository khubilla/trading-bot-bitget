# Trade History Analytics Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new "Analytics" tab to the dashboard that shows, per Bitget strategy (S1–S6), a combined cumulative-P&L + per-trade-P&L chart, a sortable trade table, and an expandable per-trade parameter detail card — all backed by a new `/api/analytics` endpoint that reads `trades.csv` / `trades_paper.csv`.

**Architecture:** New pure-function module `analytics.py` handles all CSV parsing, OPEN/CLOSE pairing, per-strategy grouping, range filtering, series building, and summary stats. A thin FastAPI endpoint `/api/analytics` in `dashboard.py` delegates to it. The front-end in `dashboard.html` adds a top-level tab bar ("Live" / "Analytics"); the Analytics tab reuses the already-loaded `lightweight-charts@4.1.3` library.

**Tech Stack:** Python 3.11+, FastAPI, pytest, vanilla JS + `lightweight-charts@4.1.3`

**Spec:** [docs/superpowers/specs/2026-04-12-trade-history-analytics-design.md](../specs/2026-04-12-trade-history-analytics-design.md)

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `analytics.py` | Create | Pure-function aggregation module |
| `tests/test_analytics.py` | Create | Unit + integration tests |
| `dashboard.py` | Modify | Add `/api/analytics` endpoint |
| `dashboard.html` | Modify | Top-level tab bar + Analytics tab (controls, chart, table, detail card) |
| `docs/DEPENDENCIES.md` | Modify | Update §4.2 readers and §9 Dashboard Integration after implementation |

**Read-only contract:** No writes to `trades.csv`, no changes to `_TRADE_FIELDS` in `bot.py`, no changes to `state.json` schema, no changes to existing endpoints. Feature is purely additive.

---

## Task 1: Scaffold `analytics.py` with constants and test file

**Files:**
- Create: `analytics.py`
- Create: `tests/test_analytics.py`

- [ ] **Step 1: Create `analytics.py` with constants only**

```python
# analytics.py
"""
Pure-function aggregation module for the Dashboard → Analytics tab.

Reads trades.csv / trades_paper.csv, pairs OPEN rows with their matching
CLOSE rows via trade_id, groups by strategy, filters by time range, and
builds chart series + summary stats. No I/O beyond reading the CSV path
it is handed.
"""
from __future__ import annotations

import csv
import os
from datetime import datetime, timedelta, timezone
from typing import Literal, Union

STRATEGIES = ("S1", "S2", "S3", "S4", "S5", "S6")

STRATEGY_SNAP_FIELDS = {
    "S1": ("snap_rsi", "snap_adx", "snap_htf", "snap_coil",
           "snap_box_range_pct", "snap_sentiment"),
    "S2": ("snap_daily_rsi",),
    "S3": ("snap_entry_trigger", "snap_sl", "snap_rr"),
    "S4": ("snap_rsi_peak", "snap_spike_body_pct",
           "snap_rsi_div", "snap_rsi_div_str"),
    "S5": ("snap_s5_ob_low", "snap_s5_ob_high", "snap_s5_tp"),
    "S6": ("snap_s6_peak", "snap_s6_drop_pct", "snap_s6_rsi_at_peak"),
}

SHARED_SNAP = ("snap_sr_clearance_pct",)

COMMON_FIELDS = ("timestamp", "trade_id", "symbol", "side",
                 "entry", "exit_price", "pnl", "pnl_pct",
                 "result", "exit_reason", "leverage", "margin")

RangeSpec = Union[Literal["all", "30d", "90d"], int]
```

- [ ] **Step 2: Create `tests/test_analytics.py` with import smoke test**

```python
# tests/test_analytics.py
"""Tests for analytics.py — trade history aggregation module."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import analytics


def test_module_imports_and_exposes_constants():
    assert analytics.STRATEGIES == ("S1", "S2", "S3", "S4", "S5", "S6")
    assert "snap_rsi" in analytics.STRATEGY_SNAP_FIELDS["S1"]
    assert analytics.SHARED_SNAP == ("snap_sr_clearance_pct",)
    assert "pnl" in analytics.COMMON_FIELDS
```

- [ ] **Step 3: Run the smoke test**

Run: `pytest tests/test_analytics.py -v`
Expected: `test_module_imports_and_exposes_constants PASS`

- [ ] **Step 4: Commit**

```bash
git add analytics.py tests/test_analytics.py
git commit -m "feat(analytics): scaffold module with constants"
```

---

## Task 2: Implement `load_closed_trades()`

Pairs `*_CLOSE` rows with their matching `*_LONG`/`*_SHORT` open rows via `trade_id`, sums any PARTIAL `pnl`, carries open-side `snap_*` fields onto the output, and derives `strategy` from the OPEN action prefix.

**Files:**
- Modify: `analytics.py`
- Modify: `tests/test_analytics.py`

- [ ] **Step 1: Write failing tests for `load_closed_trades`**

Append to `tests/test_analytics.py`:

```python
# ── helpers ────────────────────────────────────────────────────────
_CSV_HEADER = (
    "timestamp,trade_id,action,symbol,side,qty,entry,sl,tp,"
    "box_low,box_high,leverage,margin,tpsl_set,strategy,"
    "snap_rsi,snap_adx,snap_htf,snap_coil,snap_box_range_pct,snap_sentiment,"
    "snap_daily_rsi,"
    "snap_entry_trigger,snap_sl,snap_rr,"
    "snap_rsi_peak,snap_spike_body_pct,snap_rsi_div,snap_rsi_div_str,"
    "snap_s5_ob_low,snap_s5_ob_high,snap_s5_tp,"
    "snap_s6_peak,snap_s6_drop_pct,snap_s6_rsi_at_peak,"
    "snap_sr_clearance_pct,"
    "result,pnl,pnl_pct,exit_reason,exit_price"
)


def _write_csv(path: Path, rows: list[dict]) -> None:
    """Write a list of dicts as a trades.csv-format file."""
    cols = _CSV_HEADER.split(",")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, restval="")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def _row(**kwargs) -> dict:
    """Build one CSV row dict (missing fields default to '')."""
    return kwargs


def test_load_closed_trades_missing_file_returns_empty(tmp_path):
    result = analytics.load_closed_trades(str(tmp_path / "nope.csv"))
    assert result == []


def test_load_closed_trades_empty_csv_returns_empty(tmp_path):
    path = tmp_path / "empty.csv"
    _write_csv(path, [])
    assert analytics.load_closed_trades(str(path)) == []


def test_load_closed_trades_pairs_open_and_close(tmp_path):
    path = tmp_path / "trades.csv"
    _write_csv(path, [
        _row(timestamp="2026-04-01T10:00:00+00:00", trade_id="t1",
             action="S3_LONG", symbol="BTCUSDT", side="LONG",
             entry="100.0", sl="95.0", tp="110.0",
             snap_entry_trigger="100.5", snap_sl="95.0", snap_rr="2.0"),
        _row(timestamp="2026-04-01T11:00:00+00:00", trade_id="t1",
             action="S3_CLOSE", symbol="BTCUSDT", side="LONG",
             pnl="10.0", pnl_pct="10.0", result="WIN",
             exit_reason="TP", exit_price="110.0"),
    ])
    trades = analytics.load_closed_trades(str(path))
    assert len(trades) == 1
    t = trades[0]
    assert t["trade_id"] == "t1"
    assert t["strategy"] == "S3"
    assert t["symbol"] == "BTCUSDT"
    assert t["side"] == "LONG"
    assert t["entry"] == 100.0
    assert t["exit_price"] == 110.0
    assert t["pnl"] == 10.0
    assert t["pnl_pct"] == 10.0
    assert t["result"] == "WIN"
    assert t["exit_reason"] == "TP"
    # snap_* from open row is carried onto output
    assert t["snap_entry_trigger"] == "100.5"
    assert t["snap_rr"] == "2.0"
    # close timestamp is the primary timestamp
    assert t["timestamp"] == "2026-04-01T11:00:00+00:00"


def test_load_closed_trades_sums_partial_pnl(tmp_path):
    path = tmp_path / "trades.csv"
    _write_csv(path, [
        _row(timestamp="2026-04-01T10:00:00+00:00", trade_id="t1",
             action="S3_LONG", symbol="BTCUSDT", side="LONG", entry="100"),
        _row(timestamp="2026-04-01T10:30:00+00:00", trade_id="t1",
             action="S3_PARTIAL", symbol="BTCUSDT", side="LONG",
             pnl="4.0", exit_price="105.0"),
        _row(timestamp="2026-04-01T11:00:00+00:00", trade_id="t1",
             action="S3_CLOSE", symbol="BTCUSDT", side="LONG",
             pnl="6.0", result="WIN", exit_price="110.0"),
    ])
    trades = analytics.load_closed_trades(str(path))
    assert len(trades) == 1
    assert trades[0]["pnl"] == 10.0   # 4 + 6


def test_load_closed_trades_skips_orphan_close(tmp_path):
    path = tmp_path / "trades.csv"
    _write_csv(path, [
        _row(timestamp="2026-04-01T11:00:00+00:00", trade_id="ghost",
             action="S3_CLOSE", symbol="BTCUSDT", side="LONG",
             pnl="6.0", result="WIN", exit_price="110.0"),
    ])
    assert analytics.load_closed_trades(str(path)) == []


def test_load_closed_trades_ignores_open_without_close(tmp_path):
    path = tmp_path / "trades.csv"
    _write_csv(path, [
        _row(timestamp="2026-04-01T10:00:00+00:00", trade_id="live",
             action="S3_LONG", symbol="BTCUSDT", side="LONG", entry="100"),
    ])
    assert analytics.load_closed_trades(str(path)) == []


def test_load_closed_trades_skips_unknown_strategy(tmp_path):
    path = tmp_path / "trades.csv"
    _write_csv(path, [
        _row(timestamp="2026-04-01T10:00:00+00:00", trade_id="t1",
             action="S99_LONG", symbol="BTCUSDT", side="LONG", entry="100"),
        _row(timestamp="2026-04-01T11:00:00+00:00", trade_id="t1",
             action="S99_CLOSE", symbol="BTCUSDT", side="LONG",
             pnl="6.0", result="WIN"),
    ])
    assert analytics.load_closed_trades(str(path)) == []


def test_load_closed_trades_skips_malformed_pnl(tmp_path):
    path = tmp_path / "trades.csv"
    _write_csv(path, [
        _row(timestamp="2026-04-01T10:00:00+00:00", trade_id="t1",
             action="S3_LONG", symbol="BTCUSDT", side="LONG", entry="100"),
        _row(timestamp="2026-04-01T11:00:00+00:00", trade_id="t1",
             action="S3_CLOSE", symbol="BTCUSDT", side="LONG",
             pnl="not-a-number", result="WIN", exit_price="110"),
    ])
    # Malformed pnl coerces to 0.0 — row is kept but pnl is 0
    trades = analytics.load_closed_trades(str(path))
    assert len(trades) == 1
    assert trades[0]["pnl"] == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_analytics.py -v`
Expected: 7 new tests FAIL with `AttributeError: module 'analytics' has no attribute 'load_closed_trades'`.

- [ ] **Step 3: Implement `load_closed_trades` and helpers in `analytics.py`**

Append to `analytics.py`:

```python
def _safe_float(v) -> float | None:
    """Coerce CSV string to float; return None on empty/invalid."""
    if v in (None, "", "None"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _open_side_fields(row: dict) -> dict:
    """Extract fields from an OPEN row that should be carried onto the close."""
    snap_keys = set()
    for keys in STRATEGY_SNAP_FIELDS.values():
        snap_keys.update(keys)
    snap_keys.update(SHARED_SNAP)
    out = {
        "entry":     _safe_float(row.get("entry")),
        "leverage":  row.get("leverage", ""),
        "margin":    row.get("margin", ""),
        "box_low":   _safe_float(row.get("box_low")),
        "box_high":  _safe_float(row.get("box_high")),
        "open_ts":   row.get("timestamp", ""),
    }
    for k in snap_keys:
        out[k] = row.get(k, "")
    return out


def load_closed_trades(csv_path: str) -> list[dict]:
    """Read CSV and return one dict per *_CLOSE row, joined with its matching OPEN.

    Rules:
      - Strategy is derived from the OPEN action prefix (e.g. "S3_LONG" -> "S3").
      - Only strategies in STRATEGIES are kept; unknown prefixes are skipped.
      - PARTIAL rows for the same trade_id have their pnl summed into the close pnl.
      - SCALE_IN rows are not counted as separate trades (same trade_id).
      - Orphan CLOSE (no matching OPEN) is skipped.
      - Orphan OPEN (no CLOSE yet — live position) is excluded.
      - Missing file returns []. Malformed pnl coerces to 0.0.
    """
    if not os.path.exists(csv_path):
        return []

    try:
        with open(csv_path, newline="") as f:
            rows = list(csv.DictReader(f))
    except (OSError, csv.Error):
        return []

    opens: dict[str, dict] = {}          # trade_id -> open-side fields + strategy
    partial_pnl: dict[str, float] = {}   # trade_id -> summed PARTIAL pnl

    # Pass 1: index OPEN, PARTIAL
    for r in rows:
        action = r.get("action") or ""
        tid = r.get("trade_id") or ""
        if not tid:
            continue

        if action.endswith("_LONG") or action.endswith("_SHORT"):
            strategy = action.split("_", 1)[0]
            if strategy not in STRATEGIES:
                continue
            opens[tid] = {
                **_open_side_fields(r),
                "strategy": strategy,
                "symbol":   r.get("symbol", ""),
                "side":     r.get("side", ""),
            }
        elif "_PARTIAL" in action:
            p = _safe_float(r.get("pnl")) or 0.0
            partial_pnl[tid] = partial_pnl.get(tid, 0.0) + p

    # Pass 2: emit one output row per CLOSE
    out: list[dict] = []
    for r in rows:
        action = r.get("action") or ""
        if "_CLOSE" not in action:
            continue
        tid = r.get("trade_id") or ""
        open_fields = opens.get(tid)
        if not open_fields:
            continue    # orphan close

        pnl = (_safe_float(r.get("pnl")) or 0.0) + partial_pnl.get(tid, 0.0)

        record = {
            "timestamp":  r.get("timestamp", ""),    # close timestamp
            "trade_id":   tid,
            "symbol":     open_fields["symbol"],
            "side":       open_fields["side"],
            "strategy":   open_fields["strategy"],
            "entry":      open_fields["entry"],
            "leverage":   open_fields["leverage"],
            "margin":     open_fields["margin"],
            "box_low":    open_fields["box_low"],
            "box_high":   open_fields["box_high"],
            "open_ts":    open_fields["open_ts"],
            "exit_price": _safe_float(r.get("exit_price")),
            "pnl":        pnl,
            "pnl_pct":    _safe_float(r.get("pnl_pct")),
            "result":     r.get("result", ""),
            "exit_reason": r.get("exit_reason", ""),
        }
        # Carry snap_* fields from the open row verbatim
        for k, v in open_fields.items():
            if k.startswith("snap_"):
                record[k] = v
        out.append(record)

    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_analytics.py -v`
Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add analytics.py tests/test_analytics.py
git commit -m "feat(analytics): implement load_closed_trades with OPEN/CLOSE pairing"
```

---

## Task 3: Implement `group_by_strategy()`

**Files:**
- Modify: `analytics.py`
- Modify: `tests/test_analytics.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_analytics.py`:

```python
def test_group_by_strategy_all_six_keys_always_present():
    result = analytics.group_by_strategy([])
    assert set(result.keys()) == set(analytics.STRATEGIES)
    assert all(result[k] == [] for k in analytics.STRATEGIES)


def test_group_by_strategy_buckets_correctly():
    trades = [
        {"strategy": "S1", "trade_id": "a"},
        {"strategy": "S3", "trade_id": "b"},
        {"strategy": "S1", "trade_id": "c"},
    ]
    result = analytics.group_by_strategy(trades)
    assert [t["trade_id"] for t in result["S1"]] == ["a", "c"]
    assert [t["trade_id"] for t in result["S3"]] == ["b"]
    assert result["S2"] == []


def test_group_by_strategy_ignores_unknown_strategy():
    trades = [{"strategy": "S99", "trade_id": "x"}]
    result = analytics.group_by_strategy(trades)
    assert all(result[k] == [] for k in analytics.STRATEGIES)
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_analytics.py::test_group_by_strategy_all_six_keys_always_present -v`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement `group_by_strategy` in `analytics.py`**

Append to `analytics.py`:

```python
def group_by_strategy(trades: list[dict]) -> dict[str, list[dict]]:
    """Bucket trades into all 6 strategy keys. Unknown strategies are dropped."""
    out: dict[str, list[dict]] = {s: [] for s in STRATEGIES}
    for t in trades:
        s = t.get("strategy")
        if s in out:
            out[s].append(t)
    return out
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_analytics.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add analytics.py tests/test_analytics.py
git commit -m "feat(analytics): add group_by_strategy"
```

---

## Task 4: Implement `filter_range()`

**Files:**
- Modify: `analytics.py`
- Modify: `tests/test_analytics.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_analytics.py`:

```python
def _mk_trade(ts: str, tid: str = "t", pnl: float = 1.0) -> dict:
    return {"timestamp": ts, "trade_id": tid, "pnl": pnl, "strategy": "S1"}


def test_filter_range_all_returns_everything():
    trades = [_mk_trade("2025-01-01T00:00:00+00:00"),
              _mk_trade("2026-04-01T00:00:00+00:00")]
    assert analytics.filter_range(trades, "all") == trades


def test_filter_range_30d_keeps_recent():
    now = datetime(2026, 4, 12, tzinfo=timezone.utc)
    old = _mk_trade("2026-03-01T00:00:00+00:00")  # 42 days ago — drop
    mid = _mk_trade("2026-03-20T00:00:00+00:00")  # 23 days ago — keep
    new = _mk_trade("2026-04-10T00:00:00+00:00")  # 2 days ago — keep
    result = analytics.filter_range([old, mid, new], "30d", now=now)
    assert result == [mid, new]


def test_filter_range_90d_keeps_recent():
    now = datetime(2026, 4, 12, tzinfo=timezone.utc)
    very_old = _mk_trade("2025-11-01T00:00:00+00:00")  # >90d — drop
    old = _mk_trade("2026-02-15T00:00:00+00:00")       # ~56d — keep
    result = analytics.filter_range([very_old, old], "90d", now=now)
    assert result == [old]


def test_filter_range_int_keeps_last_n():
    trades = [_mk_trade(f"2026-04-0{i}T00:00:00+00:00", tid=f"t{i}")
              for i in range(1, 6)]
    result = analytics.filter_range(trades, 3)
    assert [t["trade_id"] for t in result] == ["t3", "t4", "t5"]


def test_filter_range_int_larger_than_len_keeps_all():
    trades = [_mk_trade("2026-04-01T00:00:00+00:00", tid="a"),
              _mk_trade("2026-04-02T00:00:00+00:00", tid="b")]
    result = analytics.filter_range(trades, 100)
    assert result == trades


def test_filter_range_empty_input_returns_empty():
    assert analytics.filter_range([], "all") == []
    assert analytics.filter_range([], "30d") == []
    assert analytics.filter_range([], 10) == []


def test_filter_range_skips_unparseable_timestamps_for_time_ranges():
    now = datetime(2026, 4, 12, tzinfo=timezone.utc)
    good = _mk_trade("2026-04-10T00:00:00+00:00", tid="good")
    bad = _mk_trade("not-a-date", tid="bad")
    result = analytics.filter_range([good, bad], "30d", now=now)
    assert [t["trade_id"] for t in result] == ["good"]
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_analytics.py -k filter_range -v`
Expected: all FAIL with `AttributeError`.

- [ ] **Step 3: Implement `filter_range` in `analytics.py`**

Append to `analytics.py`:

```python
def _parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def filter_range(trades: list[dict],
                 range_spec: RangeSpec,
                 now: datetime | None = None) -> list[dict]:
    """Filter trades by range spec.

    range_spec:
      - "all" → return unchanged
      - "30d" / "90d" → keep trades whose close timestamp is within N days of `now`
      - int N → keep the most recent N trades (by list order, which is CSV order)
    """
    if range_spec == "all":
        return list(trades)

    if isinstance(range_spec, int):
        if range_spec <= 0:
            return []
        return list(trades[-range_spec:])

    days_map = {"30d": 30, "90d": 90}
    if range_spec not in days_map:
        return list(trades)

    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days_map[range_spec])
    out = []
    for t in trades:
        dt = _parse_iso(t.get("timestamp", ""))
        if dt is None:
            continue
        if dt >= cutoff:
            out.append(t)
    return out
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_analytics.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add analytics.py tests/test_analytics.py
git commit -m "feat(analytics): add filter_range with time and last-N modes"
```

---

## Task 5: Implement `build_series()`

**Files:**
- Modify: `analytics.py`
- Modify: `tests/test_analytics.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_analytics.py`:

```python
def test_build_series_trade_mode_indices_start_at_one():
    trades = [{"pnl": 1.0, "timestamp": "2026-04-01T00:00:00+00:00"},
              {"pnl": 2.0, "timestamp": "2026-04-02T00:00:00+00:00"}]
    result = analytics.build_series(trades, "trade")
    assert [p["x"] for p in result["cum_pnl"]] == [1, 2]
    assert [p["x"] for p in result["bars"]] == [1, 2]


def test_build_series_cum_pnl_is_running_sum():
    trades = [{"pnl": 1.0, "timestamp": "2026-04-01T00:00:00+00:00"},
              {"pnl": 2.5, "timestamp": "2026-04-02T00:00:00+00:00"},
              {"pnl": -1.0, "timestamp": "2026-04-03T00:00:00+00:00"}]
    result = analytics.build_series(trades, "trade")
    assert [p["y"] for p in result["cum_pnl"]] == [1.0, 3.5, 2.5]
    assert [p["y"] for p in result["bars"]] == [1.0, 2.5, -1.0]


def test_build_series_bar_colors_match_sign():
    trades = [{"pnl": 1.0, "timestamp": "2026-04-01T00:00:00+00:00"},
              {"pnl": -0.5, "timestamp": "2026-04-02T00:00:00+00:00"},
              {"pnl": 0.0, "timestamp": "2026-04-03T00:00:00+00:00"}]
    result = analytics.build_series(trades, "trade")
    colors = [p["color"] for p in result["bars"]]
    assert colors == ["green", "red", "green"]


def test_build_series_time_mode_uses_iso_timestamp():
    trades = [{"pnl": 1.0, "timestamp": "2026-04-01T10:00:00+00:00"}]
    result = analytics.build_series(trades, "time")
    assert result["cum_pnl"][0]["x"] == "2026-04-01T10:00:00+00:00"
    assert result["bars"][0]["x"] == "2026-04-01T10:00:00+00:00"


def test_build_series_empty_input():
    result = analytics.build_series([], "trade")
    assert result == {"cum_pnl": [], "bars": []}
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_analytics.py -k build_series -v`
Expected: all FAIL.

- [ ] **Step 3: Implement `build_series` in `analytics.py`**

Append to `analytics.py`:

```python
def build_series(trades: list[dict], x_mode: Literal["trade", "time"]) -> dict:
    """Build chart series from a per-strategy trade list.

    Returns {"cum_pnl": [{x, y}, ...], "bars": [{x, y, color}, ...]}.
      - x_mode="trade" → x is integer index starting at 1.
      - x_mode="time"  → x is the ISO close timestamp string.
      - bars.color is "green" if pnl >= 0 else "red".
    """
    cum_pnl: list[dict] = []
    bars: list[dict] = []
    running = 0.0
    for i, t in enumerate(trades, start=1):
        pnl = float(t.get("pnl") or 0.0)
        running += pnl
        x = i if x_mode == "trade" else t.get("timestamp", "")
        cum_pnl.append({"x": x, "y": running})
        bars.append({"x": x, "y": pnl,
                     "color": "green" if pnl >= 0 else "red"})
    return {"cum_pnl": cum_pnl, "bars": bars}
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_analytics.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add analytics.py tests/test_analytics.py
git commit -m "feat(analytics): add build_series for cum-pnl line and pnl bars"
```

---

## Task 6: Implement `summarize()`

**Files:**
- Modify: `analytics.py`
- Modify: `tests/test_analytics.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_analytics.py`:

```python
def test_summarize_empty_has_zero_counts_and_none_stats():
    r = analytics.summarize([])
    assert r["count"] == 0
    assert r["wins"] == 0
    assert r["losses"] == 0
    assert r["win_rate"] is None
    assert r["total_pnl"] == 0.0
    assert r["avg_win"] is None
    assert r["avg_loss"] is None
    assert r["best"] is None
    assert r["worst"] is None


def test_summarize_all_wins():
    trades = [{"pnl": 1.0}, {"pnl": 2.0}, {"pnl": 3.0}]
    r = analytics.summarize(trades)
    assert r["count"] == 3
    assert r["wins"] == 3
    assert r["losses"] == 0
    assert r["win_rate"] == 1.0
    assert r["total_pnl"] == 6.0
    assert r["avg_win"] == 2.0
    assert r["avg_loss"] is None
    assert r["best"] == 3.0
    assert r["worst"] == 1.0


def test_summarize_all_losses():
    trades = [{"pnl": -1.0}, {"pnl": -2.0}]
    r = analytics.summarize(trades)
    assert r["count"] == 2
    assert r["wins"] == 0
    assert r["losses"] == 2
    assert r["win_rate"] == 0.0
    assert r["total_pnl"] == -3.0
    assert r["avg_win"] is None
    assert r["avg_loss"] == -1.5
    assert r["best"] == -1.0
    assert r["worst"] == -2.0


def test_summarize_mixed():
    trades = [{"pnl": 10.0}, {"pnl": -5.0}, {"pnl": 20.0}, {"pnl": -2.0}]
    r = analytics.summarize(trades)
    assert r["count"] == 4
    assert r["wins"] == 2
    assert r["losses"] == 2
    assert r["win_rate"] == 0.5
    assert r["total_pnl"] == 23.0
    assert r["avg_win"] == 15.0
    assert r["avg_loss"] == -3.5
    assert r["best"] == 20.0
    assert r["worst"] == -5.0


def test_summarize_treats_zero_pnl_as_win_like_result_column_would():
    # pnl = 0 is counted as a win (not a loss) — keeps divide-by-zero impossible
    trades = [{"pnl": 0.0}, {"pnl": -1.0}]
    r = analytics.summarize(trades)
    assert r["wins"] == 1
    assert r["losses"] == 1
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_analytics.py -k summarize -v`
Expected: FAIL.

- [ ] **Step 3: Implement `summarize` in `analytics.py`**

Append to `analytics.py`:

```python
def summarize(trades: list[dict]) -> dict:
    """Aggregate stats. pnl >= 0 is counted as a win."""
    if not trades:
        return {
            "count": 0, "wins": 0, "losses": 0,
            "win_rate": None, "total_pnl": 0.0,
            "avg_win": None, "avg_loss": None,
            "best": None, "worst": None,
        }
    pnls = [float(t.get("pnl") or 0.0) for t in trades]
    wins = [p for p in pnls if p >= 0]
    losses = [p for p in pnls if p < 0]
    return {
        "count":     len(pnls),
        "wins":      len(wins),
        "losses":    len(losses),
        "win_rate":  len(wins) / len(pnls),
        "total_pnl": sum(pnls),
        "avg_win":   (sum(wins) / len(wins))   if wins   else None,
        "avg_loss":  (sum(losses) / len(losses)) if losses else None,
        "best":      max(pnls),
        "worst":     min(pnls),
    }
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_analytics.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add analytics.py tests/test_analytics.py
git commit -m "feat(analytics): add summarize stats"
```

---

## Task 7: Implement `build_analytics()` orchestrator

**Files:**
- Modify: `analytics.py`
- Modify: `tests/test_analytics.py`

- [ ] **Step 1: Write failing integration test**

Append to `tests/test_analytics.py`:

```python
def test_build_analytics_end_to_end_shape(tmp_path):
    path = tmp_path / "trades.csv"
    _write_csv(path, [
        _row(timestamp="2026-04-01T10:00:00+00:00", trade_id="a",
             action="S1_LONG", symbol="BTCUSDT", side="LONG",
             entry="100", snap_rsi="60"),
        _row(timestamp="2026-04-01T11:00:00+00:00", trade_id="a",
             action="S1_CLOSE", symbol="BTCUSDT", side="LONG",
             pnl="5.0", result="WIN", exit_price="105"),
        _row(timestamp="2026-04-02T10:00:00+00:00", trade_id="b",
             action="S3_LONG", symbol="ETHUSDT", side="LONG",
             entry="2000", snap_rr="2.0"),
        _row(timestamp="2026-04-02T11:00:00+00:00", trade_id="b",
             action="S3_CLOSE", symbol="ETHUSDT", side="LONG",
             pnl="-3.0", result="LOSS", exit_price="1997"),
    ])
    result = analytics.build_analytics(str(path), "all", "trade")

    assert set(result.keys()) == {"strategies"}
    assert set(result["strategies"].keys()) == set(analytics.STRATEGIES)

    s1 = result["strategies"]["S1"]
    assert set(s1.keys()) == {"trades", "series", "summary"}
    assert len(s1["trades"]) == 1
    assert s1["trades"][0]["snap_rsi"] == "60"
    assert s1["summary"]["count"] == 1
    assert s1["summary"]["total_pnl"] == 5.0
    assert s1["series"]["cum_pnl"] == [{"x": 1, "y": 5.0}]
    assert s1["series"]["bars"][0]["color"] == "green"

    s3 = result["strategies"]["S3"]
    assert s3["summary"]["total_pnl"] == -3.0
    assert s3["series"]["bars"][0]["color"] == "red"

    # other strategies empty but present
    for k in ("S2", "S4", "S5", "S6"):
        s = result["strategies"][k]
        assert s["trades"] == []
        assert s["series"] == {"cum_pnl": [], "bars": []}
        assert s["summary"]["count"] == 0


def test_build_analytics_missing_file_returns_empty_strategies():
    result = analytics.build_analytics("/nonexistent/path.csv", "all", "trade")
    assert set(result["strategies"].keys()) == set(analytics.STRATEGIES)
    for k, s in result["strategies"].items():
        assert s["trades"] == []
        assert s["summary"]["count"] == 0
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_analytics.py -k build_analytics -v`
Expected: FAIL.

- [ ] **Step 3: Implement `build_analytics` in `analytics.py`**

Append to `analytics.py`:

```python
def build_analytics(csv_path: str,
                    range_spec: RangeSpec,
                    x_mode: Literal["trade", "time"]) -> dict:
    """Top-level orchestrator. Returns the full payload the endpoint serves.

    Shape:
      {"strategies": {
          "S1": {"trades": [...], "series": {"cum_pnl":[...], "bars":[...]},
                 "summary": {...}},
          ...
      }}
    """
    all_trades = load_closed_trades(csv_path)
    by_strat = group_by_strategy(all_trades)

    strategies = {}
    for s in STRATEGIES:
        rows = filter_range(by_strat[s], range_spec)
        strategies[s] = {
            "trades":  rows,
            "series":  build_series(rows, x_mode),
            "summary": summarize(rows),
        }
    return {"strategies": strategies}
```

- [ ] **Step 4: Run all analytics tests**

Run: `pytest tests/test_analytics.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add analytics.py tests/test_analytics.py
git commit -m "feat(analytics): add build_analytics orchestrator"
```

---

## Task 8: Add `/api/analytics` endpoint to `dashboard.py`

**Files:**
- Modify: `dashboard.py` (add endpoint near existing `/api/candles` and `/state` endpoints; add import at top)
- Create tests: `tests/test_analytics_endpoint.py`

- [ ] **Step 1: Write failing endpoint tests**

Create `tests/test_analytics_endpoint.py`:

```python
"""Tests for the /api/analytics endpoint in dashboard.py."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import dashboard


_CSV_HEADER = (
    "timestamp,trade_id,action,symbol,side,qty,entry,sl,tp,"
    "box_low,box_high,leverage,margin,tpsl_set,strategy,"
    "snap_rsi,snap_adx,snap_htf,snap_coil,snap_box_range_pct,snap_sentiment,"
    "snap_daily_rsi,snap_entry_trigger,snap_sl,snap_rr,"
    "snap_rsi_peak,snap_spike_body_pct,snap_rsi_div,snap_rsi_div_str,"
    "snap_s5_ob_low,snap_s5_ob_high,snap_s5_tp,"
    "snap_s6_peak,snap_s6_drop_pct,snap_s6_rsi_at_peak,"
    "snap_sr_clearance_pct,result,pnl,pnl_pct,exit_reason,exit_price"
)


def _write_csv(path: Path, rows: list[dict]) -> None:
    cols = _CSV_HEADER.split(",")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, restval="")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


@pytest.fixture
def client_with_csv(tmp_path, monkeypatch):
    """Point dashboard at a temp trades.csv and return a TestClient."""
    csv_path = tmp_path / "trades.csv"
    _write_csv(csv_path, [
        {"timestamp": "2026-04-01T10:00:00+00:00", "trade_id": "t1",
         "action": "S3_LONG", "symbol": "BTCUSDT", "side": "LONG",
         "entry": "100", "snap_rr": "2.0"},
        {"timestamp": "2026-04-01T11:00:00+00:00", "trade_id": "t1",
         "action": "S3_CLOSE", "symbol": "BTCUSDT", "side": "LONG",
         "pnl": "5.0", "result": "WIN", "exit_price": "105"},
    ])
    state_path = tmp_path / "state.json"
    state_path.write_text("{}")
    monkeypatch.setattr(dashboard, "STATE_FILE", str(state_path))
    return TestClient(dashboard.app)


def test_analytics_endpoint_returns_shape(client_with_csv):
    r = client_with_csv.get("/api/analytics")
    assert r.status_code == 200
    data = r.json()
    assert "strategies" in data
    assert set(data["strategies"].keys()) == {
        "S1", "S2", "S3", "S4", "S5", "S6"
    }
    s3 = data["strategies"]["S3"]
    assert s3["summary"]["count"] == 1
    assert s3["summary"]["total_pnl"] == 5.0


def test_analytics_endpoint_accepts_range_30d(client_with_csv):
    r = client_with_csv.get("/api/analytics?range=30d&x=trade")
    assert r.status_code == 200


def test_analytics_endpoint_accepts_lastN(client_with_csv):
    r = client_with_csv.get("/api/analytics?range=lastN&n=10")
    assert r.status_code == 200


def test_analytics_endpoint_rejects_unknown_range(client_with_csv):
    r = client_with_csv.get("/api/analytics?range=bogus")
    assert r.status_code == 400


def test_analytics_endpoint_rejects_unknown_x_mode(client_with_csv):
    r = client_with_csv.get("/api/analytics?x=nope")
    assert r.status_code == 400


def test_analytics_endpoint_rejects_lastN_without_valid_n(client_with_csv):
    assert client_with_csv.get("/api/analytics?range=lastN").status_code == 400
    assert client_with_csv.get("/api/analytics?range=lastN&n=0").status_code == 400
    assert client_with_csv.get("/api/analytics?range=lastN&n=10001").status_code == 400


def test_analytics_endpoint_returns_empty_when_csv_missing(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    state_path.write_text("{}")
    monkeypatch.setattr(dashboard, "STATE_FILE", str(state_path))
    # No trades.csv next to state.json
    client = TestClient(dashboard.app)
    r = client.get("/api/analytics")
    assert r.status_code == 200
    data = r.json()
    assert all(data["strategies"][k]["summary"]["count"] == 0
               for k in ("S1", "S2", "S3", "S4", "S5", "S6"))
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_analytics_endpoint.py -v`
Expected: FAIL with 404 (endpoint not registered).

- [ ] **Step 3: Add import and endpoint to `dashboard.py`**

At the top of `dashboard.py`, add to the existing import block:

```python
import analytics
```

Near the other `@app.get` endpoints (a good spot is right after the existing `get_state` endpoint, around line 330 — but the exact location does not matter), add:

```python
@app.get("/api/analytics")
@limiter.limit("30/minute")
async def get_analytics(request: Request,
                        range: str = "all",
                        x: str = "trade",
                        n: int | None = None):
    """Return per-strategy trade analytics for the dashboard Analytics tab.

    Query params:
      range  — "all" | "30d" | "90d" | "lastN"  (default "all")
      x      — "trade" | "time"                  (default "trade")
      n      — required when range == "lastN"; 1 ≤ n ≤ 10000
    """
    # Validate x
    if x not in ("trade", "time"):
        return JSONResponse({"error": "invalid x"}, status_code=400)

    # Validate range + derive range_spec
    if range == "all":
        range_spec = "all"
    elif range in ("30d", "90d"):
        range_spec = range
    elif range == "lastN":
        if n is None or n < 1 or n > 10000:
            return JSONResponse({"error": "invalid n"}, status_code=400)
        range_spec = n
    else:
        return JSONResponse({"error": "invalid range"}, status_code=400)

    # Resolve CSV path — same rule as existing state handler at dashboard.py:300
    csv_path = STATE_FILE.replace(
        "state_paper.json", "trades_paper.csv"
    ).replace(
        "state.json", "trades.csv"
    )
    payload = analytics.build_analytics(csv_path, range_spec, x)
    return JSONResponse(payload)
```

- [ ] **Step 4: Run endpoint tests**

Run: `pytest tests/test_analytics_endpoint.py -v`
Expected: all 7 pass.

- [ ] **Step 5: Run full test suite to verify nothing regressed**

Run: `pytest -q`
Expected: all pass (or the same pre-existing failures you had before starting, if any).

- [ ] **Step 6: Commit**

```bash
git add dashboard.py tests/test_analytics_endpoint.py
git commit -m "feat(dashboard): add /api/analytics endpoint"
```

---

## Task 9: Add top-level tab bar to `dashboard.html`

Add an `Analytics` tab alongside the existing "Live" content. The Live content is everything currently inside the main wrapper; it goes into a `<div class="view view-live">`. Add an empty `<div class="view view-analytics hidden">` placeholder — we fill it in subsequent tasks.

**Files:**
- Modify: `dashboard.html`

- [ ] **Step 1: Locate the main content wrapper**

Open `dashboard.html` and find the first `<body>` tag. The existing page is one big wrapper (there is no current top-level tab bar). We will:

1. Insert a `<nav class="top-tabs">` element at the top of `<body>`.
2. Wrap the *entire existing body content* (everything between `<body>` and `</body>`, minus the trailing `<script>` tags) in `<div class="view view-live">`.
3. Append a sibling `<div class="view view-analytics hidden"></div>` after it.

- [ ] **Step 2: Add CSS for the tab bar**

Find the `<style>` block in `dashboard.html`. After the `:root` variables (around line 16), add:

```css
.top-tabs {
  display: flex;
  gap: 4px;
  padding: 8px 16px 0 16px;
  background: var(--panel, #0f1823);
  border-bottom: 1px solid var(--border, #1e2d3d);
  position: sticky; top: 0; z-index: 100;
}
.top-tabs .tab {
  padding: 8px 18px;
  cursor: pointer;
  border: 1px solid transparent;
  border-bottom: none;
  border-radius: 6px 6px 0 0;
  color: var(--text, #d4dce4);
  font-size: 14px;
  font-weight: 500;
  opacity: 0.7;
  user-select: none;
}
.top-tabs .tab:hover { opacity: 1; }
.top-tabs .tab.active {
  opacity: 1;
  background: var(--bg, #0b1320);
  border-color: var(--border, #1e2d3d);
  color: var(--amber, #f0a500);
}
.view.hidden { display: none !important; }
```

- [ ] **Step 3: Add the tab-bar markup and wrap existing content**

Immediately after `<body>`, insert:

```html
<nav class="top-tabs">
  <div class="tab active" data-view="live">Live</div>
  <div class="tab"        data-view="analytics">Analytics</div>
</nav>
<div class="view view-live">
```

Then, immediately before the *first* `<script>` tag at the bottom of `<body>`, insert:

```html
</div><!-- /view-live -->
<div class="view view-analytics hidden">
  <!-- Analytics content is populated in later tasks -->
</div>
```

- [ ] **Step 4: Add tab-switch JS**

In the main `<script>` block at the bottom, near the top of the block (before any existing init code), add:

```javascript
// ── Top-level tab switching ───────────────────────────────────────
(function initTopTabs() {
  const tabs  = document.querySelectorAll('.top-tabs .tab');
  const views = {
    live:      document.querySelector('.view-live'),
    analytics: document.querySelector('.view-analytics'),
  };
  const saved = sessionStorage.getItem('topTab') || 'live';

  function activate(name) {
    if (!views[name]) name = 'live';
    tabs.forEach(t => t.classList.toggle('active', t.dataset.view === name));
    Object.entries(views).forEach(([k, el]) => {
      if (el) el.classList.toggle('hidden', k !== name);
    });
    sessionStorage.setItem('topTab', name);
    if (name === 'analytics' && typeof window.onAnalyticsShown === 'function') {
      window.onAnalyticsShown();
    }
  }

  tabs.forEach(t => t.addEventListener('click', () => activate(t.dataset.view)));
  activate(saved);
})();
```

- [ ] **Step 5: Open the dashboard in a browser and verify both tabs switch**

Run the dashboard (in another terminal): `python dashboard.py --paper`
Open: `http://localhost:8081`
Expected: Two tabs visible at top ("Live", "Analytics"); clicking each toggles visibility; refresh preserves selection.

- [ ] **Step 6: Run existing HTML/regression tests if any exist**

Run: `pytest tests/test_trade_chart.py -v` (smoke — ensures we didn't break existing dashboard HTML parsing)
Expected: all existing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add dashboard.html
git commit -m "feat(dashboard): add top-level Live/Analytics tab bar"
```

---

## Task 10: Build the Analytics tab shell — strategy sub-tabs + controls

**Files:**
- Modify: `dashboard.html` (populate `.view-analytics`)

- [ ] **Step 1: Add CSS for the Analytics panel**

In the `<style>` block, after the `.top-tabs` CSS, add:

```css
.analytics-wrap { padding: 16px; }
.analytics-controls {
  display: flex; align-items: center; gap: 16px;
  padding: 10px 14px; margin-bottom: 12px;
  background: var(--panel, #0f1823);
  border: 1px solid var(--border, #1e2d3d);
  border-radius: 8px;
}
.analytics-controls label { font-size: 12px; opacity: 0.75; }
.analytics-controls select {
  background: var(--bg, #0b1320); color: var(--text, #d4dce4);
  border: 1px solid var(--border); border-radius: 4px; padding: 4px 8px;
}
.analytics-controls button {
  background: var(--panel); color: var(--text); border: 1px solid var(--border);
  border-radius: 4px; padding: 4px 12px; cursor: pointer;
}
.analytics-controls button:hover { border-color: var(--amber); color: var(--amber); }
.analytics-controls .last-n { display: none; }
.analytics-controls .last-n.visible { display: inline-flex; gap: 4px; align-items: center; }
.analytics-controls .last-n input { width: 64px; background: var(--bg); color: var(--text); border: 1px solid var(--border); border-radius: 4px; padding: 4px 6px; }

.strategy-tabs { display: flex; gap: 4px; margin-bottom: 12px; }
.strategy-tabs .stab {
  padding: 6px 14px; cursor: pointer; border: 1px solid var(--border);
  border-radius: 4px; font-size: 13px; opacity: 0.7; user-select: none;
}
.strategy-tabs .stab:hover { opacity: 1; }
.strategy-tabs .stab.active {
  opacity: 1; border-color: var(--amber); color: var(--amber);
  background: rgba(240,165,0,0.08);
}

.strategy-panel { display: none; }
.strategy-panel.active { display: block; }

.summary-row {
  display: flex; gap: 24px; padding: 10px 14px; margin-bottom: 12px;
  background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
  font-size: 13px;
}
.summary-row .stat { display: flex; flex-direction: column; gap: 2px; }
.summary-row .stat .label { opacity: 0.6; font-size: 11px; text-transform: uppercase; }
.summary-row .stat .value { font-weight: 600; }
.summary-row .stat.positive .value { color: var(--green, #3cb371); }
.summary-row .stat.negative .value { color: var(--rose, #e04a5f); }

.analytics-chart {
  height: 320px; background: var(--panel);
  border: 1px solid var(--border); border-radius: 8px; margin-bottom: 12px;
  position: relative;
}
.analytics-empty {
  padding: 32px; text-align: center; opacity: 0.5; font-size: 14px;
}
```

- [ ] **Step 2: Replace the `.view-analytics` placeholder with full markup**

Replace the `<div class="view view-analytics hidden">` block you added in Task 9 with:

```html
<div class="view view-analytics hidden">
  <div class="analytics-wrap">

    <div class="analytics-controls">
      <label>Range</label>
      <select id="aRange">
        <option value="all">All</option>
        <option value="30d">Last 30 days</option>
        <option value="90d">Last 90 days</option>
        <option value="lastN">Last N trades…</option>
      </select>
      <span class="last-n" id="aLastNWrap">
        <label>N</label>
        <input id="aLastN" type="number" min="1" max="10000" value="100">
      </span>

      <label>X-axis</label>
      <select id="aXMode">
        <option value="trade">Trade #</option>
        <option value="time">Time</option>
      </select>

      <button id="aRefresh">Refresh</button>
      <span id="aStatus" style="opacity:0.6; font-size:12px; margin-left:auto;"></span>
    </div>

    <div class="strategy-tabs" id="aStratTabs">
      <div class="stab active" data-s="S1">S1</div>
      <div class="stab"        data-s="S2">S2</div>
      <div class="stab"        data-s="S3">S3</div>
      <div class="stab"        data-s="S4">S4</div>
      <div class="stab"        data-s="S5">S5</div>
      <div class="stab"        data-s="S6">S6</div>
    </div>

    <div id="aStrategyPanels">
      <!-- one panel per strategy, populated by JS -->
      <div class="strategy-panel active" data-s="S1"></div>
      <div class="strategy-panel"        data-s="S2"></div>
      <div class="strategy-panel"        data-s="S3"></div>
      <div class="strategy-panel"        data-s="S4"></div>
      <div class="strategy-panel"        data-s="S5"></div>
      <div class="strategy-panel"        data-s="S6"></div>
    </div>

  </div><!-- /.analytics-wrap -->
</div><!-- /.view-analytics -->
```

- [ ] **Step 3: Add the Analytics JS bootstrap**

Immediately below the tab-switch IIFE you added in Task 9, add:

```javascript
// ── Analytics tab ────────────────────────────────────────────────
const Analytics = (() => {
  const state = {
    data:     null,          // last payload from /api/analytics
    strategy: sessionStorage.getItem('aStrat')  || 'S1',
    range:    sessionStorage.getItem('aRange')  || 'all',
    xMode:    sessionStorage.getItem('aXMode')  || 'trade',
    lastN:    parseInt(sessionStorage.getItem('aLastN') || '100', 10),
    charts:   {},            // strategy -> {chart, line, hist}
    selected: {},            // strategy -> selected trade index (null = none)
  };

  function authHeaders() {
    // Reuse any existing auth mechanism; if DASHBOARD_API_KEY isn't set
    // server-side, no header is needed.
    const stored = localStorage.getItem('apiKey') || '';
    return stored ? { 'Authorization': 'Bearer ' + stored } : {};
  }

  async function fetchData() {
    const params = new URLSearchParams({ range: state.range, x: state.xMode });
    if (state.range === 'lastN') params.set('n', state.lastN);
    setStatus('Loading…');
    try {
      const r = await fetch('/api/analytics?' + params.toString(),
                            { headers: authHeaders() });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      state.data = await r.json();
      setStatus('');
      renderAll();
    } catch (e) {
      setStatus('Failed: ' + e.message);
    }
  }

  function setStatus(s) {
    const el = document.getElementById('aStatus');
    if (el) el.textContent = s;
  }

  function renderAll() {
    if (!state.data) return;
    for (const s of ['S1','S2','S3','S4','S5','S6']) {
      renderStrategy(s, state.data.strategies[s]);
    }
  }

  function renderStrategy(s, payload) {
    const panel = document.querySelector(`.strategy-panel[data-s="${s}"]`);
    if (!panel) return;
    const summary = payload.summary || {};
    const fmt = (v, d=2) => v == null ? '—' : Number(v).toFixed(d);
    const pct = v => v == null ? '—' : (v * 100).toFixed(1) + '%';
    const pnlClass = (summary.total_pnl || 0) >= 0 ? 'positive' : 'negative';
    panel.innerHTML = `
      <div class="summary-row">
        <div class="stat"><span class="label">Trades</span><span class="value">${summary.count ?? 0}</span></div>
        <div class="stat"><span class="label">Wins</span><span class="value">${summary.wins ?? 0}</span></div>
        <div class="stat"><span class="label">Losses</span><span class="value">${summary.losses ?? 0}</span></div>
        <div class="stat"><span class="label">Win rate</span><span class="value">${pct(summary.win_rate)}</span></div>
        <div class="stat ${pnlClass}"><span class="label">Total P&L</span><span class="value">${fmt(summary.total_pnl)}</span></div>
        <div class="stat"><span class="label">Avg win</span><span class="value">${fmt(summary.avg_win)}</span></div>
        <div class="stat"><span class="label">Avg loss</span><span class="value">${fmt(summary.avg_loss)}</span></div>
        <div class="stat"><span class="label">Best</span><span class="value">${fmt(summary.best)}</span></div>
        <div class="stat"><span class="label">Worst</span><span class="value">${fmt(summary.worst)}</span></div>
      </div>
      <div class="analytics-chart" id="aChart-${s}"></div>
      <div class="analytics-table-wrap" id="aTable-${s}"></div>
      <div class="analytics-detail"     id="aDetail-${s}"></div>
    `;
    if (!payload.trades || payload.trades.length === 0) {
      document.getElementById('aChart-' + s).innerHTML =
        '<div class="analytics-empty">No closed trades in this range.</div>';
      document.getElementById('aTable-' + s).innerHTML = '';
      document.getElementById('aDetail-' + s).innerHTML = '';
      return;
    }
    // Chart, table, detail-card rendering land in Tasks 11 & 12.
    if (typeof renderAnalyticsChart === 'function') renderAnalyticsChart(s, payload);
    if (typeof renderAnalyticsTable === 'function') renderAnalyticsTable(s, payload);
  }

  function wireControls() {
    const rangeSel = document.getElementById('aRange');
    const xSel     = document.getElementById('aXMode');
    const lastN    = document.getElementById('aLastN');
    const lastWrap = document.getElementById('aLastNWrap');
    const refresh  = document.getElementById('aRefresh');

    rangeSel.value = state.range;
    xSel.value     = state.xMode;
    lastN.value    = state.lastN;
    lastWrap.classList.toggle('visible', state.range === 'lastN');

    rangeSel.addEventListener('change', () => {
      state.range = rangeSel.value;
      sessionStorage.setItem('aRange', state.range);
      lastWrap.classList.toggle('visible', state.range === 'lastN');
      fetchData();
    });
    xSel.addEventListener('change', () => {
      state.xMode = xSel.value;
      sessionStorage.setItem('aXMode', state.xMode);
      fetchData();
    });
    lastN.addEventListener('change', () => {
      state.lastN = Math.max(1, Math.min(10000, parseInt(lastN.value || '100', 10)));
      sessionStorage.setItem('aLastN', String(state.lastN));
      if (state.range === 'lastN') fetchData();
    });
    refresh.addEventListener('click', fetchData);

    document.querySelectorAll('#aStratTabs .stab').forEach(el => {
      el.addEventListener('click', () => {
        const s = el.dataset.s;
        state.strategy = s;
        sessionStorage.setItem('aStrat', s);
        document.querySelectorAll('#aStratTabs .stab')
          .forEach(x => x.classList.toggle('active', x.dataset.s === s));
        document.querySelectorAll('.strategy-panel')
          .forEach(x => x.classList.toggle('active', x.dataset.s === s));
      });
    });

    // Restore active strategy tab from sessionStorage
    document.querySelectorAll('#aStratTabs .stab')
      .forEach(x => x.classList.toggle('active', x.dataset.s === state.strategy));
    document.querySelectorAll('.strategy-panel')
      .forEach(x => x.classList.toggle('active', x.dataset.s === state.strategy));
  }

  return {
    init() { wireControls(); },
    show() { if (!state.data) fetchData(); },
    state,
  };
})();

document.addEventListener('DOMContentLoaded', () => Analytics.init());
window.onAnalyticsShown = () => Analytics.show();
```

- [ ] **Step 4: Open dashboard and verify**

Run: `python dashboard.py --paper`
Open `http://localhost:8081` → click "Analytics" tab.
Expected: Controls row + strategy tabs S1–S6 + per-strategy summary row populate. Chart/table placeholders show "No closed trades…" if paper CSV is empty. Changing Range triggers a re-fetch.

- [ ] **Step 5: Commit**

```bash
git add dashboard.html
git commit -m "feat(dashboard): add analytics tab shell with controls and summary"
```

---

## Task 11: Render the combined chart (line + histogram)

**Files:**
- Modify: `dashboard.html` (add `renderAnalyticsChart` function)

- [ ] **Step 1: Add `renderAnalyticsChart` function**

Inside the `<script>` block, after the `Analytics` IIFE (outside of it, top-level), add:

```javascript
function renderAnalyticsChart(s, payload) {
  const container = document.getElementById('aChart-' + s);
  if (!container || !payload.series) return;

  // Dispose existing chart for this strategy to allow re-render on data changes
  const existing = Analytics.state.charts[s];
  if (existing && existing.chart) {
    existing.chart.remove();
  }

  const chart = LightweightCharts.createChart(container, {
    layout:     { background: { color: '#0f1823' }, textColor: '#d4dce4' },
    grid:       { vertLines: { color: '#1e2d3d' }, horzLines: { color: '#1e2d3d' } },
    rightPriceScale: { borderColor: '#1e2d3d' },
    leftPriceScale:  { visible: true, borderColor: '#1e2d3d' },
    timeScale:  { borderColor: '#1e2d3d', rightOffset: 4, barSpacing: 12 },
    handleScroll: true, handleScale: true,
  });

  const isTimeMode = Analytics.state.xMode === 'time';

  function toBarTime(x) {
    // lightweight-charts accepts: unix-seconds number OR "YYYY-MM-DD" string.
    // For trade-# mode we synthesize increasing unix-seconds starting at 0,
    // then override the tick formatter to display "#1, #2, …".
    if (isTimeMode) {
      // x is an ISO string — convert to UTC seconds.
      return Math.floor(new Date(x).getTime() / 1000);
    }
    // x is an integer 1..N; offset by an arbitrary base so values are positive
    // and monotonic but small enough to render as integer ticks.
    return x;  // integer seconds; lib accepts this
  }

  const cumData = payload.series.cum_pnl.map(p => ({
    time: toBarTime(p.x), value: p.y,
  }));
  const barData = payload.series.bars.map(p => ({
    time:  toBarTime(p.x),
    value: p.y,
    color: p.color === 'green' ? '#3cb371' : '#e04a5f',
  }));

  const line = chart.addLineSeries({
    priceScaleId: 'left',
    color: '#f0a500',
    lineWidth: 2,
    lastValueVisible: false,
    priceLineVisible: false,
  });
  line.setData(cumData);

  const hist = chart.addHistogramSeries({
    priceScaleId: 'right',
    priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    lastValueVisible: false,
    priceLineVisible: false,
  });
  hist.setData(barData);

  // In trade-# mode, override tick labels to show "#N"
  if (!isTimeMode) {
    chart.applyOptions({
      localization: {
        timeFormatter: t => '#' + t,
      },
      timeScale: {
        tickMarkFormatter: t => '#' + t,
      },
    });
  }

  chart.timeScale().fitContent();

  // Click → highlight matching trade
  chart.subscribeClick(param => {
    if (!param || param.time == null) return;
    // Find the trade whose series x matches clicked time
    const idx = payload.series.bars.findIndex(
      b => toBarTime(b.x) === param.time
    );
    if (idx >= 0) selectTrade(s, idx, payload);
  });

  Analytics.state.charts[s] = { chart, line, hist };
}

function selectTrade(s, idx, payload) {
  Analytics.state.selected[s] = idx;
  // Table + detail highlights are wired in Task 12.
  const tableRows = document.querySelectorAll(
    `#aTable-${s} .atable tbody tr`
  );
  tableRows.forEach((r, i) => r.classList.toggle('selected', i === idx));
  if (typeof renderAnalyticsDetail === 'function') {
    renderAnalyticsDetail(s, payload, idx);
  }
}
```

- [ ] **Step 2: Open dashboard and verify the chart renders**

Prerequisite: you need a paper CSV with at least a few closed trades, or live `trades.csv`. If neither has closed trades, seed one by running the bot in paper mode briefly (see README) or copy a known-good `trades.csv` into the working directory.

Run: `python dashboard.py` (live) or `python dashboard.py --paper`
Open and click "Analytics" → pick a strategy that has trades.
Expected: amber equity curve line + green/red histogram bars on a shared time axis; toggling X-axis between "Trade #" and "Time" re-draws correctly.

- [ ] **Step 3: Commit**

```bash
git add dashboard.html
git commit -m "feat(dashboard): render analytics combined equity + p&l chart"
```

---

## Task 12: Render the trade table + detail card

**Files:**
- Modify: `dashboard.html` (add `renderAnalyticsTable` and `renderAnalyticsDetail`)

- [ ] **Step 1: Add CSS for the table and detail card**

In the `<style>` block, after the `.analytics-chart` rule, add:

```css
.analytics-table-wrap { max-height: 320px; overflow-y: auto; margin-bottom: 12px;
  border: 1px solid var(--border); border-radius: 8px; }
table.atable { width: 100%; border-collapse: collapse; font-size: 12px; }
table.atable th, table.atable td {
  padding: 6px 10px; text-align: left; border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
table.atable th { background: var(--panel); cursor: pointer; user-select: none;
  position: sticky; top: 0; }
table.atable th:hover { color: var(--amber); }
table.atable tbody tr { cursor: pointer; }
table.atable tbody tr:hover { background: rgba(240,165,0,0.05); }
table.atable tbody tr.selected { background: rgba(240,165,0,0.12); }
table.atable td.positive { color: var(--green, #3cb371); }
table.atable td.negative { color: var(--rose, #e04a5f); }

.analytics-detail {
  background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
  padding: 0;
}
.analytics-detail.empty { display: none; }
.analytics-detail .hdr {
  display: flex; justify-content: space-between; align-items: center;
  padding: 10px 14px; border-bottom: 1px solid var(--border);
}
.analytics-detail .hdr .title { font-weight: 600; }
.analytics-detail .hdr button {
  background: transparent; color: var(--text); border: 1px solid var(--border);
  border-radius: 4px; padding: 2px 10px; cursor: pointer;
}
.analytics-detail .grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 8px 16px; padding: 10px 14px;
}
.analytics-detail .kv { display: flex; justify-content: space-between; font-size: 12px;
  border-bottom: 1px dashed var(--border); padding: 3px 0; }
.analytics-detail .kv .k { opacity: 0.65; }
.analytics-detail .kv .v { font-weight: 500; }
```

- [ ] **Step 2: Add `renderAnalyticsTable` and `renderAnalyticsDetail`**

In the `<script>` block, after `selectTrade`, add:

```javascript
// Strategy-specific snap_* columns (mirror of analytics.STRATEGY_SNAP_FIELDS)
const STRATEGY_SNAP_COLS = {
  S1: ['snap_rsi','snap_adx','snap_htf','snap_coil','snap_box_range_pct','snap_sentiment'],
  S2: ['snap_daily_rsi'],
  S3: ['snap_entry_trigger','snap_sl','snap_rr'],
  S4: ['snap_rsi_peak','snap_spike_body_pct','snap_rsi_div','snap_rsi_div_str'],
  S5: ['snap_s5_ob_low','snap_s5_ob_high','snap_s5_tp'],
  S6: ['snap_s6_peak','snap_s6_drop_pct','snap_s6_rsi_at_peak'],
};
const SHARED_SNAP_COLS = ['snap_sr_clearance_pct'];

const COMMON_COLS = [
  ['timestamp','Close time'], ['symbol','Symbol'], ['side','Side'],
  ['entry','Entry'], ['exit_price','Exit'],
  ['pnl','P&L'], ['pnl_pct','P&L %'],
  ['result','Result'], ['exit_reason','Exit reason'],
];

function renderAnalyticsTable(s, payload) {
  const wrap = document.getElementById('aTable-' + s);
  if (!wrap) return;
  const trades = payload.trades || [];
  const snapCols = [...(STRATEGY_SNAP_COLS[s] || []), ...SHARED_SNAP_COLS];
  const cols = [...COMMON_COLS, ...snapCols.map(c => [c, c])];

  const fmt = (v) => (v == null || v === '') ? '—'
    : (typeof v === 'number' ? v.toLocaleString(undefined, {maximumFractionDigits: 4}) : String(v));

  const thead = '<thead><tr>' + cols.map((c, i) =>
    `<th data-col="${c[0]}" data-idx="${i}">${c[1]}</th>`
  ).join('') + '</tr></thead>';

  const tbody = '<tbody>' + trades.map((t, i) => {
    const pnl = Number(t.pnl || 0);
    const cls = pnl >= 0 ? 'positive' : 'negative';
    return `<tr data-idx="${i}">` + cols.map(c => {
      const key = c[0];
      let v = t[key];
      let tdCls = '';
      if (key === 'pnl' || key === 'pnl_pct') tdCls = cls;
      return `<td class="${tdCls}">${fmt(v)}</td>`;
    }).join('') + '</tr>';
  }).join('') + '</tbody>';

  wrap.innerHTML = `<table class="atable">${thead}${tbody}</table>`;

  // Row-click → select trade
  wrap.querySelectorAll('tbody tr').forEach(tr => {
    tr.addEventListener('click', () => {
      const idx = parseInt(tr.dataset.idx, 10);
      selectTrade(s, idx, payload);
    });
  });

  // Header-click → client-side sort
  wrap.querySelectorAll('thead th').forEach(th => {
    th.addEventListener('click', () => {
      const col = th.dataset.col;
      const asc = th.dataset.sort !== 'asc';
      const sorted = [...trades].sort((a, b) => {
        const av = a[col], bv = b[col];
        const na = Number(av), nb = Number(bv);
        if (!isNaN(na) && !isNaN(nb)) return asc ? na - nb : nb - na;
        return asc ? String(av).localeCompare(String(bv))
                   : String(bv).localeCompare(String(av));
      });
      // Re-render with sorted copy but keep original payload for selection indices
      renderAnalyticsTable(s, { ...payload, trades: sorted });
      // Mark sort direction
      const newTh = wrap.querySelector(`th[data-col="${col}"]`);
      if (newTh) newTh.dataset.sort = asc ? 'asc' : 'desc';
    });
  });
}

function renderAnalyticsDetail(s, payload, idx) {
  const el = document.getElementById('aDetail-' + s);
  if (!el) return;
  const trade = (payload.trades || [])[idx];
  if (!trade) { el.className = 'analytics-detail empty'; el.innerHTML = ''; return; }

  const snapCols = [...(STRATEGY_SNAP_COLS[s] || []), ...SHARED_SNAP_COLS];
  const allFields = [
    ...['trade_id','strategy','symbol','side','timestamp','open_ts',
        'entry','exit_price','pnl','pnl_pct','result','exit_reason',
        'leverage','margin','box_low','box_high'],
    ...snapCols,
  ];

  const rows = allFields.map(k => {
    const v = trade[k];
    const disp = (v == null || v === '') ? '—' : String(v);
    return `<div class="kv"><span class="k">${k}</span><span class="v">${disp}</span></div>`;
  }).join('');

  el.className = 'analytics-detail';
  el.innerHTML = `
    <div class="hdr">
      <span class="title">${s} · ${trade.symbol} · ${trade.side} · #${idx + 1}</span>
      <button id="aDetailClose-${s}">Close</button>
    </div>
    <div class="grid">${rows}</div>
  `;
  document.getElementById('aDetailClose-' + s).addEventListener('click', () => {
    el.className = 'analytics-detail empty';
    el.innerHTML = '';
    document.querySelectorAll(`#aTable-${s} tbody tr`)
      .forEach(r => r.classList.remove('selected'));
    Analytics.state.selected[s] = null;
  });
}
```

- [ ] **Step 2: Open dashboard and verify the table + detail card**

Run the dashboard and click Analytics → a strategy with trades.
Expected:
- Table below the chart shows one row per closed trade, with the right strategy-specific snap_* columns.
- Click a column header → rows sort.
- Click a row OR a bar in the chart → row highlights and a detail card appears below the table showing all fields.
- Click "Close" on the detail card → it collapses.

- [ ] **Step 3: Run the full test suite**

Run: `pytest -q`
Expected: all pass (same baseline as before you started).

- [ ] **Step 4: Commit**

```bash
git add dashboard.html
git commit -m "feat(dashboard): add analytics trade table and detail card"
```

---

## Task 13: Update `docs/DEPENDENCIES.md`

**Files:**
- Modify: `docs/DEPENDENCIES.md`

- [ ] **Step 1: Update §4.2 trades.csv readers list**

Find the "Readers:" block under §4.2 (around line 498–502). Add a new bullet:

```markdown
- `analytics.py` — aggregates closed trades per strategy for the dashboard `/api/analytics` endpoint (read-only)
```

- [ ] **Step 2: Populate §9 Dashboard Integration**

Replace the placeholder under `## 9. Dashboard Integration` (currently "[To be populated in Task 11]") with:

```markdown
### 9.1 Analytics tab (`/api/analytics`)

**Purpose:** Per-strategy trade history analytics (Bitget bot, strategies S1–S6).

**Write chain:** none — read-only feature.

**Read chain:**
- `dashboard.py`: `@app.get("/api/analytics")` → calls `analytics.build_analytics(csv_path, range_spec, x_mode)`
- `analytics.py`: reads `trades.csv` or `trades_paper.csv` (same path resolution as existing `get_state` endpoint)

**Query parameters:**
- `range` — `"all"` | `"30d"` | `"90d"` | `"lastN"` (default `"all"`)
- `x` — `"trade"` | `"time"` (default `"trade"`)
- `n` — required when `range="lastN"`; `1 ≤ n ≤ 10000`

**Response shape:**
```json
{
  "strategies": {
    "S1": {"trades": [...], "series": {"cum_pnl": [...], "bars": [...]},
            "summary": {"count": ..., "wins": ..., "win_rate": ..., ...}},
    "S2": {...}, ..., "S6": {...}
  }
}
```

**Breaking scenarios:**

1. **Renaming `STRATEGY_SNAP_FIELDS` keys in analytics.py** → dashboard.html
   `STRATEGY_SNAP_COLS` no longer matches → per-strategy snap columns disappear from the table.
   Fix: keep the two lists in sync.

2. **Adding a new strategy (e.g. S7)** → analytics silently drops its trades (not in `STRATEGIES`).
   Fix: add to `analytics.STRATEGIES` and `STRATEGY_SNAP_FIELDS`; add a strategy tab in `dashboard.html`.

3. **Changing `trades.csv` column names** → `analytics.load_closed_trades` reads by name;
   renaming a `snap_*` field breaks the per-trade detail card values (empty strings render as "—").
   Fix: § 4.2 already covers the CSV column contract — keep field lists in sync.

**Verification commands:**

```bash
# Endpoint smoke test
curl -s 'http://localhost:8081/api/analytics?range=30d&x=trade' | jq '.strategies | keys'

# Analytics module unit tests
pytest tests/test_analytics.py -v

# Endpoint tests
pytest tests/test_analytics_endpoint.py -v
```
```

- [ ] **Step 3: Commit**

```bash
git add docs/DEPENDENCIES.md
git commit -m "docs(deps): document /api/analytics endpoint and analytics.py consumer"
```

---

## Task 14: Final verification

- [ ] **Step 1: Run the full test suite**

Run: `pytest -q`
Expected: all pass.

- [ ] **Step 2: Invoke QA skill**

Invoke the `qa-trading-bot` skill (per repo convention — it runs pytest with auto-fixing for source failures).

- [ ] **Step 3: Manual browser verification**

Start the dashboard: `python dashboard.py --paper` and `python dashboard.py` (live).

Verify for each:
- Top tabs show "Live" and "Analytics"; clicking switches; refresh preserves.
- Analytics tab loads and fetches `/api/analytics` once.
- All 6 strategy sub-tabs render (empty ones show "No closed trades…").
- For a strategy with data: summary row populates; chart shows amber curve + green/red bars; X-axis toggle reshapes chart; Range selector re-fetches; table renders and sorts; clicking a row or bar opens the detail card.

- [ ] **Step 4: Final commit (if any follow-up tweaks)**

If no changes needed, nothing to commit. Otherwise:

```bash
git add -A
git commit -m "chore(analytics): final polish from manual QA"
```

---

## Self-Review Notes

**Spec coverage:**
- Bitget-only, strategies S1–S6 — ✅ Tasks 1–7 (`STRATEGIES = (S1..S6)`), Task 10 (strategy tabs).
- Combined equity + bars chart — ✅ Task 11.
- X-axis toggle (trade # / time) + range (all/30d/90d/lastN) — ✅ Tasks 4, 8, 10.
- Sortable table + expandable detail card — ✅ Task 12.
- New top-level "Analytics" tab — ✅ Task 9.
- Follows existing `--paper` flag — ✅ Task 8 (CSV path resolution mirrors `dashboard.py:300`).
- No CSV / state / endpoint contract changes — ✅ read-only throughout.
- Tests on everything — ✅ Tasks 1–8 include unit + endpoint tests.
- DEPENDENCIES.md updated — ✅ Task 13.

**Type consistency:** `STRATEGY_SNAP_FIELDS` (Python) mirrors `STRATEGY_SNAP_COLS` (JS). `build_analytics` signature `(csv_path, range_spec, x_mode)` matches endpoint call in Task 8. Response shape `{"strategies": {S1: {trades, series, summary}}}` is consistent across tests (Tasks 7, 8) and JS consumers (Tasks 10–12).

**Placeholder scan:** No TBDs, no "implement later", every code step has concrete code.
