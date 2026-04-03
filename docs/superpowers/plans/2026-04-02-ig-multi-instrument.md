# IG Multi-Instrument Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Gold (CS.D.CFDGOLD.CFDGC.IP) alongside US30 in the IG bot with per-instrument CONFIG dicts, and remove the S5 config patching mechanism entirely.

**Architecture:** One CONFIG dict per instrument file; `evaluate_s5(cfg=None)` uses cfg when provided (IG path) or falls through to `from config_s5 import` (Bitget path unchanged); `ig_state.json` gains `positions`/`pending_orders` dicts keyed by `display_name`; `_tick()` loops over `INSTRUMENTS` list.

**Tech Stack:** Python 3.11, pandas, IG REST API, FastAPI (dashboard.py), vanilla JS (dashboard.html)

**Spec:** `docs/superpowers/specs/2026-04-01-ig-multi-instrument-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `config_ig_us30.py` | **Create** | US30 instrument CONFIG dict (all trading + S5 params) |
| `config_ig_gold.py` | **Create** | Gold instrument CONFIG dict (all trading + S5 params) |
| `config_ig.py` | **Modify** | Registry: INSTRUMENTS list + shared settings (credentials, URLs, file paths, session window) |
| `config_ig_s5.py` | **Delete** | Absorbed into config_ig_us30.py |
| `strategy.py` | **Modify** | Add `cfg=None` param to `evaluate_s5()` |
| `ig_bot.py` | **Modify** | Remove patching block + S5 imports + module constants; add symbol to _TRADE_FIELDS; state migration; multi-instrument _tick; all methods accept instrument param |
| `dashboard.py` | **Modify** | Read `positions` dict instead of single `position` |
| `dashboard.html` | **Modify** | `renderIGPositions(positions)` iterating over dict |
| `optimize_ig.py` | **Modify** | Handle symbol column; add `--symbol` filter flag |
| `tests/test_config_ig_instruments.py` | **Create** | Config shape validation tests |
| `tests/test_strategy_cfg_param.py` | **Create** | evaluate_s5 cfg param tests |
| `tests/test_ig_bot_multi_instrument.py` | **Create** | Migration, state, multi-instrument loop tests |
| `tests/test_ig_bot_pending.py` | **Modify** | Update `bot.pending_order` → `bot.pending_orders["US30"]` |
| `docs/DEPENDENCIES.md` | **Modify** | Update sections 1, 2.1, 4.3, 4.4, 5, 10.3 |

---

## Task 1: Per-Instrument Config Files

**Files:**
- Create: `config_ig_us30.py`
- Create: `config_ig_gold.py`
- Modify: `config_ig.py`
- Create: `tests/test_config_ig_instruments.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_ig_instruments.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

_REQUIRED_KEYS = {
    "epic", "display_name", "currency",
    "contract_size", "partial_size", "point_value",
    "session_start", "session_end",
    "daily_limit", "htf_limit", "m15_limit",
    "s5_enabled", "s5_daily_ema_fast", "s5_daily_ema_med", "s5_daily_ema_slow",
    "s5_htf_bos_lookback", "s5_ltf_interval", "s5_ob_lookback",
    "s5_ob_min_impulse", "s5_ob_min_range_pct", "s5_choch_lookback",
    "s5_max_entry_buffer", "s5_sl_buffer_pct", "s5_ob_invalidation_buffer_pct",
    "s5_swing_lookback", "s5_smc_fvg_filter", "s5_smc_fvg_lookback",
    "s5_leverage", "s5_trade_size_pct", "s5_min_rr",
    "s5_trail_range_pct", "s5_use_candle_stops", "s5_min_sr_clearance",
}

def test_config_ig_us30_has_all_required_keys():
    from config_ig_us30 import CONFIG
    missing = _REQUIRED_KEYS - CONFIG.keys()
    assert not missing, f"Missing keys: {missing}"

def test_config_ig_gold_has_all_required_keys():
    from config_ig_gold import CONFIG
    missing = _REQUIRED_KEYS - CONFIG.keys()
    assert not missing, f"Missing keys: {missing}"

def test_config_ig_instruments_list_has_us30_and_gold():
    from config_ig import INSTRUMENTS
    names = [i["display_name"] for i in INSTRUMENTS]
    assert "US30" in names
    assert "GOLD" in names

def test_config_ig_has_shared_settings():
    import config_ig
    assert hasattr(config_ig, "STATE_FILE")
    assert hasattr(config_ig, "TRADE_LOG")
    assert hasattr(config_ig, "LOG_FILE")
    assert hasattr(config_ig, "POLL_INTERVAL_SEC")
    assert hasattr(config_ig, "SESSION_START")
    assert hasattr(config_ig, "SESSION_END")

def test_config_ig_us30_values():
    from config_ig_us30 import CONFIG
    assert CONFIG["epic"] == "IX.D.DOW.IFD.IP"
    assert CONFIG["display_name"] == "US30"
    assert CONFIG["s5_min_rr"] == 2.0
    assert CONFIG["s5_use_candle_stops"] is True

def test_config_ig_gold_values():
    from config_ig_gold import CONFIG
    assert CONFIG["epic"] == "CS.D.CFDGOLD.CFDGC.IP"
    assert CONFIG["display_name"] == "GOLD"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
pytest tests/test_config_ig_instruments.py -v
```

Expected: `ModuleNotFoundError: No module named 'config_ig_us30'`

- [ ] **Step 3: Create `config_ig_us30.py`**

Values come from current `config_ig.py` (instrument section) merged with `config_ig_s5.py` (all S5 values):

```python
# config_ig_us30.py
"""US30 (Wall Street Cash) instrument configuration for IG bot."""

CONFIG = {
    # Instrument identity
    "epic":         "IX.D.DOW.IFD.IP",
    "display_name": "US30",
    "currency":     "USD",

    # Contract sizing
    "contract_size": 1,    # opening size (contracts)
    "partial_size":  0.5,  # close at TP1 (50%)
    "point_value":   1.0,  # USD per point per contract

    # Session window (hour, minute) in ET
    "session_start": (0, 0),
    "session_end":   (23, 59),

    # Candle fetch limits
    "daily_limit": 200,
    "htf_limit":   50,
    "m15_limit":   300,

    # S5 strategy parameters
    "s5_enabled":                    True,
    "s5_daily_ema_fast":             10,
    "s5_daily_ema_med":              20,
    "s5_daily_ema_slow":             50,
    "s5_htf_bos_lookback":           5,
    "s5_ltf_interval":               "15m",
    "s5_ob_lookback":                20,
    "s5_ob_min_impulse":             0.005,
    "s5_ob_min_range_pct":           0.002,
    "s5_choch_lookback":             10,
    "s5_max_entry_buffer":           0.01,
    "s5_sl_buffer_pct":              0.002,
    "s5_ob_invalidation_buffer_pct": 0.001,
    "s5_swing_lookback":             20,
    "s5_smc_fvg_filter":             False,
    "s5_smc_fvg_lookback":           10,
    "s5_leverage":                   1,
    "s5_trade_size_pct":             0.05,
    "s5_min_rr":                     2.0,
    "s5_trail_range_pct":            5,
    "s5_use_candle_stops":           True,
    "s5_min_sr_clearance":           0.10,
}
```

- [ ] **Step 4: Create `config_ig_gold.py`**

```python
# config_ig_gold.py
"""Gold (CS.D.CFDGOLD.CFDGC.IP) instrument configuration for IG bot."""

