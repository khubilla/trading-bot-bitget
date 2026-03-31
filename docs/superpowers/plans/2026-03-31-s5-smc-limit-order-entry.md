# S5 SMC Limit Order Entry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace S5's ChoCH + market order entry with a GTC limit order placed at `ob_high` the moment the OB is touched, giving a true SMC entry price instead of chasing above the OB.

**Architecture:** strategy.py removes the ChoCH candle-close check and always returns PENDING with `entry_trigger = ob_high`. Both bots immediately place a GTC limit order at that price with preset SL, monitor for fill via order status polling, and replace the preset SL/TP with the full `_place_s5_exits()` setup on fill. OB invalidation (mark crosses `ob_low`) cancels the order.

**Tech Stack:** Python 3.12, Bitget V2 REST API (`/api/v2/mix/order/`), IG REST API (`/workingorders/otc`), pytest + monkeypatch, pandas DataFrames

---

## File Map

| File | Change |
|---|---|
| `config_s5.py` | Remove `S5_ENTRY_BUFFER_PCT`, add `S5_OB_INVALIDATION_BUFFER_PCT` |
| `config_ig_s5.py` | Mirror config_s5 changes |
| `strategy.py` | Remove ChoCH block, `entry_trigger = ob_high`, stale guard, always PENDING |
| `trader.py` | Add `place_limit_long`, `place_limit_short`, `cancel_order`, `get_order_fill` |
| `bot.py` | `_queue_s5_pending` places limit immediately; watcher polls fill + OB invalidation; new `_handle_limit_filled` replaces `_fire_pending` |
| `ig_client.py` | Add `place_limit_long`, `place_limit_short`, `cancel_working_order`, `get_working_order_status` |
| `ig_bot.py` | Add `pending_order` state; handle `PENDING_LONG/SHORT` in `_tick`; fill detection, OB invalidation, session-end cancel |
| `tests/test_s5_strategy.py` | New: unit tests for evaluate_s5 strategy changes |
| `tests/test_trader_limit_orders.py` | New: unit tests for Bitget limit order functions |
| `tests/test_ig_limit_orders.py` | New: unit tests for IG working order functions |

---

## Task 1: Config — remove S5_ENTRY_BUFFER_PCT, add S5_OB_INVALIDATION_BUFFER_PCT

**Files:**
- Modify: `config_s5.py`
- Modify: `config_ig_s5.py`

- [ ] **Step 1: Edit config_s5.py**

In `config_s5.py`, replace the `S5_ENTRY_BUFFER_PCT` line and add the new constant:

```python
# ── Entry / SL ────────────────────────────────────────────── #
# S5_ENTRY_BUFFER_PCT removed — entry is at ob_high exactly (limit order)
S5_MAX_ENTRY_BUFFER = 0.04   # HOLD if price already >4% above ob_high at signal time
S5_SL_BUFFER_PCT    = 0.003  # 0.3% beyond OB outer edge for SL
S5_OB_INVALIDATION_BUFFER_PCT = 0.001  # cancel limit if mark crosses ob_low by >0.1%
```

- [ ] **Step 2: Edit config_ig_s5.py**

Open `config_ig_s5.py`. Find the line that sets `S5_ENTRY_BUFFER_PCT` and remove it. Add:

```python
S5_OB_INVALIDATION_BUFFER_PCT = 0.001
```

- [ ] **Step 3: Verify both bots import cleanly**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
python -c "import bot; print('Bitget OK')"
python -c "import ig_bot; print('IG OK')"
```

Expected: both print OK with no AttributeError.

- [ ] **Step 4: Commit**

```bash
git add config_s5.py config_ig_s5.py
git commit -m "config(s5): remove S5_ENTRY_BUFFER_PCT, add S5_OB_INVALIDATION_BUFFER_PCT"
```

---

## Task 2: strategy.py — remove ChoCH, entry_trigger = ob_high, always PENDING

**Files:**
- Modify: `strategy.py:1093-1244` (LONG branch of evaluate_s5)
- Modify: `strategy.py:1246-1320` (SHORT branch of evaluate_s5)
- Create: `tests/test_s5_strategy.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_s5_strategy.py`:

```python
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pandas as pd
import numpy as np
import pytest


# ── DataFrame builders ────────────────────────────────────── #

def _make_daily_bullish(n=120) -> pd.DataFrame:
    """120 daily candles with EMA10 > EMA20 > EMA50 (rising trend)."""
    closes = [100.0 + i * 0.1 for i in range(n)]
    return pd.DataFrame({
        "open":  [c - 0.05 for c in closes],
        "high":  [c + 0.1  for c in closes],
        "low":   [c - 0.1  for c in closes],
        "close": closes,
        "vol":   [1000.0]  * n,
    })


def _make_htf_bos(prior_swing_high=1.0, current_close=1.05, n=14) -> pd.DataFrame:
    """1H candles with current close > prior_swing_high (BOS confirmed)."""
    rows = []
    for i in range(n - 1):
        rows.append({"open": 0.99, "high": prior_swing_high - 0.01,
                     "low": 0.95, "close": 0.98, "vol": 500.0})
    # Last candle: close above prior swing high
    rows.append({"open": 1.00, "high": current_close + 0.01,
                 "low": 0.99, "close": current_close, "vol": 500.0})
    return pd.DataFrame(rows)


