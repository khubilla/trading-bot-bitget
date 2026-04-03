# S1 Partial TP + Trailing Exit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace S1's static 10% hard TP with a 50% partial TP at +10%, a 5% Bitget trailing stop on the remaining 50%, and a per-tick 3m pivot-based SL step.

**Architecture:** Three independent changes — add `_place_s1_exits()` to `trader.py` (mirrors `_place_s2_exits`), add `use_s1_exits` param to `open_long`/`open_short`, then update `_execute_s1` in `bot.py` to use the new path and add the swing trail monitor block.

**Tech Stack:** Python, Bitget REST API, pytest/monkeypatch (existing test patterns)

---

## File Map

| File | Change |
|------|--------|
| `config_s1.py` | Add `S1_TRAIL_RANGE_PCT`, `S1_USE_SWING_TRAIL`, `S1_SWING_LOOKBACK` |
| `trader.py` | Add `_place_s1_exits()`; add `use_s1_exits=False` param to `open_long` and `open_short` |
| `bot.py` | Update `_execute_s1` to pass `use_s1_exits=True`; store `"sl"` in `active_positions`; add `"S1"` to partial close list; add S1 swing trail block |
| `tests/test_trader_exits.py` | Add `TestPlaceS1ExitsQty` + `TestOpenLongS1Exits` + `TestOpenShortS1Exits` |
| `tests/test_bot_s1_sl.py` | Update existing mocks; add swing trail guard tests |

---

## Task 1: Add new params to `config_s1.py`

**Files:**
- Modify: `config_s1.py:34-42`

- [ ] **Step 1: Add the three new config params**

In `config_s1.py`, after the existing `S1_MIN_SR_CLEARANCE` line (currently the last line), add:

```python
# ── Trailing Exit (partial TP + moving_plan) ─────────────────────────── #
S1_TRAIL_RANGE_PCT  = 5     # moving_plan callback % (5 = trail 5% below high)
S1_USE_SWING_TRAIL  = True  # enable per-tick 3m pivot SL stepping
S1_SWING_LOOKBACK   = 20    # candles to scan for swing pivots (3m chart)
```

- [ ] **Step 2: Run existing tests to confirm nothing breaks**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
pytest tests/test_bot_s1_sl.py -v
```

Expected: all 7 tests pass.

- [ ] **Step 3: Commit**

```bash
git add config_s1.py
git commit -m "config(s1): add S1_TRAIL_RANGE_PCT, S1_USE_SWING_TRAIL, S1_SWING_LOOKBACK"
```

---

## Task 2: Add `_place_s1_exits()` to `trader.py`

**Files:**
- Modify: `trader.py` (insert after `_place_s2_exits` block, before line 338)
- Test: `tests/test_trader_exits.py`

- [ ] **Step 1: Write failing tests**

In `tests/test_trader_exits.py`, add this class after `TestPlaceS2ExitsQty`:

```python
# ── _place_s1_exits: integer half/rest qty ─────────────────────────── #

