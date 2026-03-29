# Comprehensive Dependency Documentation System

**Date:** 2026-03-28
**Status:** Approved
**Purpose:** Prevent fragile iterations by documenting all dependencies, data contracts, and breaking change scenarios across the entire codebase.

---

## Problem Statement

The codebase has grown to include two independent bots (Bitget and IG), five strategies with shared code, multiple data contracts (state.json, CSV files), and external tools that consume these formats. Changes to shared files or data structures can break consumers in non-obvious ways:

- Changing `strategy.py` can break both bots
- Modifying `config_s5.py` affects Bitget directly but IG via a patching mechanism
- Removing state.json fields breaks dashboard rendering
- Changing CSV columns breaks optimize.py and backtest.py
- Confusing file names (paper_state.json vs state_paper.json) cause mistakes

Without a comprehensive dependency map, every change risks breaking something downstream.

---

## Solution

Create two documents that work together as a system:

1. **PRE_CHANGE_CHECKLIST.md** — Short mandatory checklist run before every change
2. **DEPENDENCIES.md** — Comprehensive catalog documenting every dependency at function/field level

The checklist forces a review workflow; the dependency map provides the detailed answers.

---

## Document 1: PRE_CHANGE_CHECKLIST.md

**Location:** `docs/PRE_CHANGE_CHECKLIST.md`

**Purpose:** Mandatory pre-flight check ensuring no dependencies are overlooked

**Structure:**

### Step 1: File Type Identification
Checkbox list of file categories with pointers to dependency map sections:
- Shared files (strategy.py, config_s5.py, etc.) → § 2
- Config files → § 5
- Bot files → § 3
- Dashboard files → § 9
- Tool files → § 8
- Data structures → § 4

### Step 2: Change Scope
Checkbox list of change types:
- Function signatures
- Field names/types
- Import statements
- Strategy logic
- Config parameters

### Step 3: Dependency Lookup
For each checked box in Steps 1-2, directs to specific DEPENDENCIES.md sections

### Step 4: Verification Commands
If shared files touched:
```bash
python -c "import bot; print('Bitget OK')"
python -c "import ig_bot; print('IG OK')"
```

### Step 5: Data Consumer Verification
If data structures touched, check specific line numbers in:
- dashboard.py (lines 60-75, 310-350)
- dashboard.html (lines 1224-2400)
- optimize.py (lines 82-94)
- optimize_ig.py (lines 95-103)

### Step 6: Document Maintenance
After changes, update DEPENDENCIES.md if new dependencies added

---

## Document 2: DEPENDENCIES.md

**Location:** `docs/DEPENDENCIES.md`

**Purpose:** Comprehensive catalog of all dependencies, data contracts, and breaking scenarios

**Estimated size:** 800-1200 lines when complete

**Update frequency:** After every PR that changes interfaces, data contracts, or cross-file dependencies

---

## Section Breakdown

### Section 1: Architecture Overview

**Content:**
- ASCII diagram showing two-bot architecture (Bitget + IG)
- Shared vs bot-specific file breakdown
- Separation contract (what must stay independent)
- High-level data flow (bot → state → dashboard, bot → CSV → optimizer)

**Purpose:** Orient readers to the system before diving into details

---

### Section 2: Shared Files (Cross-Bot)

**Coverage:** strategy.py, config_s5.py, paper_trader.py, state.py, scanner.py

**Detail level per file:**

#### For strategy.py:
- **Purpose statement**
- **Used by:** List of files with line numbers where imports occur
- **Function catalog:** Every exported function with:
  ```
  function_name(params) → return_type
    Defined in: strategy.py line X
    Called by:
      - bot.py line Y (context: inside _tick loop)
      - ig_bot.py line Z (context: main evaluation)
      - backtest.py line W (context: backtest loop)
    Depends on:
      - Config params: list with line numbers of import statements
      - Other functions: list with call sites
    Returns: exact tuple structure with field meanings
    Breaking scenarios: what breaks if signature changes
  ```
- **Critical invariants:** Rules that must never be violated (e.g., signal values, DataFrame column names)
- **Verification commands:** Commands to run after changes