CONFIG = {
    # Instrument identity
    "epic":         "CS.D.CFDGOLD.CFDGC.IP",
    "display_name": "GOLD",
    "currency":     "USD",

    # Contract sizing (IG Gold CFD: 1 contract = 1 troy oz equivalent)
    "contract_size": 1,
    "partial_size":  0.5,
    "point_value":   1.0,  # USD per point per contract

    # Session window (hour, minute) in ET
    "session_start": (0, 0),
    "session_end":   (23, 59),

    # Candle fetch limits
    "daily_limit": 200,
    "htf_limit":   50,
    "m15_limit":   300,

    # S5 strategy parameters (tuned for ~$3200/oz Gold price)
    "s5_enabled":                    True,
    "s5_daily_ema_fast":             10,
    "s5_daily_ema_med":              21,
    "s5_daily_ema_slow":             50,
    "s5_htf_bos_lookback":           5,
    "s5_ltf_interval":               "15m",
    "s5_ob_lookback":                20,
    "s5_ob_min_impulse":             0.005,
    "s5_ob_min_range_pct":           0.002,
    "s5_choch_lookback":             10,
    "s5_max_entry_buffer":           0.005,
    "s5_sl_buffer_pct":              0.002,
    "s5_ob_invalidation_buffer_pct": 0.001,
    "s5_swing_lookback":             20,
    "s5_smc_fvg_filter":             False,
    "s5_smc_fvg_lookback":           10,
    "s5_leverage":                   1,
    "s5_trade_size_pct":             0.05,
    "s5_min_rr":                     2.0,
    "s5_trail_range_pct":            5,
    "s5_use_candle_stops":           True,
    "s5_min_sr_clearance":           0.10,
}
```

- [ ] **Step 5: Update `config_ig.py` to become a registry**

Read the current `config_ig.py` first. Keep: `.env` loading block, all credential vars (`IG_API_KEY`, `IG_USERNAME`, `IG_PASSWORD`, `IG_ACC_TYPE`, `IG_ACCOUNT_ID`), `IG_DEMO_URL`, `IG_LIVE_URL`, `POLL_INTERVAL_SEC`, `PAPER_MODE`, `LOG_FILE`, `TRADE_LOG`, `STATE_FILE`, `SESSION_START`, `SESSION_END`.

Remove: `EPIC`, `CURRENCY`, `CONTRACT_SIZE`, `PARTIAL_SIZE`, `POINT_VALUE`, `DAILY_LIMIT`, `HTF_LIMIT`, `M15_LIMIT`, and the S5 comment block.

Add at the top:
```python
from config_ig_us30 import CONFIG as _US30
from config_ig_gold  import CONFIG as _GOLD

INSTRUMENTS = [_US30, _GOLD]
```

Final `config_ig.py` should look like:
```python
"""
IG.com CFD bot configuration — registry and shared settings.
Per-instrument params live in config_ig_<name>.py.
"""
import os
from pathlib import Path

from config_ig_us30 import CONFIG as _US30
from config_ig_gold  import CONFIG as _GOLD

INSTRUMENTS = [_US30, _GOLD]

# Load .env from project root
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# ── IG Credentials ─────────────────────────────────────────── #
IG_API_KEY    = os.environ.get("IG_API_KEY",    "")
IG_USERNAME   = os.environ.get("IG_USERNAME",   "")
IG_PASSWORD   = os.environ.get("IG_PASSWORD",   "")
IG_ACC_TYPE   = os.environ.get("IG_ACC_TYPE",   "DEMO").upper()
IG_ACCOUNT_ID = os.environ.get("IG_ACCOUNT_ID", "")

IG_DEMO_URL = "https://demo-api.ig.com/gateway/deal"
IG_LIVE_URL = "https://api.ig.com/gateway/deal"

# ── Shared settings ─────────────────────────────────────────── #
SESSION_START     = (0, 0)
SESSION_END       = (23, 59)
POLL_INTERVAL_SEC = 45
PAPER_MODE        = False

# ── File paths ──────────────────────────────────────────────── #
LOG_FILE   = "ig_bot.log"
TRADE_LOG  = "ig_trades.csv"
STATE_FILE = "ig_state.json"
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
pytest tests/test_config_ig_instruments.py -v
```

Expected: All 6 tests PASS

- [ ] **Step 7: Commit**

```bash
git add config_ig_us30.py config_ig_gold.py config_ig.py tests/test_config_ig_instruments.py
git commit -m "feat(config): per-instrument CONFIG dicts for US30 and Gold; config_ig.py becomes registry"
```

---

## Task 2: `strategy.py` — Add `cfg` Parameter to `evaluate_s5()`

**Files:**
- Modify: `strategy.py` (around line 1104)
- Create: `tests/test_strategy_cfg_param.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_strategy_cfg_param.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pandas as pd
import pytest

# Minimal single-row DataFrame that satisfies column requirements
_DF = pd.DataFrame({
    "open":   [100.0] * 60,
    "high":   [101.0] * 60,
    "low":    [99.0]  * 60,
    "close":  [100.0] * 60,
    "volume": [1000.0]* 60,
})

_BASE_CFG = {
    "s5_enabled":                    False,  # disabled → fast return
    "s5_daily_ema_fast":             10,
    "s5_daily_ema_med":              20,
    "s5_daily_ema_slow":             50,
    "s5_htf_bos_lookback":           5,
    "s5_ob_lookback":                20,
    "s5_ob_min_impulse":             0.005,
    "s5_ob_min_range_pct":           0.002,
    "s5_choch_lookback":             10,
    "s5_max_entry_buffer":           0.01,
    "s5_sl_buffer_pct":              0.002,
    "s5_min_rr":                     2.0,
    "s5_swing_lookback":             20,
    "s5_smc_fvg_filter":             False,
    "s5_smc_fvg_lookback":           10,
}


