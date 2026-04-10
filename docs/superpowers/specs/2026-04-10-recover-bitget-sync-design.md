# recover.py — Bitget Sync Redesign

**Date:** 2026-04-10
**Status:** Approved

---

## Problem

The current `recover.py` uses `state.json` as its source of truth, targeting only positions
where `strategy == "UNKNOWN"` or `sl` is missing/blank. This misses:

1. Positions that exist on Bitget but are entirely absent from `state.json`
2. Positions with a known strategy (S1–S6) whose SL/TP has gone stale or was never set
3. Positions in `state.json` that no longer exist on Bitget (potential orphans)

## Design Goal

Make `recover.py` a manual reconciliation tool that treats Bitget's live position API as the
source of truth for what positions *exist*, and `state.json` + `trades.csv` as the source of
truth for strategy metadata.

---

## Architecture

### Two-Pass Structure (mirrors `bot.py._startup_recovery`)

**Pass 1 — Exchange → State** (for each symbol returned by `tr.get_all_open_positions()`):

1. Fetch open CSV row for symbol via `_get_open_csv_row()`
2. Fetch state.json entry via `st.get_open_trade()`
3. Classify and act:

| Classification | Condition | Action |
|---|---|---|
| `SKIP` | CSV row exists AND SL/TP are valid floats > 0 | No-op |
| `PATCH_SLTP` | CSV row exists BUT SL or TP is missing/bad | Run S5 OB recovery (if S5) or `estimate_sl_tp()`; patch state.json only — no new CSV row |
| `FULL_RECOVERY` | No CSV open row | Assign new `trade_id`, write CSV open row, patch/add state.json entry, save snapshot |

"Valid SL/TP" = both fields parse as `float > 0` (not `"?"`, `""`, `None`, or `0`).

**Pass 2 — State → Exchange** (for each entry in `st.get_open_trades()`):

- Symbol not in Bitget positions → print warning, no writes

---

## Components

### `_get_open_csv_row(csv_path, symbol) -> dict | None`

Move from `bot.py` (where it is private) into `recover.py`. Returns the most recent CSV row
whose `action` ends with `_LONG` or `_SHORT` for the given symbol. Already exists verbatim —
no logic change.

### `_is_valid_sltp(sl, tp) -> bool`

New helper. Returns `True` iff both `sl` and `tp` parse as `float > 0`.

```python
def _is_valid_sltp(sl, tp) -> bool:
    try:
        return float(sl) > 0 and float(tp) > 0
    except (TypeError, ValueError):
        return False
```

### `_patch_sltp(sym, state_entry, exchange_pos, state_file, csv_path, dry_run) -> dict`

New function for `PATCH_SLTP` case. Derives SL/TP via S5 OB recovery (if strategy is S5)
or `estimate_sl_tp()` (all others). Patches state.json entry only — no CSV write.
Returns summary dict: `{symbol, action, strategy, sl, tp}`.

### `_full_recovery(sym, exchange_pos, state_file, csv_path, dry_run) -> dict`

Rename of current `recover_position()`. Called only for `FULL_RECOVERY` case.
Assigns new `trade_id`, writes CSV open row, patches/adds state.json entry, saves snapshot.
Returns summary dict: `{symbol, action, trade_id, entry, sl, tp, snapshot}`.

### `main(args=None)`

Restructured orchestrator:

1. Call `tr.get_all_open_positions()` — this is the outer loop
2. Apply `--symbols` filter to exchange positions
3. Run Pass 1 classification and dispatch
4. Run Pass 2 warning scan over `st.get_open_trades()`
5. Print structured report

---

## State Writes

- **State reads/writes:** Use `st.get_open_trade()`, `st.add_open_trade()`, `st._read()` +
  `st._write()` — same pattern as `bot.py._startup_recovery()`. No raw `json.loads/write`.
- **`_patch_state()` (current):** Replaced by inline `st._read()` / `st._write()` calls inside
  `_patch_sltp()` and `_full_recovery()`, matching the bot pattern exactly.

---

## CLI Interface

No changes to flags:

```
python recover.py [--dry-run] [--symbols SYM1 SYM2 ...]
```

`--dry-run` suppresses all file writes. `--symbols` filters which Bitget positions are
processed in Pass 1 (Pass 2 always scans full state.json).

---

## Output Format

```
Fetched 3 position(s) from Bitget.

Pass 1 — Exchange → State:
  BTCUSDT    SKIP        (CSV + SL/TP intact)
  ETHUSDT    PATCH_SLTP  sl=2850.00000  tp=2650.00000  (S5 OB recovery)
  SOLUSDT    FULL        trade_id=a1b2c3d4  entry=142.50000  sl=135.00000  tp=158.00000

Pass 2 — State → Exchange:
  XRPUSDT    ⚠ WARNING: in state.json but NOT on Bitget — restart bot to close

Summary: 1 skipped, 1 patched, 1 fully recovered, 1 warning(s).
⚠  tpsl_set=False for recovered/patched positions. Manually set SL/TP on Bitget, or restart the bot.
```

Dry-run prefixes each action line with `[DRY RUN]`.

---

## SL/TP Recovery Strategy

| Strategy | Method |
|---|---|
| S5 | `attempt_s5_recovery()` from `startup_recovery.py`; fall back to `estimate_sl_tp()` |
| S1, S2, S3, S4, S6, UNKNOWN | `estimate_sl_tp()` always |

---

## What Does NOT Change

- `startup_recovery.py` — no changes (`fetch_candles_at`, `estimate_sl_tp`, `attempt_s5_recovery` unchanged)
- `_TRADE_FIELDS` list — no changes
- `_log_trade_to_csv()` — no changes
- `snapshot.save_snapshot()` call pattern — no changes
- `--dry-run` / `--symbols` CLI flags — no changes
- `ig_bot.py`, `ig_state.json` — not affected (Bitget-only tool)

---

## Files Changed

| File | Change |
|---|---|
| `recover.py` | Restructure `main()`, rename `recover_position()` → `_full_recovery()`, add `_patch_sltp()`, add `_get_open_csv_row()`, add `_is_valid_sltp()`, remove `_patch_state()` |
| `docs/DEPENDENCIES.md` | Add `recover.py` entry after implementation |