**Example function entry:**
```
evaluate_s5(symbol, daily_df, htf_df, ltf_df, allowed_direction) → (signal, adx, entry_trigger, sl_price, reason)
  Defined in: strategy.py line 1089
  Called by:
    - bot.py line 590 (_evaluate_pair context, all qualified pairs)
    - ig_bot.py line 400 (_tick context, US30 only)
  Depends on:
    - config_s5: S5_ENABLED, S5_OB_LOOKBACK, S5_OB_MIN_IMPULSE (imported line 1090)
    - Functions: find_swing_low_target (line 1200), calculate_ema (line 1150)
  Returns: (signal, adx, entry_trigger, sl_price, reason)
    - signal: "LONG" | "SHORT" | "PENDING_LONG" | "PENDING_SHORT" | "HOLD"
    - reason: human-readable string for logs/dashboard
  Breaking scenarios:
    - Change return tuple length → bot.py/ig_bot.py unpacking breaks
    - Remove reason field → dashboard error messages incomplete
    - Change signal enum values → execution logic breaks
```

#### For config_s5.py:
- **Purpose statement**
- **Patching mechanism:** How ig_bot.py overrides with config_ig_s5.py values at startup
- **Parameter catalog:** Every parameter with:
  - Name, type, default value
  - Who imports it (file + line number)
  - Used where (line numbers in strategy.py or bot.py)
  - IG override value (from config_ig_s5.py)
- **Import timing explanation:** Module-load vs function-call imports (critical for patching)
- **Breaking scenarios**

---

### Section 3: Bot-Specific Files

**Coverage:** bot.py (Bitget), ig_bot.py (IG)

**Detail level per bot:**
- **Dependencies:** All imports with line numbers
- **Entry points:** Main classes and functions (MTFBot._tick, etc.)
- **Output files:** state.json, trades.csv (or _paper/_ig variants)
- **Strategy integration:** Where each evaluate_* function is called (line numbers)
- **Data flow:** How data moves through the bot (scan → evaluate → rank → execute)

---

### Section 4: Data Contracts

**Coverage:** state.json, state_paper.json, paper_state.json, trades.csv, trades_paper.csv, ig_trades.csv, ig_state.json

**Detail level per file:**

#### state.json / state_paper.json structure:

**Write chain:**
```
bot.py _tick() line 200
  → StateManager.update_pair_state(symbol, {...}) line 636
  → state.py StateManager.update() line 45
  → writes to state_paper.json (atomic write via temp file)
```

**Read chain:**
```
dashboard.py /api/state endpoint line 60
  → reads state_paper.json line 65
  → injects trade_history from CSV line 70
  → injects strategy_enabled from configs line 75
  → returns JSON to frontend line 85

dashboard.html JavaScript
  → polls /api/state every 3s line 1395
  → render(data) line 1400
  → updates UI elements lines 1224-2400
```

**Field catalog format:**
```
field_name: type
  Written by: file.py line X (function context)
  Read by: file.py line Y, file.html line Z
  Purpose: what it's used for
  Breaking scenarios: what breaks if removed/renamed/changed type
```

**All top-level fields documented:** status, started_at, last_tick, balance, open_trades, qualified_pairs, pair_states, trade_history (injected), strategy_enabled (injected)

**All pair_states[symbol] fields documented:**
- Core: close, rsi, htf_bull, htf_bear
- S1: s1_signal, s1_bh, s1_bl, s1_rsi, etc.
- S2: s2_signal, s2_coiling, s2_box_low, s2_box_high, s2_sr_resistance_pct, s2_sr_resistance_price
- S3: s3_signal, s3_sr_resistance_pct, s3_sr_resistance_price
- S4: s4_signal, s4_sr_support_pct
- S5: s5_signal, s5_entry_trigger, s5_ob_low, s5_ob_high, s5_priority_rank, s5_priority_score
- Generic: sr_resistance_pct, sr_support_pct

#### CSV file structures:

**trades.csv / trades_paper.csv:**
- **Writer:** bot.py _log_trade() line 180
- **Write triggers:** open, close, partial close, scale-in
- **Complete column list** with types
- **Readers:**
  1. dashboard.py line 31 (_load_csv_history)
     - Used by: dashboard.html trade history panel line 1620
  2. optimize.py line 108 (load_trades)
     - Expected columns per strategy (STRATEGY_COLUMNS dict line 82-94)
     - Pairing logic (OPEN + CLOSE rows)
