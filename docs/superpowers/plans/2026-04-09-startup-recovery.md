# Startup Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically detect and recover positions that filled from limit orders while the bot was stopped, restoring CSV entries, state.json, snapshots, and exchange TPSL orders.

**Architecture:** A new `startup_recovery.py` module provides three shared helpers (`fetch_candles_at`, `estimate_sl_tp`, `attempt_s5_recovery`). `Bot._startup_recovery()` in `bot.py` uses these helpers and is called once from `__init__` after Pass B. A `recover.py` CLI script uses the same helpers for manual recovery when the bot is already running.

**Tech Stack:** Python stdlib (csv, uuid, argparse), pandas, existing trader/state/snapshot/strategy modules, bitget_client for historical candle fetching with `endTime`.

---

## File Map

| File | Role |
|---|---|
| `startup_recovery.py` | NEW — three shared helper functions only; no side effects |
| `bot.py` | MODIFY — add `Bot._startup_recovery()` method; add one call in `__init__` after Pass B |
| `recover.py` | NEW — CLI script; uses helpers from `startup_recovery.py` and bot-module functions |
| `tests/test_startup_recovery.py` | NEW — tests for `Bot._startup_recovery()` and the helpers |
| `tests/test_recover_cli.py` | NEW — tests for `recover.py` CLI |

---

## Task 1: `startup_recovery.py` — `fetch_candles_at` helper

**Files:**
- Create: `startup_recovery.py`
- Create: `tests/test_startup_recovery.py`

- [ ] **Step 1.1: Write the failing test**

```python
# tests/test_startup_recovery.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pandas as pd
import pytest
from unittest.mock import patch

import startup_recovery as sr


class TestFetchCandlesAt:
    def test_returns_dataframe_with_correct_columns(self):
        """fetch_candles_at returns a sorted DataFrame when API returns rows."""
        fake_rows = [
            ["1744000800000", "9.3", "9.4", "9.2", "9.35", "100", "930"],
            ["1744000200000", "9.1", "9.2", "9.0", "9.15", "80",  "731"],
        ]
        fake_resp = {"data": fake_rows}

        with patch("startup_recovery.bc.get_public", return_value=fake_resp) as mock_get:
            df = sr.fetch_candles_at("LINKUSDT", "15m", limit=50, end_ms=1744001000000)

        assert not df.empty
        assert list(df.columns[:6]) == ["ts", "open", "high", "low", "close", "vol"]
        # sorted by ts ascending
        assert df.iloc[0]["ts"] < df.iloc[1]["ts"]
        # endTime param was passed
        call_params = mock_get.call_args[1]["params"]
        assert call_params["endTime"] == "1744001000000"
        assert call_params["limit"] == "50"

    def test_returns_empty_on_empty_response(self):
        """fetch_candles_at returns empty DataFrame when API returns no data."""
        with patch("startup_recovery.bc.get_public", return_value={"data": []}):
            df = sr.fetch_candles_at("LINKUSDT", "15m", limit=50, end_ms=1744001000000)
        assert df.empty

    def test_returns_empty_on_api_exception(self):
        """fetch_candles_at returns empty DataFrame (not exception) on API error."""
        with patch("startup_recovery.bc.get_public", side_effect=RuntimeError("API down")):
            df = sr.fetch_candles_at("LINKUSDT", "15m", limit=50, end_ms=1744001000000)
        assert df.empty
```

- [ ] **Step 1.2: Run test to confirm FAIL**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
pytest tests/test_startup_recovery.py::TestFetchCandlesAt -v
```

Expected: `ModuleNotFoundError: No module named 'startup_recovery'`

- [ ] **Step 1.3: Create `startup_recovery.py` with `fetch_candles_at`**

```python
# startup_recovery.py
"""
startup_recovery.py — Shared helpers for recovering positions that filled
while the bot was stopped (crashed-before-log scenario).

Used by:
  - Bot._startup_recovery() in bot.py (automatic, at startup)
  - recover.py CLI (manual, when bot is already running)
"""
import logging
import pandas as pd

import bitget_client as bc
from config import PRODUCT_TYPE

logger = logging.getLogger(__name__)


def fetch_candles_at(symbol: str, interval: str, limit: int, end_ms: int) -> pd.DataFrame:
    """
    Fetch up to `limit` candles ending at `end_ms` (epoch milliseconds).
    Uses Bitget's endTime query param — not exposed by trader.get_candles().
    Returns empty DataFrame on error or no data.
    """
    try:
        data = bc.get_public(
            "/api/v2/mix/market/candles",
            params={
                "symbol":      symbol,
                "productType": PRODUCT_TYPE,
                "granularity": interval,
                "limit":       str(limit),
                "endTime":     str(end_ms),
            },
        )
        rows = data.get("data", [])
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(
            rows,
            columns=["ts", "open", "high", "low", "close", "vol", "quote_vol"],
        )
        df[["open", "high", "low", "close", "vol"]] = (
            df[["open", "high", "low", "close", "vol"]].astype(float)
        )
        df["ts"] = df["ts"].astype(int)
        return df.sort_values("ts").reset_index(drop=True)
    except Exception as e:
        logger.warning(f"[{symbol}] fetch_candles_at error: {e}")
        return pd.DataFrame()
```

- [ ] **Step 1.4: Run tests to confirm PASS**

```bash
pytest tests/test_startup_recovery.py::TestFetchCandlesAt -v
```

Expected: 3 passed

- [ ] **Step 1.5: Commit**

```bash
git add startup_recovery.py tests/test_startup_recovery.py
git commit -m "feat(recovery): add fetch_candles_at helper with tests"
```

---

## Task 2: `startup_recovery.py` — `estimate_sl_tp` helper

**Files:**
- Modify: `startup_recovery.py`
- Modify: `tests/test_startup_recovery.py`

- [ ] **Step 2.1: Add failing tests**

Append to `tests/test_startup_recovery.py`:

```python
class TestEstimateSlTp:
    def test_short_sl_above_entry(self):
        """SHORT: SL is 5% above entry."""
        sl, tp, ob_low, ob_high = sr.estimate_sl_tp(10.0, "SHORT")
        assert sl == pytest.approx(10.5, rel=1e-6)

    def test_short_tp_below_entry(self):
        """SHORT: TP is 10% below entry."""
        sl, tp, ob_low, ob_high = sr.estimate_sl_tp(10.0, "SHORT")
        assert tp == pytest.approx(9.0, rel=1e-6)

    def test_short_ob_band(self):
        """SHORT: ob_high == entry, ob_low == entry * 0.99."""
        sl, tp, ob_low, ob_high = sr.estimate_sl_tp(10.0, "SHORT")
        assert ob_high == pytest.approx(10.0, rel=1e-6)
        assert ob_low  == pytest.approx(9.9,  rel=1e-6)

    def test_long_sl_below_entry(self):
        """LONG: SL is 5% below entry."""
        sl, tp, ob_low, ob_high = sr.estimate_sl_tp(10.0, "LONG")
        assert sl == pytest.approx(9.5, rel=1e-6)

    def test_long_tp_above_entry(self):
        """LONG: TP is 10% above entry."""
        sl, tp, ob_low, ob_high = sr.estimate_sl_tp(10.0, "LONG")
        assert tp == pytest.approx(11.0, rel=1e-6)

    def test_long_ob_band(self):
        """LONG: ob_low == entry, ob_high == entry * 1.01."""
        sl, tp, ob_low, ob_high = sr.estimate_sl_tp(10.0, "LONG")
        assert ob_low  == pytest.approx(10.0,  rel=1e-6)
        assert ob_high == pytest.approx(10.1,  rel=1e-6)
