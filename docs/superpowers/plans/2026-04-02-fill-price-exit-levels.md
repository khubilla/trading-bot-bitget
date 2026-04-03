# Fill Price Exit Levels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the pre-order mark price with the actual post-fill average entry price when computing SL, TP, and trailing trigger levels for all strategies, including after scale-in.

**Architecture:** Two root fixes applied to `trader.py`: (1) `open_long`/`open_short` re-fetch `openPriceAvg` from Bitget after the market order fills and use it for all exit-level math; (2) `refresh_plan_exits` gains an optional `new_trail_trigger` param so `_do_scale_in` in `bot.py` can recalculate the trigger from the new average entry after a scale-in. `_do_scale_in` also recomputes and pushes an updated SL for S2 LONG (where SL has a percentage cap relative to entry). S4 SHORT uses a structural SL only — no percentage cap — so no SL update needed there. Both fallback gracefully when the position isn't visible yet.

**Tech Stack:** Python, pytest/monkeypatch, Bitget REST API (`/api/v2/mix/position/all-position`)

---

## Files modified

- `trader.py` — `open_long`, `open_short`, `refresh_plan_exits`
- `bot.py` — `_do_scale_in`
- `tests/test_trader_exits.py` — update existing setup helpers + new tests
- `docs/DEPENDENCIES.md` — update Section 6.1 to reflect new semantics

---

### Task 1: `refresh_plan_exits` — accept optional `new_trail_trigger`

**Files:**
- Modify: `trader.py:200-272`
- Test: `tests/test_trader_exits.py`

- [ ] **Step 1: Write the failing test**

Add to the bottom of `TestRefreshPlanExits` in `tests/test_trader_exits.py`:

```python
def test_new_trail_trigger_overrides_existing_order_trigger(self, monkeypatch):
    """When new_trail_trigger is passed, placed orders must use it, not the old order's trigger."""
    monkeypatch.setattr(trader, "_sym_info", _sym_info_stousdt)
    monkeypatch.setattr(bc, "get",
        lambda path, params=None: self._make_plan_orders() if "plan-orders" in path else {})
    monkeypatch.setattr(trader, "get_all_open_positions",
        lambda: {"STOUSDT": {"qty": 200.0}})

    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)

    place_calls = []

    def fake_post(path, payload):
        if "cancel" not in path:
            place_calls.append(payload)
        return {}

    monkeypatch.setattr(bc, "post", fake_post)

    # Pass a new trigger (0.19) — existing profit_plan has 0.17
    trader.refresh_plan_exits("STOUSDT", "long", new_trail_trigger=0.19)

    for p in place_calls:
        assert p.get("triggerPrice") == "0.19", (
            f"Expected triggerPrice 0.19 but got {p.get('triggerPrice')}"
        )

def test_zero_new_trail_trigger_preserves_existing_trigger(self, monkeypatch):
    """new_trail_trigger=0 (default) must fall back to the existing profit_plan trigger."""
    monkeypatch.setattr(trader, "_sym_info", _sym_info_stousdt)
    monkeypatch.setattr(bc, "get",
        lambda path, params=None: self._make_plan_orders() if "plan-orders" in path else {})
    monkeypatch.setattr(trader, "get_all_open_positions",
        lambda: {"STOUSDT": {"qty": 200.0}})

    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)

    place_calls = []

    def fake_post(path, payload):
        if "cancel" not in path:
            place_calls.append(payload)
        return {}

    monkeypatch.setattr(bc, "post", fake_post)

    trader.refresh_plan_exits("STOUSDT", "long")  # no new_trail_trigger

    for p in place_calls:
        assert p.get("triggerPrice") == "0.17"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
python -m pytest tests/test_trader_exits.py::TestRefreshPlanExits::test_new_trail_trigger_overrides_existing_order_trigger tests/test_trader_exits.py::TestRefreshPlanExits::test_zero_new_trail_trigger_preserves_existing_trigger -v
```

Expected: FAIL — `refresh_plan_exits() takes 2 positional arguments but 3 were given` (or similar).

- [ ] **Step 3: Implement the change in `trader.py`**

Change the signature and the trigger-selection line in `refresh_plan_exits`:

```python
def refresh_plan_exits(symbol: str, hold_side: str, new_trail_trigger: float = 0) -> bool:
    """
    Called after a scale-in to resize profit_plan and moving_plan orders to the
    current total position qty.  The SL (place-pos-tpsl) is position-level on
    Bitget and auto-scales — this function only touches plan orders.

    new_trail_trigger: if > 0, recalculates trigger price from the new average
    entry (e.g. after scale-in changes the avg).  Defaults to 0 which preserves
    the trigger price from the existing profit_plan order.

    Steps:
    1. Fetch pending profit_plan + moving_plan for this hold_side.
    2. Cancel them.
    3. Read current total position qty from the exchange.
    4. Re-place both orders — at new_trail_trigger if provided, else at the
       original trigger price from the existing profit_plan.
    """
```

Then replace the single line at the top of the try block where `trail_trigger` is set (currently reads from the existing order):

```python
    trail_trigger = new_trail_trigger if new_trail_trigger > 0 else float(profit["triggerPrice"])
```

This is the only logic change — the rest of the function is unchanged.

- [ ] **Step 4: Run all `TestRefreshPlanExits` tests to confirm they pass**

```bash
python -m pytest tests/test_trader_exits.py::TestRefreshPlanExits -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add trader.py tests/test_trader_exits.py
git commit -m "feat(trader): refresh_plan_exits accepts new_trail_trigger for recalc after scale-in"
```

---

### Task 2: `_do_scale_in` — compute and pass `new_trail_trigger`

**Files:**
- Modify: `bot.py:1143-1153`
- Test: `tests/test_snapshots.py` (extend existing fixture)

- [ ] **Step 1: Write the failing tests**

Add these new tests to `tests/test_snapshots.py` (after the existing `test_bot_saves_scale_in_snapshot`):

