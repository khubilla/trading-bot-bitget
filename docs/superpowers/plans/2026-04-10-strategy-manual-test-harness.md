# Strategy Manual Test Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create `tests/manual/run_test_sN.py` (N=1–6) — standalone scripts that run each strategy's full trade lifecycle against a spy mock, printing every Bitget API call with its exact payload.

**Architecture:** A shared `_bc_spy` context manager patches `trader.bc.post` / `trader.bc.get` + all state/logging side effects. Each `run_test_sN.py` uses the real `config_sN.py` values and calls the same bot/trader methods production uses, so output directly mirrors what would reach Bitget.

**Tech Stack:** Python `unittest.mock`, `contextlib.contextmanager`, existing `bot.py` / `trader.py` / `config_sN.py`

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `tests/manual/__init__.py` | Create | Makes directory a package |
| `tests/manual/_bc_spy.py` | Create | Spy context manager — intercepts bc + suppresses state/log side effects |
| `tests/manual/_bot_factory.py` | Create | Builds a bare `MTFBot` instance without the run loop |
| `tests/manual/run_test_s1.py` | Create | S1 entry (long/short), SL variants, trailing |
| `tests/manual/run_test_s2.py` | Create | S2 entry (long), scale-in, trailing refresh, SL |
| `tests/manual/run_test_s3.py` | Create | S3 entry (long), structural SL, trailing refresh |
| `tests/manual/run_test_s4.py` | Create | S4 entry (short), scale-in, trailing refresh |
| `tests/manual/run_test_s5.py` | Create | S5 entry (long/short), partial TP refresh |
| `tests/manual/run_test_s6.py` | Create | S6 entry (short), scale-in, trailing refresh |

---

## Task 1: `__init__.py` + `_bc_spy.py`

**Files:**
- Create: `tests/manual/__init__.py`
- Create: `tests/manual/_bc_spy.py`

- [ ] **Step 1: Create the empty package marker**

```bash
mkdir -p tests/manual
touch tests/manual/__init__.py
```

- [ ] **Step 2: Write `_bc_spy.py`**

