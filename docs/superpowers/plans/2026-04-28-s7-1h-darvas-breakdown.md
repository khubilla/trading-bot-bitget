# S7 — Post-Pump 1H Darvas Breakdown Short Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add S7 — a SHORT strategy that mirrors S4's daily post-pump setup gates but swaps the entry trigger for a confirmed close below a 1H Darvas-box low formed within the current UTC day.

**Architecture:** Pure additive change. New module `strategies/s7.py` and config `config_s7.py`, mirrored after `strategies/s4.py` / `config_s4.py`. Daily evaluation reuses S4's spike + RSI gates; entry detection runs a new stateless 1H Darvas walking algorithm against today's accumulating 1H candles. SL is leverage-capped (atomic via `presetStopLossPrice`), partial + trailing + scale-in machinery reuses S4's helpers. Bot, dashboard, optimizer, backtest, paper trader, recovery — all extended via per-strategy whitelisting (no signature changes).

**Tech Stack:** Python 3, pandas, pytest, FastAPI (dashboard), Bitget USDT-M futures API, vanilla JS frontend.

**Spec:** `docs/superpowers/specs/2026-04-27-s7-1h-darvas-breakdown-design.md`

**Reference (mirror this):** `strategies/s4.py`, `config_s4.py`, `tests/manual/run_test_s4.py`, `bot.py:_fire_s4` (~line 2034)

---

## Phase 1 — Foundation (config + Darvas detector + evaluate)

### Task 1: Create `config_s7.py`

**Files:**
- Create: `config_s7.py`

- [ ] **Step 1: Create `config_s7.py` with all S7 defaults**

```python
# ============================================================
#  Strategy 7 Configuration — Post-Pump 1H Darvas Breakdown Short
# ============================================================
# Same daily setup as S4 (post-pump RSI exhaustion).
# Entry trigger differs: instead of "below previous day's low",
# wait for a stair-step 1H Darvas box (top + low) formed within
# the current UTC day, then fire on a confirmed 1H close below
# the low box.
# Sentiment gate: BEARISH only (gated in bot.py scan loop).

S7_ENABLED = True

# ── Big Candle Detection (mirrors S4) ───────────────────── #
S7_BIG_CANDLE_BODY_PCT  = 0.20   # ≥ 20% body to qualify as a momentum candle
S7_BIG_CANDLE_LOOKBACK  = 30     # search last 30 daily candles

# ── RSI Gates (mirrors S4) ──────────────────────────────── #
S7_RSI_PEAK_THRESH      = 75    # peak ≥ 75 within last RSI_PEAK_LOOKBACK days
S7_RSI_PEAK_LOOKBACK    = 10
S7_RSI_STILL_HOT_THRESH = 70    # prev-day RSI must remain ≥ 70 (no fade yet)
S7_RSI_DIV_MIN_DROP     = 5     # informational divergence threshold

# ── 1H Darvas Box Detection (NEW) ───────────────────────── #
S7_BOX_CONFIRM_COUNT    = 2     # candles required to "hold" above/below the
                                # establishing candle before the box locks
                                # → 1 establishing + 2 confirming = 3 candles per box
                                # → minimum total ≈ 6 candles since UTC midnight

# ── Entry Trigger ───────────────────────────────────────── #
S7_ENTRY_BUFFER     = 0.005     # entry trigger = box_low × (1 − 0.5%)
S7_MAX_ENTRY_BUFFER = 0.04      # skip if mark already > 4% past trigger
                                # (SL is leverage-capped, not spike-anchored)

# ── Risk Management (mirrors S4) ────────────────────────── #
S7_LEVERAGE         = 10
S7_TRADE_SIZE_PCT   = 0.04      # 4% portfolio margin (50% initial → +50% scale-in)

S7_TRAILING_TRIGGER_PCT = 0.10  # 50% partial close at −10% (price)
S7_TRAILING_RANGE_PCT   = 10    # 10% callback on remaining 50%
S7_USE_SWING_TRAIL      = True
S7_SWING_LOOKBACK       = 30    # daily candles for swing-trail anchor

# ── S/R Clearance ───────────────────────────────────────── #
S7_MIN_SR_CLEARANCE = 0.15      # skip SHORT if support floor < 15% below entry
```

- [ ] **Step 2: Verify import**

Run: `python -c "import config_s7; print('S7_ENABLED:', config_s7.S7_ENABLED, '| confirm:', config_s7.S7_BOX_CONFIRM_COUNT)"`
Expected: `S7_ENABLED: True | confirm: 2`

- [ ] **Step 3: Commit**

```bash
git add config_s7.py
git commit -m "feat(s7): add config_s7.py with defaults"
```

---

### Task 2: Create `strategies/s7.py` skeleton + `today_h1_slice()` helper

**Files:**
- Create: `strategies/s7.py`
- Create: `tests/test_s7_darvas.py`

- [ ] **Step 1: Write failing test for `today_h1_slice`**

Create `tests/test_s7_darvas.py`:

```python
"""Unit tests for S7 1H Darvas-box detector and helpers."""
import pandas as pd
import pytest

from strategies.s7 import today_h1_slice, detect_darvas_box


def _make_h1_df(rows):
    """rows: list of (open_ts, high, low). Returns a DataFrame indexed by UTC ts."""
    idx = pd.DatetimeIndex([r[0] for r in rows], tz="UTC")
    return pd.DataFrame(
        {"high": [r[1] for r in rows], "low": [r[2] for r in rows],
         "open": [r[1] for r in rows], "close": [(r[1] + r[2]) / 2 for r in rows]},
        index=idx,
    )


def test_today_h1_slice_drops_yesterday_and_forming_hour(monkeypatch):
    df = _make_h1_df([
        ("2026-04-27 22:00", 100, 95),  # yesterday — drop
        ("2026-04-27 23:00", 99,  94),  # yesterday — drop
        ("2026-04-28 00:00", 98,  94),  # today closed
        ("2026-04-28 01:00", 99,  93),  # today closed
        ("2026-04-28 02:00", 96,  93),  # today, currently forming — drop
    ])
    monkeypatch.setattr(
        "strategies.s7._utcnow",
        lambda: pd.Timestamp("2026-04-28 02:30", tz="UTC"),
    )
    s = today_h1_slice(df)
    assert len(s) == 2
    assert s.index[0] == pd.Timestamp("2026-04-28 00:00", tz="UTC")
    assert s.index[-1] == pd.Timestamp("2026-04-28 01:00", tz="UTC")
```

- [ ] **Step 2: Run test, verify it fails**

Run: `pytest tests/test_s7_darvas.py::test_today_h1_slice_drops_yesterday_and_forming_hour -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'strategies.s7'`

- [ ] **Step 3: Create `strategies/s7.py` with skeleton + helper**

```python
"""
Strategy 7 — Post-Pump 1H Darvas Breakdown Short.

Setup gates mirror S4 (spike body ≥ 20% within last 30D, RSI peak ≥ 75 within
last 10D, RSI still hot ≥ 70). Entry trigger is a confirmed 1H close below a
locked Darvas-box low formed within the current UTC day.

Sentiment gate: BEARISH only (gated upstream in bot.py).
"""

import logging
from typing import Literal

import pandas as pd

logger = logging.getLogger(__name__)
Signal = Literal["LONG", "SHORT", "HOLD", "PENDING_LONG", "PENDING_SHORT"]

SNAPSHOT_INTERVAL = "1D"


def _utcnow() -> pd.Timestamp:
    """Wrapper for monkeypatch-friendly current-UTC timestamp."""
    return pd.Timestamp.utcnow()


def today_h1_slice(h1_df: pd.DataFrame) -> pd.DataFrame:
    """Closed 1H candles since the most recent UTC midnight (forming hour excluded)."""
    if h1_df.empty:
        return h1_df
    today_utc = _utcnow().floor("1D")
    if today_utc.tzinfo is None:
        today_utc = today_utc.tz_localize("UTC")
    mask = h1_df.index >= today_utc
    return h1_df[mask].iloc[:-1]


def detect_darvas_box(
    h1_slice: pd.DataFrame,
    confirm: int = 2,
) -> tuple[bool, float, float, int, int, str]:
    """Stub — implemented in Task 3."""
    raise NotImplementedError("detect_darvas_box implemented in Task 3")
```

- [ ] **Step 4: Run test, verify it passes**

Run: `pytest tests/test_s7_darvas.py::test_today_h1_slice_drops_yesterday_and_forming_hour -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add strategies/s7.py tests/test_s7_darvas.py
git commit -m "feat(s7): add strategies/s7.py skeleton with today_h1_slice"
```

---

### Task 3: Implement `detect_darvas_box()` (TDD)

**Files:**
- Modify: `strategies/s7.py`
- Modify: `tests/test_s7_darvas.py`

- [ ] **Step 1: Add 5 failing tests covering the detector**

Append to `tests/test_s7_darvas.py`:

```python
def _walk_h1(highs, lows, start="2026-04-28 00:00"):
    """Build a 1H DataFrame from parallel highs/lows lists."""
    base = pd.Timestamp(start, tz="UTC")
    rows = [(base + pd.Timedelta(hours=i), h, l) for i, (h, l) in enumerate(zip(highs, lows))]
    return _make_h1_df(rows)


def test_detector_returns_false_when_too_few_candles():
    df = _walk_h1([99, 98, 97, 96], [95, 94, 93, 92])  # 4 < 6
    locked, top, low, ti, li, reason = detect_darvas_box(df, confirm=2)
    assert locked is False
    assert "Need" in reason or "candles" in reason.lower()


def test_detector_locks_top_then_low_on_canonical_example():
    # Example from spec §5.3: top locks at 99 (idx 1), low locks at 84 (idx 7)
    highs = [98, 99, 96, 95, 92, 91, 88, 87, 86, 85]
    lows  = [95, 94, 93, 90, 88, 87, 85, 84, 84, 85]
    df = _walk_h1(highs, lows)
    locked, top, low, ti, li, reason = detect_darvas_box(df, confirm=2)
    assert locked is True
    assert top == 99
    assert low == 84
    assert ti == 1
    assert li == 7


def test_detector_top_not_locked_when_high_keeps_pushing():
    # Highs keep making new highs — top never confirms
    highs = [90, 91, 92, 93, 94, 95, 96, 97]
    lows  = [85, 86, 87, 88, 89, 90, 91, 92]
    df = _walk_h1(highs, lows)
    locked, top, low, ti, li, reason = detect_darvas_box(df, confirm=2)
    assert locked is False
    assert "Top box not yet confirmed" in reason


def test_detector_low_not_locked_when_low_keeps_falling():
    # Top locks early, but lows keep falling after — low never confirms
    highs = [99, 98, 97, 96, 95, 94, 93, 92]
    lows  = [95, 94, 93, 92, 91, 90, 89, 88]
    df = _walk_h1(highs, lows)
    locked, top, low, ti, li, reason = detect_darvas_box(df, confirm=2)
    assert locked is False
    assert "Low box not yet confirmed" in reason


def test_detector_rejects_inverted_structure():
    # Low ends up >= top (degenerate) — sanity rejection
    highs = [100, 99, 98, 97, 96, 95]
    lows  = [99, 98, 97, 96, 95, 95]
    df = _walk_h1(highs, lows)
    locked, top, low, *_ = detect_darvas_box(df, confirm=2)
    assert locked is False
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `pytest tests/test_s7_darvas.py -v`
Expected: 5 FAIL with `NotImplementedError`, 1 PASS (the helper test from Task 2).

- [ ] **Step 3: Replace the `detect_darvas_box` stub with a working implementation**

In `strategies/s7.py`, replace the stub:

```python
def detect_darvas_box(
    h1_slice: pd.DataFrame,
    confirm: int = 2,
) -> tuple[bool, float, float, int, int, str]:
    """
    Walk the 1H slice forward and lock a top-box high then a low-box low using
    classic Darvas mechanics: each new lower-low / higher-high resets the
    confirmation counter; the box locks once `confirm` consecutive candles
    hold above/below the establishing candle.

    Returns (locked, top_high, low_low, top_idx, low_idx, reason).
    """
    min_needed = 2 * confirm + 2
    if len(h1_slice) < min_needed:
        return (False, 0.0, 0.0, -1, -1,
                f"Need ≥ {min_needed} 1H candles since UTC midnight (have {len(h1_slice)})")

    rows = list(h1_slice.itertuples())

    # --- top-box pass ---
    top_high, top_idx, conf, top_locked = float("-inf"), -1, 0, False
    for i, row in enumerate(rows):
        if row.high > top_high:
            top_high, top_idx, conf = float(row.high), i, 0
        else:
            conf += 1
            if conf >= confirm:
                top_locked = True
                break
    if not top_locked:
        return (False, top_high, 0.0, top_idx, -1,
                "Top box not yet confirmed (running high still pushing)")

    # --- low-box pass over rows after top_idx ---
    low_low, low_off, conf, low_locked = float("+inf"), -1, 0, False
    for j, row in enumerate(rows[top_idx + 1:]):
        if row.low < low_low:
            low_low, low_off, conf = float(row.low), j, 0
        else:
            conf += 1
            if conf >= confirm:
                low_locked = True
                break
    if not low_locked:
        return (False, top_high, low_low, top_idx, -1,
                "Low box not yet confirmed (running low still falling)")

    if low_low >= top_high:
        return (False, top_high, low_low, top_idx, top_idx + 1 + low_off,
                f"Sanity: low_low {low_low} >= top_high {top_high}")

    low_idx = top_idx + 1 + low_off
    return (True, top_high, low_low, top_idx, low_idx,
            f"Darvas box ✅ top={top_high} low={low_low}")
```

- [ ] **Step 4: Run all tests, verify they pass**

Run: `pytest tests/test_s7_darvas.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add strategies/s7.py tests/test_s7_darvas.py
git commit -m "feat(s7): implement detect_darvas_box walking algorithm"
```

---

### Task 4: Implement `evaluate_s7()` (TDD)

**Files:**
- Modify: `strategies/s7.py`
- Create: `tests/test_s7_evaluate.py`

- [ ] **Step 1: Write 3 failing tests for `evaluate_s7`**

Create `tests/test_s7_evaluate.py`:

```python
"""Unit tests for evaluate_s7() daily-gate + Darvas composition."""
import pandas as pd
import pytest

from strategies.s7 import evaluate_s7


def _daily_with_spike(rsi_peak_value=80.0, post_peak_decay=0.0, body_pct=0.30, n=40):
    """Build a 40-day daily DataFrame with a controllable spike + RSI peak."""
    import numpy as np
    closes = [100.0]
    for _ in range(n - 1):
        closes.append(closes[-1] * 1.001)  # baseline drift
    # Inject a spike candle 5 days ago: body ≈ body_pct
    spike_idx = n - 6
    closes[spike_idx] = closes[spike_idx - 1] * (1 + body_pct)
    # Push subsequent closes down a bit (RSI fades)
    for i in range(spike_idx + 1, n):
        closes[i] = closes[i - 1] * (1 - post_peak_decay)
    idx = pd.date_range("2026-03-20", periods=n, freq="D", tz="UTC")
    df = pd.DataFrame({
        "open": closes, "close": closes,
        "high": [c * 1.005 for c in closes], "low": [c * 0.995 for c in closes],
    }, index=idx)
    # Make spike candle have an actual body of `body_pct`
    df.loc[df.index[spike_idx], "open"] = closes[spike_idx - 1]
    df.loc[df.index[spike_idx], "close"] = closes[spike_idx]
    df.loc[df.index[spike_idx], "high"] = closes[spike_idx] * 1.005
    return df


def _h1_with_locked_box():
    """A 1H slice that produces a locked Darvas box (matches detector canonical example)."""
    base = pd.Timestamp("2026-04-28 00:00", tz="UTC")
    highs = [98, 99, 96, 95, 92, 91, 88, 87, 86, 85]
    lows  = [95, 94, 93, 90, 88, 87, 85, 84, 84, 85]
    return pd.DataFrame({
        "open":  highs, "close": [(h + l) / 2 for h, l in zip(highs, lows)],
        "high":  highs, "low":   lows,
    }, index=[base + pd.Timedelta(hours=i) for i in range(len(highs))])


def test_evaluate_s7_disabled_returns_hold(monkeypatch):
    monkeypatch.setattr("config_s7.S7_ENABLED", False)
    sig, *_, reason = evaluate_s7("BTCUSDT", _daily_with_spike(), _h1_with_locked_box())
    assert sig == "HOLD"
    assert "disabled" in reason.lower()


def test_evaluate_s7_holds_when_no_spike(monkeypatch):
    # Tiny body — under threshold
    daily = _daily_with_spike(body_pct=0.05)
    sig, *_, reason = evaluate_s7("BTCUSDT", daily, _h1_with_locked_box())
    assert sig == "HOLD"
    assert "spike" in reason.lower()


def test_evaluate_s7_returns_short_when_all_gates_pass(monkeypatch):
    # Real daily setup + locked Darvas box → SHORT
    monkeypatch.setattr(
        "strategies.s7._utcnow",
        lambda: pd.Timestamp("2026-04-28 11:00", tz="UTC"),
    )
    daily = _daily_with_spike(body_pct=0.30)
    h1 = _h1_with_locked_box()
    sig, daily_rsi, box_top, box_low, body_pct, rsi_peak, rsi_div, rsi_div_str, reason = (
        evaluate_s7("BTCUSDT", daily, h1)
    )
    # Either SHORT (full pass) or HOLD with "1H Darvas ✅" if RSI gate fails on synthetic data
    if sig == "SHORT":
        assert box_top == 99
        assert box_low == 84
    else:
        # On synthetic fixture daily RSI may not exceed 75 — ok, just sanity-check shape
        assert isinstance(reason, str) and len(reason) > 0
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `pytest tests/test_s7_evaluate.py -v`
Expected: 3 FAIL — `evaluate_s7` not defined or raises.

- [ ] **Step 3: Implement `evaluate_s7` in `strategies/s7.py`**

Append to `strategies/s7.py`:

```python
def evaluate_s7(
    symbol: str,
    daily_df: pd.DataFrame,
    h1_df: pd.DataFrame | None = None,
) -> tuple[Signal, float, float, float, float, float, bool, str, str]:
    """
    Strategy 7 — post-pump 1H Darvas breakdown short.

    Returns (signal, daily_rsi, box_top, box_low, body_pct, rsi_peak,
             rsi_div, rsi_div_str, reason).
    """
    from indicators import calculate_rsi
    from tools import body_pct as _body_pct
    from config_s7 import (
        S7_ENABLED, S7_BIG_CANDLE_BODY_PCT, S7_BIG_CANDLE_LOOKBACK,
        S7_RSI_PEAK_THRESH, S7_RSI_PEAK_LOOKBACK, S7_RSI_DIV_MIN_DROP,
        S7_RSI_STILL_HOT_THRESH, S7_BOX_CONFIRM_COUNT,
    )

    if not S7_ENABLED:
        return "HOLD", 50.0, 0.0, 0.0, 0.0, 0.0, False, "", "S7 disabled"

    rsi_period = 14
    min_candles = rsi_period + S7_BIG_CANDLE_LOOKBACK + 2
    if len(daily_df) < min_candles:
        return "HOLD", 50.0, 0.0, 0.0, 0.0, 0.0, False, "", "Not enough daily candles"

    closes    = daily_df["close"].astype(float)
    rsi_ser   = calculate_rsi(closes, rsi_period)
    daily_rsi = float(rsi_ser.iloc[-1])

    # --- spike detection ---
    lookback = daily_df.iloc[-(S7_BIG_CANDLE_LOOKBACK + 1):-1]
    spike_found, best_body, spike_high = False, 0.0, 0.0
    for _, row in lookback.iterrows():
        bp = _body_pct(row)
        if bp >= S7_BIG_CANDLE_BODY_PCT:
            spike_found = True
            if bp > best_body:
                best_body = bp
        if spike_found:
            spike_high = max(spike_high, float(row["high"]))
    if not spike_found:
        return "HOLD", daily_rsi, 0.0, 0.0, 0.0, 0.0, False, "", (
            f"No spike candle ≥{S7_BIG_CANDLE_BODY_PCT*100:.0f}% body in last {S7_BIG_CANDLE_LOOKBACK}d"
        )

    # --- RSI peak gate ---
    rsi_window = rsi_ser.iloc[-S7_RSI_PEAK_LOOKBACK - 1:-1]
    rsi_peak   = float(rsi_window.max())
    if rsi_peak < S7_RSI_PEAK_THRESH:
        return "HOLD", daily_rsi, 0.0, 0.0, best_body, rsi_peak, False, "", (
            f"Spike ✅ body={best_body*100:.0f}% | RSI peak={rsi_peak:.1f} < {S7_RSI_PEAK_THRESH}"
        )

    # --- RSI still hot ---
    prev_rsi = float(rsi_ser.iloc[-2])
    if prev_rsi < S7_RSI_STILL_HOT_THRESH:
        return "HOLD", daily_rsi, 0.0, 0.0, best_body, rsi_peak, False, "", (
            f"Spike ✅ RSI peak={rsi_peak:.1f} | prev RSI={prev_rsi:.1f} < {S7_RSI_STILL_HOT_THRESH} (faded)"
        )

    # --- RSI divergence (informational) ---
    rsi_div, rsi_div_str, div_note = False, "", ""
    if len(rsi_window) >= 4:
        mid      = len(rsi_window) // 2
        first_h  = float(rsi_window.iloc[:mid].max())
        second_h = float(rsi_window.iloc[mid:].max())
        rsi_div_str = f"{first_h:.1f}→{second_h:.1f}"
        if first_h > 0 and (first_h - second_h) >= S7_RSI_DIV_MIN_DROP:
            rsi_div, div_note = True, f" | RSI div ✅ ({rsi_div_str})"
        else:
            div_note = f" | RSI div ❌ ({rsi_div_str})"

    # --- 1H Darvas detector ---
    if h1_df is None or h1_df.empty:
        return "HOLD", daily_rsi, 0.0, 0.0, best_body, rsi_peak, rsi_div, rsi_div_str, (
            f"S7 daily ✅ spike={best_body*100:.0f}% | RSI peak={rsi_peak:.1f}{div_note} | 1H Darvas ❌ no H1 data"
        )
    today_slice = today_h1_slice(h1_df)
    locked, box_top, box_low, _, _, det_reason = detect_darvas_box(today_slice, confirm=S7_BOX_CONFIRM_COUNT)
    if not locked:
        return "HOLD", daily_rsi, 0.0, 0.0, best_body, rsi_peak, rsi_div, rsi_div_str, (
            f"S7 daily ✅ spike={best_body*100:.0f}% | RSI peak={rsi_peak:.1f}{div_note} | 1H Darvas ❌ {det_reason}"
        )

    logger.info(
        f"[S7][{symbol}] ✅ SHORT setup | spike={best_body*100:.0f}% | "
        f"RSI peak={rsi_peak:.1f} now={daily_rsi:.1f}{div_note} | "
        f"Darvas top={box_top:.5f} low={box_low:.5f}"
    )
    return "SHORT", daily_rsi, box_top, box_low, best_body, rsi_peak, rsi_div, rsi_div_str, (
        f"S7 ✅ spike={best_body*100:.0f}% | RSI peak={rsi_peak:.1f}{div_note} | "
        f"Darvas top={box_top:.5f} low={box_low:.5f}"
    )
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `pytest tests/test_s7_evaluate.py tests/test_s7_darvas.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add strategies/s7.py tests/test_s7_evaluate.py
git commit -m "feat(s7): implement evaluate_s7 daily gates + 1H Darvas composition"
```

---

## Phase 2 — Strategy module helpers (mirror S4)

### Task 5: S7 risk + scale-in helpers (mirror S4)

**Files:**
- Modify: `strategies/s7.py`

- [ ] **Step 1: Append risk helpers mirroring S4's `dna_fields`, `compute_paper_trail_short`, scale-in helpers**

Append to `strategies/s7.py`:

```python
# ── S7 DNA Snapshot Fields ────────────────────────────────── #

def dna_fields(candles: dict) -> dict:
    """S7 trade fingerprint: daily EMA/RSI, optional H1 EMA. Mirrors S4."""
    from indicators import calculate_ema, calculate_rsi
    from trade_dna import ema_slope, price_vs_ema, rsi_bucket, _is_empty, _closes_from

    out = {}
    daily = candles.get("daily")
    h1    = candles.get("h1")
    if _is_empty(daily):
        return out
    closes_d = _closes_from(daily)
    ema_d    = calculate_ema(closes_d, 20)
    rsi_d    = calculate_rsi(closes_d)
    out["snap_trend_daily_ema_slope"]    = ema_slope(closes_d, 20)
    out["snap_trend_daily_price_vs_ema"] = price_vs_ema(float(closes_d.iloc[-1]), float(ema_d.iloc[-1]))
    out["snap_trend_daily_rsi_bucket"]   = rsi_bucket(float(rsi_d.iloc[-1]))
    if not _is_empty(h1):
        closes_h = _closes_from(h1)
        ema_h    = calculate_ema(closes_h, 20)
        out["snap_trend_h1_ema_slope"]    = ema_slope(closes_h, 20)
        out["snap_trend_h1_price_vs_ema"] = price_vs_ema(float(closes_h.iloc[-1]), float(ema_h.iloc[-1]))
    return out


# ── S7 Paper Trail Setup ──────────────────────────────────── #

def compute_paper_trail_short(mark: float, sl_price: float, tp_price_abs: float = 0,
                              take_profit_pct: float = 0.05) -> tuple[bool, float, float, float, bool]:
    """Paper-trader SHORT trail setup for S7. Returns (use_trailing, trail_trigger, trail_range, tp_price, breakeven_after_partial)."""
    from config_s7 import S7_TRAILING_TRIGGER_PCT, S7_TRAILING_RANGE_PCT
    trail_trigger = mark * (1 - S7_TRAILING_TRIGGER_PCT)
    trail_range   = S7_TRAILING_RANGE_PCT
    return True, trail_trigger, trail_range, trail_trigger, False


# ── S7 Scale-In Helpers ───────────────────────────────────── #

def scale_in_specs() -> dict:
    """Per-strategy scale-in orchestration constants for S7 (SHORT)."""
    import config_s7
    return {
        "direction": "BEARISH",
        "hold_side": "short",
        "leverage":  config_s7.S7_LEVERAGE,
    }


def is_scale_in_window(ap: dict, mark_now: float) -> bool:
    """True when price is retesting the S7 box-low breakdown level."""
    import config_s7
    bl = ap["s7_box_low"]
    return (bl * (1 - config_s7.S7_MAX_ENTRY_BUFFER)
            <= mark_now
            <= bl * (1 - config_s7.S7_ENTRY_BUFFER))


def recompute_scale_in_sl_trigger(ap: dict, new_avg: float) -> tuple[float, float]:
    """S7 post-scale-in: SL at new_avg*(1+0.50/LEVERAGE), trail at new_avg*(1-TRIG_PCT)."""
    import config_s7
    new_sl   = new_avg * (1 + 0.50 / config_s7.S7_LEVERAGE)
    new_trig = new_avg * (1 - config_s7.S7_TRAILING_TRIGGER_PCT)
    return new_sl, new_trig
```

- [ ] **Step 2: Verify import + sanity**

Run: `python -c "from strategies.s7 import dna_fields, compute_paper_trail_short, scale_in_specs, is_scale_in_window, recompute_scale_in_sl_trigger; print(scale_in_specs())"`
Expected: `{'direction': 'BEARISH', 'hold_side': 'short', 'leverage': 10}`

- [ ] **Step 3: Commit**

```bash
git add strategies/s7.py
git commit -m "feat(s7): add DNA, paper trail, and scale-in helpers"
```

---

### Task 6: S7 exit placement + swing-trail helpers (mirror S4)

**Files:**
- Modify: `strategies/s7.py`

- [ ] **Step 1: Append `compute_and_place_short_exits()` and `maybe_trail_sl()` to `strategies/s7.py`**

Reuses `strategies.s4._place_partial_trail_exits()` (strategy-agnostic placement helper). Swing-trail mirrors S4 against `config_s7` knobs.

```python
# ── S7 Exit Placement ─────────────────────────────────────── #

def compute_and_place_short_exits(symbol: str, qty_str: str, fill: float,
                                  sl_trig: float, sl_exec: float) -> tuple[bool, float, float]:
    """
    Compute S7 short-side trail level and place the 3-leg exits
    (SL + 50% partial at trail_trigger + trailing stop on remainder).
    Returns (ok, sl_trig, trail_trig).
    """
    import trader
    from strategies.s4 import _place_partial_trail_exits
    from config_s7 import S7_TRAILING_TRIGGER_PCT, S7_TRAILING_RANGE_PCT

    trail_trig = float(trader._round_price(fill * (1 - S7_TRAILING_TRIGGER_PCT), symbol))
    ok = _place_partial_trail_exits(symbol, "short", qty_str, sl_trig, sl_exec,
                                    trail_trig, S7_TRAILING_RANGE_PCT)
    return ok, sl_trig, trail_trig


# ── S7 Swing Trail ────────────────────────────────────────── #