- **Breaking scenarios:** Which columns can't be removed without breaking consumers

**ig_trades.csv:**
- **Writer:** ig_bot.py _log_trade() line 69
- **Complete column list** (different from Bitget)
- **Differences:** No symbol column, pnl in USD not pct, S5_* action names
- **Reader:** optimize_ig.py line 45 (load_trades)
  - Pairing logic (S5_OPEN + S5_PARTIAL + S5_CLOSE)
  - PnL aggregation (partial + close)

#### paper_state.json vs state_paper.json clarification:
Document the confusing naming and completely different purposes (see Section 10)

---

### Section 5: Config Dependencies

**Coverage:** config.py, config_s1.py through config_s5.py, config_ig.py, config_ig_s5.py

**Detail level per file:**
- **Purpose statement**
- **Every parameter:**
  - Name, type, default value
  - Who imports it (file + line number)
  - Used where (line numbers in strategy/bot code)
  - Related parameters in other configs (e.g., S2_MIN_RR vs S3_MIN_RR — same concept, different values)
- **Special cases:**
  - config_s5 patching (IG bot overrides)
  - config_ig_s5 US30-specific tuning rationale (why 50→20 for OB_LOOKBACK, etc.)

---

### Section 6: Function Call Graph

**Coverage:** Every critical function in the codebase

**Detail level per function:**
```
function_name(params) → return_type
  Defined in: file.py line X
  Called by:
    - file.py line Y (context description)
    - file2.py line Z (context description)
  Depends on:
    - Config params: list with import line numbers
    - Other functions: list with call line numbers
  Returns: exact structure with field meanings
  Breaking scenarios: specific examples of what breaks
```

**Functions to document:**
- All evaluate_s1 through evaluate_s5
- All indicator functions (calculate_ema, calculate_rsi, calculate_adx, calculate_stoch, calculate_macd)
- All S/R functions (find_nearest_resistance, find_nearest_support, find_spike_base)
- All trade management (open_long, open_short, update_position_sl, partial_close, scale_in)
- State management (update_pair_state, update_global_state)
- Scanner functions (get_qualified_pairs_and_sentiment, _fetch_tickers_bitget)
- Paper trader functions (simulate_order, check_sl_tp, update_positions)
- Dashboard functions (_load_csv_history, get_candles, get_state)

---

### Section 7: Strategy Implementations

**Coverage:** S1 through S5 strategy logic

**Detail level per strategy:**
- **Implementation location:** File + function name
- **Config files:** Which config file(s) control it
- **Used by:** Which bot(s) (Bitget only, IG only, or both)
- **Entry conditions:** Fields/functions that determine entry (high-level summary)
- **Exit conditions:** SL/TP placement logic, trailing stops, partial closes
- **State fields written:** Which pair_states fields this strategy populates
- **Shared functions:** Cross-strategy dependencies (e.g., S2 and S5 both use find_nearest_resistance)
- **Breaking change scenarios**

**Special note for S5:**
- Document that it's used by both bots but with different configs
- Bitget: config_s5.py (crypto defaults)
- IG: config_ig_s5.py (US30-tuned) patched into config_s5 at startup

---

### Section 8: External Tool Dependencies

**Coverage:** optimize.py, optimize_ig.py, backtest.py

**Detail level per tool:**
- **Purpose statement**
- **Input files:** Which files it reads (with exact paths)
- **CSV columns expected:** List with line numbers where accessed
- **Config dependencies:** Which config params it reads (e.g., optimize.py CURRENT_PARAMS dict)
- **Output:** What it produces (terminal output, files, etc.)
- **Breaking scenarios:** What CSV/config changes break it

**Example for optimize.py:**
```
optimize.py — Claude-powered parameter optimizer

Reads: trades.csv OR trades_paper.csv
  - Selected via --paper flag
  - Loaded by load_trades() line 108
  - Pairing logic: matches OPEN with *_CLOSE rows by symbol

CSV columns expected:
  - result (line 134) — "WIN" or "LOSS"
  - pnl_pct (line 136) — percentage gain/loss
  - exit_reason (line 137) — why trade closed
  - strategy (line 159) — "S1" through "S5"
  - Per-strategy snapshot fields (STRATEGY_COLUMNS dict lines 82-94):
    - S1: snap_rsi, snap_adx, snap_sentiment, snap_box_range_pct
    - S2: snap_daily_rsi, snap_sentiment, snap_sr_clearance_pct, snap_box_range_pct
    [etc.]

Config dependency:
  - CURRENT_PARAMS dict (lines 23-78) must match config_s*.py values
  - Shown to Claude in prompt so it knows baseline
  - If param removed from config, remove from CURRENT_PARAMS

Breaking scenarios:
  - Remove "result" column → line 134 KeyError
  - Rename "pnl_pct" → line 136 KeyError
  - Remove strategy-specific snap field → table formatting incomplete
  - CURRENT_PARAMS out of sync → Claude gets wrong baseline
```