```python
# tests/manual/_bc_spy.py
"""
Spy context manager for manual strategy tests.

Usage:
    with bc_spy(symbol="BTCUSDT", mark_price=50_000.0, hold_side="long"):
        b = make_bot()
        b._fire_s2("BTCUSDT", sig, mark=50_000.0, balance=10_000.0)

Every bc.post / bc.get call is printed with its full payload.
All state/logging side-effects (state.json writes, CSV, snapshots) are suppressed.
"""
import json
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from contextlib import contextmanager
from unittest.mock import patch, MagicMock

import trader


def _print_call(method: str, endpoint: str, payload):
    sep = "─" * 60
    print(f"\n{sep}")
    print(f"[API] {method} {endpoint}")
    if payload:
        print("      " + json.dumps(payload, indent=2).replace("\n", "\n      "))
    print(sep)


@contextmanager
def bc_spy(
    symbol: str = "BTCUSDT",
    mark_price: float = 50_000.0,
    fill_price: float = None,     # defaults to mark_price
    init_qty: float = 0.002,      # qty returned after initial place-order
    scale_in_qty: float = 0.004,  # qty returned on 2nd+ all-position calls (scale-in poll)
    hold_side: str = "long",
):
    """
    Context manager that:
    1. Pre-populates trader._sym_cache for `symbol` so _round_qty/_round_price work.
    2. Patches trader.bc.post and trader.bc.get with spies that print + return canned data.
    3. Patches time.sleep to 0 (avoids 2-second post-order wait).
    4. Suppresses all bot state/logging side-effects.
    """
    _fill = fill_price if fill_price is not None else mark_price

    # Pre-populate symbol cache so _round_qty/_round_price don't need real API
    trader._sym_cache[symbol] = {
        "price_place":   1,      # 1 decimal place (e.g. 50000.1)
        "volume_place":  3,      # 3 decimal places for qty
        "size_mult":     0.001,
        "min_trade_num": 0.001,
    }

    # Track all-position call count so scale-in poll gets updated qty
    _pos_call = [0]

    def _mock_post(endpoint, payload=None, **kw):
        _print_call("POST", endpoint, payload)
        if "place-order" in endpoint:
            return {"data": {"orderId": "mock-order-001"}, "code": "00000"}
        if "place-pos-tpsl" in endpoint:
            return {"msg": "success", "code": "00000"}
        if "place-tpsl-order" in endpoint:
            return {"data": {"orderId": "plan-mock-001"}, "code": "00000"}
        if "cancel-plan-order" in endpoint:
            return {"msg": "success", "code": "00000"}
        if "cancel-all-orders" in endpoint:
            return {"msg": "success", "code": "00000"}
        if "cancel-order" in endpoint:
            return {"msg": "success", "code": "00000"}
        if "set-leverage" in endpoint:
            return {"msg": "success", "code": "00000"}
        if "place-pos-tpsl" in endpoint:
            return {"msg": "success", "code": "00000"}
        return {"msg": "success", "code": "00000"}

    def _mock_get(endpoint, params=None, **kw):
        _print_call("GET ", endpoint, params)
        if "all-position" in endpoint:
            _pos_call[0] += 1
            qty = init_qty if _pos_call[0] <= 1 else scale_in_qty
            return {"data": [{
                "symbol":       symbol,
                "holdSide":     hold_side,
                "openPriceAvg": str(_fill),
                "total":        str(qty),
                "available":    str(qty),
                "unrealizedPL": "0",
                "markPrice":    str(mark_price),
                "marginSize":   str(round(_fill * qty / 10, 4)),
                "leverage":     "10",
            }]}
        if "accounts" in endpoint:
            return {"data": [{"available": "10000", "equity": "10000"}]}
        if "symbol-price" in endpoint:
            return {"data": [{"markPrice": str(mark_price)}]}
        if "plan-orders" in endpoint:
            # Return two existing plan orders so refresh_plan_exits can cancel + replace
            return {"data": {"entrustedList": [
                {
                    "orderId":      "pp-existing-001",
                    "planType":     "profit_plan",
                    "holdSide":     hold_side,
                    "triggerPrice": str(round(mark_price * 1.10, 1)),
                    "size":         str(init_qty / 2),
                },
                {
                    "orderId":      "mp-existing-002",
                    "planType":     "moving_plan",
                    "holdSide":     hold_side,
                    "triggerPrice": str(round(mark_price * 1.10, 1)),
                    "size":         str(init_qty / 2),
                    "rangeRate":    "15",
                },
            ]}}
        if "order/detail" in endpoint:
            return {"data": {"state": "filled", "fillPrice": str(_fill)}}
        if "history-position" in endpoint:
            return {"data": {"list": []}}
        return {"data": [], "code": "00000"}

    def _mock_get_public(endpoint, params=None, **kw):
        _print_call("GET*", endpoint, params)
        if "contracts" in endpoint:
            return {"data": []}
        if "candles" in endpoint:
            return {"data": []}
        if "symbol-price" in endpoint:
            return {"data": [{"markPrice": str(mark_price)}]}
        return {"data": []}

    patches = [
        patch.object(trader.bc, "post",       side_effect=_mock_post),
        patch.object(trader.bc, "get",        side_effect=_mock_get),
        patch.object(trader.bc, "get_public", side_effect=_mock_get_public),
        patch("time.sleep"),                          # suppress all sleeps
        # Suppress bot state / logging side-effects
        patch("bot.st.add_scan_log",          new=MagicMock()),
        patch("bot.st.add_open_trade",        new=MagicMock()),
        patch("bot.st.save_pending_signals",  new=MagicMock()),
        patch("bot.st.get_pair_state",        return_value={}),
        patch("bot.st.patch_pair_state",      new=MagicMock()),
        patch("bot.st.update_open_trade_margin",    new=MagicMock()),
        patch("bot.st.update_position_memory",      new=MagicMock()),
        patch("bot.st.is_pair_paused",        return_value=False),
        patch("bot._log_trade",               new=MagicMock()),
        patch("bot.snapshot.save_snapshot",   new=MagicMock()),
        patch("bot.config.CLAUDE_FILTER_ENABLED", new=False),
        patch("bot.PAPER_MODE",               new=False),
    ]

    started = []
    try:
        for p in patches:
            started.append(p.start())
        yield
    finally:
        for p in reversed(patches):
            p.stop()
```

- [ ] **Step 3: Verify the spy imports cleanly**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
source venv/bin/activate
python -c "from tests.manual._bc_spy import bc_spy; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add tests/manual/__init__.py tests/manual/_bc_spy.py
git commit -m "feat(manual-tests): add _bc_spy context manager"
```

---

## Task 2: `_bot_factory.py`

**Files:**
- Create: `tests/manual/_bot_factory.py`

- [ ] **Step 1: Write `_bot_factory.py`**

```python
# tests/manual/_bot_factory.py
"""
Builds a bare MTFBot instance without starting the run loop.

Usage (always inside a bc_spy context):
    with bc_spy(...):
        b = make_bot()
        b._fire_s2("BTCUSDT", sig, mark=50_000.0, balance=10_000.0)
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import threading
import bot


def make_bot() -> bot.MTFBot:
    """
    Creates MTFBot via object.__new__ (bypasses __init__/run loop).
    Call inside bc_spy() — that context manager handles all state patches.
    """
    b = object.__new__(bot.MTFBot)
    b.pending_signals  = {}
    b.active_positions = {}
    b._trade_lock      = threading.Lock()
    b.running          = True
    b.sentiment        = type("Sentiment", (), {"direction": "NEUTRAL"})()
    return b
```

- [ ] **Step 2: Verify it imports cleanly**

```bash
python -c "from tests.manual._bot_factory import make_bot; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add tests/manual/_bot_factory.py
git commit -m "feat(manual-tests): add _bot_factory helper"
```

---

## Task 3: `run_test_s1.py`

S1 fires via `_execute_s1`. Covers: entry LONG, entry SHORT, two SL variants (box_low near vs far), trailing.

**Files:**
- Create: `tests/manual/run_test_s1.py`

- [ ] **Step 1: Write the script**

```python
# tests/manual/run_test_s1.py
"""
S1 manual test — RSI/ADX momentum strategy.