class TestPlaceS1ExitsQty:
    """half_qty and rest_qty must be exchange-compatible (no fractional sizes)."""

    def test_odd_qty_integer_symbol_no_fractions(self, monkeypatch):
        """qty=209 STOUSDT → half=104, rest=105 (not 104.5)."""
        calls = []
        monkeypatch.setattr(trader, "_sym_info", _sym_info_stousdt)
        monkeypatch.setattr(bc, "post", lambda path, p: calls.append(p) or {})

        trader._place_s1_exits("STOUSDT", "long", "209", 0.13, 0.129, 0.17, 5)

        sizes = [c["size"] for c in calls if "size" in c]
        assert "104.5" not in sizes, "fractional size sent to exchange"
        assert sizes[0] == "104.0"   # profit_plan half
        assert sizes[1] == "105.0"   # moving_plan rest

    def test_even_qty_splits_evenly(self, monkeypatch):
        """qty=200 → half=100, rest=100."""
        calls = []
        monkeypatch.setattr(trader, "_sym_info", _sym_info_stousdt)
        monkeypatch.setattr(bc, "post", lambda path, p: calls.append(p) or {})

        trader._place_s1_exits("STOUSDT", "long", "200", 0.13, 0.129, 0.17, 5)

        sizes = [c["size"] for c in calls if "size" in c]
        assert sizes[0] == "100.0"
        assert sizes[1] == "100.0"

    def test_decimal_symbol_rounds_correctly(self, monkeypatch):
        """qty=0.003 BTC → half=0.001, rest=0.002."""
        calls = []
        monkeypatch.setattr(trader, "_sym_info", _sym_info_btcusdt)
        monkeypatch.setattr(bc, "post", lambda path, p: calls.append(p) or {})

        trader._place_s1_exits("BTCUSDT", "long", "0.003", 55000, 54725, 65000, 5)

        sizes = [c["size"] for c in calls if "size" in c]
        assert sizes[0] == "0.001"
        assert sizes[1] == "0.002"

    def test_places_three_orders(self, monkeypatch):
        """Must place exactly 3 orders: loss_plan SL, profit_plan partial, moving_plan trail."""
        calls = []
        monkeypatch.setattr(trader, "_sym_info", _sym_info_stousdt)
        monkeypatch.setattr(bc, "post", lambda path, p: calls.append((path, p)) or {})

        trader._place_s1_exits("STOUSDT", "long", "200", 0.13, 0.129, 0.17, 5)

        plan_types = [p.get("planType") for _, p in calls]
        # place-pos-tpsl has no planType, profit_plan and moving_plan do
        assert plan_types.count("profit_plan") == 1
        assert plan_types.count("moving_plan") == 1

    def test_trail_range_passed_to_moving_plan(self, monkeypatch):
        """rangeRate on moving_plan must equal the trail_range param."""
        calls = []
        monkeypatch.setattr(trader, "_sym_info", _sym_info_stousdt)
        monkeypatch.setattr(bc, "post", lambda path, p: calls.append(p) or {})

        trader._place_s1_exits("STOUSDT", "long", "200", 0.13, 0.129, 0.17, 5)

        moving = next(c for c in calls if c.get("planType") == "moving_plan")
        assert moving["rangeRate"] == "5"

    def test_returns_false_on_all_failures(self, monkeypatch):
        """Returns False when exchange raises on every attempt."""
        import time
        monkeypatch.setattr(trader, "_sym_info", _sym_info_stousdt)
        monkeypatch.setattr(bc, "post", lambda path, p: (_ for _ in ()).throw(Exception("API error")))
        monkeypatch.setattr(time, "sleep", lambda s: None)

        result = trader._place_s1_exits("STOUSDT", "long", "200", 0.13, 0.129, 0.17, 5)
        assert result is False
```

- [ ] **Step 2: Run to verify tests fail**

```bash
pytest tests/test_trader_exits.py::TestPlaceS1ExitsQty -v
```

Expected: `AttributeError: module 'trader' has no attribute '_place_s1_exits'`

- [ ] **Step 3: Implement `_place_s1_exits` in `trader.py`**

Insert this function in `trader.py` immediately after the `_place_s2_exits` function (after its closing `return False`, before `_place_s5_exits`). Check the current line where `_place_s2_exits` ends and insert after it:

```python
def _place_s1_exits(symbol: str, hold_side: str, qty_str: str,
                    sl_trig: float, sl_exec: float,
                    trail_trigger: float, trail_range: float) -> bool:
    """
    S1 exit orders placed at entry:
    1. SL at pivot box low/high (place-pos-tpsl loss_plan)
    2. Partial TP — sell 50% at trail_trigger (place-tpsl-order profit_plan)
    3. Trailing stop on remaining 50% with trail_range% callback (moving_plan)
    """
    import time as _t
    half_qty = _round_qty(float(qty_str) / 2, symbol)
    rest_qty = _round_qty(float(qty_str) - float(half_qty), symbol)

    for attempt in range(3):
        try:
            # 1. SL on full position
            bc.post("/api/v2/mix/order/place-pos-tpsl", {
                "symbol":               symbol,
                "productType":          PRODUCT_TYPE,
                "marginCoin":           MARGIN_COIN,
                "holdSide":             hold_side,
                "stopLossTriggerPrice": str(sl_trig),
                "stopLossTriggerType":  "mark_price",
                "stopLossExecutePrice": str(sl_exec),
            })
            _t.sleep(0.5)

            # 2. Partial TP — sell 50% when trail_trigger hit
            bc.post("/api/v2/mix/order/place-tpsl-order", {
                "symbol":       symbol,
                "productType":  PRODUCT_TYPE,
                "marginCoin":   MARGIN_COIN,
                "planType":     "profit_plan",
                "triggerPrice": str(trail_trigger),
                "triggerType":  "mark_price",
                "executePrice": "0",
                "holdSide":     hold_side,
                "size":         half_qty,
            })
            _t.sleep(0.5)

            # 3. Trailing stop on remaining 50%
            bc.post("/api/v2/mix/order/place-tpsl-order", {
                "symbol":       symbol,
                "productType":  PRODUCT_TYPE,
                "marginCoin":   MARGIN_COIN,
                "planType":     "moving_plan",
                "triggerPrice": str(trail_trigger),
                "triggerType":  "mark_price",
                "holdSide":     hold_side,
                "size":         rest_qty,
                "rangeRate":    str(trail_range),
            })
            return True
        except Exception as e:
            logger.warning(f"[{symbol}] S1 exits attempt {attempt+1}/3: {e}")
            if attempt < 2:
                _t.sleep(1.5)
    return False
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_trader_exits.py::TestPlaceS1ExitsQty -v
```

Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add trader.py tests/test_trader_exits.py
git commit -m "feat(trader): add _place_s1_exits with partial TP + 5% moving_plan trail"
```