def test_evaluate_s5_accepts_cfg_keyword():
    """evaluate_s5() accepts a cfg= keyword argument without raising TypeError."""
    from strategy import evaluate_s5
    # Just verify the signature accepts cfg — disabled cfg returns quickly
    result = evaluate_s5("TEST", _DF, _DF, _DF, "LONG", cfg=_BASE_CFG)
    assert result is not None


def test_evaluate_s5_cfg_disabled_returns_hold():
    """When cfg has s5_enabled=False, returns Signal.HOLD."""
    from strategy import evaluate_s5, Signal
    sig, *_ = evaluate_s5("TEST", _DF, _DF, _DF, "LONG", cfg={**_BASE_CFG, "s5_enabled": False})
    assert sig == Signal.HOLD


def test_evaluate_s5_no_cfg_uses_bitget_path(monkeypatch):
    """When cfg=None (default), the Bitget path is used (config_s5 module)."""
    import config_s5
    # Disable via config_s5; if cfg path were used it would fail (no s5_enabled key)
    original = config_s5.S5_ENABLED
    try:
        config_s5.S5_ENABLED = False
        from strategy import evaluate_s5, Signal
        sig, *_ = evaluate_s5("TEST", _DF, _DF, _DF, "LONG")
        assert sig == Signal.HOLD
    finally:
        config_s5.S5_ENABLED = original
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_strategy_cfg_param.py::test_evaluate_s5_accepts_cfg_keyword -v
```

Expected: `TypeError: evaluate_s5() got an unexpected keyword argument 'cfg'`

- [ ] **Step 3: Modify `strategy.py` — add `cfg=None` and if/else block**

Read `strategy.py` around lines 1104–1140. Find the `evaluate_s5` function signature and the `from config_s5 import (...)` block inside the function.

Change the signature from:
```python
def evaluate_s5(
    symbol: str,
    daily_df: pd.DataFrame,
    htf_df: pd.DataFrame,
    m15_df: pd.DataFrame,
    allowed_direction: str,
) -> tuple[...]:
```
To:
```python
def evaluate_s5(
    symbol: str,
    daily_df: pd.DataFrame,
    htf_df: pd.DataFrame,
    m15_df: pd.DataFrame,
    allowed_direction: str,
    cfg=None,   # instrument CONFIG dict; None = Bitget path (config_s5 module)
) -> tuple[...]:
```

Replace the `from config_s5 import (...)` block (verify exact params by reading the file) with:

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
        S5_MIN_RR                     = cfg["s5_min_rr"]
        S5_SWING_LOOKBACK             = cfg["s5_swing_lookback"]
        S5_SMC_FVG_FILTER             = cfg["s5_smc_fvg_filter"]
        S5_SMC_FVG_LOOKBACK           = cfg["s5_smc_fvg_lookback"]
    else:
        from config_s5 import (          # noqa: PLC0415  (intentional — enables IG patching legacy path)
            S5_ENABLED,
            S5_DAILY_EMA_FAST, S5_DAILY_EMA_MED, S5_DAILY_EMA_SLOW,
            S5_HTF_BOS_LOOKBACK,
            S5_OB_LOOKBACK, S5_OB_MIN_IMPULSE, S5_OB_MIN_RANGE_PCT,
            S5_CHOCH_LOOKBACK, S5_MAX_ENTRY_BUFFER, S5_SL_BUFFER_PCT,
            S5_MIN_RR, S5_SWING_LOOKBACK, S5_SMC_FVG_FILTER, S5_SMC_FVG_LOOKBACK,
        )
```

**Important:** The exact list of params in the `else` branch must match what was in the original `from config_s5 import (...)`. Read the file before editing to confirm. Do not add or remove params — this preserves the Bitget path exactly.

- [ ] **Step 4: Run all strategy-related tests to verify nothing broke**

```bash
pytest tests/test_strategy_cfg_param.py tests/test_strategy.py -v 2>/dev/null || pytest tests/test_strategy_cfg_param.py -v
```

Expected: All `test_strategy_cfg_param.py` tests PASS. Existing strategy tests unchanged.

- [ ] **Step 5: Commit**

```bash
git add strategy.py tests/test_strategy_cfg_param.py
git commit -m "feat(strategy): add cfg=None param to evaluate_s5 for per-instrument config"
```

---

## Task 3: `ig_bot.py` — Structural Cleanup

Remove the patching block, module-level S5 imports, module-level instrument constants. Add `"symbol"` to `_TRADE_FIELDS`. Add startup validation.

**Files:**
- Modify: `ig_bot.py`

- [ ] **Step 1: Read `ig_bot.py` lines 1–130 to verify exact line numbers before editing**

```bash
# Use the Read tool to read ig_bot.py lines 1-130
```

- [ ] **Step 2: Delete the config_ig_s5 patching block (approx lines 27–39)**

Find and delete this entire block (exact content, not line numbers — verify by reading):
```python
import config_s5 as _cs5_orig
import config_ig_s5 as _cs5_ig
_base_attrs = {a for a in dir(_cs5_orig) if not a.startswith('_')}
for _attr in [a for a in dir(_cs5_ig) if not a.startswith('_')]:
    if _attr not in _base_attrs:
        raise AttributeError(...)
    setattr(_cs5_orig, _attr, getattr(_cs5_ig, _attr))
del _cs5_orig, _cs5_ig, _attr, _base_attrs
```

- [ ] **Step 3: Delete the module-level S5 imports from config_ig_s5 (approx lines 41–45)**

Find and delete this block:
```python
from config_ig_s5 import (
    S5_DAILY_EMA_FAST, S5_DAILY_EMA_SLOW,
    S5_USE_CANDLE_STOPS, S5_SL_BUFFER_PCT, S5_SWING_LOOKBACK, S5_MAX_ENTRY_BUFFER,
    S5_LTF_INTERVAL, S5_OB_INVALIDATION_BUFFER_PCT,
)
```

- [ ] **Step 4: Delete module-level instrument constants (approx lines 103–108)**

Find and delete these lines:
```python
EPIC          = config_ig.EPIC
CONTRACT_SIZE = config_ig.CONTRACT_SIZE
DISPLAY_NAME  = "US30"
PARTIAL_SIZE  = config_ig.PARTIAL_SIZE
POINT_VALUE   = config_ig.POINT_VALUE
```

- [ ] **Step 5: Add `"symbol"` to `_TRADE_FIELDS` (approx lines 112–119)**

