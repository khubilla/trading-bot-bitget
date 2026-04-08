# Startup Recovery for Crashed-Before-Log Positions

**Date:** 2026-04-09
**Status:** Approved
**Context:** On 2026-04-08 the bot placed 6 S5 limit orders, crashed before the fills were detected, and restarted with those positions registered as strategy=UNKNOWN with no SL/TP, no CSV entries, and no snapshots. This feature prevents that from happening again.

---

## Problem

When the bot stops (crash, SIGTERM, restart) between placing a limit order and detecting its fill, three things go wrong:

1. `_handle_limit_filled()` never runs → no TPSL orders placed on exchange, no CSV row, no snapshot
2. On restart, the startup sync finds the open position but no CSV match → registers it as strategy=UNKNOWN, sl="?", tp="?"
3. On the first tick, the pending_signals loop sees the symbol in `active_positions` and discards the signal — losing the original OB/SL/TP values forever

The same gap exists for any strategy whose market order filled in the window between execution and the bot's `_log_trade()` call, though this window is milliseconds vs hours for limit orders.

---

## Scope

- **In scope:** positions on the exchange that have no CSV open row (any strategy)
- **In scope:** pending S5 signals whose limit orders filled and position subsequently closed while bot was down
- **Out of scope:** positions that already have a CSV open row — those are handled by the existing Pass A / Pass B reconciliation in `__init__`
- **Out of scope:** IG bot (ig_bot.py uses market orders only; no limit order pending mechanism)

---

## Architecture

### New: `Bot._startup_recovery(self, existing: dict, balance: float)`

A method on the `Bot` class called once from `__init__`, immediately after the existing startup sync block (after Pass B). Takes the already-fetched `existing` positions dict. Fetches `balance` internally via `tr.get_usdt_balance()` (one call, reused for all happy-path recoveries in the loop).

Operates in two passes:

**Pass 1 — Open positions with no CSV record:**
```
for sym, pos in existing.items():
    if _get_open_csv_row(TRADE_LOG, sym) is not None:
        continue   # CSV exists — already handled by Pass A/B
    if sym in self.pending_signals:
        _happy_path_recovery(sym)
    else:
        _sad_path_recovery(sym, pos)
```

**Pass 2 — Pending signals whose position opened AND closed while bot was down:**
```
for sym, sig in list(self.pending_signals.items()):
    if sym in self.active_positions:
        continue   # already handled in Pass 1 or normal startup
    order_id = sig.get('order_id')
    if not order_id or order_id == 'PAPER':
        continue   # non-S5 or paper-mode signal — skip
    fill = tr.get_order_fill(sym, order_id)
    if fill['status'] == 'filled':
        _log_open_and_close(sym, sig, fill['fill_price'])
        self.pending_signals.pop(sym)
```

### Happy Path Recovery (pending signal found)

Triggered when: position is open on exchange, no CSV row, `pending_signals[sym]` exists with `order_id`.

Steps:
1. Call `tr.get_order_fill(sym, sig['order_id'])` — confirm fill price
2. If status is `"live"` (order not yet filled): skip — not a crash case, still pending
3. If status is `"filled"`: call `self._handle_limit_filled(sym, sig, fill_price, balance)` — this does everything: places `_place_s5_exits()`, logs CSV, `st.add_open_trade()`, saves snapshot, registers `active_positions[sym]`
4. Remove sym from `self.pending_signals`; call `st.save_pending_signals()`
5. Log: `⚠️ Startup recovery [happy]: {sym} limit filled while bot was down @ {fill_price}`

`_handle_limit_filled()` is called unmodified — no special-casing needed.

### Sad Path Recovery (no pending signal)

Triggered when: position is open on exchange, no CSV row, no pending signal. Any strategy.

Steps:
1. Generate `trade_id = uuid.uuid4().hex[:8]`
2. Determine `opened_at`: from `pos.get('opened_at')` if available, else current UTC
3. Fetch historical 15m candles using Bitget `endTime` param at `opened_at` timestamp (via `bc.get_public` directly — `trader.get_candles()` does not expose `endTime`)
4. Attempt OB/SL/TP recovery:
   - If position side is SHORT and candles available: call `evaluate_s5(sym, daily_df, htf_df, m15_df, allowed_direction="SHORT")`
   - If `evaluate_s5` returns a PENDING/SHORT signal with `sl > 0`: use those values
   - Otherwise: fallback — `sl = entry * 1.05` (SHORT) or `entry * 0.95` (LONG), `tp = entry * 0.90` (SHORT) or `entry * 1.10` (LONG), `ob_high = entry`, `ob_low = entry * 0.99`
