# Entry Watcher for All Strategies — Design Spec

**Date:** 2026-04-06
**Branch:** feat/entry-watcher-all-strategies

---

## Goal

Route S2, S3, S4, and S6 trade execution through the 4-second entry watcher thread so all strategies benefit from faster price-trigger resolution, signal-based cancellation, and bot-restart survival.

---

## Architecture

### Execution paths (after change)

```
15s tick                              4s entry watcher
─────────────────────────             ──────────────────────────────────────
_execute_best_candidate()             for each pending_signals entry:
  S1 → _execute_s1()  ────────────►    S2 → signal check + trigger + inval → _fire_s2()
  S2 → _queue_s2_pending()             S3 → signal check + trigger + inval → _fire_s3()
  S3 → _queue_s3_pending()             S4 → signal check + trigger + inval → _fire_s4()
  S4 → _queue_s4_pending()             S5 → limit fill poll + OB inval (unchanged)
  S5 → _queue_s5_pending()             S6 → two-phase + sentiment inval → _fire_s6()
  S6 → _queue_s6_pending()   ◄── replaces _queue_s6_watcher() + s6_watchers dict
```

### Removed

- `self.s6_watchers` dict
- `_queue_s6_watcher()`
- `_process_s6_watchers()`
- Call to `_process_s6_watchers()` at `bot.py` line 922
- `_execute_s2`, `_execute_s3`, `_execute_s4` as direct execution entry points (logic moves to `_fire_sX`)

---

## Pending Payload

Lightweight — only JSON-serializable primitives. No DataFrames.

### S2 payload
```python
{
    "strategy":     "S2",
    "side":         "LONG",
    "trigger":      float,   # s2_bh (box top = breakout level)
    "s2_bh":        float,   # same as trigger, used for window check
    "s2_bl":        float,   # box_low = SL floor; used for invalidation
    "priority_rank":  int,
    "priority_score": float,
    # snap fields for CSV log
    "snap_daily_rsi":     float,
    "snap_box_range_pct": float | None,
    "snap_sentiment":     str,
}
```

### S3 payload
```python
{
    "strategy":     "S3",
    "side":         "LONG",
    "trigger":      float,   # s3_trigger
    "s3_sl":        float,   # used for invalidation
    "priority_rank":  int,
    "priority_score": float,
    # snap fields
    "snap_adx":              float | None,
    "snap_entry_trigger":    float,
    "snap_sl":               float,
    "snap_rr":               float | None,
    "snap_sentiment":        str,
}
```

### S4 payload
```python
{
    "strategy":     "S4",
    "side":         "SHORT",
    "trigger":      float,   # s4_trigger
    "s4_sl":        float,   # used for invalidation: cancel if mark > s4_sl
    "prev_low":     float,   # prev_low_approx; used for lower bound check
    "priority_rank":  int,
    "priority_score": float,
    # snap fields
    "snap_rsi":             float,
    "snap_rsi_peak":        float,
    "snap_spike_body_pct":  float,
    "snap_rsi_div":         bool,
    "snap_rsi_div_str":     str,
    "snap_sentiment":       str,
}
```

### S6 payload
```python
{
    "strategy":       "S6",
    "side":           "SHORT",
    "peak_level":     float,
    "sl":             float,   # s6_sl from candidate
    "drop_pct":       float,
    "rsi_at_peak":    float,
    "fakeout_seen":   bool,
    "detected_at":    float,   # time.time() at queue
    # snap fields
    "snap_s6_peak":       float,
    "snap_s6_drop_pct":   float,
    "snap_s6_rsi_at_peak": float,
    "snap_sentiment":     str | None,
}
```

---

## Watcher Logic Per Strategy

### S2
- **Signal check:** `pair_states[symbol]["s2_signal"] in ("LONG",)` → still valid; else cancel
- **Trigger:** `s2_bh <= mark <= s2_bh * (1 + S2_MAX_ENTRY_BUFFER)` → fire
- **Invalidation:** `mark < s2_bl` → cancel