def _make_m15_ob_touched_no_choch(
    ob_high=1.000, ob_low=0.983, n=80
) -> pd.DataFrame:
    """
    15m candles with:
    - A bullish OB at index n-40 (bearish candle before +1.5% impulse)
    - OB touched within last 20 candles (current candle low <= ob_high * 1.002)
    - NO ChoCH: last completed candle (index -2) close <= ob_high
    - Current price (index -1) close <= ob_high (not stale)
    """
    rows = []
    # Indices 0 to n-41: neutral, all below ob_high
    for _ in range(n - 40):
        rows.append({"open": 0.97, "high": 0.98, "low": 0.96, "close": 0.975, "vol": 200.0})

    # Index n-40: bearish OB candle (open=ob_high, close=ob_low+0.002)
    rows.append({
        "open": ob_high, "high": ob_high + 0.005,
        "low": ob_low, "close": ob_low + 0.002, "vol": 500.0
    })

    # Indices n-39 to n-38: bullish impulse (+1.5%)
    rows.append({"open": ob_low + 0.002, "high": ob_high - 0.005,
                 "low": ob_low + 0.001, "close": ob_high - 0.005, "vol": 600.0})
    rows.append({"open": ob_high - 0.005, "high": ob_high + 0.015,
                 "low": ob_high - 0.005, "close": ob_high + 0.015, "vol": 700.0})

    # Indices n-37 to n-3: price ranging above OB, staying < ob_high * 1.04
    for _ in range(35):
        rows.append({"open": ob_high + 0.010, "high": ob_high + 0.020,
                     "low": ob_high + 0.005, "close": ob_high + 0.012, "vol": 300.0})

    # Index n-2 (last completed): close BELOW ob_high — no ChoCH
    rows.append({"open": ob_high + 0.005, "high": ob_high + 0.006,
                 "low": ob_high - 0.008, "close": ob_high - 0.002, "vol": 400.0})

    # Index n-1 (current): low touches OB zone, close still below ob_high
    rows.append({"open": ob_high - 0.002, "high": ob_high + 0.001,
                 "low": ob_high - 0.006, "close": ob_high - 0.001, "vol": 350.0})

    assert len(rows) == n
    return pd.DataFrame(rows)


# ── Tests ─────────────────────────────────────────────────── #

def test_pending_long_fires_without_choch():
    """PENDING_LONG fires when OB is touched even though no candle closed above ob_high."""
    from strategy import evaluate_s5
    daily = _make_daily_bullish()
    htf   = _make_htf_bos()
    m15   = _make_m15_ob_touched_no_choch()

    sig, trigger, sl, tp, ob_low, ob_high, reason = evaluate_s5(
        "TESTUSDT", daily, htf, m15, "BULLISH"
    )

    assert sig == "PENDING_LONG", f"Expected PENDING_LONG, got {sig}: {reason}"
    assert trigger == ob_high, f"entry_trigger should be ob_high={ob_high}, got {trigger}"


def test_entry_trigger_equals_ob_high():
    """entry_trigger must be ob_high exactly (no buffer added)."""
    from strategy import evaluate_s5
    daily = _make_daily_bullish()
    htf   = _make_htf_bos()
    m15   = _make_m15_ob_touched_no_choch(ob_high=1.000)

    sig, trigger, sl, tp, ob_low, ob_high_ret, reason = evaluate_s5(
        "TESTUSDT", daily, htf, m15, "BULLISH"
    )
    if sig == "PENDING_LONG":
        assert trigger == ob_high_ret
        assert trigger == 1.000


def test_stale_ob_returns_hold():
    """HOLD when current price is already >4% above ob_high (stale OB)."""
    from strategy import evaluate_s5
    daily = _make_daily_bullish()
    htf   = _make_htf_bos()

    ob_high = 1.000
    m15 = _make_m15_ob_touched_no_choch(ob_high=ob_high)
    # Override last candle so current close is 5% above ob_high
    m15_stale = m15.copy()
    m15_stale.iloc[-1] = {"open": 1.050, "high": 1.060, "low": 1.045, "close": 1.055, "vol": 300.0}

    sig, *_, reason = evaluate_s5("TESTUSDT", daily, htf, m15_stale, "BULLISH")
    assert sig == "HOLD", f"Expected HOLD for stale OB, got {sig}: {reason}"
    assert "stale" in reason.lower() or "far" in reason.lower() or "above" in reason.lower()


def test_return_tuple_has_7_elements():
    """evaluate_s5 always returns a 7-tuple regardless of signal."""
    from strategy import evaluate_s5
    daily = _make_daily_bullish()
    htf   = _make_htf_bos()
    m15   = _make_m15_ob_touched_no_choch()

    result = evaluate_s5("TESTUSDT", daily, htf, m15, "BULLISH")
    assert len(result) == 7


def test_no_immediate_long_signal():
    """evaluate_s5 never returns LONG — only PENDING_LONG or HOLD."""
    from strategy import evaluate_s5
    daily = _make_daily_bullish()
    htf   = _make_htf_bos()
    m15   = _make_m15_ob_touched_no_choch()

    sig, *_ = evaluate_s5("TESTUSDT", daily, htf, m15, "BULLISH")
    assert sig != "LONG", "evaluate_s5 should never return immediate LONG in new design"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
pytest tests/test_s5_strategy.py -v 2>&1 | head -40
```

Expected: `test_pending_long_fires_without_choch` FAILS (current code requires ChoCH close).
`test_no_immediate_long_signal` may also FAIL (current code returns LONG when price > trigger).

- [ ] **Step 3: Update evaluate_s5() LONG branch in strategy.py**

In `strategy.py`, find the evaluate_s5 LONG branch. The changes are:

**3a. Update the config import** (inside evaluate_s5, around line 1093):

Remove `S5_ENTRY_BUFFER_PCT` from the import, add `S5_MAX_ENTRY_BUFFER`:

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

**3b. Replace the ChoCH block and entry logic** (LONG branch, after `ob_touched` check):

Remove this block entirely:
```python
        # ChoCH: last completed 15m candle closed above OB high
        last_closed = float(m15_df["close"].iloc[-2])
        if last_closed <= ob_high:
            return "HOLD", 0.0, 0.0, ob_low, ob_high, (
                f"Daily EMA ✅ | 1H BOS ✅ | Bullish OB {ob_low:.5f}–{ob_high:.5f} | "
                f"OB touched ✅ | Waiting ChoCH close above {ob_high:.5f} (last={last_closed:.5f})"
            )

        entry_trigger = ob_high * (1 + S5_ENTRY_BUFFER_PCT)
        sl_price      = ob_low  * (1 - S5_SL_BUFFER_PCT)
        current_close = float(m15_df["close"].iloc[-1])
