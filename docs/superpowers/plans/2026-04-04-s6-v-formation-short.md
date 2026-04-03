# S6 V-Formation Liquidity Sweep Short — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add S6, a daily-chart short strategy that detects a V-formation (overbought swing high → 30%+ spike down → direct bullish pivot) then waits for a liquidity sweep above the peak before shorting the reversal.

**Architecture:** `evaluate_s6()` in `strategy.py` detects the V-pattern and returns `PENDING_SHORT` + `peak_level`. `bot.py` maintains a persistent `self.s6_watchers` dict (keyed by symbol) that tracks the two-phase entry: phase 1 waits for `mark > peak_level` (fakeout), phase 2 executes a market SHORT when `mark < peak_level`. Exit reuses `_place_s2_exits()` (same as S4) via a new `use_s6_exits` flag in `trader.py`.

**Tech Stack:** Python, pandas, existing `strategy.py` / `bot.py` / `trader.py` / `snapshot.py` / `dashboard.html` patterns.

**Spec:** `docs/superpowers/specs/2026-04-03-s6-v-formation-short-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `config_s6.py` | **Create** | All S6 tunable parameters |
| `strategy.py` | **Modify** | Add `evaluate_s6()` (swing-high/low V-detector) |
| `trader.py` | **Modify** | Add `use_s6_exits=False` to `open_short()`, wired to `_place_s2_exits()` |
| `bot.py` | **Modify** | `_TRADE_FIELDS`, `s6_watchers` init, `_queue_s6_watcher()`, `_evaluate_pair()` wiring, `update_pair_state()` S6 fields, `_execute_s6()`, `_process_s6_watchers()`, scan-loop call |
| `dashboard.html` | **Modify** | S6 tab, `s6CardHTML()`, `renderPairGrid()` dispatch, chart interval map, trade chart tab map, tab visibility array |
| `tests/test_s6.py` | **Create** | Unit tests for `evaluate_s6()` |
| `DEPENDENCIES.md` | **Modify** | Document S6 state fields + CSV columns after all tasks done |

---

## Task 1: Create config_s6.py

**Files:**
- Create: `config_s6.py`

- [ ] **Step 1: Create the file**

```python
# config_s6.py
# ============================================================
#  Strategy 6 Configuration — V-Formation Liquidity Sweep Short
# ============================================================
S6_ENABLED = True

# ── Pattern detection ─────────────────────────────────────── #
S6_RSI_LOOKBACK      = 14      # RSI period
S6_SPIKE_LOOKBACK    = 30      # Max daily candles to scan for a V-formation
S6_OVERBOUGHT_RSI    = 70.0    # Minimum RSI at the swing-high candle
S6_MIN_DROP_PCT      = 0.30    # Minimum drop from peak_level to spike low (30%)

# ── Exit levels ───────────────────────────────────────────── #
S6_SL_PCT              = 0.50  # SL = fill * (1 + 0.50), i.e. 50% above entry
S6_TRAILING_TRIGGER_PCT = 1.00 # Partial-TP trigger = fill * (1 - 1.00), i.e. 100% below entry
S6_TRAIL_RANGE_PCT     = 10    # 10% trailing range on remainder (Bitget rangeRate integer, same units as S5_TRAIL_RANGE_PCT=5)

# ── Position sizing ───────────────────────────────────────── #
S6_LEVERAGE       = 10
S6_TRADE_SIZE_PCT = 0.04
```

- [ ] **Step 2: Verify import works**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
python -c "import config_s6; print('S6_ENABLED:', config_s6.S6_ENABLED)"
```

Expected: `S6_ENABLED: True`

- [ ] **Step 3: Commit**

```bash
git add config_s6.py
git commit -m "feat(s6): add config_s6.py"
```

---

## Task 2: Tests + evaluate_s6() in strategy.py

**Files:**
- Create: `tests/test_s6.py`
- Modify: `strategy.py` (append after `evaluate_s5`)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_s6.py`:

```python
"""Tests for evaluate_s6 — V-Formation Liquidity Sweep Short."""
import pytest
import pandas as pd
import numpy as np


# ── Fixtures ──────────────────────────────────────────────── #

def _rising_closes(n, start=100.0, pct=0.04):
    """n closes rising pct each step."""
    return [start * ((1 + pct) ** i) for i in range(n)]


