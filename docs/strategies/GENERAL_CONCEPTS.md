# General Trading Concepts

This file is the authoritative reference for all terminology, rules, and concepts used across S1–S6. When revising any strategy, confirm that the implementation aligns with the definitions here.

---

## 1. Darvas Box / Consolidation / Coil

A **Darvas Box** (also called a **coil** or **consolidation zone**) is a tight price range where a candle's body stays within a narrow band after a prior impulse move.

### Rules

- The box is defined by the **effective high** and **effective low** of the consolidation candles (see Body vs Wick Rule below).
- The box is valid when all candle bodies/wicks stay within a tight band anchored to the top:
  - Floor = `effective_high × (1 − CONSOL_RANGE_PCT)` — lows must not fall below this
  - Ceiling = `effective_high`
  - Equivalently from the bottom: `effective_low × (1 + CONSOL_RANGE_PCT)` is the upper bound
  - If `(effective_high − effective_low) / effective_high > CONSOL_RANGE_PCT`, the window is **not** a valid coil.
- The number of candles in the coil must be within the configured range (e.g., 1–5 candles for S2, 2 candles for S1 on 3m).
- **RSI zone constraint (S1/S2):** All consolidation candles must have RSI above the long threshold (>70) or below the short threshold (<30) throughout the coil. A single candle outside the zone invalidates the setup.
- The box is **not** formed by candles that have already broken out. The current forming candle is excluded from the box calculation.

### Darvas Box Top Rule (S2)

The entry trigger is determined by whether the highest candle in the consolidation has a significant upper wick:

| Wick size relative to body top | Meaning | Entry |
|-------------------------------|---------|-------|
| Wick > 5% of body top | Price was rejected at that wick high — treat it as a false break | Enter above the **body top** (candle close or open, whichever is higher) |
| Wick ≤ 5% of body top | Clean high — wick is a valid breakout level | Enter above the **wick high** |

The trigger is then floored against the most recent big candle's body top, preventing a misleadingly low trigger from a near-zero body coil.

---

## 2. Body vs Wick Rule

**When to use the body, when to use the wick:**

If the wick of a candle is **more than 5% of the body top**, treat the wick as a **rejection spike**. Use the **candle body** (open or close, whichever is the effective boundary) as the operative high or low.

If the wick is **5% or less**, it represents a **clean breakout or test**. Use the **full wick** (high or low) as the operative price.

This rule prevents entering above a candle that merely spiked and reversed, which would put entry inside noise rather than above genuine structure.

**Application:**
- S2 box top: used to select the entry trigger level (see Darvas Box Top Rule above)
- S2 consolidation box range: `_eff_top` uses body top when wick > 5% of body top, else wick high
- The same principle guides manual chart analysis across all strategies

---

## 3. Swing High / Swing Low Rules

A **swing high** is a candle whose high is strictly greater than the highs of **both its immediate left and right neighbours** (a 3-candle window). Formally:

```
candle[i].high > candle[i-1].high  AND  candle[i].high > candle[i+1].high
```

A **swing low** is a candle whose low is strictly less than the lows of both its immediate neighbours:

```
candle[i].low < candle[i-1].low  AND  candle[i].low < candle[i+1].low
```

### Usage by strategy

| Strategy | Swing High / Low used for |
|----------|--------------------------|
| S5 (1H BOS) | Break of Structure: prior swing high/low in last `S5_HTF_BOS_LOOKBACK` completed 1H candles |
| S5 (15m TP) | `find_swing_high_target` / `find_swing_low_target` to locate the next structural TP level |
| S5 (trail) | Reference-high/low gated on 15m: ref = next swing high (LONG) or swing low (SHORT); SL steps only after price breaks ref AND post-ref opposing swing forms |
| S1 (trail) | Reference-high/low gated on 3m: same cycle as S5; bidirectional (LONG + SHORT) |
| S3 (trail) | Reference-high gated on 15m: LONG only; SL steps after price breaks ref swing high AND post-ref swing low forms |
| S2 (trail) | Reference-high gated on daily: LONG only; same cycle as S3; activates post-partial only |
| S4 (trail) | Reference-low gated on daily: SHORT only; mirror of S2; activates post-partial only |
| S4 (entry) | Previous day's low is the entry trigger (not a swing definition but a structural level) |

### Notes

- The **current forming candle** is always excluded from swing detection (use `iloc[-2]` or `iloc[:-1]` slices).
- Swing detection requires a minimum of 3 candles per side (6-candle window for the `pivot_n=3` variant used in S/R detection).
- For SL trailing, the bot only **steps the SL in the favourable direction** — it never moves the SL against the trade.