```

Replace with:
```python
        # SMC entry: limit order at ob_high — no ChoCH candle close needed.
        # The limit fills only if price dips below ob_high and bounces back — that IS confirmation.
        entry_trigger = ob_high
        sl_price      = ob_low * (1 - S5_SL_BUFFER_PCT)
        current_close = float(m15_df["close"].iloc[-1])

        # Stale OB guard: if price has already moved too far above ob_high, skip
        if current_close > ob_high * (1 + S5_MAX_ENTRY_BUFFER):
            return "HOLD", entry_trigger, sl_price, 0.0, ob_low, ob_high, (
                f"Daily EMA ✅ | 1H BOS ✅ | Bullish OB ✅ | OB touched ✅ | "
                f"Stale — price {current_close:.5f} already >{S5_MAX_ENTRY_BUFFER*100:.0f}% above ob_high {ob_high:.5f}"
            )
```

**3c. Replace the PENDING_LONG/LONG branch** — remove the `if current_close <= entry_trigger` split and always return PENDING_LONG. Replace:

```python
        if current_close <= entry_trigger:
            return "PENDING_LONG", entry_trigger, sl_price, tp_price, ob_low, ob_high, (
                f"Daily EMA ✅ | 1H BOS ✅ | Bullish OB ✅ | ChoCH ✅ | R:R={rr:.1f} | "
                f"⏳ Waiting entry > {entry_trigger:.5f} (now {current_close:.5f})"
            )

        logger.info(
            f"[S5][{symbol}] ✅ LONG | Daily EMA bullish | 1H BOS ✅ | "
            f"Bullish OB {ob_low:.5f}–{ob_high:.5f} | ChoCH | TP={tp_price:.5f} | R:R={rr:.1f}"
        )
        return "LONG", entry_trigger, sl_price, tp_price, ob_low, ob_high, (
            f"S5 ✅ OB {ob_low:.5f}–{ob_high:.5f} | ChoCH LONG | TP={tp_price:.5f} R:R={rr:.1f}"
        )
```

With:

```python
        logger.info(
            f"[S5][{symbol}] 🕐 PENDING_LONG | Bullish OB {ob_low:.5f}–{ob_high:.5f} | "
            f"limit@{entry_trigger:.5f} | TP={tp_price:.5f} | R:R={rr:.1f}"
        )
        return "PENDING_LONG", entry_trigger, sl_price, tp_price, ob_low, ob_high, (
            f"S5 OB {ob_low:.5f}–{ob_high:.5f} | Limit@{entry_trigger:.5f} | TP={tp_price:.5f} R:R={rr:.1f}"
        )
```

**3d. Apply the same changes to the SHORT branch** (find_bearish_ob path, around line 1246):

- Remove the ChoCH block (SHORT version checks `last_closed >= ob_low`)
- Change `entry_trigger = ob_low * (1 - S5_ENTRY_BUFFER_PCT)` → `entry_trigger = ob_low`
- Add stale guard: `if current_close < ob_low * (1 - S5_MAX_ENTRY_BUFFER): return "HOLD", ...`
- Remove PENDING_SHORT/SHORT split, always return PENDING_SHORT

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_s5_strategy.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Verify both bots still import**

```bash
python -c "import bot; print('Bitget OK')"
python -c "import ig_bot; print('IG OK')"
```

- [ ] **Step 6: Commit**

```bash
git add strategy.py tests/test_s5_strategy.py
git commit -m "feat(s5): limit order entry at ob_high — remove ChoCH, always PENDING"
```

---

## Task 3: trader.py — add Bitget limit order functions

**Files:**
- Modify: `trader.py`
- Create: `tests/test_trader_limit_orders.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_trader_limit_orders.py`:

```python
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import pytest


def test_place_limit_long_calls_bitget_api(monkeypatch):
    """place_limit_long posts to Bitget place-order with correct fields."""
    import trader as tr
    posted = {}
    monkeypatch.setattr(tr, "_load_symbol_cache", lambda: None)
    monkeypatch.setattr(tr, "_sym_cache",
        {"ONTUSDT": {"price_place": 5, "volume_place": 0, "size_mult": 1.0, "min_trade_num": 1.0}})

    import bitget_client as bc
    def fake_post(path, body):
        posted.update({"path": path, "body": body})
        return {"data": {"orderId": "abc123", "clientOid": ""}}
    monkeypatch.setattr(bc, "post", fake_post)

    order_id = tr.place_limit_long("ONTUSDT", limit_price=0.07951, sl_price=0.07835,
                                    tp_price=0.08955, qty_str="100")
    assert order_id == "abc123"
    assert posted["path"] == "/api/v2/mix/order/place-order"
    assert posted["body"]["orderType"] == "limit"
    assert posted["body"]["side"] == "buy"
    assert posted["body"]["timeInForceValue"] == "gtc"
    assert float(posted["body"]["price"]) == pytest.approx(0.07951)
    assert float(posted["body"]["presetStopLossPrice"]) == pytest.approx(0.07835)


def test_place_limit_short_calls_bitget_api(monkeypatch):
    """place_limit_short posts to Bitget place-order with side=sell."""
    import trader as tr
    posted = {}
    monkeypatch.setattr(tr, "_load_symbol_cache", lambda: None)
    monkeypatch.setattr(tr, "_sym_cache",
        {"ONTUSDT": {"price_place": 5, "volume_place": 0, "size_mult": 1.0, "min_trade_num": 1.0}})

    import bitget_client as bc
    def fake_post(path, body):
        posted.update({"path": path, "body": body})
        return {"data": {"orderId": "def456", "clientOid": ""}}
    monkeypatch.setattr(bc, "post", fake_post)

    order_id = tr.place_limit_short("ONTUSDT", limit_price=0.08100, sl_price=0.08250,
                                     tp_price=0.07500, qty_str="100")
    assert order_id == "def456"
    assert posted["body"]["side"] == "sell"
    assert posted["body"]["orderType"] == "limit"


def test_cancel_order_calls_bitget_api(monkeypatch):
    """cancel_order posts to Bitget cancel-order with correct order_id."""
    import trader as tr
    posted = {}
    import bitget_client as bc
    monkeypatch.setattr(bc, "post", lambda path, body: posted.update({"path": path, "body": body}) or {})

    tr.cancel_order("ONTUSDT", "abc123")
    assert posted["path"] == "/api/v2/mix/order/cancel-order"
    assert posted["body"]["orderId"] == "abc123"


