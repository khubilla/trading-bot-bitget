# Codebase Dependency Map

**Last updated:** 2026-03-31
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
│  Crypto USDT-margined futures · S1-S5 strategies           │
│  Output: state.json, trades.csv (or _paper variants)       │
└─────────────────────────────────────────────────────────────┘
                              ↓
                    ┌─────────────────┐
                    │  strategy.py    │ ← SHARED
                    │  config_s5.py   │ ← SHARED (patched by IG)
                    │  paper_trader.py│ ← SHARED
                    └─────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                      IG BOT (ig_bot.py)                     │
│  US30/Dow CFD · S5 only · 09:30-12:30 ET session          │
│  Output: ig_state.json, ig_trades.csv                      │
└─────────────────────────────────────────────────────────────┘
```

### Shared Code Contract

**Files shared between bots:**
- `strategy.py` — all evaluate_s1 through evaluate_s5 functions
- `config_s5.py` — S5 parameters (Bitget direct, IG patched with config_ig_s5)
- `paper_trader.py` — simulation engine (used by Bitget bot only in paper mode)

**Separation rules:**
- Changes to shared files require testing BOTH bots
- Bitget and IG must work independently (no cross-bot state)
- config_s5 changes affect Bitget immediately; IG overrides via config_ig_s5

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

**Purpose:** Contains all strategy evaluation logic (S1-S5) shared by both Bitget and IG bots.

**Used by:**
- `bot.py` (Bitget bot) — imports and calls evaluate_s1 through evaluate_s5
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
) -> tuple[Signal, float, float, float, float, float, str]:
```

**Defined:** `strategy.py` line 1067

**Called by:**
- `bot.py` line 585 — `s5_sig, s5_trigger, s5_sl, s5_tp, s5_ob_low, s5_ob_high, s5_reason = evaluate_s5(...)`
- `ig_bot.py` line 415 — `sig, trigger, sl, tp, ob_low, ob_high, reason = evaluate_s5(...)`

**Dependencies:**
- Line 1085: Performs LATE import of config_s5 module
  ```python
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
  This import happens at CALL TIME, not module load time.

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
2. Modifying config_s5 parameter names without updating the import statement
3. Moving the config_s5 import to module-level (breaks IG patching mechanism)
4. Changing return tuple structure (adding/removing/reordering elements)

#### Config Import Timing — CRITICAL

**Why the import is inside the function:**

The `from config_s5 import ...` statement occurs at LINE 1085, INSIDE the function body. This is NOT a refactoring oversight — it's an intentional design that enables `ig_bot.py` to patch config_s5 parameters before evaluate_s5() reads them.

**Patching mechanism in ig_bot.py (lines 25-37):**

```python
# Apply US30-specific S5 params — must happen before strategy.evaluate_s5() is imported
# or called, since evaluate_s5() does `from config_s5 import ...` at call time.
import config_s5 as _cs5_orig
import config_ig_s5 as _cs5_ig
_base_attrs = {a for a in dir(_cs5_orig) if not a.startswith('_')}
for _attr in [a for a in dir(_cs5_ig) if not a.startswith('_')]:
    if _attr not in _base_attrs:
        raise AttributeError(
            f"config_ig_s5.{_attr} has no matching attribute in config_s5 — "
            f"check for a typo in config_ig_s5.py"
        )
    setattr(_cs5_orig, _attr, getattr(_cs5_ig, _attr))
