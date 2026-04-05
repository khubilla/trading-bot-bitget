# Full Code Review — Bitget MTF Bot

**Date:** 2026-04-05
**Scope:** strategy.py, bot.py, trader.py, paper_trader.py, config_s1–s6.py
**Compared against:** docs/strategies/S1–S6.md, GENERAL_CONCEPTS.md

---

## CRITICAL BUGS

### 1. S5 LONG/SHORT: Undefined variables `nearest_res` / `nearest_sup` (bot.py:1636, 1699)

**File:** `bot.py` lines 1636, 1699
**Impact:** Will crash with `NameError` when S5 executes via the immediate (non-limit) path.

```python
# bot.py:1636 — S5 LONG immediate execution
trade["snap_sr_clearance_pct"] = round((nearest_res - mark_now) / mark_now * 100, 1) if nearest_res else None

# bot.py:1699 — S5 SHORT immediate execution
trade["snap_sr_clearance_pct"] = round((mark_now - nearest_sup) / mark_now * 100, 1) if nearest_sup else None
```

`nearest_res` and `nearest_sup` are never defined in `_execute_s5()`. Per the S5 docs, S/R clearance is handled internally via R:R. These should be `None`.

---

### 2. Paper trader missing S1 and S6 exit support (paper_trader.py:132-287)

**File:** `paper_trader.py`
**Impact:** S1 and S6 trades in paper mode use default TP/SL instead of strategy-specific exits (partial TP + trailing).

- `open_long()` (line 132) accepts `use_s2_exits` and `use_s5_exits` but NOT `use_s1_exits`
- `open_short()` (line 209) accepts `use_s4_exits` and `use_s5_exits` but NOT `use_s1_exits` or `use_s6_exits`

Live `trader.py` supports all: `use_s1_exits`, `use_s2_exits`, `use_s5_exits` for LONG; `use_s1_exits`, `use_s4_exits`, `use_s5_exits`, `use_s6_exits` for SHORT.

When bot.py calls `paper_trader.open_long(use_s1_exits=True)`, the kwarg is silently ignored via `**kwargs` or causes a TypeError. Either way, S1/S6 paper trades do NOT get the correct exit logic (no partial TP at +10%, no 5%/10% trailing callback).

---

### 3. S3 uses `use_s2_exits=True` instead of its own exit path (bot.py:1491)

**File:** `bot.py` line 1491
**Impact:** S3 entries reuse S2's exit configuration, which is wrong.

```python
trade = tr.open_long(symbol, sl_floor=c["s3_sl"], leverage=config_s3.S3_LEVERAGE,
                     trade_size_pct=config_s3.S3_TRADE_SIZE_PCT, use_s2_exits=True)
```

S2 exits use:
- `S2_TRAILING_TRIGGER_PCT = 0.10` (10% above entry)
- `S2_TRAILING_RANGE_PCT = 10` (10% callback)

S3 docs say the same numbers (`S3_TRAILING_TRIGGER_PCT = 0.10`, `S3_TRAILING_RANGE_PCT = 10`), so **functionally the values happen to match**. However:
- There is no `_place_s3_exits()` function — S3 piggybacks on S2's exit logic
- If S3 exit parameters ever diverge from S2's, S3 would silently use the wrong values
- The `use_s2_exits=True` naming is misleading in an S3 context

**Verdict:** Not actively wrong today because the parameters match, but a design debt / confusion risk.

---

## WRONG / MISLEADING LOGIC

### 4. S4 swing trail uses `S4_ENTRY_BUFFER` as SL buffer (bot.py:759)

**File:** `bot.py` line 759

```python
swing_sl = raw * (1 + config_s4.S4_ENTRY_BUFFER)  # S4_ENTRY_BUFFER = 0.01
```

`S4_ENTRY_BUFFER = 0.01` is the **entry trigger buffer** (1% below prev_low for entry). Using it as the SL buffer for the swing trail is semantically wrong. Compare:
- S2 swing trail (bot.py:732): uses `config_s2.S2_STOP_LOSS_PCT` (0.05) — makes sense
- S1 swing trail: uses `config_s1.S1_SL_BUFFER_PCT` (0.005) — dedicated buffer
- S3 swing trail: uses `config_s3.S3_SL_BUFFER_PCT` (0.002) — dedicated buffer

S4 should have a dedicated `S4_SL_BUFFER_PCT` parameter. The S4.md doc says swing trail uses `S4_ENTRY_BUFFER` (1%), which is very wide compared to S1 (0.5%) / S3 (0.2%). This may be intentional (daily chart = wider buffer) but it's using a parameter named for a completely different purpose.

---

### 5. S6 partial TP trigger is unreachable (config_s6.py:19, S6.md:97-99)

**File:** `config_s6.py` line 19