```

- [ ] **Step 2.2: Run to confirm FAIL**

```bash
pytest tests/test_startup_recovery.py::TestEstimateSlTp -v
```

Expected: `AttributeError: module 'startup_recovery' has no attribute 'estimate_sl_tp'`

- [ ] **Step 2.3: Add `estimate_sl_tp` to `startup_recovery.py`**

Add after `fetch_candles_at`:

```python
def estimate_sl_tp(
    entry: float, side: str
) -> tuple[float, float, float, float]:
    """
    Fallback SL/TP estimation when original signal data is unavailable.
    Returns (sl, tp, ob_low, ob_high).

    SHORT: SL = entry * 1.05  |  TP = entry * 0.90  |  OB ≈ entry band
    LONG:  SL = entry * 0.95  |  TP = entry * 1.10  |  OB ≈ entry band
    """
    if side == "SHORT":
        sl      = round(entry * 1.05, 8)
        tp      = round(entry * 0.90, 8)
        ob_high = round(entry,        8)
        ob_low  = round(entry * 0.99, 8)
    else:  # LONG
        sl      = round(entry * 0.95, 8)
        tp      = round(entry * 1.10, 8)
        ob_high = round(entry * 1.01, 8)
        ob_low  = round(entry,        8)
    return sl, tp, ob_low, ob_high
```

- [ ] **Step 2.4: Run tests to confirm PASS**

```bash
pytest tests/test_startup_recovery.py::TestEstimateSlTp -v
```

Expected: 6 passed

- [ ] **Step 2.5: Commit**

```bash
git add startup_recovery.py tests/test_startup_recovery.py
git commit -m "feat(recovery): add estimate_sl_tp helper with tests"
```

---

## Task 3: `startup_recovery.py` — `attempt_s5_recovery` helper

**Files:**
- Modify: `startup_recovery.py`
- Modify: `tests/test_startup_recovery.py`

- [ ] **Step 3.1: Add failing tests**

Append to `tests/test_startup_recovery.py`:

```python
class TestAttemptS5Recovery:
    """Tests for attempt_s5_recovery — mocks evaluate_s5 to avoid exchange calls."""

    def _make_df(self):
        import numpy as np
        import time
        n = 60
        now_ms = int(time.time() * 1000)
        ts = [now_ms - i * 900_000 for i in range(n, 0, -1)]
        c = np.linspace(9.0, 10.0, n)
        return pd.DataFrame({
            "ts": ts, "open": c - 0.05, "high": c + 0.1,
            "low": c - 0.1, "close": c, "vol": [1000.0] * n,
        })

    def test_returns_values_when_evaluate_s5_finds_signal(self):
        """Returns (sl, tp, ob_low, ob_high) when evaluate_s5 returns PENDING_SHORT."""
        df = self._make_df()
        mock_result = ("PENDING_SHORT", 9.5, 9.9, 8.5, 9.4, 9.5, "OB found")

        with patch("startup_recovery.evaluate_s5", return_value=mock_result):
            result = sr.attempt_s5_recovery("LINKUSDT", df, df, df, "SHORT")

        assert result is not None
        sl, tp, ob_low, ob_high = result
        assert sl      == pytest.approx(9.9, rel=1e-6)
        assert tp      == pytest.approx(8.5, rel=1e-6)
        assert ob_low  == pytest.approx(9.4, rel=1e-6)
        assert ob_high == pytest.approx(9.5, rel=1e-6)

    def test_returns_none_when_evaluate_s5_returns_hold(self):
        """Returns None when evaluate_s5 returns HOLD (no usable signal)."""
        df = self._make_df()
        mock_result = ("HOLD", 0.0, 0.0, 0.0, 0.0, 0.0, "Not enough candles")

        with patch("startup_recovery.evaluate_s5", return_value=mock_result):
            result = sr.attempt_s5_recovery("LINKUSDT", df, df, df, "SHORT")

        assert result is None

    def test_returns_none_when_evaluate_s5_raises(self):
        """Returns None (not exception) when evaluate_s5 raises."""
        df = self._make_df()

        with patch("startup_recovery.evaluate_s5", side_effect=RuntimeError("crash")):
            result = sr.attempt_s5_recovery("LINKUSDT", df, df, df, "SHORT")

        assert result is None

    def test_long_side_accepted(self):
        """Returns values for LONG side when evaluate_s5 returns PENDING_LONG."""
        df = self._make_df()
        mock_result = ("PENDING_LONG", 9.5, 9.1, 10.5, 9.4, 9.6, "OB found")

        with patch("startup_recovery.evaluate_s5", return_value=mock_result):
            result = sr.attempt_s5_recovery("LINKUSDT", df, df, df, "LONG")

        assert result is not None
```

- [ ] **Step 3.2: Run to confirm FAIL**

```bash
pytest tests/test_startup_recovery.py::TestAttemptS5Recovery -v
```

Expected: `AttributeError: module 'startup_recovery' has no attribute 'attempt_s5_recovery'`

- [ ] **Step 3.3: Add `attempt_s5_recovery` to `startup_recovery.py`**

Add the import at the top of `startup_recovery.py` (after existing imports):

```python
from strategy import evaluate_s5
```

Then add the function after `estimate_sl_tp`:

```python
def attempt_s5_recovery(
    symbol: str,
    m15_df: pd.DataFrame,
    htf_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    side: str,
) -> tuple[float, float, float, float] | None:
    """
    Run evaluate_s5() on historical candles to recover OB/SL/TP.
    Returns (sl, tp, ob_low, ob_high) if a usable signal is found, else None.

    `side` must be 'SHORT' or 'LONG' — only the matching signal direction is
    accepted (prevents using a stale opposing OB as SL/TP).
    """
    try:
        _accepted = ("PENDING_SHORT", "SHORT") if side == "SHORT" else ("PENDING_LONG", "LONG")
        sig, _trigger, sl, tp, ob_low, ob_high, reason = evaluate_s5(
            symbol, daily_df, htf_df, m15_df, allowed_direction=side,
        )
        if sig in _accepted and sl > 0:
            logger.info(
                f"[{symbol}] attempt_s5_recovery: found OB | "
                f"sl={sl:.5f} tp={tp:.5f} | {reason[:60]}"
            )
            return round(sl, 8), round(tp, 8), round(ob_low, 8), round(ob_high, 8)
        logger.debug(f"[{symbol}] attempt_s5_recovery: sig={sig} — no usable signal")
        return None
    except Exception as e:
        logger.warning(f"[{symbol}] attempt_s5_recovery error: {e}")
        return None