def _make_v_df():
    """
    Daily df with a clean V-formation:
      - candles 0-18: strong uptrend (+4%/day) → RSI > 70 by candle 18
      - candle 18: swing-high  (local max, RSI > 70)
      - candle 19: spike down 38% from candle 18's high
      - candle 20: bullish pivot (close > open, close > spike close)
      - candles 21-39: slow recovery

    peak_level = df.loc[18, "high"]
    """
    closes = _rising_closes(19)  # indices 0-18 (uptrend → peak)

    peak_high = closes[-1] * 1.005  # swing-high candle's high
    spike_close = peak_high * 0.62  # 38% drop
    pivot_close = spike_close * 1.14  # bullish pivot, clearly above spike close

    closes += [spike_close]   # index 19 — spike
    closes += [pivot_close]   # index 20 — pivot
    closes += [pivot_close * (1.005 ** i) for i in range(1, 20)]  # recovery

    n = len(closes)
    df = pd.DataFrame({
        "open":   [c * 0.998 for c in closes],
        "high":   [c * 1.003 for c in closes],
        "low":    [c * 0.997 for c in closes],
        "close":  closes,
        "volume": [1000.0] * n,
    })

    # Ensure candle 18 is a strict local maximum
    df.loc[18, "high"] = peak_high
    df.loc[17, "high"] = peak_high * 0.994   # just below peak
    df.loc[19, "high"] = spike_close * 1.002  # spike candle high < peak

    # Spike candle: opens near peak, closes at spike_close
    df.loc[19, "open"]  = closes[18] * 0.99
    df.loc[19, "close"] = spike_close
    df.loc[19, "low"]   = spike_close * 0.995

    # Pivot candle: bullish (close > open, close > spike close)
    df.loc[20, "open"]  = spike_close * 0.999
    df.loc[20, "close"] = pivot_close
    df.loc[20, "high"]  = pivot_close * 1.003
    df.loc[20, "low"]   = spike_close * 0.997

    return df


def _make_shallow_drop_df():
    """Like _make_v_df but spike is only 20% — below S6_MIN_DROP_PCT."""
    df = _make_v_df()
    peak_high = df.loc[18, "high"]
    shallow_close = peak_high * 0.82  # 18% drop — not enough
    df.loc[19, "close"] = shallow_close
    df.loc[19, "low"]   = shallow_close * 0.995
    # Pivot candle still bullish but above shallow close
    pivot_close = shallow_close * 1.05
    df.loc[20, "open"]  = shallow_close * 0.999
    df.loc[20, "close"] = pivot_close
    return df


def _make_no_pivot_df():
    """Like _make_v_df but spike candle is the last candle — no pivot candle yet."""
    df = _make_v_df()
    # Trim to candle 19 (spike is the last row, no candle 20)
    return df.iloc[:20].reset_index(drop=True)


def _make_bearish_pivot_df():
    """Like _make_v_df but pivot candle is bearish (close < open)."""
    df = _make_v_df()
    spike_close = df.loc[19, "close"]
    # Make pivot bearish: open > close, but close still above spike_close
    df.loc[20, "open"]  = spike_close * 1.10
    df.loc[20, "close"] = spike_close * 1.02  # close < open → bearish
    return df


# ── Tests ─────────────────────────────────────────────────── #

def test_hold_when_direction_not_bearish():
    from strategy import evaluate_s6
    df = _make_v_df()
    sig, *_ = evaluate_s6("TEST", df, "BULLISH")
    assert sig == "HOLD"


def test_hold_when_s6_disabled(monkeypatch):
    import config_s6
    monkeypatch.setattr(config_s6, "S6_ENABLED", False)
    from strategy import evaluate_s6
    df = _make_v_df()
    sig, *_ = evaluate_s6("TEST", df, "BEARISH")
    assert sig == "HOLD"


def test_pending_short_on_valid_v_formation():
    from strategy import evaluate_s6
    df = _make_v_df()
    sig, peak_level, sl_price, drop_pct, rsi_at_peak, reason = evaluate_s6("TEST", df, "BEARISH")
    assert sig == "PENDING_SHORT"
    assert peak_level > 0
    assert sl_price > peak_level  # SL is above entry for a short
    assert drop_pct >= 0.30
    assert rsi_at_peak > 70.0
    assert "V-formation" in reason


def test_peak_level_equals_swing_high_candle_high():
    from strategy import evaluate_s6
    df = _make_v_df()
    _, peak_level, _, _, _, _ = evaluate_s6("TEST", df, "BEARISH")
    # peak_level must match the high of candle 18 (the swing high)
    assert abs(peak_level - df.loc[18, "high"]) < 1e-6