Run standalone:  python tests/manual/run_test_s1.py
Run via pytest:  pytest tests/manual/run_test_s1.py -v -s
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import pandas as pd
from tests.manual._bc_spy import bc_spy
from tests.manual._bot_factory import make_bot
import config_s1

SYMBOL = "BTCUSDT"
MARK   = 50_000.0


def _make_candidate(sig: str, box_low_near: bool = True) -> dict:
    """Build an S1 candidate dict. box_low_near controls which SL path fires."""
    bh = MARK * 1.02
    # Near: box_low within STOP_LOSS_PCT → SL uses box_low * (1 - S1_SL_BUFFER_PCT)
    # Far:  box_low far below entry → SL uses mark * (1 - STOP_LOSS_PCT)
    bl = MARK * 0.995 if box_low_near else MARK * 0.90
    return {
        "symbol":       SYMBOL,
        "sig":          sig,
        "ltf_df":       pd.DataFrame(),
        "sr_pct":       config_s1.S1_MIN_SR_CLEARANCE * 100 + 1.0,  # pass gate
        "s1_bh":        bh,
        "s1_bl":        bl,
        "rsi_val":      72.0,
        "adx_val":      28.0,
        "htf_bull":     sig == "LONG",
        "htf_bear":     sig == "SHORT",
        "is_coil":      False,
        "priority_rank": 1,
    }


def test_s1_entry_long():
    print(f"\n{'='*60}")
    print(f"S1 — Entry LONG  (box_low near, SL from box_low)")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, hold_side="long"):
        b = make_bot()
        b._execute_s1(_make_candidate("LONG", box_low_near=True), balance=10_000.0)


def test_s1_entry_short():
    print(f"\n{'='*60}")
    print(f"S1 — Entry SHORT  (box_high near, SL from box_high)")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, hold_side="short"):
        b = make_bot()
        b._execute_s1(_make_candidate("SHORT", box_low_near=True), balance=10_000.0)


def test_s1_sl_box_low_far():
    print(f"\n{'='*60}")
    print(f"S1 — Entry LONG  (box_low far → SL floored at STOP_LOSS_PCT)")
    print(f"  config_s1.STOP_LOSS_PCT = {config_s1.STOP_LOSS_PCT}")
    print(f"  Expected SL ≈ {MARK * (1 - config_s1.STOP_LOSS_PCT):.1f}")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, hold_side="long"):
        b = make_bot()
        b._execute_s1(_make_candidate("LONG", box_low_near=False), balance=10_000.0)


def test_s1_trailing_long():
    """Calls _place_s1_exits directly to show moving_plan payload."""
    import trader as tr
    print(f"\n{'='*60}")
    print(f"S1 — Trailing stop (moving_plan)  rangeRate={config_s1.S1_TRAIL_RANGE_PCT}")
    print(f"{'='*60}")
    trail_trig = round(MARK * (1 + config_s1.TAKE_PROFIT_PCT), 1)
    with bc_spy(symbol=SYMBOL, mark_price=MARK, hold_side="long"):
        tr._place_s1_exits(SYMBOL, "long", "0.002",
                           round(MARK * 0.97, 1),   # sl_trig
                           round(MARK * 0.965, 1),  # sl_exec
                           trail_trig,
                           config_s1.S1_TRAIL_RANGE_PCT)


if __name__ == "__main__":
    test_s1_entry_long()
    test_s1_entry_short()
    test_s1_sl_box_low_far()
    test_s1_trailing_long()
    print(f"\n{'='*60}")
    print("S1 — all scenarios complete")
    print(f"{'='*60}")
