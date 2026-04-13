# Trade DNA Fingerprint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record trend fingerprint fields (`snap_trend_*`) at every trade entry so future per-symbol pattern-match lookups have data from day one.

**Architecture:** New `trade_dna.py` module with `snapshot()` (computes and returns fingerprint dict) and `lookup()` stub. `bot.py` calls `snapshot()` just before `_log_trade()` at each strategy entry point and merges the returned dict into the trade row.

**Tech Stack:** Python 3.11+, pandas, existing `strategy.calculate_ema` / `strategy.calculate_adx`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `trade_dna.py` | Create | Bucketing helpers + `snapshot()` + `lookup()` stub |
| `bot.py` | Modify | Add 10 fields to `_TRADE_FIELDS`; call `dna_snapshot()` at 7 entry points |
| `tests/test_trade_dna.py` | Create | Unit + integration + error-path tests |

---

## Task 1: Bucketing helpers in `trade_dna.py`

**Files:**
- Create: `trade_dna.py`
- Test: `tests/test_trade_dna.py`

- [ ] **Step 1: Write failing tests for all bucketing helpers**

Create `tests/test_trade_dna.py`:

```python
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
```

- [ ] **Step 2: Run tests — verify they all fail with ImportError**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot && python -m pytest tests/test_trade_dna.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError: No module named 'trade_dna'`

- [ ] **Step 3: Implement bucketing helpers in `trade_dna.py`**

Create `trade_dna.py`:

```python
"""
trade_dna.py — Trade fingerprint recorder and (future) pattern-match filter.

Phase 1 (current): snapshot() records trend context at entry into trades.csv.
Phase 2 (future):  lookup() replaces claude_filter.py as the approval gate.
"""
import logging
import pandas as pd

logger = logging.getLogger(__name__)

# ── Bucketing thresholds (internal constants) ──────────────────────────── #
EMA_SLOPE_THRESHOLD = 0.003   # 0.3% change over n candles = rising/falling
ADX_STATE_THRESHOLD = 3       # absolute ADX point change over n candles


def ema_slope(closes: pd.Series, period: int, n: int = 10) -> str:
    """
    Returns "rising" / "falling" / "flat" based on EMA direction over last n candles.
    Returns "" if series is too short to compute.
    """
    if len(closes) < period + n:
        return ""
    from strategy import calculate_ema
    ema = calculate_ema(closes, period)
    v_now  = float(ema.iloc[-1])
    v_prev = float(ema.iloc[-n])
    if v_prev == 0:
        return ""
    change = (v_now - v_prev) / v_prev
    if change > EMA_SLOPE_THRESHOLD:
        return "rising"
    if change < -EMA_SLOPE_THRESHOLD:
        return "falling"
    return "flat"


def price_vs_ema(price: float, ema: float) -> str:
    """Returns "above" if price > ema, else "below"."""
    return "above" if price > ema else "below"


def rsi_bucket(rsi: float) -> str:
    """Bucket RSI value into a labelled range string."""
    if rsi < 50:
        return "<50"
    if rsi < 60:
        return "50-60"
    if rsi < 65:
        return "60-65"
    if rsi < 70:
        return "65-70"
    if rsi < 75:
        return "70-75"
    if rsi < 80:
        return "75-80"
    return ">80"


def adx_state(adx_series: pd.Series, n: int = 10) -> str:
    """
    Returns "rising" / "falling" / "flat" based on ADX direction over last n candles.
    Returns "" if series is too short.
    adx_series: pre-computed ADX values as pd.Series.
    """
    if len(adx_series) < n + 1:
        return ""
    v_now  = float(adx_series.iloc[-1])
    v_prev = float(adx_series.iloc[-n])
    diff = v_now - v_prev
    if diff > ADX_STATE_THRESHOLD:
        return "rising"
    if diff < -ADX_STATE_THRESHOLD:
        return "falling"
    return "flat"
```

- [ ] **Step 4: Run tests — verify helpers pass**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot && python -m pytest tests/test_trade_dna.py -v 2>&1 | head -40
```