def test_sl_price_is_peak_times_sl_pct():
    import config_s6
    from strategy import evaluate_s6
    df = _make_v_df()
    _, peak_level, sl_price, _, _, _ = evaluate_s6("TEST", df, "BEARISH")
    expected_sl = peak_level * (1 + config_s6.S6_SL_PCT)
    assert abs(sl_price - expected_sl) < 1e-6


def test_hold_when_drop_below_threshold():
    from strategy import evaluate_s6
    df = _make_shallow_drop_df()
    sig, *_ = evaluate_s6("TEST", df, "BEARISH")
    assert sig == "HOLD"


def test_hold_when_no_pivot_candle():
    from strategy import evaluate_s6
    df = _make_no_pivot_df()
    sig, *_ = evaluate_s6("TEST", df, "BEARISH")
    assert sig == "HOLD"


def test_hold_when_pivot_candle_bearish():
    from strategy import evaluate_s6
    df = _make_bearish_pivot_df()
    sig, *_ = evaluate_s6("TEST", df, "BEARISH")
    assert sig == "HOLD"


def test_hold_when_rsi_at_swing_high_below_threshold(monkeypatch):
    """When RSI at the swing-high candle is < 70, no signal."""
    import strategy as strat
    # Return constant RSI of 60 everywhere
    monkeypatch.setattr(strat, "calculate_rsi", lambda closes, period=14: pd.Series([60.0] * len(closes), index=closes.index))
    from strategy import evaluate_s6
    df = _make_v_df()
    sig, *_ = evaluate_s6("TEST", df, "BEARISH")
    assert sig == "HOLD"
```

- [ ] **Step 2: Run tests to confirm they all fail**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
pytest tests/test_s6.py -v 2>&1 | head -30
```

Expected: `ImportError: cannot import name 'evaluate_s6' from 'strategy'`

- [ ] **Step 3: Implement evaluate_s6() in strategy.py**

Append this function after `evaluate_s5` (find the last line of `evaluate_s5`, then append after it):

```python
# ── S6: V-Formation Liquidity Sweep Short ─────────────────── #

def evaluate_s6(
    symbol: str,
    daily_df: pd.DataFrame,
    allowed_direction: str,
) -> tuple[Signal, float, float, float, float, str]:
    """
    Scans the last S6_SPIKE_LOOKBACK daily candles for a V-formation:
      1. Swing high: local maximum with RSI > S6_OVERBOUGHT_RSI
      2. Spike low : price drops >= S6_MIN_DROP_PCT from swing-high's high
      3. V-pivot   : candle immediately after spike low is bullish
                     (close > open AND close > spike_low_candle.close)

    Returns (signal, peak_level, sl_price, drop_pct, rsi_at_peak, reason).
    signal is PENDING_SHORT when a valid V is found in a BEARISH market.
    """
    from config_s6 import (
        S6_ENABLED, S6_RSI_LOOKBACK, S6_SPIKE_LOOKBACK,
        S6_OVERBOUGHT_RSI, S6_MIN_DROP_PCT, S6_SL_PCT,
    )

    _hold = lambda msg: ("HOLD", 0.0, 0.0, 0.0, 0.0, msg)

    if not S6_ENABLED:
        return _hold("S6 disabled")

    if allowed_direction != "BEARISH":
        return _hold(f"Direction {allowed_direction!r} — S6 requires BEARISH")

    min_rows = S6_SPIKE_LOOKBACK + S6_RSI_LOOKBACK + 2
    if len(daily_df) < min_rows:
        return _hold(f"Insufficient daily candles ({len(daily_df)} < {min_rows})")

    rsi_series = calculate_rsi(daily_df["close"], S6_RSI_LOOKBACK)

    # Work over the lookback window (reset index for safe iloc arithmetic)
    window    = daily_df.iloc[-(S6_SPIKE_LOOKBACK + 2):].reset_index(drop=True)
    rsi_win   = rsi_series.iloc[-(S6_SPIKE_LOOKBACK + 2):].reset_index(drop=True)
    n         = len(window)

    # Scan swing highs from most recent to oldest.
    # i must have at least 1 candle before (i-1) and 2 after (spike + pivot).
    for i in range(n - 3, 0, -1):
        # ── Swing-high check ─────────────────────────────── #
        if not (window["high"].iloc[i] > window["high"].iloc[i - 1] and
                window["high"].iloc[i] > window["high"].iloc[i + 1]):
            continue
        if pd.isna(rsi_win.iloc[i]) or rsi_win.iloc[i] <= S6_OVERBOUGHT_RSI:
            continue

        peak_level  = float(window["high"].iloc[i])
        rsi_at_peak = float(rsi_win.iloc[i])

        # ── Spike low: minimum low after swing high ────────── #
        after_high   = window.iloc[i + 1:]
        spike_abs    = int(after_high["low"].idxmin())   # absolute index in window
        spike_low    = float(window["low"].iloc[spike_abs])
        spike_candle = window.iloc[spike_abs]

        # ── Drop magnitude ────────────────────────────────── #
        drop_pct = (peak_level - spike_low) / peak_level
        if drop_pct < S6_MIN_DROP_PCT:
            continue

        # ── Pivot candle must exist (spike cannot be the last row) ─ #
        if spike_abs + 1 >= n:
            continue

        # ── Direct V-pivot: immediate bullish candle ──────── #
        pivot = window.iloc[spike_abs + 1]
        if not (pivot["close"] > pivot["open"] and
                pivot["close"] > spike_candle["close"]):
            continue

        # ── Valid V-formation found ────────────────────────── #
        sl_price = peak_level * (1 + S6_SL_PCT)
        reason   = (
            f"V-formation ✅ | RSI at peak {rsi_at_peak:.1f} | "
            f"Drop {drop_pct * 100:.1f}% | Peak {peak_level:.5f} | "
            f"SL {sl_price:.5f}"
        )
        return "PENDING_SHORT", peak_level, sl_price, drop_pct, rsi_at_peak, reason

    return _hold(f"No V-formation in last {S6_SPIKE_LOOKBACK} days")
```