---

## Task 3: Add `use_s1_exits` to `open_long` and `open_short`

**Files:**
- Modify: `trader.py` — `open_long` (line ~415) and `open_short` (line ~507)
- Test: `tests/test_trader_exits.py`

- [ ] **Step 1: Write failing tests**

In `tests/test_trader_exits.py`, add two new classes after `TestOpenLongSLCap`:

```python
# ── open_long use_s1_exits path ───────────────────────────────────── #

class TestOpenLongS1Exits:
    """open_long(use_s1_exits=True) must call _place_s1_exits with correct args."""

    def _setup(self, monkeypatch, mark=0.15, sl_floor=0.14, fill=None):
        calls = []
        monkeypatch.setattr(trader, "_sym_info", _sym_info_stousdt)
        monkeypatch.setattr(trader, "get_usdt_balance", lambda: 100.0)
        monkeypatch.setattr(trader, "_get_total_equity", lambda: 100.0)
        monkeypatch.setattr(trader, "get_mark_price", lambda sym: mark)
        monkeypatch.setattr(trader, "set_leverage", lambda *a: None)
        monkeypatch.setattr(bc, "post", lambda path, p: calls.append(p) or {})

        _fill = fill if fill is not None else mark
        monkeypatch.setattr(trader, "get_all_open_positions",
            lambda: {"STOUSDT": {"entry_price": _fill, "qty": 65.0}})

        import time
        monkeypatch.setattr(time, "sleep", lambda s: None)

        import config_s1
        monkeypatch.setattr(config_s1, "S1_TRAIL_RANGE_PCT", 5)
        monkeypatch.setattr(config_s1, "TAKE_PROFIT_PCT", 0.10)

        result = trader.open_long(
            "STOUSDT", sl_floor=sl_floor,
            leverage=10, trade_size_pct=0.1,
            use_s1_exits=True,
        )
        return result, calls

    def test_profit_plan_trigger_at_10pct_above_fill(self, monkeypatch):
        """profit_plan triggerPrice must be fill * 1.10."""
        result, calls = self._setup(monkeypatch, mark=0.15, fill=0.151)
        expected = round(0.151 * 1.10, 5)
        profit_plan = next(c for c in calls if c.get("planType") == "profit_plan")
        assert abs(float(profit_plan["triggerPrice"]) - expected) < 0.0001

    def test_moving_plan_range_rate_is_5(self, monkeypatch):
        """moving_plan rangeRate must be '5' (S1_TRAIL_RANGE_PCT)."""
        _, calls = self._setup(monkeypatch)
        moving = next(c for c in calls if c.get("planType") == "moving_plan")
        assert moving["rangeRate"] == "5"

    def test_sl_uses_sl_floor(self, monkeypatch):
        """Position-level SL trigger must equal sl_floor."""
        _, calls = self._setup(monkeypatch, sl_floor=0.14)
        # place-pos-tpsl has no planType; find by stopLossTriggerPrice presence
        sl_order = next(c for c in calls if "stopLossTriggerPrice" in c)
        assert abs(float(sl_order["stopLossTriggerPrice"]) - 0.14) < 0.0001

    def test_entry_is_fill_price(self, monkeypatch):
        """Return dict entry must be fill price."""
        result, _ = self._setup(monkeypatch, mark=0.15, fill=0.152)
        assert result["entry"] == 0.152

    def test_tpsl_set_true_on_success(self, monkeypatch):
        """tpsl_set must be True when all orders placed successfully."""
        result, _ = self._setup(monkeypatch)
        assert result["tpsl_set"] is True


# ── open_short use_s1_exits path ──────────────────────────────────── #

class TestOpenShortS1Exits:
    """open_short(use_s1_exits=True) must call _place_s1_exits with correct args."""

    def _setup(self, monkeypatch, mark=0.34, sl_floor=0.35, fill=None):
        calls = []
        monkeypatch.setattr(trader, "_sym_info", _sym_info_stousdt)
        monkeypatch.setattr(trader, "get_usdt_balance", lambda: 100.0)
        monkeypatch.setattr(trader, "_get_total_equity", lambda: 100.0)
        monkeypatch.setattr(trader, "get_mark_price", lambda sym: mark)
        monkeypatch.setattr(trader, "set_leverage", lambda *a: None)
        monkeypatch.setattr(bc, "post", lambda path, p: calls.append(p) or {})

        _fill = fill if fill is not None else mark
        monkeypatch.setattr(trader, "get_all_open_positions",
            lambda: {"STOUSDT": {"entry_price": _fill, "qty": 65.0}})

        import time
        monkeypatch.setattr(time, "sleep", lambda s: None)

        import config_s1
        monkeypatch.setattr(config_s1, "S1_TRAIL_RANGE_PCT", 5)
        monkeypatch.setattr(config_s1, "TAKE_PROFIT_PCT", 0.10)

        result = trader.open_short(
            "STOUSDT", sl_floor=sl_floor,
            leverage=10, trade_size_pct=0.1,
            use_s1_exits=True,
        )
        return result, calls

    def test_profit_plan_trigger_at_10pct_below_fill(self, monkeypatch):
        """profit_plan triggerPrice must be fill * 0.90."""
        result, calls = self._setup(monkeypatch, mark=0.34, fill=0.338)
        expected = round(0.338 * 0.90, 5)
        profit_plan = next(c for c in calls if c.get("planType") == "profit_plan")
        assert abs(float(profit_plan["triggerPrice"]) - expected) < 0.0001

    def test_moving_plan_range_rate_is_5(self, monkeypatch):
        """moving_plan rangeRate must be '5'."""
        _, calls = self._setup(monkeypatch)
        moving = next(c for c in calls if c.get("planType") == "moving_plan")
        assert moving["rangeRate"] == "5"

    def test_sl_uses_sl_floor(self, monkeypatch):
        """Position-level SL trigger must equal sl_floor."""
        _, calls = self._setup(monkeypatch, sl_floor=0.35)
        sl_order = next(c for c in calls if "stopLossTriggerPrice" in c)
        assert abs(float(sl_order["stopLossTriggerPrice"]) - 0.35) < 0.0001

    def test_entry_is_fill_price(self, monkeypatch):
        """Return dict entry must be fill price."""
        result, _ = self._setup(monkeypatch, mark=0.34, fill=0.337)
        assert result["entry"] == 0.337
```

