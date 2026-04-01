# IG Multi-Instrument Support + S5 Decoupling

**Date:** 2026-04-01  
**Status:** Approved  
**Scope:** ig_bot.py, config_ig*.py, strategy.py (evaluate_s5), ig_state.json, ig_trades.csv, optimize_ig.py, dashboard.py, dashboard.html

---

## Goal

Run Gold (CS.D.CFDGOLD.CFDGC.IP) alongside US30 in the IG bot, with a clean per-instrument config architecture that makes adding future epics a one-file + two-line change. Decouple IG S5 config from Bitget's `config_s5.py` patching mechanism entirely.

---

## Key Decisions

- **Up to one open position per instrument simultaneously** (e.g. US30 long + Gold long at the same time)
- **One file per instrument** — each contains all trading params + S5 params as a plain dict
- **evaluate_s5() gets optional `cfg` param** — backward-compatible; Bitget/backtest unchanged
- **One shared ig_trades.csv** with a new `symbol` column
- **Dashboard shows one card per instrument** in the IG positions panel

---

## Section 1: Config Structure

### `config_ig.py` — Registry only

```python
from config_ig_us30 import CONFIG as US30
from config_ig_gold  import CONFIG as GOLD

INSTRUMENTS = [US30, GOLD]

# Shared non-instrument settings
LOG_FILE          = "ig_bot.log"
TRADE_LOG         = "ig_trades.csv"
STATE_FILE        = "ig_state.json"
POLL_INTERVAL_SEC = 45
PAPER_MODE        = False
```

### Per-instrument config files

Each instrument file exports a single `CONFIG` dict with this shape:

```python
CONFIG = {
    # Instrument identity
    "epic":          "IX.D.DOW.IFD.IP",
    "display_name":  "US30",
    "currency":      "USD",

    # Contract sizing
    "contract_size": 1,       # opening size (contracts)
    "partial_size":  0.5,     # close at TP1 (50%)
    "point_value":   1.0,     # USD per point per contract

    # Session window (hour, minute) in ET
    "session_start": (0, 0),
    "session_end":   (23, 59),

    # Candle fetch limits
    "daily_limit":   200,
    "htf_limit":     50,
    "m15_limit":     300,

    # S5 strategy parameters (all S5_* names lowercased)
    "s5_enabled":               True,
    "s5_daily_ema_fast":        10,
    "s5_daily_ema_med":         20,
    "s5_daily_ema_slow":        50,
    "s5_htf_bos_lookback":      5,
    "s5_ltf_interval":          "15m",
    "s5_ob_lookback":           20,
    "s5_ob_min_impulse":        0.005,
    "s5_ob_min_range_pct":      0.002,
    "s5_choch_lookback":        10,
    "s5_max_entry_buffer":      0.01,
    "s5_sl_buffer_pct":         0.002,
    "s5_ob_invalidation_buffer_pct": 0.001,
    "s5_swing_lookback":        20,
    "s5_smc_fvg_filter":        False,
    "s5_smc_fvg_lookback":      10,
    "s5_leverage":              1,
    "s5_trade_size_pct":        0.05,
    "s5_min_rr":                2.0,
    "s5_trail_range_pct":       5,
    "s5_use_candle_stops":      True,
    "s5_min_sr_clearance":      0.10,
}
```

`config_ig_us30.py` — copy of current `config_ig.py` instrument params + all values from `config_ig_s5.py`, merged into the shape above.

`config_ig_gold.py` — same shape, Gold-tuned values (contract size, point value, S5 thresholds scaled for ~$3200/oz price).

### Files deleted

- `config_ig_s5.py` — absorbed into `config_ig_us30.py`

### Adding a new instrument in the future

1. Create `config_ig_<name>.py` with the CONFIG dict (copy an existing file, tune values)
2. Two lines in `config_ig.py`:
   ```python
   from config_ig_nasdaq import CONFIG as NASDAQ
   INSTRUMENTS = [US30, GOLD, NASDAQ]
   ```

`ig_bot.py` startup validates each instrument config against a required-keys list and raises a clear `KeyError` if a key is missing.

---

## Section 2: `evaluate_s5()` Decoupling (`strategy.py`)

### Signature change

