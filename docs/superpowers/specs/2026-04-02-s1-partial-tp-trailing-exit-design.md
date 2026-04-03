# S1 Partial TP + Trailing Exit Design

**Date:** 2026-04-02  
**Status:** Approved

---

## Overview

Replace S1's static 10% hard TP with a two-stage exit:
1. Sell 50% at +10% (partial TP via Bitget `profit_plan`)
2. Trail remaining 50% with a 5% Bitget `moving_plan` trailing stop
3. Step the hard SL up/down each tick as new 3m swing pivots form

---

## Change 1 — `config_s1.py`

Add three new parameters:

```python
S1_TRAIL_RANGE_PCT = 5     # moving_plan trail range (%)
S1_USE_SWING_TRAIL = True  # enable pivot-based SL stepping
S1_SWING_LOOKBACK  = 20    # 3m candles to scan for swing pivots
```

Keep `TAKE_PROFIT_PCT = 0.10` — it now drives the partial TP trigger price (`fill * (1 + TAKE_PROFIT_PCT)` for LONG) rather than a hard full-position close.

The existing `S1_SL_BUFFER_PCT = 0.005` is reused for the swing trail buffer.

---

## Change 2 — `trader.py`: `_place_s1_exits()`

New function, mirrors `_place_s2_exits` exactly with S1-specific trail range.

**Signature:**
```python
def _place_s1_exits(
    symbol: str,
    hold_side: str,       # "long" | "short"
    qty_str: str,         # total position qty as string
    sl_trig: float,       # initial hard SL trigger price
    sl_exec: float,       # initial hard SL execute price
    trail_trigger: float, # price at which partial TP and trail are armed (+10% from fill)
    trail_range: float,   # moving_plan callback % (5.0)
) -> bool:
```

**Orders placed (3 total):**
1. `place-pos-tpsl` `loss_plan` — position-level SL on full position (auto-scales with qty)
2. `place-tpsl-order` `profit_plan` — sell `half_qty` at `trail_trigger` (the +10% level)
3. `place-tpsl-order` `moving_plan` — trail `rest_qty` with `rangeRate=trail_range` triggered at `trail_trigger`

Qty splitting uses `_round_qty` (same as `_place_s2_exits`) to respect symbol minimum volume.

Returns `True` if all orders placed; `False` on 3 consecutive failures.

---

## Change 3A — `bot.py`: `_execute_s1()`

Replace the current `tr.open_long` / `tr.open_short` exit approach:

**Current flow:**
- `tr.open_long(..., take_profit_pct=TAKE_PROFIT_PCT)` — hard TP set inside `open_long`

**New flow:**
1. Call `tr.open_long(symbol, sl_floor=sl_long, leverage=lev, trade_size_pct=...)` — **no** `take_profit_pct`; this places only a market order with position SL
2. Read actual fill price from the returned `trade["entry"]`
3. Compute `trail_trigger = fill * 1.10` (LONG) or `fill * 0.90` (SHORT)
4. Compute `sl_trig` / `sl_exec` (unchanged from current pivot-based logic)
5. Call `_place_s1_exits(symbol, hold_side, qty_str, sl_trig, sl_exec, trail_trigger, trail_range=5.0)`
6. Set `trade["tpsl_set"]` from return value of `_place_s1_exits`
7. Store `ap["sl"] = sl_trig` in `active_positions[symbol]` for swing trail comparison

The `active_positions` entry gains `"sl"` key so the swing trail monitor knows the current SL.

---

## Change 3B — `bot.py`: Partial Close Detection

Extend the partial close detection strategy list from:
```python
if not PAPER_MODE and ap.get("strategy") in ("S2", "S3", "S4", "S5"):
```
to:
```python
if not PAPER_MODE and ap.get("strategy") in ("S1", "S2", "S3", "S4", "S5"):
```

This ensures that when the `profit_plan` fires and reduces S1 position qty, the bot logs the partial close to CSV and state.

---

## Change 3C — `bot.py`: S1 Swing Trail Block

Add a new swing trail block after the existing S3 block, following the identical pattern:

```python
# S1 Swing Trail — trail SL to nearest 3m swing low/high
if config_s1.S1_USE_SWING_TRAIL and ap.get("strategy") == "S1":
    try:
        cs_df  = tr.get_candles(sym, config_s1.LTF_INTERVAL, limit=config_s1.S1_SWING_LOOKBACK + 5)
        mark_s1 = tr.get_mark_price(sym)
        if not cs_df.empty and len(cs_df) >= 3:
            if ap["side"] == "LONG":
                raw     = find_swing_low_target(cs_df, mark_s1, lookback=config_s1.S1_SWING_LOOKBACK)
                swing_sl = raw * (1 - config_s1.S1_SL_BUFFER_PCT) if raw else None
                hold_s  = "long"
                # Guard: only step SL up
                if swing_sl is not None and swing_sl <= ap.get("sl", 0):
                    swing_sl = None
            else:
                raw     = find_swing_high_target(cs_df, mark_s1, lookback=config_s1.S1_SWING_LOOKBACK)
                swing_sl = raw * (1 + config_s1.S1_SL_BUFFER_PCT) if raw else None
                hold_s  = "short"
                # Guard: only step SL down
                if swing_sl is not None and swing_sl >= ap.get("sl", float("inf")):
                    swing_sl = None
            if swing_sl is not None and tr.update_position_sl(sym, swing_sl, hold_side=hold_s):
                ap["sl"] = swing_sl
                st.update_open_trade_sl(sym, swing_sl)
                logger.info(f"[S1][{sym}] 📍 Swing trail: SL → {swing_sl:.5f} (3m swing {'low' if ap['side'] == 'LONG' else 'high'})")
    except Exception as e:
        logger.error(f"[S1] Swing trail error [{sym}]: {e}")
```

**Key guard:** SL only moves in the profitable direction (up for LONG, down for SHORT). This prevents a new lower swing low from pulling the SL back down.

---

## State / CSV Updates

All state and CSV updates are handled by the existing pattern — no new mechanisms needed:

| Event | State update | CSV update |
|-------|-------------|------------|
| Entry orders placed | `trade["tpsl_set"]` set from `_place_s1_exits` return | `_log_trade` captures `sl`, `tpsl_set` |
| Partial TP fires | Existing partial close detection (now includes S1) | `_log_trade("S1_PARTIAL", ...)` |
| Pivot SL step | `ap["sl"] = swing_sl` | `st.update_open_trade_sl(sym, swing_sl)` |
| Full close | Existing close detection (unchanged) | `_log_trade("S1_CLOSE", ...)` |

---

## Files Changed

| File | Change |
|------|--------|
| `config_s1.py` | Add `S1_TRAIL_RANGE_PCT`, `S1_USE_SWING_TRAIL`, `S1_SWING_LOOKBACK`; keep `TAKE_PROFIT_PCT` (now partial trigger) |
| `trader.py` | Add `_place_s1_exits()` function |
| `bot.py` | Update `_execute_s1()` to call `_place_s1_exits`; extend partial close detection; add S1 swing trail block |

**Not changed:** `strategy.py`, `ig_bot.py`, `state.py`, `dashboard.py`, CSV columns, `open_long`/`open_short` signatures.

---

## Constraints

- `open_long` / `open_short` signatures are not changed
- `_place_s2_exits` is not touched — S1 gets its own dedicated function
- S1 swing trail fires every tick (same cadence as S3/S5); gated by `S1_USE_SWING_TRAIL` flag
- SL guard ensures the position-level SL never moves against the trade direction