```

- [ ] **Step 2: Run standalone and verify output**

```bash
python tests/manual/run_test_s1.py
```

Expected: 4 sections each showing API calls — `set-leverage` (×2), `place-order`, `all-position`, then `place-pos-tpsl` + `place-tpsl-order`. No tracebacks.

- [ ] **Step 3: Run via pytest**

```bash
pytest tests/manual/run_test_s1.py -v -s
```

Expected: 4 tests PASSED, same printed output visible.

- [ ] **Step 4: Commit**

```bash
git add tests/manual/run_test_s1.py
git commit -m "feat(manual-tests): S1 entry, SL variants, trailing"
```

---

## Task 4: `run_test_s2.py`

S2 fires via `_fire_s2`. Covers: entry LONG, scale-in LONG, trailing refresh.
S2 is LONG-only (breakout above daily consolidation box).

**Files:**
- Create: `tests/manual/run_test_s2.py`

- [ ] **Step 1: Write the script**

```python
# tests/manual/run_test_s2.py
"""
S2 manual test — Daily momentum coil breakout (LONG only).

Run standalone:  python tests/manual/run_test_s2.py
Run via pytest:  pytest tests/manual/run_test_s2.py -v -s
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.manual._bc_spy import bc_spy
from tests.manual._bot_factory import make_bot
import config_s2
import trader as tr

SYMBOL = "BTCUSDT"
MARK   = 50_000.0
BOX_H  = MARK          # trigger == box_high
BOX_L  = MARK * 0.93   # 7% range — roughly realistic


def _make_sig() -> dict:
    return {
        "strategy":           "S2",
        "side":               "LONG",
        "trigger":            BOX_H,
        "s2_bh":              BOX_H,
        "s2_bl":              BOX_L,
        "snap_daily_rsi":     72.5,
        "snap_box_range_pct": round((BOX_H - BOX_L) / BOX_L * 100, 3),
        "snap_sentiment":     "BULLISH",
        "priority_rank":      1,
        "priority_score":     35.0,
    }


def test_s2_entry_long():
    print(f"\n{'='*60}")
    print(f"S2 — Entry LONG")
    print(f"  leverage={config_s2.S2_LEVERAGE}  size={config_s2.S2_TRADE_SIZE_PCT*0.5*100:.0f}% of equity (50% initial)")
    print(f"  SL = fill × (1 - {config_s2.S2_STOP_LOSS_PCT}) ≈ {MARK*(1-config_s2.S2_STOP_LOSS_PCT):.1f}")
    print(f"  trail trigger = fill × (1 + {config_s2.S2_TRAILING_TRIGGER_PCT}) ≈ {MARK*(1+config_s2.S2_TRAILING_TRIGGER_PCT):.1f}")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, hold_side="long"):
        b = make_bot()
        b._fire_s2(SYMBOL, _make_sig(), mark=MARK, balance=10_000.0)


def test_s2_scale_in_long():
    print(f"\n{'='*60}")
    print(f"S2 — Scale-in LONG  (+{config_s2.S2_TRADE_SIZE_PCT*0.5*100:.0f}% of equity)")
    print(f"  in-window: mark must be between {BOX_H:.1f} and {BOX_H*(1+config_s2.S2_MAX_ENTRY_BUFFER):.1f}")
    print(f"{'='*60}")
    # mark_price must satisfy: BOX_H <= mark <= BOX_H * (1 + S2_MAX_ENTRY_BUFFER)
    mark_in_window = BOX_H * 1.01
    ap = {
        "side":                     "LONG",
        "strategy":                 "S2",
        "box_high":                 BOX_H,
        "box_low":                  BOX_L,
        "scale_in_pending":         True,
        "scale_in_trade_size_pct":  config_s2.S2_TRADE_SIZE_PCT,
        "qty":                      0.0,   # triggers scale-in poll from 0
        "trade_id":                 "test-trade-001",
    }
    with bc_spy(symbol=SYMBOL, mark_price=mark_in_window, init_qty=0.002, scale_in_qty=0.004, hold_side="long"):
        b = make_bot()
        b.active_positions[SYMBOL] = ap
        b._do_scale_in(SYMBOL, ap)


def test_s2_trailing_refresh():
    print(f"\n{'='*60}")
    print(f"S2 — Trailing refresh (plan cancel + replace with new qty)")
    print(f"  rangeRate={config_s2.S2_TRAILING_RANGE_PCT}")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, scale_in_qty=0.004, hold_side="long"):
        tr.refresh_plan_exits(SYMBOL, "long", new_trail_trigger=MARK * 1.10)


if __name__ == "__main__":
    test_s2_entry_long()
    test_s2_scale_in_long()
    test_s2_trailing_refresh()
    print(f"\n{'='*60}")
    print("S2 — all scenarios complete")
    print(f"{'='*60}")
```

- [ ] **Step 2: Run standalone**

```bash
python tests/manual/run_test_s2.py
```

Expected: 3 sections. Entry shows `set-leverage` × 2, `place-order(buy)`, `all-position`, `place-pos-tpsl(SL)`, `place-tpsl-order(profit_plan)`, `place-tpsl-order(moving_plan)`. Scale-in shows `place-order(buy)` + poll + plan cancel/replace. No tracebacks.

- [ ] **Step 3: Run via pytest**

```bash
pytest tests/manual/run_test_s2.py -v -s
```

Expected: 3 tests PASSED.

- [ ] **Step 4: Commit**

```bash
git add tests/manual/run_test_s2.py
git commit -m "feat(manual-tests): S2 entry, scale-in, trailing refresh"
```

---

## Task 5: `run_test_s3.py`

S3 fires via `_fire_s3`. LONG-only. Covers: entry LONG, structural SL (sl_floor from pending sig), trailing refresh.

**Files:**
- Create: `tests/manual/run_test_s3.py`

- [ ] **Step 1: Write the script**

```python
# tests/manual/run_test_s3.py
"""
S3 manual test — Pullback to support (LONG only).