```python
S6_TRAILING_TRIGGER_PCT = 1.00  # Partial-TP trigger = fill * (1 - 1.00) = 0
```

The partial TP fires at `fill * (1 - 1.00) = 0`. Price cannot reach 0, so the partial TP **never fires**. The S6.md doc acknowledges this:

> "In practice this trigger is essentially never reached as a fixed price target (price cannot go to 0). The intent is that the trailing stop activates first."

But this means the 50% profit_plan order is dead weight on Bitget — it consumes an order slot but will never trigger. The `moving_plan` trailing stop also has the same unreachable activation trigger of entry * 0.0, meaning it **also never activates**.

**Consequence:** S6 trades have NO functional trailing stop and NO partial TP. The only exits are:
- The wide SL at +50% above entry (which at 10x = -500% P/L, essentially no SL)
- Manual bot-side position close detection

This appears to be by design for a "hold until full reversal" approach, but the documented exit structure is misleading — the plan orders exist but do nothing.

---

### 6. S6 SL computation differs between evaluate_s6() and _execute_s6()

**File:** `strategy.py:1535` vs `bot.py:1732`

```python
# strategy.py:1535 (evaluate_s6)
sl_price = peak_level * (1 + S6_SL_PCT)       # SL based on peak_level

# bot.py:1732 (_execute_s6)
sl_price = mark_now * (1 + S6_SL_PCT / S6_LEVERAGE)  # SL based on fill price / leverage
```

- `evaluate_s6()` computes SL as `peak_level * 1.50` (50% above peak)
- `_execute_s6()` computes SL as `mark_now * (1 + 0.50/10)` = `mark_now * 1.05` (5% above fill)

The S6.md doc says: `SL = entry_fill * (1 + S6_SL_PCT / S6_LEVERAGE)` (matches `_execute_s6`). The `evaluate_s6()` SL is only used for display in `pair_states` and never used for actual order placement.

However, the state.json shows `s6_sl` from `evaluate_s6()` = `peak * 1.50`, while the actual trade SL is `fill * 1.05`. **The dashboard displays a completely different SL than what's actually placed.**

---

### 7. S1 consolidation range check uses midpoint instead of high-anchored formula

**File:** `strategy.py:268-269`

```python
range_pct = (box_high - box_low) / mid   # mid = (box_high + box_low) / 2
```

The S1.md doc and GENERAL_CONCEPTS.md say:
> Floor = `effective_high * (1 - CONSOLIDATION_RANGE_PCT)` — effective lows must not fall below this
> Equivalently: `(box_high - box_low) / box_high <= CONSOLIDATION_RANGE_PCT`

The code divides by `mid` (average), not `box_high`. For S1's 0.3% threshold this difference is negligible (~0.15% error), but it's technically inconsistent with the documented formula. S2's consolidation check (strategy.py:526) correctly uses `(eff_h - eff_l) / eff_h`.

---

## INCONSISTENCIES WITH DOCUMENTATION

### 8. S2 doc says `box_low * 0.999` for SL; code uses `box_low * 0.999` OR `sl_floor`

**File:** `trader.py` line 545

The S2 SL in trader.py is:
```python
raw_sl = sl_floor if sl_floor > 0 else box_low * 0.999
sl_trig = max(raw_sl, sl_cap)   # sl_cap = fill * (1 - stop_loss_pct)
```

In bot.py, S2 entries call `tr.open_long(symbol, sl_floor=0, ...)` so `sl_floor=0`, meaning the `box_low * 0.999` path is used. This matches the docs. **Correct but worth noting the fallback path.**

---

### 9. config_s5.py comment says "2% of total portfolio" but value is 0.04 (4%)

**File:** `config_s5.py` line 54

```python
S5_TRADE_SIZE_PCT   = 0.04   # 2% of total portfolio as margin
```

The comment says 2% but the value is 0.04 = 4%. All other strategies also use 0.04 with the comment "4% of total portfolio as margin". The S5 comment is simply wrong.

---

### 10. config_s6.py comment has wrong formula for recovery ratio

**File:** `config_s6.py` line 15

```python
S6_MIN_RECOVERY_RATIO = 0.25   # ...
                                # Formula: (close - spike_low) / (peak - spike_low) >= 0.15
```

The comment says `>= 0.15` but the actual value is `0.25` (25%). The formula in the comment references `0.15` which was likely an older value.

---

## VAGUE / QUESTIONABLE DESIGN

### 11. S2 big_candle_body_top tracks ALL big candles, not most recent

**File:** `strategy.py:486`

```python
big_candle_body_top = max(float(row["close"]), float(row["open"]))
```

This iterates through ALL big candles in the lookback and takes the body top of the **last** one found (chronologically). If there are multiple big candles, the `big_candle_body_top` is from the most recent one — but only because the loop iterates forward in time. The S2.md doc doesn't specify which big candle's body top is used for the floor.