```python
def test_do_scale_in_passes_new_trail_trigger_to_refresh(monkeypatch):
    """After S2 scale-in, refresh_plan_exits must receive a trail trigger derived
    from the new average entry price, not the old profit_plan trigger price."""
    import bot, config_s2

    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: 15.80)
    monkeypatch.setattr(bot.tr, "scale_in_long", lambda *a, **kw: None)
    monkeypatch.setattr(bot.tr, "get_candles",
        lambda sym, interval, limit=100: __import__("pandas").DataFrame())
    monkeypatch.setattr(bot.st, "add_scan_log", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "_log_trade", lambda action, details: None)
    monkeypatch.setattr(bot, "PAPER_MODE", False)

    # avg entry after scale-in is 15.50; S2_TRAILING_TRIGGER_PCT=0.10
    # expected new_trail_trigger = 15.50 * 1.10 = 17.05
    monkeypatch.setattr(config_s2, "S2_TRAILING_TRIGGER_PCT", 0.10)
    monkeypatch.setattr(bot.tr, "get_all_open_positions",
        lambda: {"RIVERUSDT": {"entry_price": 15.50, "qty": 200.0, "margin": 6.0}})

    captured = {}

    def fake_refresh(symbol, hold_side, new_trail_trigger=0):
        captured["new_trail_trigger"] = new_trail_trigger
        return True

    monkeypatch.setattr(bot.tr, "refresh_plan_exits", fake_refresh)

    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)

    b = object.__new__(bot.MTFBot)
    ap = {
        "side": "LONG", "strategy": "S2", "trade_id": "tid_s2",
        "box_high": 15.80, "box_low": 14.99,
        "scale_in_pending": True, "scale_in_after": 0,
        "scale_in_trade_size_pct": 0.02,
    }
    b._do_scale_in("RIVERUSDT", ap)

    assert "new_trail_trigger" in captured, "refresh_plan_exits was not called"
    expected = round(15.50 * 1.10, 8)
    assert abs(captured["new_trail_trigger"] - expected) < 1e-6, (
        f"Expected {expected}, got {captured['new_trail_trigger']}"
    )


def test_do_scale_in_s4_short_passes_new_trail_trigger(monkeypatch):
    """After S4 SHORT scale-in, trail trigger is new_avg * (1 - S4_TRAILING_TRIGGER_PCT)."""
    import bot, config_s4

    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: 0.340)
    monkeypatch.setattr(bot.tr, "scale_in_short", lambda *a, **kw: None)
    monkeypatch.setattr(bot.tr, "get_candles",
        lambda sym, interval, limit=100: __import__("pandas").DataFrame())
    monkeypatch.setattr(bot.st, "add_scan_log", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "_log_trade", lambda action, details: None)
    monkeypatch.setattr(bot, "PAPER_MODE", False)

    monkeypatch.setattr(config_s4, "S4_TRAILING_TRIGGER_PCT", 0.10)
    monkeypatch.setattr(config_s4, "S4_MAX_ENTRY_BUFFER", 0.01)
    monkeypatch.setattr(config_s4, "S4_ENTRY_BUFFER", 0.002)
    monkeypatch.setattr(bot.tr, "get_all_open_positions",
        lambda: {"STOUSDT": {"entry_price": 0.345, "qty": 200.0, "margin": 3.0}})

    captured = {}

    def fake_refresh(symbol, hold_side, new_trail_trigger=0):
        captured["new_trail_trigger"] = new_trail_trigger
        return True

    monkeypatch.setattr(bot.tr, "refresh_plan_exits", fake_refresh)

    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)

    b = object.__new__(bot.MTFBot)
    # S4 SHORT uses s4_prev_low for window check
    prev_low = 0.340
    ap = {
        "side": "SHORT", "strategy": "S4", "trade_id": "tid_s4",
        "s4_prev_low": prev_low,
        "scale_in_pending": True, "scale_in_after": 0,
        "scale_in_trade_size_pct": 0.02,
    }
    b._do_scale_in("STOUSDT", ap)

    assert "new_trail_trigger" in captured, "refresh_plan_exits was not called"
    expected = round(0.345 * (1 - 0.10), 8)
    assert abs(captured["new_trail_trigger"] - expected) < 1e-6, (
        f"Expected {expected}, got {captured['new_trail_trigger']}"
    )


def test_do_scale_in_s2_updates_sl_from_new_avg(monkeypatch):
    """After S2 LONG scale-in, SL must be recomputed from new avg entry and pushed to exchange.
    S2 scale-in is above box_high so new_avg > original entry → new sl_cap is higher → SL tightens.
    Expected new SL = max(box_low * 0.999, new_avg * (1 - S2_STOP_LOSS_PCT))
    """
    import bot, config_s2

    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: 0.162)   # in scale-in window
    monkeypatch.setattr(bot.tr, "scale_in_long", lambda *a, **kw: None)
    monkeypatch.setattr(bot.tr, "get_candles",
        lambda sym, interval, limit=100: __import__("pandas").DataFrame())
    monkeypatch.setattr(bot.st, "add_scan_log", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "_log_trade", lambda action, details: None)
    monkeypatch.setattr(bot, "PAPER_MODE", False)

    monkeypatch.setattr(config_s2, "S2_STOP_LOSS_PCT", 0.05)
    monkeypatch.setattr(config_s2, "S2_TRAILING_TRIGGER_PCT", 0.10)
    monkeypatch.setattr(config_s2, "S2_MAX_ENTRY_BUFFER", 0.01)

    # new_avg after scale-in is 0.158; box_low is 0.140
    # new sl_cap = 0.158 * 0.95 = 0.1501
    # box_low * 0.999 = 0.1399
    # expected new SL = max(0.1399, 0.1501) = 0.1501
    new_avg = 0.158
    monkeypatch.setattr(bot.tr, "get_all_open_positions",
        lambda: {"STOUSDT": {"entry_price": new_avg, "qty": 200.0, "margin": 6.0}})

    monkeypatch.setattr(bot.tr, "refresh_plan_exits", lambda *a, **kw: True)

    sl_update_calls = []
    monkeypatch.setattr(bot.tr, "update_position_sl",
        lambda sym, new_sl, hold_side="long": sl_update_calls.append(new_sl) or True)

    state_sl_updates = []
    monkeypatch.setattr(bot.st, "update_open_trade_sl",
        lambda sym, new_sl: state_sl_updates.append(new_sl))

    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)

    b = object.__new__(bot.MTFBot)
    ap = {
        "side": "LONG", "strategy": "S2", "trade_id": "tid_s2_sl",
        "box_high": 0.160, "box_low": 0.140,
        "sl": 0.132,   # original SL (below new cap)
        "scale_in_pending": True, "scale_in_after": 0,
        "scale_in_trade_size_pct": 0.02,
    }
    b._do_scale_in("STOUSDT", ap)

    expected_new_sl = max(0.140 * 0.999, new_avg * (1 - 0.05))  # = max(0.1399, 0.1501)
    assert len(sl_update_calls) == 1, "update_position_sl should be called once"
    assert abs(sl_update_calls[0] - expected_new_sl) < 1e-6, (
        f"SL sent to exchange {sl_update_calls[0]} != expected {expected_new_sl}"
    )
    assert len(state_sl_updates) == 1, "update_open_trade_sl (state) should be called once"
    assert abs(ap["sl"] - expected_new_sl) < 1e-6, "ap['sl'] should be updated in-memory"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python -m pytest tests/test_snapshots.py::test_do_scale_in_passes_new_trail_trigger_to_refresh tests/test_snapshots.py::test_do_scale_in_s4_short_passes_new_trail_trigger -v
```