Expected: all `TestEmaSlopeHelper`, `TestPriceVsEma`, `TestRsiBucket`, `TestAdxState` tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot && git add trade_dna.py tests/test_trade_dna.py && git commit -m "feat(dna): add trade_dna bucketing helpers with tests"
```

---

## Task 2: `snapshot()` function

**Files:**
- Modify: `trade_dna.py`
- Modify: `tests/test_trade_dna.py`

- [ ] **Step 1: Write failing integration tests for `snapshot()`**

Append to `tests/test_trade_dna.py`:

```python
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
```

- [ ] **Step 2: Run new tests — verify they fail**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot && python -m pytest tests/test_trade_dna.py::TestSnapshot -v 2>&1 | head -30
```

Expected: `AttributeError: module 'trade_dna' has no attribute 'snapshot'`

- [ ] **Step 3: Implement `snapshot()` in `trade_dna.py`**

Append to `trade_dna.py` (after the helper functions):

```python
# ── Per-strategy field definitions ────────────────────────────────────── #
# Maps strategy → list of (field_name, compute_fn)
# compute_fn receives the candles dict and returns a string value.

def _s1_fields(candles: dict) -> dict:
    out = {}
    daily = candles.get("daily")
    h1    = candles.get("h1")
    m3    = candles.get("m3")

    if daily is not None and not (hasattr(daily, "empty") and daily.empty):
        from strategy import calculate_ema, calculate_adx
        closes_d = daily["close"].astype(float) if hasattr(daily, "columns") else daily
        ema_d = calculate_ema(closes_d, 20)
        out["snap_trend_daily_ema_slope"]    = ema_slope(closes_d, 20)
        out["snap_trend_daily_price_vs_ema"] = price_vs_ema(float(closes_d.iloc[-1]), float(ema_d.iloc[-1]))
        if hasattr(daily, "columns") and len(daily) >= 20:
            adx_d = calculate_adx(daily)["adx"]
            out["snap_trend_daily_adx_state"] = adx_state(adx_d)
        else:
            out["snap_trend_daily_adx_state"] = ""

    if h1 is not None and not (hasattr(h1, "empty") and h1.empty):
        from strategy import calculate_ema
        closes_h = h1["close"].astype(float) if hasattr(h1, "columns") else h1
        ema_h = calculate_ema(closes_h, 20)
        out["snap_trend_h1_ema_slope"]    = ema_slope(closes_h, 20)
        out["snap_trend_h1_price_vs_ema"] = price_vs_ema(float(closes_h.iloc[-1]), float(ema_h.iloc[-1]))

    if m3 is not None and not (hasattr(m3, "empty") and m3.empty):
        from strategy import calculate_ema
        closes_m3 = m3["close"].astype(float) if hasattr(m3, "columns") else m3
        ema_m3 = calculate_ema(closes_m3, 20)
        out["snap_trend_m3_price_vs_ema"] = price_vs_ema(float(closes_m3.iloc[-1]), float(ema_m3.iloc[-1]))

    return out


def _s2_fields(candles: dict) -> dict:
    out = {}
    daily = candles.get("daily")
    if daily is None or (hasattr(daily, "empty") and daily.empty):
        return out
    from strategy import calculate_ema, calculate_rsi
    closes_d = daily["close"].astype(float) if hasattr(daily, "columns") else daily
    ema_d    = calculate_ema(closes_d, 20)
    rsi_d    = calculate_rsi(closes_d)
    out["snap_trend_daily_ema_slope"]    = ema_slope(closes_d, 20)
    out["snap_trend_daily_price_vs_ema"] = price_vs_ema(float(closes_d.iloc[-1]), float(ema_d.iloc[-1]))
    out["snap_trend_daily_rsi_bucket"]   = rsi_bucket(float(rsi_d.iloc[-1]))
    return out


def _s3_fields(candles: dict) -> dict:
    out = {}
    m15 = candles.get("m15")
    if m15 is None or (hasattr(m15, "empty") and m15.empty):
        return out
    from strategy import calculate_ema, calculate_adx
    closes_m15 = m15["close"].astype(float) if hasattr(m15, "columns") else m15
    ema_m15    = calculate_ema(closes_m15, 20)
    out["snap_trend_m15_ema_slope"]    = ema_slope(closes_m15, 20)
    out["snap_trend_m15_price_vs_ema"] = price_vs_ema(float(closes_m15.iloc[-1]), float(ema_m15.iloc[-1]))
    if hasattr(m15, "columns") and len(m15) >= 20:
        adx_m15 = calculate_adx(m15)["adx"]
        out["snap_trend_m15_adx_state"] = adx_state(adx_m15)
    else:
        out["snap_trend_m15_adx_state"] = ""
    return out


def _s4_fields(candles: dict) -> dict:
    out = {}
    daily = candles.get("daily")
    h1    = candles.get("h1")
    if daily is None or (hasattr(daily, "empty") and daily.empty):
        return out
    from strategy import calculate_ema, calculate_rsi
    closes_d = daily["close"].astype(float) if hasattr(daily, "columns") else daily
    ema_d    = calculate_ema(closes_d, 20)
    rsi_d    = calculate_rsi(closes_d)
    out["snap_trend_daily_ema_slope"]    = ema_slope(closes_d, 20)
    out["snap_trend_daily_price_vs_ema"] = price_vs_ema(float(closes_d.iloc[-1]), float(ema_d.iloc[-1]))
    out["snap_trend_daily_rsi_bucket"]   = rsi_bucket(float(rsi_d.iloc[-1]))
    if h1 is not None and not (hasattr(h1, "empty") and h1.empty):
        from strategy import calculate_ema as _ema
        closes_h = h1["close"].astype(float) if hasattr(h1, "columns") else h1
        ema_h    = _ema(closes_h, 20)
        out["snap_trend_h1_ema_slope"]    = ema_slope(closes_h, 20)
        out["snap_trend_h1_price_vs_ema"] = price_vs_ema(float(closes_h.iloc[-1]), float(ema_h.iloc[-1]))
    return out


def _s5_fields(candles: dict) -> dict:
    out = {}
    daily = candles.get("daily")
    h1    = candles.get("h1")
    m15   = candles.get("m15")
    if daily is not None and not (hasattr(daily, "empty") and daily.empty):
        from strategy import calculate_ema
        closes_d = daily["close"].astype(float) if hasattr(daily, "columns") else daily
        ema_d    = calculate_ema(closes_d, 20)
        out["snap_trend_daily_ema_slope"]    = ema_slope(closes_d, 20)
        out["snap_trend_daily_price_vs_ema"] = price_vs_ema(float(closes_d.iloc[-1]), float(ema_d.iloc[-1]))
    if h1 is not None and not (hasattr(h1, "empty") and h1.empty):
        from strategy import calculate_ema
        closes_h = h1["close"].astype(float) if hasattr(h1, "columns") else h1
        ema_h    = calculate_ema(closes_h, 20)
        out["snap_trend_h1_ema_slope"]    = ema_slope(closes_h, 20)
        out["snap_trend_h1_price_vs_ema"] = price_vs_ema(float(closes_h.iloc[-1]), float(ema_h.iloc[-1]))
    if m15 is not None and not (hasattr(m15, "empty") and m15.empty):
        from strategy import calculate_ema
        closes_m15 = m15["close"].astype(float) if hasattr(m15, "columns") else m15
        ema_m15    = calculate_ema(closes_m15, 20)
        out["snap_trend_m15_ema_slope"]    = ema_slope(closes_m15, 20)
        out["snap_trend_m15_price_vs_ema"] = price_vs_ema(float(closes_m15.iloc[-1]), float(ema_m15.iloc[-1]))
    return out


def _s6_fields(candles: dict) -> dict:
    out = {}
    daily = candles.get("daily")
    if daily is None or (hasattr(daily, "empty") and daily.empty):
        return out
    from strategy import calculate_ema, calculate_rsi
    closes_d = daily["close"].astype(float) if hasattr(daily, "columns") else daily
    ema_d    = calculate_ema(closes_d, 20)
    rsi_d    = calculate_rsi(closes_d)
    out["snap_trend_daily_ema_slope"]    = ema_slope(closes_d, 20)
    out["snap_trend_daily_price_vs_ema"] = price_vs_ema(float(closes_d.iloc[-1]), float(ema_d.iloc[-1]))
    out["snap_trend_daily_rsi_bucket"]   = rsi_bucket(float(rsi_d.iloc[-1]))
    return out


_STRATEGY_HANDLERS = {
    "S1": _s1_fields,
    "S2": _s2_fields,
    "S3": _s3_fields,
    "S4": _s4_fields,
    "S5": _s5_fields,
    "S6": _s6_fields,
}


def snapshot(strategy: str, symbol: str, candles: dict) -> dict:
    """
    Compute trend fingerprint fields for the given strategy at entry time.

    candles: dict with keys "daily" / "h1" / "m15" / "m3".
             Values are pd.DataFrame (OHLCV) or pd.Series (closes).
             Pass only the timeframes available — missing keys are skipped.

    Returns flat dict of snap_trend_* keys → bucketed string values.
    On any error: logs warning and returns {} so trades are never blocked.
    """
    handler = _STRATEGY_HANDLERS.get(strategy)
    if handler is None:
        logger.warning("trade_dna.snapshot: unknown strategy %s — skipping", strategy)
        return {}
    try:
        return handler(candles)
    except Exception as exc:
        logger.warning(
            "trade_dna.snapshot error for %s %s — skipping fingerprint: %s",
            strategy, symbol, exc,
        )
        return {}


def lookup(strategy: str, symbol: str, fingerprint: dict) -> dict:
    """
    Future drop-in replacement for claude_approve().
    Returns {"approved": bool, "reason": str, "matches": int, "win_rate": float}.
    Not yet implemented — raises NotImplementedError.
    """
    raise NotImplementedError(
        "trade_dna.lookup() is not yet implemented. "
        "Enable claude_filter or disable CLAUDE_FILTER_ENABLED instead."
    )
```