def test_get_order_fill_returns_filled(monkeypatch):
    """get_order_fill returns status=filled and fill_price when order is done."""
    import trader as tr
    import bitget_client as bc
    monkeypatch.setattr(bc, "get", lambda path, params: {
        "data": {"status": "filled", "priceAvg": "0.07951", "orderId": "abc123"}
    })

    result = tr.get_order_fill("ONTUSDT", "abc123")
    assert result["status"] == "filled"
    assert result["fill_price"] == pytest.approx(0.07951)


def test_get_order_fill_returns_live(monkeypatch):
    """get_order_fill returns status=live when order is still open."""
    import trader as tr
    import bitget_client as bc
    monkeypatch.setattr(bc, "get", lambda path, params: {
        "data": {"status": "live", "priceAvg": "0", "orderId": "abc123"}
    })

    result = tr.get_order_fill("ONTUSDT", "abc123")
    assert result["status"] == "live"
    assert result["fill_price"] == 0.0
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_trader_limit_orders.py -v 2>&1 | head -20
```

Expected: all fail with `AttributeError: module 'trader' has no attribute 'place_limit_long'`.

- [ ] **Step 3: Add functions to trader.py**

Add after the existing `cancel_all_orders` function (around line 604):

```python
def place_limit_long(symbol: str, limit_price: float, sl_price: float,
                     tp_price: float, qty_str: str) -> str:
    """
    Place a GTC limit buy order at limit_price with preset SL.
    SL is active immediately on fill — no unprotected window.
    Returns order_id string.
    """
    body = {
        "symbol":               symbol,
        "productType":          PRODUCT_TYPE,
        "marginMode":           "isolated",
        "marginCoin":           MARGIN_COIN,
        "size":                 qty_str,
        "price":                _round_price(limit_price, symbol),
        "side":                 "buy",
        "tradeSide":            "open",
        "orderType":            "limit",
        "timeInForceValue":     "gtc",
        "presetStopLossPrice":  _round_price(sl_price, symbol),
    }
    if tp_price > limit_price:
        body["presetTakeProfitPrice"] = _round_price(tp_price, symbol)
    data = bc.post("/api/v2/mix/order/place-order", body)
    return str(data.get("data", {}).get("orderId", ""))


def place_limit_short(symbol: str, limit_price: float, sl_price: float,
                      tp_price: float, qty_str: str) -> str:
    """
    Place a GTC limit sell order at limit_price with preset SL.
    Returns order_id string.
    """
    body = {
        "symbol":               symbol,
        "productType":          PRODUCT_TYPE,
        "marginMode":           "isolated",
        "marginCoin":           MARGIN_COIN,
        "size":                 qty_str,
        "price":                _round_price(limit_price, symbol),
        "side":                 "sell",
        "tradeSide":            "open",
        "orderType":            "limit",
        "timeInForceValue":     "gtc",
        "presetStopLossPrice":  _round_price(sl_price, symbol),
    }
    if tp_price > 0 and tp_price < limit_price:
        body["presetTakeProfitPrice"] = _round_price(tp_price, symbol)
    data = bc.post("/api/v2/mix/order/place-order", body)
    return str(data.get("data", {}).get("orderId", ""))


def cancel_order(symbol: str, order_id: str) -> None:
    """Cancel an open limit order by order_id."""
    bc.post("/api/v2/mix/order/cancel-order", {
        "symbol":      symbol,
        "productType": PRODUCT_TYPE,
        "orderId":     order_id,
    })


def get_order_fill(symbol: str, order_id: str) -> dict:
    """
    Poll order status.
    Returns {"status": "live"|"filled"|"cancelled", "fill_price": float}
    Bitget statuses: "live" (open), "filled", "cancelled", "partially_fill"
    """
    data = bc.get("/api/v2/mix/order/detail", params={
        "symbol":      symbol,
        "productType": PRODUCT_TYPE,
        "orderId":     order_id,
    })
    order = data.get("data", {})
    raw_status = order.get("status", "live")
    fill_price = float(order.get("priceAvg") or 0)
    # Normalise: "partially_fill" treated as live (still open)
    if raw_status == "partially_fill":
        raw_status = "live"
    return {"status": raw_status, "fill_price": fill_price}
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_trader_limit_orders.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add trader.py tests/test_trader_limit_orders.py
git commit -m "feat(trader): add place_limit_long/short, cancel_order, get_order_fill"
```

---

## Task 4: bot.py — _queue_s5_pending places limit order immediately

**Files:**
- Modify: `bot.py` — `_queue_s5_pending` method (around line 1403)

The key change: after storing the signal in `self.pending_signals`, immediately compute qty and call `tr.place_limit_long/short()`. Store the returned `order_id` in the pending dict.

- [ ] **Step 1: Update _queue_s5_pending in bot.py**

In the `_queue_s5_pending` method, locate where `self.pending_signals[symbol] = {...}` is set (around line 1553). After that assignment, add the limit order placement:

```python
        self.pending_signals[symbol] = {
            "strategy": "S5", "side": side,
            "trigger": trigger, "sl": sl, "tp": tp,
            "ob_low": ob_low, "ob_high": ob_high,
            "rr": rr, "sr_clearance_pct": sr_pct,
            "sentiment": self.sentiment.direction if self.sentiment else "?",
            "expires": time.time() + 4 * 3600,
            "priority_rank": priority_rank,
            "priority_score": priority_score,
            "order_id": None,   # filled in below
        }

        # Place the GTC limit order immediately — SL preset so position is protected on fill
        try:
            balance = tr.get_usdt_balance()
            equity  = tr._get_total_equity() or balance
            notional = equity * config_s5.S5_TRADE_SIZE_PCT * config_s5.S5_LEVERAGE
            mark     = tr.get_mark_price(symbol)
            qty_str  = tr._round_qty(notional / mark, symbol)
            if side == "LONG":
                order_id = tr.place_limit_long(symbol, trigger, sl, tp, qty_str)
            else:
                order_id = tr.place_limit_short(symbol, trigger, sl, tp, qty_str)
            self.pending_signals[symbol]["order_id"] = order_id
            logger.info(
                f"[S5][{symbol}] 📋 Limit {side} placed @ {trigger:.5f} | "
                f"order_id={order_id} | SL={sl:.5f} | TP={tp:.5f}"
            )
        except Exception as e:
            logger.error(f"[S5][{symbol}] ❌ Failed to place limit order: {e}")
            self.pending_signals.pop(symbol, None)
            return