---

## 4. Support / Resistance (S/R) Rules

S/R levels are detected using **daily candle pivot highs and lows** over a lookback window (default 90 daily candles, `pivot_n=3` requiring candles to be the highest/lowest in a 7-candle window).

### Resistance

- `find_nearest_resistance(daily_df, entry_price)` — returns the **closest swing high above entry price**.
- Used as a **clearance gate**: if resistance is closer than `MIN_SR_CLEARANCE` (e.g., 15% for S1/S2/S3/S4, 10% for S5), the entry is **skipped**.
- S2: resistance search starts above the spike peak (not the coil top), because the coil sits below the spike. The spike itself is not treated as resistance for S2.
- S3: resistance search starts above the recent pre-pullback peak (highest high in last 50 15m candles).
- S4: uses `find_spike_base` (lowest recent bearish spike candle below price) as the support-turned-resistance floor.

### Support

- `find_nearest_support(daily_df, entry_price)` — returns the **closest swing low below entry price**.
- Displayed in the dashboard `sr_support_pct` field for informational purposes.
- Not used as an entry gate for LONG strategies (support below you is a target, not a blocker).

### S/R is computed on daily candles by default

Exceptions: S3 resistance uses 15m candles (300-candle lookback on `m15_df`).

---

## 5. Trailing Stop Rules

The bot uses two distinct trailing mechanisms, sometimes combined:

### 5a. Exchange-Side Trailing Stop (`moving_plan`)

A **Bitget trailing stop order** placed as a `moving_plan` at entry time. This trails automatically on the exchange:

- **Activation trigger**: price must first reach `trail_trigger` (e.g., entry ± 10%) to activate the trailing.
- **Callback range**: once activated, the trailing stop follows at `trail_range_pct`% behind the best price (e.g., 10% callback = stop moves up as price rises, fires if price drops 10% from its high).
- This is set once at entry and not modified by the bot (except after scale-in, where it is cancelled and re-placed via `refresh_plan_exits`).

**Used by:** S1 (5% trail), S2 (10% trail), S3 (10% trail), S4 (10% trail), S5 (5% fallback trail), S6 (10% trail).

### 5b. Bot-Side Swing Trail (per tick)

A **structural trailing stop** that the bot updates each scan cycle by finding the nearest swing high/low and calling `update_position_sl()`:

- Runs after the position is open, on every tick.
- SL is only moved in the **favourable direction** (up for LONG, down for SHORT). Once set, it never moves against the trade.
- All strategies (S1, S2, S3, S4, S5) use a **reference-gated** swing trail. The SL never moves just because price is rising — it only steps after two things happen in sequence:
  1. Price breaks through a structural **reference swing high** (LONG) or **reference swing low** (SHORT)
  2. A new swing low (LONG) or swing high (SHORT) forms **after** that broken reference candle
  - That post-ref swing becomes the new SL (with buffer), and the next structural swing in the direction of travel becomes the new reference
  - This prevents premature SL movement on minor pullbacks and keeps the stop anchored to confirmed structure
- For S1: uses the nearest 3m swing low/high.
- For S2 (post-partial): uses the nearest daily swing low.
- For S4 (post-partial): uses the nearest daily swing high.

**Activation condition:**
- S2/S4: swing trail only starts after the partial TP has fired.
- S5: swing trail runs from trade open (no partial requirement), using `S5_USE_CANDLE_STOPS`.
- S1: swing trail runs from trade open, using `S1_USE_SWING_TRAIL`.
- S3: swing trail runs from trade open, using `S3_USE_SWING_TRAIL`.

### 5c. Partial TP (50% close at 1:1 or fixed level)

Before the trailing stop activates on the remaining position, **50% of the position is closed** at a target:

| Strategy | Partial TP trigger |
|----------|--------------------|
| S1 | entry ± 10% (`TAKE_PROFIT_PCT`) |
| S2 | entry + 10% (`S2_TRAILING_TRIGGER_PCT`) |
| S3 | entry + 10% (`S3_TRAILING_TRIGGER_PCT`) |
| S4 | entry − 10% (`S4_TRAILING_TRIGGER_PCT`) |
| S5 | entry ± 1:1 risk (distance = SL distance; `partial_trig = entry ± risk`) |
| S6 | entry − 100% (effectively never fires in practice; exit is via trailing) |