- [ ] **Step 4: Run all tests — verify they pass**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot && python -m pytest tests/test_trade_dna.py -v 2>&1 | tail -20
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot && git add trade_dna.py tests/test_trade_dna.py && git commit -m "feat(dna): implement snapshot() with per-strategy fingerprint computation"
```

---

## Task 3: Add `snap_trend_*` fields to `_TRADE_FIELDS` in `bot.py`

**Files:**
- Modify: `bot.py:103-122`

- [ ] **Step 1: Add the 10 new fields to `_TRADE_FIELDS`**

In `bot.py`, find the `_TRADE_FIELDS` list (line ~103). After the line:
```python
    # S/R clearance at entry (S2/S3/S4/S5/S6)
    "snap_sr_clearance_pct",
```

Add:
```python
    # Trade DNA trend fingerprint (recorded at entry for future pattern-match filter)
    "snap_trend_daily_ema_slope", "snap_trend_daily_price_vs_ema",
    "snap_trend_daily_rsi_bucket", "snap_trend_daily_adx_state",
    "snap_trend_h1_ema_slope", "snap_trend_h1_price_vs_ema",
    "snap_trend_m15_ema_slope", "snap_trend_m15_price_vs_ema",
    "snap_trend_m15_adx_state",
    "snap_trend_m3_price_vs_ema",
