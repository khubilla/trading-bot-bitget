# Liquidity Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Filter illiquid pairs out of `qualified_pairs` at scan time so no strategy ever sees a pair with a thin order book.

**Architecture:** Add two config params (`LIQUIDITY_CHECK_ENABLED`, `MIN_OB_DEPTH_USDT`) and a private helper `_filter_by_liquidity()` in `scanner.py`. The helper is called as the last step in `get_qualified_pairs_and_sentiment()`, after volume and price filters. Top-of-book USDT depth (`bidSz×bidPr + askSz×askPr`) is read from the existing bulk ticker response — zero extra API calls.

**Tech Stack:** Python, pytest, Bitget REST API v2 (`/api/v2/mix/market/tickers`)

---

## File Map

| File | Change |
|---|---|
| `config.py` | Add 2 new params near `MIN_VOLUME_USDT` (line 36) |
| `scanner.py` | Update import line 24; add `depth_map` to ticker loop; add `_filter_by_liquidity()`; wire up before return |
| `tests/test_scanner_liquidity.py` | New file — unit tests for helper + integration tests |

No changes to `bot.py`, `trader.py`, `ig_bot.py`, strategy files, state.json, or CSV schema.

---

## Task 1: Verify ticker fields + add config params

**Files:**
- Modify: `config.py:36-38`

- [ ] **Step 1: Verify `bidSz`/`askSz` exist in the Bitget tickers response**

Run this one-liner (requires API credentials in environment):

```bash
python -c "
import bitget_client as bc
data = bc.get_public('/api/v2/mix/market/tickers', {'productType': 'usdt-futures'})
sample = next((t for t in data['data'] if t.get('symbol') == 'BTCUSDT'), data['data'][0])
print('bidSz present:', 'bidSz' in sample)
print('askSz present:', 'askSz' in sample)
print('keys:', sorted(sample.keys()))
"
```

**Expected output if fields exist:**
```
bidSz present: True
askSz present: True
keys: ['askPr', 'askSz', 'baseVolume', 'bidPr', 'bidSz', ...]
```

**If `bidSz`/`askSz` are absent:** The ticker endpoint doesn't provide sizes. Use the merge-depth fallback described at the end of this task instead of the ticker approach in Tasks 3 and 5.

- [ ] **Step 2: Add config params**

In `config.py`, after line 38 (`SCAN_INTERVAL_SEC = 60`), add:

```python
# --- Liquidity Filter ---
LIQUIDITY_CHECK_ENABLED = True
MIN_OB_DEPTH_USDT       = 50_000   # bidSz×bidPr + askSz×askPr must meet this
```

- [ ] **Step 3: Verify config imports cleanly**

```bash
python -c "from config import LIQUIDITY_CHECK_ENABLED, MIN_OB_DEPTH_USDT; print(LIQUIDITY_CHECK_ENABLED, MIN_OB_DEPTH_USDT)"
```

Expected: `True 50000`

---

## Task 2: Write failing unit tests for `_filter_by_liquidity`

**Files:**
- Create: `tests/test_scanner_liquidity.py`

- [ ] **Step 1: Create the test file**