Expected: FAIL — `refresh_plan_exits` is called with only 2 args (no `new_trail_trigger`).

- [ ] **Step 3: Implement the change in `bot.py`**

Replace the live-mode `try` block inside `_do_scale_in` (currently lines 1145-1153) with:

```python
                try:
                    import time as _si_t
                    _si_t.sleep(1.5)  # allow fill to settle
                    hold_side = "long" if ap["side"] == "LONG" else "short"
                    # Recompute trail trigger and SL from new average entry after scale-in
                    new_trig = 0.0
                    _scale_pos = tr.get_all_open_positions().get(sym, {})
                    new_avg = _scale_pos.get("entry_price", 0)
                    if new_avg > 0:
                        if ap["strategy"] == "S2":
                            new_trig = new_avg * (1 + config_s2.S2_TRAILING_TRIGGER_PCT)
                            # Recompute SL cap from new avg (S2 scale-in is above box_high,
                            # so new_avg > original entry → sl_cap moves up → SL tightens)
                            new_sl = max(
                                ap.get("box_low", 0) * 0.999,
                                new_avg * (1 - config_s2.S2_STOP_LOSS_PCT),
                            )
                            if new_sl > ap.get("sl", 0):
                                if tr.update_position_sl(sym, new_sl, hold_side="long"):
                                    ap["sl"] = new_sl
                                    st.update_open_trade_sl(sym, new_sl)
                        elif ap["strategy"] == "S4":
                            new_trig = new_avg * (1 - config_s4.S4_TRAILING_TRIGGER_PCT)
                            # S4 SL is structural (box_high * 1.001) — no percentage cap,
                            # no SL update needed after scale-in
                    if not tr.refresh_plan_exits(sym, hold_side, new_trig):
                        logger.warning(f"[{ap['strategy']}][{sym}] ⚠️ Scale-in exits refresh failed — verify plan orders manually")
                        st.add_scan_log(f"[{ap['strategy']}][{sym}] ⚠️ Scale-in exits refresh failed", "WARN")
                except Exception as _ref_e:
                    logger.warning(f"[{ap['strategy']}][{sym}] ⚠️ Scale-in exits refresh error: {_ref_e}")
```

- [ ] **Step 4: Run new tests plus the existing snapshot test**

```bash
python -m pytest tests/test_snapshots.py -v
```

Expected: all tests PASS, including:
- `test_bot_saves_scale_in_snapshot` — its `get_all_open_positions` mock returns no `entry_price`, so `new_trig` stays 0.0, `new_sl` is not computed, and `refresh_plan_exits` errors are caught by the try/except as before.
- `test_do_scale_in_passes_new_trail_trigger_to_refresh` — PASS
- `test_do_scale_in_s4_short_passes_new_trail_trigger` — PASS
- `test_do_scale_in_s2_updates_sl_from_new_avg` — PASS

- [ ] **Step 5: Commit**

```bash
git add bot.py tests/test_snapshots.py
git commit -m "feat(bot): recompute trail trigger from new avg entry after scale-in"
```

---

### Task 3: `open_long` — use actual fill price for exit levels

**Files:**
- Modify: `trader.py:412-494`
- Test: `tests/test_trader_exits.py`

- [ ] **Step 1: Update `TestOpenLongSLCap._setup` to mock `get_all_open_positions`**

The setup currently doesn't mock `get_all_open_positions`, which will now be called after the market order. Add the mock so the fill falls back to mark (preserving existing test logic), then add a new test that verifies exit levels use fill price:

In `TestOpenLongSLCap`, update `_setup` to add one monkeypatch line and an optional `fill` param:

```python
def _setup(self, monkeypatch, mark=0.15324, box_low=0.10000, stop_loss_pct=0.05, fill=None):
    sl_calls = []
    monkeypatch.setattr(trader, "_sym_info", _sym_info_stousdt)
    monkeypatch.setattr(trader, "get_usdt_balance", lambda: 100.0)
    monkeypatch.setattr(trader, "_get_total_equity", lambda: 100.0)
    monkeypatch.setattr(trader, "get_mark_price", lambda sym: mark)
    monkeypatch.setattr(trader, "set_leverage", lambda *a: None)
    monkeypatch.setattr(bc, "post", lambda path, p: sl_calls.append(p) or {})

    _fill = fill if fill is not None else mark
    monkeypatch.setattr(trader, "get_all_open_positions",
        lambda: {"STOUSDT": {"entry_price": _fill, "qty": 65.0}})

    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)

    import config_s2
    monkeypatch.setattr(config_s2, "S2_TRAILING_TRIGGER_PCT", 0.10)
    monkeypatch.setattr(config_s2, "S2_TRAILING_RANGE_PCT", 10)

    result = trader.open_long(
        "STOUSDT", box_low=box_low,
        leverage=10, trade_size_pct=0.1,
        stop_loss_pct=stop_loss_pct,
        use_s2_exits=True,
    )
    return result, sl_calls
```