```

- [ ] **Step 2: Verify bot imports cleanly**

```bash
python -c "import bot; print('Bitget OK')"
```

- [ ] **Step 3: Commit**

```bash
git add bot.py
git commit -m "feat(bot): _queue_s5_pending places GTC limit order immediately"
```

---

## Task 5: bot.py — watcher polls order fill + OB invalidation + _handle_limit_filled

**Files:**
- Modify: `bot.py` — `_entry_watcher_loop` (around line 1571) and add `_handle_limit_filled`

- [ ] **Step 1: Replace _entry_watcher_loop pending signal block in bot.py**

Find the `if self.pending_signals:` block inside `_entry_watcher_loop` (lines ~1584-1648). Replace the entire pending signals loop body with:

```python
            if self.pending_signals:
                try:
                    balance = tr.get_usdt_balance()
                except Exception:
                    time.sleep(4)
                    continue
                ordered = sorted(
                    self.pending_signals.items(),
                    key=lambda kv: kv[1].get("priority_rank", 999),
                )
                for symbol, sig in ordered:
                    if not sig:
                        continue
                    # Expire stale signals
                    if time.time() > sig["expires"]:
                        logger.info(f"[S5][{symbol}] ⏰ Pending expired — cancelling limit order")
                        if sig.get("order_id"):
                            try:
                                tr.cancel_order(symbol, sig["order_id"])
                            except Exception as e:
                                logger.warning(f"[S5][{symbol}] cancel on expire failed: {e}")
                        st.add_scan_log(f"[S5][{symbol}] ⏰ Pending expired", "INFO")
                        self.pending_signals.pop(symbol, None)
                        continue
                    # Already in a trade (concurrent fill from another path)
                    if symbol in self.active_positions:
                        self.pending_signals.pop(symbol, None)
                        continue
                    # Need an order_id to poll — if missing, signal was never placed
                    order_id = sig.get("order_id")
                    if not order_id:
                        self.pending_signals.pop(symbol, None)
                        continue
                    try:
                        mark = tr.get_mark_price(symbol)
                    except Exception:
                        continue
                    # OB invalidation: price closed below ob_low (OB support broken)
                    ob_low = sig.get("ob_low", 0)
                    invalidation_threshold = ob_low * (1 - config_s5.S5_OB_INVALIDATION_BUFFER_PCT) if ob_low > 0 else 0
                    if sig["side"] == "LONG" and invalidation_threshold > 0 and mark < invalidation_threshold:
                        logger.info(
                            f"[S5][{symbol}] 🚫 OB invalidated — mark {mark:.5f} < ob_low {ob_low:.5f} "
                            f"— cancelling limit order"
                        )
                        try:
                            tr.cancel_order(symbol, order_id)
                        except Exception as e:
                            logger.warning(f"[S5][{symbol}] cancel on OB invalidation failed: {e}")
                        st.add_scan_log(f"[S5][{symbol}] 🚫 OB invalidated — limit cancelled", "WARN")
                        self.pending_signals.pop(symbol, None)
                        continue
                    ob_high = sig.get("ob_high", 0)
                    if sig["side"] == "SHORT" and ob_high > 0 and mark > ob_high * (1 + config_s5.S5_OB_INVALIDATION_BUFFER_PCT):
                        logger.info(f"[S5][{symbol}] 🚫 OB invalidated (SHORT) — cancelling")
                        try:
                            tr.cancel_order(symbol, order_id)
                        except Exception as e:
                            logger.warning(f"[S5][{symbol}] cancel SHORT OB invalidation failed: {e}")
                        self.pending_signals.pop(symbol, None)
                        continue
                    # Poll order fill status
                    try:
                        fill = tr.get_order_fill(symbol, order_id)
                    except Exception as e:
                        logger.debug(f"[S5][{symbol}] order status poll error: {e}")
                        continue
                    if fill["status"] == "cancelled":
                        logger.info(f"[S5][{symbol}] Limit order cancelled externally — removing")
                        self.pending_signals.pop(symbol, None)
                        continue
                    if fill["status"] == "filled":
                        with self._trade_lock:
                            if symbol in self.active_positions:
                                self.pending_signals.pop(symbol, None)
                                continue
                            if len(self.active_positions) >= config.MAX_CONCURRENT_TRADES:
                                break
                            if st.is_pair_paused(symbol):
                                continue
                            self._handle_limit_filled(symbol, sig, fill["fill_price"], balance)
                        self.pending_signals.pop(symbol, None)