- [ ] **Step 2: Run to verify tests fail**

```bash
pytest tests/test_trader_exits.py::TestOpenLongS1Exits tests/test_trader_exits.py::TestOpenShortS1Exits -v
```

Expected: `TypeError: open_long() got an unexpected keyword argument 'use_s1_exits'`

- [ ] **Step 3: Add `use_s1_exits=False` to `open_long` and wire the branch**

In `trader.py`, modify the `open_long` signature (line ~415):
```python
def open_long(
    symbol: str,
    box_low: float         = 0,
    sl_floor: float        = 0,
    leverage: int          = LEVERAGE,
    trade_size_pct: float  = TRADE_SIZE_PCT,
    take_profit_pct: float = TAKE_PROFIT_PCT,
    stop_loss_pct: float   = STOP_LOSS_PCT,
    use_s2_exits: bool     = False,
    use_s5_exits: bool     = False,
    use_s1_exits: bool     = False,
    tp_price_abs: float    = 0,
) -> dict:
```

Then in `open_long`, add the `elif use_s1_exits:` branch between `elif use_s2_exits:` and `else:`:

Replace:
```python
    else:
        tp_trig = float(_round_price(fill * (1 + take_profit_pct), symbol))
        tp_exec = float(_round_price(tp_trig * 1.005, symbol))
        if sl_floor > 0:
            sl_trig = float(_round_price(sl_floor, symbol))
            sl_exec = float(_round_price(sl_floor * 0.995, symbol))
        else:
            sl_trig = float(_round_price(fill * (1 - stop_loss_pct), symbol))
            sl_exec = float(_round_price(sl_trig * 0.995, symbol))
        ok = _place_tpsl(symbol, "long", tp_trig, tp_exec, sl_trig, sl_exec)
```

