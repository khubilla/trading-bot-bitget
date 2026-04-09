# Strategy Manual Test Harness — Design Spec

**Date:** 2026-04-10
**Status:** Approved

---

## Problem

Existing tests in `tests/` are scattered across files by concern (exits, watcher, SL, etc.), not by strategy. They assert correctness but never print what API calls are being made. There is no way to manually run "show me every Bitget API call that S2 entry makes" without reading code.

---

## Goal

Standalone scripts — one per strategy — that:
1. Run without hitting real Bitget APIs (fully mocked)
2. Print every `bc.post` / `bc.get` call with endpoint + full payload as it happens
3. Can be run as `python tests/manual/run_test_s1.py` or `pytest tests/manual/run_test_s1.py -s`
4. Use actual `config_sN.py` values (not hardcoded numbers) — changing config changes the output

---

## File Structure

```
tests/manual/
  __init__.py
  _bc_spy.py          ← context manager that patches bc and prints every call
  _bot_factory.py     ← builds a bare MTFBot without starting the run loop
  run_test_s1.py
  run_test_s2.py
  run_test_s3.py
  run_test_s4.py
  run_test_s5.py
  run_test_s6.py
```

---

## `_bc_spy.py` — The Spy Mock

### What it patches

Patches these on the `trader` module (not on `bitget_client` directly, so the import already in flight is intercepted):

| Patch target | Reason |
|---|---|
| `trader.bc.post` | All order placement, SL/TP, leverage, cancellations |
| `trader.bc.get` | Positions, balance, price, order detail |
| `trader.bc.get_public` | Symbol contracts (called at import) |
| `time.sleep` (in `trader`) | Avoid 2-second wait after place-order |

### Print format

```
─────────────────────────────────────────
[API] POST /api/v2/mix/order/place-order
      {
        "symbol": "BTCUSDT",
        "productType": "USDT-FUTURES",
        "marginMode": "isolated",
        "marginCoin": "USDT",
        "size": "0.002",
        "side": "buy",
        "tradeSide": "open",
        "orderType": "market",
        "force": "ioc"
      }
─────────────────────────────────────────
```

### Mock return values

Each endpoint returns a canned response shaped to what the real API returns, so `open_long` / `open_short` / `_do_scale_in` don't crash mid-execution:

| Endpoint | Canned response |
|---|---|
| `GET /api/v2/mix/account/accounts` | balance = 10 000 USDT |
| `GET /api/v2/mix/market/symbol-price` | markPrice = configurable per script |
| `GET /api/v2/mix/position/all-position` | position at configured fill price + qty |
| `POST /api/v2/mix/order/place-order` | `{"orderId": "mock-order-001"}` |
| `POST /api/v2/mix/order/place-pos-tpsl` | `{"msg": "success"}` |
| `POST /api/v2/mix/order/place-tpsl-order` | `{"data": {"orderId": "plan-mock-001"}}` |
| `GET /api/v2/mix/order/plan-orders` | empty plan order list |
| `POST /api/v2/mix/order/cancel-plan-order` | `{"msg": "success"}` |
| `POST /api/v2/mix/account/set-leverage` | `{"msg": "success"}` |
| `POST /api/v2/mix/order/cancel-all-orders` | `{"msg": "success"}` |
| `GET /api/v2/mix/market/contracts` | minimal contract list for `_sym_info` |

Mark price and position are injected per-script via parameters so that entry buffer / in-window checks pass correctly for each strategy.

### Usage

```python
from tests.manual._bc_spy import bc_spy

with bc_spy(mark_price=50000.0, fill_price=50100.0, qty="0.002"):
    tr.open_long("BTCUSDT", sl_floor=48000, leverage=10, use_s2_exits=True)
```

---

## `_bot_factory.py` — Bare Bot Builder

Builds a `bot.MTFBot` instance without starting the tick loop. Patches `bot.st` (state module) to no-ops so no files are written.