```python
# tests/test_scanner_liquidity.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import scanner


def _ticker(symbol, bid_pr, ask_pr, bid_sz, ask_sz, vol=10_000_000):
    """Build a minimal ticker dict that passes volume and price filters."""
    last_pr = (float(bid_pr) + float(ask_pr)) / 2
    return {
        "symbol":      symbol,
        "quoteVolume": str(vol),
        "openUtc":     str(last_pr * 0.99),
        "lastPr":      str(last_pr),
        "bidPr":       str(bid_pr),
        "askPr":       str(ask_pr),
        "bidSz":       str(bid_sz),
        "askSz":       str(ask_sz),
    }


def _mock_response(tickers):
    return {"code": "00000", "data": tickers}


# ── Unit tests for _filter_by_liquidity ──────────────────────────────

class TestFilterByLiquidity:

    def test_removes_shallow_pairs(self, monkeypatch):
        monkeypatch.setattr(scanner, "MIN_OB_DEPTH_USDT", 50_000)
        depth_map = {"LIQUIDUSDT": 175_000.0, "ARIAUSDT": 367.0}
        result = scanner._filter_by_liquidity(["LIQUIDUSDT", "ARIAUSDT"], depth_map)
        assert result == ["LIQUIDUSDT"]

    def test_keeps_pair_at_exact_threshold(self, monkeypatch):
        monkeypatch.setattr(scanner, "MIN_OB_DEPTH_USDT", 50_000)
        result = scanner._filter_by_liquidity(["XYZUSDT"], {"XYZUSDT": 50_000.0})
        assert result == ["XYZUSDT"]

    def test_missing_pair_in_depth_map_excluded(self, monkeypatch):
        monkeypatch.setattr(scanner, "MIN_OB_DEPTH_USDT", 50_000)
        result = scanner._filter_by_liquidity(["XYZUSDT"], depth_map={})
        assert result == []

    def test_all_liquid_returns_all(self, monkeypatch):
        monkeypatch.setattr(scanner, "MIN_OB_DEPTH_USDT", 50_000)
        pairs = ["AAUSDT", "BBUSDT"]
        depth_map = {"AAUSDT": 200_000.0, "BBUSDT": 80_000.0}
        result = scanner._filter_by_liquidity(pairs, depth_map)
        assert result == pairs

    def test_empty_input_returns_empty(self, monkeypatch):
        monkeypatch.setattr(scanner, "MIN_OB_DEPTH_USDT", 50_000)
        result = scanner._filter_by_liquidity([], depth_map={})
        assert result == []


# ── Integration tests for get_qualified_pairs_and_sentiment ──────────

class TestLiquidityFilterIntegration:

    def _setup(self, monkeypatch):
        monkeypatch.setattr(scanner, "MIN_VOLUME_USDT", 5_000_000)
        monkeypatch.setattr(scanner, "MAX_PRICE_USDT", 150)
        monkeypatch.setattr(scanner, "SENTIMENT_THRESHOLD", 0.55)
        monkeypatch.setattr(scanner, "MIN_OB_DEPTH_USDT", 50_000)

    def test_enabled_removes_illiquid_pair(self, monkeypatch):
        # LIQUIDUSDT: 20000 × 5.00 + 15000 × 5.01 = $175,150 → passes
        # ARIAUSDT:    200  × 1.04 +   150 × 1.06 = $367     → fails
        self._setup(monkeypatch)
        monkeypatch.setattr(scanner, "LIQUIDITY_CHECK_ENABLED", True)
        tickers = [
            _ticker("LIQUIDUSDT", bid_pr=5.00, ask_pr=5.01, bid_sz=20000, ask_sz=15000),
            _ticker("ARIAUSDT",   bid_pr=1.04, ask_pr=1.06, bid_sz=200,   ask_sz=150),
        ]
        monkeypatch.setattr(scanner.bc, "get_public", lambda *a, **kw: _mock_response(tickers))
        pairs, _ = scanner.get_qualified_pairs_and_sentiment()
        assert "LIQUIDUSDT" in pairs
        assert "ARIAUSDT" not in pairs

    def test_disabled_keeps_illiquid_pair(self, monkeypatch):
        self._setup(monkeypatch)
        monkeypatch.setattr(scanner, "LIQUIDITY_CHECK_ENABLED", False)
        tickers = [
            _ticker("LIQUIDUSDT", bid_pr=5.00, ask_pr=5.01, bid_sz=20000, ask_sz=15000),
            _ticker("ARIAUSDT",   bid_pr=1.04, ask_pr=1.06, bid_sz=200,   ask_sz=150),
        ]
        monkeypatch.setattr(scanner.bc, "get_public", lambda *a, **kw: _mock_response(tickers))
        pairs, _ = scanner.get_qualified_pairs_and_sentiment()
        assert "ARIAUSDT" in pairs

    def test_missing_bid_ask_sz_excluded(self, monkeypatch):
        """Pairs where ticker lacks bidSz/askSz get depth=0 and are excluded."""
        self._setup(monkeypatch)
        monkeypatch.setattr(scanner, "LIQUIDITY_CHECK_ENABLED", True)
        ticker_no_size = {
            "symbol":      "NOSIZUSDT",
            "quoteVolume": "10000000",
            "openUtc":     "4.95",
            "lastPr":      "5.0",
            "bidPr":       "4.99",
            "askPr":       "5.01",
            # bidSz and askSz intentionally absent
        }
        monkeypatch.setattr(scanner.bc, "get_public", lambda *a, **kw: _mock_response([ticker_no_size]))
        pairs, _ = scanner.get_qualified_pairs_and_sentiment()
        assert "NOSIZUSDT" not in pairs
```

- [ ] **Step 2: Run tests to confirm they fail (function not yet defined)**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot && python -m pytest tests/test_scanner_liquidity.py -v 2>&1 | head -40
```

Expected: `AttributeError: module 'scanner' has no attribute '_filter_by_liquidity'`

---

## Task 3: Implement `_filter_by_liquidity` helper

**Files:**
- Modify: `scanner.py` — add function before `get_qualified_pairs_and_sentiment` (after the `SentimentResult` dataclass, around line 38)

- [ ] **Step 1: Add `_filter_by_liquidity` to scanner.py**

Insert this function after the `SentimentResult` dataclass (after line 37) and before `def get_qualified_pairs_and_sentiment`:

```python
def _filter_by_liquidity(pairs: list[str], depth_map: dict[str, float]) -> list[str]:
    """
    Remove pairs whose top-of-book USDT depth is below MIN_OB_DEPTH_USDT.
    depth_map: symbol → bidSz×bidPr + askSz×askPr (from ticker loop).
    Conservative: symbols absent from depth_map treated as depth=0 (excluded).
    """
    liquid   = []
    excluded = []
    for sym in pairs:
        if depth_map.get(sym, 0.0) >= MIN_OB_DEPTH_USDT:
            liquid.append(sym)
        else:
            excluded.append(sym)
    if excluded:
        logger.info(
            f"Liquidity filter: removed {len(excluded)} illiquid pair(s): "
            + ", ".join(f"{s}(${depth_map.get(s, 0.0):.0f})" for s in excluded)
        )
    return liquid