Find `_TRADE_FIELDS` list. Change:
```python
_TRADE_FIELDS = [
    "timestamp", "trade_id", "action",
    "side", ...
```
To:
```python
_TRADE_FIELDS = [
    "timestamp", "trade_id", "action", "symbol",
    "side", ...
```

- [ ] **Step 6: Add startup validation function after the imports section**

Add after the existing module-level constants (before the class definitions):

```python
_REQUIRED_INSTRUMENT_KEYS = {
    "epic", "display_name", "currency",
    "contract_size", "partial_size", "point_value",
    "session_start", "session_end",
    "daily_limit", "htf_limit", "m15_limit",
    "s5_enabled", "s5_daily_ema_fast", "s5_daily_ema_med", "s5_daily_ema_slow",
    "s5_htf_bos_lookback", "s5_ltf_interval", "s5_ob_lookback",
    "s5_ob_min_impulse", "s5_ob_min_range_pct", "s5_choch_lookback",
    "s5_max_entry_buffer", "s5_sl_buffer_pct", "s5_ob_invalidation_buffer_pct",
    "s5_swing_lookback", "s5_smc_fvg_filter", "s5_smc_fvg_lookback",
    "s5_leverage", "s5_trade_size_pct", "s5_min_rr",
    "s5_trail_range_pct", "s5_use_candle_stops", "s5_min_sr_clearance",
}


def _validate_instruments() -> None:
    for inst in config_ig.INSTRUMENTS:
        missing = _REQUIRED_INSTRUMENT_KEYS - inst.keys()
        if missing:
            raise KeyError(
                f"Instrument config '{inst.get('display_name', '?')}' missing keys: {missing}"
            )


_validate_instruments()
```

- [ ] **Step 7: Verify ig_bot.py still imports without error**

```bash
python -c "import ig_bot; print('OK')"
```

Expected: `OK` (no import errors)

- [ ] **Step 8: Run existing scan state tests to confirm no regression**

```bash
pytest tests/test_ig_bot_scan_state.py -v
```

Expected: All pass (or skip tests requiring live server)

- [ ] **Step 9: Commit**

```bash
git add ig_bot.py
git commit -m "refactor(ig_bot): remove S5 patching block and module-level constants; add symbol to TRADE_FIELDS; add startup validation"
```

---

## Task 4: `ig_bot.py` — State Management Refactor

Refactor state to use `positions`/`pending_orders` dicts keyed by `display_name`. Neutralise `_PaperState._save()`. Update `_get_candles` to accept `epic` param with tuple cache key. Add startup migration for old state format.

**Files:**
- Modify: `ig_bot.py`
- Create: `tests/test_ig_bot_multi_instrument.py` (first batch of tests)

- [ ] **Step 1: Write failing tests for state migration and new dict format**

```python
# tests/test_ig_bot_multi_instrument.py
import sys, os, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import ig_bot
import config_ig


def _make_bot(monkeypatch, state_data=None, paper=True):
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    json.dump(state_data or {}, tmp)
    tmp.close()
    monkeypatch.setattr(config_ig, "STATE_FILE", tmp.name)
    monkeypatch.setattr(ig_bot, "_in_trading_window", lambda now: True)
    monkeypatch.setattr(ig_bot, "_is_session_end",    lambda now: False)
    return ig_bot.IGBot(paper=paper)


# ── State migration ────────────────────────────────────────── #

def test_state_migration_old_format_wraps_position_under_us30(monkeypatch):
    """Old state with 'position' key migrates to positions['US30']."""
    old_state = {
        "position": {"trade_id": "abc", "side": "LONG", "qty": 1.0,
                     "entry": 44200.0, "sl": 44100.0, "tp": 44400.0,
                     "opened_at": "2026-01-01T10:00:00+00:00"},
        "pending_order": None,
        "scan_signals": {},
        "scan_log": [],
    }
    bot = _make_bot(monkeypatch, state_data=old_state)
    assert bot.positions["US30"]["trade_id"] == "abc"
    assert bot.pending_orders["US30"] is None


def test_state_migration_writes_new_format_to_file(monkeypatch):
    """After migration, state file no longer has 'position' key."""
    old_state = {
        "position": {"trade_id": "abc", "side": "LONG", "qty": 1.0,
                     "entry": 44200.0, "sl": 44100.0, "tp": 44400.0,
                     "opened_at": "2026-01-01T10:00:00+00:00"},
        "pending_order": None, "scan_signals": {}, "scan_log": [],
    }
    _make_bot(monkeypatch, state_data=old_state)
    with open(config_ig.STATE_FILE) as f:
        data = json.load(f)
    assert "positions" in data
    assert "position" not in data


def test_new_state_format_loads_correctly(monkeypatch):
    """New-format state with 'positions' dict loads without migration."""
    new_state = {
        "positions": {"US30": None, "GOLD": None},
        "pending_orders": {"US30": None, "GOLD": None},
        "scan_signals": {},
        "scan_log": [],
    }
    bot = _make_bot(monkeypatch, state_data=new_state)
    assert bot.positions["US30"] is None
    assert bot.positions["GOLD"] is None


def test_positions_initialized_for_all_instruments(monkeypatch):
    """IGBot initializes positions for every instrument in INSTRUMENTS."""
    bot = _make_bot(monkeypatch)
    expected_names = {i["display_name"] for i in config_ig.INSTRUMENTS}
    assert expected_names <= set(bot.positions.keys())
    assert expected_names <= set(bot.pending_orders.keys())


# ── _save_state ────────────────────────────────────────────── #

def test_save_state_writes_positions_dict(monkeypatch):
    """_save_state() writes 'positions' and 'pending_orders' dicts."""
    bot = _make_bot(monkeypatch)
    bot._save_state()
    with open(config_ig.STATE_FILE) as f:
        data = json.load(f)
    assert "positions" in data
    assert "pending_orders" in data
    assert "position" not in data  # old key must not appear
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_ig_bot_multi_instrument.py -v
```

Expected: Multiple failures — `AttributeError: 'IGBot' object has no attribute 'positions'`

- [ ] **Step 3: Read `ig_bot.py` sections to understand current structure before editing**

Read these sections:
- `_PaperState` class (approx lines 205–250): understand `_save()` and `_load()`
- `IGBot.__init__` (approx lines 311–350): understand how `self.position` / `self.pending_order` are set
- `_sync_live_position` (approx lines 390–414): understand state file loading
- `_save_state` (approx lines 416–423): understand what is written

- [ ] **Step 4: Make `_PaperState._save()` a no-op**

Find `_PaperState._save()`. Change to:
```python
def _save(self) -> None:
    pass  # IGBot._save_state() handles all persistence
```

- [ ] **Step 5: Update `IGBot.__init__` — replace singular position with dicts**

