# S7 — Post-Pump 1H Darvas Breakdown Short

**Status:** Design — awaiting user review before plan-writing.
**Author:** Brainstormed with Claude, 2026-04-27.
**Companion strategy:** S4 (post-pump RSI divergence short).

---

## 1. Purpose

Add a new SHORT strategy that reuses S4's daily setup (post-pump exhaustion) but waits for a tighter intraday confirmation before firing: a **1H Darvas-box stair-step** breakdown formed within the current UTC day. Where S4 fires on any breach of the previous day's low, S7 requires the day to first build a **top box** (post-pump consolidation) and then a lower **low box** (distribution at a lower level), and only fires on a confirmed **close** below the low box.

This is the same kind of post-pump opportunity as S4, but with a structurally cleaner entry — the breakdown is below visible 1H support, not just yesterday's wick.

---

## 2. Scope and Non-Scope

**In scope:**

- New strategy module `strategies/s7.py` with the same shape as `strategies/s4.py`.
- New config module `config_s7.py`.
- Daily-level setup gates identical to S4 (spike body %, RSI peak, RSI still hot, optional RSI divergence).
- 1H Darvas-box detection: classic walking algorithm (see §5) over today's completed 1H candles since UTC midnight.
- Entry watcher / pending queue with confirmed-close trigger and 24h expiry.
- Risk machinery mirroring S4 (atomic SL, partial TP, exchange trailing, daily swing trail post-partial, scale-in 1h after fill with retest gate at the box low).
- Bot-loop registration (`bot.py` scan loop, `_fire_s7`, scale-in branch, recovery, paper trail).
- Dashboard, optimizer (`STRATEGY_GRIDS` + `STRATEGY_COLUMNS`), and backtest support.
- Tests: unit tests for the Darvas detector, integration test `tests/manual/run_test_s7.py`.
- New strategy doc `docs/strategies/S7.md` and `DEPENDENCIES.md` updates.

**Out of scope:**