```

- [ ] **Step 2: Add _handle_limit_filled method to bot.py**

Add the new method after `_fire_pending` (around line 1700). This replaces `_fire_pending` for the limit order path:

```python
    def _handle_limit_filled(self, symbol: str, sig: dict, fill_price: float, balance: float) -> None:
        """
        Called when a GTC limit order fills. Sets up full S5 exits and logs the trade.
        Called under _trade_lock.
        The preset SL is already active (placed at limit order time); here we replace it
        with the full _place_s5_exits setup (partial TP at 1:1 + trailing candle stop).
        """
        side = sig["side"]
        logger.info(
            f"[S5][{symbol}] ✅ Limit filled {side} @ {fill_price:.5f} "
            f"(limit={sig['trigger']:.5f} | SL={sig['sl']:.5f} | TP={sig['tp']:.5f})"
        )
        # Re-derive qty from fill (Bitget order detail has filled size; use balance for margin calc)
        equity   = tr._get_total_equity() or balance
        notional = equity * config_s5.S5_TRADE_SIZE_PCT * config_s5.S5_LEVERAGE
        qty_str  = tr._round_qty(notional / fill_price, symbol)

        sl_trig  = float(tr._round_price(sig["sl"], symbol))
        sl_exec  = float(tr._round_price(sig["sl"] * 0.995, symbol))
        one_r    = abs(fill_price - sl_trig)
        if side == "LONG":
            part_trig = float(tr._round_price(fill_price + one_r, symbol))
        else:
            part_trig = float(tr._round_price(fill_price - one_r, symbol))
        tp_targ  = float(tr._round_price(sig["tp"], symbol)) if sig.get("tp") else 0.0

        hold_side = "long" if side == "LONG" else "short"
        ok = tr._place_s5_exits(
            symbol, hold_side, qty_str,
            sl_trig, sl_exec,
            part_trig, tp_targ, config_s5.S5_TRAIL_RANGE_PCT,
        )
        if not ok:
            logger.error(f"[S5][{symbol}] ⚠️ S5 exits failed after limit fill — set manually: SL={sl_trig}")

        trade_id = uuid.uuid4().hex[:8]
        trade = {
            "symbol": symbol, "side": side, "strategy": "S5",
            "entry": fill_price, "sl": sl_trig, "tp": tp_targ or part_trig,
            "qty": qty_str, "leverage": config_s5.S5_LEVERAGE,
            "margin": round(equity * config_s5.S5_TRADE_SIZE_PCT, 4),
            "tpsl_set": ok, "trade_id": trade_id,
            "snap_entry_trigger": round(sig["trigger"], 8),
            "snap_sl":            round(sig["sl"], 8),
            "snap_rr":            sig.get("rr"),
            "snap_sentiment":     sig.get("sentiment", "?"),
            "snap_sr_clearance_pct": sig.get("sr_clearance_pct"),
            "snap_s5_ob_low":  round(sig["ob_low"],  8) if sig.get("ob_low")  else None,
            "snap_s5_ob_high": round(sig["ob_high"], 8) if sig.get("ob_high") else None,
            "snap_s5_tp":      round(sig["tp"], 8)      if sig.get("tp")      else None,
        }
        _log_trade(f"S5_{side}", trade)
        st.add_open_trade(trade)
        if PAPER_MODE:
            tr.tag_strategy(symbol, "S5")
        self.active_positions[symbol] = {
            "side": side, "strategy": "S5",
            "box_high": sig["trigger"], "box_low": sig["sl"],
            "trade_id": trade_id,
        }
        st.add_scan_log(
            f"[S5][{symbol}] ✅ {'🟢' if side == 'LONG' else '🔴'} {side} filled @ {fill_price:.5f} | "
            f"OB {sig.get('ob_low', 0):.5f}–{sig.get('ob_high', 0):.5f}",
            "SIGNAL"
        )
```

- [ ] **Step 3: Verify bot imports cleanly**

```bash
python -c "import bot; print('Bitget OK')"
```

- [ ] **Step 4: Commit**

```bash
git add bot.py
git commit -m "feat(bot): watcher polls order fill + OB invalidation, add _handle_limit_filled"
```

---

## Task 6: ig_client.py — add IG working order functions

**Files:**
- Modify: `ig_client.py`
- Create: `tests/test_ig_limit_orders.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_ig_limit_orders.py`:

```python
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import pytest


def _mock_session(monkeypatch, post_response=None, get_response=None, delete_response=None):
    """Helper: patch ig_client._get_session() with fake HTTP methods."""
    import ig_client
    class FakeSession:
        def post(self, endpoint, body=None, version="1"):
            return post_response or {}
        def get(self, endpoint, params=None, version="1"):
            return get_response or {}
        def delete(self, endpoint, body=None, version="1"):
            return delete_response or {}
    monkeypatch.setattr(ig_client, "_get_session", lambda: FakeSession())


def test_place_limit_long_places_working_order(monkeypatch):
    """place_limit_long posts to /workingorders/otc with LIMIT type and GTC."""
    import ig_client
    calls = []
    class FakeSession:
        def post(self, endpoint, body=None, version="1"):
            calls.append({"endpoint": endpoint, "body": body})
            return {"dealReference": "REF001"}
        def get(self, endpoint, params=None, version="1"):
            # _poll_confirm response
            return {"dealStatus": "ACCEPTED", "dealId": "DEAL001", "level": 43500.0}
    monkeypatch.setattr(ig_client, "_get_session", lambda: FakeSession())

    deal_id = ig_client.place_limit_long("IX.D.DOW.DAILY.IP",
                                          limit_price=43500.0,
                                          sl_price=43200.0,
                                          tp_price=44000.0)
    assert deal_id == "DEAL001"
    assert len(calls) == 1
    assert calls[0]["endpoint"] == "/workingorders/otc"
    assert calls[0]["body"]["type"] == "LIMIT"
    assert calls[0]["body"]["timeInForce"] == "GOOD_TILL_CANCELLED"
    assert calls[0]["body"]["direction"] == "BUY"
    assert calls[0]["body"]["level"] == 43500.0
    assert calls[0]["body"]["stopLevel"] == 43200.0


def test_place_limit_short_uses_sell_direction(monkeypatch):
    """place_limit_short posts with direction=SELL."""
    import ig_client
    calls = []
    class FakeSession:
        def post(self, endpoint, body=None, version="1"):
            calls.append(body)
            return {"dealReference": "REF002"}
        def get(self, endpoint, params=None, version="1"):
            return {"dealStatus": "ACCEPTED", "dealId": "DEAL002", "level": 43800.0}
    monkeypatch.setattr(ig_client, "_get_session", lambda: FakeSession())

    ig_client.place_limit_short("IX.D.DOW.DAILY.IP", 43800.0, 44100.0, 43200.0)
    assert calls[0]["direction"] == "SELL"
    assert calls[0]["type"] == "LIMIT"


def test_cancel_working_order_calls_delete(monkeypatch):
    """cancel_working_order sends DELETE to /workingorders/otc/{dealId}."""
    import ig_client
    deleted = []
    class FakeSession:
        def delete(self, endpoint, body=None, version="1"):
            deleted.append(endpoint)
            return {}
    monkeypatch.setattr(ig_client, "_get_session", lambda: FakeSession())

    ig_client.cancel_working_order("DEAL001")
    assert deleted == ["/workingorders/otc/DEAL001"]