Then add these new tests below the existing two:

```python
def test_fill_price_used_for_trail_trigger(self, monkeypatch):
    """When fill price differs from mark, trail trigger must be based on fill."""
    # mark=0.15, fill=0.151 (slight upward slippage)
    result, sl_calls = self._setup(monkeypatch, mark=0.15, box_low=0.14, fill=0.151)
    # trail_trig = fill * 1.10 = 0.1661
    expected_trail = round(0.151 * 1.10, 5)
    # The profit_plan order is sl_calls[1] (after pos-tpsl at index 0)
    profit_plan = next(c for c in sl_calls if c.get("planType") == "profit_plan")
    actual_trig = float(profit_plan["triggerPrice"])
    assert abs(actual_trig - expected_trail) < 0.0001, (
        f"trail trigger {actual_trig} != expected {expected_trail}"
    )

def test_fill_price_returned_as_entry(self, monkeypatch):
    """Return dict entry field must be the fill price, not the pre-order mark."""
    result, _ = self._setup(monkeypatch, mark=0.15, box_low=0.14, fill=0.152)
    assert result["entry"] == 0.152, f"entry {result['entry']} != fill 0.152"

def test_fill_fallback_to_mark_when_position_not_visible(self, monkeypatch):
    """If get_all_open_positions returns empty, exit levels fall back to mark."""
    sl_calls = []
    monkeypatch.setattr(trader, "_sym_info", _sym_info_stousdt)
    monkeypatch.setattr(trader, "get_usdt_balance", lambda: 100.0)
    monkeypatch.setattr(trader, "_get_total_equity", lambda: 100.0)
    monkeypatch.setattr(trader, "get_mark_price", lambda sym: 0.15)
    monkeypatch.setattr(trader, "set_leverage", lambda *a: None)
    monkeypatch.setattr(bc, "post", lambda path, p: sl_calls.append(p) or {})
    monkeypatch.setattr(trader, "get_all_open_positions", lambda: {})  # position not yet visible

    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)

    import config_s2
    monkeypatch.setattr(config_s2, "S2_TRAILING_TRIGGER_PCT", 0.10)
    monkeypatch.setattr(config_s2, "S2_TRAILING_RANGE_PCT", 10)

    result = trader.open_long(
        "STOUSDT", box_low=0.14,
        leverage=10, trade_size_pct=0.1,
        stop_loss_pct=0.05,
        use_s2_exits=True,
    )
    # Falls back to mark=0.15 → trail_trig = 0.15 * 1.10 = 0.165
    assert result["entry"] == 0.15
    profit_plan = next(c for c in sl_calls if c.get("planType") == "profit_plan")
    assert abs(float(profit_plan["triggerPrice"]) - 0.165) < 0.0001
```

- [ ] **Step 2: Run tests to confirm new ones fail**

```bash
python -m pytest tests/test_trader_exits.py::TestOpenLongSLCap -v
```

Expected: the two existing tests still PASS (fill defaults to mark). The three new tests FAIL.

- [ ] **Step 3: Implement the change in `open_long`**

