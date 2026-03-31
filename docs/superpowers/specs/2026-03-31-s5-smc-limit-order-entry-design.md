# S5 SMC Limit Order Entry — Design Spec

**Date:** 2026-03-31
**Status:** Approved

---

## Problem

S5 currently enters via market order after a ChoCH candle closes above `ob_high`. By the time conditions are met, price has already moved significantly above the OB zone. The ONT trade (2026-03-30) illustrates this: OB high was 0.07951, entry trigger 0.07991, actual fill 0.08023 — 72 pips above the OB. The OB support zone that justified the trade was far below entry, inflating effective risk from 116 to 188 pips.

The root cause: the "ChoCH" confirmation check is a workaround for market-order execution. It requires a candle close above `ob_high` before firing, guaranteeing a late, above-OB entry.

---

## Solution

Replace market-order-on-breakout with a **GTC limit order placed at `ob_high`** as soon as the OB is touched during pullback. This is the standard SMC OB entry: the limit fills precisely when price returns to the OB boundary after dipping into it. The fill mechanics provide equivalent confirmation to ChoCH — price must dip below `ob_high` and bounce back to fill the limit — at a materially better price.

The 1H BOS check (HTF structural confirmation) remains. The ChoCH candle-close check is removed, replaced by the limit order mechanics.

Applied to both Bitget bot and IG bot.

---

## Entry Logic Changes (strategy.py)

### Before
```
OB touched → ChoCH (last candle close > ob_high) → entry_trigger = ob_high * 1.005
→ if price already above trigger: LONG (market)
→ if price below trigger: PENDING_LONG (watcher fires market when crossed)
```

### After
```
OB touched → entry_trigger = ob_high (no buffer)
→ stale guard: if current_close > ob_high * (1 + S5_MAX_ENTRY_BUFFER): HOLD
→ always: PENDING_LONG (limit order placed at ob_high)
```

### Changes to evaluate_s5()

1. **Remove** ChoCH block (lines 1205–1211 for LONG, equivalent for SHORT)
2. **Change** `entry_trigger = ob_high * (1 + S5_ENTRY_BUFFER_PCT)` → `entry_trigger = ob_high`
3. **Add** stale guard before PENDING return:
   ```python
   if current_close > ob_high * (1 + S5_MAX_ENTRY_BUFFER):
       return "HOLD", ..., "OB stale — price too far above ob_high"
   ```
4. **Remove** the `if current_close <= entry_trigger / else LONG` branch — always return `PENDING_LONG`
5. Return signature unchanged: `(signal, entry_trigger, sl_price, tp_price, ob_low, ob_high, reason)`

`sl_price = ob_low * (1 - S5_SL_BUFFER_PCT)` — unchanged.

---

## Config Changes

### config_s5.py

| Change | Detail |
|---|---|
| Remove `S5_ENTRY_BUFFER_PCT` | No longer used — entry is at `ob_high` exactly |
| Add `S5_OB_INVALIDATION_BUFFER_PCT = 0.001` | Tolerance before cancelling limit on OB breach; avoids cancellation on minor wicks below `ob_low` |
| Keep `S5_MAX_ENTRY_BUFFER = 0.04` | Repurposed: stale OB guard in evaluate_s5() (was watcher guard; now strategy guard) |
| Keep `S5_CHOCH_LOOKBACK` | Renamed semantically to OB touch lookback window — variable name unchanged to avoid breaking ig_bot patching |

### config_ig_s5.py
Mirror removal of `S5_ENTRY_BUFFER_PCT` and addition of `S5_OB_INVALIDATION_BUFFER_PCT`.

---

## Bitget — New Limit Order Functions (trader.py)

```python
def place_limit_long(symbol: str, limit_price: float, sl_price: float,
                     tp_price: float) -> str:
    """Place GTC limit buy. Returns order_id."""

def place_limit_short(symbol: str, limit_price: float, sl_price: float,
                      tp_price: float) -> str:
    """Place GTC limit sell. Returns order_id."""

def cancel_order(symbol: str, order_id: str) -> None:
    """Cancel an open limit order."""

def get_order_fill(symbol: str, order_id: str) -> dict:
    """
    Returns {"status": "live"|"filled"|"cancelled", "fill_price": float}
    """
```

Bitget API endpoints:
- Place: `POST /api/v2/mix/order/place-order` with `orderType: "limit"`, `timeInForceValue: "gtc"`
- Cancel: `POST /api/v2/mix/order/cancel-order`
- Status: `GET /api/v2/mix/order/detail`

**SL at placement time:** Bitget supports `presetStopLossPrice` on limit orders. Attach the SL (`sl_price`) at placement so the position is protected the moment the limit fills — no unprotected window between fill and watcher detection (up to 4s). Structural TP (`presetTakeProfitPrice`) is also attached as a safety net.

**After fill detected:** Replace the preset SL/TP with the full `_place_s5_exits()` setup (partial TP at 1:1 + trailing candle stop), matching the current post-open flow.

---

## Bitget — Bot Changes (bot.py)

### _queue_s5_pending()
After storing signal in `self.pending_signals`, immediately place the limit order:
```python
order_id = tr.place_limit_long(symbol, trigger, sig["sl"], sig["tp"])
self.pending_signals[symbol]["order_id"] = order_id
```

### _entry_watcher_loop()
Replace price-trigger check with order status polling:

```
For each pending signal:
  1. poll tr.get_order_fill(symbol, order_id)
  2. if filled:
       → _handle_limit_filled(symbol, sig, fill_price, balance)
       → remove from pending_signals
  3. elif mark < sig["ob_low"] * (1 - S5_OB_INVALIDATION_BUFFER_PCT):
       → tr.cancel_order(symbol, order_id)
       → log "OB invalidated"
       → remove from pending_signals
  4. elif expired:
       → tr.cancel_order(symbol, order_id)
       → remove from pending_signals
```