def test_get_working_order_status_open(monkeypatch):
    """get_working_order_status returns open when working order still pending."""
    import ig_client
    class FakeSession:
        def get(self, endpoint, params=None, version="1"):
            return {"workingOrders": [{"workingOrderData": {"dealId": "DEAL001", "orderLevel": 43500.0}}]}
    monkeypatch.setattr(ig_client, "_get_session", lambda: FakeSession())

    result = ig_client.get_working_order_status("DEAL001")
    assert result["status"] == "open"
    assert result["fill_price"] is None


def test_get_working_order_status_filled(monkeypatch):
    """get_working_order_status returns filled when order is no longer in working orders (became a position)."""
    import ig_client
    class FakeSession:
        def get(self, endpoint, params=None, version="1"):
            # Order not found in working orders = it filled
            return {"workingOrders": []}
    monkeypatch.setattr(ig_client, "_get_session", lambda: FakeSession())

    result = ig_client.get_working_order_status("DEAL001")
    assert result["status"] == "filled"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_ig_limit_orders.py -v 2>&1 | head -20
```

Expected: all fail with `AttributeError: module 'ig_client' has no attribute 'place_limit_long'`.

- [ ] **Step 3: Add functions to ig_client.py**

Add after the `open_short` function (around line 325):

```python
def place_limit_long(epic: str, limit_price: float, sl_price: float,
                     tp_price: float, size: float = None) -> str:
    """
    Place a GTC limit BUY working order with attached SL.
    SL is active immediately on fill.
    Returns deal_id string.
    """
    if size is None:
        size = config_ig.CONTRACT_SIZE
    body = {
        "epic":           epic,
        "expiry":         "-",
        "direction":      "BUY",
        "size":           size,
        "level":          round(limit_price, 1),
        "type":           "LIMIT",
        "timeInForce":    "GOOD_TILL_CANCELLED",
        "currencyCode":   config_ig.CURRENCY,
        "forceOpen":      True,
        "guaranteedStop": False,
        "stopLevel":      round(sl_price, 1) if sl_price else None,
        "limitLevel":     round(tp_price, 1) if tp_price else None,
    }
    body = {k: v for k, v in body.items() if v is not None}
    resp     = _get_session().post("/workingorders/otc", body=body, version="2")
    deal_ref = resp.get("dealReference", "")
    confirm  = _poll_confirm(deal_ref)
    return confirm.get("dealId", "")


def place_limit_short(epic: str, limit_price: float, sl_price: float,
                      tp_price: float, size: float = None) -> str:
    """
    Place a GTC limit SELL working order with attached SL.
    Returns deal_id string.
    """
    if size is None:
        size = config_ig.CONTRACT_SIZE
    body = {
        "epic":           epic,
        "expiry":         "-",
        "direction":      "SELL",
        "size":           size,
        "level":          round(limit_price, 1),
        "type":           "LIMIT",
        "timeInForce":    "GOOD_TILL_CANCELLED",
        "currencyCode":   config_ig.CURRENCY,
        "forceOpen":      True,
        "guaranteedStop": False,
        "stopLevel":      round(sl_price, 1) if sl_price else None,
        "limitLevel":     round(tp_price, 1) if tp_price else None,
    }
    body = {k: v for k, v in body.items() if v is not None}
    resp     = _get_session().post("/workingorders/otc", body=body, version="2")
    deal_ref = resp.get("dealReference", "")
    confirm  = _poll_confirm(deal_ref)
    return confirm.get("dealId", "")


def cancel_working_order(deal_id: str) -> None:
    """Cancel a GTC working order by deal_id."""
    _get_session().delete(f"/workingorders/otc/{deal_id}", version="2")


def get_working_order_status(deal_id: str) -> dict:
    """
    Check if a working order is still open or has been filled.
    IG does not have a direct "get order by ID" endpoint for working orders.
    Strategy: GET /workingorders and look for deal_id.
      - Found → status=open (still pending)
      - Not found → status=filled (order became a position) or deleted
    Returns {"status": "open"|"filled"|"deleted", "fill_price": float | None}
    """
    data   = _get_session().get("/workingorders", version="2")
    orders = data.get("workingOrders", [])
    for o in orders:
        wd = o.get("workingOrderData", {})
        if wd.get("dealId") == deal_id:
            return {"status": "open", "fill_price": None}
    # Not found in working orders — assume filled (caller checks active positions to confirm)
    return {"status": "filled", "fill_price": None}
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_ig_limit_orders.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Verify IG bot imports cleanly**

```bash
python -c "import ig_bot; print('IG OK')"
```

- [ ] **Step 6: Commit**

```bash
git add ig_client.py tests/test_ig_limit_orders.py
git commit -m "feat(ig_client): add place_limit_long/short, cancel_working_order, get_working_order_status"
```

---

## Task 7: ig_bot.py — pending_order state, PENDING handling, fill detection, session-end cancel

**Files:**
- Modify: `ig_bot.py`

- [ ] **Step 1: Add pending_order field to IGBot.__init__**

In `IGBot.__init__` (around line 265), add after `self.position = None` (or similar):

```python
self.pending_order: dict | None = None
# Structure when set:
# {"deal_id": str, "side": str, "ob_low": float, "ob_high": float,
#  "sl": float, "tp": float, "trigger": float}
```

- [ ] **Step 2: Add _check_pending_order method to IGBot**

Add a new method after `_open_trade`:

```python
    def _check_pending_order(self) -> None:
        """
        Called each tick when self.pending_order is set.
        Checks fill status and OB invalidation.
        """
        import config_s5
        po       = self.pending_order
        deal_id  = po["deal_id"]
        mark     = ig.get_mark_price(EPIC)
        if mark <= 0:
            return

        # OB invalidation
        if po["side"] == "LONG":
            threshold = po["ob_low"] * (1 - config_s5.S5_OB_INVALIDATION_BUFFER_PCT)
            if mark < threshold:
                logger.info(f"[S5] OB invalidated — mark {mark:.1f} < ob_low {po['ob_low']:.1f} — cancelling")
                try:
                    ig.cancel_working_order(deal_id)
                except Exception as e:
                    logger.warning(f"[S5] cancel on OB invalidation failed: {e}")
                self.pending_order = None
                self._save_state()
                return
        else:
            threshold = po["ob_high"] * (1 + config_s5.S5_OB_INVALIDATION_BUFFER_PCT)
            if mark > threshold:
                logger.info(f"[S5] OB invalidated (SHORT) — cancelling")
                try:
                    ig.cancel_working_order(deal_id)
                except Exception as e:
                    logger.warning(f"[S5] cancel SHORT OB invalidation failed: {e}")
                self.pending_order = None
                self._save_state()
                return

        # Poll fill status
        try:
            status_info = ig.get_working_order_status(deal_id)
        except Exception as e:
            logger.debug(f"[S5] working order status poll error: {e}")
            return

        if status_info["status"] == "filled":
            logger.info(f"[S5] Limit order filled — syncing position")
            # Sync the now-open position from exchange
            self._sync_live_position()
            if self.position:
                logger.info(f"[S5] ✅ Position confirmed: {self.position['side']} @ {self.position['entry']:.1f}")
                _log_trade(f"S5_{po['side']}", {
                    "side": po["side"], "qty": self.position.get("qty"),
                    "entry": self.position.get("entry"),
                    "sl": po["sl"], "tp": po["tp"],
                    "snap_entry_trigger": round(po["trigger"], 5),
                    "snap_sl": round(po["sl"], 5),
                    "snap_s5_ob_low":  round(po["ob_low"],  5),
                    "snap_s5_ob_high": round(po["ob_high"], 5),
                    "snap_s5_tp":      round(po["tp"],      5),
                }, paper=self.paper)
            self.pending_order = None
            self._save_state()
```

- [ ] **Step 3: Update _tick() to check pending order and handle PENDING signal**

In `_tick` (around line 379), insert step 2b after step 2 (monitor open position):

```python
        # 2b. Check pending limit order (fill or OB invalidation)
        if self.pending_order:
            self._check_pending_order()
            return  # don't look for new entries while a limit order is live
```

Then update step 7 (evaluate signal) — replace:

```python
        if sig not in ("LONG", "SHORT"):
            return

        # 8. Entry window check
        mark = ig.get_mark_price(EPIC)
        if mark <= 0 or not _entry_in_window(sig, mark, trigger):
            return

        # 9. Open trade
        self._open_trade(sig, sl, tp, ob_low, ob_high, trigger, mark)
```

With:

```python
        if sig not in ("PENDING_LONG", "PENDING_SHORT"):
            return

        # Already have a pending order — don't stack
        if self.pending_order:
            return

        # 8. Entry window check — only place limit if we're in the trading window
        mark = ig.get_mark_price(EPIC)
        if mark <= 0:
            return

        # 9. Place GTC limit order at ob_high/ob_low
        side = "LONG" if sig == "PENDING_LONG" else "SHORT"
        try:
            if side == "LONG":
                deal_id = ig.place_limit_long(EPIC, trigger, sl, tp)
            else:
                deal_id = ig.place_limit_short(EPIC, trigger, sl, tp)
        except Exception as e:
            logger.error(f"[S5] Failed to place IG limit order: {e}")
            return

        self.pending_order = {
            "deal_id":  deal_id,
            "side":     side,
            "ob_low":   ob_low,
            "ob_high":  ob_high,
            "sl":       sl,
            "tp":       tp,
            "trigger":  trigger,
        }
        self._save_state()
        logger.info(
            f"[S5] 📋 Limit {side} placed @ {trigger:.1f} | deal_id={deal_id} | "
            f"OB {ob_low:.1f}–{ob_high:.1f} | SL={sl:.1f}"
        )
```

- [ ] **Step 4: Update _session_end_close to cancel pending_order**

In `_session_end_close` (around line 666), at the start of the method add:

```python
        # Cancel any pending limit order before session closes
        if self.pending_order:
            try:
                ig.cancel_working_order(self.pending_order["deal_id"])
                logger.info(f"[S5] Session end — cancelled pending limit order {self.pending_order['deal_id']}")
            except Exception as e:
                logger.warning(f"[S5] Session end cancel failed: {e}")
            self.pending_order = None
```

- [ ] **Step 5: Update _save_state and _load to persist pending_order**

In `_save_state` (around line 349), add `"pending_order": self.pending_order` to the state dict being written.

In `_load` (around line 168), add:
```python
self.pending_order = state.get("pending_order", None)
```

- [ ] **Step 6: Verify IG bot imports cleanly**

```bash
python -c "import ig_bot; print('IG OK')"
```

- [ ] **Step 7: Verify both bots import cleanly**

```bash
python -c "import bot; print('Bitget OK')"
python -c "import ig_bot; print('IG OK')"
```

- [ ] **Step 8: Run full test suite**

```bash
pytest tests/ -v
```

Expected: all tests pass, no regressions.

- [ ] **Step 9: Commit**

```bash
git add ig_bot.py
git commit -m "feat(ig_bot): pending_order state, PENDING handling, fill detection, session-end cancel"
```

---

## Final Verification

- [ ] **Run QA suite**

```bash
pytest tests/ -v
```

Expected: all tests green.

- [ ] **Verify config invariant: config_ig_s5 has no unknown attributes**

```bash
python -c "
import config_s5 as c5, config_ig_s5 as ig5
base = {a for a in dir(c5) if not a.startswith('_')}
for attr in [a for a in dir(ig5) if not a.startswith('_')]:
    assert attr in base, f'config_ig_s5.{attr} has no match in config_s5'
print('Config invariant OK')
"
```

- [ ] **Verify evaluate_s5 import is still inside function (not module-level)**

```bash
grep -n "from config_s5 import" strategy.py
```

Expected: line number inside the `def evaluate_s5` body (not at top of file).

- [ ] **Verify evaluate_s5 never returns LONG or SHORT directly**

```bash
grep -n "return \"LONG\"\|return \"SHORT\"\|return 'LONG'\|return 'SHORT'" strategy.py | grep -v "evaluate_s[1234]"
```

Expected: no matches in the evaluate_s5 function body.

- [ ] **Final commit tag**

```bash
git tag s5-limit-order-entry
```