Replace lines 412-494 in `trader.py` with:

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
    tp_price_abs: float    = 0,
) -> dict:
    """
    Opens a LONG position.
    Exit levels (SL, TP, trail trigger) are computed from the actual fill price
    fetched after the market order fills (falls back to pre-order mark if the
    position is not yet visible).
    S2/S3: partial TP at +100% margin + trailing stop on remaining 50%
      S2 uses box_low for SL; S3 passes sl_floor (pre-computed pivot SL)
    """
    import time as _t
    balance  = get_usdt_balance()
    equity   = _get_total_equity() or balance
    mark     = get_mark_price(symbol)
    notional = equity * trade_size_pct * leverage
    qty      = _round_qty(notional / mark, symbol)

    set_leverage(symbol, leverage)

    bc.post("/api/v2/mix/order/place-order", {
        "symbol": symbol, "productType": PRODUCT_TYPE,
        "marginMode": "isolated", "marginCoin": MARGIN_COIN,
        "size": qty, "side": "buy", "tradeSide": "open",
        "orderType": "market", "force": "ioc",
    })

    _t.sleep(2.0)

    # Re-fetch actual fill price from exchange; fall back to pre-order mark if
    # the position is not yet visible (e.g. very fast execution or API lag).
    _pos_after = get_all_open_positions()
    fill = _pos_after.get(symbol, {}).get("entry_price", 0) or mark

    if use_s5_exits:
        from config_s5 import S5_TRAIL_RANGE_PCT
        sl_trig     = float(_round_price(sl_floor, symbol))
        sl_exec     = float(_round_price(sl_trig * 0.995, symbol))
        one_r       = fill - sl_trig
        part_trig   = float(_round_price(fill + one_r, symbol))   # 1:1 R:R
        tp_targ     = float(_round_price(tp_price_abs, symbol)) if tp_price_abs > fill else 0.0
        ok = _place_s5_exits(symbol, "long", qty,
                             sl_trig, sl_exec,
                             part_trig, tp_targ, S5_TRAIL_RANGE_PCT)
        tp_trig = tp_targ if tp_targ > 0 else part_trig
    elif use_s2_exits:
        from config_s2 import S2_TRAILING_TRIGGER_PCT, S2_TRAILING_RANGE_PCT
        trail_trig = float(_round_price(fill * (1 + S2_TRAILING_TRIGGER_PCT), symbol))
        raw_sl = sl_floor if sl_floor > 0 else box_low * 0.999
        sl_cap = fill * (1 - stop_loss_pct)   # cap: never risk more than stop_loss_pct (e.g. 5% = -50% at 10x)
        sl_trig = float(_round_price(max(raw_sl, sl_cap), symbol))
        sl_exec = float(_round_price(sl_trig * 0.995, symbol))
        ok = _place_s2_exits(symbol, "long", qty,
                             sl_trig, sl_exec,
                             trail_trig, S2_TRAILING_RANGE_PCT)
        tp_trig = trail_trig  # For dashboard display: show where partial TP triggers
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

    if not ok:
        logger.error(f"[{symbol}] ⚠️  TP/SL failed! Set manually: SL={sl_trig}")

    result = {
        "symbol": symbol, "side": "LONG", "qty": qty,
        "entry": fill, "sl": sl_trig, "tp": tp_trig,
        "box_low": box_low, "leverage": leverage,
        "margin": round(equity * trade_size_pct, 4), "tpsl_set": ok,
    }
    logger.info(
        f"[{symbol}] 🟢 LONG {leverage}x | qty={qty} entry≈{fill:.5f} "
        f"SL={sl_trig} | {'✅ S2 exits' if use_s2_exits else 'TP='+str(tp_trig)} | {'✅' if ok else '❌ SET MANUALLY'}"
    )
    return result
```

- [ ] **Step 4: Run all `TestOpenLongSLCap` tests**

```bash
python -m pytest tests/test_trader_exits.py::TestOpenLongSLCap -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add trader.py tests/test_trader_exits.py
git commit -m "feat(trader): open_long uses actual fill price for exit level calculations"
```

---

### Task 4: `open_short` — use actual fill price for exit levels

**Files:**
- Modify: `trader.py:497-572`
- Test: `tests/test_trader_exits.py`

- [ ] **Step 1: Write failing tests**

Add a new test class to `tests/test_trader_exits.py`:

```python
# ── open_short fill price ─────────────────────────────────────────── #

