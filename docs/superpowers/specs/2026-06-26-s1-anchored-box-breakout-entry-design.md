# S1 Anchored-Box Breakout Entry — Design

**Date:** 2026-06-26
**Strategy:** S1 — MTF RSI Breakout
**Scope:** Entry execution only (crypto bots: Bitget / Bybit / Binance). No exit, sizing, or data-contract changes.

---

## 1. Problem

S1 is meant to enter on a **breakout of a consolidation that follows momentum** (the "chart 1" pattern: impulse → tight coil → breakout → buy near the box edge). In practice it does not.

`check_ltf_long` / `check_ltf_short` ([strategies/s1.py:130](../../../strategies/s1.py)) recompute the "box" on **every scan tick** as a **sliding 2-candle window** (`ltf_df.iloc[-4:-2]`) and check whether the last *closed* candle (`iloc[-2]`) closed beyond it by 0.5%. Consequences observed on the live ledger:

- **No stable box.** The "box" is always the last two candles before the last closed candle, so it re-anchors as price moves. There is no single consolidation that price "breaks out of."
- **Entry lands several candles after the visible break.** If the first valid breakout tick doesn't line up (window not tight, RSI dipped, close inside the buffer), the bot waits and fires on a *later* re-anchored pop, often well into the move at an inflated market price.
- **Box-less entries.** A 2-candle window with a 4% tolerance is satisfied by almost any two adjacent 3m candles. Example: `SUSDT` S1 SHORT on 2026-06-25 (`a4a44a65`) — `snap_box_range_pct = 2.387%`, no visible consolidation, shorted into an oversold flush (3m RSI 12.5) and stopped out for −41.79% on margin.

The entry is a **market order placed on the tick after the breakout candle closes** ([bot.py:2089](../../../bot.py)), filling at whatever price the market has run to — not at the box edge.

## 2. Goal

Make S1 enter on the **first** breakout of an **anchored** consolidation box: when a valid coil forms, lock it; then enter on the first candle that **closes** beyond that locked box + 0.5%. This restores the chart-1 behavior and removes the "wrong box / several candles late" problem.

Out of scope (explicitly shelved): the prior-impulse filter, the `detect_consolidation` snap-logging window bug, and any backtest-side replication.

## 3. Design — two-phase in-memory watcher

A per-symbol watcher anchors the box and waits for a close-confirmed breakout. Lives in the `bot.py` scan loop (shared by all three crypto bots). Gated by `S1_ANCHOR_BOX`.

### State