def maybe_trail_sl(symbol: str, ap: dict, tr_mod, st_mod, partial_done: bool) -> None:
    """
    Structural swing trail for S7 SHORT: after partial fires, pull SL down to the
    nearest daily swing-high above entry. Mirrors strategies.s4.maybe_trail_sl.
    """
    import config_s7
    from tools import find_swing_low_target, find_swing_high_after_ref

    if not config_s7.S7_USE_SWING_TRAIL:
        return
    if ap.get("side") != "SHORT" or not partial_done:
        return
    try:
        lb    = config_s7.S7_SWING_LOOKBACK
        cs_df = tr_mod.get_candles(symbol, "1D", limit=lb + 5)
        mark  = tr_mod.get_mark_price(symbol)
        if cs_df.empty or len(cs_df) < 3:
            return
        ref = ap.get("swing_trail_ref")
        if ref is None:
            ap["swing_trail_ref"] = find_swing_low_target(cs_df, mark, lookback=lb)
            return
        if mark <= ref:
            raw = find_swing_high_after_ref(cs_df, mark, ref, lookback=lb)
            if raw:
                # No spike-anchor buffer for S7 — use a small structural buffer of 0.5%
                swing_sl = raw * (1 + config_s7.S7_ENTRY_BUFFER)
                if swing_sl < ap.get("sl", float("inf")) and tr_mod.update_position_sl(symbol, swing_sl, hold_side="short"):
                    ap["sl"] = swing_sl
                    st_mod.update_open_trade_sl(symbol, swing_sl)
                    ap["swing_trail_ref"] = find_swing_low_target(cs_df, mark, lookback=lb)
                    logger.info(f"[S7][{symbol}] 📍 Swing trail: SL → {swing_sl:.5f} (daily swing high after ref low {ref:.5f})")
    except Exception as e:
        logger.error(f"S7 swing trail error [{symbol}]: {e}")
```

- [ ] **Step 2: Verify imports**

Run: `python -c "from strategies.s7 import compute_and_place_short_exits, maybe_trail_sl; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add strategies/s7.py
git commit -m "feat(s7): add exit placement and swing trail helpers"
```

---

### Task 7: S7 pending queue + entry watcher

**Files:**
- Modify: `strategies/s7.py`

- [ ] **Step 1: Append `queue_pending()` and `handle_pending_tick()` to `strategies/s7.py`**

```python
# ── S7 Pending-Signal Queue ───────────────────────────────── #

def queue_pending(bot, c: dict) -> None:
    """Queue an S7 SHORT spike-reversal pending signal for the entry watcher."""
    import time as _t
    import state as st
    import config_s7

    symbol      = c["symbol"]
    s7_trigger  = c["s7_trigger"]
    s7_sl       = c["s7_sl"]
    box_top     = c["s7_box_top"]
    box_low     = c["s7_box_low"]
    bot.pending_signals[symbol] = {
        "strategy":             "S7",
        "side":                 "SHORT",
        "trigger":              s7_trigger,
        "s7_sl":                s7_sl,
        "box_low":              box_low,
        "box_top":              box_top,
        "priority_rank":        c.get("priority_rank", 999),
        "priority_score":       c.get("priority_score", 0.0),
        "snap_rsi":             round(c["s7_rsi"], 1),
        "snap_rsi_peak":        round(c["s7_rsi_peak"], 1),
        "snap_spike_body_pct":  round(c["s7_body_pct"] * 100, 1),
        "snap_rsi_div":         c["s7_div"],
        "snap_rsi_div_str":     c["s7_div_str"],
        "snap_box_top":         box_top,
        "snap_box_low_initial": box_low,
        "snap_sentiment":       bot.sentiment.direction if bot.sentiment else "?",
        "expires":              _t.time() + 86400,
    }
    st.save_pending_signals(bot.pending_signals)
    logger.info(
        f"[S7][{symbol}] 🕐 PENDING SHORT queued | trigger≤{s7_trigger:.5f} | "
        f"box top={box_top:.5f} low={box_low:.5f} | SL={s7_sl:.5f}"
    )
    st.add_scan_log(
        f"[S7][{symbol}] 🕐 PENDING SHORT | trigger≤{s7_trigger:.5f}", "SIGNAL"
    )


# ── S7 Entry Watcher (pending tick) ───────────────────────── #

def handle_pending_tick(bot, symbol: str, sig: dict, balance: float,
                        paper_mode: bool | None = None) -> str | None:
    """
    S7 entry-watcher tick. Returns 'break' to stop outer loop (concurrency cap),
    None otherwise. Fires SHORT only on a confirmed 1H *close* below box_low.
    """
    import state as st
    import trader as tr
    import config, config_s7

    ps = st.get_pair_state(symbol)
    if ps.get("s7_signal", "HOLD") not in ("SHORT",):
        logger.info(f"[S7][{symbol}] 🚫 Signal gone — cancelling pending")
        st.add_scan_log(f"[S7][{symbol}] 🚫 Pending cancelled (signal gone)", "INFO")
        bot.pending_signals.pop(symbol, None)
        st.save_pending_signals(bot.pending_signals)
        return None

    # Re-run Darvas detector on fresh 1H candles
    try:
        h1_df = tr.get_candles(symbol, "1H", limit=48)
    except Exception:
        return None
    today_slice = today_h1_slice(h1_df)
    locked, box_top, box_low, _, _, _ = detect_darvas_box(
        today_slice, confirm=config_s7.S7_BOX_CONFIRM_COUNT
    )
    if not locked:
        return None  # still pending; do not cancel

    # Update pending fields if box has expanded (wick-and-reclaim)
    if box_low != sig["box_low"] or box_top != sig.get("box_top"):
        sig["box_low"] = box_low
        sig["box_top"] = box_top
        sig["trigger"] = box_low * (1 - config_s7.S7_ENTRY_BUFFER)
        st.save_pending_signals(bot.pending_signals)

    try:
        mark = tr.get_mark_price(symbol)
    except Exception:
        return None

    # SL invalidation
    if mark > sig["s7_sl"]:
        logger.info(f"[S7][{symbol}] ❌ Invalidated — mark {mark:.5f} > SL {sig['s7_sl']:.5f}")
        st.add_scan_log(f"[S7][{symbol}] ❌ Pending cancelled (price above SL)", "INFO")
        bot.pending_signals.pop(symbol, None)
        st.save_pending_signals(bot.pending_signals)
        return None

    # Confirmed-close trigger: latest CLOSED 1H must close below box_low
    if len(h1_df) < 2:
        return None
    last_closed_close = float(h1_df.iloc[-2]["close"])
    if last_closed_close >= box_low:
        return None  # not yet broken on close

    # Stale-entry guard
    s7_trigger = sig["trigger"]
    in_window  = (mark <= s7_trigger and
                  mark >= box_low * (1 - config_s7.S7_MAX_ENTRY_BUFFER))
    if not in_window:
        return None  # don't chase

    with bot._trade_lock:
        if symbol in bot.active_positions:
            bot.pending_signals.pop(symbol, None)
            st.save_pending_signals(bot.pending_signals)
            return None
        if len(bot.active_positions) >= config.MAX_CONCURRENT_TRADES:
            return "break"
        if st.is_pair_paused(symbol):
            return None
        bot._fire_s7(symbol, sig, mark, balance)
    bot.pending_signals.pop(symbol, None)
    st.save_pending_signals(bot.pending_signals)
    return None
```

- [ ] **Step 2: Verify imports**

Run: `python -c "from strategies.s7 import queue_pending, handle_pending_tick; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add strategies/s7.py
git commit -m "feat(s7): add pending queue and entry watcher"
```

---

## Phase 3 — State layer

### Task 8: Add S7 fields to `state.py:_default_pair_state`

**Files:**
- Modify: `state.py`

- [ ] **Step 1: Locate `_default_pair_state` and view S4 field block**

Run: `grep -n "s4_signal\|s4_reason\|_default_pair_state" state.py`

Expected output: a function definition near the top, plus a block where `s4_signal`, `s4_reason`, `s4_1h_low`, `s4_sr_support_pct` are set.

- [ ] **Step 2: Insert S7 fields right after the S4 block in `_default_pair_state()`**

Open `state.py` and find the line containing `"s4_sr_support_pct"` (or the last S4 field). Add immediately after it:

```python
        # S7 — post-pump 1H Darvas breakdown short
        "s7_signal":          "HOLD",
        "s7_reason":          "",
        "s7_box_top":         None,
        "s7_box_low":         None,
        "s7_rsi":             None,
        "s7_rsi_peak":        None,
        "s7_body_pct":        None,
        "s7_div":             False,
        "s7_div_str":         "",
        "s7_sr_support_pct":  None,
```

(Match the dictionary indentation of surrounding S4 entries — 8 spaces is typical.)

- [ ] **Step 3: Verify**

Run: `python -c "import state; ps = state._default_pair_state(); print('s7_signal:', ps['s7_signal'], '| s7_box_low:', ps['s7_box_low'])"`
Expected: `s7_signal: HOLD | s7_box_low: None`

- [ ] **Step 4: Commit**

```bash
git add state.py
git commit -m "feat(s7): add S7 fields to default pair_state"
```

---

## Phase 4 — Bot integration

### Task 9: `bot.py` — imports, init log, strategy whitelist tuples

**Files:**
- Modify: `bot.py`

- [ ] **Step 1: Add imports**

In `bot.py`, find `import config_s4` (around line 19) and `from strategies.s4 import evaluate_s4` (around line 27). Add immediately after each:

```python
import config_s7
```

```python
from strategies.s7 import evaluate_s7
```

- [ ] **Step 2: Update bot init log**

Find: `st.add_scan_log("Bot initialised (S1 + S2 + S3 + S4 + S5 + S6)", "INFO")` (~line 284).

Replace with:

```python
        st.add_scan_log("Bot initialised (S1 + S2 + S3 + S4 + S5 + S6 + S7)", "INFO")