In `IGBot.__init__`, replace:
```python
self.position: dict | None = None
self.pending_order: dict | None = None
```
With:
```python
self.positions: dict[str, dict | None] = {
    i["display_name"]: None for i in config_ig.INSTRUMENTS
}
self.pending_orders: dict[str, dict | None] = {
    i["display_name"]: None for i in config_ig.INSTRUMENTS
}
```

Also add a call to `self._load_state()` in `__init__` (replacing any existing state loading call).

- [ ] **Step 6: Add `_load_state()` method to `IGBot`**

Add this method to the `IGBot` class (after `__init__`):

```python
def _load_state(self) -> None:
    """Load state from file, migrating old single-instrument format if needed."""
    if not os.path.exists(config_ig.STATE_FILE):
        return
    with open(config_ig.STATE_FILE) as f:
        data = json.load(f)

    # Migrate old format: "position" / "pending_order" (singular) → dicts
    if "position" in data and "positions" not in data:
        data["positions"]      = {"US30": data.pop("position")}
        data["pending_orders"] = {"US30": data.pop("pending_order", None)}
        with open(config_ig.STATE_FILE, "w") as f:
            json.dump(data, f, indent=2)

    self.positions = {
        **{i["display_name"]: None for i in config_ig.INSTRUMENTS},
        **data.get("positions", {}),
    }
    self.pending_orders = {
        **{i["display_name"]: None for i in config_ig.INSTRUMENTS},
        **data.get("pending_orders", {}),
    }
    self._scan_signals = data.get("scan_signals", {})
    self._scan_log     = data.get("scan_log", [])
```

- [ ] **Step 7: Update `_save_state()` to write new format**

Find `_save_state()`. Replace its body with:

```python
def _save_state(self) -> None:
    data = {
        "positions":      self.positions,
        "pending_orders": self.pending_orders,
        "scan_signals":   self._scan_signals,
        "scan_log":       self._scan_log,
    }
    with open(config_ig.STATE_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)
```

- [ ] **Step 8: Update `_get_candles()` to accept `epic` param and use tuple cache key**

Read the current `_get_candles` implementation (approx lines 351–388).

Change the signature from `_get_candles(self, interval, limit)` to `_get_candles(self, epic: str, interval: str, limit: int)`.

Change cache key from `interval` (string) to `(epic, interval)` (tuple).

Change wherever `EPIC` (module-level constant) was used to the `epic` parameter.

- [ ] **Step 9: Update `_sync_live_position()` for new dict format**

Read the current `_sync_live_position` implementation (approx lines 390–414).

This method currently reads `data.get("position")` and `data.get("pending_order")`. After this task, state loading is fully handled by `_load_state()`. Remove any state-file reading from `_sync_live_position`. Keep only the live API call logic.

This method will be further updated in Task 5 to accept an `instrument` param.

- [ ] **Step 10: Run tests to verify they pass**

```bash
pytest tests/test_ig_bot_multi_instrument.py tests/test_ig_bot_scan_state.py -v
```

Expected: All pass

- [ ] **Step 11: Commit**

```bash
git add ig_bot.py tests/test_ig_bot_multi_instrument.py
git commit -m "refactor(ig_bot): positions/pending_orders dicts; _load_state with migration; _save_state new format"
```

---

## Task 5: `ig_bot.py` — Multi-Instrument `_tick()` Loop

Refactor all trade management methods to accept `instrument` dict as first param. Replace single-instrument `_tick()` with loop over `INSTRUMENTS`.

**Files:**
- Modify: `ig_bot.py`

- [ ] **Step 1: Read `ig_bot.py` lines 491–1017 to understand all methods before editing**

Read these sections before making changes:
- `_tick` (approx lines 491–584): current single-instrument flow
- `_open_trade` / `_place_pending_order`: how trades are placed
- `_check_pending_order` / `_handle_pending_filled`: pending order management
- `_monitor_position` / `_handle_partial_close` / `_trail_sl_candle`: position management
- `_handle_position_closed` / `_session_end_close`: cleanup methods
- `_calc_pnl` / `_calc_partial_pnl` (approx lines 184–196): P&L helpers using module constants

- [ ] **Step 2: Update `_calc_pnl` and `_calc_partial_pnl` to accept instrument param**

These functions currently use module-level `CONTRACT_SIZE`, `POINT_VALUE`, `PARTIAL_SIZE`. Add `instrument: dict` as first param and use `instrument["contract_size"]`, etc. instead.

Find and update both functions. The exact signatures before editing (verify by reading):
```python
# Before:
def _calc_pnl(entry, exit_price, side) -> float:
    return (exit_price - entry) * CONTRACT_SIZE * POINT_VALUE * (1 if side == "LONG" else -1)

def _calc_partial_pnl(entry, exit_price, side) -> float:
    return (exit_price - entry) * PARTIAL_SIZE * POINT_VALUE * (1 if side == "LONG" else -1)

# After:
def _calc_pnl(instrument: dict, entry: float, exit_price: float, side: str) -> float:
    return (exit_price - entry) * instrument["contract_size"] * instrument["point_value"] * (1 if side == "LONG" else -1)

def _calc_partial_pnl(instrument: dict, entry: float, exit_price: float, side: str) -> float:
    return (exit_price - entry) * instrument["partial_size"] * instrument["point_value"] * (1 if side == "LONG" else -1)
```

Update all call sites of `_calc_pnl(...)` and `_calc_partial_pnl(...)` in `ig_bot.py` to pass `instrument` as first argument.

- [ ] **Step 3: Add `instrument` param to all trade management methods**

For each method below, add `instrument: dict` as first parameter (after `self`). Replace all references to:
- Module-level `EPIC` → `instrument["epic"]`
- Module-level `CONTRACT_SIZE` → `instrument["contract_size"]`
- Module-level `PARTIAL_SIZE` → `instrument["partial_size"]`
- Module-level `POINT_VALUE` → `instrument["point_value"]`
- Module-level `S5_USE_CANDLE_STOPS` → `instrument["s5_use_candle_stops"]`
- Module-level `S5_SL_BUFFER_PCT` → `instrument["s5_sl_buffer_pct"]`
- Module-level `S5_SWING_LOOKBACK` → `instrument["s5_swing_lookback"]`
- Module-level `S5_MAX_ENTRY_BUFFER` → `instrument["s5_max_entry_buffer"]`
- Module-level `S5_LTF_INTERVAL` → `instrument["s5_ltf_interval"]`
- Module-level `S5_OB_INVALIDATION_BUFFER_PCT` → `instrument["s5_ob_invalidation_buffer_pct"]`
- Module-level `S5_DAILY_EMA_FAST` → `instrument["s5_daily_ema_fast"]`
- Module-level `S5_DAILY_EMA_SLOW` → `instrument["s5_daily_ema_slow"]`
- `DISPLAY_NAME` / hardcoded `"US30"` → `instrument["display_name"]`
- `self.position` → `self.positions[instrument["display_name"]]`
- `self.pending_order` → `self.pending_orders[instrument["display_name"]]`
- `_get_candles(interval, limit)` → `_get_candles(instrument["epic"], interval, limit)`