In-memory only on the `Bot` instance — **not** persisted to `state.json` (consistent with S1's existing `swing_trail_ref`, which is also in-memory only). A bot restart clears armed boxes; they re-arm on the next valid coil.

```python
self.s1_armed: dict[str, dict]   # keyed by symbol
# value: {
#   "dir":        "LONG" | "SHORT",
#   "box_high":   float,
#   "box_low":    float,
#   "rsi_thresh": float,    # 70 (LONG) / 30 (SHORT)
#   "armed_at_ts": ts,      # timestamp of the last closed 3m candle at arm time
# }
```

Age is derived from candle timestamps (`armed_at_ts` vs the current last-closed candle), so it advances once **per closed 3m candle**, not per scan tick (there are many ticks per candle).

### Phase 1 — Arm

On a tick where the symbol has **no open S1 position** and **no armed box**, and the existing S1 macro gates pass (1H HTF break, daily trend/ADX, S/R clearance) and 3m RSI is in zone:

- Detect a valid coil on the last closed candles (existing rule: `CONSOLIDATION_CANDLES = 2`, range ≤ `CONSOLIDATION_RANGE_PCT = 4%`, RSI in zone throughout — unchanged; reuse `detect_consolidation` box levels).
- Only arm if price has **not already** broken out (last closed candle has not closed beyond box ± buffer).
- Store the anchored box. This is now "the box"; it stops moving.

### Phase 2 — Fire / disarm

While a box is armed, each tick, in order:

1. **Disarm (no trade)** if any of:
   - 3m RSI has left the zone (≤ 70 LONG / ≥ 30 SHORT) — momentum gone.
   - Last closed candle closed beyond the box the **wrong** way (below `box_low` for a LONG-arm / above `box_high` for a SHORT-arm) — consolidation failed.
   - A macro gate flipped (daily trend or HTF no longer valid for the direction).
   - box age (closed candles since `armed_at_ts`) `> S1_BOX_MAX_AGE` — stale box.
2. **Fire** if the last *closed* 3m candle closed beyond the anchored edge + buffer:
   - LONG: `close > box_high × (1 + 0.005)`
   - SHORT: `close < box_low × (1 − 0.005)`
   → open via `tr.open_long` / `tr.open_short` exactly as today, passing the **anchored** `box_low` / `box_high` for the structural SL. Disarm.

Only one armed box per symbol at a time. After a disarm, a fresh coil can re-arm on the next tick.

## 4. Config (3 crypto copies: `config_s1.py`, `config_bybit_s1.py`, `config_binance_s1.py`)

| Param | Default | Meaning |
|---|---|---|
| `S1_ANCHOR_BOX` | `True` | Master toggle. `False` = current sliding behavior, bit-for-bit. Instant revert. |
| `S1_BOX_MAX_AGE` | `10` | 3m candles an unbroken armed box survives before it expires. |

`CONSOLIDATION_CANDLES`, `CONSOLIDATION_RANGE_PCT`, `BREAKOUT_BUFFER_PCT` are **unchanged**.

## 5. Cross-bot safety & scope

- The watcher lives in the `bot.py` entry loop, which the Bitget (`bot.py`), Bybit (`bybit_bot.py`), and Binance (`binance_bot.py`) entry points all share.
- `evaluate_s1` / `check_ltf_long` / `check_ltf_short` **signatures, return tuples, and logic are unchanged**. When `S1_ANCHOR_BOX` is `True`, the loop ignores `evaluate_s1`'s sliding breakout decision and uses the anchored watcher instead (it still uses `evaluate_s1` / `detect_consolidation` for the coil levels, RSI, ADX, and gate booleans).
- **IG (`ig_bot.py`) and `backtest.py` are untouched** — they call `evaluate_s1` directly and keep the sliding behavior. No `cfg`-path changes; no IG config changes; `_validate_instruments` untouched.

## 6. Data contracts

- **No** change to `trades.csv` columns, `state.json` / `pair_states` fields, or `evaluate_s1`'s return shape.
- Armed-box state is in-memory only — nothing serialized, so no dashboard / optimizer impact.

## 7. Known limitation (accepted)

Because the watcher lives in the live loop and not in `evaluate_s1`, **`backtest.py` will not reflect the anchored entry**. This change is validated **live**, behind `S1_ANCHOR_BOX = True`, with the `False` toggle as the safety net. Backtest validation would require replicating the watcher in `backtest.py` — deliberately out of scope.

## 8. Testing

New unit tests (pytest; never modify existing tests):
- Arm: a valid pre-breakout coil arms a box with correct levels/direction.
- Fire: a subsequent candle closing beyond the anchored edge + buffer triggers entry; entry uses the anchored box levels.
- Disarm — RSI leaves zone, wrong-way close, macro-gate flip, and `S1_BOX_MAX_AGE` expiry each clear the box without trading.
- Toggle off: `S1_ANCHOR_BOX = False` preserves current sliding behavior.

Existing S1 tests must stay green. `qa-trading-bot` runs the suite.

## 9. Docs to update (on implementation)

- `docs/strategies/S1.md` — §1 params table (`S1_ANCHOR_BOX`, `S1_BOX_MAX_AGE`); §2 entry rewritten as two-phase (arm box → close-confirmed breakout); §6 active-trade note.
- `docs/strategies/GENERAL_CONCEPTS.md` — §1 note that S1 anchors the box rather than sliding.
- `docs/DEPENDENCIES.md` — §5.4 / §7 note for the new params and the bot-loop watcher.

## 10. Revert plan

1. **Runtime:** set `S1_ANCHOR_BOX = False` in the live config and restart — entry reverts to today's sliding logic exactly.
2. **Tuning:** adjust `S1_BOX_MAX_AGE` without disabling.
3. **Full:** `git revert` on this feature branch. No state/CSV migration — only future entries are affected; open trades and history are untouched.