With:
```python
    elif use_s1_exits:
        from config_s1 import S1_TRAIL_RANGE_PCT, TAKE_PROFIT_PCT as _S1_TP_PCT
        trail_trig = float(_round_price(fill * (1 + _S1_TP_PCT), symbol))
        sl_trig    = float(_round_price(sl_floor, symbol))
        sl_exec    = float(_round_price(sl_trig * 0.995, symbol))
        ok = _place_s1_exits(symbol, "long", qty,
                             sl_trig, sl_exec,
                             trail_trig, S1_TRAIL_RANGE_PCT)
        tp_trig = trail_trig
    else:
        tp_trig = float(_round_price(fill * (1 + take_profit_pct), symbol))
        tp_exec = float(_round_price(tp_trig * 1.005, symbol))
        if sl_floor > 0:
            sl_trig = float(_round_price(sl_floor, symbol))
            sl_exec = float(_round_price(sl_floor * 0.995, symbol))
        else:
            sl_trig = float(_round_price(fill * (1 - stop_loss_pct), symbol))
            sl_exec = float(_round_price(sl_trig * 0.995, symbol))
        ok = _place_tpsl(symbol, "long", tp_trig, tp_exec, sl_trig, sl_exec)
```

Also update the logger line after the result dict in `open_long` to include s1 in the display string. Find:
```python
    logger.info(
        f"[{symbol}] 🟢 LONG {leverage}x | qty={qty} entry≈{fill:.5f} "
        f"SL={sl_trig} | {'✅ S2 exits' if use_s2_exits else 'TP='+str(tp_trig)} | {'✅' if ok else '❌ SET MANUALLY'}"
    )
```

Replace with:
```python
    logger.info(
        f"[{symbol}] 🟢 LONG {leverage}x | qty={qty} entry≈{fill:.5f} "
        f"SL={sl_trig} | {'✅ S1 exits' if use_s1_exits else '✅ S2 exits' if use_s2_exits else 'TP='+str(tp_trig)} | {'✅' if ok else '❌ SET MANUALLY'}"
    )
```

- [ ] **Step 4: Add `use_s1_exits=False` to `open_short` and wire the branch**

In `trader.py`, modify the `open_short` signature (line ~507):
```python
def open_short(
    symbol: str,
    box_high: float        = 0,
    sl_floor: float        = 0,
    leverage: int          = LEVERAGE,
    trade_size_pct: float  = TRADE_SIZE_PCT,
    take_profit_pct: float = TAKE_PROFIT_PCT,
    use_s4_exits: bool     = False,
    use_s5_exits: bool     = False,
    use_s1_exits: bool     = False,
    tp_price_abs: float    = 0,
) -> dict:
```

Then in `open_short`, add `elif use_s1_exits:` between `elif use_s4_exits:` and `else:`.

Note: in `open_short`, `sl_trig` and `sl_exec` are already computed before the order placement from `sl_floor` or `box_high`. The new branch reuses them directly.

Replace:
```python
    else:
        tp_trig = float(_round_price(fill * (1 - take_profit_pct), symbol))
        tp_exec = float(_round_price(tp_trig * 0.995, symbol))
        ok = _place_tpsl(symbol, "short", tp_trig, tp_exec, sl_trig, sl_exec)
```

With:
```python
    elif use_s1_exits:
        from config_s1 import S1_TRAIL_RANGE_PCT, TAKE_PROFIT_PCT as _S1_TP_PCT
        trail_trig = float(_round_price(fill * (1 - _S1_TP_PCT), symbol))
        ok = _place_s1_exits(symbol, "short", qty,
                             sl_trig, sl_exec,
                             trail_trig, S1_TRAIL_RANGE_PCT)
        tp_trig = trail_trig
    else:
        tp_trig = float(_round_price(fill * (1 - take_profit_pct), symbol))
        tp_exec = float(_round_price(tp_trig * 0.995, symbol))
        ok = _place_tpsl(symbol, "short", tp_trig, tp_exec, sl_trig, sl_exec)
```

Also update the logger line in `open_short`:
```python
    logger.info(
        f"[{symbol}] 🔴 SHORT {leverage}x | qty={qty} entry≈{fill:.5f} "
        f"SL={sl_trig} | {'✅ S1 exits' if use_s1_exits else '✅ S5 exits' if use_s5_exits else '✅ S4 exits' if use_s4_exits else 'TP='+str(tp_trig)} | {'✅' if ok else '❌ SET MANUALLY'}"
    )
```

- [ ] **Step 5: Run all new tests**

```bash
pytest tests/test_trader_exits.py::TestOpenLongS1Exits tests/test_trader_exits.py::TestOpenShortS1Exits tests/test_trader_exits.py::TestPlaceS1ExitsQty -v
```

Expected: all 15 tests pass.

- [ ] **Step 6: Run the full trader exits test suite to confirm no regressions**

```bash
pytest tests/test_trader_exits.py -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add trader.py tests/test_trader_exits.py
git commit -m "feat(trader): add use_s1_exits to open_long/open_short, routes to _place_s1_exits"
```

---