Run standalone:  python tests/manual/run_test_s3.py
Run via pytest:  pytest tests/manual/run_test_s3.py -v -s
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.manual._bc_spy import bc_spy
from tests.manual._bot_factory import make_bot
import config_s3
import trader as tr

SYMBOL = "BTCUSDT"
MARK   = 50_000.0
S3_SL  = MARK * 0.96   # structural SL ~4% below entry — within STOP_LOSS_PCT cap


def _make_sig() -> dict:
    return {
        "strategy":              "S3",
        "side":                  "LONG",
        "trigger":               MARK,
        "s3_sl":                 S3_SL,
        "snap_adx":              28.5,
        "snap_entry_trigger":    round(MARK, 8),
        "snap_sl":               round(S3_SL, 8),
        "snap_sentiment":        "BULLISH",
        "snap_sr_clearance_pct": 8.0,
        "priority_rank":         1,
        "priority_score":        28.0,
    }


def test_s3_entry_long():
    print(f"\n{'='*60}")
    print(f"S3 — Entry LONG  (structural SL from sig)")
    print(f"  leverage={config_s3.S3_LEVERAGE}  size={config_s3.S3_TRADE_SIZE_PCT*100:.0f}% of equity")
    print(f"  s3_sl={S3_SL:.1f}  (max(sl_floor={S3_SL:.1f}, fill*(1-STOP_LOSS_PCT)={MARK*(1-config_s3.STOP_LOSS_PCT):.1f}))")
    print(f"  trail trigger ≈ fill × (1 + {config_s3.S3_TRAILING_TRIGGER_PCT}) = {MARK*(1+config_s3.S3_TRAILING_TRIGGER_PCT):.1f}")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, hold_side="long"):
        b = make_bot()
        b._fire_s3(SYMBOL, _make_sig(), mark=MARK, balance=10_000.0)


def test_s3_sl_capped_by_pct():
    """sl_floor far below entry → SL capped at fill*(1-STOP_LOSS_PCT)."""
    sl_far = MARK * 0.80   # 20% below — will be overridden by STOP_LOSS_PCT cap
    sig = _make_sig()
    sig["s3_sl"] = sl_far
    print(f"\n{'='*60}")
    print(f"S3 — Entry LONG  (sl_floor far → capped at STOP_LOSS_PCT={config_s3.STOP_LOSS_PCT})")
    print(f"  s3_sl={sl_far:.1f}  capped at fill*(1-{config_s3.STOP_LOSS_PCT}) = {MARK*(1-config_s3.STOP_LOSS_PCT):.1f}")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, hold_side="long"):
        b = make_bot()
        b._fire_s3(SYMBOL, sig, mark=MARK, balance=10_000.0)


def test_s3_trailing_refresh():
    print(f"\n{'='*60}")
    print(f"S3 — Trailing refresh  rangeRate={config_s3.S3_TRAILING_RANGE_PCT}")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, scale_in_qty=0.002, hold_side="long"):
        tr.refresh_plan_exits(SYMBOL, "long", new_trail_trigger=MARK * 1.10)


if __name__ == "__main__":
    test_s3_entry_long()
    test_s3_sl_capped_by_pct()
    test_s3_trailing_refresh()
    print(f"\n{'='*60}")
    print("S3 — all scenarios complete")
    print(f"{'='*60}")