class TestOpenShortFillPrice:
    """open_short must use actual fill price for TP/trail calculations."""

    def _setup_short(self, monkeypatch, mark=0.340, box_high=0.345, fill=None,
                     use_s4_exits=False):
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

        if use_s4_exits:
            import config_s4
            monkeypatch.setattr(config_s4, "S4_TRAILING_TRIGGER_PCT", 0.10)
            monkeypatch.setattr(config_s4, "S4_TRAILING_RANGE_PCT", 10)

        result = trader.open_short(
            "STOUSDT", box_high=box_high,
            leverage=10, trade_size_pct=0.1,
            use_s4_exits=use_s4_exits,
        )
        return result, calls

    def test_s4_trail_trigger_uses_fill_price(self, monkeypatch):
        """S4 trail trigger must be fill * (1 - S4_TRAILING_TRIGGER_PCT)."""
        result, calls = self._setup_short(
            monkeypatch, mark=0.340, fill=0.338, use_s4_exits=True
        )
        expected_trail = round(0.338 * (1 - 0.10), 5)
        profit_plan = next(c for c in calls if c.get("planType") == "profit_plan")
        actual_trig = float(profit_plan["triggerPrice"])
        assert abs(actual_trig - expected_trail) < 0.0001, (
            f"trail trigger {actual_trig} != expected {expected_trail}"
        )

    def test_default_tp_uses_fill_price(self, monkeypatch):
        """Default TP (no special exits) must be fill * (1 - take_profit_pct)."""
        _, calls = self._setup_short(monkeypatch, mark=0.340, fill=0.338)
        tp_order = next((c for c in calls if "takeProfitTriggerPrice" in c
                         or c.get("planType") == "profit_plan"), None)
        # _place_tpsl sets presetTakeProfitPrice or uses place-pos-tpsl
        tp_call = next(c for c in calls if "takeProfitTriggerPrice" in c)
        actual_tp = float(tp_call["takeProfitTriggerPrice"])
        # take_profit_pct defaults to TAKE_PROFIT_PCT from config
        import config_s1
        expected_tp = round(0.338 * (1 - config_s1.TAKE_PROFIT_PCT), 5)
        assert abs(actual_tp - expected_tp) < 0.001

    def test_fill_returned_as_entry(self, monkeypatch):
        """Return dict entry field must equal fill price."""
        result, _ = self._setup_short(monkeypatch, mark=0.340, fill=0.337)
        assert result["entry"] == 0.337

    def test_sl_still_based_on_box_high(self, monkeypatch):
        """SL is a structural level (box_high * 1.001) — must not change with fill price."""
        result, _ = self._setup_short(monkeypatch, mark=0.340, box_high=0.345, fill=0.337)
        expected_sl = round(0.345 * 1.001, 5)
        assert abs(result["sl"] - expected_sl) < 0.001, (
            f"SL {result['sl']} should be near box_high*1.001={expected_sl}"
        )
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python -m pytest tests/test_trader_exits.py::TestOpenShortFillPrice -v
```

Expected: FAIL — `open_short` still uses `mark` not `fill`.

Note: `test_default_tp_uses_fill_price` may also need a check on how `_place_tpsl` is called for shorts — if the test setup needs adjustment based on what's actually in `_place_tpsl`, update the assertion to check `tp_trig` in the result dict instead:

```python
def test_default_tp_uses_fill_price(self, monkeypatch):
    """Default TP (no special exits) must be fill * (1 - take_profit_pct)."""
    result, _ = self._setup_short(monkeypatch, mark=0.340, fill=0.338,
                                  take_profit_pct=0.10)
    expected_tp = round(0.338 * (1 - 0.10), 5)
    assert abs(result["tp"] - expected_tp) < 0.001