```python
def make_bot(monkeypatch=None) -> bot.MTFBot:
    """Works standalone (monkeypatch=None) or inside pytest."""
```

When `monkeypatch` is None, uses `unittest.mock.patch` to suppress `st.add_scan_log`, `st.add_open_trade`, `st.save_pending_signals`, `_log_trade`, `snapshot.save_snapshot`.

---

## Per-Strategy Scripts

### Dual-mode pattern (all scripts)

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.manual._bc_spy import bc_spy
from tests.manual._bot_factory import make_bot
import config_s1          # ← real config, not hardcoded values
import trader as tr

MARK = 50_000.0           # realistic price for BTCUSDT in this test

def test_s1_entry_long():
    print("\n=== S1 — Entry LONG ===")
    with bc_spy(mark_price=MARK, fill_price=MARK * 1.001, qty="0.002"):
        b = make_bot()
        c = _make_candidate("LONG")
        b._execute_s1(c, balance=10_000.0)

if __name__ == "__main__":
    test_s1_entry_long()
    test_s1_entry_short()
    test_s1_trailing_long()
    test_s1_stop_loss_long()
    print("\n✅ All S1 scenarios done")
```

- Pytest discovers `test_*` functions when run with `pytest -s`
- `__main__` block calls them directly in sequence

### Scenario coverage per strategy

Scenarios are derived from what each strategy actually calls in `bot.py` and `trader.py`.

**S1 — `run_test_s1.py`**
| Scenario | Bot method | Key API calls shown |
|---|---|---|
| Entry LONG | `_execute_s1` | `set-leverage`, `place-order(buy)`, `place-pos-tpsl(SL)`, `place-tpsl-order(moving_plan)` |
| Entry SHORT | `_execute_s1` | same with `sell` side |
| SL: box_low near entry | `_execute_s1` | shows SL computed from `config_s1.S1_SL_BUFFER_PCT` × box_low |
| SL: box_low far → floor | `_execute_s1` | shows SL computed from `config_s1.STOP_LOSS_PCT` × mark |
| Trailing (long) | `tr._place_s1_exits` directly | `place-tpsl-order(moving_plan)` with `rangeRate=config_s1.S1_TRAIL_RANGE_PCT` |

Config used: `config_s1.LEVERAGE`, `config_s1.TRADE_SIZE_PCT`, `config_s1.STOP_LOSS_PCT`, `config_s1.S1_SL_BUFFER_PCT`, `config_s1.TAKE_PROFIT_PCT`, `config_s1.S1_TRAIL_RANGE_PCT`

---

**S2 — `run_test_s2.py`**
| Scenario | Bot method | Key API calls shown |
|---|---|---|
| Entry LONG | `_fire_s2` | `set-leverage`, `place-order(buy)`, `place-pos-tpsl(SL)`, `place-tpsl-order(profit_plan)`, `place-tpsl-order(moving_plan)` |
| Entry SHORT | `_fire_s2` | same with `sell` |
| Scale-in LONG | `_do_scale_in` (S2) | `place-order(buy, scale_in qty)`, plan refresh |
| Trailing refresh | `tr.refresh_plan_exits` | `cancel-plan-order(×N)`, `place-tpsl-order(profit_plan)`, `place-tpsl-order(moving_plan)` |
| SL: fixed at fill | `_fire_s2` | SL = `fill × (1 - config_s2.S2_STOP_LOSS_PCT)` |

Config used: `config_s2.S2_LEVERAGE`, `config_s2.S2_TRADE_SIZE_PCT`, `config_s2.S2_TRAILING_TRIGGER_PCT`, `config_s2.S2_TRAILING_RANGE_PCT`, `config_s2.S2_STOP_LOSS_PCT`

---

**S3 — `run_test_s3.py`**
| Scenario | Bot method | Key API calls shown |
|---|---|---|
| Entry LONG | `_fire_s3` | `set-leverage`, `place-order(buy)`, `place-pos-tpsl(SL)`, `place-tpsl-order(profit_plan)`, `place-tpsl-order(moving_plan)` |
| Entry SHORT | `_fire_s3` | same with `sell` |
| SL: structural (sl_floor) | `_fire_s3` | SL = `max(sl_floor, fill × (1 - config_s3.STOP_LOSS_PCT))` |
| Trailing refresh | `tr.refresh_plan_exits` | plan cancel + replace |

Config used: `config_s3.S3_LEVERAGE`, `config_s3.S3_TRADE_SIZE_PCT`, `config_s3.S3_TRAILING_TRIGGER_PCT`, `config_s3.S3_TRAILING_RANGE_PCT`, `config_s3.STOP_LOSS_PCT`

---

**S4 — `run_test_s4.py`**
| Scenario | Bot method | Key API calls shown |
|---|---|---|
| Entry SHORT | `_fire_s4` | `set-leverage`, `place-order(sell)`, `place-pos-tpsl(SL)`, `place-tpsl-order(profit_plan)`, `place-tpsl-order(moving_plan)` |
| Scale-in SHORT | `_do_scale_in` (S4) | `place-order(sell, scale_in qty)`, plan refresh |
| Trailing refresh | `tr.refresh_plan_exits` | cancel + replace plan orders |

Config used: `config_s4.S4_LEVERAGE`, `config_s4.S4_TRADE_SIZE_PCT`, `config_s4.S4_TRAILING_TRIGGER_PCT`, `config_s4.S4_TRAILING_RANGE_PCT`, `config_s4.S4_STOP_LOSS_PCT`, `config_s4.S4_MAX_ENTRY_BUFFER`, `config_s4.S4_ENTRY_BUFFER`

---

**S5 — `run_test_s5.py`**
| Scenario | Bot method | Key API calls shown |
|---|---|---|
| Entry LONG (via fire_pending) | `_fire_pending` → `_execute_s5` | `place-order(buy)`, `place-pos-tpsl(SL)`, `place-tpsl-order(profit_plan 1:1)`, `place-tpsl-order(moving_plan)` |
| Entry SHORT | same with `sell` | |
| Partial TP refresh | `tr.refresh_plan_exits` | plan cancel + replace showing S5_TRAIL_RANGE_PCT |

Config used: `config_s5.S5_LEVERAGE`, `config_s5.S5_TRADE_SIZE_PCT`, `config_s5.S5_TRAIL_RANGE_PCT`, `config_s5.S5_MAX_ENTRY_BUFFER`

---

**S6 — `run_test_s6.py`**
| Scenario | Bot method | Key API calls shown |
|---|---|---|
| Entry SHORT | `_fire_s6` | `set-leverage`, `place-order(sell)`, `place-pos-tpsl(SL)`, `place-tpsl-order(profit_plan)`, `place-tpsl-order(moving_plan)` |
| Scale-in SHORT | `_do_scale_in` (S6) | `place-order(sell, scale_in qty)`, plan refresh |
| Trailing refresh | `tr.refresh_plan_exits` | cancel + replace showing S6_TRAIL_RANGE_PCT |

Config used: `config_s6.S6_LEVERAGE`, `config_s6.S6_TRADE_SIZE_PCT`, `config_s6.S6_SL_PCT`, `config_s6.S6_TRAILING_TRIGGER_PCT`, `config_s6.S6_TRAIL_RANGE_PCT`

---

## Running

```bash
# Single strategy, standalone
python tests/manual/run_test_s1.py

# Single strategy, pytest with print output
pytest tests/manual/run_test_s1.py -v -s

# All strategies
pytest tests/manual/ -v -s

# All strategies standalone (one-liner)
for f in tests/manual/run_test_s*.py; do python "$f"; done
```

---

## What is NOT in scope

- No real API calls — the spy always intercepts
- No state.json / CSV writes — suppressed in `_bot_factory.py`
- No assertion failures by default — these are inspection scripts, not correctness tests. If a scenario crashes (TypeError, KeyError), that's the signal that something is wrong.
- No IG bot coverage — Bitget only