```

- [ ] **Step 2: Run standalone**

```bash
python tests/manual/run_test_s3.py
```

Expected: 3 sections, no tracebacks. Note SL price differences between `test_s3_entry_long` (structural SL) and `test_s3_sl_capped_by_pct` (percentage SL).

- [ ] **Step 3: Run via pytest**

```bash
pytest tests/manual/run_test_s3.py -v -s
```

Expected: 3 tests PASSED.

- [ ] **Step 4: Commit**

```bash
git add tests/manual/run_test_s3.py
git commit -m "feat(manual-tests): S3 entry, SL variants, trailing refresh"
```

---

## Task 6: `run_test_s4.py`

S4 fires via `_fire_s4`. SHORT-only. Covers: entry SHORT, scale-in SHORT, trailing refresh.

**Files:**
- Create: `tests/manual/run_test_s4.py`

- [ ] **Step 1: Write the script**

```python
# tests/manual/run_test_s4.py
"""
S4 manual test — RSI divergence spike-reversal (SHORT only).

Run standalone:  python tests/manual/run_test_s4.py
Run via pytest:  pytest tests/manual/run_test_s4.py -v -s
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.manual._bc_spy import bc_spy
from tests.manual._bot_factory import make_bot
import config_s4
import trader as tr

SYMBOL   = "BTCUSDT"
MARK     = 50_000.0
PREV_LOW = MARK / (1 - config_s4.S4_ENTRY_BUFFER)   # ≈ entry = prev_low*(1-ENTRY_BUFFER)
S4_SL    = MARK * (1 + 0.50 / config_s4.S4_LEVERAGE) # computed inside _fire_s4


def _make_sig() -> dict:
    return {
        "strategy":          "S4",
        "side":              "SHORT",
        "trigger":           MARK,
        "s4_sl":             S4_SL,
        "prev_low":          PREV_LOW,
        "snap_rsi":          45.0,
        "snap_rsi_peak":     85.0,
        "snap_spike_body_pct": 65.0,
        "snap_rsi_div":      True,
        "snap_rsi_div_str":  "RSI divergence",
        "snap_sentiment":    "BEARISH",
        "priority_rank":     1,
        "priority_score":    22.0,
    }


def test_s4_entry_short():
    print(f"\n{'='*60}")
    print(f"S4 — Entry SHORT  (RSI divergence reversal)")
    print(f"  leverage={config_s4.S4_LEVERAGE}  size={config_s4.S4_TRADE_SIZE_PCT*0.5*100:.0f}% equity (50% initial)")
    print(f"  SL = mark*(1 + 0.50/{config_s4.S4_LEVERAGE}) = {S4_SL:.2f}")
    print(f"  trail trigger = fill*(1 - {config_s4.S4_TRAILING_TRIGGER_PCT}) ≈ {MARK*(1-config_s4.S4_TRAILING_TRIGGER_PCT):.1f}")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, hold_side="short"):
        b = make_bot()
        b._fire_s4(SYMBOL, _make_sig(), mark=MARK, balance=10_000.0)


def test_s4_scale_in_short():
    print(f"\n{'='*60}")
    print(f"S4 — Scale-in SHORT  (+{config_s4.S4_TRADE_SIZE_PCT*0.5*100:.0f}% of equity)")
    print(f"  in-window: mark must be between")
    print(f"    {PREV_LOW*(1-config_s4.S4_MAX_ENTRY_BUFFER):.1f} and {PREV_LOW*(1-config_s4.S4_ENTRY_BUFFER):.1f}")
    print(f"{'='*60}")
    # mark must satisfy: prev_low*(1-MAX_ENTRY_BUFFER) <= mark <= prev_low*(1-ENTRY_BUFFER)
    mark_in_window = PREV_LOW * (1 - config_s4.S4_ENTRY_BUFFER * 1.5)
    ap = {
        "side":                    "SHORT",
        "strategy":                "S4",
        "box_high":                S4_SL,
        "box_low":                 MARK,
        "scale_in_pending":        True,
        "scale_in_trade_size_pct": config_s4.S4_TRADE_SIZE_PCT,
        "s4_prev_low":             PREV_LOW,
        "qty":                     0.0,
        "trade_id":                "test-trade-s4-001",
    }
    with bc_spy(symbol=SYMBOL, mark_price=mark_in_window, init_qty=0.002, scale_in_qty=0.004, hold_side="short"):
        b = make_bot()
        b.active_positions[SYMBOL] = ap
        b._do_scale_in(SYMBOL, ap)


def test_s4_trailing_refresh():
    print(f"\n{'='*60}")
    print(f"S4 — Trailing refresh  rangeRate={config_s4.S4_TRAILING_RANGE_PCT}")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, scale_in_qty=0.004, hold_side="short"):
        tr.refresh_plan_exits(SYMBOL, "short", new_trail_trigger=MARK * 0.90)


if __name__ == "__main__":
    test_s4_entry_short()
    test_s4_scale_in_short()
    test_s4_trailing_refresh()
    print(f"\n{'='*60}")
    print("S4 — all scenarios complete")
    print(f"{'='*60}")
```

- [ ] **Step 2: Run standalone**

```bash
python tests/manual/run_test_s4.py
```

Expected: 3 sections. Entry shows `sell` side in `place-order`. Scale-in shows additional `sell` order + plan refresh. No tracebacks.

- [ ] **Step 3: Run via pytest**

```bash
pytest tests/manual/run_test_s4.py -v -s
```

Expected: 3 tests PASSED.

- [ ] **Step 4: Commit**

```bash
git add tests/manual/run_test_s4.py
git commit -m "feat(manual-tests): S4 entry SHORT, scale-in, trailing refresh"
```

---

## Task 7: `run_test_s5.py`

S5 fires via `_fire_pending`. Covers: entry LONG (with partial TP at 1:1 R:R), entry SHORT, trailing refresh.

**Files:**
- Create: `tests/manual/run_test_s5.py`

- [ ] **Step 1: Write the script**

```python
# tests/manual/run_test_s5.py
"""
S5 manual test — SMC order block entry.

Run standalone:  python tests/manual/run_test_s5.py
Run via pytest:  pytest tests/manual/run_test_s5.py -v -s
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.manual._bc_spy import bc_spy
from tests.manual._bot_factory import make_bot
import config_s5
import trader as tr

SYMBOL    = "BTCUSDT"
MARK      = 50_000.0
S5_SL     = MARK * 0.96     # OB-based structural SL (4% below)
S5_TP     = MARK * 1.08     # hard TP (2:1 R:R on 8% target, SL=4%)
S5_OB_LOW = MARK * 0.97
S5_OB_HI  = MARK * 0.98