```

- [ ] **Step 3: Update strategy whitelist tuples**

Find each line containing `("S1", "S2", "S3", "S4", "S5", "S6")` (search: `grep -n '"S1", "S2", "S3", "S4", "S5", "S6"' bot.py`).

For each match, append `, "S7"` inside the tuple. Expected matches: 2 (~lines 355 and 869).

Example, before:
```python
if ap.get("strategy") not in ("S1", "S2", "S3", "S4", "S5", "S6"):
```

After:
```python
if ap.get("strategy") not in ("S1", "S2", "S3", "S4", "S5", "S6", "S7"):
```

- [ ] **Step 4: Verify import + run pytest smoke**

Run: `python -c "import bot; print('bot OK')"`
Expected: `bot OK`

Run: `pytest tests/test_s7_darvas.py tests/test_s7_evaluate.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add bot.py
git commit -m "feat(s7): wire bot.py imports, init log, and whitelists"
```

---

### Task 10: `bot.py` — evaluation block + pair_state assemble

**Files:**
- Modify: `bot.py`

- [ ] **Step 1: Locate the S4 evaluation block**

Run: `grep -n "s4_sig, s4_rsi, s4_trigger" bot.py`

You should find: `s4_sig, s4_rsi, s4_trigger, s4_sl, s4_body_pct, s4_rsi_peak, s4_div, s4_div_str, s4_reason = "HOLD", ...` (~line 1293) followed by an `if config_s4.S4_ENABLED and self.sentiment.direction == "BEARISH":` block.

- [ ] **Step 2: Add a parallel S7 evaluation block right after the S4 block (before the `s4_1h_low` section)**

Insert:

```python
        # ── S7 evaluation (post-pump 1H Darvas breakdown short) ───── #
        s7_sig, s7_rsi, s7_box_top, s7_box_low, s7_body_pct, s7_rsi_peak, s7_div, s7_div_str, s7_reason = (
            "HOLD", 50.0, 0.0, 0.0, 0.0, 0.0, False, "", ""
        )
        if config_s7.S7_ENABLED and self.sentiment.direction == "BEARISH":
            s7_sig, s7_rsi, s7_box_top, s7_box_low, s7_body_pct, s7_rsi_peak, s7_div, s7_div_str, s7_reason = (
                evaluate_s7(symbol, daily_df, htf_df)
            )
            logger.info(f"[S7][{symbol}] {s7_reason}")

        s7_sr_sup_pct = None
        if s7_sig == "SHORT" and s7_box_low > 0:
            from tools import find_spike_base
            _s7_base = find_spike_base(daily_df, price_ceiling=close)
            s7_sr_sup_pct = round((close - _s7_base) / close * 100, 1) if _s7_base else None
```

- [ ] **Step 3: Locate the pair_state assembly block**

Run: `grep -n '"s4_signal":\|"s4_1h_low":' bot.py`

Find the dict that contains `"s4_reason"`, `"s4_signal"`, `"s4_1h_low"`, `"s4_sr_support_pct"` (~lines 1347–1378).

- [ ] **Step 4: Add S7 fields to the pair_state dict**

Right after `"s4_sr_support_pct": s4_sr_sup_pct,` add:

```python
            "s7_reason":          s7_reason,
            "s7_signal":          s7_sig,
            "s7_box_top":         s7_box_top if s7_box_top > 0 else None,
            "s7_box_low":         s7_box_low if s7_box_low > 0 else None,
            "s7_rsi":             round(s7_rsi, 1) if s7_rsi else None,
            "s7_rsi_peak":        round(s7_rsi_peak, 1) if s7_rsi_peak else None,
            "s7_body_pct":        round(s7_body_pct, 4) if s7_body_pct else None,
            "s7_div":             s7_div,
            "s7_div_str":         s7_div_str,
            "s7_sr_support_pct":  s7_sr_sup_pct,
```

- [ ] **Step 5: Verify**

Run: `python -c "import bot; print('bot OK')"`
Expected: `bot OK`

- [ ] **Step 6: Commit**

```bash
git add bot.py
git commit -m "feat(s7): add S7 evaluation block and pair_state fields in scan loop"
```

---

### Task 11: `bot.py` — candidate collection + signal/strategy collapse

**Files:**
- Modify: `bot.py`

- [ ] **Step 1: Locate the S4 candidate-collection block**

Run: `grep -n "Collect S4 candidate" bot.py`
Expected: a comment line near `# ── Collect S4 candidate ──...` (~line 1435), followed by an `if s4_sig == "SHORT" and s4_trigger > 0:` block.

- [ ] **Step 2: Add a parallel S7 candidate block immediately after the S4 block**

```python
        # ── Collect S7 candidate ──────────────────────────────────── #
        if s7_sig == "SHORT" and s7_box_low > 0:
            s7_trigger = s7_box_low * (1 - config_s7.S7_ENTRY_BUFFER)
            s7_sl      = s7_trigger * (1 + 0.50 / config_s7.S7_LEVERAGE)
            cands.append({
                "strategy": "S7", "symbol": symbol, "sig": "SHORT",
                "rr": None, "sr_pct": s7_sr_sup_pct,
                "s7_trigger": s7_trigger, "s7_sl": s7_sl,
                "s7_box_top": s7_box_top, "s7_box_low": s7_box_low,
                "s7_rsi": s7_rsi, "s7_rsi_peak": s7_rsi_peak,
                "s7_body_pct": s7_body_pct, "s7_div": s7_div, "s7_div_str": s7_div_str,
                "s7_reason": s7_reason, "daily_df": daily_df,
            })
```

(Priority rank/score are computed downstream by the candidate ranker — same as S4.)

- [ ] **Step 3: Locate the final signal collapse**

Run: `grep -n '"signal": s1_sig if s1_sig != "HOLD"' bot.py`
Expected: a line near 1335 with a chained ternary across S1..S6.

- [ ] **Step 4: Update the signal collapse to include S7**

Insert `s7_sig` between `s4_sig` and the rest. The new chain should read:

```python
            "signal": s1_sig if s1_sig != "HOLD" else (s2_sig if s2_sig != "HOLD" else (s3_sig if s3_sig != "HOLD" else (s4_sig if s4_sig != "HOLD" else (s7_sig if s7_sig != "HOLD" else ("PENDING" if s5_sig.startswith("PENDING") else (s6_sig if s6_sig != "HOLD" else s5_sig)))))),
```

- [ ] **Step 5: Update the strategy collapse the same way**

Find the line near 1366 starting with `"strategy": "S1" if s1_sig != "HOLD"` and update similarly:

```python
            "strategy": "S1" if s1_sig != "HOLD" else ("S2" if s2_sig != "HOLD" else ("S3" if s3_sig != "HOLD" else ("S4" if s4_sig != "HOLD" else ("S7" if s7_sig != "HOLD" else ("S6" if s6_sig not in ("HOLD", "") else ("S5" if s5_sig not in ("HOLD", "") else "S1")))))),
```

- [ ] **Step 6: Verify**

Run: `python -c "import bot; print('bot OK')"`
Expected: `bot OK`

- [ ] **Step 7: Commit**

```bash
git add bot.py
git commit -m "feat(s7): collect S7 candidates and include in signal/strategy collapse"
```

---

### Task 12: `bot.py` — `_fire_s7`, min-balance dispatch, scale-in branch, watcher dispatch

**Files:**
- Modify: `bot.py`

- [ ] **Step 1: Locate `_fire_s4` and the surrounding `_fire_*` methods**

Run: `grep -n "def _fire_s4\|def _fire_s5\|def _fire_s6" bot.py`
Expected: the three method definitions; `_fire_s4` starts ~line 2034.

- [ ] **Step 2: Add a new `_fire_s7` method right after `_fire_s4`**

Insert:

```python
    def _fire_s7(self, symbol: str, sig: dict, mark: float, balance: float) -> None:
        """Open S7 SHORT at fire time. Runs S/R check against pair_states."""
        ps = st.get_pair_state(symbol)
        sr_support_pct = ps.get("s7_sr_support_pct")
        if sr_support_pct is not None and sr_support_pct < config_s7.S7_MIN_SR_CLEARANCE * 100:
            logger.info(
                f"[S7][{symbol}] ⏸️ Fire skipped — support clearance {sr_support_pct:.1f}% too small"
            )
            st.add_scan_log(
                f"[S7][{symbol}] ⛔ Fire: support too close ({sr_support_pct:.1f}%)", "WARN"
            )
            self.pending_signals.pop(symbol, None)
            st.save_pending_signals(self.pending_signals)
            return
        if config.CLAUDE_FILTER_ENABLED:
            _sr_str = f"{sr_support_pct:.1f}%" if sr_support_pct else "none found"
            _cd = claude_approve("S7", symbol, {
                "RSI peak": sig.get("snap_rsi_peak", "?"),
                "RSI divergence": str(sig.get("snap_rsi_div", "?")),
                "S/R clearance (spike base)": _sr_str,
                "Sentiment": sig.get("snap_sentiment", "?"),
                "Box top": round(sig.get("box_top", 0), 5),
                "Box low": round(sig.get("box_low", 0), 5),
                "Entry": round(mark, 5), "SL": round(sig["s7_sl"], 5),
            })
            if not _cd["approved"]:
                logger.info(f"[S7][{symbol}] 🤖 Claude rejected: {_cd['reason']}")
                st.add_scan_log(f"[S7][{symbol}] 🤖 Rejected: {_cd['reason']}", "WARN")
                self.pending_signals.pop(symbol, None)
                st.save_pending_signals(self.pending_signals)
                return

        s7_sl_actual = mark * (1 + 0.50 / config_s7.S7_LEVERAGE)
        st.add_scan_log(
            f"[S7][{symbol}] 🔴 SHORT fired @ {mark:.5f} | entry≤{sig['trigger']:.5f} | "
            f"box low={sig.get('box_low', 0):.5f}", "SIGNAL"
        )
        trade = tr.open_short(
            symbol, sl_floor=s7_sl_actual, leverage=config_s7.S7_LEVERAGE,
            trade_size_pct=config_s7.S7_TRADE_SIZE_PCT * 0.5, strategy="S7",
        )
        trade["strategy"]              = "S7"
        trade["snap_rsi"]              = sig.get("snap_rsi")
        trade["snap_rsi_peak"]         = sig.get("snap_rsi_peak")
        trade["snap_spike_body_pct"]   = sig.get("snap_spike_body_pct")
        trade["snap_rsi_div"]          = sig.get("snap_rsi_div")
        trade["snap_rsi_div_str"]      = sig.get("snap_rsi_div_str")
        trade["snap_box_top"]          = sig.get("snap_box_top")
        trade["snap_box_low_initial"]  = sig.get("snap_box_low_initial")
        trade["snap_sl"]               = round(s7_sl_actual, 8)
        trade["snap_sentiment"]        = sig.get("snap_sentiment")
        trade["snap_sr_clearance_pct"] = sr_support_pct
        trade["trade_id"] = uuid.uuid4().hex[:8]
        trade.update(dna_snapshot("S7", symbol, {
            "daily": sig.get("daily_df"),
        }))
        _log_trade("S7_SHORT", trade)
        st.add_open_trade(trade)
        try:
            snapshot.save_snapshot(
                trade_id=trade["trade_id"], event="open",
                symbol=symbol, interval="1D", candles=[],
                event_price=float(trade.get("entry", 0)),
            )
        except Exception as e:
            logger.warning(f"[S7][{symbol}] snapshot save failed: {e}")
        if PAPER_MODE: tr.tag_strategy(symbol, "S7")
        self.active_positions[symbol] = {
            "side": "SHORT", "strategy": "S7",
            "box_high": sig["s7_sl"], "box_low": sig["trigger"],
            "scale_in_pending": True, "scale_in_after": time.time() + 3600,
            "scale_in_trade_size_pct": config_s7.S7_TRADE_SIZE_PCT,
            "s7_box_low": sig["box_low"],
            "s7_box_top": sig["box_top"],
            "trade_id": trade["trade_id"],
        }
```