### S3
- **Signal check:** `pair_states[symbol]["s3_signal"] in ("LONG",)` → still valid; else cancel
- **Trigger:** `s3_trigger <= mark <= s3_trigger * (1 + S3_MAX_ENTRY_BUFFER)` → fire
- **Invalidation:** `mark < s3_sl` → cancel

### S4
- **Signal check:** `pair_states[symbol]["s4_signal"] in ("SHORT",)` → still valid; else cancel
- **Trigger:** `mark <= s4_trigger` AND `mark >= prev_low * (1 - S4_MAX_ENTRY_BUFFER)` → fire
- **Invalidation:** `mark > s4_sl` → cancel

### S6
- **Signal check:** `pair_states[symbol]["s6_signal"] in ("PENDING_SHORT",)` → still valid; else cancel
- **Sentiment check:** if `self.sentiment.direction == "BULLISH"` → cancel (same as today)
- **Phase 1:** `mark > peak_level` → set `fakeout_seen = True`, patch pair_state
- **Phase 2:** `fakeout_seen and mark < peak_level` → fire
- **No expiry** (30-day behaviour from today is replaced by signal-based validity)

---

## Fire Functions (S/R + Claude at fire time)

### _fire_s2(symbol, sig, mark, balance)
1. Read `pair_states[symbol]["s2_sr_resistance_price"]` for S/R check
2. Compute clearance = `(sr_resistance - mark) / mark`
3. If clearance < `S2_MIN_SR_CLEARANCE` → skip (log warning, remove from pending)
4. Claude filter (if enabled)
5. `tr.open_long(...)` with S2 params
6. Log trade, update state, set `active_positions`

### _fire_s3(symbol, sig, mark, balance)
1. Read `pair_states[symbol]["s3_sr_resistance_price"]` for S/R check
2. Same clearance gate as today's `_execute_s3`
3. Claude filter
4. `tr.open_long(...)` with S3 params
5. Log, state, active_positions

### _fire_s4(symbol, sig, mark, balance)
1. Read `pair_states[symbol]["s4_sr_support_pct"]` — if below `S4_MIN_SR_CLEARANCE*100` → skip
2. S4 SL is computed at fire time: `mark_now * (1 + 0.50 / S4_LEVERAGE)`
3. Claude filter
4. `tr.open_short(...)` with S4 params
5. Log, state, active_positions

### _fire_s6(symbol, sig, mark, balance)
1. No S/R check (same as today)
2. SL computed at fire time: `mark * (1 + S6_SL_PCT / S6_LEVERAGE)`
3. `tr.open_short(...)` with S6 params
4. Snapshot uses empty candles (no df in payload) — or skip snapshot
5. Log, state, active_positions

---

## State Persistence

### state.py additions
```python
_default["pending_signals"] = {}   # new top-level field

def save_pending_signals(signals: dict) -> None:
    """Persist pending_signals dict to state.json."""

def load_pending_signals() -> dict:
    """Load pending_signals from state.json. Returns {} if not present."""
```

### state.py reset()
`pending_signals` is preserved across `reset()` — same treatment as `position_memory`.

### bot.py startup
After `st.reset()`, call `self.pending_signals = st.load_pending_signals()` to restore pending entries.

### bot.py on queue/cancel
Every write to `self.pending_signals` calls `st.save_pending_signals(self.pending_signals)`.

---

## _execute_best_candidate changes

For S2/S3/S4 (new):
- If `sig in ("LONG", "SHORT")` and `sym not in self.pending_signals` → `_queue_sX_pending(candidate)`
- If `sym already in self.pending_signals` → skip (don't re-queue; watcher handles it)

For S6 (changed):
- `PENDING_SHORT` now calls `_queue_s6_pending(candidate)` instead of `_queue_s6_watcher(candidate)`

---

## Constraints

- S1 is untouched — direct market order, no queuing
- S5 queue/fill path is untouched
- `_execute_s2/s3/s4` methods are kept as private helpers but are no longer called from `_execute_best_candidate` — their body is extracted into `_fire_s2/s3/s4`
- `_process_s6_watchers()` and `s6_watchers` dict are removed completely
- No time-based expiry for any strategy — signal validity is the only gate