def _make_sig(side: str) -> dict:
    sl = S5_SL if side == "LONG" else MARK * 1.04
    tp = S5_TP if side == "LONG" else MARK * 0.92
    return {
        "strategy":          "S5",
        "side":              side,
        "trigger":           MARK,
        "sl":                sl,
        "tp":                tp,
        "ob_low":            S5_OB_LOW,
        "ob_high":           S5_OB_HI,
        "sentiment":         "BULLISH" if side == "LONG" else "BEARISH",
        "rr":                2.0,
        "sr_clearance_pct":  12.0,
        "priority_rank":     1,
    }


def test_s5_entry_long():
    print(f"\n{'='*60}")
    print(f"S5 — Entry LONG  (OB zone entry, partial TP at 1:1 R:R)")
    print(f"  leverage={config_s5.S5_LEVERAGE}  size={config_s5.S5_TRADE_SIZE_PCT*100:.0f}% of equity")
    print(f"  SL={S5_SL:.1f}  hard_TP={S5_TP:.1f}")
    print(f"  partial TP (profit_plan) at 1:1 R:R ≈ {MARK + (MARK - S5_SL):.1f}")
    print(f"  trail rangeRate={config_s5.S5_TRAIL_RANGE_PCT}")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, hold_side="long"):
        b = make_bot()
        b._fire_pending(SYMBOL, _make_sig("LONG"), mark_now=MARK, balance=10_000.0)


def test_s5_entry_short():
    print(f"\n{'='*60}")
    print(f"S5 — Entry SHORT  (OB zone entry, partial TP at 1:1 R:R below entry)")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, hold_side="short"):
        b = make_bot()
        b._fire_pending(SYMBOL, _make_sig("SHORT"), mark_now=MARK, balance=10_000.0)


def test_s5_trailing_refresh():
    print(f"\n{'='*60}")
    print(f"S5 — Partial TP refresh  rangeRate={config_s5.S5_TRAIL_RANGE_PCT}")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, scale_in_qty=0.002, hold_side="long"):
        tr.refresh_plan_exits(SYMBOL, "long", new_trail_trigger=MARK * 1.04)


if __name__ == "__main__":
    test_s5_entry_long()
    test_s5_entry_short()
    test_s5_trailing_refresh()
    print(f"\n{'='*60}")
    print("S5 — all scenarios complete")
    print(f"{'='*60}")