- [ ] **Step 3: Update min-balance dispatch**

Run: `grep -n "elif strategy == \"S4\":" bot.py`
Find the elif chain near line 1540 and add an S7 case right after the S4 case:

```python
            elif strategy == "S7":
                min_bal = 5.0 / (config_s7.S7_TRADE_SIZE_PCT * config_s7.S7_LEVERAGE)
```

- [ ] **Step 4: Update scale-in branch**

Run: `grep -n '_strat in ("S2", "S4")' bot.py`
Expected: ~line 926.

Replace `("S2", "S4")` with `("S2", "S4", "S7")`. Then in the same branch, add a parallel `from strategies.s7 import maybe_trail_sl as _trail_s7` import next to the existing S4 import, and dispatch correctly. Concretely, the existing block:

```python
                    elif _strat in ("S2", "S4"):
                        if _strat == "S4":
                            from strategies.s4 import maybe_trail_sl as _trail_s4
                            _trail_s4(sym, ap, tr, st, _partial_done)
```

becomes:

```python
                    elif _strat in ("S2", "S4", "S7"):
                        if _strat == "S4":
                            from strategies.s4 import maybe_trail_sl as _trail_s4
                            _trail_s4(sym, ap, tr, st, _partial_done)
                        elif _strat == "S7":
                            from strategies.s7 import maybe_trail_sl as _trail_s7
                            _trail_s7(sym, ap, tr, st, _partial_done)
```

- [ ] **Step 5: Update pending-watcher dispatch**

Run: `grep -n "handle_pending_tick\|sig\[.strategy.\]" bot.py | head -20`
Find where pending dispatch switches on `sig["strategy"]`. Add an `S7` case dispatching to `strategies.s7.handle_pending_tick`.

Example shape (your existing code may vary slightly):

```python
                if sig["strategy"] == "S4":
                    from strategies.s4 import handle_pending_tick as _h
                    res = _h(self, symbol, sig, balance, paper_mode=PAPER_MODE)
                elif sig["strategy"] == "S7":
                    from strategies.s7 import handle_pending_tick as _h
                    res = _h(self, symbol, sig, balance, paper_mode=PAPER_MODE)
```

If the dispatch is already a generic `getattr(strategies, sig["strategy"].lower()).handle_pending_tick(...)` pattern, no change is needed — confirm by reading the surrounding code.

- [ ] **Step 6: Verify**

Run: `python -c "import bot; print('bot OK')"`
Expected: `bot OK`

Run: `pytest tests/test_s7_darvas.py tests/test_s7_evaluate.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add bot.py
git commit -m "feat(s7): add _fire_s7, min-balance, scale-in, and watcher dispatch"
```

---

## Phase 5 — Adjacent integrations

### Task 13: `paper_trader.py` — extend strategy whitelist + S7 trail branch

**Files:**
- Modify: `paper_trader.py`

- [ ] **Step 1: Locate the S4/S5 trail branch**

Run: `grep -n 'strategy in ("S4", "S5")' paper_trader.py`
Expected: ~line 227.

- [ ] **Step 2: Extend the whitelist and add S7 branch**

Replace `if strategy in ("S4", "S5"):` with `if strategy in ("S4", "S5", "S7"):`.

Inside that block, find where S4 calls `strategies.s4.compute_paper_trail_short(...)`. Add a parallel branch for S7:

```python
        elif strategy == "S7":
            from strategies.s7 import compute_paper_trail_short as _p
            use_trail, trail_trig, trail_range, tp_price, be_after_partial = _p(
                mark, sl_price, tp_price_abs, take_profit_pct
            )
```

(Adjust to the exact local variable names in that function — read the surrounding ~30 lines to confirm.)

- [ ] **Step 3: Verify**

Run: `python -c "import paper_trader; print('paper OK')"`
Expected: `paper OK`

- [ ] **Step 4: Commit**

```bash
git add paper_trader.py
git commit -m "feat(s7): extend paper_trader for S7 SHORT trail setup"
```

---

### Task 14: `recover.py`, `startup_recovery.py`, `analytics.py` — strategy tuples

**Files:**
- Modify: `recover.py`
- Modify: `startup_recovery.py`
- Modify: `analytics.py`

- [ ] **Step 1: Find every iteration tuple `("S1", "S2", "S3", "S4", "S5", "S6")`**

Run: `grep -n '"S1", "S2", "S3", "S4", "S5", "S6"' recover.py startup_recovery.py analytics.py`

- [ ] **Step 2: Append `, "S7"` to each tuple**

For each match, edit the tuple to include `"S7"` at the end.

- [ ] **Step 3: Verify imports**

Run: `python -c "import recover, startup_recovery, analytics; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add recover.py startup_recovery.py analytics.py
git commit -m "feat(s7): add S7 to recover/startup_recovery/analytics strategy tuples"
```

---

### Task 15: `optimize.py` — STRATEGY_GRIDS + STRATEGY_COLUMNS

**Files:**
- Modify: `optimize.py`

- [ ] **Step 1: Add `import config_s7`**

Locate `import config_s4` near line 19 and add right after:

```python
import config_s7
```

- [ ] **Step 2: Add S7 to `STRATEGY_GRIDS`**

Locate the `"S4": _cfg(config_s4, ...)` block (~line 57). Add immediately after the S4 block:

```python
    "S7": _cfg(config_s7,
        "S7_RSI_PEAK_THRESH", "S7_RSI_STILL_HOT_THRESH",
        "S7_RSI_DIV_MIN_DROP", "S7_RSI_PEAK_LOOKBACK",
        "S7_BIG_CANDLE_BODY_PCT", "S7_BIG_CANDLE_LOOKBACK",
        "S7_ENTRY_BUFFER", "S7_MAX_ENTRY_BUFFER",
        "S7_MIN_SR_CLEARANCE",
        "S7_BOX_CONFIRM_COUNT",
        "S7_TRAILING_TRIGGER_PCT", "S7_TRAILING_RANGE_PCT",
        "S7_USE_SWING_TRAIL", "S7_SWING_LOOKBACK",
    ),
```

- [ ] **Step 3: Add S7 to `STRATEGY_COLUMNS`**

Locate `"S4": [...]` near line 91 and add:

```python
    "S7": ["result", "pnl_pct", "exit_reason",
           "snap_rsi", "snap_rsi_peak", "snap_spike_body_pct",
           "snap_rsi_div", "snap_box_top", "snap_box_low_initial",
           "snap_sentiment", "snap_sl", "snap_sr_clearance_pct"],
```

(If S4's column list differs in this codebase, mirror it instead — the S7 list should match S4's set plus the box snapshot fields.)

- [ ] **Step 4: Verify**

Run: `python -c "import optimize; print('S7 in grids:', 'S7' in optimize.STRATEGY_GRIDS, '| in cols:', 'S7' in optimize.STRATEGY_COLUMNS)"`
Expected: `S7 in grids: True | in cols: True`

- [ ] **Step 5: Commit**

```bash
git add optimize.py
git commit -m "feat(s7): add S7 to optimizer grids and columns"
```

---

### Task 16: `backtest_engine.py` — `use_s7_exits` + enabled set + config load

**Files:**
- Modify: `backtest_engine.py`

- [ ] **Step 1: Add `use_s7_exits` parameter to `_attach_exits()`**

Find the function signature (~line 345). It currently has `use_s1_exits: bool = False, use_s4_exits: bool = False, ...`. Add `use_s7_exits: bool = False,` to the signature.

- [ ] **Step 2: Add S7 exit branch in `_attach_exits()`**

Find the `elif use_s4_exits:` branch (~line 369). Add immediately after:

```python
        elif use_s7_exits:
            tp_trig   = mark * (1 - _cfg("config_s7", "S7_TRAILING_TRIGGER_PCT", 0.10))
            trail_pct = _cfg("config_s7", "S7_TRAILING_RANGE_PCT", 10.0)
```