del _cs5_orig, _cs5_ig, _attr, _base_attrs
```

**Flow:**
1. ig_bot.py imports config_s5 module
2. ig_bot.py patches config_s5's attributes with config_ig_s5 values using setattr()
3. ig_bot.py imports evaluate_s5 from strategy.py
4. When evaluate_s5() is called, it performs `from config_s5 import ...` and receives the PATCHED values

**If you move the import to module-level:**
- strategy.py would import config_s5 when the module loads
- ig_bot.py's patching would happen AFTER strategy.py has already imported the original values
- evaluate_s5() would see Bitget parameters, not US30-tuned IG parameters
- IG bot would use wrong parameters → trades fail

#### Critical Invariants

1. The config_s5 import MUST remain inside evaluate_s5() at call time
2. All bots must call evaluate_s5() with the same signature
3. Return tuple order must remain stable: (signal, trigger, sl, tp, ob_low, ob_high, reason)
4. config_ig_s5 must be a SUBSET of config_s5 attributes (enforced by ig_bot.py)

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

**Note:** Additional strategy functions (evaluate_s1, evaluate_s2, etc.) can be added to this section as needed for comprehensive dependency documentation.

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
}
```

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

  # Shared S/R fields
  "sr_resistance_pct": float | None,
  "sr_support_pct": float | None,

  # Metadata
  "signal": str,              # Aggregate signal (first non-HOLD from S1-S5)
  "strategy": str,            # "S1" | "S2" | "S3" | "S4" | "S5"
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

**Columns (38 fields):**

```csv
timestamp,trade_id,action,symbol,side,qty,entry,sl,tp,
box_low,box_high,leverage,margin,tpsl_set,strategy,
snap_rsi,snap_adx,snap_htf,snap_coil,snap_box_range_pct,snap_sentiment,
snap_daily_rsi,
snap_entry_trigger,snap_sl,snap_rr,
snap_rsi_peak,snap_spike_body_pct,snap_rsi_div,snap_rsi_div_str,
snap_s5_ob_low,snap_s5_ob_high,snap_s5_tp,
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

**Columns (19 fields):**

```csv
timestamp,trade_id,action,
side,qty,entry,sl,tp,
snap_entry_trigger,snap_sl,snap_rr,
snap_s5_ob_low,snap_s5_ob_high,snap_s5_tp,
result,pnl,exit_reason,
session_date,mode
```

**Key differences from Bitget CSV:**
- **Fewer columns:** IG only uses S5 strategy, so S1-S4 snapshot fields are omitted
- **Additional fields:** `session_date` (YYYY-MM-DD), `mode` (PAPER | LIVE)
- **No symbol column:** IG bot only trades US30 (single instrument)
- **No leverage/margin:** IG uses fixed position sizing via config_ig

**Breaking scenarios:**

1. **Changing _TRADE_FIELDS order** → CSV header misaligns
   - Fix: Update _TRADE_FIELDS at ig_bot.py line 65

2. **Removing mode or session_date** → optimize_ig.py fails to filter trades
   - Fix: Update _TRADE_FIELDS and all _log_trade calls in ig_bot.py

3. **Renaming columns** → Claude API optimization prompts break
   - Fix: Update optimize_ig.py prompt template

**Reader:**
- `optimize_ig.py` — sends completed trades to Claude API for parameter tuning

**Verification commands:**

```bash
# Check IG CSV exists (may not exist if bot never ran)
ls -lh ig_trades.csv 2>/dev/null || echo "IG CSV not yet created"

# Check column definition
sed -n '65,72p' ig_bot.py