- [ ] **Step 4: Run tests — all must pass**

```bash
pytest tests/test_s6.py -v
```

Expected: 9 tests PASSED

- [ ] **Step 5: Confirm strategy.py still imports cleanly**

```bash
python -c "from strategy import evaluate_s6; print('OK')"
```

- [ ] **Step 6: Commit**

```bash
git add strategy.py tests/test_s6.py
git commit -m "feat(s6): add evaluate_s6 with V-formation detector and tests"
```

---

## Task 3: trader.py — use_s6_exits flag

**Files:**
- Modify: `trader.py`

- [ ] **Step 1: Find open_short() signature**

```bash
grep -n "^def open_short" trader.py
```

Note the line number. Then read the function signature and the `use_s4_exits` block inside it.

- [ ] **Step 2: Add use_s6_exits parameter to open_short()**

In `trader.py`, add `import config_s6` near the top of the file (alongside the other config imports).

Then find `open_short`'s signature and add `use_s6_exits: bool = False` after `use_s4_exits: bool = False`.

Find the `use_s4_exits` block inside `open_short` — it looks up the actual fill price (`fill = openPriceAvg`), then calls `_place_s2_exits`. Add an `elif use_s6_exits:` block immediately after it, using the same `fill` variable that is already in scope at that point:

```python
elif use_s6_exits:
    trail_trigger = fill * (1 - config_s6.S6_TRAILING_TRIGGER_PCT)
    tpsl_ok = _place_s2_exits(
        symbol, hold_side, str(qty),
        sl_trig, sl_exec,
        trail_trigger, config_s6.S6_TRAIL_RANGE_PCT,
    )
```

(`fill` is the actual `openPriceAvg` fetched after the market order — identical to how S4 computes its trail_trigger.)

- [ ] **Step 3: Verify trader.py imports cleanly**

```bash
python -c "import trader; print('trader OK')"
```

- [ ] **Step 4: Commit**

```bash
git add trader.py
git commit -m "feat(s6): add use_s6_exits flag to open_short"
```

---

## Task 4: bot.py — _TRADE_FIELDS, init, pair-state wiring

**Files:**
- Modify: `bot.py`

- [ ] **Step 1: Add S6 snapshot fields to _TRADE_FIELDS**

Open `bot.py`. Find `_TRADE_FIELDS` (line 101). After the S5 snapshot block, add:

```python
    # S6 snapshot
    "snap_s6_peak", "snap_s6_drop_pct", "snap_s6_rsi_at_peak",
```

