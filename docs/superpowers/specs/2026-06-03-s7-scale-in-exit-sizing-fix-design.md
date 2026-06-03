# S7 Scale-In Exit-Sizing Fix — Design

**Date:** 2026-06-03
**Status:** Approved (pending spec review)
**Scope:** `bot.py` (shared `_do_scale_in`, all 3 exchange bots), `trader.py` (Bitget `update_position_sl`)

## Problem

After an S7 scale-in, the exit orders do not cover the scaled-in position size.
Observed live on AMDUSDT (LONG, total qty **0.18** @ 535.49):

| Order | planType | live size | correct size |
|---|---|---|---|
| Position SL | `pos_loss` | 0 → full 0.18 ✓ | full |
| Stale SL | `loss_plan` | **0.09** (½) | should not exist |
| Partial TP | `profit_plan` | **0.04** (¼) | 0.09 |
| Trailing | `moving_plan` | **0.04** (¼) | 0.09 |

`round_qty` reproduces the numbers exactly: a total of **0.09** (the pre-scale-in
qty) splits to `half=0.04, rest=0.04`; a total of **0.18** splits to `0.09, 0.09`.
So the exits were sized against the position *before* the scale-in fill was
reflected, and never re-sized afterward.

## Root Causes

### Bug A — exits refreshed against stale (pre-fill) qty, never retried
`bot.py:_do_scale_in` places the scale-in market order, then polls ≤12s for
Bitget's position API to show the larger qty. The `while…else` **falls through on
timeout** and proceeds anyway, calling `update_position_sl` + `refresh_plan_exits`
against the stale pre-scale qty, then sets `scale_in_pending=False`. Once the fill
finally reflects, nothing re-refreshes. Order-placement and exit-refresh are fused
in one function, so the only alternatives the current code has are "refresh stale"
or "retry → double the scale-in."

This path is **shared by Bitget, Bybit, and Binance** (`bybit_bot.py` /
`binance_bot.py` sys.modules-alias `trader`/`bitget` and run the same
`bot.MTFBot().run()`).

### Bug B — orphaned preset `loss_plan` on scale-in (Bitget only)
The initial SL is a size-bound preset on the entry order (`presetStopLossPrice`,
`trader.open_long`) which Bitget stores as a `loss_plan` bound to the entry qty.
Scale-in asserts a **position-level** `pos_loss` via `trader.update_position_sl`,
but nothing cancels the old `loss_plan`. Result: a full-position `pos_loss` AND a
stale half-size `loss_plan` at a slightly different trigger, which would fire first
and close half the position early. Bybit (position-level trading-stop) and Binance
(standalone STOP_MARKET) have no `loss_plan`, so this is Bitget-specific.

## Design

### Fix A — decouple order placement from exit refresh
Split `_do_scale_in` into two phases keyed by state flags on the active-position
dict (`ap`), persisted via `st.update_position_memory`:

1. **Placement phase** (when `scale_in_pending`): place the scale-in market order
   exactly once (unchanged sentiment/window gates). On success, record the
   pre-scale qty as `scale_in_pre_qty`, set `scale_in_refresh_pending = True`, and
   set `scale_in_pending = False`. Save the snapshot. Do **not** touch exits here.

2. **Refresh phase** (when `scale_in_refresh_pending`, checked each tick alongside
   the existing scale-in check): read live position qty. If `qty <=
   scale_in_pre_qty`, the fill hasn't reflected yet — return and retry next tick
   (bounded by a deadline, e.g. `scale_in_refresh_deadline`; on deadline expiry,
   log a loud warning and run the refresh anyway as a last resort so exits are at
   least re-asserted). If `qty > scale_in_pre_qty`, the fill is confirmed: update
   `initial_qty`/margin, recompute SL + trail trigger from the new avg, call
   `update_position_sl` and `refresh_plan_exits(..., sl_price=new_sl)`, then clear
   `scale_in_refresh_pending`.

Because the market order is only placed in phase 1 (gated by `scale_in_pending`,
which is cleared immediately after), retrying phase 2 across ticks can never
re-order. The Bybit atomic-SL path (`sl_price` passed through `refresh_plan_exits`)
is preserved.

### Fix B — cancel stale `loss_plan` when asserting `pos_loss` (Bitget)
In `trader.update_position_sl` (Bitget version only), after the `place-pos-tpsl`
call succeeds, query pending `loss_plan` orders for the given `hold_side` and
cancel them. This covers both the scale-in path and the swing-trail path (both call
`update_position_sl`). Failure to cancel is logged but non-fatal (the `pos_loss`
already protects the full position). No change to `bybit.update_position_sl` /
`binance.update_position_sl` — they create no `loss_plan`.

## State / Data Contract

New transient `ap` / `position_memory` fields (additive, Bitget+Bybit+Binance):
- `scale_in_refresh_pending: bool`
- `scale_in_pre_qty: float`
- `scale_in_refresh_deadline: float` (epoch seconds)

Additive only; dashboard reads are field-specific and ignore unknown keys.

## Out of Scope (flagged for follow-up)
- Float-rounding tick loss: `rest_qty = round_qty(total - half)` can drop one tick
  (e.g. total 0.09 → 0.04+0.04, leaving 0.01 uncovered). Affects all strategies'
  exit splits; lower severity; separate change.

## Live Remediation (separate, after code fix + tests)
One-off script to repair the open AMDUSDT position: cancel the stale `loss_plan`
and resize `profit_plan`/`moving_plan` to 0.09/0.09. Run only with user approval.

## Testing
- Unit: phase-2 refresh waits for `qty > pre_qty` before refreshing; never
  re-places the market order on retry; deadline fallback path.
- Unit: Bitget `update_position_sl` cancels pending `loss_plan` for the hold_side;
  Bybit/Binance versions unaffected.
- Import smoke: `bot`, `bybit_bot` (alias swap), `binance_bot` still import.
- Full `pytest` via qa-trading-bot before commit.

## Docs to update post-implementation
- `S7.md` "After Scale-in" — confirm-before-refresh sequencing + Bitget loss_plan cleanup.
- `GENERAL_CONCEPTS.md` §11 — exits refresh only after fill confirmed.
- `DEPENDENCIES.md` §6.1 — `_do_scale_in` two-phase flags; `update_position_sl` loss_plan cleanup.