```

- [ ] **Step 2: Verify the field list parses cleanly**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot && python -c "import bot; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot && git add bot.py && git commit -m "feat(dna): add snap_trend_* fields to _TRADE_FIELDS"
```

---

## Task 4: Wire `dna_snapshot()` into S1 entry

**Files:**
- Modify: `bot.py` — `_execute_s1()` method (~line 1809)

S1 has `c["daily_df"]` (OHLCV DataFrame), `c["ltf_df"]` (3m OHLCV), and `htf_df` (1h OHLCV) in scope.

- [ ] **Step 1: Add import at top of `bot.py`**

Find the existing imports near line 34:
```python
from claude_filter import claude_approve
```

Add directly below it:
```python
from trade_dna import snapshot as dna_snapshot
```

- [ ] **Step 2: Wire into `_execute_s1()`**

Find this block in `_execute_s1()` (~line 1848):
```python
        trade["trade_id"] = uuid.uuid4().hex[:8]
        _log_trade(f"S1_{s1_sig}", trade)
```

Add the DNA snapshot call immediately before `_log_trade`:
```python
        trade["trade_id"] = uuid.uuid4().hex[:8]
        trade.update(dna_snapshot("S1", symbol, {
            "daily": c.get("daily_df"),
            # h1 not in S1 candidate dict — snap_trend_h1_* will record as ""
            "m3":    c.get("ltf_df"),
        }))
        _log_trade(f"S1_{s1_sig}", trade)
```