The section should look like:
```python
    # S5 snapshot
    "snap_s5_ob_low", "snap_s5_ob_high", "snap_s5_tp",
    # S6 snapshot
    "snap_s6_peak", "snap_s6_drop_pct", "snap_s6_rsi_at_peak",
    # S/R clearance at entry (S2/S3/S4/S5/S6)
    "snap_sr_clearance_pct",
```

- [ ] **Step 2: Add self.s6_watchers to __init__**

Find `self.candidates` or `self.pending_signals` initialization in `__init__`. Add nearby:

```python
self.s6_watchers: dict[str, dict] = {}   # S6 two-phase entry watchers keyed by symbol
```

- [ ] **Step 3: Add import for config_s6 and evaluate_s6**

At the top of `bot.py`, find where `config_s4`, `config_s5` are imported. Add:

```python
import config_s6
from strategy import evaluate_s6
```

(Or follow the existing import pattern — some strategies use late imports inside methods. Follow whichever pattern `config_s5` uses in bot.py.)

- [ ] **Step 4: Add _queue_s6_watcher() method to bot.py**

Add this method near the `_queue_s5_pending` method (or near the S5 execution methods):

```python
def _queue_s6_watcher(
    self,
    symbol: str,
    peak_level: float,
    sl_price: float,
    drop_pct: float,
    rsi_at_peak: float,
    reason: str,
    daily_df,
) -> None:
    """Add or refresh an S6 two-phase watcher."""
    if symbol in self.s6_watchers:
        existing = self.s6_watchers[symbol]
        # Refresh peak if meaningfully changed (> 0.1%)
        if abs(existing["peak_level"] - peak_level) / peak_level > 0.001:
            logger.info(f"[S6][{symbol}] Peak updated {existing['peak_level']:.5f} → {peak_level:.5f}")
            existing["peak_level"] = peak_level
            existing["sl"]         = sl_price
        return
    from datetime import datetime, timezone
    self.s6_watchers[symbol] = {
        "symbol":       symbol,
        "peak_level":   peak_level,
        "sl":           sl_price,
        "fakeout_seen": False,
        "reason":       reason,
        "detected_at":  datetime.now(timezone.utc),
        "drop_pct":     drop_pct,
        "rsi_at_peak":  rsi_at_peak,
        "daily_df":     daily_df,
    }
    logger.info(
        f"[S6][{symbol}] Watching — peak {peak_level:.5f} "
        f"drop {drop_pct*100:.1f}% RSI {rsi_at_peak:.1f}"
    )
```

- [ ] **Step 5: Call evaluate_s6() inside _evaluate_pair() and update pair_state**

Find the section in `_evaluate_pair()` that calls `evaluate_s5()` and writes S5 fields to `update_pair_state()`. Add the S6 evaluation immediately after:

```python
# ── S6 evaluation ──────────────────────────────────────── #
s6_sig, s6_peak, s6_sl, s6_drop_pct, s6_rsi_peak, s6_reason = evaluate_s6(
    symbol, daily_df, allowed_direction
)
if s6_sig == "PENDING_SHORT" and s6_peak > 0:
    if symbol not in self.active_positions:
        self._queue_s6_watcher(
            symbol, s6_peak, s6_sl, s6_drop_pct, s6_rsi_peak, s6_reason, daily_df
        )
```

Then in the `update_pair_state(symbol, {...})` call for this pair (lines ~957–999), add S6 fields:

```python
"s6_signal":       s6_sig,
"s6_reason":       s6_reason,
"s6_peak_level":   round(s6_peak, 8) if s6_peak else None,
"s6_sl":           round(s6_sl, 8)   if s6_sl   else None,
"s6_fakeout_seen": self.s6_watchers.get(symbol, {}).get("fakeout_seen", False),
```

- [ ] **Step 6: Verify bot.py imports cleanly**

```bash
python -c "import bot; print('bot OK')"
```

- [ ] **Step 7: Run the test suite**

```bash
pytest tests/ -x -q
```

Expected: all existing tests pass + S6 tests pass.

- [ ] **Step 8: Commit**

```bash
git add bot.py
git commit -m "feat(s6): wire evaluate_s6 into _evaluate_pair, add _TRADE_FIELDS entries, s6_watchers init"
```

---

## Task 5: bot.py — _execute_s6() and _process_s6_watchers()

**Files:**
- Modify: `bot.py`

- [ ] **Step 1: Add _execute_s6() method**

Add this method near `_execute_s4`:

```python
def _execute_s6(self, w: dict, balance: float) -> bool:
    """Execute a market SHORT for an S6 V-formation sweep entry."""
    symbol = w["symbol"]
    if symbol in self.active_positions:
        return False

    mark_now  = tr.get_mark_price(symbol)
    sl_actual = mark_now * (1 + config_s6.S6_SL_PCT)
    # trail_trigger is computed inside open_short from the actual fill price

    logger.info(
        f"[S6][{symbol}] Executing SHORT — mark {mark_now:.5f} "
        f"peak {w['peak_level']:.5f} SL {sl_actual:.5f}"
    )

    trade = tr.open_short(
        symbol,
        sl_floor=sl_actual,
        leverage=config_s6.S6_LEVERAGE,
        trade_size_pct=config_s6.S6_TRADE_SIZE_PCT,
        use_s6_exits=True,
    )

    if not trade:
        logger.warning(f"[S6][{symbol}] open_short returned None — skipping")
        return False

    fill = trade.get("entry", mark_now)

    trade["strategy"]            = "S6"
    trade["snap_s6_peak"]        = round(w["peak_level"], 8)
    trade["snap_s6_drop_pct"]    = round(w["drop_pct"] * 100, 2)
    trade["snap_s6_rsi_at_peak"] = round(w["rsi_at_peak"], 1)
    trade["snap_sentiment"]      = self.sentiment.direction

    _log_trade("S6_SHORT", trade)

    snapshot.save_snapshot(
        trade_id=trade.get("trade_id", ""),
        event="open",
        symbol=symbol,
        interval="1D",
        candles=_df_to_candles(w["daily_df"]),
        event_price=round(fill, 8),
    )

    self.active_positions[symbol] = {
        **trade,
        "strategy":     "S6",
        "s6_peak":      w["peak_level"],
        "snap_interval": "1D",
    }
    logger.info(f"[S6][{symbol}] Position opened — fill {fill:.5f}")
    return True
```

- [ ] **Step 2: Add _process_s6_watchers() method**

Add this method near `_execute_s6`:

```python
def _process_s6_watchers(self, balance: float) -> None:
    """
    Two-phase S6 entry watcher — called every scan cycle.
    Phase 1: wait for mark > peak_level  (fakeout / liquidity sweep above resistance)
    Phase 2: execute SHORT when mark < peak_level after fakeout seen
    """
    from datetime import datetime, timezone, timedelta

    to_remove = []

    for symbol, w in list(self.s6_watchers.items()):
        # ── Already in a position ────────────────────────── #
        if symbol in self.active_positions:
            to_remove.append(symbol)
            continue

        # ── Sentiment gate: cancel if no longer bearish ─── #
        if self.sentiment.direction != "BEARISH":
            logger.info(f"[S6][{symbol}] Cancelled — sentiment {self.sentiment.direction}")
            to_remove.append(symbol)
            continue

        # ── 30-day expiry ─────────────────────────────────── #
        age = datetime.now(timezone.utc) - w["detected_at"]
        if age > timedelta(days=config_s6.S6_SPIKE_LOOKBACK):
            logger.info(f"[S6][{symbol}] Expired ({age.days}d)")
            to_remove.append(symbol)
            continue

        mark = tr.get_mark_price(symbol)
        peak = w["peak_level"]

        if not w["fakeout_seen"]:
            # ── Phase 1: watch for sweep above peak ───────── #
            if mark > peak:
                logger.info(f"[S6][{symbol}] Fakeout ✅ mark {mark:.5f} > peak {peak:.5f}")
                w["fakeout_seen"] = True
        else:
            # ── Phase 2: execute when price drops back below ─ #
            if mark < peak:
                logger.info(f"[S6][{symbol}] Entry ✅ mark {mark:.5f} < peak {peak:.5f}")
                self._execute_s6(w, balance)
                to_remove.append(symbol)

    for symbol in to_remove:
        self.s6_watchers.pop(symbol, None)
```

- [ ] **Step 3: Wire _process_s6_watchers() into the main scan loop**

Find where the main scan loop calls `_execute_best_candidate()` or processes S5 pending signals. Add the S6 watcher call immediately after:

```python
self._process_s6_watchers(balance)
```

Place it after `_execute_best_candidate()` so S6 runs every tick independently of the candidate ranking system.

- [ ] **Step 4: Verify bot.py imports cleanly**

```bash
python -c "import bot; print('bot OK')"
```

- [ ] **Step 5: Run the test suite**