```python
def evaluate_s5(
    symbol: str,
    daily_df: pd.DataFrame,
    htf_df: pd.DataFrame,
    m15_df: pd.DataFrame,
    allowed_direction: str,
    cfg=None,   # NEW: instrument CONFIG dict; None = Bitget path (config_s5 module)
) -> tuple[Signal, float, float, float, float, float, str]:
```

### Inside the function

Replace the existing `from config_s5 import ...` block with:

```python
if cfg is not None:
    S5_ENABLED                    = cfg["s5_enabled"]
    S5_DAILY_EMA_FAST             = cfg["s5_daily_ema_fast"]
    S5_DAILY_EMA_MED              = cfg["s5_daily_ema_med"]
    S5_DAILY_EMA_SLOW             = cfg["s5_daily_ema_slow"]
    S5_HTF_BOS_LOOKBACK           = cfg["s5_htf_bos_lookback"]
    S5_OB_LOOKBACK                = cfg["s5_ob_lookback"]
    S5_OB_MIN_IMPULSE             = cfg["s5_ob_min_impulse"]
    S5_OB_MIN_RANGE_PCT           = cfg["s5_ob_min_range_pct"]
    S5_CHOCH_LOOKBACK             = cfg["s5_choch_lookback"]
    S5_MAX_ENTRY_BUFFER           = cfg["s5_max_entry_buffer"]
    S5_SL_BUFFER_PCT              = cfg["s5_sl_buffer_pct"]
    S5_OB_INVALIDATION_BUFFER_PCT = cfg["s5_ob_invalidation_buffer_pct"]
    S5_SWING_LOOKBACK             = cfg["s5_swing_lookback"]
    S5_SMC_FVG_FILTER             = cfg["s5_smc_fvg_filter"]
    S5_SMC_FVG_LOOKBACK           = cfg["s5_smc_fvg_lookback"]
    S5_MIN_RR                     = cfg["s5_min_rr"]
    S5_TRAIL_RANGE_PCT            = cfg["s5_trail_range_pct"]
    S5_USE_CANDLE_STOPS           = cfg["s5_use_candle_stops"]
else:
    from config_s5 import (
        S5_ENABLED, S5_DAILY_EMA_FAST, ...  # unchanged — Bitget path
    )
```

### Impact on other callers

- `bot.py:585` — no change; passes no `cfg`, uses Bitget path
- `backtest.py` — no change; passes no `cfg`, uses Bitget path
- `ig_bot.py` — passes `cfg=instrument` for each instrument in the loop

### ig_bot.py patching block removed

The startup block in `ig_bot.py` lines 25–37 (`import config_s5 as _cs5_orig` … `setattr` loop) is **deleted entirely**.

---

## Section 3: State Management (`ig_state.json`)

### Structure change

`position` (singular) and `pending_order` (singular) become per-instrument dicts:

```json
{
  "positions": {
    "US30": {
      "trade_id": "abc123",
      "side": "LONG",
      "qty": 1.0,
      "entry": 44200.0,
      "sl": 44100.0,
      "tp": 44400.0,
      "opened_at": "2026-04-01T10:00:00+00:00"
    },
    "GOLD": null
  },
  "pending_orders": {
    "US30": null,
    "GOLD": {
      "deal_id": "def456",
      "side": "LONG",
      "ob_low": 3180.0,
      "ob_high": 3195.0,
      "sl": 3170.0,
      "tp": 3230.0,
      "trigger": 3195.0,
      "size": 1.0,
      "expires": 1743500000
    }
  },
  "scan_signals": { },
  "scan_log": []
}
```

`scan_signals` and `scan_log` are already keyed by display_name — no change needed.

### Migration

On startup, `ig_bot.py` detects the old format (has `"position"` key, not `"positions"`) and migrates in-place:
- Wraps old `position` value under `positions["US30"]`
- Wraps old `pending_order` value under `pending_orders["US30"]`
- Writes migrated state back to file before proceeding

### Files updated

- `ig_bot.py` — all `_save_state()` / `_sync_live_position()` / position reads use `state["positions"][display_name]`
- `dashboard.py:494` — changes from `json.load(f).get("position")` → `json.load(f).get("positions", {})`

---

## Section 4: Trade Logging (`ig_trades.csv`)

### New column: `symbol`

Added after `action` (column index 3):