Methods to update: `_open_trade`, `_place_pending_order` (if separate), `_check_pending_order`, `_handle_pending_filled`, `_monitor_position`, `_handle_partial_close`, `_trail_sl_candle`, `_handle_position_closed`, `_session_end_close`, `_sync_live_position`.

When writing trade CSV rows, add the `symbol` field populated from `instrument["display_name"]`.

- [ ] **Step 4: Rewrite `_tick()` as a multi-instrument loop**

Replace the current single-instrument `_tick()` body with:

```python
def _tick(self) -> None:
    now = datetime.now(timezone.utc)

    for instrument in config_ig.INSTRUMENTS:
        display_name = instrument["display_name"]

        try:
            # Fetch candles for this instrument
            daily_df = self._get_candles(instrument["epic"], "1d",
                                         instrument["daily_limit"])
            htf_df   = self._get_candles(instrument["epic"], "1h",
                                         instrument["htf_limit"])
            m15_df   = self._get_candles(instrument["epic"],
                                         instrument["s5_ltf_interval"],
                                         instrument["m15_limit"])

            # Determine allowed direction (preserve existing EMA bias logic here)
            # Read the original _tick to find how allowed_direction was computed
            # and replicate that logic using daily_df + instrument S5 EMA params
            allowed_direction = self._get_allowed_direction(instrument, daily_df)

            # Evaluate strategy with instrument-specific config
            sig, trigger, sl, tp, ob_low, ob_high, reason = evaluate_s5(
                display_name, daily_df, htf_df, m15_df,
                allowed_direction, cfg=instrument,
            )

            # Update scan state for dashboard
            self._update_scan_state(
                display_name, sig, reason, ob_low, ob_high, trigger, sl, tp
            )

            # Session-end force close
            if _is_session_end(now) and self.positions[display_name]:
                self._session_end_close(instrument)
                continue

            # Manage position and pending orders
            if self.positions[display_name]:
                self._monitor_position(instrument, m15_df)
            elif self.pending_orders[display_name]:
                self._check_pending_order(instrument)
            elif sig in (Signal.LONG, Signal.SHORT):
                self._open_trade(instrument, sig, trigger, sl, tp, ob_low, ob_high)

        except Exception as exc:
            logging.error("[%s] tick error: %s", display_name, exc, exc_info=True)

    self._save_state()
```

**Note:** `_get_allowed_direction(instrument, daily_df)` extracts the EMA-bias logic that was previously inline in `_tick`. Read the original `_tick` to find the exact logic (it checks if fast/slow EMA are aligned bullish/bearish) and extract it into this helper:

```python
def _get_allowed_direction(self, instrument: dict, daily_df: pd.DataFrame) -> str:
    """Return 'LONG', 'SHORT', or 'BOTH' based on daily EMA alignment."""
    # Extract this logic from the original _tick — it uses:
    # instrument["s5_daily_ema_fast"], instrument["s5_daily_ema_slow"]
    # and daily_df["close"] to compute EMA values
    # Preserve the exact logic; just replace module-level constant names with instrument dict lookups
    ...
```

- [ ] **Step 5: Run all ig_bot tests**

```bash
pytest tests/test_ig_bot_scan_state.py tests/test_ig_bot_multi_instrument.py -v
```

Expected: All pass

- [ ] **Step 6: Smoke test — verify ig_bot imports without error**

```bash
python -c "import ig_bot; print('OK')"
```

Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add ig_bot.py
git commit -m "feat(ig_bot): multi-instrument _tick loop; all methods accept instrument param"
```

---

## Task 6: Update Existing Tests for New API

**Files:**
- Modify: `tests/test_ig_bot_pending.py`
- Modify: `tests/test_ig_bot_scan_state.py` (scan test uses old state format — verify it still passes)

- [ ] **Step 1: Read `tests/test_ig_bot_pending.py` to find all `bot.pending_order` references**

```bash
# Use Grep to find all pending_order usages
# grep -n "pending_order" tests/test_ig_bot_pending.py
```

- [ ] **Step 2: Update all `bot.pending_order` references to `bot.pending_orders["US30"]`**

For each occurrence of `bot.pending_order` in `test_ig_bot_pending.py`, change to `bot.pending_orders["US30"]`.

For each occurrence of `bot.position` (singular) in any test file, change to `bot.positions["US30"]`.

Example pattern:
```python
# Before:
assert bot.pending_order is not None
assert bot.pending_order["side"] == "LONG"
bot.pending_order = None

# After:
assert bot.pending_orders["US30"] is not None
assert bot.pending_orders["US30"]["side"] == "LONG"
bot.pending_orders["US30"] = None
```

- [ ] **Step 3: Verify scan state test still passes (it uses old state format for migration)**

Check `test_startup_restores_scan_fields` in `test_ig_bot_scan_state.py`. It writes a state file with `"position"` key (old format). This should now trigger the migration code in `_load_state()`. The test asserts scan fields are restored — that still works because migration preserves `scan_signals` and `scan_log`. Verify the test still passes:

```bash
pytest tests/test_ig_bot_scan_state.py::test_startup_restores_scan_fields -v
```

If it fails due to the old-format state: the migration in `_load_state()` should handle it. If the test was relying on `bot.position` after startup, update it to `bot.positions["US30"]`.

- [ ] **Step 4: Run all ig_bot tests**

```bash
pytest tests/test_ig_bot_pending.py tests/test_ig_bot_scan_state.py tests/test_ig_bot_multi_instrument.py -v
```

Expected: All pass

- [ ] **Step 5: Delete `config_ig_s5.py`**

```bash
git rm config_ig_s5.py
```

Verify nothing else imports it:
```bash
grep -r "config_ig_s5" . --include="*.py" --exclude-dir=__pycache__
```

Expected: no matches (it was only used by ig_bot.py patching block, which is now deleted).

- [ ] **Step 6: Commit**

```bash
git add tests/test_ig_bot_pending.py tests/test_ig_bot_scan_state.py
git commit -m "refactor(tests): update ig_bot tests for pending_orders dict; delete config_ig_s5.py"
```

---

## Task 7: Dashboard — `positions` Dict

**Files:**
- Modify: `dashboard.py` (around line 994)
- Modify: `dashboard.html` (around lines 3464, 3539–3590)

- [ ] **Step 1: Write a test for the dashboard state endpoint**

Add to `tests/test_ig_bot_multi_instrument.py`:

```python
import httpx