Note: `c` carries `daily_df` and `ltf_df` (3m) but not the 1h DataFrame, so `snap_trend_h1_*` will be empty string for S1 trades.

- [ ] **Step 3: Verify bot.py imports cleanly**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot && python -c "import bot; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Run full test suite**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot && python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: all existing tests still pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot && git add bot.py && git commit -m "feat(dna): wire dna_snapshot into S1 entry"
```

---

## Task 5: Wire `dna_snapshot()` into S2 entry

**Files:**
- Modify: `bot.py` — `_fire_s2()` method (~line 2204)

S2 has `sig["daily_df"]` (OHLCV DataFrame) in the sig dict.

- [ ] **Step 1: Wire into `_fire_s2()`**

Find this block in `_fire_s2()`:
```python
        trade["trade_id"] = uuid.uuid4().hex[:8]
        _log_trade("S2_LONG", trade)
```

Add immediately before `_log_trade`:
```python
        trade["trade_id"] = uuid.uuid4().hex[:8]
        trade.update(dna_snapshot("S2", symbol, {
            "daily": sig.get("daily_df"),
        }))
        _log_trade("S2_LONG", trade)
```

- [ ] **Step 2: Run full test suite**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot && python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot && git add bot.py && git commit -m "feat(dna): wire dna_snapshot into S2 entry"
```

---

## Task 6: Wire `dna_snapshot()` into S3 entry

**Files:**
- Modify: `bot.py` — `_fire_s3()` method (~line 2269)

S3 has `sig["m15_df"]` (15m OHLCV DataFrame) in the sig dict. No daily_df in the S3 sig dict.

- [ ] **Step 1: Wire into `_fire_s3()`**

Find this block in `_fire_s3()`:
```python
        trade["trade_id"] = uuid.uuid4().hex[:8]
        _log_trade("S3_LONG", trade)
```

Add immediately before `_log_trade`:
```python
        trade["trade_id"] = uuid.uuid4().hex[:8]
        trade.update(dna_snapshot("S3", symbol, {
            "m15": sig.get("m15_df"),
        }))
        _log_trade("S3_LONG", trade)
```

- [ ] **Step 2: Run full test suite**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot && python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot && git add bot.py && git commit -m "feat(dna): wire dna_snapshot into S3 entry"
```

---

## Task 7: Wire `dna_snapshot()` into S4 entry

**Files:**
- Modify: `bot.py` — `_fire_s4()` method (~line 2330)

S4 has `sig["daily_df"]` (OHLCV). The 1h DataFrame is `htf_df` in the original scan but is **not** in the S4 sig dict. S4 h1 fields will be empty string for now — acceptable for recording phase.

- [ ] **Step 1: Wire into `_fire_s4()`**

Find this block in `_fire_s4()`:
```python
        trade["trade_id"] = uuid.uuid4().hex[:8]
        _log_trade("S4_SHORT", trade)
```

Add immediately before `_log_trade`:
```python
        trade["trade_id"] = uuid.uuid4().hex[:8]
        trade.update(dna_snapshot("S4", symbol, {
            "daily": sig.get("daily_df"),
            # h1 not carried in sig dict — will be added when lookup() is implemented
        }))
        _log_trade("S4_SHORT", trade)
```

- [ ] **Step 2: Run full test suite**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot && python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot && git add bot.py && git commit -m "feat(dna): wire dna_snapshot into S4 entry"
```