5. Strategy is always `"UNKNOWN"` in the sad path — without a pending signal there is no reliable way to identify which strategy opened the position. `evaluate_s5()` is used only for SL/TP estimation, not strategy identification.
6. Patch `active_positions[sym]`: set `strategy="UNKNOWN"`, `sl`, `box_high`, `box_low`, `trade_id`
7. Update state.json open_trades entry: set `strategy`, `trade_id`, `sl`, `tp`, `box_high`, `box_low`, `tpsl_set=False`
8. Append `{STRATEGY}_{SIDE}` row to trades.csv with `tpsl_set=False`
9. Save `{trade_id}_open.json` snapshot with recovered candles
10. Log: `⚠️ Startup recovery [sad]: {sym} — no signal data, tpsl_set=False, manual TPSL needed`

### Open+Close While Down (Pass 2)

When a limit order filled AND the position subsequently closed while the bot was down:
1. Write the open CSV row (as sad path, but with fill_price from order fill)
2. Call `tr.get_history_position(sym, open_time_iso=fill_time)` to get close PnL
3. Write the close CSV row
4. Do NOT call `st.add_open_trade()` — position is already gone
5. Remove from `pending_signals`

---

## New: `recover.py` CLI Script

Standalone script for manual recovery when the bot is already running and positions have already been registered as UNKNOWN.

```
python recover.py [--dry-run] [--symbols SYM1 SYM2 ...]
```

- `--dry-run`: prints what would change, writes nothing to disk
- `--symbols`: limit to specific symbols (default: all UNKNOWN positions in state.json)
- No Bot class instantiation — operates on state.json, trades.csv, snapshots directly
- Mirrors the sad path logic exactly (shared helper functions)
- Prints a summary table on completion

**Shared helpers** (importable by both `bot.py` and `recover.py`):
- `_fetch_candles_at(symbol, interval, limit, end_ms)` — wraps `bc.get_public` with `endTime`
- `_estimate_sl_tp(entry, side)` — fallback SL/TP from entry ± 5%
- `_attempt_s5_recovery(sym, m15_df, htf_df, daily_df, side)` — runs `evaluate_s5`, returns `(sl, tp, ob_low, ob_high)` or None

These helpers live in a new `startup_recovery.py` module to keep `bot.py` from growing further.

---

## Data Contract: No Changes

- trades.csv columns: unchanged (uses existing `_TRADE_FIELDS`)
- state.json fields: unchanged (uses existing open_trades field names)
- snapshot format: unchanged

---

## Error Handling

- All of `_startup_recovery()` is wrapped in a top-level try/except — any unhandled error logs a warning and returns; `__init__` continues normally
- Per-symbol errors: caught individually, logged as WARNING, symbol skipped
- `get_order_fill()` API failure: symbol skipped for this startup, retried next restart
- Empty candle response: snapshot skipped (no blank files written)
- CSV write failure: logged as ERROR, state.json patch still applied

---

## Tests

**`tests/test_startup_recovery.py`:**

| Test | Setup | Assert |
|---|---|---|
| happy_path | pending_signals has sym with order_id; get_order_fill → "filled" | `_handle_limit_filled` called with correct args; sym removed from pending_signals |
| happy_path_still_live | get_order_fill → "live" | `_handle_limit_filled` NOT called; sym stays in pending_signals |
| sad_path_no_signal | no pending signal; candles available; evaluate_s5 → HOLD | CSV row appended; state patched; `tpsl_set=False`; snapshot saved |
| sad_path_no_candles | candles empty | CSV row appended; snapshot skipped; no crash |
| open_and_close_while_down | pending signal; sym NOT in active positions; get_order_fill → "filled" | open + close CSV rows written; NOT in active_positions after |
| error_isolation | get_order_fill raises exception | warning logged; __init__ completes normally |

**`tests/test_recover_cli.py`:**

| Test | Assert |
|---|---|
| dry_run_writes_nothing | trades.csv and state.json unchanged after `--dry-run` |
| symbols_filter | only specified symbols processed |
| summary_table_printed | stdout contains symbol, trade_id, sl, tp |

---

## Integration Point in `__init__`

```python
# After existing Pass B block (~line 454):
if not PAPER_MODE:
    try:
        self._startup_recovery(existing)
    except Exception as e:
        logger.warning(f"Startup recovery failed: {e}")
```

`existing` is the dict already fetched at the top of the startup sync block. `balance` is fetched once inside `_startup_recovery()` and reused across all recovered positions.

---

## File Changes

| File | Change |
|---|---|
| `startup_recovery.py` | New module — shared helpers (`_fetch_candles_at`, `_estimate_sl_tp`, `_attempt_s5_recovery`) |
| `bot.py` | Add `Bot._startup_recovery()` method; call it from `__init__` after Pass B |
| `recover.py` | New CLI script using `startup_recovery.py` helpers |
| `tests/test_startup_recovery.py` | New test file |
| `tests/test_recover_cli.py` | New test file |