```

- [ ] **Step 2: Run standalone**

```bash
python tests/manual/run_test_s5.py
```

Expected: 3 sections. Entry LONG shows `profit_plan` at 1:1 R:R AND `moving_plan`. Entry SHORT shows same pattern inverted. Refresh shows plan cancel + replace.

- [ ] **Step 3: Run via pytest**

```bash
pytest tests/manual/run_test_s5.py -v -s
```

Expected: 3 tests PASSED.

- [ ] **Step 4: Commit**

```bash
git add tests/manual/run_test_s5.py
git commit -m "feat(manual-tests): S5 entry LONG/SHORT, partial TP refresh"
```

---

## Task 8: `run_test_s6.py`

S6 fires via `_fire_s6`. SHORT-only. Covers: entry SHORT, scale-in SHORT, trailing refresh.

**Files:**
- Create: `tests/manual/run_test_s6.py`

- [ ] **Step 1: Write the script**

```python
# tests/manual/run_test_s6.py
"""
S6 manual test — V-formation liquidity sweep (SHORT only).

Run standalone:  python tests/manual/run_test_s6.py
Run via pytest:  pytest tests/manual/run_test_s6.py -v -s
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.manual._bc_spy import bc_spy
from tests.manual._bot_factory import make_bot
import config_s6
import trader as tr

SYMBOL     = "BTCUSDT"
MARK       = 50_000.0
PEAK_LEVEL = MARK * 1.50    # peak level well above current price (fakeout resolved)
S6_SL      = MARK * (1 + config_s6.S6_SL_PCT / config_s6.S6_LEVERAGE)


def _make_sig() -> dict:
    return {
        "strategy":             "S6",
        "side":                 "SHORT",
        "peak_level":           PEAK_LEVEL,
        "sl":                   S6_SL,
        "drop_pct":             0.35,
        "rsi_at_peak":          78.0,
        "fakeout_seen":         True,
        "snap_s6_peak":         round(PEAK_LEVEL, 8),
        "snap_s6_drop_pct":     35.0,
        "snap_s6_rsi_at_peak":  78.0,
        "snap_sentiment":       "BEARISH",
        "priority_rank":        1,
        "priority_score":       18.0,
    }


def test_s6_entry_short():
    print(f"\n{'='*60}")
    print(f"S6 — Entry SHORT  (V-formation fakeout confirmed)")
    print(f"  leverage={config_s6.S6_LEVERAGE}  size={config_s6.S6_TRADE_SIZE_PCT*0.5*100:.0f}% equity (50% initial)")
    print(f"  SL = mark*(1 + {config_s6.S6_SL_PCT}/{config_s6.S6_LEVERAGE}) = {S6_SL:.2f}")
    print(f"  trail trigger = fill*(1 - {config_s6.S6_TRAILING_TRIGGER_PCT}) ≈ {MARK*(1-config_s6.S6_TRAILING_TRIGGER_PCT):.1f}")
    print(f"  trail rangeRate={config_s6.S6_TRAIL_RANGE_PCT}")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, hold_side="short"):
        b = make_bot()
        b._fire_s6(SYMBOL, _make_sig(), mark=MARK, balance=10_000.0)


def test_s6_scale_in_short():
    print(f"\n{'='*60}")
    print(f"S6 — Scale-in SHORT  (+{config_s6.S6_TRADE_SIZE_PCT*0.5*100:.0f}% of equity)")
    print(f"  in-window: mark < peak_level = {PEAK_LEVEL:.1f}")
    print(f"{'='*60}")
    # mark must be < peak_level for in_window check to pass
    ap = {
        "side":                    "SHORT",
        "strategy":                "S6",
        "box_high":                S6_SL,
        "box_low":                 PEAK_LEVEL,
        "scale_in_pending":        True,
        "scale_in_trade_size_pct": config_s6.S6_TRADE_SIZE_PCT,
        "qty":                     0.0,
        "trade_id":                "test-trade-s6-001",
    }
    with bc_spy(symbol=SYMBOL, mark_price=MARK, init_qty=0.002, scale_in_qty=0.004, hold_side="short"):
        b = make_bot()
        b.active_positions[SYMBOL] = ap
        b._do_scale_in(SYMBOL, ap)


def test_s6_trailing_refresh():
    print(f"\n{'='*60}")
    print(f"S6 — Trailing refresh  rangeRate={config_s6.S6_TRAIL_RANGE_PCT}")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, scale_in_qty=0.004, hold_side="short"):
        tr.refresh_plan_exits(SYMBOL, "short", new_trail_trigger=MARK * 0.90)


if __name__ == "__main__":
    test_s6_entry_short()
    test_s6_scale_in_short()
    test_s6_trailing_refresh()
    print(f"\n{'='*60}")
    print("S6 — all scenarios complete")
    print(f"{'='*60}")
```

- [ ] **Step 2: Run standalone**

```bash
python tests/manual/run_test_s6.py
```

Expected: 3 sections. Entry shows `sell` side, SL ~5% above mark (50% SL_PCT / 10x leverage), trail trigger 10% below fill. Scale-in shows additional `sell` + plan refresh. No tracebacks.

- [ ] **Step 3: Run via pytest**

```bash
pytest tests/manual/run_test_s6.py -v -s
```

Expected: 3 tests PASSED.

- [ ] **Step 4: Commit**

```bash
git add tests/manual/run_test_s6.py
git commit -m "feat(manual-tests): S6 entry SHORT, scale-in, trailing refresh"
```

---

## Task 9: Final smoke test — all strategies

- [ ] **Step 1: Run all scripts standalone**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
source venv/bin/activate
for f in tests/manual/run_test_s*.py; do
    echo ""; echo ">>> $f"; python "$f" || echo "FAILED: $f"
done
```

Expected: All 6 scripts complete without tracebacks. Each prints API call blocks for every scenario.

- [ ] **Step 2: Run all via pytest**

```bash
pytest tests/manual/ -v -s --tb=short
```

Expected: All tests PASSED.

- [ ] **Step 3: Verify no leakage to real files**

```bash
ls -la state.json ig_state.json trades.csv trades_paper.csv 2>/dev/null && echo "LEAKAGE" || echo "Clean — no state files written"
```

Expected: `Clean — no state files written` (or files exist from before but are unmodified — check with `git status`).

- [ ] **Step 4: Final commit**

```bash
git add tests/manual/
git commit -m "feat(manual-tests): strategy test harness complete — S1-S6 bc spy scripts"
```

---

## Self-Review Against Spec

| Spec requirement | Covered by |
|---|---|
| Standalone `python run_test_sN.py` | `__main__` block in every script |
| `pytest tests/manual/run_test_sN.py -s` | `test_*` function naming |
| Print every `bc.post` / `bc.get` call | `_print_call()` in `_bc_spy.py` |
| Use real `config_sN.py` values | Every script imports its own config and reads from it |
| No real API calls | `patch.object(trader.bc, 'post/get')` |
| No state.json / CSV writes | All `st.*`, `_log_trade`, `snapshot` patched in `_bc_spy` |
| Entry (long/short) | S1, S2, S3 (LONG), S4 (SHORT), S5, S6 (SHORT) |
| Scale-in | S2 (long), S4 (short), S6 (short) |
| Partial TP + trailing | All 6 — via `place-tpsl-order(profit_plan + moving_plan)` |
| Stop loss | S1 (both variants), S3 (both variants) |
| `_sym_cache` pre-populated | `bc_spy.__enter__` sets `trader._sym_cache[symbol]` |