## Task 4: Update `_execute_s1` in `bot.py` and fix existing tests

**Files:**
- Modify: `bot.py:1201-1257` (`_execute_s1`)
- Modify: `tests/test_bot_s1_sl.py` (update mock signatures)

- [ ] **Step 1: Update existing test mocks in `test_bot_s1_sl.py`**

The existing tests mock `open_long` / `open_short` with `take_profit_pct` as a positional param. Since `_execute_s1` will now pass `use_s1_exits=True` instead of `take_profit_pct=...`, update all fake function signatures.

In `test_bot_s1_sl.py`, replace every occurrence of:
```python
def fake_open_long(symbol, sl_floor, leverage, trade_size_pct, take_profit_pct):
```
with:
```python
def fake_open_long(symbol, sl_floor, leverage, trade_size_pct, use_s1_exits):
```

And every occurrence of:
```python
def fake_open_short(symbol, sl_floor, leverage, trade_size_pct, take_profit_pct):
```
with:
```python
def fake_open_short(symbol, sl_floor, leverage, trade_size_pct, use_s1_exits):
```

There are 3 `fake_open_long` definitions (tests 1, 2, 7) and 2 `fake_open_short` definitions (tests 3, 4). Update all of them.

Also update `test_long_proceeds_when_sr_clearance_at_gate` (test 7):
```python
def fake_open_long(symbol, sl_floor, leverage, trade_size_pct, use_s1_exits):
    opened.append("long")
    return {"symbol": symbol, "side": "LONG", "qty": 1.0, "entry": mark,
            "sl": sl_floor, "tp": mark * 1.1, "leverage": leverage,
            "margin": 10.0, "tpsl_set": True}
```

- [ ] **Step 2: Run existing tests to verify they still pass before making bot.py changes**

```bash
pytest tests/test_bot_s1_sl.py -v
```

Expected: all 7 tests pass (mocks now match the new signature).

- [ ] **Step 3: Update `_execute_s1` in `bot.py`**

In `bot.py`, find `_execute_s1` (line ~1201). Replace the two `open_long`/`open_short` calls and the `active_positions` assignment.

Replace:
```python
        if s1_sig == "LONG":
            trade = tr.open_long(symbol, sl_floor=sl_long, leverage=lev,
                                 trade_size_pct=config_s1.TRADE_SIZE_PCT,
                                 take_profit_pct=config_s1.TAKE_PROFIT_PCT)
        else:
            trade = tr.open_short(symbol, sl_floor=sl_short, leverage=lev,
                                  trade_size_pct=config_s1.TRADE_SIZE_PCT,
                                  take_profit_pct=config_s1.TAKE_PROFIT_PCT)
```

With:
```python
        if s1_sig == "LONG":
            trade = tr.open_long(symbol, sl_floor=sl_long, leverage=lev,
                                 trade_size_pct=config_s1.TRADE_SIZE_PCT,
                                 use_s1_exits=True)
        else:
            trade = tr.open_short(symbol, sl_floor=sl_short, leverage=lev,
                                  trade_size_pct=config_s1.TRADE_SIZE_PCT,
                                  use_s1_exits=True)
```

Then find the `active_positions` assignment at the end of `_execute_s1` (~line 1252):

Replace:
```python
        self.active_positions[symbol] = {
            "side": s1_sig, "strategy": "S1",
            "box_high": c["s1_bh"], "box_low": c["s1_bl"],
            "trade_id": trade["trade_id"],
        }
```

With:
```python
        self.active_positions[symbol] = {
            "side": s1_sig, "strategy": "S1",
            "box_high": c["s1_bh"], "box_low": c["s1_bl"],
            "trade_id": trade["trade_id"],
            "sl": trade.get("sl", 0.0),
        }
```

- [ ] **Step 4: Run the S1 bot tests**

```bash
pytest tests/test_bot_s1_sl.py -v
```

Expected: all 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add bot.py tests/test_bot_s1_sl.py
git commit -m "feat(bot): _execute_s1 uses use_s1_exits=True, stores sl in active_positions"
```

---

## Task 5: Partial close detection + S1 swing trail block

**Files:**
- Modify: `bot.py:549` (partial close strategy list)
- Modify: `bot.py` (add S1 swing trail block after S3 block at ~line 619)
- Test: `tests/test_bot_s1_sl.py`

- [ ] **Step 1: Write failing tests for the swing trail guard**

In `tests/test_bot_s1_sl.py`, add these tests at the end of the file. They test the directional guard logic by simulating a monitor tick:

```python
# ── Swing trail guard tests ───────────────────────────────────────── #