```
timestamp, trade_id, action, symbol, side, qty, entry, sl, tp,
snap_entry_trigger, snap_sl, snap_rr,
snap_s5_ob_low, snap_s5_ob_high, snap_s5_tp,
result, pnl, exit_reason, session_date, mode
```

Column count: 19 → 20.

### Backward compatibility

Existing rows in `ig_trades.csv` will have an empty `symbol` field. `optimize_ig.py` treats empty `symbol` as "US30".

### `optimize_ig.py` changes

- Reads `symbol` column
- Adds optional `--symbol` flag to filter analysis by instrument (omitting it analyses all instruments)

---

## Section 5: `ig_bot.py` Main Loop

### Multi-instrument poll loop

```python
for instrument in INSTRUMENTS:
    display_name = instrument["display_name"]

    # Fetch candles for this instrument
    daily_df, htf_df, m15_df = _fetch_candles(instrument)

    # Evaluate S5 with instrument-specific config
    sig, trigger, sl, tp, ob_low, ob_high, reason = evaluate_s5(
        display_name, daily_df, htf_df, m15_df,
        allowed_direction, cfg=instrument
    )

    # Update scan state for this instrument
    _update_scan_state(display_name, sig, reason, ...)

    # Manage position and orders for this instrument
    _manage_position(instrument, sig, trigger, sl, tp, ob_low, ob_high)
```

All order placement, partial TP, trailing stop, and force-close functions accept `instrument` as their first argument and use `instrument["display_name"]` as the key into `positions` / `pending_orders`.

### Startup validation

```python
_REQUIRED_KEYS = {
    "epic", "display_name", "currency",
    "contract_size", "partial_size", "point_value",
    "session_start", "session_end",
    "daily_limit", "htf_limit", "m15_limit",
    "s5_enabled", "s5_daily_ema_fast", ...  # all S5 keys
}

for inst in INSTRUMENTS:
    missing = _REQUIRED_KEYS - inst.keys()
    if missing:
        raise KeyError(f"Instrument config '{inst.get('display_name', '?')}' missing keys: {missing}")
```

---

## Section 6: Dashboard

### `dashboard.py`

`get_ig_state()` changes:
- `position` read → `positions` dict read
- Returns `positions` dict to frontend

### `dashboard.html` — IG Positions Panel

Iterates over `positions` dict, renders one card per instrument:

```
┌─────────────────────────────────────┐
│ IG Positions                        │
│                                     │
│  US30    LONG  1.0ct  entry 44200   │
│          SL 44100  TP 44400         │
│                                     │
│  GOLD    —  no position             │
└─────────────────────────────────────┘
```

Card shows: instrument name, side, size, entry, SL, TP (same fields as current single-position panel).

`scan_signals` and `scan_log` panels are unchanged — already multi-instrument.

---

## Files Changed Summary

| File | Change |
|---|---|
| `config_ig.py` | Becomes registry — `INSTRUMENTS` list + shared settings only |
| `config_ig_us30.py` | **New** — merges old `config_ig.py` instrument params + `config_ig_s5.py` |
| `config_ig_gold.py` | **New** — Gold instrument params + Gold-tuned S5 params |
| `config_ig_s5.py` | **Deleted** — absorbed into `config_ig_us30.py` |
| `strategy.py` | Add optional `cfg` param to `evaluate_s5()` |
| `ig_bot.py` | Remove patching block; multi-instrument loop; per-instrument state keys |
| `ig_state.json` | `position`/`pending_order` → `positions`/`pending_orders` dicts; startup migration |
| `ig_trades.csv` | Add `symbol` column (col 4); old rows get empty symbol treated as US30 |
| `optimize_ig.py` | Handle `symbol` column; add `--symbol` filter flag |
| `dashboard.py` | Read `positions` dict instead of single `position` |
| `dashboard.html` | IG positions panel iterates over instruments, one card each |

---

## DEPENDENCIES.md Updates Required After Implementation

- Section 1 (Architecture): update diagram to show multi-instrument IG bot
- Section 2.1 (evaluate_s5): document new `cfg` parameter
- Section 4.3 (ig_trades.csv): update column list (19 → 20), note `symbol` column
- Section 4.4 (ig_state.json): update structure to show `positions`/`pending_orders` dicts
- Section 5 (Config): document per-instrument config shape and required keys
- Section 10.3 (Import Timing): note patching mechanism removed; `cfg` param is the new approach
