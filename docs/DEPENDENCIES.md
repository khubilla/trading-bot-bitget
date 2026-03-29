# Codebase Dependency Map

**Last updated:** 2026-03-29
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
- `backtest.py` — historical strategy testing

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
      S5_ENTRY_BUFFER_PCT, S5_SL_BUFFER_PCT,
      S5_MIN_RR, S5_SWING_LOOKBACK,
      S5_OB_MIN_RANGE_PCT, S5_SMC_FVG_FILTER, S5_SMC_FVG_LOOKBACK,
  )
  ```
  This import happens at CALL TIME, not module load time.

**Return values:**
```python
(signal, entry_trigger, sl_price, tp_price, ob_low, ob_high, reason)
# signal: "LONG" | "SHORT" | "HOLD"
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

**Patching mechanism in ig_bot.py (lines 24-36):**

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
grep -n "evaluate_s5" bot.py ig_bot.py backtest.py

# Verify config import is inside function
sed -n '1067,1095p' strategy.py | grep "from config_s5 import"

# Check IG patching mechanism
sed -n '24,36p' ig_bot.py
```

**Note:** Additional strategy functions (evaluate_s1, evaluate_s2, etc.) can be added to this section as needed for comprehensive dependency documentation.

---

## 3. Bot-Specific Files

[To be populated in Task 7]

---

## 4. Data Contracts

[To be populated in Task 5]

---

## 5. Config Dependencies

[To be populated in Task 8]

---

## 6. Function Call Graph

[To be populated in Task 6]

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

[To be populated in Task 12]

---

## 11. Maintenance Guide

[To be populated in Task 13]