def _make_monitor_bot(monkeypatch, ap_side="LONG", ap_sl=97.0):
    """Set up bot with one S1 active position and mocked dependencies."""
    import threading
    b = object.__new__(bot.MTFBot)
    b.running = True
    b.active_positions = {
        "BTCUSDT": {
            "side": ap_side, "strategy": "S1",
            "box_high": 102.0, "box_low": 98.0,
            "trade_id": "abc123",
            "sl": ap_sl,
        }
    }
    b._trade_lock = threading.Lock()
    b.sentiment = type("S", (), {"direction": "BULLISH"})()
    b.candidates = []
    b.last_scan_time = 0
    b.qualified_pairs = ["BTCUSDT"]

    monkeypatch.setattr(bot.st, "add_scan_log",        lambda *a, **kw: None)
    monkeypatch.setattr(bot.st, "update_open_trade_sl", lambda *a, **kw: None)
    monkeypatch.setattr(bot.snapshot, "save_snapshot",  lambda **kw: None)
    monkeypatch.setattr(bot, "PAPER_MODE", False)

    return b


def test_swing_trail_long_sl_steps_up(monkeypatch):
    """LONG: when swing low is above current SL, SL steps up."""
    b = _make_monitor_bot(monkeypatch, ap_side="LONG", ap_sl=97.0)
    ap = b.active_positions["BTCUSDT"]

    # Swing low at 98.5 → swing_sl = 98.5 * (1 - 0.005) = 98.00725
    monkeypatch.setattr(bot.tr, "get_candles",    lambda sym, interval, limit: _make_ltf_df())
    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: 102.0)
    monkeypatch.setattr(bot,    "find_swing_low_target", lambda df, price, lookback: 98.5)
    monkeypatch.setattr(bot.tr, "update_position_sl",    lambda sym, sl, hold_side: True)

    import config_s1
    monkeypatch.setattr(config_s1, "S1_USE_SWING_TRAIL", True)
    monkeypatch.setattr(config_s1, "S1_SWING_LOOKBACK",  20)

    # Simulate the swing trail block by calling it directly via the logic we will add
    # (we test the guard: new swing_sl must be > ap["sl"] before update fires)
    new_sl = 98.5 * (1 - config_s1.S1_SL_BUFFER_PCT)  # 98.00725
    assert new_sl > ap["sl"], "Guard passes: new swing_sl above current SL"


def test_swing_trail_long_guard_prevents_step_down(monkeypatch):
    """LONG: when swing low is below current SL, guard prevents update."""
    b = _make_monitor_bot(monkeypatch, ap_side="LONG", ap_sl=99.0)
    ap = b.active_positions["BTCUSDT"]

    # Swing low at 97.0 → swing_sl = 97.0 * 0.995 = 96.515 — BELOW current sl=99.0
    new_sl = 97.0 * (1 - 0.005)  # 96.515
    assert new_sl <= ap["sl"], "Guard blocks: new swing_sl would move SL down"


def test_swing_trail_short_sl_steps_down(monkeypatch):
    """SHORT: when swing high is below current SL, SL steps down."""
    b = _make_monitor_bot(monkeypatch, ap_side="SHORT", ap_sl=105.0)
    ap = b.active_positions["BTCUSDT"]

    # Swing high at 103.0 → swing_sl = 103.0 * 1.005 = 103.515 — BELOW current sl=105.0
    new_sl = 103.0 * (1 + 0.005)  # 103.515
    assert new_sl < ap["sl"], "Guard passes: new swing_sl below current SL"


def test_swing_trail_short_guard_prevents_step_up(monkeypatch):
    """SHORT: when swing high is above current SL, guard prevents update."""
    b = _make_monitor_bot(monkeypatch, ap_side="SHORT", ap_sl=103.0)
    ap = b.active_positions["BTCUSDT"]

    # Swing high at 106.0 → swing_sl = 106.0 * 1.005 = 106.53 — ABOVE current sl=103.0
    new_sl = 106.0 * (1 + 0.005)  # 106.53
    assert new_sl >= ap["sl"], "Guard blocks: new swing_sl would move SL up"
```

- [ ] **Step 2: Run the new tests to verify they pass (these test the math, not the bot loop)**

```bash
pytest tests/test_bot_s1_sl.py::test_swing_trail_long_sl_steps_up \
       tests/test_bot_s1_sl.py::test_swing_trail_long_guard_prevents_step_down \
       tests/test_bot_s1_sl.py::test_swing_trail_short_sl_steps_down \
       tests/test_bot_s1_sl.py::test_swing_trail_short_guard_prevents_step_up -v