# Find _log_trade callers in IG bot
grep -n "_log_trade" ig_bot.py
```

---

### 4.4 ig_state.json

**Purpose:** Minimal state file for IG bot position persistence (no state.py module).

**Write chain:**
- `ig_bot.py`: Direct JSON writes (no state.py abstraction)
- IG bot manages state manually due to single-instrument simplicity

**Read chain:**
- `dashboard.py` line 494: `position = json.load(f).get("position")` — loads current US30 position
- `ig_bot.py`: Loads position on startup to resume after restart

**Structure:**
```python
{
  "position": {
    "trade_id": str,
    "side": "LONG" | "SHORT",
    "qty": float,
    "entry": float,
    "sl": float,
    "tp": float,
    "opened_at": str,  # ISO timestamp
  } | None,
  "pending_order": {
    "deal_id": str,
    "side": "LONG" | "SHORT",
    "ob_low": float,
    "ob_high": float,
    "sl": float,
    "tp": float,
    "trigger": float,
    "size": float,
    "expires": float,   # Unix timestamp
  } | None,
  "scan_signals": {
    "<DISPLAY_NAME>": {   # e.g. "US30"
      "signal":        str,   # "PENDING_LONG" | "PENDING_SHORT" | "HOLD"
      "reason":        str,   # full evaluate_s5 reason string
      "ema_ok":        bool,
      "bos_ok":        bool,
      "ob_ok":         bool,
      "ob_low":        float | None,
      "ob_high":       float | None,
      "entry_trigger": float | None,
      "sl":            float | None,
      "tp":            float | None,
      "updated_at":    str,   # ISO timestamp (UTC)
    }
  },
  "scan_log": [           # last 20 entries, newest first
    {
      "ts":         str,  # "HH:MM" in ET timezone
      "instrument": str,  # e.g. "US30"
      "message":    str,  # evaluate_s5 reason string
    }
  ]
}
```

**Breaking scenarios:**

1. **Renaming "position" key** → Dashboard IG panel crashes
   - Fix: Update dashboard.py line 494

2. **Changing position field names** → IG bot fails to resume position after restart
   - Fix: Update all ig_bot.py position reads/writes

3. **Removing or renaming "pending_order" key** → ig_bot.py fails to restore pending orders after restart
   - Fix: Update ig_bot.py _save_state() and _sync_live_position() load paths

4. **Renaming "scan_signals" key** → Dashboard IG scanner panel shows no cards
   - Fix: Update dashboard.py get_ig_state() `.get("scan_signals", {})` and renderIGScanner() call

5. **Changing scan_signals entry field names** → Dashboard cards show wrong or missing check values
   - Fix: Update renderIGScanner() in dashboard.html to match new field names

6. **Renaming "scan_log" key** → Dashboard scan log panel stays empty
   - Fix: Update dashboard.py get_ig_state() `.get("scan_log", [])` and renderIGScanLog() call

**Verification commands:**

```bash
# Check IG state structure
python -c "import json; print(json.load(open('ig_state.json')))"

# Find dashboard reads
grep -n "ig_state.json" dashboard.py
```

---

## 5. Config Dependencies

[To be populated in Task 8]

---

## 6. Function Call Graph

### 6.1 trader.py — Exit Order Functions

These functions place and manage exit orders on Bitget. All are called from `bot.py` only (not `ig_bot.py`).

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

#### `open_long(symbol, box_low, sl_floor, leverage, trade_size_pct, take_profit_pct, stop_loss_pct, use_s2_exits, use_s5_exits, tp_price_abs)`
Opens a LONG market order then places exits based on the `use_*_exits` flag.

**Fill price:** After placing the market order and sleeping 2s, calls `get_all_open_positions()` to get `openPriceAvg` as the actual fill price. All exit-level calculations (SL, TP, trail trigger, partial TP) use this fill price. Falls back to the pre-order mark price if the position is not yet visible. The pre-order mark is still used for qty sizing.

**SL cap (S2 path):** `sl_trig = max(box_low * 0.999, fill * (1 - stop_loss_pct))` — prevents box_low from being so far below entry that the SL exceeds the intended max loss (e.g. `-50%` at 10x for `S2_STOP_LOSS_PCT=0.05`).

**Returns:** dict with `{symbol, side, qty, entry, sl, tp, box_low, leverage, margin, tpsl_set}` — `entry` is the actual fill price from `get_all_open_positions()` (falls back to pre-order mark if position not yet visible).

**Called from:** `bot.py` — `_execute_s2`, `_execute_s3`, `_execute_s5` (paper path)

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

[To be populated in Task 10]

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

3. **Forgetting to sync between config_s5.py and config_ig_s5.py**
   - Mistake: Updating S5_MIN_RR in config_s5.py but not config_ig_s5.py
   - Fix: If changing S5_MIN_RR, update BOTH files if IG override is intended
   - Impact: Bitget bot uses new value; IG bot uses old default (or vice versa)

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

**The Problem:**

`strategy.py`'s `evaluate_s5()` function imports config_s5 INSIDE the function body (line 1085), not at module load time. This is intentional but fragile.

**Why it matters:**

The IG bot patches `config_s5` module attributes at startup (ig_bot.py lines 27-36):

```python
# ig_bot.py: Patch config_s5 before calling evaluate_s5()
import config_s5 as _cs5_orig
import config_ig_s5 as _cs5_ig
_base_attrs = {a for a in dir(_cs5_orig) if not a.startswith('_')}
for _attr in [a for a in dir(_cs5_ig) if not a.startswith('_')]:
    if _attr not in _base_attrs:
        raise AttributeError(
            f"config_ig_s5.{_attr} has no matching attribute in config_s5 — "
            f"check for a typo in config_ig_s5.py"
        )
    setattr(_cs5_orig, _attr, getattr(_cs5_ig, _attr))