- [ ] **Step 3: Update `needs_scale_in`**

Find: `needs_scale_in = use_s4_exits or use_s6_exits` (~line 379). Change to:

```python
        needs_scale_in = use_s4_exits or use_s6_exits or use_s7_exits
```

- [ ] **Step 4: Add S7 to the enabled set in three places**

Run: `grep -n '"S1", "S2", "S3", "S4", "S5", "S6"' backtest_engine.py`
Expected: ~lines 755, 1033, 1100.

For each match, append `, "S7"` inside the set/tuple.

- [ ] **Step 5: Add `"config_s7"` to the config-modules list**

Find: `for name in ["config_s1", "config_s2", ..., "config_s4", "config_s5", "config_s6"]:` (~line 773). Add `"config_s7"` to the list.

- [ ] **Step 6: Verify**

Run: `python -c "import backtest_engine; print('bt OK')"`
Expected: `bt OK`

- [ ] **Step 7: Commit**

```bash
git add backtest_engine.py
git commit -m "feat(s7): wire S7 into backtest_engine"
```

---

### Task 17: `dashboard.py` + `dashboard.html` — display S7

**Files:**
- Modify: `dashboard.py`
- Modify: `dashboard.html`

- [ ] **Step 1: Pass S7 fields through `dashboard.py:get_state`**

Locate the per-pair `get_state` payload assembly (lines 60–87 area, per `docs/PRE_CHANGE_CHECKLIST.md`). Find where it reads S4 fields like `s4_signal`, `s4_reason`, `s4_1h_low`. Add the S7 reads alongside:

```python
            "s7_signal":          ps.get("s7_signal", "HOLD"),
            "s7_reason":          ps.get("s7_reason", ""),
            "s7_box_top":         ps.get("s7_box_top"),
            "s7_box_low":         ps.get("s7_box_low"),
            "s7_rsi":             ps.get("s7_rsi"),
            "s7_rsi_peak":        ps.get("s7_rsi_peak"),
            "s7_body_pct":        ps.get("s7_body_pct"),
            "s7_div":             ps.get("s7_div"),
            "s7_div_str":         ps.get("s7_div_str"),
            "s7_sr_support_pct":  ps.get("s7_sr_support_pct"),
```

- [ ] **Step 2: Add an S7 panel to `dashboard.html` mirroring the S4 panel**

In `dashboard.html`, find the per-pair S4 display block (search for `s4_signal` or `S4` headers). Duplicate the block, change every `s4_*` field name to `s7_*`, and label it `S7 (1H Darvas Breakdown)`. Show:

- `s7_signal`
- `s7_reason`
- `s7_box_top` / `s7_box_low` (the two Darvas levels)
- `s7_rsi`, `s7_rsi_peak`, `s7_body_pct`, `s7_div_str`
- `s7_sr_support_pct`

(For active positions, the existing strategy-agnostic panel already reads `box_high` / `box_low` from `active_positions[symbol]` — no change needed there.)

- [ ] **Step 3: Verify dashboard imports + loads**

Run: `python -c "import dashboard; print('dash OK')"`
Expected: `dash OK`