Remove `too_far` check (now caught in evaluate_s5() stale guard).

### New _handle_limit_filled()
```python
def _handle_limit_filled(self, symbol, sig, fill_price, balance):
    # Place S5 exits (SL/TP) at fill_price
    # Add to self.active_positions
    # Log CSV row (action=S5_LONG/S5_SHORT, entry=fill_price)
```

---

## IG — New Limit Order Functions (ig_client.py)

```python
def place_limit_long(epic: str, limit_price: float, sl_price: float,
                     tp_price: float, size: float = None) -> str:
    """Place GTC limit buy working order. Returns deal_id."""

def place_limit_short(epic: str, limit_price: float, sl_price: float,
                      tp_price: float, size: float = None) -> str:
    """Place GTC limit sell working order. Returns deal_id."""

def cancel_working_order(deal_id: str) -> None:
    """Cancel a working order by deal_id."""

def get_working_order_status(deal_id: str) -> dict:
    """
    Returns {"status": "open"|"filled"|"deleted", "fill_price": float | None}
    """
```

IG API endpoints:
- Place: `POST /workingorders/otc` with `type: "LIMIT"`, `timeInForce: "GOOD_TILL_CANCELLED"`
  - Include `stopLevel` and `limitLevel` at placement time (IG supports attached SL/TP on working orders)
- Cancel: `DELETE /workingorders/otc/{dealId}`
- Status: `GET /workingorders` filtered by deal_id, OR monitor via position sync after fill

**SL at placement time:** IG working orders support `stopLevel` and `limitLevel` at placement. Attach the SL immediately so the position is protected on fill. The `_monitor_position()` loop handles partial TP and trailing candle stop after that, same as today.

---

## IG — Bot Changes (ig_bot.py)

### New state field
```python
self.pending_order: dict | None = None
# Structure: {"deal_id": str, "side": str, "ob_low": float, "ob_high": float,
#              "sl": float, "tp": float, "trigger": float, "expires": float}
```

### _tick() additions

**Step 2b (new) — check pending order:**
```
if self.pending_order:
    status = ig_client.get_working_order_status(deal_id)
    if filled:
        → _handle_pending_filled(fill_price)
        → clear self.pending_order
    elif mark < ob_low * (1 - S5_OB_INVALIDATION_BUFFER_PCT):
        → ig_client.cancel_working_order(deal_id)
        → log "OB invalidated"
        → clear self.pending_order
    return  # don't evaluate for new entries while pending order exists
```

**Step 7 (change) — signal handling:**
```python
# Before: if sig not in ("LONG", "SHORT"): return
# After:
if sig not in ("PENDING_LONG", "PENDING_SHORT"):
    return
if self.pending_order:
    return  # already have a pending order
side = "LONG" if sig == "PENDING_LONG" else "SHORT"
deal_id = ig_client.place_limit_long/short(...)
self.pending_order = {"deal_id": deal_id, "side": side, "ob_low": ob_low, ...}
```

### _session_end_close()
Extend to cancel `self.pending_order` if one exists before closing any open position.

### New _handle_pending_filled()
Uses fill price to set `self.position`, logs CSV row.

---

## OB Invalidation Rule

For both bots:

| Direction | Cancel condition |
|---|---|
| LONG | `mark < ob_low * (1 - S5_OB_INVALIDATION_BUFFER_PCT)` |
| SHORT | `mark > ob_high * (1 + S5_OB_INVALIDATION_BUFFER_PCT)` |

Default `S5_OB_INVALIDATION_BUFFER_PCT = 0.001` (0.1% below ob_low for LONG).

---

## Unchanged

- `evaluate_s5()` return tuple: `(signal, entry_trigger, sl_price, tp_price, ob_low, ob_high, reason)` — 7 elements, same order
- `config_s5` import location (inside function body — required for IG patching mechanism)
- CSV columns — no additions or removals
- SL/TP exit logic after entry (`_place_s5_exits()`)
- Daily EMA bias check
- 1H BOS check
- OB detection (`find_bullish_ob` / `find_bearish_ob`)
- OB touch detection
- R:R minimum check

---

## ONT Trade — Before vs After

| | Before | After |
|---|---|---|
| Entry | 0.08023 (market, above OB) | 0.07951 (limit at ob_high) |
| SL | 0.07835 | 0.07835 |
| Risk | 188 pips | 116 pips |
| RR | 6.21 (inflated by poor entry) | ~10 (honest RR) |
| Result | -13.59% loss | Limit at ob_high — price returned to 0.07914, limit may not have filled (ob_high was 0.07951) — or would have filled and SL managed from a better basis |

---

## Files Changed

| File | Type | What changes |
|---|---|---|
| `strategy.py` | Shared | Remove ChoCH, entry_trigger=ob_high, stale guard, always PENDING |
| `config_s5.py` | Shared | Remove S5_ENTRY_BUFFER_PCT, add S5_OB_INVALIDATION_BUFFER_PCT |
| `config_ig_s5.py` | IG-specific | Mirror config changes |
| `trader.py` | Bitget | Add place_limit_long/short, cancel_order, get_order_fill |
| `bot.py` | Bitget | Queue places limit, watcher polls fill+invalidation, _handle_limit_filled |
| `ig_client.py` | IG | Add place_limit_long/short, cancel_working_order, get_working_order_status |
| `ig_bot.py` | IG | pending_order state, PENDING handling in _tick, fill/invalidation/session-end |