```

- [ ] **Step 3.4: Run tests to confirm PASS**

```bash
pytest tests/test_startup_recovery.py -v
```

Expected: all tests in the file pass (TestFetchCandlesAt + TestEstimateSlTp + TestAttemptS5Recovery)

- [ ] **Step 3.5: Commit**

```bash
git add startup_recovery.py tests/test_startup_recovery.py
git commit -m "feat(recovery): add attempt_s5_recovery helper with tests"
```

---

## Task 4: `Bot._startup_recovery()` — happy path (Pass 1)

**Files:**
- Modify: `bot.py` (add method)
- Modify: `tests/test_startup_recovery.py`

- [ ] **Step 4.1: Add failing tests**

Append to `tests/test_startup_recovery.py`:

```python
import threading
import bot
import state as st


def _make_bot(monkeypatch) -> bot.MTFBot:
    """Return a minimal MTFBot bypassing __init__."""
    b = object.__new__(bot.MTFBot)
    b.pending_signals = {}
    b.active_positions = {}
    b._trade_lock = threading.Lock()
    b.running = True
    b.sentiment = type("S", (), {"direction": "BEARISH"})()
    monkeypatch.setattr(bot.st, "add_scan_log", lambda *a, **kw: None)
    monkeypatch.setattr(bot.st, "save_pending_signals", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "PAPER_MODE", False)
    return b


def _make_sig(order_id="ORD001", side="SHORT"):
    import time
    return {
        "strategy": "S5", "side": side,
        "trigger":  9.311, "sl": 9.777, "tp": 8.566,
        "ob_low":   9.22,  "ob_high": 9.311,
        "qty_str":  "14.0", "rr": 2.5,
        "sentiment": "BEARISH",
        "expires":  time.time() + 7200,
        "order_id": order_id,
    }


SYM = "LINKUSDT"


class TestStartupRecoveryHappyPath:
    def test_calls_handle_limit_filled_when_order_filled(self, monkeypatch):
        """Happy path: pending signal + filled order → _handle_limit_filled called."""
        b = _make_bot(monkeypatch)
        sig = _make_sig()
        b.pending_signals[SYM] = sig

        # No CSV row for this symbol
        monkeypatch.setattr(bot, "_get_open_csv_row", lambda path, sym: None)
        monkeypatch.setattr(
            bot.tr, "get_order_fill",
            lambda sym, oid: {"status": "filled", "fill_price": 9.31},
        )
        monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)

        handled = []
        monkeypatch.setattr(
            b, "_handle_limit_filled",
            lambda sym, s, fp, bal: handled.append((sym, fp, bal)),
        )

        existing = {SYM: {"side": "SHORT", "entry_price": 9.311, "qty": 14.0,
                          "margin": 13.05, "leverage": 10,
                          "unrealised_pnl": 1.5, "mark_price": 9.0}}
        b._startup_recovery(existing)

        assert len(handled) == 1
        assert handled[0] == (SYM, 9.31, 1000.0)
        assert SYM not in b.pending_signals

    def test_skips_when_order_still_live(self, monkeypatch):
        """Happy path: order still live → _handle_limit_filled NOT called, signal kept."""
        b = _make_bot(monkeypatch)
        b.pending_signals[SYM] = _make_sig()

        monkeypatch.setattr(bot, "_get_open_csv_row", lambda path, sym: None)
        monkeypatch.setattr(
            bot.tr, "get_order_fill",
            lambda sym, oid: {"status": "live", "fill_price": 0.0},
        )
        monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)

        handled = []
        monkeypatch.setattr(
            b, "_handle_limit_filled",
            lambda sym, s, fp, bal: handled.append(sym),
        )

        existing = {SYM: {"side": "SHORT", "entry_price": 9.311, "qty": 14.0,
                          "margin": 13.05, "leverage": 10,
                          "unrealised_pnl": 1.5, "mark_price": 9.0}}
        b._startup_recovery(existing)

        assert len(handled) == 0
        assert SYM in b.pending_signals

    def test_skips_symbol_with_existing_csv_row(self, monkeypatch):
        """If CSV already has an open row, symbol is skipped entirely."""
        b = _make_bot(monkeypatch)
        b.pending_signals[SYM] = _make_sig()

        # CSV row exists → skip
        monkeypatch.setattr(bot, "_get_open_csv_row", lambda path, sym: {"qty": "14"})
        monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)

        handled = []
        monkeypatch.setattr(
            b, "_handle_limit_filled",
            lambda sym, s, fp, bal: handled.append(sym),
        )

        existing = {SYM: {"side": "SHORT", "entry_price": 9.311, "qty": 14.0,
                          "margin": 13.05, "leverage": 10,
                          "unrealised_pnl": 1.5, "mark_price": 9.0}}
        b._startup_recovery(existing)

        assert len(handled) == 0
```

- [ ] **Step 4.2: Run to confirm FAIL**

```bash
pytest tests/test_startup_recovery.py::TestStartupRecoveryHappyPath -v
```

Expected: `AttributeError: 'MTFBot' object has no attribute '_startup_recovery'`

- [ ] **Step 4.3: Add `_startup_recovery` to `bot.py` — happy path only**

Find the closing `except Exception as e: logger.error(f"Startup sync error: {e}")` block (around line 462) and the `def stop(self, *_):` that follows it. Add the new method between them. The method skeleton with Pass 1 happy path:

```python
    def _startup_recovery(self, existing: dict) -> None:
        """
        Detect and recover positions that filled while the bot was stopped.

        Pass 1: positions on exchange with no CSV open row.
        Pass 2: pending signals whose limit orders filled AND closed while down.

        Called once from __init__ after Pass B. PAPER_MODE: skip (no limit orders).
        """
        from startup_recovery import fetch_candles_at, estimate_sl_tp, attempt_s5_recovery

        try:
            balance = tr.get_usdt_balance()
        except Exception as _e:
            logger.warning(f"Startup recovery: could not fetch balance: {_e}")
            balance = 0.0

        recovered = 0

        # ── Pass 1: open positions with no CSV record ──────────────── #
        for sym, pos in existing.items():
            try:
                if _get_open_csv_row(config.TRADE_LOG, sym) is not None:
                    continue  # CSV exists — handled by Pass A/B

                sig = self.pending_signals.get(sym)

                if sig and sig.get("order_id") and sig["order_id"] != "PAPER":
                    # Happy path — original signal data available
                    try:
                        fill_info = tr.get_order_fill(sym, sig["order_id"])
                    except Exception as _e:
                        logger.warning(
                            f"[S5][{sym}] ⚠️ Startup recovery: get_order_fill failed: {_e}"
                        )
                        continue

                    if fill_info["status"] == "live":
                        continue  # still pending — not a crash case

                    if fill_info["status"] == "filled":
                        fill_price = fill_info["fill_price"]
                        logger.warning(
                            f"[S5][{sym}] ⚠️ Startup recovery [happy]: limit filled while "
                            f"bot was down @ {fill_price:.5f} — running _handle_limit_filled"
                        )
                        st.add_scan_log(
                            f"[S5][{sym}] ⚠️ Recovery [happy]: filled @ {fill_price:.5f}", "WARN"
                        )
                        self._handle_limit_filled(sym, sig, fill_price, balance)
                        self.pending_signals.pop(sym, None)
                        st.save_pending_signals(self.pending_signals)
                        recovered += 1

                else:
                    pass  # sad path — implemented in Task 5

            except Exception as _e:
                logger.warning(f"[{sym}] Startup recovery error: {_e}")

        # Pass 2 implemented in Task 6

        if recovered:
            _rebuild_stats_from_csv(config.TRADE_LOG)
            st.add_scan_log(
                f"⚠️ Startup recovery: {recovered} position(s) recovered", "WARN"
            )
            logger.warning(f"Startup recovery: {recovered} position(s) recovered")