```bash
pytest tests/ -x -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add bot.py
git commit -m "feat(s6): add _execute_s6, _process_s6_watchers, wire into scan loop"
```

---

## Task 6: dashboard.html — S6 tab and s6CardHTML()

**Files:**
- Modify: `dashboard.html`

- [ ] **Step 1: Add S6 tab button**

Find the `<div class="strat-tabs">` block (around line 1034). After the S5 tab button, add:

```html
<button class="strat-tab" id="tab-s6" onclick="switchTab('s6')">S6 — V-Sweep</button>
```

- [ ] **Step 2: Add s6CardHTML() function**

Find `s5CardHTML` (around line 2535). Immediately after it ends, add:

```javascript
function s6CardHTML(sym, ps) {
  const sig        = ps.s6_signal || 'HOLD';
  const reason     = ps.s6_reason || '—';
  const symShort   = sym.replace('USDT', '');
  const cardClass  = sig === 'PENDING_SHORT' ? 'signal-short' : '';
  const sigLabel   = sig === 'PENDING_SHORT' ? 'PENDING' : sig;

  const peakLevel   = ps.s6_peak_level  ? ps.s6_peak_level.toFixed(5)  : '—';
  const slPrice     = ps.s6_sl          ? ps.s6_sl.toFixed(5)           : '—';
  const fakeoutSeen = ps.s6_fakeout_seen || false;

  // Infer check states from reason string
  const rsiOk    = reason.includes('RSI at peak') && !reason.includes('No V');
  const dropOk   = reason.includes('Drop')        && !reason.includes('No V');
  const pivotOk  = reason.includes('V-formation ✅');

  // Progressive muting: each check gates the next
  const rsiClass   = rsiOk  ? 'pass'  : (sig !== 'HOLD' ? 'pass' : 'fail');
  const dropClass  = !rsiOk ? 'muted' : (dropOk  ? 'pass' : 'fail');
  const pivotClass = !dropOk ? 'muted' : (pivotOk ? 'pass' : 'fail');
  const fakeClass  = !pivotOk ? 'muted' : (fakeoutSeen ? 'pass' : 'warn');

  // Drop % from reason, e.g. "Drop 38.0%"
  const dropMatch = reason.match(/Drop (\d+\.\d+)%/);
  const dropLabel = dropMatch ? dropMatch[1] + '%' : '—';

  // Phase indicator
  let phaseLabel = '';
  if (sig === 'PENDING_SHORT') {
    phaseLabel = fakeoutSeen
      ? '<span style="color:var(--amber);font-size:10px">Phase 2 — watching entry below peak</span>'
      : '<span style="color:var(--amber);font-size:10px">Phase 1 — waiting for sweep above peak</span>';
  }

  // Age
  const age     = ps.updated_at ? _age(ps.updated_at) : '';
  const rClass  = sig === 'HOLD' ? 'muted' : '';

  return `
<div class="pair-card ${cardClass}" data-symbol="${sym}">
  <div class="pair-top">
    <div class="pair-name" title="${sym}">${symShort}</div>
    <span class="pair-sig-badge ${sigLabel}">${sigLabel}</span>
  </div>
  <div class="pair-checks">
    <div class="pair-check">
      <span class="check-label">RSI &gt; ${ps.s6_overbought_rsi || 70} at peak</span>
      <span class="check-val ${rsiClass}">${rsiOk || sig !== 'HOLD' ? 'Yes ✓' : 'Not found'}</span>
    </div>
    <div class="pair-check">
      <span class="check-label">Drop ≥ 30%</span>
      <span class="check-val ${dropClass}">${dropClass === 'muted' ? '—' : (dropOk ? dropLabel + ' ✓' : 'Too small')}</span>
    </div>
    <div class="pair-check">
      <span class="check-label">Direct V-pivot</span>
      <span class="check-val ${pivotClass}">${pivotClass === 'muted' ? '—' : (pivotOk ? 'Found ✓' : 'Not found')}</span>
    </div>
    <div class="pair-check">
      <span class="check-label">Fakeout above peak</span>
      <span class="check-val ${fakeClass}">${fakeClass === 'muted' ? '—' : (fakeoutSeen ? 'Seen ✓' : '⏳ Waiting')}</span>
    </div>
    <div class="pair-check">
      <span class="check-label">Peak / SL</span>
      <span class="check-val muted">${peakLevel} / ${slPrice}</span>
    </div>
  </div>
  <div class="pair-reason ${rClass}">${phaseLabel ? phaseLabel + '<br>' : ''}${_truncReason(reason)} ${age}</div>