Additionally, `best_body_pct` tracks the largest body across ALL big candles (line 485: `max(best_body_pct, bp)`), but `big_candle_body_top` doesn't correspond to the biggest candle — it corresponds to the last one. These two values can be from different candles.

---

### 12. S5 direction logic allows LONG on NEUTRAL sentiment

**File:** `strategy.py:1231`

```python
go_long  = allowed_direction != "BEARISH"   # BULLISH or NEUTRAL -> look for LONG
go_short = allowed_direction == "BEARISH"
```

GENERAL_CONCEPTS.md (Section 7 — Sentiment Gate) says:
> NEUTRAL: S2 LONG only (S1 paused, S4/S6 inactive)

But S5 allows LONG on NEUTRAL. The S5.md doc says:
> S5 LONG/SHORT (in the sentiment table)

And the code says S5 LONG fires on both BULLISH and NEUTRAL. This seems intentional but contradicts the GENERAL_CONCEPTS.md table which shows NEUTRAL only allowing S2 LONG. **Either the general concepts doc needs updating or S5 NEUTRAL should be restricted.**

---

### 13. S6 fakeout_seen state reset/display logic

**File:** `bot.py` (from S6.md Section 4)

> `s6_fakeout_seen` is reset to `False` each scan cycle in `update_pair_state()`, then patched to `True` by `_process_s6_watchers()` when Phase 1 is observed during the same tick.

This means the dashboard only shows `fakeout_seen = True` during the exact tick when `_process_s6_watchers()` runs. If the dashboard refreshes between scan cycles, it shows `False` even if the watcher is actively in Phase 2. The watcher dict itself tracks `fakeout_seen = True` persistently, but the pair_states display flickers.

---

## CODE QUALITY / DUPLICATION

### 14. Swing trail logic duplicated 5 times (bot.py:621-767)

The swing trail pattern (initialize ref -> check gate -> find swing -> update SL -> re-initialize) is copy-pasted for S5, S1, S3, S2, S4 with only config imports and candle intervals differing. This is ~150 lines of near-identical code that could be a single parameterized function:

```python
def _try_swing_trail(self, sym, ap, config, interval, direction, lookback, buffer_pct):
```

---

### 15. S4/S6 SHORT reuse `_place_s2_exits()` (trader.py)

S4, S6, and S3 all route through `_place_s2_exits()` in trader.py. While functionally correct (same profit_plan + moving_plan pattern), the naming is confusing. A better name would be `_place_partial_trail_exits()`.

---

### 16. strategy.py module docstring is outdated

**File:** `strategy.py` lines 1-27

The module docstring says "Strategy 2 — 30-Day Breakout + 3m Consolidation" with "3m: RSI > 70" and "3m: Tight consolidation". S2 is actually a **daily-only** strategy (no 3m involvement). The docstring appears to be from an early version when S2 used 3m candles.

---

## SUMMARY TABLE

| # | Severity | File | Issue |
|---|----------|------|-------|
| 1 | **CRITICAL** | bot.py:1636,1699 | `nearest_res`/`nearest_sup` undefined — crashes S5 immediate execution |
| 2 | **CRITICAL** | paper_trader.py | Missing S1/S6 exit params — paper mode exits are wrong |
| 3 | **HIGH** | bot.py:1491 | S3 uses `use_s2_exits=True` — works today but fragile |
| 4 | **MEDIUM** | bot.py:759 | S4 swing trail uses entry buffer as SL buffer |
| 5 | **MEDIUM** | config_s6.py | S6 partial TP/trailing triggers are unreachable (price=0) |
| 6 | **MEDIUM** | strategy.py:1535 vs bot.py:1732 | S6 SL differs between evaluate and execute — dashboard shows wrong SL |
| 7 | **LOW** | strategy.py:268 | S1 consolidation divides by mid instead of box_high |
| 8 | **LOW** | - | S2 SL paths match docs (verified) |
| 9 | **LOW** | config_s5.py:54 | Comment says 2% but value is 4% |
| 10 | **LOW** | config_s6.py:15 | Comment formula references 0.15, not 0.25 |
| 11 | **LOW** | strategy.py:486 | S2 big_candle_body_top tracks last, not largest candle |
| 12 | **MEDIUM** | strategy.py:1231 | S5 LONG on NEUTRAL contradicts GENERAL_CONCEPTS.md sentiment table |
| 13 | **LOW** | bot.py | S6 fakeout_seen flickers in dashboard between scans |
| 14 | **LOW** | bot.py:621-767 | Swing trail duplicated 5x — refactor opportunity |
| 15 | **LOW** | trader.py | S4/S6 naming confusion with `_place_s2_exits()` |
| 16 | **LOW** | strategy.py:1-27 | Module docstring references 3m candles for S2 (outdated) |