```

- [ ] **Step 4.4: Run tests to confirm PASS**

```bash
pytest tests/test_startup_recovery.py::TestStartupRecoveryHappyPath -v
```

Expected: 3 passed

- [ ] **Step 4.5: Commit**

```bash
git add bot.py tests/test_startup_recovery.py
git commit -m "feat(recovery): Bot._startup_recovery happy path with tests"
```

---

## Task 5: `Bot._startup_recovery()` — sad path (Pass 1)

**Files:**
- Modify: `bot.py` (fill in sad path)
- Modify: `tests/test_startup_recovery.py`

- [ ] **Step 5.1: Add failing tests**

Append to `tests/test_startup_recovery.py`:

```python
class TestStartupRecoverySadPath:
    def _existing(self, entry=9.311, side="SHORT"):
        return {SYM: {
            "side": side, "entry_price": entry,
            "qty": 14.0, "margin": 13.05, "leverage": 10,
            "unrealised_pnl": 1.5, "mark_price": 9.0,
        }}

    def test_sad_path_patches_state_and_logs_csv(self, monkeypatch, tmp_path):
        """No pending signal → state patched with tpsl_set=False; CSV row appended."""
        import csv, json

        b = _make_bot(monkeypatch)
        # No pending signal for SYM

        monkeypatch.setattr(bot, "_get_open_csv_row", lambda path, sym: None)
        monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)
        monkeypatch.setattr(bot.tr, "get_candles", lambda sym, i, limit=100: pd.DataFrame())

        # fetch_candles_at returns empty — forces fallback estimate
        with patch("bot.fetch_candles_at", return_value=pd.DataFrame()):
            pass  # will be patched via startup_recovery import

        import startup_recovery
        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())

        # Fake state.json with the UNKNOWN position
        state_data = {
            "open_trades": [{
                "symbol": SYM, "side": "SHORT", "qty": 14.0,
                "entry": 9.311, "sl": "?", "tp": "?",
                "strategy": "UNKNOWN", "trade_id": "",
                "opened_at": "2026-04-08T09:05:59+00:00",
                "margin": 13.05, "leverage": 10,
                "unrealised_pnl": 1.5, "mark_price": 9.0,
            }],
            "pending_signals": {}, "position_memory": {},
        }
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(state_data))
        monkeypatch.setattr(bot.st, "_STATE_FILE", str(state_file))
        monkeypatch.setattr(bot.st, "_read",
                            lambda: json.loads(state_file.read_text()))
        monkeypatch.setattr(bot.st, "_write",
                            lambda s: state_file.write_text(json.dumps(s)))

        csv_file = tmp_path / "trades.csv"
        logged = []
        monkeypatch.setattr(bot, "_log_trade",
                            lambda action, details: logged.append((action, details)))
        monkeypatch.setattr(bot, "_rebuild_stats_from_csv", lambda *a: None)
        monkeypatch.setattr(bot.snapshot, "save_snapshot", lambda **kw: None)
        monkeypatch.setattr(bot.st, "get_open_trade", lambda sym: state_data["open_trades"][0])

        b.active_positions[SYM] = {
            "side": "SHORT", "strategy": "UNKNOWN", "sl": "?",
            "box_high": 0, "box_low": 0, "trade_id": "",
            "opened_at": "2026-04-08T09:05:59+00:00",
        }

        b._startup_recovery(self._existing())

        # CSV row was appended
        assert len(logged) == 1
        action, details = logged[0]
        assert action == "UNKNOWN_SHORT"
        assert details["tpsl_set"] is False
        assert float(details["sl"]) > 9.311  # SL above entry for SHORT

        # trade_id was generated
        assert details["trade_id"] != ""

    def test_sad_path_skips_snapshot_on_empty_candles(self, monkeypatch, tmp_path):
        """Empty candle response → snapshot.save_snapshot NOT called; no crash."""
        import json
        b = _make_bot(monkeypatch)

        monkeypatch.setattr(bot, "_get_open_csv_row", lambda path, sym: None)
        monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)
        monkeypatch.setattr(bot.tr, "get_candles", lambda sym, i, limit=100: pd.DataFrame())

        import startup_recovery
        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())

        state_data = {
            "open_trades": [{
                "symbol": SYM, "side": "SHORT", "qty": 14.0,
                "entry": 9.311, "sl": "?", "tp": "?",
                "strategy": "UNKNOWN", "trade_id": "",
                "opened_at": "2026-04-08T09:05:59+00:00",
                "margin": 13.05, "leverage": 10,
                "unrealised_pnl": 1.5, "mark_price": 9.0,
            }],
            "pending_signals": {}, "position_memory": {},
        }
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(state_data))
        monkeypatch.setattr(bot.st, "_read",
                            lambda: json.loads(state_file.read_text()))
        monkeypatch.setattr(bot.st, "_write",
                            lambda s: state_file.write_text(json.dumps(s)))
        monkeypatch.setattr(bot.st, "get_open_trade", lambda sym: state_data["open_trades"][0])
        monkeypatch.setattr(bot, "_log_trade", lambda *a, **kw: None)
        monkeypatch.setattr(bot, "_rebuild_stats_from_csv", lambda *a: None)

        snap_calls = []
        monkeypatch.setattr(bot.snapshot, "save_snapshot",
                            lambda **kw: snap_calls.append(kw))

        b.active_positions[SYM] = {
            "side": "SHORT", "strategy": "UNKNOWN",
            "sl": "?", "box_high": 0, "box_low": 0, "trade_id": "",
            "opened_at": "2026-04-08T09:05:59+00:00",
        }

        b._startup_recovery(self._existing())  # should not raise

        assert snap_calls == [], "snapshot.save_snapshot must NOT be called with empty candles"

    def test_error_in_sad_path_does_not_crash(self, monkeypatch):
        """Exception during sad path recovery is caught; _startup_recovery returns normally."""
        b = _make_bot(monkeypatch)
        monkeypatch.setattr(bot, "_get_open_csv_row", lambda path, sym: None)
        monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)
        # st.get_open_trade raises — simulates unexpected state
        monkeypatch.setattr(bot.st, "get_open_trade",
                            lambda sym: (_ for _ in ()).throw(RuntimeError("boom")))

        import startup_recovery
        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())
        monkeypatch.setattr(bot.tr, "get_candles", lambda sym, i, limit=100: pd.DataFrame())
        monkeypatch.setattr(bot, "_rebuild_stats_from_csv", lambda *a: None)

        b.active_positions[SYM] = {
            "side": "SHORT", "strategy": "UNKNOWN",
            "sl": "?", "box_high": 0, "box_low": 0, "trade_id": "",
        }

        # Should complete without raising
        b._startup_recovery({SYM: {"side": "SHORT", "entry_price": 9.311,
                                    "qty": 14.0, "margin": 13.05, "leverage": 10,
                                    "unrealised_pnl": 1.5, "mark_price": 9.0}})