del _cs5_orig, _cs5_ig, _attr, _base_attrs
```

If the import moved to module-level, the patching would fail because the local binding would be cached:

```
BAD (module-level import):
─────────────────────────
strategy.py module loads
  → executes: from config_s5 import S5_MIN_RR
  → creates local binding: S5_MIN_RR = 2.0 (Bitget default)

ig_bot.py starts
  → patches config_s5 module
  → config_s5.S5_MIN_RR = 1.5 (IG override)

evaluate_s5() called
  → uses already-cached local binding (2.0)
  → IG override ignored because the import created a local name binding

Note: `import config_s5` (module reference) would work, but `from config_s5
import X` (local binding) would fail. The issue is specifically with the
local binding being cached before patching occurs.
```

**Common mistakes:**

1. **"Refactoring" the import to module-level**
   - Mistake: Moving `from config_s5 import ...` outside the function
   - Impact: IG bot stops using IG parameters; uses Bitget defaults instead
   - Why hard to debug: Both bots run without errors, but IG trades fail silently

2. **Calling evaluate_s5() before patching (IG bot only)**
   - Mistake: Importing `from strategy import evaluate_s5` before ig_bot.py patches
   - Impact: IG bot sees Bitget parameters on first call
   - Why hard to debug: Works fine on second call (after patching); fails intermittently

3. **Forgetting the patching mechanism exists**
   - Mistake: Wondering why IG config_ig_s5.py seems ignored
   - Fix: Check ig_bot.py lines 25-37 to see patching in action
   - Why hard to debug: Parameter changes in config_ig_s5.py mysteriously don't take effect

**Control flow (correct):**

```
ig_bot.py startup:
  1. import config_s5 as _cs5_orig
  2. import config_ig_s5 as _cs5_ig
  3. Loop: setattr(config_s5, attr, config_ig_s5_value)
  4. from strategy import evaluate_s5
  5. Call evaluate_s5(...)
     → evaluate_s5() executes: from config_s5 import ...
     → Reads PATCHED values
```

**Verification:**

```bash
# Confirm evaluate_s5 has function-level import
sed -n '1067,1095p' strategy.py | grep -A 20 "def evaluate_s5"

# Expected: line 1085 should show "from config_s5 import"

# Verify IG bot patching happens first
sed -n '1,50p' ig_bot.py | grep -A 15 "import config_s5 as"

# Expected: ig_bot patching at lines 25-37, import evaluate_s5 at line 39+
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
   - Bitget has 37 columns; IG has 19 columns
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
# Expected: 19 columns

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
| Evaluate_s5 import moved | IG bot ignores config_ig_s5 | Keep import inside function | `sed -n '1085p' strategy.py` |
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