def test_ig_state_endpoint_returns_positions_dict(live_server_url):
    """/api/ig/state returns 'positions' dict, not singular 'position'."""
    r = httpx.get(
        f"{live_server_url}/api/ig/state",
        headers={"Authorization": "Bearer test-token"},
        timeout=5.0,
    )
    assert r.status_code == 200
    data = r.json()
    assert "positions" in data, "'positions' missing from /api/ig/state"
    assert "position" not in data, "old singular 'position' key must not appear"
    assert isinstance(data["positions"], dict)
```

- [ ] **Step 2: Update `dashboard.py` — `get_ig_state()`**

Read `dashboard.py` around lines 990–1068. Find the `get_ig_state()` function.

Change:
```python
position = ig_state.get("position")
```
To:
```python
positions = ig_state.get("positions", {})
```

Change the return dict entry:
```python
# Before:
"position": position,
# After:
"positions": positions,
```

Also check `session_active` logic around lines 1000–1004. It uses `_cfg_ig.SESSION_START` / `_cfg_ig.SESSION_END`. These are now shared settings in `config_ig.py` (the registry), so `_cfg_ig.SESSION_START` still works — no change needed there.

- [ ] **Step 3: Update `dashboard.html` — IG positions panel**

Read `dashboard.html` around lines 1151–1156, 3464, and 3539–3590.

**Change 1** — Panel header (around line 1152): Update the panel title from `"Open Position"` / `"Wall Street Cash · US30"` to `"IG Positions"`. Find and update the relevant `<h3>` or panel header element.

**Change 2** — In `renderIG(d)` (around line 3464): Change:
```javascript
renderIGPosition(d.position);
```
To:
```javascript
renderIGPositions(d.positions);
```

**Change 3** — Replace `renderIGPosition(pos)` function (approx lines 3539–3559) with `renderIGPositions(positions)`:

```javascript
function renderIGPositions(positions) {
  const body = $('ig-pos-body');
  if (!positions || Object.keys(positions).length === 0) {
    body.innerHTML = '<div style="color:var(--muted);font-size:0.85rem;padding:8px 0">No open positions</div>';
    return;
  }
  const cards = Object.entries(positions).map(([name, pos]) => {
    if (!pos) {
      return `<div class="ig-pos-row" style="color:var(--muted);font-size:0.85rem;padding:4px 0">
        <span style="font-weight:600;margin-right:8px">${name}</span> — no position
      </div>`;
    }
    const sc = pos.side === 'LONG' ? 'var(--emerald)' : 'var(--rose)';
    const partial = pos.partial_done ? ' <span style="color:var(--muted);font-size:0.8rem">(partial done)</span>' : '';
    return `<div class="ig-pos-row" style="margin-bottom:8px">
      <span style="font-weight:600;margin-right:8px">${name}</span>
      <span style="color:${sc};font-weight:600">${pos.side}</span>
      ${partial}
      <span style="margin-left:8px;color:var(--muted);font-size:0.8rem">${pos.current_qty ?? pos.qty}ct</span>
      <div style="font-size:0.82rem;margin-top:2px">
        Entry <b>${pos.entry}</b> &nbsp;
        SL <b style="color:var(--rose)">${pos.sl}</b> &nbsp;
        TP <b style="color:var(--emerald)">${pos.tp}</b>
      </div>
      <div style="font-size:0.78rem;color:var(--muted);margin-top:1px">
        OB ${pos.ob_low ?? '—'} – ${pos.ob_high ?? '—'} &nbsp;|&nbsp; ${pos.opened_at ?? ''}
      </div>
    </div>`;
  });
  body.innerHTML = cards.join('');
}
```

- [ ] **Step 4: Run dashboard test**

```bash
pytest tests/test_ig_bot_scan_state.py::test_ig_state_endpoint_includes_scan_fields -v 2>/dev/null
pytest tests/test_ig_bot_multi_instrument.py::test_ig_state_endpoint_returns_positions_dict -v 2>/dev/null
```

Expected: Pass (or skip if live_server_url fixture not configured — that's OK, the unit tests cover the logic)

- [ ] **Step 5: Commit**

```bash
git add dashboard.py dashboard.html
git commit -m "feat(dashboard): IG positions panel iterates positions dict; one card per instrument"
```

---

## Task 8: `optimize_ig.py` — Symbol Column + `--symbol` Flag

**Files:**
- Modify: `optimize_ig.py`

- [ ] **Step 1: Read `optimize_ig.py` fully before editing**

Read the full file. Note:
- `_load_current_params()` at line 22: currently `import config_ig_s5 as c`
- `load_trades()` at line 40: CSV parsing (check exact column index for `symbol`)
- `build_prompt()` at line 143: hardcodes "US30", "from config_ig_s5.py"
- `main()` at line 181: no `--symbol` flag

- [ ] **Step 2: Update `_load_current_params()` to use `INSTRUMENTS[0]` (US30 config)**

Change:
```python
def _load_current_params():
    import config_ig_s5 as c
    return {
        "S5_DAILY_EMA_FAST": c.S5_DAILY_EMA_FAST,
        ...
    }
```
To:
```python
def _load_current_params(instrument=None):
    from config_ig import INSTRUMENTS
    cfg = instrument if instrument is not None else INSTRUMENTS[0]
    return {
        "S5_DAILY_EMA_FAST":             cfg["s5_daily_ema_fast"],
        "S5_DAILY_EMA_MED":              cfg["s5_daily_ema_med"],
        "S5_DAILY_EMA_SLOW":             cfg["s5_daily_ema_slow"],
        "S5_HTF_BOS_LOOKBACK":           cfg["s5_htf_bos_lookback"],
        "S5_OB_LOOKBACK":                cfg["s5_ob_lookback"],
        "S5_OB_MIN_IMPULSE":             cfg["s5_ob_min_impulse"],
        "S5_OB_MIN_RANGE_PCT":           cfg["s5_ob_min_range_pct"],
        "S5_CHOCH_LOOKBACK":             cfg["s5_choch_lookback"],
        "S5_MAX_ENTRY_BUFFER":           cfg["s5_max_entry_buffer"],
        "S5_SL_BUFFER_PCT":              cfg["s5_sl_buffer_pct"],
        "S5_MIN_RR":                     cfg["s5_min_rr"],
        "S5_SWING_LOOKBACK":             cfg["s5_swing_lookback"],
        "S5_SMC_FVG_FILTER":             cfg["s5_smc_fvg_filter"],
        "S5_SMC_FVG_LOOKBACK":           cfg["s5_smc_fvg_lookback"],
        "S5_OB_INVALIDATION_BUFFER_PCT": cfg["s5_ob_invalidation_buffer_pct"],
        "S5_USE_CANDLE_STOPS":           cfg["s5_use_candle_stops"],
        "S5_TRAIL_RANGE_PCT":            cfg["s5_trail_range_pct"],
        "S5_MIN_SR_CLEARANCE":           cfg["s5_min_sr_clearance"],
    }