```

- [ ] **Step 2: Run the unit tests (integration tests still fail — that's expected)**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot && python -m pytest tests/test_scanner_liquidity.py::TestFilterByLiquidity -v
```

Expected: all 5 `TestFilterByLiquidity` tests PASS.

- [ ] **Step 3: Commit**

```bash
git add scanner.py tests/test_scanner_liquidity.py && git commit -m "feat(scanner): add _filter_by_liquidity helper + unit tests"
```

---

## Task 4: Wire up depth_map and filter in `get_qualified_pairs_and_sentiment`

**Files:**
- Modify: `scanner.py:24` (import line), `scanner.py:56-62` (init), `scanner.py:85` (loop body), `scanner.py:130-136` (return block)

- [ ] **Step 1: Update the config import line (scanner.py line 24)**

Change:
```python
from config import MIN_VOLUME_USDT, MAX_PRICE_USDT, PRODUCT_TYPE, SENTIMENT_THRESHOLD
```

To:
```python
from config import (
    MIN_VOLUME_USDT, MAX_PRICE_USDT, PRODUCT_TYPE, SENTIMENT_THRESHOLD,
    LIQUIDITY_CHECK_ENABLED, MIN_OB_DEPTH_USDT,
)
```

- [ ] **Step 2: Add `depth_map` initialisation before the ticker loop**

In `get_qualified_pairs_and_sentiment`, after the existing variable initializations (after line 61 `btc_change = 0.0`), add:

```python
    depth_map   : dict[str, float] = {}
```

The block should look like:
```python
    qualified    : list[str] = []
    green_volume = 0.0
    red_volume   = 0.0
    green_count  = 0
    red_count    = 0
    btc_change   = 0.0          # ← NEW: for BTC veto
    depth_map   : dict[str, float] = {}
```

- [ ] **Step 3: Collect depth inside the ticker loop**

After line 85 (`qualified.append(symbol)`), add:

```python
        # Liquidity probe: top-of-book USDT depth from ticker fields
        try:
            bid_d = float(t.get("bidSz") or 0) * float(t.get("bidPr") or 0)
            ask_d = float(t.get("askSz") or 0) * float(t.get("askPr") or 0)
            depth_map[symbol] = bid_d + ask_d
        except (ValueError, TypeError):
            depth_map[symbol] = 0.0
```

- [ ] **Step 4: Call the filter before the return**

Replace lines 130–136 (the `logger.info` + `return` block) with:

```python
    logger.info(
        f"Scanner: {len(qualified)} pairs | "
        f"Sentiment: {direction} ({bullish_w*100:.1f}% green by vol×magnitude) | "
        f"🟢 {green_count}  🔴 {red_count} | "
        f"BTC={btc_change:+.1f}%"
    )
    if LIQUIDITY_CHECK_ENABLED:
        qualified = _filter_by_liquidity(qualified, depth_map)
    return qualified, sentiment
```

- [ ] **Step 5: Run all scanner liquidity tests**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot && python -m pytest tests/test_scanner_liquidity.py -v
```

Expected: all 8 tests PASS.

- [ ] **Step 6: Run the full test suite**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot && python -m pytest --tb=short 2>&1 | tail -20
```

Expected: all previously passing tests still pass. Fix any failures before continuing.

- [ ] **Step 7: Commit**

```bash
git add scanner.py config.py && git commit -m "feat(scanner): liquidity filter — exclude illiquid pairs from qualified list"
```

---

## Fallback: If `bidSz`/`askSz` absent from tickers (Task 1 check failed)

If the verification in Task 1 Step 1 showed `bidSz present: False`, replace the depth collection in Task 4 Step 3 with a per-pair merge-depth call instead:

```python
        # Liquidity probe: fetch top-of-book via merge-depth (limit=1)
        try:
            ob = bc.get_public(
                "/api/v2/mix/market/merge-depth",
                params={"symbol": symbol, "productType": PRODUCT_TYPE, "limit": "1"},
            )
            bids = ob.get("data", {}).get("bids", [])
            asks = ob.get("data", {}).get("asks", [])
            bid_d = float(bids[0][0]) * float(bids[0][1]) if bids else 0.0
            ask_d = float(asks[0][0]) * float(asks[0][1]) if asks else 0.0
            depth_map[symbol] = bid_d + ask_d
        except Exception:
            depth_map[symbol] = 0.0
```

Also update the test helper `_ticker` to not include `bidSz`/`askSz`, and add a separate mock for `bc.get_public` that returns different responses for the tickers vs merge-depth endpoints:

```python
def _make_get_public(ticker_response, depth_responses):
    """depth_responses: dict of symbol → merge-depth response data"""
    def _get_public(path, params=None, **kw):
        if "tickers" in path:
            return ticker_response
        symbol = (params or {}).get("symbol", "")
        bids = depth_responses.get(symbol, {}).get("bids", [])
        asks = depth_responses.get(symbol, {}).get("asks", [])
        return {"code": "00000", "data": {"bids": bids, "asks": asks}}
    return _get_public
```