```

- [ ] **Step 5.2: Run to confirm FAIL**

```bash
pytest tests/test_startup_recovery.py::TestStartupRecoverySadPath -v
```

Expected: tests fail (sad path `pass` placeholder does nothing).

- [ ] **Step 5.3: Implement the sad path in `bot.py` — replace the `else: pass` block**

Replace `else: pass  # sad path — implemented in Task 5` with:

```python
                else:
                    # Sad path — no pending signal; estimate SL/TP from entry
                    _ot       = st.get_open_trade(sym)
                    entry     = float(pos.get("entry_price") or pos.get("entry", 0))
                    side      = pos.get("side", "SHORT")
                    margin    = float(pos.get("margin", 0))
                    leverage  = int(float(pos.get("leverage") or 10))
                    qty       = float(pos.get("qty", 0))
                    opened_at = (
                        (_ot or {}).get("opened_at")
                        or datetime.now(timezone.utc).isoformat()
                    )
                    trade_id  = uuid.uuid4().hex[:8]

                    # Parse opened_at → end_ms for Bitget historical candle fetch
                    try:
                        end_ms = int(
                            datetime.fromisoformat(opened_at).timestamp() * 1000
                        ) + 60_000
                    except Exception:
                        end_ms = int(time.time() * 1000)

                    # Fetch historical candles at fill time
                    m15_df   = fetch_candles_at(sym, "15m", limit=100, end_ms=end_ms)
                    htf_df   = fetch_candles_at(sym, "1H",  limit=50,  end_ms=end_ms)
                    daily_df = tr.get_candles(sym, "1D", limit=60)

                    # Try to recover OB/SL/TP from historical candles
                    result = None
                    if not m15_df.empty and not htf_df.empty and not daily_df.empty:
                        result = attempt_s5_recovery(sym, m15_df, htf_df, daily_df, side)

                    if result:
                        sl, tp, ob_low, ob_high = result
                    else:
                        sl, tp, ob_low, ob_high = estimate_sl_tp(entry, side)

                    # Patch active_positions
                    if sym in self.active_positions:
                        self.active_positions[sym].update({
                            "strategy": "UNKNOWN",
                            "sl":       sl,
                            "box_high": ob_high,
                            "box_low":  ob_low,
                            "trade_id": trade_id,
                        })

                    # Patch state.json open_trades entry
                    _s = st._read()
                    for _t in _s["open_trades"]:
                        if _t["symbol"] == sym:
                            _t.update({
                                "trade_id": trade_id,
                                "sl":       round(sl, 8),
                                "tp":       round(tp, 8),
                                "box_high": round(ob_high, 8),
                                "box_low":  round(ob_low,  8),
                                "tpsl_set": False,
                            })
                            break
                    st._write(_s)

                    # Append open row to trades.csv
                    _log_trade(f"UNKNOWN_{side}", {
                        "trade_id":        trade_id,
                        "symbol":          sym,
                        "side":            side,
                        "qty":             qty,
                        "entry":           entry,
                        "sl":              round(sl,      8),
                        "tp":              round(tp,      8),
                        "box_low":         round(ob_low,  8),
                        "box_high":        round(ob_high, 8),
                        "leverage":        leverage,
                        "margin":          round(margin,  8),
                        "tpsl_set":        False,
                        "strategy":        "UNKNOWN",
                        "snap_s5_ob_low":  round(ob_low,  8),
                        "snap_s5_ob_high": round(ob_high, 8),
                        "snap_s5_tp":      round(tp,      8),
                    })

                    # Save open snapshot (only if candles available)
                    if not m15_df.empty:
                        snapshot.save_snapshot(
                            trade_id=trade_id,
                            event="open",
                            symbol=sym,
                            interval="15m",
                            candles=_df_to_candles(m15_df),
                            event_price=entry,
                            captured_at=opened_at,
                        )

                    logger.warning(
                        f"[UNKNOWN][{sym}] ⚠️ Startup recovery [sad]: no signal data | "
                        f"SL≈{sl:.5f} | tpsl_set=False — manual TPSL needed"
                    )
                    st.add_scan_log(
                        f"[UNKNOWN][{sym}] ⚠️ Recovery [sad]: tpsl_set=False — manual TPSL needed",
                        "WARN",
                    )
                    recovered += 1
```

Also add the missing imports at the top of `bot.py` where other imports are (line 11–12 area). The method uses `uuid` (already imported), `datetime`/`timezone` (already imported), `fetch_candles_at`, `estimate_sl_tp`, `attempt_s5_recovery` (imported locally inside the method).

- [ ] **Step 5.4: Run tests to confirm PASS**

```bash
pytest tests/test_startup_recovery.py -v
```

Expected: all passing (happy path + sad path tests)

- [ ] **Step 5.5: Commit**

```bash
git add bot.py tests/test_startup_recovery.py
git commit -m "feat(recovery): sad path — estimate SL/TP, patch state/CSV/snapshot"
```

---

## Task 6: `Bot._startup_recovery()` — Pass 2 (opened and closed while down)

**Files:**
- Modify: `bot.py`
- Modify: `tests/test_startup_recovery.py`

- [ ] **Step 6.1: Add failing tests**

Append to `tests/test_startup_recovery.py`:

```python
class TestStartupRecoveryPass2:
    def test_logs_open_and_close_for_filled_and_closed_signal(self, monkeypatch):
        """Pass 2: signal filled + position not in active_positions → open+close CSV rows."""
        b = _make_bot(monkeypatch)
        sig = _make_sig(order_id="ORD123", side="SHORT")
        b.pending_signals[SYM] = sig
        # SYM is NOT in active_positions (position already closed)

        monkeypatch.setattr(bot, "_get_open_csv_row", lambda path, sym: None)
        monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)
        monkeypatch.setattr(
            bot.tr, "get_order_fill",
            lambda sym, oid: {"status": "filled", "fill_price": 9.31},
        )
        monkeypatch.setattr(
            bot.tr, "get_history_position",
            lambda sym, **kw: {"pnl": 2.5, "exit_price": 8.5, "close_time": "2026-04-08T12:00:00"},
        )
        monkeypatch.setattr(bot, "_rebuild_stats_from_csv", lambda *a: None)

        logged = []
        monkeypatch.setattr(bot, "_log_trade",
                            lambda action, details: logged.append((action, details)))

        # existing has NO SYM (it's already closed on exchange)
        b._startup_recovery({})

        actions = [a for a, _ in logged]
        assert "S5_SHORT" in actions, "open row must be logged"
        assert "S5_CLOSE" in actions, "close row must be logged"
        assert SYM not in b.pending_signals

    def test_skips_non_s5_signals_in_pass2(self, monkeypatch):
        """Pass 2 skips signals without order_id (non-S5) — no get_order_fill called."""
        b = _make_bot(monkeypatch)
        b.pending_signals[SYM] = {
            "strategy": "S3", "side": "LONG", "trigger": 9.0,
            "sl": 8.5, "expires": 9999999999,
            # No order_id
        }

        monkeypatch.setattr(bot, "_get_open_csv_row", lambda path, sym: None)
        monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)
        monkeypatch.setattr(bot, "_rebuild_stats_from_csv", lambda *a: None)

        fill_calls = []
        monkeypatch.setattr(
            bot.tr, "get_order_fill",
            lambda sym, oid: fill_calls.append(oid) or {"status": "live", "fill_price": 0.0},
        )

        b._startup_recovery({})

        assert fill_calls == [], "get_order_fill must NOT be called for non-S5 signals"
        assert SYM in b.pending_signals  # signal untouched

    def test_skips_paper_order_id_in_pass2(self, monkeypatch):
        """Pass 2 skips signals with order_id='PAPER'."""
        b = _make_bot(monkeypatch)
        b.pending_signals[SYM] = _make_sig(order_id="PAPER")

        monkeypatch.setattr(bot, "_get_open_csv_row", lambda path, sym: None)
        monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)
        monkeypatch.setattr(bot, "_rebuild_stats_from_csv", lambda *a: None)

        fill_calls = []
        monkeypatch.setattr(
            bot.tr, "get_order_fill",
            lambda sym, oid: fill_calls.append(oid) or {"status": "live", "fill_price": 0.0},
        )

        b._startup_recovery({})

        assert fill_calls == []
```