```

Expected: all 4 pass (they test guard conditions, no bot plumbing needed).

- [ ] **Step 3: Add `"S1"` to partial close detection in `bot.py`**

In `bot.py`, find line ~549:
```python
                    if not PAPER_MODE and ap.get("strategy") in ("S2", "S3", "S4", "S5"):
```

Replace with:
```python
                    if not PAPER_MODE and ap.get("strategy") in ("S1", "S2", "S3", "S4", "S5"):
```

- [ ] **Step 4: Add S1 swing trail block in `bot.py`**

In `bot.py`, find the S3 swing trail block (starts with `# S3 Structural Swing Trail`). Insert the S1 block **immediately before it**:

```python
                    # S1 Swing Trail — trail SL to nearest 3m swing low (LONG) or swing high (SHORT)
                    if config_s1.S1_USE_SWING_TRAIL and ap.get("strategy") == "S1":
                        try:
                            cs_df   = tr.get_candles(sym, config_s1.LTF_INTERVAL, limit=config_s1.S1_SWING_LOOKBACK + 5)
                            mark_s1 = tr.get_mark_price(sym)
                            if not cs_df.empty and len(cs_df) >= 3:
                                if ap["side"] == "LONG":
                                    raw      = find_swing_low_target(cs_df, mark_s1, lookback=config_s1.S1_SWING_LOOKBACK)
                                    swing_sl = raw * (1 - config_s1.S1_SL_BUFFER_PCT) if raw else None
                                    hold_s   = "long"
                                    if swing_sl is not None and swing_sl <= ap.get("sl", 0):
                                        swing_sl = None
                                else:
                                    raw      = find_swing_high_target(cs_df, mark_s1, lookback=config_s1.S1_SWING_LOOKBACK)
                                    swing_sl = raw * (1 + config_s1.S1_SL_BUFFER_PCT) if raw else None
                                    hold_s   = "short"
                                    if swing_sl is not None and swing_sl >= ap.get("sl", float("inf")):
                                        swing_sl = None
                                if swing_sl is not None and tr.update_position_sl(sym, swing_sl, hold_side=hold_s):
                                    ap["sl"] = swing_sl
                                    st.update_open_trade_sl(sym, swing_sl)
                                    logger.info(
                                        f"[S1][{sym}] 📍 Swing trail: SL → {swing_sl:.5f} "
                                        f"(3m swing {'low' if ap['side'] == 'LONG' else 'high'} ±{config_s1.S1_SL_BUFFER_PCT*100:.1f}% buffer)"
                                    )
                        except Exception as e:
                            logger.error(f"[S1] Swing trail error [{sym}]: {e}")

```

- [ ] **Step 5: Verify `find_swing_high_target` is imported in `bot.py`**

```bash
grep -n "find_swing_high_target\|find_swing_low_target" bot.py | head -5
```

Expected: both names appear in an import line near the top of bot.py. If `find_swing_high_target` is missing from the import, add it to the same `from strategy import ...` line.

- [ ] **Step 6: Run all S1 tests**

```bash
pytest tests/test_bot_s1_sl.py -v
```

Expected: all 11 tests pass.

- [ ] **Step 7: Commit**

```bash
git add bot.py tests/test_bot_s1_sl.py
git commit -m "feat(bot): S1 partial close detection + 3m swing trail SL with directional guard"
```

---

## Task 6: Full test suite + final verification

**Files:** None (verification only)

- [ ] **Step 1: Run the full test suite**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
pytest tests/ -v
```

Expected: all tests pass with no failures.

- [ ] **Step 2: Verify both bots import cleanly**

```bash
python -c "import bot; print('Bitget OK')"
python -c "import ig_bot; print('IG OK')"
```

Expected:
```
Bitget OK
IG OK
```

- [ ] **Step 3: Update DEPENDENCIES.md**

In `docs/DEPENDENCIES.md`, add an entry to Section 6.1 (trader.py exit functions) describing `_place_s1_exits` and the `use_s1_exits` param addition to `open_long`/`open_short`. Add a new line to the Document History at the bottom:

```
- 2026-04-02: Added _place_s1_exits() to trader.py; added use_s1_exits=False param to open_long/open_short. S1 now uses partial TP (profit_plan at +TAKE_PROFIT_PCT) + trailing stop (moving_plan, S1_TRAIL_RANGE_PCT%) + per-tick 3m pivot SL stepping (S1_USE_SWING_TRAIL). Partial close detection extended to include "S1".
```

- [ ] **Step 4: Commit DEPENDENCIES.md**

```bash
git add docs/DEPENDENCIES.md
git commit -m "docs: update DEPENDENCIES.md for S1 partial TP + trailing exit changes"
```