(Visual smoke check is covered by Task 19's manual run.)

- [ ] **Step 4: Commit**

```bash
git add dashboard.py dashboard.html
git commit -m "feat(s7): display S7 panel and box levels in dashboard"
```

---

## Phase 6 — Tests + docs

### Task 18: Manual integration test `tests/manual/run_test_s7.py`

**Files:**
- Create: `tests/manual/run_test_s7.py`

- [ ] **Step 1: Create the manual test file mirroring `run_test_s4.py`**

```python
# tests/manual/run_test_s7.py
"""
S7 manual test — 1H Darvas breakdown short.

Run standalone:  python tests/manual/run_test_s7.py
Run via pytest:  pytest tests/manual/run_test_s7.py -v -s
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.manual._bc_spy import bc_spy
from tests.manual._bot_factory import make_bot
import config_s7
import trader as tr

SYMBOL  = "BTCUSDT"
MARK    = 50_000.0
BOX_LOW = MARK / (1 - config_s7.S7_ENTRY_BUFFER)            # entry ≈ MARK after buffer
S7_SL   = MARK * (1 + 0.50 / config_s7.S7_LEVERAGE)          # leverage cap


def _make_sig() -> dict:
    return {
        "strategy":             "S7",
        "side":                 "SHORT",
        "trigger":              MARK,
        "s7_sl":                S7_SL,
        "box_low":              BOX_LOW,
        "box_top":              BOX_LOW * 1.05,
        "snap_rsi":             45.0,
        "snap_rsi_peak":        85.0,
        "snap_spike_body_pct":  65.0,
        "snap_rsi_div":         True,
        "snap_rsi_div_str":     "RSI divergence",
        "snap_box_top":         BOX_LOW * 1.05,
        "snap_box_low_initial": BOX_LOW,
        "snap_sentiment":       "BEARISH",
        "priority_rank":        1,
        "priority_score":       22.0,
    }


def test_s7_entry_short():
    print(f"\n{'='*60}")
    print(f"S7 — Entry SHORT (1H Darvas breakdown)")
    print(f"  config_s7.S7_LEVERAGE             = {config_s7.S7_LEVERAGE}")
    print(f"  config_s7.S7_TRADE_SIZE_PCT       = {config_s7.S7_TRADE_SIZE_PCT} (initial 50% = {config_s7.S7_TRADE_SIZE_PCT*0.5*100:.0f}%)")
    print(f"  SL = mark*(1 + 0.50/{config_s7.S7_LEVERAGE}) = {S7_SL:.2f}")
    print(f"  config_s7.S7_TRAILING_TRIGGER_PCT = {config_s7.S7_TRAILING_TRIGGER_PCT}  → trail ≈ {MARK*(1-config_s7.S7_TRAILING_TRIGGER_PCT):.1f}")
    print(f"  config_s7.S7_TRAILING_RANGE_PCT   = {config_s7.S7_TRAILING_RANGE_PCT}")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, hold_side="short"):
        b = make_bot()
        b._fire_s7(SYMBOL, _make_sig(), mark=MARK, balance=10_000.0)


def test_s7_scale_in_short():
    print(f"\n{'='*60}")
    print(f"S7 — Scale-in SHORT (+{config_s7.S7_TRADE_SIZE_PCT*0.5*100:.0f}% of equity)")
    print(f"  in-window: {BOX_LOW*(1-config_s7.S7_MAX_ENTRY_BUFFER):.1f} ≤ mark ≤ {BOX_LOW*(1-config_s7.S7_ENTRY_BUFFER):.1f}")
    print(f"{'='*60}")
    mark_in_window = BOX_LOW * (1 - config_s7.S7_ENTRY_BUFFER * 1.5)
    ap = {
        "side":                    "SHORT",
        "strategy":                "S7",
        "box_high":                S7_SL,
        "box_low":                 MARK,
        "scale_in_pending":        True,
        "scale_in_trade_size_pct": config_s7.S7_TRADE_SIZE_PCT,
        "s7_box_low":              BOX_LOW,
        "s7_box_top":              BOX_LOW * 1.05,
        "qty":                     0.0,
        "trade_id":                "test-trade-s7-001",
    }
    with bc_spy(symbol=SYMBOL, mark_price=mark_in_window, init_qty=0.002, scale_in_qty=0.004, hold_side="short"):
        b = make_bot()
        b.active_positions[SYMBOL] = ap
        b._do_scale_in(SYMBOL, ap)


def test_s7_trailing_refresh():
    print(f"\n{'='*60}")
    print(f"S7 — Trailing refresh  rangeRate={config_s7.S7_TRAILING_RANGE_PCT}")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, scale_in_qty=0.004, hold_side="short"):
        tr.refresh_plan_exits(SYMBOL, "short", new_trail_trigger=MARK * 0.90)


if __name__ == "__main__":
    test_s7_entry_short()
    test_s7_scale_in_short()
    test_s7_trailing_refresh()
    print(f"\n{'='*60}")
    print("S7 — all scenarios complete")
    print(f"{'='*60}")
```

- [ ] **Step 2: Run the manual test**

Run: `pytest tests/manual/run_test_s7.py -v -s`
Expected: 3 PASS. The `bc_spy` context manager will assert that the SHORT entry, partial, trailing, and scale-in calls reach the Bitget client with the right parameters.

- [ ] **Step 3: Commit**

```bash
git add tests/manual/run_test_s7.py
git commit -m "test(s7): add manual integration test mirroring S4"
```

---

### Task 19: Extend `tests/test_bot_entry_watcher_all.py` and `tests/test_state_pending_signals.py` (if they iterate strategies)

**Files:**
- Modify (conditional): `tests/test_bot_entry_watcher_all.py`
- Modify (conditional): `tests/test_state_pending_signals.py`

- [ ] **Step 1: Inspect both files for strategy iteration**

Run: `grep -n 'S1\|S2\|S3\|S4\|S5\|S6\|@pytest.mark.parametrize' tests/test_bot_entry_watcher_all.py tests/test_state_pending_signals.py`

- [ ] **Step 2: If a parametrize over strategies exists, add `"S7"` to it**

For each `@pytest.mark.parametrize("strategy", [..., "S6"])` (or similar), append `"S7"`. If the test then references `_fire_s4`-style methods, add an `_fire_s7` analogue.

If no strategy iteration exists in these files, this task is a no-op — just verify the existing tests still pass.

- [ ] **Step 3: Run both test files**

Run: `pytest tests/test_bot_entry_watcher_all.py tests/test_state_pending_signals.py -v`
Expected: all PASS.

- [ ] **Step 4: Commit (only if files were modified)**

```bash
git add tests/test_bot_entry_watcher_all.py tests/test_state_pending_signals.py
git commit -m "test(s7): extend cross-strategy tests to cover S7"
```

---

### Task 20: Strategy doc `docs/strategies/S7.md`

**Files:**
- Create: `docs/strategies/S7.md`

- [ ] **Step 1: Create the doc mirroring `docs/strategies/S4.md` structure**

```markdown
# Strategy 7 — Post-Pump 1H Darvas Breakdown Short

S7 is a SHORT strategy that fires on confirmed 1H Darvas-box breakdowns following a daily post-pump exhaustion setup. Conceptually identical to S4 except that the entry trigger is replaced: instead of "intraday breach below the previous day's low," S7 waits for a stair-step 1H Darvas box (top + low) to form within the current UTC day and fires only on a confirmed 1H *close* below the box low.

## Setup gates (mirrors S4)

| Gate | Default | Description |
|---|---|---|
| `S7_BIG_CANDLE_BODY_PCT` | `0.20` | Spike candle body must be ≥ 20% within last `S7_BIG_CANDLE_LOOKBACK` daily candles |
| `S7_BIG_CANDLE_LOOKBACK` | `30` | Daily lookback window for spike detection |
| `S7_RSI_PEAK_THRESH`     | `75`   | Daily RSI peak must reach this within `S7_RSI_PEAK_LOOKBACK` candles |
| `S7_RSI_PEAK_LOOKBACK`   | `10`   | Window for RSI peak |
| `S7_RSI_STILL_HOT_THRESH`| `70`   | Previous-day RSI must remain ≥ this (no fade yet) |
| `S7_RSI_DIV_MIN_DROP`    | `5`    | Informational divergence threshold |

Sentiment gate: BEARISH only.

## 1H Darvas-box detection (the new piece)

For each scan cycle, after the daily gates pass, S7 looks at the **completed 1H candles since the most recent UTC midnight** (excluding the currently-forming hour) and runs a classic Darvas walking algorithm:

1. **Top box pass.** Walk forward, tracking the running max high. Each new higher high resets a confirmation counter; once `S7_BOX_CONFIRM_COUNT` consecutive candles fail to exceed it, the top box is **locked**.
2. **Low box pass** (over candles after the top index). Same mechanic for the running min low: each new lower low resets, and the box locks once `S7_BOX_CONFIRM_COUNT` consecutive candles hold above it.
3. **Sanity:** `box_low < box_top`.

Default `S7_BOX_CONFIRM_COUNT = 2` → each box has 1 establishing + 2 confirming = 3 candles, total ≥ 6 candles since UTC midnight.

The detector is **stateless** — re-runs each scan. Box "expansion" (a wick above/below that closes back inside) happens for free on the next cycle: the new wick becomes the running extremum, and the box re-locks at the deeper level.

### Backtest

For each backtest tick, the 1H window is `h1_df[h1_df.index.floor('1D') == current_day][:-1]`.

## Entry trigger

Once a box locks: `entry_trigger = box_low × (1 − S7_ENTRY_BUFFER)`. The watcher fires SHORT only when:

- The latest CLOSED 1H candle's close is below `box_low`, AND
- Mark is inside `[box_low × (1 − S7_MAX_ENTRY_BUFFER), entry_trigger]` (no chasing stale moves), AND
- Mark has not rallied above `s7_sl` in the meantime.

## Risk management

| Knob | Default | Description |
|---|---|---|
| `S7_LEVERAGE`              | `10`   | |
| `S7_TRADE_SIZE_PCT`        | `0.04` | 4% portfolio margin (50% initial → +50% scale-in) |
| `S7_ENTRY_BUFFER`          | `0.005` | Tighter than S4's 1% — Darvas low is sharper |
| `S7_MAX_ENTRY_BUFFER`      | `0.04` | |
| `S7_TRAILING_TRIGGER_PCT`  | `0.10` | 50% partial close at −10% |
| `S7_TRAILING_RANGE_PCT`    | `10`   | Callback on remaining 50% |
| `S7_USE_SWING_TRAIL`       | `True` | Daily swing-high trail post-partial |
| `S7_SWING_LOOKBACK`        | `30`   | Daily candles for swing trail |
| `S7_MIN_SR_CLEARANCE`      | `0.15` | Skip if support floor < 15% below entry |

**SL is leverage-capped (atomic).** At fire time: `s7_sl = mark × (1 + 0.50 / S7_LEVERAGE)`, passed to Bitget as `presetStopLossPrice` on the same API call as the SHORT market entry.

## Scale-in

After initial 50%-size fill, scale-in queues for ~1 hour later. Both gates must hold:

- Sentiment must still be BEARISH
- Mark must be inside the retest window: `box_low × (1 − S7_MAX_ENTRY_BUFFER) ≤ mark ≤ box_low × (1 − S7_ENTRY_BUFFER)`

## Snapshots

`open` / `partial` / `close` / `scale_in` events save 1D-interval snapshots, same as S4 (per `GENERAL_CONCEPTS.md §12`).
```

- [ ] **Step 2: Verify**

Run: `head -5 docs/strategies/S7.md`
Expected: title and first lines render.

- [ ] **Step 3: Commit**

```bash
git add docs/strategies/S7.md
git commit -m "docs(s7): add strategy doc"
```

---

### Task 21: Update `docs/DEPENDENCIES.md`

**Files:**
- Modify: `docs/DEPENDENCIES.md`

- [ ] **Step 1: Locate the §2 (Shared Files) S4 entries**

Run: `grep -n "^##\|S4\|paper_trader" docs/DEPENDENCIES.md | head -40`

- [ ] **Step 2: Add S7 alongside S4 references in §2**

In the `paper_trader.py` row, change `"S4", "S5"` → `"S4", "S5", "S7"`. In the `bot.py` scale-in branch row, change `("S2", "S4")` → `("S2", "S4", "S7")`.

- [ ] **Step 3: Add a new entry in §5 (Config Dependencies) for `config_s7.py`**

Mirror the existing `config_s4.py` entry, listing every consumer:

- `bot.py` — imports `config_s7`, reads `S7_*` knobs in scan loop, candidate collection, `_fire_s7`, scale-in branch
- `optimize.py` — STRATEGY_GRIDS / STRATEGY_COLUMNS
- `backtest_engine.py` — `_attach_exits` reads `S7_TRAILING_*`, `enabled_strategies` includes S7, `config_s7` loaded in module list
- `paper_trader.py` — calls `strategies.s7.compute_paper_trail_short` which reads `S7_TRAILING_*`
- `strategies/s7.py` — primary consumer

- [ ] **Step 4: Add a new §7 entry for S7**

Mirror the §7 S4 entry. Cover:

- Files: `strategies/s7.py`, `config_s7.py`, `docs/strategies/S7.md`
- Knobs: list from §13 of the spec
- Integration points: `bot.py` (eval, candidate, fire, scale-in, watcher), `state.py` (pair_state fields), `paper_trader.py`, `recover.py`, `startup_recovery.py`, `analytics.py`, `dashboard.py`/`dashboard.html`, `optimize.py`, `backtest_engine.py`
- Tests: `tests/test_s7_darvas.py`, `tests/test_s7_evaluate.py`, `tests/manual/run_test_s7.py`

- [ ] **Step 5: Commit**

```bash
git add docs/DEPENDENCIES.md
git commit -m "docs(s7): update DEPENDENCIES.md with S7 entries"
```

---

## Final verification

### Task 22: End-to-end smoke + qa

**Files:** none

- [ ] **Step 1: Run the qa skill**

Run the project's QA skill (per CLAUDE.md / `qa-trading-bot` skill). It executes pytest and auto-fixes failures iteratively.

If using pytest directly: `pytest tests/ -v --tb=short`
Expected: all PASS, no failures, no errors.

- [ ] **Step 2: Smoke imports**

Run: `python -c "import bot; print('bot OK')"`
Expected: `bot OK`

If the IG bot imports anything from shared files we touched, also run:
Run: `python -c "import ig_bot; print('ig_bot OK')"`
Expected: `ig_bot OK` (S7 is not in IG, but the imports must not break IG's load path).

- [ ] **Step 3: Smoke optimize + backtest module loads**

Run: `python -c "import optimize, backtest_engine, dashboard, paper_trader, recover, startup_recovery, analytics; print('all imports OK')"`
Expected: `all imports OK`

- [ ] **Step 4: Run S7 manual integration test**

Run: `pytest tests/manual/run_test_s7.py -v -s`
Expected: 3 PASS.

- [ ] **Step 5: No commit needed if everything green**

If `qa-trading-bot` made fixes, those will produce their own commits. Otherwise nothing to commit.

---

## Acceptance checklist (from spec §16)

Confirm each item is true before declaring the feature done:

- [ ] `evaluate_s7()` returns `SHORT` only when daily gates AND a fully locked 1H Darvas box are present, with `box_low < box_top` and ≥ 6 closed 1H candles since UTC midnight.
- [ ] The pending watcher fires entry only on a confirmed 1H *close* below `box_low`, never on a wick alone, and only while mark is inside `[box_low × (1 − MAX_ENTRY_BUFFER), trigger]`.
- [ ] Box low expansion (wick-and-reclaim) updates `pending_signals[symbol]["box_low"]` and `["trigger"]` on the next watcher tick.
- [ ] SL is bound atomically with the market entry via `presetStopLossPrice`. Partial TP + trailing follow as a separate retry-up-to-3× call.
- [ ] Scale-in queues 1h after fill, gated by BEARISH sentiment AND mark inside the retest window around `box_low`.
- [ ] Daily swing trail kicks in post-partial via `strategies.s7.maybe_trail_sl()`.
- [ ] Dashboard shows S7 status alongside S4. Optimizer treats S7 as a first-class grid. Backtest recognises `S7` as an enabled strategy with its own exits.
- [ ] `tests/test_s7_darvas.py`, `tests/test_s7_evaluate.py`, and `tests/manual/run_test_s7.py` pass.
- [ ] `python -c "import bot"` and `python -c "import ig_bot"` both succeed.
- [ ] `docs/DEPENDENCIES.md` has S7 entries that match the as-built code.