---

### Section 9: Dashboard Integration

**Coverage:** dashboard.py and dashboard.html

**Detail level:**

#### dashboard.py endpoints:
- **/api/state** (line 60)
  - Reads: state.json or state_paper.json
  - Injects: trade_history from CSV (line 70), strategy_enabled from configs (line 75)
  - S/R overrides: Lines 310-350 override pair_states S/R values for S2/S3 charts
  - Returns: JSON to frontend

- **/api/candles/{symbol}** (line 77)
  - Computes: indicators for chart (Stoch, MACD, EMA)
  - S3 evaluation: Calls evaluate_s3 to get entry trigger/SL (line 341)
  - Returns: Candle data + indicator series + S3/S5 overlays

- **/api/ig/state** (line 460)
  - IG bot state endpoint (separate from Bitget)

#### dashboard.html consumption:
- **Polling:** Line 1395 fetches /api/state every 3s
- **Rendering:** Line 1400 calls render(data)
- **State fields used:** Document every pair_states field access with line numbers
  - Line 1785: pair grid card rendering
  - Line 1850: S2 signal badges
  - Line 1900: S3 signal badges
  - Line 2000: S5 signal badges
  - Line 2171: S3 chart status label (uses s3_signal_live)
- **Tab visibility:** Line 1236 (strategy_enabled logic)
- **Trade history:** Line 1620 (trade_history panel)

---

### Section 10: Confusing Names & Common Pitfalls

**Purpose:** Document naming ambiguities and patterns that cause confusion

**Content:**

#### File name confusions:

**paper_state.json vs state_paper.json:**
```
❌ TRAP: Names look like duplicates but completely different purposes

state_paper.json (64KB, actively updated)
  Purpose: Bot state for paper mode (same structure as state.json)
  Written by: bot.py → StateManager → state.py
  Read by: dashboard.py
  Structure: {status, balance, open_trades, pair_states, ...}
  Update frequency: Every 3s (every bot tick)

paper_state.json (417 bytes, rarely changes)
  Purpose: Paper trader's INTERNAL simulation state
  Written by: paper_trader.py line 22
  Read by: paper_trader.py only (loads on startup, saves on trades)
  Structure: {balance, positions, history, total_pnl}
  Update frequency: Only when paper trades execute

⚠️ Breaking different things:
  - Break state_paper.json → dashboard shows no data in paper mode
  - Break paper_state.json → paper simulation loses state across restarts

✅ How to remember:
  - state_*.json = bot state (main system)
  - paper_state.json = paper trader internals (subsystem)
```

#### Similar parameter names:
```
S2_MIN_RR vs S3_MIN_RR vs S5_MIN_RR
  - Same concept (minimum reward:risk ratio)
  - Same value (all 2.0 by default)
  - Live in different config files
  - NOT shared — changing one doesn't affect others
  - Each strategy independently checks its own value
```

#### Import timing traps:

**config_s5 module load vs function call imports:**
```
❌ TRAP: Not all config_s5 imports happen at the same time

Module-load imports (ig_bot.py lines 24-28):
  from config_s5 import (
      S5_DAILY_EMA_FAST, S5_DAILY_EMA_SLOW,
      S5_USE_CANDLE_STOPS, S5_SL_BUFFER_PCT,
  )
  ✅ These are frozen at startup
  ✅ ig_bot.py must import from config_ig_s5 instead for US30 values

Function-call imports (strategy.py evaluate_s5 line 1090):
  from config_s5 import (
      S5_ENABLED, S5_OB_LOOKBACK, S5_OB_MIN_IMPULSE, ...
  )
  ✅ These read current module values at call time
  ✅ ig_bot.py patches config_s5 at startup before calling evaluate_s5

⚠️ If you add a new S5 param:
  1. Add to config_s5.py
  2. Add to config_ig_s5.py (with US30-tuned value if different)
  3. Import inside evaluate_s5 function body (not at module level)
  4. If ig_bot needs it directly, import from config_ig_s5 at module level
```