</div>`;
}
```

- [ ] **Step 3: Add S6 dispatch to renderPairGrid()**

Find the `renderPairGrid` function (around line 2601). Inside it, find the `if (activeTab === 's5')` chain. Add S6 before the final `else`:

```javascript
} else if (activeTab === 's6') {
  html = pairs.map(sym => s6CardHTML(sym, pairStates[sym] || {})).join('');
}
```

- [ ] **Step 4: Add S6 to tab visibility array**

Find (around line 1383):
```javascript
['S1','S2','S3','S4','S5'].forEach(k => {
```

Change to:
```javascript
['S1','S2','S3','S4','S5','S6'].forEach(k => {
```

- [ ] **Step 5: Add S6 to chart interval map**

Find (around line 1566):
```javascript
const _ENTRY_CHART_INTERVAL = {S1:'3m', S2:'1D', S3:'15m', S4:'1D', S5:'15m'};
```

Change to:
```javascript
const _ENTRY_CHART_INTERVAL = {S1:'3m', S2:'1D', S3:'15m', S4:'1D', S5:'15m', S6:'1D'};
```

- [ ] **Step 6: Add S6 to trade chart tab map**

Find (around line 2244):
```javascript
const tabMap = { S1: 's1', S2: 's2', S3: 's3', S4: 's4', S5: 's5' };
```

Change to:
```javascript
const tabMap = { S1: 's1', S2: 's2', S3: 's3', S4: 's4', S5: 's5', S6: 's6' };
```

- [ ] **Step 7: Verify dashboard.html renders (smoke test)**

```bash
python -c "import dashboard; print('dashboard OK')"
```

- [ ] **Step 8: Commit**

```bash
git add dashboard.html
git commit -m "feat(s6): add S6 tab and s6CardHTML dashboard panel"
```

---

## Task 7: Run full QA and update DEPENDENCIES.md

**Files:**
- Modify: `docs/DEPENDENCIES.md`

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/ -x -q
```

Expected: all tests pass, no failures.

- [ ] **Step 2: Verify both bots import**

```bash
python -c "import bot; print('Bitget OK')"
python -c "import ig_bot; print('IG OK')"
```

- [ ] **Step 3: Update DEPENDENCIES.md**

In `docs/DEPENDENCIES.md`, find Section 4.1 (`pair_states` structure). Add S6 fields to the `pair_states` field list:

```python
# S6 fields
"s6_signal":       str,           # "PENDING_SHORT" | "HOLD"
"s6_reason":       str,
"s6_peak_level":   float | None,  # resistance watch level
"s6_sl":           float | None,  # stop loss price
"s6_fakeout_seen": bool | None,   # whether phase-1 fakeout has been seen
```

In Section 4.2 (`trades.csv` columns), add to the S6 snapshot block (append after S5):

```
snap_s6_peak, snap_s6_drop_pct, snap_s6_rsi_at_peak
```

Also note column count increased by 3 (was 38, now 41).

In the Document History at the bottom of DEPENDENCIES.md, add:

```
- 2026-04-04: S6 V-Formation Liquidity Sweep Short — added evaluate_s6() to strategy.py (Bitget-only);
  pair_states gained 5 s6_* fields; trades.csv gained 3 snap_s6_* columns (38→41);
  bot.py gained s6_watchers two-phase entry tracker; dashboard.html gained S6 tab + s6CardHTML.
```

- [ ] **Step 4: Commit everything**

```bash
git add docs/DEPENDENCIES.md
git commit -m "docs: update DEPENDENCIES.md for S6 — pair_states fields, CSV columns, history"
```

- [ ] **Step 5: Push branch**

```bash
git push -u origin feat/s6-v-formation-short
```

---

## Verification Checklist

```bash
# 1. All tests pass
pytest tests/ -x -q

# 2. Both bots import cleanly
python -c "import bot; print('Bitget OK')"
python -c "import ig_bot; print('IG OK')"

# 3. evaluate_s6 importable and functional
python -c "from strategy import evaluate_s6; print('evaluate_s6 OK')"

# 4. config_s6 loads
python -c "import config_s6; print('S6_ENABLED:', config_s6.S6_ENABLED)"

# 5. Dashboard loads
python -c "import dashboard; print('dashboard OK')"

# 6. CSV column count (should be 41)
head -1 trades_paper.csv | tr ',' '\n' | wc -l
```