---

## Task 8: Wire `dna_snapshot()` into S5 entries

**Files:**
- Modify: `bot.py` — `_execute_s5()` (~line 1872) and pending watcher (~line 2750)

S5 has `m15_df` passed directly to `_execute_s5()`. No daily_df or h1_df in scope at fire time. These will be empty strings for now — acceptable for recording phase.

- [ ] **Step 1: Wire into `_execute_s5()` LONG branch**

Find this block in `_execute_s5()` LONG branch:
```python
            trade["trade_id"] = uuid.uuid4().hex[:8]
            _log_trade("S5_LONG", trade)
```

Add immediately before `_log_trade`:
```python
            trade["trade_id"] = uuid.uuid4().hex[:8]
            trade.update(dna_snapshot("S5", symbol, {
                "m15": m15_df,
            }))
            _log_trade("S5_LONG", trade)
```

- [ ] **Step 2: Wire into `_execute_s5()` SHORT branch**

Find the SHORT branch block:
```python
            trade["trade_id"] = uuid.uuid4().hex[:8]
            _log_trade("S5_SHORT", trade)
```

Add immediately before `_log_trade`:
```python
            trade["trade_id"] = uuid.uuid4().hex[:8]
            trade.update(dna_snapshot("S5", symbol, {
                "m15": m15_df,
            }))
            _log_trade("S5_SHORT", trade)
```

- [ ] **Step 3: Wire into pending watcher `_log_trade(f"S5_{side}", trade)` (~line 2777)**

Find the pending watcher `_log_trade` call (the one around line 2777 inside the entry watcher, not inside `_execute_s5`). Its context:
```python
        trade["trade_id"] = uuid.uuid4().hex[:8]
        _log_trade(f"S5_{side}", trade)
```

Add immediately before `_log_trade`:
```python
        trade["trade_id"] = uuid.uuid4().hex[:8]
        trade.update(dna_snapshot("S5", symbol, {}))  # no DFs available in watcher
        _log_trade(f"S5_{side}", trade)
```

Note: There are multiple S5 `_log_trade` calls (~lines 2777, 2844, 2909). Apply the same pattern to all of them. Each will have the same empty `{}` candles dict since DFs are not carried in pending_signals.

- [ ] **Step 4: Run full test suite**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot && python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot && git add bot.py && git commit -m "feat(dna): wire dna_snapshot into S5 entries"
```

---

## Task 9: Wire `dna_snapshot()` into S6 entry

**Files:**
- Modify: `bot.py` — `_fire_s6()` method (~line 2398)

S6 has `sig["daily_df"]` (OHLCV DataFrame) in the sig dict.

- [ ] **Step 1: Wire into `_fire_s6()`**

Find this block in `_fire_s6()`:
```python
        trade["trade_id"] = uuid.uuid4().hex[:8]
        _log_trade("S6_SHORT", trade)
```

Add immediately before `_log_trade`:
```python
        trade["trade_id"] = uuid.uuid4().hex[:8]
        trade.update(dna_snapshot("S6", symbol, {
            "daily": sig.get("daily_df"),
        }))
        _log_trade("S6_SHORT", trade)
```

- [ ] **Step 2: Run full test suite**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot && python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot && git add bot.py && git commit -m "feat(dna): wire dna_snapshot into S6 entry"
```

---

## Task 10: Smoke test — verify CSV output

**Files:**
- No code changes

- [ ] **Step 1: Confirm new fields appear in CSV header**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot && python -c "
import csv, config
with open(config.TRADE_LOG) as f:
    fields = next(csv.reader(f))
dna_fields = [f for f in fields if f.startswith('snap_trend_')]
print(f'DNA fields in CSV ({len(dna_fields)}):')
for f in dna_fields: print(' ', f)
"
```

Expected: 10 fields printed, matching the spec table exactly.

- [ ] **Step 2: Run full test suite one final time**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot && python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: all tests pass, no regressions.

- [ ] **Step 3: Final commit**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot && git add -p && git commit -m "feat(dna): complete trade DNA fingerprint recording phase"
```