After partial close, the remaining 50% is managed by the trailing stop (exchange-side or swing trail).

---

## 6. Order of Strategy Priority

When multiple strategies generate signals in the same scan cycle, candidates are ranked by:

1. **R:R × 10** (primary weight)
2. **S/R clearance %** (secondary weight, tiebreaker)

The highest-scoring candidate is executed or queued first. Only one trade per symbol is allowed at a time.

---

## 7. Sentiment Gate

The market sentiment is computed each scan cycle from the ratio of green vs. red daily candles across all qualified pairs:

| Sentiment | Strategies allowed |
|-----------|--------------------|
| BULLISH | S1 LONG, S2 LONG, S3 LONG, S5 LONG/SHORT |
| BEARISH | S1 SHORT, S4 SHORT, S5 SHORT, S6 SHORT |
| NEUTRAL | S2 LONG only (S1 paused, S4/S6 inactive) |

S4 and S6 only fire when sentiment is **not BULLISH**. S2 fires on BULLISH and NEUTRAL.

---

## 8. Entry Buffer and Stale Entry Guards

Every strategy has a **maximum entry buffer** — if price has already moved too far beyond the trigger, the entry is skipped (the move is considered missed):

| Strategy | Guard | Parameter |
|----------|-------|-----------|
| S2 | price > trigger + 4% | `S2_MAX_ENTRY_BUFFER = 0.04` |
| S3 | price > trigger + 1% | `S3_MAX_ENTRY_BUFFER = 0.01` |
| S4 | price < prev_low − 4% | `S4_MAX_ENTRY_BUFFER = 0.04` |
| S5 | price > ob_high + 4% (LONG) / price < ob_low − 4% (SHORT) | `S5_MAX_ENTRY_BUFFER = 0.04` |

S1 does not use an entry buffer — it enters at market when the breakout is detected.

---

## 9. Pair Pause Rule

A symbol is **paused for the rest of the calendar day (UTC)** if it records **3 losses** in a single day. The pause prevents over-trading after consecutive failures on the same pair. The pause lifts at the next UTC midnight.

---

## 10. Concurrent Trade Limit

The bot allows a maximum of `config.MAX_CONCURRENT_TRADES` open positions simultaneously (default: 2). Once the limit is reached, no new entries are opened until a position closes.

---

## 11. Scale-In Rules (S2 and S4 only)

- After an initial entry at 50% of the target trade size, the bot queues a **scale-in** 1 hour later.
- Scale-in executes only if price is within the valid entry window at that time:
  - S2: price is between `box_high` and `box_high * (1 + S2_MAX_ENTRY_BUFFER)`
  - S4: price is between `prev_low * (1 - S4_MAX_ENTRY_BUFFER)` and `prev_low * (1 - S4_ENTRY_BUFFER)`
- After scale-in, the exchange plan exits (`profit_plan` + `moving_plan`) are cancelled and re-placed with the new total qty and updated trail trigger based on the new average entry price.
- S2: SL cap is recomputed from the new average entry and raised if necessary.

---

## 12. Snapshot Convention

At each lifecycle event, the bot saves a candle snapshot to `data/snapshots/{trade_id}_{event}.json`:

| Event | When |
|-------|------|
| `open` | Trade entry executed |
| `scale_in` | Scale-in fill (S2/S4) |
| `partial` | Partial TP detected |
| `close` | Position closed by SL/TP or bot |

The interval used for the snapshot matches the strategy's primary execution timeframe (3m for S1, 1D for S2/S4/S6, 15m for S3/S5).

---

## 13. Signal Values (Canonical)

Always use these exact strings when referring to signals in code and documentation:

| Signal | Meaning |
|--------|---------|
| `"LONG"` | Immediate long entry triggered |
| `"SHORT"` | Immediate short entry triggered |
| `"PENDING_LONG"` | Limit order queued — waiting for price to reach ob_high |
| `"PENDING_SHORT"` | Limit order queued — waiting for price to reach ob_low |
| `"HOLD"` | No action this cycle |

**Never use:** `"BUY"`, `"SELL"`, `"ENTRY"`, `"EXIT"`, `"WAIT"`.

---

## 14. Timeframe Abbreviations

| Code | Meaning |
|------|---------|
| `"3m"` | 3-minute candles (S1 LTF) |
| `"15m"` | 15-minute candles (S3 + S5 LTF) |
| `"1H"` | 1-hour candles (S1 + S5 HTF) |
| `"1D"` | Daily candles (all strategies for trend/context) |
