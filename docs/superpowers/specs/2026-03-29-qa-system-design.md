# QA System Design

**Date:** 2026-03-29

---

## Problem

No test infrastructure exists. Code changes can silently break strategy logic, cross-bot isolation, or data contracts (state.json / CSV fields) — errors only surface at runtime in paper or live mode.

## Solution

A pytest-based QA suite with pre-recorded fixture data, covering the three highest-risk areas: strategy correctness, cross-bot isolation, and data contracts. A Claude skill (`qa-trading-bot`) runs tests automatically after every code change, auto-fixes failures, and loops until clean.

---

## Architecture

```
tests/
├── conftest.py                  # shared pytest fixtures (load OHLCV JSON, load CSV/JSON state)
├── fixtures/
│   ├── ohlcv_s1_long.json       # candles that produce a LONG signal from evaluate_s1
│   ├── ohlcv_s1_hold.json       # candles that produce HOLD from evaluate_s1
│   ├── ohlcv_s2_long.json
│   ├── ohlcv_s2_hold.json
│   ├── ohlcv_s3_long.json
│   ├── ohlcv_s3_hold.json
│   ├── ohlcv_s4_long.json
│   ├── ohlcv_s4_hold.json
│   ├── ohlcv_s5_long.json
│   └── ohlcv_s5_hold.json
├── test_strategy.py             # evaluate_s1 through evaluate_s5 unit tests
├── test_cross_bot.py            # Bitget/IG isolation tests
└── test_data_contracts.py       # state.json fields, CSV columns
```

**Skill:** `.claude/skills/qa-trading-bot/SKILL.md`

---

## Fixture Format

Each fixture is a JSON file containing three DataFrames (daily, htf/1H, ltf/15m) as lists of OHLCV records. Enough candles for the strategy's lookback windows (200 daily, 100 1H, 100 15m). Captured from real market data, then frozen.

```json
{
  "daily": [{"ts": 0, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05, "vol": 1000}, ...],
  "htf":   [...],
  "ltf":   [...]
}
```

---

## Test Coverage

### `test_strategy.py` — strategy logic (~10 tests)

For each strategy S1–S5:
- `test_sN_long_signal`: feed LONG fixture → assert signal == "LONG" or "SHORT" (not HOLD, not crash)
- `test_sN_hold_signal`: feed HOLD fixture → assert signal == "HOLD"
- `test_sN_return_types`: assert return tuple has correct length and types for the strategy

Signal enum invariant: signal must always be one of `{"LONG", "SHORT", "HOLD", "PENDING_LONG", "PENDING_SHORT"}`.

Return tuple invariants per strategy (from DEPENDENCIES.md Section 2):
- `evaluate_s5` → `(signal, entry_trigger, sl_price, tp_price, ob_low, ob_high, reason)` — 7 elements

### `test_cross_bot.py` — isolation (~3 tests)

- `test_both_bots_import_clean`: import `bot` and `ig_bot` in the same process, assert no exception and no shared mutable state
- `test_ig_uses_config_ig_s5`: after importing `ig_bot`, assert `config_s5.S5_OB_LOOKBACK` has been patched to the `config_ig_s5` value (20, not 50)
- `test_ig_does_not_import_paper_trader`: assert `"paper_trader"` not in `sys.modules` after importing `ig_bot`

**⚠️ Test isolation requirement:** Importing `ig_bot` patches `config_s5` at module level. `test_cross_bot.py` must use `importlib.reload(config_s5)` in teardown to restore Bitget defaults, so `test_strategy.py` tests are not contaminated. `conftest.py` enforces this with an autouse fixture.

### `test_data_contracts.py` — data contracts (~5 tests)

- `test_trades_csv_columns`: load `trades_paper.csv`, assert all 37 columns from DEPENDENCIES.md Section 4 are present
- `test_state_json_top_level_keys`: load `state_paper.json`, assert required keys present: `status`, `balance`, `pair_states`, `open_trades`, `qualified_pairs`, `last_tick`
- `test_paper_state_json_structure`: load `paper_state.json`, assert keys are `balance`, `positions`, `history`, `total_pnl` — confirming it is NOT the same structure as state_paper.json
- `test_paper_state_not_state_paper`: assert the two files have different top-level key sets (catches accidental cross-write)
- `test_ig_trades_csv_columns`: load `ig_trades.csv` (if exists), assert all 19 columns present

---

## QA Skill Behavior

**File:** `.claude/skills/qa-trading-bot/SKILL.md`

**Trigger:** After every code change, before committing.

**Steps:**
1. Run `pytest tests/ -v --tb=short`
2. If exit code 0 → report `✅ QA clean — N tests passing`, stop
3. If failures → for each FAILED test:
   - Read the failing test to understand what it expects
   - Read the source file(s) it's testing
   - Fix the source (never modify tests — tests are the source of truth)
4. Re-run pytest
5. Repeat until clean or 3 consecutive fix attempts fail on the same test (escalate to user with full context)

**Fix loop guard:** If the same test fails 3 times in a row after fix attempts, stop and report to user with: failing test, last fix attempted, and why it's stuck.

---

## Workflow Position

```
check-trading-bot-dependencies   ← runs first (pre-change)
        ↓
[make changes]
        ↓
qa-trading-bot                   ← runs here (post-change, pre-commit)
        ↓
git commit + push
```

The QA skill runs **before** committing. Clean tests are a commit requirement.

---

## What Is NOT Tested

- Live API calls (Bitget, IG) — no network in tests
- Dashboard rendering — frontend JS is out of scope
- Scanner filtering logic — lower risk, add later if needed
- Paper trader simulation accuracy — complex stateful simulation, add later

---

## Dependencies

- `pytest` — add to `requirements.txt`
- No other new dependencies

---

## Success Criteria

- `pytest tests/` passes from a clean checkout with no API keys
- All 3 priority areas covered: strategy logic, cross-bot isolation, data contracts
- QA skill catches a real breakage (e.g., changing evaluate_s5 return tuple length) before commit