- IG bot integration. S4 is Bitget-only; S7 follows the same scope.
- New snapshot types or DNA fingerprint fields (S7 reuses S4's shape).
- Refactoring S4. S7 is purely additive; existing function signatures unchanged.
- IG configs (`config_ig*.py`) — untouched.

---

## 3. Setup gates (mirrors S4)

`evaluate_s7(symbol, daily_df, h1_df) -> (signal, daily_rsi, box_top, box_low, body_pct, rsi_peak, rsi_div, rsi_div_str, reason)`

Returns the same arity as `evaluate_s4`, but `entry_trigger`/`sl_price` are replaced by `box_top`/`box_low` (computed from the 1H series; SL is computed downstream in `_fire_s7` from the spike high).

| Gate | Rule | Knob |
|---|---|---|
| Strategy enabled | `S7_ENABLED` | `config_s7.S7_ENABLED` |
| Min daily candles | `≥ 14 + S7_BIG_CANDLE_LOOKBACK + 2` | mirrors S4 |
| Spike candle | ≥ `S7_BIG_CANDLE_BODY_PCT` body within last `S7_BIG_CANDLE_LOOKBACK` daily candles | default `0.20`, `30` |
| RSI peak | RSI window over last `S7_RSI_PEAK_LOOKBACK` daily candles, peak ≥ `S7_RSI_PEAK_THRESH` | default `10`, `75` |
| RSI still hot | `prev_rsi ≥ S7_RSI_STILL_HOT_THRESH` | default `70` |
| RSI divergence | optional informational note (`first_h → second_h`, drop ≥ `S7_RSI_DIV_MIN_DROP`) | default `5` |
| Sentiment | BEARISH only — gated in `bot.py` scan loop, identical to S4 | — |
| 1H low filter | **NOT USED** — the Darvas breakdown is itself the 1H structural break | (was `S4_LOW_LOOKBACK`) |

**Order of operations inside `evaluate_s7()`:**

1. Run daily gates above. If any fail → return `HOLD` with diagnostic reason.
2. Daily passes → run the 1H Darvas detector (§5). If detector returns no locked box → `HOLD` with reason `"S7 daily ✅ ... | 1H Darvas ❌ <why>"`.
3. Both halves pass → return `SHORT` with `box_top` and `box_low`.

Always assemble a single human-readable `reason` string (S4 convention) for the dashboard `s7_reason` field.

---

## 4. Sentiment, concurrency, pause rule

- **Sentiment gate:** S7 only fires when sentiment is **BEARISH**. Wrapped in the same `bot.py` conditional that gates S4 today.
- **Concurrent trade limit:** S7 trades count toward `config.MAX_CONCURRENT_TRADES` like every other strategy.
- **Pair pause rule:** standard — 3 losses on a symbol within a UTC day pauses it for the rest of the day.

---

## 5. 1H Darvas-box detector (the new piece)

Pure functions inside `strategies/s7.py`. No global state.

### 5.1 Window selection

- Get all completed 1H candles since the most recent UTC midnight, **excluding** the currently-forming hour.
- Helper:

  ```python
  def today_h1_slice(h1_df: pd.DataFrame) -> pd.DataFrame:
      """Closed 1H candles since the most recent UTC midnight (forming hour excluded)."""
      if h1_df.empty:
          return h1_df
      today_utc = pd.Timestamp.utcnow().floor("1D")
      mask = h1_df.index >= today_utc          # h1_df is indexed by candle open ts (UTC)
      slice_ = h1_df[mask].iloc[:-1]           # drop the currently-forming hour
      return slice_
  ```
- Minimum window size: `2 × S7_BOX_CONFIRM_COUNT + 2 = 6` candles for default `confirm = 2`. Below that → `HOLD` with reason `"Not enough 1H candles since UTC midnight (need ≥6)"`.
- Backtest: same logic, but `today_utc = current_backtest_tick.floor("1D")`. Documented in §12 and `docs/strategies/S7.md`.

### 5.2 Walking algorithm

```python
def detect_darvas_box(h1_slice: pd.DataFrame, confirm: int = 2):
    """
    Returns (locked, top_high, low_low, top_idx, low_idx, reason).
    """
    if len(h1_slice) < 2 * confirm + 2:
        return False, 0, 0, -1, -1, "Need >= 6 1H candles since UTC midnight"

    # --- top box pass ---
    top_high, top_idx, conf = float("-inf"), -1, 0
    top_locked = False
    for i, row in enumerate(h1_slice.itertuples()):
        if row.high > top_high:
            top_high, top_idx, conf = row.high, i, 0   # new high → reset
        else:
            conf += 1
            if conf >= confirm:
                top_locked = True; break
    if not top_locked:
        return False, top_high, 0, top_idx, -1, "Top box not yet confirmed (running high still pushing)"

    # --- low box pass over remainder ---
    after = h1_slice.iloc[top_idx + 1:]
    low_low, low_off, conf = float("+inf"), -1, 0
    low_locked = False
    for j, row in enumerate(after.itertuples()):
        if row.low < low_low:
            low_low, low_off, conf = row.low, j, 0     # new low → reset
        else:
            conf += 1
            if conf >= confirm:
                low_locked = True; break
    if not low_locked:
        return False, top_high, low_low, top_idx, -1, "Low box not yet confirmed (running low still falling)"

    if low_low >= top_high:
        return False, top_high, low_low, top_idx, top_idx + 1 + low_off, "Sanity: low_low not below top_high"

    low_idx = top_idx + 1 + low_off
    return True, top_high, low_low, top_idx, low_idx, f"Darvas box ✅ top={top_high} low={low_low}"
```

### 5.3 Worked example

```
hour:  00 01 02 03 04 05 06 07 08 09
high:  98 99 96 95 92 91 88 87 86 85
low:   95 94 93 90 88 87 85 84 84 85
```

Top pass:
- 00: cand=98, conf=0
- 01: 99>98 → cand=99, conf=0 (reset)
- 02: 96≤99, conf=1
- 03: 95≤99, conf=2 → **TOP LOCKED at 99** (idx=1)

Low pass (idx=2 onward):
- 02: cand=93, conf=0
- 03: 90<93 → cand=90, conf=0
- 04: 88<90 → cand=88, conf=0
- 05: 87<88 → cand=87, conf=0
- 06: 85<87 → cand=85, conf=0
- 07: 84<85 → cand=84, conf=0
- 08: 84≥84, conf=1
- 09: 85≥84, conf=2 → **LOW LOCKED at 84** (idx=7)

Result: `top_high = 99`, `low_low = 84`. Top box "candles" = {01, 02, 03}; low box "candles" = {07, 08, 09}; descent candles in between = {04, 05, 06}.

### 5.4 Box expansion via re-evaluation

We do **not** maintain persistent box state. Each scan cycle re-runs the detector on the day's accumulating 1H window. Consequences:

- A 1H wicking below the prior cycle's `low_low` but closing back inside causes the next scan's running min to lock on the new wick low (downward expansion happens for free, the entry trigger slides down).
- A 1H wicking above the prior cycle's `top_high` but closing back inside makes the new candle's high the candidate; it then needs `confirm` more candles before the top re-locks. The structure naturally re-validates.
- Output is **idempotent at the candle close** but updates each scan as new 1H closes arrive.

### 5.5 Configuration knob

- `S7_BOX_CONFIRM_COUNT = 2` — confirmation candles required after the establishing candle. Default 2 means each box has 1 establishing + 2 confirming = 3 candles.

---

## 6. Entry trigger, pending queue, watcher

Lifecycle: scan → daily+Darvas pass → queue pending → watcher waits for confirmed 1H close below box low → fire SHORT.

### 6.1 Candidate collection (`bot.py` scan loop)

After the existing S4 candidate block (currently `bot.py:1435`), add:

```python
if s7_sig == "SHORT" and s7_box_low > 0:
    s7_trigger    = s7_box_low * (1 - config_s7.S7_ENTRY_BUFFER)
    s7_sl         = s7_trigger * (1 + 0.50 / config_s7.S7_LEVERAGE)   # leverage-cap preview; recomputed at fire time
    cands.append({
        "strategy": "S7", "symbol": symbol, "sig": "SHORT",
        "rr": None, "sr_pct": s7_sr_sup_pct,
        "s7_trigger": s7_trigger, "s7_sl": s7_sl,
        "s7_box_top": s7_box_top, "s7_box_low": s7_box_low,
        "s7_rsi": s7_rsi, "s7_rsi_peak": s7_rsi_peak,
        "s7_body_pct": s7_body_pct, "s7_div": s7_div, "s7_div_str": s7_div_str,
        "s7_reason": s7_reason, "daily_df": daily_df,
        "priority_rank": ..., "priority_score": ...,
    })
```

S/R clearance gate (`S7_MIN_SR_CLEARANCE`, default 15%) is applied during candidate ranking the same way as S4 (uses `find_spike_base` for the support-turned-resistance floor).

### 6.2 Pending queue (`strategies/s7.py:queue_pending`)

```python
bot.pending_signals[symbol] = {
    "strategy":             "S7",
    "side":                 "SHORT",
    "trigger":              s7_trigger,             # box_low × (1 − ENTRY_BUFFER)
    "s7_sl":                s7_sl,
    "box_low":              s7_box_low,             # mutated each tick (Darvas re-eval)
    "box_top":              s7_box_top,             # snapshot for reference
    "snap_rsi":             ...,
    "snap_rsi_peak":        ...,
    "snap_spike_body_pct":  ...,
    "snap_rsi_div":         ...,
    "snap_rsi_div_str":     ...,
    "snap_box_top":         s7_box_top,
    "snap_box_low_initial": s7_box_low,
    "snap_sentiment":       "BEARISH",
    "priority_rank":        ...,
    "priority_score":       ...,
    "expires":              now + 86400,
}
st.save_pending_signals(bot.pending_signals)
```

### 6.3 Entry watcher (`strategies/s7.py:handle_pending_tick`)

```python
def handle_pending_tick(bot, symbol, sig, balance, paper_mode=None):
    ps = st.get_pair_state(symbol)
    if ps.get("s7_signal") not in ("SHORT",):
        cancel("Signal gone — daily setup invalidated"); return None

    h1_df = tr.get_candles(symbol, "1H", limit=48)   # 2 days of 1H candles — enough to slice "since UTC midnight"
    today_slice = today_h1_slice(h1_df)              # see §5.1
    locked, box_top, box_low, *_ = detect_darvas_box(today_slice,
                                                    confirm=config_s7.S7_BOX_CONFIRM_COUNT)
    if not locked:
        return None  # still pending; do not cancel

    # box expansion → slide trigger
    if box_low != sig["box_low"]:
        sig["box_low"] = box_low
        sig["trigger"] = box_low * (1 - config_s7.S7_ENTRY_BUFFER)
        st.save_pending_signals(bot.pending_signals)

    mark = tr.get_mark_price(symbol)
    if mark > sig["s7_sl"]:
        cancel("Invalidated — mark > SL"); return None

    # confirmed-close trigger (latest CLOSED 1H, not forming hour)
    last_closed = h1_df.iloc[-2]
    if last_closed["close"] < box_low:
        in_window = (mark <= sig["trigger"] and
                     mark >= box_low * (1 - config_s7.S7_MAX_ENTRY_BUFFER))
        if in_window:
            with bot._trade_lock:
                if symbol in bot.active_positions:
                    cancel(); return None
                if len(bot.active_positions) >= config.MAX_CONCURRENT_TRADES:
                    return "break"
                if st.is_pair_paused(symbol):
                    return None
                bot._fire_s7(symbol, sig, mark, balance)
            cancel()
        # else: stale entry, skip this tick (but stay pending for box expansion)
    return None
```

**Three guard rails:**

1. **Confirmed close, not wick.** Trigger is `last_closed["close"] < box_low`, never `mark < box_low`. Wick-and-reclaim is *expansion*, not entry.
2. **Stale-entry guard.** If mark already moved more than `S7_MAX_ENTRY_BUFFER` (default 4%) below the trigger, skip — don't chase a missed move.
3. **SL invalidation.** If mark rallies above `s7_sl` while pending, cancel — setup is dead.

### 6.4 Trade dispatch (`bot._fire_s7`)

Mirrors `bot._fire_s4`. Atomic SL via Bitget `presetStopLossPrice` (commit `7e8207a`):

```python
s7_sl_actual = mark * (1 + 0.50 / config_s7.S7_LEVERAGE)   # leverage cap
trade = tr.open_short(
    symbol,
    sl_floor=s7_sl_actual,          # → presetStopLossPrice (atomic with market)
    leverage=config_s7.S7_LEVERAGE,
    trade_size_pct=config_s7.S7_TRADE_SIZE_PCT * 0.5,   # initial 50% size
    strategy="S7",
)
# attach trade_id, snap_*, dna fields, then add_open_trade(...)
# ... follow-up: place partial + trailing via strategies.s7.compute_and_place_short_exits
```

The market entry and SL go in the same API call; the partial-TP `profit_plan` and trailing `moving_plan` are placed in a separate retry-up-to-3× follow-up. If exits fail, the position still has its bound SL (no naked-short window).

**Active-position record:** mirrors S4's shape, with two parts:

```python
self.active_positions[symbol] = {
    "side": "SHORT", "strategy": "S7",
    # Bot-level standardized fields used by strategy-agnostic exit code
    # (note: the existing S4 record uses `box_high`/`box_low` for SL/trigger —
    # these names are re-used by the bot regardless of strategy)
    "box_high": sig["s7_sl"],         # standardized "exit ceiling"
    "box_low":  sig["trigger"],       # standardized "entry floor"
    # Scale-in orchestration
    "scale_in_pending": True,
    "scale_in_after":   time.time() + 3600,
    "scale_in_trade_size_pct": config_s7.S7_TRADE_SIZE_PCT,
    # S7-specific fields read by the swing-trail and scale-in helpers
    "s7_box_low":  sig["box_low"],    # actual Darvas box low (for scale-in retest window)
    "s7_box_top":  sig["box_top"],    # informational
    "trade_id":    trade["trade_id"],
}
```

`s7_box_low` is the field that `strategies.s7.is_scale_in_window()` and `strategies.s7.maybe_trail_sl()` read.

### 6.5 Pair-state fields (`state.py:_default_pair_state`)

Add (initialized empty/null, S4 default style):

`s7_signal`, `s7_reason`, `s7_box_top`, `s7_box_low`, `s7_rsi`, `s7_rsi_peak`, `s7_body_pct`, `s7_div`, `s7_div_str`, `s7_sr_support_pct`.

---

## 7. Risk management — SL, exits, scale-in, trail

### 7.1 SL

Identical to S4: **leverage-cap SL**, two-stage:

- **At scan time** (`evaluate_s7` / candidate collection): SL preview = `s7_trigger × (1 + 0.50 / S7_LEVERAGE)`. Anchored to the expected entry trigger so the watcher's "mark > SL" invalidation guard has something concrete to compare against.
- **At fire time** (`_fire_s7`): SL is recomputed from the actual mark price → `s7_sl_actual = mark × (1 + 0.50 / S7_LEVERAGE)`. This is the value passed to Bitget as `presetStopLossPrice`, atomic with the SHORT market entry.

Both stages cap the loss at −50% margin at leverage 10× (i.e., a 5% adverse price move). There is **no** spike-anchored SL — the s4.py docstring's mention of `spike_high × (1 + S4_SL_BUFFER)` is stale; the actual S4 code uses leverage-cap, and S7 follows suit.

### 7.2 Partial TP + exchange trailing

Identical to S4: 50% close at `entry × (1 − S7_TRAILING_TRIGGER_PCT)` (default −10%), 10% callback `moving_plan` on the remaining 50%. Placed via the existing `strategies.s4._place_partial_trail_exits()` helper (strategy-agnostic).

### 7.3 Bot-side daily swing trail (post-partial)

Identical mechanic to S4: after partial fires, scan the daily candles for swing highs above the entry and step the SL down to the most recent. Implemented as `strategies.s7.maybe_trail_sl()` reading `config_s7.S7_USE_SWING_TRAIL` / `S7_SWING_LOOKBACK` (~30 lines, mirrors S4).

### 7.4 Scale-in (enabled per Q7 decision)

After initial 50%-size fill, queue scale-in for ~1 hour later. Reuses the bot's existing scale-in orchestration. Two gates per `GENERAL_CONCEPTS §11`:

| Gate | S7 rule |
|---|---|
| Sentiment | must still be **BEARISH** |
| Price window | `box_low × (1 − S7_MAX_ENTRY_BUFFER) ≤ mark ≤ box_low × (1 − S7_ENTRY_BUFFER)` (retest of the breakdown level) |

Implementation in `strategies/s7.py`:

```python
def scale_in_specs() -> dict:
    return {"direction": "BEARISH", "hold_side": "short", "leverage": config_s7.S7_LEVERAGE}

def is_scale_in_window(ap: dict, mark_now: float) -> bool:
    bl = ap["s7_box_low"]
    return (bl * (1 - config_s7.S7_MAX_ENTRY_BUFFER)
            <= mark_now
            <= bl * (1 - config_s7.S7_ENTRY_BUFFER))

def recompute_scale_in_sl_trigger(ap: dict, new_avg: float) -> tuple[float, float]:
    new_sl   = new_avg * (1 + 0.50 / config_s7.S7_LEVERAGE)
    new_trig = new_avg * (1 - config_s7.S7_TRAILING_TRIGGER_PCT)
    return new_sl, new_trig
```

`bot.py:911–926` scale-in branch updated from `_strat in ("S2", "S4")` to `("S2", "S4", "S7")`.

### 7.5 Paper trail (`paper_trader.py:227`)

Extend `if strategy in ("S4", "S5")` to `("S4", "S5", "S7")` and add the S7 branch alongside S4 for the SHORT trail setup:

```python
def compute_paper_trail_short(mark, sl_price, tp_price_abs=0, take_profit_pct=0.05):
    trail_trigger = mark * (1 - S7_TRAILING_TRIGGER_PCT)
    return True, trail_trigger, S7_TRAILING_RANGE_PCT, trail_trigger, False
```

### 7.6 DNA fingerprint

Reuse S4 shape: daily EMA slope, daily price-vs-EMA, daily RSI bucket, optional H1 EMA slope/price-vs-EMA. Implemented as `strategies.s7.dna_fields(candles)` mirroring `strategies.s4.dna_fields`.

---

## 8. Bot integration — touchpoint inventory

| Location | S4 today | S7 addition |
|---|---|---|
| `bot.py:19` | `import config_s4` | + `import config_s7` |
| `bot.py:27` | `from strategies.s4 import evaluate_s4` | + `from strategies.s7 import evaluate_s7` |
| `bot.py:284` init log | `"... S1 + ... + S6"` | append `+ S7` |
| `bot.py:355` strategy whitelist tuple | `("S1", ..., "S6")` | + `"S7"` |
| `bot.py:869` PAPER_MODE strategy whitelist | same tuple | + `"S7"` |
| `bot.py:911–926` scale-in branch | `_strat in ("S2", "S4")` | → `("S2", "S4", "S7")` plus `from strategies.s7 import maybe_trail_sl` |
| `bot.py:1293` evaluation block | calls `evaluate_s4` after BEARISH check | parallel S7 block under same BEARISH gate |
| `bot.py:1335` final signal collapse | chain through S6/S5 | include `s7_sig` between S4 and S5 |
| `bot.py:1347–1378` pair_state assemble | `s4_*` fields | + `s7_reason`, `s7_signal`, `s7_box_top`, `s7_box_low`, `s7_sr_support_pct`, etc. |
| `bot.py:1366` strategy collapse | similar chain | include `"S7"` between S4 and S5 |
| `bot.py:1435` candidate collection | S4 candidate block | parallel S7 block (§6.1) |
| `bot.py:1540` min-balance dispatch | `elif strategy == "S4":` | + `elif strategy == "S7":` using S7 size + leverage |
| `bot.py:_fire_s4` | new sibling `_fire_s7` (§6.4) |
| `bot.py:pending_watcher` dispatch | switches on `sig["strategy"]` | add `"S7"` case → `strategies.s7.handle_pending_tick` |

`recover.py`, `startup_recovery.py`, `analytics.py`: any tuple iterating `("S1", ..., "S6")` gets `"S7"` appended.

Active-position field for swing trail anchor: `s7_box_low` (mirroring `s4_prev_low`).

---

## 9. Snapshots and recovery

- `snapshot.save_snapshot(..., interval="1D", ...)` for `open` / `partial` / `close` / `scale_in` events. Same as S4 (per `GENERAL_CONCEPTS §12`).
- No new snapshot types or DNA fields.
- Recovery walks open trades and reattaches exits via the existing strategy-dispatch path. S7 entries appear as `strategy="S7"` and route to the S7 helpers automatically.

---

## 10. Dashboard

- `dashboard.py:get_state` passes through the new pair_state fields in the JSON response.
- `dashboard.html` adds an S7 panel (parallel to the S4 panel) showing `s7_signal`, `s7_reason`, `s7_box_top`, `s7_box_low`, `s7_rsi`, `s7_rsi_peak`, `s7_body_pct`, `s7_div_str`, `s7_sr_support_pct`. Active-position panel uses the existing strategy-agnostic display; `box_top` / `box_low` come from `active_positions[symbol]`.
- Pair chart overlays `box_top` / `box_low` using the same horizontal-line render path used for S4's `prev_low`. No new chart types.

---

## 11. Optimizer (`optimize.py`)

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

`STRATEGY_COLUMNS["S7"]` mirrors `STRATEGY_COLUMNS["S4"]` (`"result", "pnl_pct", "exit_reason", ...`).

`S7_LOW_LOOKBACK` is **not** present — replaced by `S7_BOX_CONFIRM_COUNT`.

---

## 12. Backtest (`backtest_engine.py`)

- Add `use_s7_exits: bool = False` parameter to `_attach_exits()` (mirror S4).
- Add `elif use_s7_exits:` branch using `config_s7.S7_TRAILING_TRIGGER_PCT` / `S7_TRAILING_RANGE_PCT`.
- `needs_scale_in = use_s4_exits or use_s6_exits or use_s7_exits`.
- Include `"S7"` in the default `enabled_strategies` set at lines 755 / 1033 / 1100.
- Add `"config_s7"` to the config-modules list at line 773.
- The historical 1H window for the Darvas detector at each backtest tick: `h1_df[h1_df.index.floor('1D') == current_day][:-1]` (today's candles up to but not including the live hour). Documented in `S7.md`.

S7 evaluation in backtest reuses `evaluate_s7()` directly with daily and 1H slices from the historical candle store.

---

## 13. Config defaults (`config_s7.py`)

```python
# ============================================================
#  Strategy 7 Configuration — Post-Pump 1H Darvas Breakdown Short
# ============================================================
S7_ENABLED = True

# ── Big Candle Detection (mirrors S4) ───────────────────── #
S7_BIG_CANDLE_BODY_PCT  = 0.20
S7_BIG_CANDLE_LOOKBACK  = 30

# ── RSI Gates (mirrors S4) ──────────────────────────────── #
S7_RSI_PEAK_THRESH      = 75
S7_RSI_PEAK_LOOKBACK    = 10
S7_RSI_STILL_HOT_THRESH = 70
S7_RSI_DIV_MIN_DROP     = 5

# ── 1H Darvas Box Detection (NEW) ───────────────────────── #
S7_BOX_CONFIRM_COUNT    = 2     # confirmation candles required after the establishing candle
                                # → 1 establishing + 2 confirming = 3 candles per box
                                # → minimum total ≈ 6 candles since UTC midnight

# ── Entry Trigger ───────────────────────────────────────── #
S7_ENTRY_BUFFER     = 0.005     # entry trigger = box_low × (1 − 0.5%)
S7_MAX_ENTRY_BUFFER = 0.04      # skip if mark already > 4% past trigger
                                # (SL is leverage-capped, not spike-anchored — see §7.1)

# ── Risk Management (mirrors S4) ────────────────────────── #
S7_LEVERAGE         = 10
S7_TRADE_SIZE_PCT   = 0.04      # 4% of portfolio as margin

S7_TRAILING_TRIGGER_PCT = 0.10  # partial 50% close at −10%
S7_TRAILING_RANGE_PCT   = 10    # 10% callback on remainder
S7_USE_SWING_TRAIL      = True
S7_SWING_LOOKBACK       = 30    # daily candles for swing-trail anchor

# ── S/R Clearance ───────────────────────────────────────── #
S7_MIN_SR_CLEARANCE = 0.15      # skip SHORT if support floor < 15% below entry
```

**Rationale for divergences from S4:**

- `S7_ENTRY_BUFFER = 0.005` vs `S4_ENTRY_BUFFER = 0.01`: S7's anchor (a confirmed 1H Darvas low) is a sharper structural level than S4's anchor (yesterday's daily low), so less below-anchor depth is needed before treating the breach as real. A wider buffer would over-delay entry and miss faster breakdowns.
- No `S7_LOW_LOOKBACK` (S4 has `S4_LOW_LOOKBACK`): the Darvas breakdown is itself the 1H structural break, so the filter is unnecessary (Q6 decision).
- New `S7_BOX_CONFIRM_COUNT`: drives the Darvas detector.

---

## 14. Tests

| File | What |
|---|---|
| `tests/test_s7_darvas.py` (new) | Unit tests on `detect_darvas_box`: locked top + locked low, top-not-yet-locked (running high pushing), low-not-yet-locked (running low falling), structure too young (<6 candles), `low_low ≥ top_high` sanity rejection, expansion via re-evaluation across two scan cycles. |
| `tests/manual/run_test_s7.py` (new) | End-to-end: construct a fake S7 pending signal with a `box_low` and `s7_sl`, run `_fire_s7` in paper mode, assert the bitget client spy sees the atomic SHORT + presetStopLossPrice plus the follow-up partial / trailing / S7 swing-trail wiring. Mirrors `tests/manual/run_test_s4.py`. |
| `tests/test_bot_entry_watcher_all.py` (existing) | Verify (during plan-writing) whether it iterates strategies; if so, add S7 case. |
| `tests/test_state_pending_signals.py` (existing) | Verify (during plan-writing) whether it tests the queue across strategies; if so, add S7 case. |

---

## 15. Documentation

- New `docs/strategies/S7.md` — strategy doc with overview, config table, gates, entry algorithm (Darvas walking), exits, examples. Mirrors `docs/strategies/S4.md` structure.
- `docs/DEPENDENCIES.md`:
  - §2 Shared Files — note that `paper_trader.py:227` and the `bot.py` scale-in branch now include `"S7"`.
  - §5 Config Dependencies — add `config_s7.py` and dependents.
  - §7 Strategy Implementations — new S7 entry summarising files, knobs, and integration points.

---

## 16. Acceptance criteria

A complete S7 implementation:

1. `evaluate_s7()` returns `SHORT` only when daily gates AND a fully locked 1H Darvas box are present, with `box_low < box_top` and at least 6 closed 1H candles since UTC midnight.
2. The pending watcher fires entry only on a confirmed 1H **close** below `box_low`, never on a wick alone, and only while mark is inside `[box_low × (1 − MAX_ENTRY_BUFFER), trigger]`.
3. Box low expansion (wick-and-reclaim) updates `pending_signals[symbol]["box_low"]` and `["trigger"]` on the next watcher tick.
4. SL is bound atomically with the market entry via `presetStopLossPrice`. Partial TP + trailing follow as a separate retry-up-to-3× call.
5. Scale-in queues 1h after fill, gated by BEARISH sentiment AND mark inside the retest window around `box_low`.
6. Daily swing trail kicks in post-partial via `strategies.s7.maybe_trail_sl()`.
7. Dashboard shows S7 status alongside S4. Optimizer treats S7 as a first-class grid. Backtest recognises `S7` as an enabled strategy with its own exits.
8. `tests/test_s7_darvas.py` and `tests/manual/run_test_s7.py` pass under `pytest`.
9. `python -c "import bot"` and (if applicable) `python -c "import ig_bot"` both succeed — no import-level breakage in either bot.
10. `docs/DEPENDENCIES.md` has S7 entries that match the as-built code.

---

## 17. Open questions / risks (none expected to block plan-writing)

- **Sliding the entry trigger after expansion** could cause a single tick to both detect a wider box and immediately fire because the already-confirmed close still sits below the *new* `box_low`. This is acceptable: the broader box low simply reflects the deeper level the market actually held; firing below that wider level is still a confirmed breakdown. We do not need to enforce a 1-tick "cool-down" after expansion.
- **24h pending expiry** matches S4. If the box hasn't broken down within a day, the setup is stale — let it be re-detected fresh next session if conditions persist.
- **Multiple symbols fighting for the slot:** the `MAX_CONCURRENT_TRADES` cap and the existing candidate-ranking by `R:R × 10 + sr_pct` apply unchanged. S7 candidates rank into the same pool as S4 / S6.