```

Update the `_setup_short` helper to accept and pass `take_profit_pct=0.10` to `open_short` when specified.

- [ ] **Step 3: Implement the change in `open_short`**

Replace lines 497-572 in `trader.py` with:

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
    tp_price_abs: float    = 0,
) -> dict:
    """
    Opens a SHORT position.
    SL = sl_floor if provided, else box_high * 1.001 (structural level, not fill-relative).
    TP/trail trigger computed from actual fill price fetched after the market order fills
    (falls back to pre-order mark if position not yet visible).
    use_s4_exits: trailing stop — 50% close at -10%, trailing stop on remainder.
    """
    import time as _t
    balance  = get_usdt_balance()
    equity   = _get_total_equity() or balance
    mark     = get_mark_price(symbol)
    notional = equity * trade_size_pct * leverage
    qty      = _round_qty(notional / mark, symbol)

    # SL is structural (box_high or sl_floor), not entry-relative — compute before order
    if sl_floor > 0:
        sl_trig = float(_round_price(sl_floor, symbol))
    else:
        sl_trig = float(_round_price(box_high * 1.001, symbol))
    sl_exec = float(_round_price(sl_trig * 1.005, symbol))

    set_leverage(symbol, leverage)

    bc.post("/api/v2/mix/order/place-order", {
        "symbol": symbol, "productType": PRODUCT_TYPE,
        "marginMode": "isolated", "marginCoin": MARGIN_COIN,
        "size": qty, "side": "sell", "tradeSide": "open",
        "orderType": "market", "force": "ioc",
    })

    _t.sleep(2.0)

    # Re-fetch actual fill price; fall back to pre-order mark if position not yet visible.
    _pos_after = get_all_open_positions()
    fill = _pos_after.get(symbol, {}).get("entry_price", 0) or mark

    if use_s5_exits:
        from config_s5 import S5_TRAIL_RANGE_PCT
        one_r     = sl_trig - fill
        part_trig = float(_round_price(fill - one_r, symbol))    # 1:1 R:R below entry
        tp_targ   = float(_round_price(tp_price_abs, symbol)) if 0 < tp_price_abs < fill else 0.0
        ok = _place_s5_exits(symbol, "short", qty,
                             sl_trig, sl_exec,
                             part_trig, tp_targ, S5_TRAIL_RANGE_PCT)
        tp_trig = tp_targ if tp_targ > 0 else part_trig
    elif use_s4_exits:
        from config_s4 import S4_TRAILING_TRIGGER_PCT, S4_TRAILING_RANGE_PCT
        trail_trig = float(_round_price(fill * (1 - S4_TRAILING_TRIGGER_PCT), symbol))
        ok = _place_s2_exits(symbol, "short", qty,
                             sl_trig, sl_exec,
                             trail_trig, S4_TRAILING_RANGE_PCT)
        tp_trig = trail_trig  # For dashboard display
    else:
        tp_trig = float(_round_price(fill * (1 - take_profit_pct), symbol))
        tp_exec = float(_round_price(tp_trig * 0.995, symbol))
        ok = _place_tpsl(symbol, "short", tp_trig, tp_exec, sl_trig, sl_exec)

    if not ok:
        logger.error(f"[{symbol}] ⚠️  TP/SL failed! Set manually: SL={sl_trig} TP={tp_trig}")

    result = {
        "symbol": symbol, "side": "SHORT", "qty": qty,
        "entry": fill, "sl": sl_trig, "tp": tp_trig,
        "box_high": box_high, "leverage": leverage,
        "margin": round(equity * trade_size_pct, 4), "tpsl_set": ok,
    }
    logger.info(
        f"[{symbol}] 🔴 SHORT {leverage}x | qty={qty} entry≈{fill:.5f} "
        f"SL={sl_trig} | {'✅ S5 exits' if use_s5_exits else '✅ S4 exits' if use_s4_exits else 'TP='+str(tp_trig)} | {'✅' if ok else '❌ SET MANUALLY'}"
    )
    return result
```

- [ ] **Step 4: Run all `open_short` tests**

```bash
python -m pytest tests/test_trader_exits.py::TestOpenShortFillPrice -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add trader.py tests/test_trader_exits.py
git commit -m "feat(trader): open_short uses actual fill price for exit level calculations"
```

---

### Task 5: Full suite + DEPENDENCIES.md update

**Files:**
- Modify: `docs/DEPENDENCIES.md`

- [ ] **Step 1: Run the full test suite**

```bash
python -m pytest --tb=short -q
```

Expected: all tests PASS. If any failures, fix them before proceeding.

- [ ] **Step 2: Update DEPENDENCIES.md Section 6.1**

In `docs/DEPENDENCIES.md`, update the `open_long` entry under Section 6.1 (currently says "entry is mark price at function call time, **not** actual fill price"):

Find this line:
```
**Returns:** dict with `{symbol, side, qty, entry, sl, tp, box_low, leverage, margin, tpsl_set}` — `entry` is mark price at function call time, **not** actual fill price.
```

Replace with:
```
**Returns:** dict with `{symbol, side, qty, entry, sl, tp, box_low, leverage, margin, tpsl_set}` — `entry` is the actual fill price fetched from `get_all_open_positions()` after the market order fills (falls back to pre-order mark price if the position is not yet visible).

All exit levels (SL, TP, trail trigger, partial TP) are computed from `entry` (fill), not from the pre-order mark price. The only value still computed from the pre-order mark is `qty` (position sizing).
```

Also update the `refresh_plan_exits` entry to reflect the new optional param:

Find:
```
#### `refresh_plan_exits(symbol, hold_side) → bool`
```

Replace:
```
#### `refresh_plan_exits(symbol, hold_side, new_trail_trigger=0) → bool`
```

And update the description to include:

```
**new_trail_trigger** (optional, default 0): If > 0, the re-placed `profit_plan` and `moving_plan` orders use this as the trigger price instead of preserving the existing order's trigger. Used by `_do_scale_in` to recalculate the trigger from the new average entry after a scale-in fill.
```

- [ ] **Step 3: Final test run and commit**

```bash
python -m pytest --tb=short -q
git add docs/DEPENDENCIES.md
git commit -m "docs(deps): update Section 6.1 — fill price semantics for open_long/short and refresh_plan_exits"
```