- [ ] **Step 6.2: Run to confirm FAIL**

```bash
pytest tests/test_startup_recovery.py::TestStartupRecoveryPass2 -v
```

Expected: tests fail (Pass 2 not yet implemented — currently `# Pass 2 implemented in Task 6` comment).

- [ ] **Step 6.3: Add Pass 2 to `bot.py` — replace the placeholder comment**

Replace `# Pass 2 implemented in Task 6` with:

```python
        # ── Pass 2: pending signals whose limit filled AND position closed while down ── #
        for sym, sig in list(self.pending_signals.items()):
            try:
                if sym in self.active_positions:
                    continue  # already handled in Pass 1 or normal startup

                order_id = sig.get("order_id")
                if not order_id or order_id == "PAPER":
                    continue  # non-S5 or paper-mode signal — skip

                try:
                    fill_info = tr.get_order_fill(sym, order_id)
                except Exception as _e:
                    logger.warning(f"[S5][{sym}] ⚠️ Pass 2 get_order_fill failed: {_e}")
                    continue

                if fill_info["status"] != "filled":
                    continue

                fill_price = fill_info["fill_price"]
                fill_time  = datetime.now(timezone.utc).isoformat()
                trade_id   = uuid.uuid4().hex[:8]
                side       = sig.get("side", "SHORT")

                # Log open row
                _log_trade(f"S5_{side}", {
                    "trade_id":           trade_id,
                    "symbol":             sym,
                    "side":               side,
                    "entry":              fill_price,
                    "sl":                 round(sig.get("sl", 0), 8),
                    "tp":                 round(sig.get("tp", 0), 8),
                    "box_low":            round(sig.get("ob_low",  0), 8),
                    "box_high":           round(sig.get("ob_high", 0), 8),
                    "leverage":           config_s5.S5_LEVERAGE,
                    "tpsl_set":           False,
                    "strategy":           "S5",
                    "snap_entry_trigger": round(sig.get("trigger", fill_price), 8),
                    "snap_sl":            round(sig.get("sl", 0), 8),
                    "snap_rr":            sig.get("rr"),
                    "snap_sentiment":     sig.get("sentiment", "?"),
                    "snap_s5_ob_low":     round(sig.get("ob_low",  0), 8),
                    "snap_s5_ob_high":    round(sig.get("ob_high", 0), 8),
                    "snap_s5_tp":         round(sig.get("tp", 0), 8),
                })

                # Fetch and log close info
                hist = None
                try:
                    hist = tr.get_history_position(
                        sym,
                        open_time_iso=fill_time,
                        entry_price=fill_price,
                    )
                except Exception as _e:
                    logger.warning(f"[S5][{sym}] Pass 2: get_history_position failed: {_e}")

                if hist:
                    close_pnl  = round(hist["pnl"], 4)
                    exit_price = hist.get("exit_price")
                    _log_trade("S5_CLOSE", {
                        "trade_id":    trade_id,
                        "symbol":      sym,
                        "side":        side,
                        "pnl":         close_pnl,
                        "result":      "WIN" if close_pnl >= 0 else "LOSS",
                        "exit_reason": "RECONCILED",
                        "exit_price":  round(exit_price, 8) if exit_price else None,
                    })
                    logger.warning(
                        f"[S5][{sym}] ⚠️ Pass 2: opened+closed while down | "
                        f"fill={fill_price:.5f} | PnL={close_pnl:+.4f}"
                    )
                else:
                    logger.warning(
                        f"[S5][{sym}] ⚠️ Pass 2: opened while down, close history unavailable"
                    )

                self.pending_signals.pop(sym, None)
                st.save_pending_signals(self.pending_signals)
                recovered += 1

            except Exception as _e:
                logger.warning(f"[S5][{sym}] Pass 2 recovery error: {_e}")
```

- [ ] **Step 6.4: Run tests to confirm PASS**

```bash
pytest tests/test_startup_recovery.py -v
```

Expected: all tests in file pass.

- [ ] **Step 6.5: Commit**

```bash
git add bot.py tests/test_startup_recovery.py
git commit -m "feat(recovery): Pass 2 — log open+close for filled-then-closed signals"
```

---

## Task 7: Wire `_startup_recovery` into `__init__`

**Files:**
- Modify: `bot.py`
- Modify: `tests/test_startup_recovery.py`

- [ ] **Step 7.1: Add integration test**

Append to `tests/test_startup_recovery.py`:

```python
class TestStartupRecoveryIntegration:
    def test_exception_in_startup_recovery_does_not_crash(self, monkeypatch):
        """Exception inside _startup_recovery is caught; caller completes normally."""
        b = _make_bot(monkeypatch)
        monkeypatch.setattr(bot.tr, "get_usdt_balance",
                            lambda: (_ for _ in ()).throw(RuntimeError("balance API down")))

        # Should not raise
        try:
            b._startup_recovery({"AAVEUSDT": {}})
        except Exception as exc:
            pytest.fail(f"_startup_recovery raised unexpectedly: {exc}")
```

- [ ] **Step 7.2: Run to confirm PASS (method already handles this)**

```bash
pytest tests/test_startup_recovery.py::TestStartupRecoveryIntegration -v
```

Expected: 1 passed (the balance fetch failure is already caught inside `_startup_recovery`).

- [ ] **Step 7.3: Add call to `_startup_recovery` in `bot.py __init__`**

Locate the `if unclosed:` / `_rebuild_stats_from_csv` block that ends Pass B (around line 454–460). After it but **inside** the outer `try` block (before the `except Exception as e: logger.error(f"Startup sync error: {e}")`), add:

```python
            # ── Startup recovery: positions that filled while bot was stopped ── #
            if not PAPER_MODE:
                try:
                    self._startup_recovery(existing)
                except Exception as _e:
                    logger.warning(f"Startup recovery failed: {_e}")
```

- [ ] **Step 7.4: Verify bot imports cleanly**

```bash
python -c "import bot; print('Bitget OK')"
```

