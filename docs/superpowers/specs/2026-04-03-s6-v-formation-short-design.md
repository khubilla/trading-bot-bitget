# S6 V-Formation Liquidity Sweep Short — Design Spec

**Date:** 2026-04-03
**Branch:** feat/s6-v-formation-short
**Status:** Approved for implementation

---

## Context

S6 is a new Bitget-only short strategy that exploits a V-formation liquidity sweep pattern on the daily chart. It is the natural successor to S4: S4 shorts the initial overbought pump (RSI divergence at the peak), while S6 shorts the dead-cat bounce — after the S4-style spike-down has played out and price attempts to recover back to the same peak resistance, S6 waits for a liquidity sweep above that level and enters short on the reversal.

The setup requires a bearish market condition and detects:

1. An overbought swing high (RSI > 70) that becomes a peak pivot
2. A sharp spike down ≥ 30% from the peak high (the left arm of the V)
3. A direct V-pivot — an immediate bullish recovery candle with no consolidation at the bottom (the right arm)
4. A liquidity sweep above the peak level — price briefly exceeds the resistance, triggering buy-side stops
5. Short entry when price reverses back below the peak level after the sweep

This pattern captures sellers who enter as price fails to hold above the prior peak, riding the reversal back down in a bearish market structure. S4 and S6 will never conflict on the same symbol because the existing `active_positions` guard prevents a second entry while a position is open.

---

## Pattern Definition

```
Daily chart, lookback = 30 candles:

  [Swing high candle: RSI > 70, local price maximum]
        │  ← peak_level = this candle's HIGH
        │ price drops ≥ 30% from peak_level to spike low
  [Spike low candle: local minimum]
        │ immediately followed by:
  [Pivot candle: close > open AND close > spike_low_close]  ← V confirmed
        │ (any number of candles later)
  [Fakeout: mark_price > peak_level]                        ← phase 1 gate
        │ price reverses back below peak_level
  ▶ SHORT market order executed                             ← entry trigger
```

**No consolidation rule:** The candle immediately after the spike low must be bullish (close > open) and must close above the spike low's close. If any candle between the spike low and the pivot candle is bearish or closes below the spike low, the V is invalid.

---

## evaluate_s6() Function

**File:** `strategy.py`

**Signature:**
```python
def evaluate_s6(
    symbol: str,
    daily_df: pd.DataFrame,
    allowed_direction: str,
) -> tuple[Signal, float, float, str]:
    # Returns: (signal, peak_level, sl_price, reason)
```

**Return values:**
- `signal`: `"PENDING_SHORT"` | `"HOLD"`
- `peak_level`: high of the swing high candle (the resistance / entry watch level)
- `sl_price`: `peak_level * (1 + S6_SL_PCT)` — fixed stop above the entry zone
- `reason`: human-readable explanation of the signal decision

**Logic:**
1. Guard: if `allowed_direction != "BEARISH"` → return `HOLD`
2. Guard: if `not S6_ENABLED` → return `HOLD`
3. Compute RSI(14) on daily_df
4. Scan the last `S6_SPIKE_LOOKBACK` candles (default 30), iterating swing highs from most recent to oldest:
   a. Find a **swing high**: candle high is greater than both its immediate neighbours (`high[i] > high[i-1]` and `high[i] > high[i+1]`) AND RSI at that candle > `S6_OVERBOUGHT_RSI`. `peak_level = candle.high`.
   b. Find the **spike low**: the minimum `low` across all candles between the swing high and the end of the lookback window. Record its index.
   c. Check **drop magnitude**: `(peak_level - spike_low) / peak_level >= S6_MIN_DROP_PCT`
   d. Check **pivot candle exists**: the candle at `spike_low_index + 1` must exist in the DataFrame (spike low cannot be the most recent candle — no pivot candle yet → skip).
   e. Check **direct V-pivot**: the candle immediately after the spike low must satisfy:
      - `close > open` (bullish candle)
      - `close > spike_low_candle.close`
   f. If all checks pass → valid V-formation found. Stop scanning (use most recent).
5. Return `PENDING_SHORT` with `peak_level`, `sl_price = peak_level * (1 + S6_SL_PCT)`, and `rsi_at_peak` (stored for CSV snapshot)
6. If no valid V found in lookback → return `HOLD`

---

## Entry Watcher (bot.py)

S6 uses a **two-phase entry watcher** stored in the candidate dict. This is distinct from S5's single-phase pending approach.

### Candidate dict structure:
```python
{
    "strategy": "S6",
    "symbol": symbol,
    "sig": "PENDING_SHORT",
    "peak_level": float,        # resistance / fakeout watch level
    "sl": float,                # peak_level * (1 + S6_SL_PCT)
    "fakeout_seen": False,      # phase 1 gate
    "reason": str,
    "detected_at": timestamp,   # for 30-day expiry
    "daily_df": daily_df,       # for snapshot
}
```

### Watcher logic (checked each scan tick, per symbol):
```
Phase 1 (fakeout_seen=False):
  if mark_price > peak_level:
      candidate["fakeout_seen"] = True

Phase 2 (fakeout_seen=True):
  if mark_price < peak_level:
      → execute _execute_s6(candidate)

Expiry checks (either phase):
  if sentiment.direction == "BULLISH":
      → cancel, remove from candidates
  if (now - detected_at) > 30 days:
      → cancel, remove from candidates
```

---

## Execution (_execute_s6)

**Entry:** Market SHORT (same as S4 execution path)

**Exit orders:** Reuses `_place_s2_exits()` — identical to S2/S4 exit wiring:
1. Position-level SL at `sl_price` (fixed, `S6_SL_PCT` above fill price)
2. Partial TP: sell 50% at `trail_trigger = fill * (1 - S6_TRAILING_TRIGGER_PCT)`
3. Trailing stop: 10% range (`S6_TRAIL_RANGE_PCT`) on remaining 50%