#### CSV action name inconsistency:
```
Bitget CSV (trades.csv):
  Action values: "OPEN", "CLOSE", "PARTIAL_CLOSE", "SCALE_IN"

IG CSV (ig_trades.csv):
  Action values: "S5_OPEN", "S5_CLOSE", "S5_PARTIAL", "S5_SL", "S5_TP"

❌ TRAP: Different parsers expect different action names
  - optimize.py expects Bitget format (checks action at line 128)
  - optimize_ig.py expects IG format (checks action at line 62)

⚠️ Don't try to unify these — tools are specialized per bot
```

---

### Section 11: Maintenance Guide

**Content:**

#### When to update this document:
Checklist of change types that require documentation updates:
- [ ] Add/remove a function called by multiple files
- [ ] Change function signature (params or return type)
- [ ] Add/remove fields to state.json or CSV files
- [ ] Change config parameter names
- [ ] Create new shared files
- [ ] Add new cross-bot dependencies
- [ ] Rename files or functions

#### How to verify dependencies are accurate:

**After each update:**
```bash
# 1. Both bots import cleanly
python -c "import bot; import ig_bot; print('Import OK')"

# 2. Check actual line numbers match docs
grep -n "evaluate_s5" bot.py ig_bot.py strategy.py

# 3. Check field references in dashboard
grep -n "s2_sr_resistance_pct" dashboard.py dashboard.html

# 4. Run a quick integration test
python bot.py --paper & sleep 10 && kill %1
```

**Quarterly audit:**
```bash
# Regenerate function call counts
grep -r "evaluate_" *.py | wc -l

# Check for new JSON files
find . -name "*.json" -type f

# Verify CSV column lists match actual files
head -1 trades_paper.csv
head -1 ig_trades.csv
```

#### Document history:
```
- 2026-03-28: Initial creation
- [Future updates logged here with date and what changed]
```

---

## Implementation Plan

### Phase 1: Write skeleton documents
1. Create `docs/PRE_CHANGE_CHECKLIST.md` with full structure
2. Create `docs/DEPENDENCIES.md` with section headers and TOC

### Phase 2: Populate DEPENDENCIES.md
1. Section 1: Architecture overview (diagrams, high-level)
2. Section 2: Shared files (strategy.py, config_s5.py, etc.)
3. Section 4: Data contracts (state.json, CSV files — critical)
4. Section 6: Function call graph (all evaluate_* and critical helpers)
5. Section 5: Config dependencies
6. Section 3: Bot-specific files
7. Section 7: Strategy implementations
8. Section 8: External tool dependencies
9. Section 9: Dashboard integration
10. Section 10: Confusing names & pitfalls
11. Section 11: Maintenance guide

### Phase 3: Verification
1. Run through checklist with a test change
2. Verify line numbers are accurate (grep checks)
3. Test that both bots still import cleanly
4. Commit both documents

---

## Success Criteria

1. **Checklist prevents mistakes:** Every change goes through the checklist first
2. **Dependency map answers questions:** When checklist flags something, the map has the answer
3. **No orphaned code:** Every function/field documented with its consumers
4. **Breaking changes obvious:** Each field/function lists what breaks if changed
5. **Both bots protected:** Shared file changes can't break a bot without explicit warning
6. **Data contracts clear:** Dashboard and tools won't break from state/CSV changes
7. **Naming confusion eliminated:** paper_state.json vs state_paper.json never confuses again

---

## Estimated Effort

- **PRE_CHANGE_CHECKLIST.md:** ~50 lines
- **DEPENDENCIES.md:** ~1000 lines (comprehensive catalog)
- **Time to populate:** 2-3 hours for initial creation, 5-10 minutes per update thereafter
- **Maintenance burden:** Low — only update when interfaces change

---

## Non-Goals

- Auto-generate from code (too complex, loses context)
- Track implementation details (just interfaces/contracts)
- Replace code comments (complements, doesn't replace)
- Document business logic (just technical dependencies)