Expected: `Bitget OK` (no import errors)

- [ ] **Step 7.5: Run full test suite**

```bash
pytest tests/test_startup_recovery.py -v
```

Expected: all tests pass.

- [ ] **Step 7.6: Commit**

```bash
git add bot.py tests/test_startup_recovery.py
git commit -m "feat(recovery): wire _startup_recovery into Bot.__init__ after Pass B"
```

---

## Task 8: `recover.py` CLI script

**Files:**
- Create: `recover.py`
- Create: `tests/test_recover_cli.py`

- [ ] **Step 8.1: Write failing tests**

```python
# tests/test_recover_cli.py
"""
Tests for recover.py CLI — manual recovery when bot is already running.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import csv
import json
import pandas as pd
import pytest
from unittest.mock import patch
from io import StringIO


class TestRecoverCli:
    def _make_state(self, tmp_path, symbols=None):
        """Write a minimal state.json with UNKNOWN open trades."""
        symbols = symbols or ["LINKUSDT", "UNIUSDT"]
        trades = [
            {
                "symbol": sym, "side": "SHORT",
                "qty": 14.0, "entry": 9.311,
                "sl": "?", "tp": "?",
                "strategy": "UNKNOWN", "trade_id": "",
                "opened_at": "2026-04-08T09:05:59+00:00",
                "margin": 13.05, "leverage": 10,
                "unrealised_pnl": 1.5, "mark_price": 9.0,
                "tpsl_set": False,
            }
            for sym in symbols
        ]
        state = {
            "open_trades": trades,
            "pending_signals": {},
            "position_memory": {},
            "balance": 500.0,
        }
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(state))
        return state_file

    def _run_cli(self, args: list[str], state_file, csv_file):
        """Import and run recover.main() with given args."""
        import importlib, types
        # Reload recover module to pick up fresh monkeypatches
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        recover.STATE_FILE = str(state_file)
        recover.TRADE_LOG  = str(csv_file)
        recover.main(args)

    def test_dry_run_writes_nothing(self, tmp_path, monkeypatch):
        """--dry-run flag: no files are written, no state changes."""
        state_file = self._make_state(tmp_path)
        csv_file   = tmp_path / "trades.csv"

        # Patch candle fetching and strategy to avoid network calls
        import startup_recovery
        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())

        import trader as tr
        monkeypatch.setattr(tr, "get_candles",
                            lambda sym, i, limit=100: pd.DataFrame())

        state_before = state_file.read_text()

        self._run_cli(["--dry-run"], state_file, csv_file)

        # State unchanged
        assert state_file.read_text() == state_before
        # CSV not created
        assert not csv_file.exists()

    def test_symbols_filter(self, tmp_path, monkeypatch, capsys):
        """--symbols LINKUSDT only processes LINKUSDT, not UNIUSDT."""
        state_file = self._make_state(tmp_path, ["LINKUSDT", "UNIUSDT"])
        csv_file   = tmp_path / "trades.csv"

        import startup_recovery
        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())

        import trader as tr
        monkeypatch.setattr(tr, "get_candles",
                            lambda sym, i, limit=100: pd.DataFrame())

        import snapshot
        monkeypatch.setattr(snapshot, "save_snapshot", lambda **kw: None)

        processed = []

        def _fake_log(action, details):
            processed.append(details.get("symbol"))

        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        recover.STATE_FILE = str(state_file)
        recover.TRADE_LOG  = str(csv_file)
        monkeypatch.setattr(recover, "_log_trade_to_csv",
                            lambda csv_path, action, details, dry_run=False:
                            processed.append(details.get("symbol")) if not dry_run else None)

        recover.main(["--symbols", "LINKUSDT"])

        assert "LINKUSDT" in processed
        assert "UNIUSDT" not in processed

    def test_summary_table_printed(self, tmp_path, monkeypatch, capsys):
        """Summary table is printed to stdout after recovery."""
        state_file = self._make_state(tmp_path, ["LINKUSDT"])
        csv_file   = tmp_path / "trades.csv"

        import startup_recovery
        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())

        import trader as tr
        monkeypatch.setattr(tr, "get_candles",
                            lambda sym, i, limit=100: pd.DataFrame())

        import snapshot
        monkeypatch.setattr(snapshot, "save_snapshot", lambda **kw: None)

        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        recover.STATE_FILE = str(state_file)
        recover.TRADE_LOG  = str(csv_file)

        recover.main([])

        captured = capsys.readouterr()
        assert "LINKUSDT" in captured.out
        assert "sl" in captured.out.lower() or "SL" in captured.out
```

- [ ] **Step 8.2: Run to confirm FAIL**

```bash
pytest tests/test_recover_cli.py -v
```

Expected: `ModuleNotFoundError: No module named 'recover'`

- [ ] **Step 8.3: Create `recover.py`**