```

- [ ] **Step 3: Update `load_trades()` to read `symbol` column and accept filter**

The new CSV has 20 fields. The `symbol` field is at index 3 (after `timestamp`, `trade_id`, `action`). Update parsing to read it. Old rows will have empty `symbol` — treat as "US30".

```python
def load_trades(csv_path: str, symbol_filter: str | None = None) -> list[dict]:
    trades = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Handle both old (19-field) and new (20-field) rows
            sym = row.get("symbol", "") or "US30"
            if symbol_filter and sym != symbol_filter:
                continue
            row["symbol"] = sym
            trades.append(row)
    return trades
```

- [ ] **Step 4: Update `build_prompt()` — remove hardcoded US30/config_ig_s5.py references**

Find all occurrences of `"US30"` and `"config_ig_s5"` in `build_prompt()`. Replace:
- `"US30"` → use `instrument["display_name"]` (pass `instrument` as param to `build_prompt`)
- `"from config_ig_s5.py"` → `"from config_ig_{name}.py"` where `name = instrument["display_name"].lower()`

Update `build_prompt()` signature to accept `instrument: dict`:
```python
def build_prompt(trades, params, instrument: dict) -> str:
    name = instrument["display_name"]
    config_file = f"config_ig_{name.lower()}.py"
    ...  # replace hardcoded "US30" / "config_ig_s5.py" with name / config_file
```

- [ ] **Step 5: Add `--symbol` flag to `main()`**

```python
def main():
    import argparse
    from config_ig import INSTRUMENTS

    parser = argparse.ArgumentParser(description="IG trade optimizer")
    parser.add_argument("--symbol", default=None,
                        help="Filter by instrument display_name (e.g. US30, GOLD). Omit to analyse all.")
    args = parser.parse_args()

    # Resolve instrument config for the symbol (used for current params + prompt)
    if args.symbol:
        instrument = next(
            (i for i in INSTRUMENTS if i["display_name"] == args.symbol), None
        )
        if instrument is None:
            raise SystemExit(f"Unknown symbol '{args.symbol}'. Known: "
                             f"{[i['display_name'] for i in INSTRUMENTS]}")
    else:
        instrument = INSTRUMENTS[0]  # default to US30 for prompt context

    trades = load_trades(config_ig.TRADE_LOG, symbol_filter=args.symbol)
    params = _load_current_params(instrument)
    prompt = build_prompt(trades, params, instrument)
    ...  # rest of main unchanged
```

- [ ] **Step 6: Verify optimize_ig.py imports without error**

```bash
python -c "import optimize_ig; print('OK')"
```

Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add optimize_ig.py
git commit -m "feat(optimize_ig): read symbol column; add --symbol filter flag; load params from instrument config"
```

---

## Task 9: Update `DEPENDENCIES.md`

**Files:**
- Modify: `docs/DEPENDENCIES.md`

- [ ] **Step 1: Read `docs/DEPENDENCIES.md` fully to find sections to update**

- [ ] **Step 2: Update Section 1 (Architecture)**

Update the architecture diagram / description to show the multi-instrument IG bot with `INSTRUMENTS` list.

- [ ] **Step 3: Update Section 2.1 (evaluate_s5)**

Document the new `cfg` parameter:
- Signature: `evaluate_s5(symbol, daily_df, htf_df, m15_df, allowed_direction, cfg=None)`
- When `cfg` is not None: reads S5 params from dict (IG path)
- When `cfg` is None: `from config_s5 import ...` inside function body (Bitget/backtest path)
- Callers: `bot.py` — no cfg; `backtest.py` — no cfg; `ig_bot.py` — passes `cfg=instrument`

- [ ] **Step 4: Update Section 4.3 (ig_trades.csv)**

Update column list: add `symbol` at position 4 (after `action`). Note total is now 20 columns. Note backward compat: empty `symbol` in old rows treated as "US30" by optimize_ig.py.

- [ ] **Step 5: Update Section 4.4 (ig_state.json)**

Update structure to show `positions`/`pending_orders` dicts keyed by `display_name`. Show the migration: on startup, old `"position"` key is wrapped under `positions["US30"]`.

- [ ] **Step 6: Update Section 5 (Config)**

Document per-instrument config shape. List all 33 required keys. Note `config_ig.py` is now a registry. Note `config_ig_s5.py` is deleted.

- [ ] **Step 7: Update Section 10.3 (Import Timing / Patching)**

Note that the patching mechanism (`setattr` loop) is removed. `cfg` param to `evaluate_s5` is the new approach. The `from config_s5 import` inside `evaluate_s5` body is now only the Bitget/backtest fallback.

- [ ] **Step 8: Commit**

```bash
git add docs/DEPENDENCIES.md
git commit -m "docs(deps): update for multi-instrument IG bot, evaluate_s5 cfg param, new state/CSV formats"
```

---

## Self-Review Against Spec

| Spec Section | Covered by Task |
|---|---|
| §1 Config Structure — per-instrument CONFIG dict, registry | Task 1 |
| §1 config_ig_s5.py deleted | Task 6 (Step 5) |
| §2 evaluate_s5() cfg=None param | Task 2 |
| §2 ig_bot.py patching block deleted | Task 3 |
| §3 positions/pending_orders dicts in state | Task 4 |
| §3 Startup migration from old format | Task 4 |
| §3 dashboard.py positions dict read | Task 7 |
| §4 symbol column in ig_trades.csv | Task 3 (_TRADE_FIELDS) + Task 5 (write rows) |
| §4 optimize_ig.py symbol column + --symbol flag | Task 8 |
| §5 Multi-instrument _tick loop | Task 5 |
| §5 Startup validation (_REQUIRED_KEYS) | Task 3 |
| §6 Dashboard one card per instrument | Task 7 |
| §6 scan_signals/scan_log unchanged | Preserved throughout (no change needed) |
| DEPENDENCIES.md updates | Task 9 |
