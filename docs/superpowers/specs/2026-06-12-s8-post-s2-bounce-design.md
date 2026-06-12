# S8 — Post-S2 Bounce (Breakout-Retest at Tri-Confluence) — Design

**Date:** 2026-06-12
**Status:** Approved
**Bots:** Bitget, Bybit, Binance (shared `bot.py`). IG untouched.
**Direction:** LONG only. **Timeframe:** 1D only.

## 1. Concept

After an S2-style daily breakout that failed to continue upward, price retraces
to a zone where three supports cluster:

1. The **Darvas coil box top** — the top of the consolidation box that formed
   *before* the S2 breakout (prior resistance turned support).
2. The **daily 20MA**.
3. The **61.8% Fibonacci retracement** of the impulse leg (coil box low → the
   post-breakout swing high).

A **small green daily candle** sitting on/just above that confluence zone arms
a stop-buy above its high. Exits copy S2 (50% partial TP at +10%, 10% trailing
callback on the rest, preset SL).

## 2. Structure detection (Approach B — structural single-pass)

`evaluate_s8(symbol, daily_df)` scans backward over completed daily candles to
find the most recent **breakout day B** within `S8_PHASE_LOOKBACK` (default 15)
days such that, as of day B:

- A 1–5 candle Darvas coil sat immediately before B. Coil math is identical to
  S2: effective top per candle = body top if upper wick > `S8_DARVAS_WICK_PCT`
  (default 5%) of body top, else the wick high; effective low = body bottom;
  effective range ≤ `S8_CONSOL_RANGE_PCT` (default 15%).
- A big momentum candle (body ≥ `S8_BIG_CANDLE_BODY_PCT`, default 20%) exists
  within `S8_BIG_CANDLE_LOOKBACK` (default 30) days before B, and all coil
  closes are at/below its body top.
- Daily RSI(14) on day B > `S8_RSI_THRESH` (default 70).
- Day B's close is above the coil's effective box top.

This re-creates S2's conditions evaluated once at the correct historical
anchor, instead of replaying S2's "now"-oriented evaluator per day.

Outputs: `box_top` (coil effective high), `box_low` (coil effective low),
breakout index B.

## 3. Entry conditions (all on completed daily candles)

1. **Structure found** per §2.
2. **Impulse leg:** `swing_high` = highest high from B to now. Require
   `swing_high > box_top × (1 + S8_MIN_EXTENSION)` (default 5%) — guarantees a
   meaningful leg and encodes "price didn't continue, it pulled back".
3. **Tri-confluence:** `fib618 = swing_high − 0.618 × (swing_high − box_low)`.
   The three levels {`box_top`, `ma20`, `fib618`} must all sit within
   `S8_CONFLUENCE_TOL` (default 2%, measured as (max−min)/max) of each other.
   Zone = [min, max] of the three. `ma20` is the daily 20-period MA;
   `S8_MA_TYPE` = `"SMA"` (default) or `"EMA"` (uses `indicators.calculate_ema`).
4. **Small green candle on the zone:** the last *completed* daily candle has
   close > open, body ≤ `S8_SMALL_BODY_PCT` (default 5% of open), and its low
   satisfies `zone_low ≤ low ≤ zone_high × (1 + S8_PROXIMITY)` (default 1%).
5. **Trigger:** `entry_trigger = green_high × (1 + S8_BREAKOUT_BUFFER)`
   (default 0.5%). Signal is queued as a pending stop-buy (S2-style watcher).
6. **Watcher behaviour** (`handle_pending_tick`):
   - Fire when `entry_trigger ≤ mark ≤ entry_trigger × (1 + S8_MAX_ENTRY_BUFFER)`
     (default 4%).
   - Invalidate when mark < zone_low, or the s8 signal disappears on rescan.
   - Pending expires after 24h (same as S2).

## 4. Sizing & exits

- **Single full-size entry. No scale-in.** `S8_TRADE_SIZE_PCT = 0.04`,
  `S8_LEVERAGE = 10`.
- **SL** (preset on the entry market order):
  `max(green_low × 0.999, fill × (1 − S8_STOP_LOSS_PCT))`, `S8_STOP_LOSS_PCT`
  default 5%. `green_low` is carried in the pending payload as the strategy's
  `box_low` analogue.
- **Partial TP:** 50% `profit_plan` at `fill × (1 + S8_TRAILING_TRIGGER_PCT)`
  (default 10%).
- **Trailing:** `moving_plan` with `S8_TRAILING_RANGE_PCT` (default 10%)
  callback on the remaining 50%, same trigger.
- Exit primitive: import `_place_partial_trail_exits` from `strategies/s2.py`
  (same cross-strategy reuse precedent as S7 → s4).
