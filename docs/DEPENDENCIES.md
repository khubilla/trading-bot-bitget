# Codebase Dependency Map

**Last updated:** 2026-04-02
**Update frequency:** After every PR that changes interfaces, data contracts, or cross-file dependencies

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Shared Files (Cross-Bot)](#2-shared-files-cross-bot)
3. [Bot-Specific Files](#3-bot-specific-files)
4. [Data Contracts](#4-data-contracts)
5. [Config Dependencies](#5-config-dependencies)
6. [Function Call Graph](#6-function-call-graph)
7. [Strategy Implementations](#7-strategy-implementations)
8. [External Tool Dependencies](#8-external-tool-dependencies)
9. [Dashboard Integration](#9-dashboard-integration)
10. [Confusing Names & Common Pitfalls](#10-confusing-names--common-pitfalls)
11. [Maintenance Guide](#11-maintenance-guide)

---

## 1. Architecture Overview

### Two Independent Bots

```
┌─────────────────────────────────────────────────────────────┐
│                     BITGET BOT (bot.py)                     │
│  Crypto USDT-margined futures · S1-S6 strategies           │
│  Output: state.json, trades.csv (or _paper variants)       │
└─────────────────────────────────────────────────────────────┘
                              ↓
                    ┌─────────────────┐
                    │  strategy.py    │ ← SHARED
                    │  config_s5.py   │ ← SHARED (Bitget only)
                    │  paper_trader.py│ ← SHARED
                    └─────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                      IG BOT (ig_bot.py)                     │
│  Multi-instrument CFD · S5 only                            │
│  Instruments: US30 (IX.D.DOW) + GOLD (CS.D.CFDGOLD)       │
│  Each tick: loops over INSTRUMENTS, one position per epic   │
│  Output: ig_state.json, ig_trades.csv                      │
└─────────────────────────────────────────────────────────────┘
```

### Shared Code Contract

**Files shared between bots:**
- `strategy.py` — all evaluate_s1 through evaluate_s6 functions
- `config_s5.py` — S5 parameters (Bitget only; IG now uses per-instrument CONFIG dicts)
- `paper_trader.py` — simulation engine (used by Bitget bot only in paper mode)

**Separation rules:**
- Changes to shared files require testing BOTH bots
- Bitget and IG must work independently (no cross-bot state)
- config_s5 changes affect Bitget only; IG uses `cfg=instrument` param to `evaluate_s5()`

### Data Flow

```
bot.py → state.py → state_paper.json → dashboard.py → dashboard.html
       → _log_trade  → trades_paper.csv  → optimize.py

ig_bot.py → _log_trade → ig_trades.csv → optimize_ig.py
          → _save_state → ig_state.json (position persistence, no state.py module)

paper_trader.py → paper_state.json (internal simulation state)
```

---

## 2. Shared Files (Cross-Bot)

### 2.1 strategy.py

**Purpose:** Contains all strategy evaluation logic (S1-S6) shared by both Bitget and IG bots. S6 is Bitget-only; ig_bot.py does not call evaluate_s6.

**Used by:**
- `bot.py` (Bitget bot) — imports and calls evaluate_s1 through evaluate_s6
- `ig_bot.py` (IG bot) — imports and calls evaluate_s5 only
- `backtest.py` — historical strategy testing (uses evaluate_s1, s2, s3; does NOT use evaluate_s5)

**Key function:** `evaluate_s5()`

#### evaluate_s5()

**Signature:**
```python
def evaluate_s5(
    symbol: str,
    daily_df: pd.DataFrame,
    htf_df: pd.DataFrame,
    m15_df: pd.DataFrame,
    allowed_direction: str,
    cfg=None,   # NEW: instrument CONFIG dict; None = Bitget/backtest path
) -> tuple[Signal, float, float, float, float, float, str]:
```

**Defined:** `strategy.py` line 1067

**Called by:**
- `bot.py` — `s5_sig, s5_trigger, s5_sl, s5_tp, s5_ob_low, s5_ob_high, s5_reason = evaluate_s5(...)` (no `cfg`)
- `backtest.py` — calls evaluate_s5 without `cfg` (Bitget/backtest path)
- `ig_bot.py` — `sig, trigger, sl, tp, ob_low, ob_high, reason = evaluate_s5(..., cfg=instrument)` for each instrument in the loop

**Dependencies (config resolution):**

The function resolves S5 parameters via one of two paths depending on the `cfg` argument:

- **When `cfg` is not None** (IG path): reads all S5 params from the instrument CONFIG dict
  ```python
  if cfg is not None:
      S5_ENABLED        = cfg["s5_enabled"]
      S5_DAILY_EMA_FAST = cfg["s5_daily_ema_fast"]
      # ... all S5 keys from instrument CONFIG dict
  ```

- **When `cfg` is None** (Bitget/backtest path): performs LATE import of config_s5 module
  ```python
  else:
      from config_s5 import (
          S5_ENABLED,
          S5_DAILY_EMA_FAST, S5_DAILY_EMA_MED, S5_DAILY_EMA_SLOW,
          S5_HTF_BOS_LOOKBACK,
          S5_OB_LOOKBACK, S5_OB_MIN_IMPULSE, S5_CHOCH_LOOKBACK,
          S5_MAX_ENTRY_BUFFER, S5_SL_BUFFER_PCT,
          S5_MIN_RR, S5_SWING_LOOKBACK,
          S5_OB_MIN_RANGE_PCT, S5_SMC_FVG_FILTER, S5_SMC_FVG_LOOKBACK,
      )
  ```
  This import happens at CALL TIME (not module load time) to remain compatible with any existing tooling that relies on late binding.

**Return values:**
```python
(signal, entry_trigger, sl_price, tp_price, ob_low, ob_high, reason)
# signal: "PENDING_LONG" | "PENDING_SHORT" | "HOLD"
# entry_trigger: float (price level to enter trade)
# sl_price: float (stop loss price)
# tp_price: float (take profit price)
# ob_low: float (order block lower boundary)
# ob_high: float (order block upper boundary)
# reason: str (explanation of signal decision)
```

**Breaking scenarios:**
1. Changing function signature (parameters or return tuple order)
2. Modifying config_s5 parameter names without updating the import statement (Bitget path)
3. Renaming S5 keys in an instrument CONFIG dict without updating the `cfg is not None` block (IG path)
4. Changing return tuple structure (adding/removing/reordering elements)

#### Critical Invariants

1. The config_s5 import MUST remain inside evaluate_s5() at call time (Bitget path)
2. All callers must use the same signature; `cfg` is optional and defaults to None
3. Return tuple order must remain stable: (signal, trigger, sl, tp, ob_low, ob_high, reason)
4. Instrument CONFIG dicts must include all S5 keys consumed in the `cfg is not None` block

#### Verification Commands

```bash
# Find evaluate_s5 definition
grep -n "^def evaluate_s5" strategy.py

# Find all callers
grep -n "evaluate_s5" bot.py ig_bot.py

# Verify config import is inside function
sed -n '1067,1095p' strategy.py | grep "from config_s5 import"

# Check IG patching mechanism
sed -n '24,36p' ig_bot.py
```

#### evaluate_s6()

**Signature:**
```python
def evaluate_s6(
    symbol: str,
    daily_df: pd.DataFrame,
    allowed_direction: str,
) -> tuple[str, float, float, float, float, str]:
    # Returns: (signal, peak_level, sl_price, drop_pct, rsi_at_peak, reason)
```

**Callers:**
- `bot.py` — `s6_sig, s6_peak_level, s6_sl, s6_drop_pct, s6_rsi_at_peak, s6_reason = evaluate_s6(symbol, daily_df, allowed_direction)` (Bitget-only; ig_bot.py does NOT call this)

**Breaking changes:**
- Changing return value count/order → bot.py unpacking fails
- ig_bot.py does NOT call evaluate_s6; no IG impact

---

### 2.2 snapshot.py

**Purpose:** Saves and loads OHLCV candle snapshots at trade lifecycle events (open, scale_in, partial, close) so charts always reflect the exact market state at each event.

**Files stored:** `data/snapshots/{trade_id}_{event}.json`

**Used by:**
- `bot.py` line 32 — `import snapshot` (module-level); calls `save_snapshot()` at trade open (line 1164), scale-in (line 1109), partial TP (line 533), close (line 692), and during startup reconciliation for partials (line 338) and closes (line 385)
- `dashboard.py` line 647 — lazy import `import snapshot as _snap`; calls `load_snapshot(trade_id, "open")` (line 659) to serve candle data via the `/api/entry-chart` endpoint
- `dashboard.py` (in `get_trade_chart`) — lazy import `import snapshot as _snap`; calls `list_snapshots(trade_id)` to discover all events, then `load_snapshot(trade_id, event)` for each event to serve merged candle data via the `/api/trade-chart` endpoint

**Key functions:**

```python
def save_snapshot(
    trade_id: str,
    event: str,          # "open" | "scale_in" | "partial" | "close"
    symbol: str,
    interval: str,       # e.g. "15m", "1D"
    candles: list[dict], # list of {"t", "o", "h", "l", "c", "v"}
    event_price: float,
    captured_at: str | None = None,
) -> None: ...

def load_snapshot(trade_id: str, event: str) -> dict | None: ...

def list_snapshots(trade_id: str) -> list[str]: ...
```

**Snapshot file structure:**
```json
{
  "trade_id":    "a1b2c3d4",
  "symbol":      "BTCUSDT",
  "interval":    "15m",
  "event":       "open",
  "captured_at": "2026-03-31T04:00:00+00:00",
  "event_price": 82500.0,
  "candles":     [{"t": 1743379200000, "o": 82400.0, "h": 82600.0, "l": 82300.0, "c": 82500.0, "v": 123.4}]
}
```

**Breaking scenarios:**

1. **Renaming event strings** (`"open"` → `"entry"`) → `load_snapshot(trade_id, "open")` in dashboard.py returns None; entry chart shows no data
   - Fix: Update all `save_snapshot(event=...)` calls in bot.py AND `load_snapshot` call in dashboard.py

2. **Moving `_SNAP_DIR`** → Existing snapshot files become unreachable
   - Fix: Migrate existing files or update `_SNAP_DIR` in snapshot.py

3. **Changing candle dict keys** (`"t"/"o"/"h"/"l"/"c"/"v"`) → dashboard.py chart renderer breaks
   - Fix: Update dashboard.py chart rendering code to match new keys

4. **Removing `event_price` from payload** → dashboard.py `/api/entry-chart` response loses price marker
   - Fix: Update dashboard.py line ~670 where `event_price` is read from snapshot

**Verification commands:**

```bash
# Check snapshot callers in bot.py
grep -n "snapshot.save_snapshot\|snapshot.load_snapshot" bot.py

# Check dashboard usage
grep -n "snapshot" dashboard.py

# List saved snapshots for a trade
python -c "import snapshot; print(snapshot.list_snapshots('a1b2c3d4'))"

# Check snapshot directory exists
ls data/snapshots/ 2>/dev/null | head -5 || echo "No snapshots yet"
```

---

## 3. Bot-Specific Files

[To be populated in Task 7]

---

## 4. Data Contracts

### 4.1 state.json / state_paper.json

**Purpose:** Persistent bot state containing balance, open trades, strategy signals, and per-pair analysis data.

**Write chain:**
- `bot.py` line 638: `st.update_pair_state(symbol, {...})` — writes strategy signals and analysis data for each pair
- `state.py`: Manages in-memory state and atomic writes to JSON file
- Output: `state.json` (live mode) or `state_paper.json` (paper mode)

**Read chain:**
- `dashboard.py` line 66: `state = json.load(f)` — loads entire state for dashboard display
- `dashboard.py` line 68: CSV history injected into state["trade_history"] (CSV is authoritative)
- `dashboard.html` line 1766: `const pairStates = s.pair_states || {}` — JavaScript consumes pair_states
- `dashboard.html` lines 1475, 1531: `ps.s2_signal` — individual strategy signals displayed
- `dashboard.html` lines 1499, 1703: `ps.s5_priority_rank` — S5 priority sorting

**Top-level fields:**
```python
{
  "status": str,              # "RUNNING" | "STOPPED"
  "started_at": str,          # ISO timestamp
  "last_tick": str,           # ISO timestamp of last scan cycle
  "balance": float,           # Current account balance (USDT)
  "open_trades": list[dict],  # Active positions
  "trade_history": list[dict],# Recently closed trades (runtime only; dashboard replaces with CSV)
  "scan_log": list[str],      # Recent scan activity log entries
  "qualified_pairs": list[str],# Pairs that passed S/R and volatility filters
  "pair_states": dict,        # Per-pair strategy analysis (see below)
  "sentiment": dict,          # Global sentiment state
  "pending_signals": dict,    # Per-symbol entry watcher payloads (S2/S3/S4/S5/S6); survives restarts
}
```

**pending_signals:** Each key is a symbol. Value is a strategy-specific dict with at minimum `strategy`, `side`, `trigger` (or `peak_level` for S6), `expires`. Written by `state.save_pending_signals()`, read on startup via `state.load_pending_signals()`. Preserved across `reset()`. **Not consumed by dashboard** — bot-internal only.

**pair_states structure:**

Each key is a symbol (e.g., "BTCUSDT"). Value is a dict with 44 fields:

```python
{
  # Price and HTF trend
  "price": float,
  "htf_bull": bool,           # Higher timeframe bullish structure
  "htf_bear": bool,           # Higher timeframe bearish structure

  # S1 fields
  "rsi": float,
  "adx": float,
  "rsi_ok": bool,
  "trend_ok": bool,
  "consolidating": bool,      # Coiling pattern detected
  "box_high": float | None,   # Consolidation box upper boundary
  "box_low": float | None,    # Consolidation box lower boundary
  "s1_signal": str,           # "LONG" | "SHORT" | "HOLD"
  "reason": str,              # S1 decision explanation

  # S2 fields
  "s2_signal": str,           # "LONG" | "SHORT" | "HOLD"
  "s2_reason": str,
  "s2_daily_rsi": float | None,
  "s2_big_candle": bool,
  "s2_coiling": bool,
  "s2_box_low": float | None,
  "s2_box_high": float | None,
  "s2_sr_resistance_pct": float | None,
  "s2_sr_resistance_price": float | None,

  # S3 fields
  "s3_signal": str,           # "LONG" | "SHORT" | "HOLD"
  "s3_reason": str,
  "s3_adx": float | None,
  "s3_sr_resistance_pct": float | None,
  "s3_sr_resistance_price": float | None,

  # S4 fields
  "s4_signal": str,           # "LONG" | "SHORT" | "HOLD"
  "s4_reason": str,
  "s4_sr_support_pct": float | None,

  # S5 fields
  "s5_signal": str,           # "LONG" | "SHORT" | "HOLD" | "PENDING"
  "s5_reason": str,
  "s5_ob_low": float | None,  # Order block lower boundary
  "s5_ob_high": float | None, # Order block upper boundary
  "s5_entry_trigger": float | None,
  "s5_sl": float | None,      # Stop loss price
  "s5_tp": float | None,      # Take profit price
  "s5_sr_pct": float | None,  # S/R clearance percentage
  "s5_priority_rank": int | None,    # Rank (1=best) set by _execute_best_s5_candidate
  "s5_priority_score": float | None, # Scoring metric for ranking

  # S6 fields
  "s6_signal": str,           # "PENDING_SHORT" | "HOLD"
  "s6_reason": str,
  "s6_peak_level": float | None,  # High of swing-high candle (resistance / entry watch level)
  "s6_sl": float | None,      # Stop loss: peak_level * (1 + S6_SL_PCT)
  "s6_fakeout_seen": bool | None, # True once mark_price > peak_level (phase 1 gate)

  # Shared S/R fields
  "sr_resistance_pct": float | None,
  "sr_support_pct": float | None,

  # Metadata
  "signal": str,              # Aggregate signal (first non-HOLD from S1-S6)
  "strategy": str,            # "S1" | "S2" | "S3" | "S4" | "S5" | "S6"
  "updated_at": str,          # ISO timestamp
}
```

**Breaking scenarios:**

1. **Renaming pair_states fields** → Dashboard crashes with "undefined" errors
   - Example: Renaming `s5_priority_rank` breaks lines 1499, 1703 in dashboard.html
   - Fix: Update all dashboard.html references + dashboard.py parsing

2. **Changing top-level keys** → Dashboard API fails
   - Example: Renaming "pair_states" to "pairs" breaks line 1766
   - Fix: Update dashboard.py and dashboard.html

3. **Changing signal values** → Dashboard filters break
   - Example: Using "BUY" instead of "LONG" breaks line 1475 comparisons
   - Fix: Update all signal comparisons in dashboard.html

4. **Removing fields** → TypeError in dashboard rendering
   - Example: Removing `s2_signal` breaks strategy tabs
   - Fix: Add null checks in dashboard.html OR maintain field in state.py

**Verification commands:**

```bash
# Check state write location
grep -n "update_pair_state" bot.py

# Check dashboard read locations
grep -n "pair_states\|s2_signal\|s5_priority_rank" dashboard.html

# Validate state structure
python -c "import json; s=json.load(open('state_paper.json')); print(list(s.keys()))"

# Check pair_states fields
python -c "import json; s=json.load(open('state_paper.json')); ps=s['pair_states']; print(list(ps[list(ps.keys())[0]].keys()) if ps else [])"
```

---

### 4.2 trades.csv / trades_paper.csv

**Purpose:** Append-only log of all trade entries and exits (Bitget bot only).

**Write chain:**
- `bot.py` line 74: `def _log_trade(action, details)` — appends row to CSV
- `bot.py` line 79: Uses `_TRADE_FIELDS` (defined at line 55) for column order
- Output: `trades.csv` (live mode) or `trades_paper.csv` (paper mode)

**Read chain:**
- `dashboard.py` line 68: `csv_path = STATE_FILE.replace(..., "trades.csv")`
- `dashboard.py` line 69: `csv_history = _load_csv_history(csv_path)` — loads recent trades
- `dashboard.py` line 71: Injects CSV history into state (CSV is authoritative source)
- `optimize.py` line 229: `csv_path.replace("trades.csv", "trades_paper.csv")` — parameter analysis

**Columns (41 fields):**

```csv
timestamp,trade_id,action,symbol,side,qty,entry,sl,tp,
box_low,box_high,leverage,margin,tpsl_set,strategy,
snap_rsi,snap_adx,snap_htf,snap_coil,snap_box_range_pct,snap_sentiment,
snap_daily_rsi,
snap_entry_trigger,snap_sl,snap_rr,
snap_rsi_peak,snap_spike_body_pct,snap_rsi_div,snap_rsi_div_str,
snap_s5_ob_low,snap_s5_ob_high,snap_s5_tp,
snap_s6_peak,snap_s6_drop_pct,snap_s6_rsi_at_peak,
snap_sr_clearance_pct,
result,pnl,pnl_pct,exit_reason,exit_price
```

**Field categories:**
- **Core trade data:** timestamp, trade_id, action, symbol, side, qty, entry, sl, tp
- **Position metadata:** box_low, box_high, leverage, margin, tpsl_set, strategy
- **S1 snapshot:** snap_rsi, snap_adx, snap_htf, snap_coil, snap_box_range_pct, snap_sentiment
- **S2 snapshot:** snap_daily_rsi
- **S3 snapshot:** snap_entry_trigger, snap_sl, snap_rr
- **S4 snapshot:** snap_rsi_peak, snap_spike_body_pct, snap_rsi_div, snap_rsi_div_str
- **S5 snapshot:** snap_s5_ob_low, snap_s5_ob_high, snap_s5_tp
- **S6 snapshot:** snap_s6_peak, snap_s6_drop_pct, snap_s6_rsi_at_peak
- **S/R snapshot:** snap_sr_clearance_pct
- **Close data:** result, pnl, pnl_pct, exit_reason, exit_price

**Breaking scenarios:**

1. **Changing column order in _TRADE_FIELDS** → CSV header misaligns with data
   - Example: Moving "symbol" after "action" breaks DictWriter column mapping
   - Fix: Update _TRADE_FIELDS definition at bot.py line 55

2. **Renaming columns** → optimize.py fails to parse CSV
   - Example: Renaming "snap_rsi" to "entry_rsi" breaks parameter analysis
   - Fix: Update optimize.py CSV parsing logic

3. **Removing columns** → DictWriter writes empty strings (restval="")
   - Example: Removing "snap_s5_ob_low" leaves empty column in CSV
   - Fix: Update _TRADE_FIELDS and all _log_trade calls

4. **Adding columns** → New field must be added to _TRADE_FIELDS
   - Example: Adding "snap_s5_sr_support_pct" without updating _TRADE_FIELDS
   - Fix: Add field to _TRADE_FIELDS at bot.py line 55

**Readers:**
- `dashboard.py` — displays recent trade history
- `optimize.py` — parameter optimization and backtest analysis
- `bot.py` line 85: `_rebuild_stats_from_csv()` — restores win/loss stats after bot restart

**Verification commands:**

```bash
# Check CSV header
head -1 trades_paper.csv

# Find _log_trade callers
grep -n "_log_trade" bot.py

# Verify column definition
sed -n '55,72p' bot.py

# Count columns
head -1 trades_paper.csv | tr ',' '\n' | wc -l
```

---

### 4.3 ig_trades.csv

**Purpose:** Append-only log of all IG bot trades (US30 CFD).

**Write chain:**
- `ig_bot.py` line 75: `def _log_trade(action, details, paper)` — appends row to CSV
- `ig_bot.py` line 85: Uses `_TRADE_FIELDS` (defined at line 65) for column order
- Output: `ig_trades.csv` (single file for both live and paper trades)

**Read chain:**
- `optimize_ig.py` line 43: "Load completed trades from ig_trades.csv"
- Sends closed trades to Claude API for S5 parameter optimization

**Columns (20 fields):**

```csv
timestamp, trade_id, action, symbol,
side, qty, entry, sl, tp,
snap_entry_trigger, snap_sl, snap_rr,
snap_s5_ob_low, snap_s5_ob_high, snap_s5_tp,
result, pnl, exit_reason,
session_date, mode
```

`symbol` is at index 3 (after `action`). Column count increased from 19 → 20.

**Key differences from Bitget CSV:**
- **Fewer columns:** IG only uses S5 strategy, so S1-S4 snapshot fields are omitted
- **Additional fields:** `session_date` (YYYY-MM-DD), `mode` (PAPER | LIVE)
- **Multi-instrument:** `symbol` column identifies which instrument the trade is for (e.g. "US30", "GOLD")
- **No leverage/margin:** IG uses fixed position sizing via per-instrument CONFIG dicts

**Backward compatibility:**

Rows written before the multi-instrument migration have an empty `symbol` field. `optimize_ig.py` treats an empty `symbol` as "US30".

**Breaking scenarios:**

1. **Changing _TRADE_FIELDS order** → CSV header misaligns
   - Fix: Update _TRADE_FIELDS at ig_bot.py line 65

2. **Removing mode or session_date** → optimize_ig.py fails to filter trades
   - Fix: Update _TRADE_FIELDS and all _log_trade calls in ig_bot.py

3. **Renaming columns** → Claude API optimization prompts break
   - Fix: Update optimize_ig.py prompt template

4. **Adding a new instrument without updating optimize_ig.py** → new symbol silently treated as US30
   - Fix: Update optimize_ig.py to handle the `symbol` column properly; use `--symbol` flag to filter by instrument

**Reader:**
- `optimize_ig.py` — sends completed trades to Claude API for parameter tuning; supports optional `--symbol` flag to filter by instrument

**Verification commands:**

```bash
# Check IG CSV exists (may not exist if bot never ran)
ls -lh ig_trades.csv 2>/dev/null || echo "IG CSV not yet created"

# Check column definition
sed -n '65,72p' ig_bot.py

# Find _log_trade callers in IG bot
grep -n "_log_trade" ig_bot.py

# Count columns (should be 20)
head -1 ig_trades.csv | tr ',' '\n' | wc -l
```

---

### 4.4 ig_state.json

**Purpose:** Minimal state file for IG bot position persistence (no state.py module).

**Write chain:**
- `ig_bot.py`: Direct JSON writes (no state.py abstraction)
- State is keyed per-instrument to support multiple simultaneous positions

**Read chain:**
- `dashboard.py`: reads `positions` dict to display IG positions panel
- `ig_bot.py`: Loads positions and pending_orders on startup to resume after restart

**Structure:**
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
  "scan_signals": {
    "<DISPLAY_NAME>": {
      "signal":        "PENDING_LONG | PENDING_SHORT | HOLD",
      "reason":        "full evaluate_s5 reason string",
      "ema_ok":        true,
      "bos_ok":        true,
      "ob_ok":         false,
      "ob_low":        null,
      "ob_high":       null,
      "entry_trigger": null,
      "sl":            null,
      "tp":            null,
      "updated_at":    "ISO timestamp (UTC)"
    }
  },
  "scan_log": [
    {
      "ts":         "HH:MM in ET timezone",
      "instrument": "US30",
      "message":    "evaluate_s5 reason string"
    }
  ]
}
```

**Startup migration:**

On startup, `ig_bot.py` detects the old single-instrument format (has `"position"` key, not `"positions"`) and migrates in-place:
- Wraps old `position` value under `positions["US30"]`
- Wraps old `pending_order` value under `pending_orders["US30"]`
- Writes migrated state back to file before proceeding

**Breaking scenarios:**

1. **Renaming "positions" key** → Dashboard IG panel crashes; ig_bot.py fails to load state
   - Fix: Update dashboard.py and all ig_bot.py state reads/writes

2. **Changing position field names** → IG bot fails to resume position after restart
   - Fix: Update all ig_bot.py position reads/writes

3. **Removing or renaming "pending_orders" key** → ig_bot.py fails to restore pending orders after restart
   - Fix: Update ig_bot.py _save_state() and _sync_live_position() load paths

4. **Renaming "scan_signals" key** → Dashboard IG scanner panel shows no cards
   - Fix: Update dashboard.py get_ig_state() `.get("scan_signals", {})` and renderIGScanner() call

5. **Changing scan_signals entry field names** → Dashboard cards show wrong or missing check values
   - Fix: Update renderIGScanner() in dashboard.html to match new field names

6. **Renaming "scan_log" key** → Dashboard scan log panel stays empty
   - Fix: Update dashboard.py get_ig_state() `.get("scan_log", [])` and renderIGScanLog() call

7. **Adding a new instrument without the startup migration guard** → Old state file missing the new key causes KeyError
   - Fix: Always access positions/pending_orders with `.get(display_name)` or ensure migration populates all known instruments

**Verification commands:**

```bash
# Check IG state structure
python -c "import json; print(json.load(open('ig_state.json')))"

# Find dashboard reads
grep -n "ig_state.json" dashboard.py
```

---

## 5. Config Dependencies

### 5.1 IG Config Architecture

`config_ig.py` is now a **registry** — it imports per-instrument CONFIG dicts and exposes them as the `INSTRUMENTS` list consumed by `ig_bot.py`.

```python
# config_ig.py
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

### 5.2 Per-Instrument CONFIG Shape

Each instrument file (`config_ig_us30.py`, `config_ig_gold.py`, etc.) exports a single flat `CONFIG` dict. All 33 keys are required:

```python
CONFIG = {
    # Instrument identity
    "epic":          str,   # IG epic ID (e.g. "IX.D.DOW.IFD.IP")
    "display_name":  str,   # Short name used as state key (e.g. "US30")
    "currency":      str,   # Account currency (e.g. "USD")

    # Contract sizing
    "contract_size": float, # Opening size in contracts
    "partial_size":  float, # Contracts to close at TP1
    "point_value":   float, # USD per point per contract

    # Session window (hour, minute) in ET timezone
    "session_start": tuple, # e.g. (9, 30)
    "session_end":   tuple, # e.g. (12, 30)

    # Candle fetch limits
    "daily_limit":   int,
    "htf_limit":     int,
    "m15_limit":     int,

    # S5 strategy parameters (lowercase versions of S5_* names)
    "s5_enabled":                    bool,
    "s5_daily_ema_fast":             int,
    "s5_daily_ema_med":              int,
    "s5_daily_ema_slow":             int,
    "s5_htf_bos_lookback":           int,
    "s5_ltf_interval":               str,   # e.g. "15m"
    "s5_ob_lookback":                int,
    "s5_ob_min_impulse":             float,
    "s5_ob_min_range_pct":           float,
    "s5_choch_lookback":             int,
    "s5_max_entry_buffer":           float,
    "s5_sl_buffer_pct":              float,
    "s5_ob_invalidation_buffer_pct": float,
    "s5_swing_lookback":             int,
    "s5_smc_fvg_filter":             bool,
    "s5_smc_fvg_lookback":           int,
    "s5_leverage":                   int,
    "s5_trade_size_pct":             float,
    "s5_min_rr":                     float,
    "s5_trail_range_pct":            float,
    "s5_use_candle_stops":           bool,
    "s5_min_sr_clearance":           float,
}
```

### 5.3 Startup Validation

`ig_bot.py` calls `_validate_instruments()` at startup, which checks every instrument in `INSTRUMENTS` against the full required-keys set. A missing key raises a clear `KeyError` before any API calls are made:

```python
missing = _REQUIRED_KEYS - inst.keys()
if missing:
    raise KeyError(f"Instrument config '{inst.get('display_name', '?')}' missing keys: {missing}")
```

### 5.4 Adding a New Instrument

1. Create `config_ig_<name>.py` with the CONFIG dict (copy an existing file, tune values)
2. Add two lines in `config_ig.py`:
   ```python
   from config_ig_nasdaq import CONFIG as NASDAQ
   INSTRUMENTS = [US30, GOLD, NASDAQ]
   ```
3. No other files need updating — `ig_bot.py` loops over `INSTRUMENTS` dynamically

### 5.5 Bitget Bot — `config.py` Scanner/Liquidity Params

These params live in `config.py` and are imported by `scanner.py`. Changing them affects all strategies, since `scanner.py` produces the `qualified_pairs` list consumed by `bot.py` each scan cycle.

| Param | Default | Purpose |
|---|---|---|
| `MIN_VOLUME_USDT` | `5_000_000` | 24h quote volume floor — pairs below this are excluded before any strategy sees them |
| `MAX_PRICE_USDT` | `150` | Exclude pairs priced above this |
| `SCAN_INTERVAL_SEC` | `60` | Re-scan interval |
| `LIQUIDITY_CHECK_ENABLED` | `True` | Master on/off for the OB depth filter |
| `MIN_OB_DEPTH_USDT` | `50_000` | Minimum combined top-of-book depth (`bidSz×bidPr + askSz×askPr`). Pairs below this are excluded as the last funnel step in `scanner.py`. |

**Who reads these:**
- `scanner.py` — imports all five; `LIQUIDITY_CHECK_ENABLED` and `MIN_OB_DEPTH_USDT` gate `_filter_by_liquidity()`
- `bot.py` — reads `SCAN_INTERVAL_SEC`, `MIN_VOLUME_USDT` indirectly via scanner
- `ig_bot.py` — **does not** read any of these; IG uses its own scan logic

**Breaking scenarios:**
- Removing or renaming `LIQUIDITY_CHECK_ENABLED` / `MIN_OB_DEPTH_USDT` → `scanner.py` import fails at startup
- Setting `MIN_OB_DEPTH_USDT` too high → all pairs filtered, bot sees empty qualified list every cycle

### 5.6 File Inventory

| File | Role |
|------|------|
| `config_ig.py` | Registry: imports instrument configs, exposes `INSTRUMENTS` list + shared settings |
| `config_ig_us30.py` | US30 CONFIG dict (instrument params + S5 params) |
| `config_ig_gold.py` | GOLD CONFIG dict (instrument params + S5 params) |
| `config_ig_s5.py` | **Deleted** — absorbed into `config_ig_us30.py` in the multi-instrument migration |

**Verification commands:**

```bash
# Confirm INSTRUMENTS list contents
python -c "from config_ig import INSTRUMENTS; print([i['display_name'] for i in INSTRUMENTS])"

# Check all required keys present in each instrument config
python -c "import ig_bot"  # raises KeyError at startup if any key missing

# Check config_ig_s5.py is gone
ls config_ig_s5.py 2>/dev/null && echo "WARNING: should be deleted" || echo "Correctly absent"
```

---

## 6. Function Call Graph

### 6.1 trader.py — Exit Order Functions

These functions place and manage exit orders on Bitget. All are called from `bot.py` only (not `ig_bot.py`).

#### `_place_s1_exits(symbol, hold_side, qty_str, sl_trig, sl_exec, trail_trigger, trail_range)`
Places 3 exit orders for S1 strategy:
1. `place-pos-tpsl` — position-level SL (full position, auto-scales with size changes)
2. `place-tpsl-order profit_plan` — sell 50% at `trail_trigger` (+10% from fill)
3. `place-tpsl-order moving_plan` — trail remaining 50% with `rangeRate=trail_range` (5%)

**Called from:** `open_long` (S1 path), `open_short` (S1 path)

**Qty splitting:** uses `_round_qty` to respect symbol minimum volume. On odd integer qty (e.g. 209): half=104, rest=105.

**Retry:** 3 attempts with 1.5s sleep between attempts. Returns `True` on success, `False` after 3 consecutive failures.

---

#### `_place_s2_exits(symbol, hold_side, qty_str, sl_trig, sl_exec, trail_trigger, trail_range)`
Places 3 exit orders for S2 (LONG) and S4 (SHORT) strategies:
1. `place-pos-tpsl` — position-level SL (full position, auto-scales with size changes)
2. `place-tpsl-order profit_plan` — partial TP at `trail_trigger` for `half_qty`
3. `place-tpsl-order moving_plan` — trailing stop at `trail_trigger` for `rest_qty`

**Called from:** `open_long` (S2/S3 path), `open_short` (S4 path)

**Key behaviour:**
- `half_qty = _round_qty(qty / 2, symbol)` — respects `volume_place` (e.g. integer-only symbols like STOUSDT)
- `rest_qty = _round_qty(qty - half, symbol)` — covers true remainder (not a duplicate half)
- Returns `False` if all 3 retry attempts fail → `tpsl_set=False` in CSV row

**Breaking change:** Changing the `size` format or plan types breaks live exit execution for S2/S4 trades.

---

#### `_place_s5_exits(symbol, hold_side, qty_str, sl_trig, sl_exec, partial_trig, tp_target, trail_range_pct)`
Places 3 exit orders for S5 strategy:
1. `place-pos-tpsl` — position-level SL
2. `place-tpsl-order profit_plan` — partial TP at 1:1 R:R for `half_qty`
3. If `tp_target > 0`: hard TP for `rest_qty`; else: `moving_plan` trailing stop for `rest_qty`

**Called from:** `open_long` (S5 path), `open_short` (S5 path), `bot.py` directly for live S5 limit fills

**Same qty splitting rules as `_place_s2_exits`.**

---

#### `open_long(symbol, box_low, sl_floor, leverage, trade_size_pct, take_profit_pct, stop_loss_pct, use_s1_exits, use_s2_exits, use_s5_exits, tp_price_abs)`
Opens a LONG market order then places exits based on the `use_*_exits` flag.

**Fill price:** After placing the market order and sleeping 2s, calls `get_all_open_positions()` to get `openPriceAvg` as the actual fill price. All exit-level calculations (SL, TP, trail trigger, partial TP) use this fill price. Falls back to the pre-order mark price if the position is not yet visible. The pre-order mark is still used for qty sizing.

**S1 path (`use_s1_exits=True`):** `trail_trigger = fill * (1 + TAKE_PROFIT_PCT)`, `sl_trig = sl_floor`. Calls `_place_s1_exits`.

**SL cap (S2 path):** `sl_trig = max(box_low * 0.999, fill * (1 - stop_loss_pct))` — prevents box_low from being so far below entry that the SL exceeds the intended max loss (e.g. `-50%` at 10x for `S2_STOP_LOSS_PCT=0.05`).

**Returns:** dict with `{symbol, side, qty, entry, sl, tp, box_low, leverage, margin, tpsl_set}` — `entry` is the actual fill price from `get_all_open_positions()` (falls back to pre-order mark if position not yet visible).

**Called from:** `bot.py` — `_execute_s1`, `_execute_s2`, `_execute_s3`, `_execute_s5` (paper path)

---

#### `scale_in_long(symbol, additional_trade_size_pct, leverage)` / `scale_in_short(...)`
Places a market order to add to an existing position. Does **not** update exit orders directly.

**Called from:** `bot.py` — `_do_scale_in` (S2 uses `scale_in_long`, S4 uses `scale_in_short`)

**Note:** After scale-in, `_do_scale_in` fetches the new average entry price, recomputes the trail trigger and (for S2 LONG) the SL cap, then calls `refresh_plan_exits()` with the updated trigger. The position-level SL (`place-pos-tpsl`) auto-scales on Bitget for qty; for S2, `update_position_sl()` is additionally called if the new SL cap is higher than the current SL.

---

#### `refresh_plan_exits(symbol, hold_side, new_trail_trigger=0) → bool`
After scale-in, resizes `profit_plan` and `moving_plan` orders to the current total position qty.

**new_trail_trigger** (optional, default 0): If > 0, the re-placed orders use this as the trigger price instead of preserving the existing order's trigger. Used by `_do_scale_in` to recalculate the trigger from the new average entry after a scale-in fill.

**Steps:**
1. Fetch pending `profit_plan` + `moving_plan` for `hold_side` via `GET /api/v2/mix/order/plan-orders`
2. Cancel both via `POST /api/v2/mix/order/cancel-plan-order`
3. Read current total qty via `get_all_open_positions()`
4. Re-place both — at `new_trail_trigger` if provided (> 0), else at the original trigger from the existing `profit_plan`, split with `_round_qty`

**Does NOT touch:** Position-level SL (`place-pos-tpsl`) — it auto-scales.

**Returns `False` if:** existing orders not found, position not found after scale-in, or all 3 placement retries fail.

**Called from:** `bot.py` — `_do_scale_in` (real mode only, after 1.5s settle delay, in isolated try/except)

**Breaking change:** Changing `profit_plan`/`moving_plan` plan types or `orderId` field name breaks cancellation.

---

## 7. Strategy Implementations

[To be populated in Task 9]

---

## 8. External Tool Dependencies

### 8.1 recover.py

**Purpose:** Manual CLI tool to reconcile all live Bitget positions against `state.json`
and `trades.csv`. Treats `tr.get_all_open_positions()` as source of truth for what positions
exist. Run when the bot is stopped or a position was opened while the bot was down.

**Usage:**
```bash
python recover.py [--dry-run] [--symbols SYM1 SYM2 ...]
```

**Calls:**
- `trader.get_all_open_positions()` — live exchange positions (Pass 1 source of truth)
- `state.get_open_trade(sym)` — read single state entry
- `state.get_open_trades()` — full list for Pass 2 orphan scan
- `state._read()` / `state._write()` — direct state patch (same pattern as `bot.py._startup_recovery`)
- `startup_recovery.fetch_candles_at()` — historical candles for S5 OB recovery
- `startup_recovery.estimate_sl_tp()` — fallback SL/TP for non-S5 strategies
- `startup_recovery.attempt_s5_recovery()` — S5 OB recovery path
- `snapshot.save_snapshot()` — open snapshot for FULL_RECOVERY case
- `config.TRADE_LOG` — trades.csv path

**Two-pass reconciliation logic:**

| Pass | Source | Action |
|---|---|---|
| Pass 1 | Bitget positions → state.json / trades.csv | SKIP / PATCH_SLTP / FULL_RECOVERY per position |
| Pass 2 | state.json open_trades → Bitget | Warn only for orphan state entries |

**Classification (Pass 1):**
- `SKIP` — CSV open row exists AND SL/TP are valid floats > 0
- `PATCH_SLTP` — CSV open row exists BUT SL or TP is bad; patches state.json only, no new CSV row
- `FULL_RECOVERY` — no CSV open row; writes CSV row, patches/adds state.json, saves snapshot

**SL/TP recovery strategy:**
- S5: `attempt_s5_recovery()` first, fall back to `estimate_sl_tp()`
- All others (S1–S4, S6, UNKNOWN): `estimate_sl_tp()` always

**Does NOT affect:**
- `ig_bot.py`, `ig_state.json` — Bitget-only tool
- `bot.py._startup_recovery()` — independent; recover.py is the manual equivalent

**Breaking scenarios:**
- Changing `tr.get_all_open_positions()` return dict structure → Pass 1 loop breaks
- Changing `state._read()` / `state._write()` → state patch fails
- Changing `startup_recovery.estimate_sl_tp()` return tuple → `_patch_sltp` and `_full_recovery` break
- Changing `startup_recovery.attempt_s5_recovery()` return type → S5 path in `_patch_sltp` breaks

---

## 9. Dashboard Integration

[To be populated in Task 11]

---

## 10. Confusing Names & Common Pitfalls

This section documents naming patterns that frequently cause bugs, especially for developers unfamiliar with the codebase.

### 10.1 File Name Confusions

#### state_paper.json vs paper_state.json

**The Problem:**

Two files with similar names but different purposes:

| File | Size | Purpose | Used by | Mode |
|------|------|---------|---------|------|
| `state_paper.json` | 70K | Persistent Bitget bot state (balance, trades, pair analysis) | bot.py, state.py, dashboard.py | Paper trading only |
| `paper_state.json` | 1.7K | Internal paper_trader.py simulation state (position tracking, balance, history) | paper_trader.py | Paper trading only |

**Why they exist:**

- **state_paper.json** — Main state file for paper-trading mode. Contains strategy signals, pair analysis, trade history. This is what the dashboard reads.
- **paper_state.json** — Internal simulation state used by paper_trader.py module to track positions, balance, and trade history. Loaded on startup (paper_trader.py line 28-34), saved on every trade (line 44-45). Persists between restarts.

**Common mistakes:**

1. **Reading the wrong file for strategy signals**
   - Mistake: Loading `paper_state.json` to check S5 signals
   - Fix: Load `state_paper.json` instead; paper_state.json only has position data
   - Impact: Missing strategy context; may think no signals are being generated

2. **Expecting paper_state.json to contain strategy signals**
   - Mistake: Reading paper_state.json to check S5 or other strategy signals
   - Fix: Use state_paper.json for strategy analysis; paper_state.json only has position/balance data
   - Impact: Missing strategy context; may think no signals are being generated

3. **Confusing which file to back up**
   - Mistake: Only backing up paper_state.json before testing
   - Fix: Backup state_paper.json to preserve trading history
   - Impact: Lose trade history, statistics, pair analysis

**Verification:**

```bash
# Check which files exist
ls -lh state_paper.json paper_state.json 2>/dev/null | awk '{print $5, $9}'

# Expected output (Bitget paper mode):
# 70K /Users/kevin/Downloads/bitget_mtf_bot/state_paper.json
# 1.7K /Users/kevin/Downloads/bitget_mtf_bot/paper_state.json

# Read state_paper.json to check pair signals
python -c "import json; s=json.load(open('state_paper.json')); print(list(s['pair_states'].keys())[:3])"

# IG bot only has ig_state.json (no state_paper.json equivalent)
ls -lh ig_state.json 2>/dev/null || echo "IG state file not found (normal if bot hasn't run)"
```

### 10.2 Similar Parameter Names Across Strategies

#### S2_MIN_RR vs S3_MIN_RR vs S5_MIN_RR

**The Problem:**

Each strategy (S2, S3, S5) has its own minimum reward:risk ratio parameter, and they apply at different points in the logic:

| Parameter | Strategy | Minimum RR | Applied where | Default |
|-----------|----------|-----------|----------------|---------|
| `S2_MIN_RR` | Not defined | — | Strategy S2 does not use MIN_RR | N/A |
| `S3_MIN_RR` | S3 (Smart Money Confluence) | 2.0 | strategy.py line 739 (breakout R:R check) | 2.0 |
| `S5_MIN_RR` | S5 (SMC Order Block Pullback) | 2.0 | strategy.py lines 1228, 1297 (R:R checks) | 2.0 |

**Why the confusion:**

1. Parameter naming follows `S{N}_MIN_RR` pattern, but S1/S4 don't have this parameter
2. S2 has NO MIN_RR check (uses fixed TP/SL multipliers instead)
3. Similar names mask different implementations

**Common mistakes:**

1. **Adding S2_MIN_RR to config_s5.py**
   - Mistake: Thinking "all strategies should have MIN_RR"
   - Fix: Check strategy.py to see which strategies actually use it
   - Impact: Unused parameter clutters config; wastes time troubleshooting

2. **Tuning the wrong parameter**
   - Mistake: Changing S5_MIN_RR expecting it to affect S3 entries
   - Fix: Understand that S3 and S5 are independent (edit config_s3.py for S3)
   - Impact: Changes don't take effect; performance doesn't improve

3. **Forgetting to update IG instrument configs when changing S5 defaults**
   - Mistake: Updating S5_MIN_RR in config_s5.py expecting IG to pick it up automatically
   - Fix: IG reads S5 params from per-instrument CONFIG dicts (`config_ig_us30.py`, `config_ig_gold.py`), not from `config_s5.py`. Update each instrument file separately.
   - Impact: Bitget bot uses new value; IG bot continues using old value from its CONFIG dict

**Verification:**

```bash
# Find all MIN_RR definitions
grep "MIN_RR" config_*.py

# Expected:
# config_s3.py:S3_MIN_RR = 2.0
# config_s5.py:S5_MIN_RR = 2.0

# Verify S2 has no MIN_RR
grep "S2_MIN_RR\|MIN_RR" config_s*.py | grep -c S2 || echo "S2 has no MIN_RR (correct)"

# Find where MIN_RR is used in strategy.py
grep -n "MIN_RR" strategy.py
```

### 10.3 Import Timing Traps

#### Module-Level vs Function-Level Config Imports

**Current approach (post multi-instrument migration):**

The `setattr`-based config_s5 patching mechanism that previously existed in `ig_bot.py` has been **removed**. `config_ig_s5.py` has also been **deleted** (its values were absorbed into `config_ig_us30.py`).

IG now passes S5 parameters via the `cfg=instrument` argument to `evaluate_s5()`. Each instrument carries its own complete set of S5 params in its CONFIG dict, eliminating any need to mutate the shared `config_s5` module.

**Why the Bitget-path import remains inside the function:**

`strategy.py`'s `evaluate_s5()` still performs `from config_s5 import ...` INSIDE the function body when `cfg is None`. This is intentional — it preserves the late-binding behaviour for the Bitget and backtest paths (though the IG patching use case no longer applies).

**Common mistakes (updated):**

1. **"Refactoring" the config_s5 import to module-level**
   - Mistake: Moving `from config_s5 import ...` outside the `else:` branch
   - Impact: Technically harmless for IG (IG now uses `cfg`), but breaks any tooling that relied on late-binding for Bitget
   - Why hard to debug: Both bots run without errors; parameter overrides silently stop working

2. **Adding new S5 keys to config_s5.py without adding them to the instrument CONFIG dicts**
   - Mistake: Extending the `cfg is not None` branch in strategy.py but not updating `config_ig_us30.py` / `config_ig_gold.py`
   - Impact: `KeyError` at runtime when IG bot calls evaluate_s5 with the instrument config
   - Fix: Always add new S5 keys to both `config_s5.py` AND each instrument CONFIG file; `_validate_instruments()` will catch missing keys at startup

3. **Expecting config_ig_s5.py to exist**
   - Mistake: Referencing `config_ig_s5.py` in a script or import
   - Impact: `ModuleNotFoundError`
   - Fix: S5 params for IG are now in `config_ig_us30.py` (and `config_ig_gold.py`, etc.)

**Control flow (current):**

```
ig_bot.py startup:
  1. from config_ig import INSTRUMENTS
  2. _validate_instruments()  — KeyError if any required key missing
  3. from strategy import evaluate_s5

ig_bot.py poll loop (per instrument):
  4. Call evaluate_s5(..., cfg=instrument)
     → cfg is not None branch executes
     → reads S5 params from instrument CONFIG dict
     → no config_s5 module involvement

bot.py (Bitget path, unchanged):
  5. Call evaluate_s5(...) — no cfg
     → cfg is None branch executes
     → from config_s5 import ... (late binding, as before)
```

**Verification:**

```bash
# Confirm evaluate_s5 has function-level import for None path
grep -n "from config_s5 import" strategy.py
# Expected: inside the function body (not at top of file)

# Confirm IG patching block is gone
grep -n "setattr.*_cs5_orig\|config_ig_s5" ig_bot.py
# Expected: no matches

# Confirm config_ig_s5.py is deleted
ls config_ig_s5.py 2>/dev/null && echo "WARNING: should be deleted" || echo "Correctly absent"

# Confirm each instrument has its own CONFIG dict
python -c "from config_ig import INSTRUMENTS; [print(i['display_name'], 's5_min_rr:', i['s5_min_rr']) for i in INSTRUMENTS]"
```

### 10.4 CSV Action Name Inconsistency

#### Bitget vs IG CSV Action Formats

**The Problem:**

Bitget bot and IG bot log trade actions with different naming conventions:

| Bot | Action Format | Examples | Notes |
|-----|---------------|----------|-------|
| Bitget | `{STRATEGY}_{SIGNAL}` `{STRATEGY}_{LIFECYCLE}` | `S1_LONG` `S5_LONG` `S5_PARTIAL` `S5_CLOSE` | Strategy-prefixed; mixed signal/lifecycle naming |
| IG | `S5_{LIFECYCLE}` | `S5_LONG` `S5_PARTIAL` `S5_CLOSE` | Only S5; uses same entry names as Bitget |

**Detailed breakdown:**

**Bitget trades.csv (bot.py):**
```
Entry actions:
  S1_LONG, S1_SHORT       (S1 entry signal)
  S2_LONG, S2_SHORT       (S2 entry signal)
  S3_LONG, S3_SHORT       (S3 entry signal)
  S4_LONG, S4_SHORT       (S4 entry signal)
  S5_LONG, S5_SHORT       (S5 entry signal)

Lifecycle actions:
  S1_PARTIAL, S2_PARTIAL, ... (partial TP hit)
  S1_SCALE_IN, S2_SCALE_IN, ... (manual scale-in)
  S1_CLOSE, S2_CLOSE, ... (full exit)
```

**IG ig_trades.csv (ig_bot.py):**
```
Entry actions (S5 only):
  S5_LONG                 (long entry, ig_bot.py line 482)
  S5_SHORT                (short entry, ig_bot.py line 482)

Lifecycle actions:
  S5_PARTIAL              (partial TP hit, ig_bot.py line 571)
  S5_CLOSE                (full exit, ig_bot.py lines 641, 683)
```

**Common mistakes:**

1. **Using optimize.py to analyze ig_trades.csv**
   - Mistake: Running `optimize.py` and passing ig_trades.csv as input
   - Impact: optimize.py expects Bitget CSV format (different action names, columns)
   - Fix: Use `optimize_ig.py` instead for IG trades
   - Verification: Check optimize.py line ~128 for action name parsing

2. **Action name parsing in optimize.py vs optimize_ig.py**
   - Mistake: Assuming both tools parse actions the same way
   - Action check in optimize.py (line ~128): `if "_CLOSE" in action:`
   - Action check in optimize_ig.py (line ~66): `elif action == "S5_PARTIAL":`
   - Impact: Different trade filtering logic; optimize_ig works only on S5
   - Fix: Understand each tool is strategy-specific

3. **Mixing Bitget and IG CSV columns**
   - Mistake: Copying ig_trades.csv columns into trades_paper.csv
   - Bitget has 38 columns; IG has 20 columns
   - Impact: CSV parser crashes or silent data loss
   - Fix: Keep CSV formats separate; never merge them

**Which tool to use:**

```bash
# For Bitget trades (any strategy, all columns)
python optimize.py

# For IG trades (S5 only, minimal columns)
python optimize_ig.py
```

**Verification:**

```bash
# Check Bitget CSV column count
head -1 trades_paper.csv | tr ',' '\n' | wc -l
# Expected: 37 columns

# Check IG CSV column count
head -1 ig_trades.csv | tr ',' '\n' | wc -l
# Expected: 20 columns

# Verify action names are different
head -20 trades_paper.csv | grep "action" | cut -d, -f1-3
# Expected: S1_LONG, S2_LONG, S3_LONG, S5_PARTIAL, S5_CLOSE, etc.

head -20 ig_trades.csv | grep "S5_" | cut -d, -f1-3
# Expected: S5_LONG, S5_SHORT, S5_PARTIAL, S5_CLOSE, etc.
```

---

## Quick Reference: Common Pitfall Quick Fixes

| Pitfall | Symptom | Fix | Verification |
|---------|---------|-----|--------------|
| Confusing state files | Dashboard shows no signals | Check `state_paper.json`, not `paper_state.json` | `python -c "import json; print(json.load(open('state_paper.json'))['pair_states'])"` |
| Tuning wrong MIN_RR | Parameter changes don't help | Update correct config file (S3 vs S5) | `grep MIN_RR config_*.py` |
| Evaluate_s5 cfg not passed | IG bot uses Bitget config_s5 defaults | Pass `cfg=instrument` in ig_bot.py | `grep "evaluate_s5" ig_bot.py` |
| Wrong CSV tool | optimize.py crashes on ig_trades.csv | Use optimize_ig.py for IG | `ls -lh optimize.py optimize_ig.py` |

---

## 11. Maintenance Guide

### When to Update This Document

Update DEPENDENCIES.md immediately after any of these changes:

- [ ] Add/remove a function that's called by multiple files
- [ ] Change a function signature (params or return type)
- [ ] Add/remove fields to state.json or CSV files
- [ ] Change config parameter names
- [ ] Create new shared files
- [ ] Add new cross-bot dependencies
- [ ] Rename files or functions

### How to Verify Dependencies Are Accurate

**After each update:**

```bash
# 1. Both bots import cleanly
python -c "import bot; import ig_bot; print('Import OK')"

# 2. Check actual line numbers match docs
grep -n "evaluate_s5" bot.py ig_bot.py strategy.py

# 3. Check field references in dashboard
grep -n "s2_sr_resistance_pct" dashboard.py dashboard.html

# 4. Run a quick integration test
python -c "import subprocess, sys; subprocess.run([sys.executable, 'bot.py', '--paper'], timeout=10)" 2>/dev/null || echo "Quick test ran for 10s"
```

**Quarterly audit:**

```bash
# Regenerate function call counts
grep "evaluate_" *.py | wc -l

# Check for new JSON files
find . -name "*.json" -type f

# Verify CSV column lists match actual files
head -1 trades_paper.csv
head -1 ig_trades.csv
```

### Document History

- 2026-03-28: Complete dependency documentation system deployed
- 2026-03-31: Added Section 2.2 (snapshot.py); added exit_price to Section 4.2 trades.csv columns (38 fields, was 37)
- 2026-03-31: Updated Section 2.2 — dashboard.py now also calls list_snapshots() + load_snapshot() for all events via /api/trade-chart
- 2026-03-31: S5 SMC Limit Order Entry — evaluate_s5() now returns PENDING_LONG/SHORT (not LONG/SHORT); removed S5_ENTRY_BUFFER_PCT from config import (replaced by S5_MAX_ENTRY_BUFFER as stale OB guard); ig_state.json gained pending_order field; ChoCH removed from S5 strategy logic
- 2026-03-31: Updated Section 4.4 — ig_state.json gained scan_signals (per-instrument S5 signal state) and scan_log (last 20 scan entries, newest first) fields. Written by ig_bot.py _update_scan_state() after each evaluate_s5() call; read by dashboard.py /api/ig/state and rendered by renderIGScanner() + renderIGScanLog() in dashboard.html.
- 2026-04-02: Updated Section 6.1 — open_long/open_short now fetch actual fill price (openPriceAvg) after market order and use it for all exit-level calculations; `entry` in return dict is now fill price not pre-order mark. refresh_plan_exits gained optional new_trail_trigger param. _do_scale_in now recomputes trail trigger and SL (S2 only) from new avg entry after scale-in.
- 2026-04-02: Multi-instrument IG bot — updated Section 1 (architecture diagram shows US30+GOLD loop); Section 2.1 (evaluate_s5 cfg param, dual config resolution paths); Section 4.3 (ig_trades.csv 19→20 columns, symbol at index 3, backward-compat note); Section 4.4 (ig_state.json positions/pending_orders dicts, startup migration); Section 5 (new — per-instrument config architecture, CONFIG shape, _validate_instruments()); Section 10.2 (updated config sync guidance); Section 10.3 (patching mechanism removed, cfg=instrument is the new approach, config_ig_s5.py deleted); Section 10.4 (IG CSV column count 19→20).
- 2026-04-03: S6 V-Formation Liquidity Sweep Short — added evaluate_s6() to Section 2.1 (Bitget-only, 6-tuple return); added S6 fields to Section 4.1 pair_states (s6_signal, s6_reason, s6_peak_level, s6_sl, s6_fakeout_seen); updated trades.csv to 41 columns (+snap_s6_peak, snap_s6_drop_pct, snap_s6_rsi_at_peak); updated Section 1 architecture diagram and signal/strategy fields to include S6.
- 2026-04-10: Liquidity filter — added Section 5.5 (Bitget scanner/liquidity config params). New params LIQUIDITY_CHECK_ENABLED and MIN_OB_DEPTH_USDT in config.py; new private function _filter_by_liquidity() in scanner.py called as last funnel step in get_qualified_pairs_and_sentiment(). Bitget-only; IG unaffected. No state.json, CSV, or return-value changes.