**Snapshot:** Save candle snapshot at open event (same as other strategies).

**CSV logging:**
```python
trade["strategy"]            = "S6"
trade["snap_s6_peak"]        = round(peak_level, 8)
trade["snap_s6_drop_pct"]    = round(drop_pct * 100, 2)   # e.g. 34.2
trade["snap_s6_rsi_at_peak"] = round(rsi_at_peak, 1)      # e.g. 74.3
trade["snap_sr_clearance_pct"] = ...                        # shared field
```

---

## Config (config_s6.py)

```python
S6_ENABLED               = True

# ── Detection ──────────────────────────────
S6_RSI_LOOKBACK          = 14       # RSI period
S6_SPIKE_LOOKBACK        = 30       # Max daily candles to scan for V
S6_OVERBOUGHT_RSI        = 70.0     # RSI threshold at swing high
S6_MIN_DROP_PCT          = 0.30     # Min 30% drop peak → spike low

# ── SL / TP ────────────────────────────────
S6_SL_PCT                = 0.50     # SL 50% above entry (fixed)
S6_TRAILING_TRIGGER_PCT  = 1.00     # Sell 50% when price falls 100% from entry
S6_TRAIL_RANGE_PCT       = 0.10     # 10% trailing range on remainder

# ── Position sizing ────────────────────────
S6_LEVERAGE              = 10
S6_TRADE_SIZE_PCT        = 0.04
```

---

## State Fields

### pair_states (state.json) — new fields:
```python
"s6_signal":      str,           # "PENDING_SHORT" | "HOLD"
"s6_reason":      str,
"s6_peak_level":  float | None,  # resistance watch level
"s6_sl":          float | None,  # stop loss price
"s6_fakeout_seen": bool | None,  # whether phase 1 has triggered
```

### trades.csv — new columns appended to _TRADE_FIELDS:
```
snap_s6_peak, snap_s6_drop_pct, snap_s6_rsi_at_peak
```

---

## Files to Create / Modify

| File | Change |
|------|--------|
| `strategy.py` | Add `evaluate_s6()` function |
| `config_s6.py` | New file — S6 config params |
| `bot.py` | Add S6 candidate collection, two-phase entry watcher, `_execute_s6()`, pair_state updates, `_TRADE_FIELDS` additions, snapshot calls |
| `dashboard.html` | Add S6 tab + `s6CardHTML()` function + `renderPairGrid` dispatch |
| `DEPENDENCIES.md` | Document new S6 fields after implementation |

**Not affected:** `ig_bot.py`, `optimize.py` (new snap_ columns are additive)

---

## Dashboard Panel (dashboard.html)

### New tab
Add an **S6** tab alongside the existing S1–S5 tabs in the pair scanner tab bar.

### s6CardHTML(sym, ps)

Follows the same structure as `s4CardHTML` / `s5CardHTML`. Reads these `pair_states` fields:

| Field | Usage |
|-------|-------|
| `ps.s6_signal` | Signal badge: `PENDING_SHORT` (amber) or `HOLD` (muted) |
| `ps.s6_reason` | Reason text at bottom of card |
| `ps.s6_peak_level` | Displayed as the resistance watch price |
| `ps.s6_sl` | Displayed as the SL price |
| `ps.s6_fakeout_seen` | Phase indicator |
| `ps.updated_at` | Age calculation |

**Card check rows (top → bottom):**

```
RSI > 70 at peak     [pass / fail]           — was swing high overbought?
Drop ≥ 30%           [pass / fail + pct]     — magnitude of the spike
Direct V-pivot       [pass / fail]           — immediate bullish pivot candle
Fakeout above peak   [pass (seen) / ⏳ (waiting)]  — phase 1 gate
```

**Phase indicator in reason area:**
- `fakeout_seen=false` → small amber label `"Phase 1 — waiting for sweep above peak"`
- `fakeout_seen=true`  → small amber label `"Phase 2 — watching for entry below peak"`

**Card border/background:** same `.signal-short` class when signal is `PENDING_SHORT`, plain when `HOLD`.

**renderPairGrid dispatch:** add `case "S6": return s6CardHTML(sym, ps)` alongside existing strategy cases.

---

## Candle Snapshots

S6 reuses the existing `snapshot.py` module (same as S1–S5). Snapshots are saved at three lifecycle events using the daily candle data:

| Event | Trigger | Data saved |
|-------|---------|------------|
| `"open"` | Inside `_execute_s6()` after the market order fills | `daily_df` last 60 candles, event_price = fill price |
| `"partial"` | When 50% TP hits (same partial-close detection loop used by S1–S5 in bot.py) | `daily_df` last 60 candles, event_price = partial exit price |
| `"close"` | When position fully closed | `daily_df` last 60 candles, event_price = exit price |

**Implementation note:** `daily_df` must be kept in the candidate dict so it is available at execution time. At partial/close time, bot.py refetches the daily candles (same pattern as S4/S5 close detection).

**Entry chart** (`/api/entry-chart`) and **trade chart** (`/api/trade-chart`) endpoints in `dashboard.py` already handle any strategy generically via `load_snapshot(trade_id, "open")` and `list_snapshots(trade_id)` — no dashboard.py changes needed for snapshot serving.

---

## Verification

```bash
# Confirm evaluate_s6 is importable
python -c "from strategy import evaluate_s6; print('OK')"

# Confirm config_s6 loads cleanly
python -c "import config_s6; print(config_s6.S6_ENABLED)"

# Confirm both bots still import (S6 is Bitget-only)
python -c "import bot; print('Bitget OK')"
python -c "import ig_bot; print('IG OK')"

# Run full test suite
pytest tests/ -x -q
```