- **Swing trail:** `S8_USE_SWING_TRAIL = False` default; logic identical to
  S2's `maybe_trail_sl` (daily swing-low step-up after partial fires),
  `S8_SWING_LOOKBACK = 30`.

## 5. Files & integration (mirrors S7's wiring)

| File | Change |
|---|---|
| `strategies/s8.py` | NEW — `evaluate_s8`, `queue_pending`, `handle_pending_tick`, `compute_and_place_long_exits`, `maybe_trail_sl`, `dna_fields`, local SMA/fib helpers |
| `config_s8.py`, `config_bybit_s8.py`, `config_binance_s8.py` | NEW — lockstep param files (alias import requires all three) |
| `bot.py` | import + scan-loop evaluate, `s8_*` pair-state fields, candidate collect, `_queue_pending_s8`, `_fire_s8`, active-trade handling (partial/close/swing-trail), `_DNA_TIMEFRAMES["S8"] = ("daily",)`, strategy tuples that enumerate S1–S7 |
| `trader.py` | S8 branch in `open_long` → `strategies.s8.compute_and_place_long_exits` |
| `trade_dna.py` | `_get_handler` S8 dispatch (daily EMA slope / price-vs-EMA / RSI bucket, same shape as S2) |
| `analytics.py` | add `"S8"` to `STRATEGIES`, add S8 entry in `STRATEGY_SNAP_FIELDS` |
| `dashboard.html` | S8 strategy tab + `STRATEGY_SNAP_COLS` entry; s8 pair-state fields |
| `optimize.py` | S8 snap columns in `STRATEGY_COLUMNS` |
| `docs/strategies/S8.md` | NEW — strategy source-of-truth doc |
| `docs/DEPENDENCIES.md` | update §2.1, §4.1, §4.2, §7, §9 |
| `tests/test_s8.py` | NEW — see §8 |

## 6. state.json fields (`pair_states[symbol]`)

```python
{
  "s8_signal":          str,           # "LONG" | "HOLD"
  "s8_reason":          str,
  "s8_box_top":         float | None,  # coil box top (support #1)
  "s8_ma20":            float | None,  # daily 20MA (support #2)
  "s8_fib618":          float | None,  # 61.8% retrace (support #3)
  "s8_zone_low":        float | None,
  "s8_zone_high":       float | None,
  "s8_trigger":         float | None,  # green_high * (1 + buffer)
  "s8_green_low":       float | None,  # SL anchor
  "s8_daily_rsi":       float | None,  # RSI at breakout day B
}
```

## 7. trades.csv

Actions: `S8_LONG` (entry), `S8_PARTIAL`, `S8_CLOSE`. No `S8_SCALE_IN`.

**No new CSV columns** (amended during implementation): the trades.csv contract
is frozen — `_TRADE_FIELDS` in bot.py is the DictWriter fieldnames list and must
match the live file's header, so adding columns would require a
migrate_trades_csv.py run. Instead S8 reuses generically-named existing columns
(same precedent as S7 reusing S4's): `snap_daily_rsi` = RSI at breakout day B,
`snap_entry_trigger` = stop-buy trigger, `snap_sl` = initial SL,
`snap_box_range_pct` = confluence zone width %, `box_low`/`box_high` = zone
bounds, `snap_sentiment`, plus standard DNA `snap_trend_daily_*` via
`dna_fields`. The three confluence levels are persisted in state.json
(`s8_box_top`/`s8_ma20`/`s8_fib618`) and the reason string.

## 8. Testing

Unit tests (pytest, existing `tests/` patterns, no live API):

- Structure detector: finds breakout day B on a synthetic big-candle → coil →
  breakout series; rejects when no big candle / no coil / RSI ≤ 70 at B /
  breakout older than lookback.
- Confluence: passes when the three levels cluster within tolerance; fails
  when spread exceeds it; fib arithmetic checked exactly.
- Candle gate: green/small/low-on-zone each individually rejected when violated.
- Trigger & watcher: pending fires in window, invalidates below zone_low.
- Exit computation: SL = max(green_low×0.999, fill×0.95); trail trig = fill×1.10;
  verifies delegation to `_place_partial_trail_exits` with mocked trader/bitget.
- Both-bot import check: `python -c "import bot"` with aliases intact.

## 9. Error handling

- `evaluate_s8` returns `("HOLD", …, reason)` on insufficient candles
  (< RSI period + big-candle lookback + phase lookback + coil + 2).
- All watcher/exit paths wrapped in the same try/except patterns as S2/S7.
- `S8_ENABLED = True` master gate; disabled returns HOLD without computation.

## 10. Out of scope

- Scale-in machinery (explicitly excluded).
- IG bot, backtest.py/backtest_ig.py integration.
- SHORT side.