```python
#!/usr/bin/env python3
"""
recover.py — Manual recovery CLI for positions that filled while the bot was stopped.

Usage:
    python recover.py [--dry-run] [--symbols SYM1 SYM2 ...]

Options:
    --dry-run    Print what would change without writing anything.
    --symbols    Limit recovery to specific symbols (default: all UNKNOWN in state.json).

Mirrors Bot._startup_recovery() sad path for use when the bot is already running.
The bot will pick up the patched state.json and trades.csv on its next tick.
"""
import argparse
import csv
import json
import sys
import uuid
import logging
from datetime import datetime, timezone
from pathlib import Path

import config
import state as st
import snapshot
import trader as tr
from startup_recovery import fetch_candles_at, estimate_sl_tp, attempt_s5_recovery

logger = logging.getLogger(__name__)

# Defaults — can be overridden in tests
STATE_FILE = "state.json"
TRADE_LOG  = config.TRADE_LOG

_TRADE_FIELDS = [
    "timestamp", "trade_id", "action", "symbol", "side", "qty", "entry", "sl", "tp",
    "box_low", "box_high", "leverage", "margin", "tpsl_set", "strategy",
    "snap_rsi", "snap_adx", "snap_htf", "snap_coil", "snap_box_range_pct", "snap_sentiment",
    "snap_daily_rsi",
    "snap_entry_trigger", "snap_sl", "snap_rr",
    "snap_rsi_peak", "snap_spike_body_pct", "snap_rsi_div", "snap_rsi_div_str",
    "snap_s5_ob_low", "snap_s5_ob_high", "snap_s5_tp",
    "snap_s6_peak", "snap_s6_drop_pct", "snap_s6_rsi_at_peak",
    "snap_sr_clearance_pct",
    "result", "pnl", "pnl_pct", "exit_reason", "exit_price",
]


def _log_trade_to_csv(csv_path: str, action: str, details: dict,
                      dry_run: bool = False) -> None:
    """Append a trade row to trades.csv. No-op in dry-run mode."""
    if dry_run:
        return
    import os
    row = {"timestamp": datetime.now(timezone.utc).isoformat(), "action": action, **details}
    write_header = not Path(csv_path).exists()
    with open(csv_path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_TRADE_FIELDS, extrasaction="ignore", restval="")
        if write_header:
            w.writeheader()
        w.writerow(row)


def _patch_state(state_file: str, sym: str, trade_id: str,
                 sl: float, tp: float, ob_low: float, ob_high: float,
                 dry_run: bool = False) -> None:
    """Update the open_trades entry for sym in state.json."""
    if dry_run:
        return
    data = json.loads(Path(state_file).read_text())
    for t in data.get("open_trades", []):
        if t["symbol"] == sym:
            t.update({
                "trade_id": trade_id,
                "sl":       round(sl,      8),
                "tp":       round(tp,      8),
                "box_high": round(ob_high, 8),
                "box_low":  round(ob_low,  8),
                "tpsl_set": False,
            })
            break
    Path(state_file).write_text(json.dumps(data, indent=2))


def _df_to_candles(df) -> list[dict]:
    return [
        {"t": int(r.ts), "o": float(r.open), "h": float(r.high),
         "l": float(r.low),  "c": float(r.close), "v": float(r.vol)}
        for r in df.itertuples()
    ]


def recover_position(sym: str, trade_entry: dict,
                     state_file: str, csv_path: str,
                     dry_run: bool = False) -> dict:
    """
    Run sad-path recovery for a single UNKNOWN position.
    Returns summary dict: {symbol, trade_id, entry, sl, tp, snapshot}.
    """
    entry     = float(trade_entry.get("entry", 0))
    side      = trade_entry.get("side", "SHORT")
    margin    = float(trade_entry.get("margin", 0))
    leverage  = int(float(trade_entry.get("leverage") or 10))
    qty       = float(trade_entry.get("qty", 0))
    opened_at = trade_entry.get("opened_at") or datetime.now(timezone.utc).isoformat()
    trade_id  = uuid.uuid4().hex[:8]

    try:
        end_ms = int(datetime.fromisoformat(opened_at).timestamp() * 1000) + 60_000
    except Exception:
        import time
        end_ms = int(time.time() * 1000)

    m15_df   = fetch_candles_at(sym, "15m", limit=100, end_ms=end_ms)
    htf_df   = fetch_candles_at(sym, "1H",  limit=50,  end_ms=end_ms)
    daily_df = tr.get_candles(sym, "1D", limit=60)

    result = None
    if not m15_df.empty and not htf_df.empty and not daily_df.empty:
        result = attempt_s5_recovery(sym, m15_df, htf_df, daily_df, side)

    sl, tp, ob_low, ob_high = result if result else estimate_sl_tp(entry, side)

    # Patch state.json
    _patch_state(state_file, sym, trade_id, sl, tp, ob_low, ob_high, dry_run=dry_run)

    # Append CSV open row
    _log_trade_to_csv(csv_path, f"UNKNOWN_{side}", {
        "trade_id":        trade_id,
        "symbol":          sym,
        "side":            side,
        "qty":             qty,
        "entry":           entry,
        "sl":              round(sl,      8),
        "tp":              round(tp,      8),
        "box_low":         round(ob_low,  8),
        "box_high":        round(ob_high, 8),
        "leverage":        leverage,
        "margin":          round(margin,  8),
        "tpsl_set":        False,
        "strategy":        "UNKNOWN",
        "snap_s5_ob_low":  round(ob_low,  8),
        "snap_s5_ob_high": round(ob_high, 8),
        "snap_s5_tp":      round(tp,      8),
    }, dry_run=dry_run)

    # Save snapshot
    snap_saved = False
    if not dry_run and not m15_df.empty:
        snapshot.save_snapshot(
            trade_id=trade_id,
            event="open",
            symbol=sym,
            interval="15m",
            candles=_df_to_candles(m15_df),
            event_price=entry,
            captured_at=opened_at,
        )
        snap_saved = True

    return {
        "symbol":   sym,
        "trade_id": trade_id,
        "entry":    entry,
        "sl":       sl,
        "tp":       tp,
        "snapshot": snap_saved,
    }


def main(args=None):
    parser = argparse.ArgumentParser(
        description="Manual recovery for positions that filled while the bot was stopped."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would change without writing to disk.")
    parser.add_argument("--symbols", nargs="+", metavar="SYM",
                        help="Limit recovery to specific symbols.")
    parsed = parser.parse_args(args)

    data   = json.loads(Path(STATE_FILE).read_text())
    trades = data.get("open_trades", [])

    # Find UNKNOWN positions (no CSV open row, trade_id blank or sl="?")
    targets = [
        t for t in trades
        if (t.get("strategy") == "UNKNOWN" or t.get("sl") in ("?", "", None))
        and (not parsed.symbols or t["symbol"] in parsed.symbols)
    ]

    if not targets:
        print("No UNKNOWN positions found — nothing to recover.")
        return

    mode = "[DRY RUN] " if parsed.dry_run else ""
    print(f"{mode}Recovering {len(targets)} position(s)...\n")

    results = []
    for t in targets:
        sym = t["symbol"]
        print(f"  {sym}...", end=" ", flush=True)
        try:
            r = recover_position(sym, t, STATE_FILE, TRADE_LOG, dry_run=parsed.dry_run)
            results.append(r)
            print("done")
        except Exception as e:
            print(f"ERROR: {e}")
            logger.warning(f"[{sym}] recover_position failed: {e}")

    # Summary table
    print(f"\n{'Symbol':14s}  {'trade_id':>9s}  {'Entry':>10s}  {'SL':>10s}  {'TP':>10s}  {'Snap':>4s}")
    print("-" * 65)
    for r in results:
        snap = "yes" if r["snapshot"] else ("skip" if not parsed.dry_run else "n/a")
        print(
            f"{r['symbol']:14s}  {r['trade_id']:>9s}  "
            f"{r['entry']:>10.5f}  {r['sl']:>10.5f}  {r['tp']:>10.5f}  {snap:>4s}"
        )

    if parsed.dry_run:
        print("\n[DRY RUN] No files were written.")
    else:
        print(f"\n⚠️  tpsl_set=False for all recovered positions.")
        print("   Manually set SL/TP on Bitget, or restart the bot to activate S5 swing-trail.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    main()
```

- [ ] **Step 8.4: Run tests to confirm PASS**

```bash
pytest tests/test_recover_cli.py -v
```

Expected: 3 passed

- [ ] **Step 8.5: Run full test suite to confirm no regressions**

```bash
pytest tests/test_startup_recovery.py tests/test_recover_cli.py -v
```

Expected: all pass

- [ ] **Step 8.6: Commit**

```bash
git add recover.py tests/test_recover_cli.py
git commit -m "feat(recovery): recover.py CLI with dry-run and --symbols filter"
```

---

## Task 9: QA — run full test suite

- [ ] **Step 9.1: Run all tests**

```bash
pytest --tb=short -q
```

Expected: all existing tests pass; no regressions. New test files add to the count.

- [ ] **Step 9.2: Verify bot imports cleanly**

```bash
python -c "import bot; print('Bitget OK')"
python -c "import ig_bot; print('IG OK')"
```

Expected: both print their OK message with no errors.

- [ ] **Step 9.3: Quick smoke test of recover.py help**

```bash
python recover.py --help
```

Expected: prints usage with `--dry-run` and `--symbols` options.

- [ ] **Step 9.4: Commit final**

```bash
git add .
git commit -m "feat(recovery): startup recovery complete — bot auto-recovers crashed-before-log positions"
```
