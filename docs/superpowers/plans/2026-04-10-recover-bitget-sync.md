# recover.py — Bitget Sync Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite `recover.py` so it uses `tr.get_all_open_positions()` as the source of truth, reconciling all live Bitget positions against `state.json` and `trades.csv` via a two-pass classify-and-act loop.

**Architecture:** Pass 1 iterates Bitget positions and classifies each as SKIP / PATCH_SLTP / FULL_RECOVERY; Pass 2 iterates state.json open_trades and warns about any not on exchange. State writes use `st._read()` / `st._write()` matching `bot.py._startup_recovery()`.

**Tech Stack:** Python 3.12+, pytest, `trader.py`, `startup_recovery.py`, `state.py`, `snapshot.py`

---

## File Map

| File | Change |
|---|---|
| `recover.py` | Full restructure — new helpers, new `main()`, remove `_patch_state()` and `recover_position()` |
| `tests/test_recover_cli.py` | Replace all existing tests; new tests cover Bitget-source-of-truth behaviour |

---

### Task 1: Add `_get_open_csv_row` and `_is_valid_sltp` helpers + tests

These are the two pure helpers that everything else depends on.

**Files:**
- Modify: `recover.py`
- Modify: `tests/test_recover_cli.py`

- [ ] **Step 1: Write failing tests**

Replace the entire content of `tests/test_recover_cli.py` with:

```python
"""
tests/test_recover_cli.py — Tests for recover.py CLI (Bitget-sync redesign).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import csv
import json
import pandas as pd
import pytest
from pathlib import Path
from unittest.mock import patch


# ── Helpers ──────────────────────────────────────────────────────────────── #

def _reload_recover(monkeypatch, state_file, csv_file):
    """Re-import recover with patched file paths."""
    if "recover" in sys.modules:
        del sys.modules["recover"]
    import recover
    recover.STATE_FILE = str(state_file)
    recover.TRADE_LOG  = str(csv_file)
    import state as st
    monkeypatch.setattr(st, "STATE_FILE", str(state_file))
    return recover


def _write_state(path, open_trades):
    data = {
        "open_trades": open_trades,
        "pending_signals": {},
        "position_memory": {},
        "balance": 500.0,
    }
    Path(path).write_text(json.dumps(data))
    return data


def _write_csv(path, rows):
    """Write a minimal trades.csv with given rows."""
    fields = [
        "timestamp", "trade_id", "action", "symbol", "side", "qty",
        "entry", "sl", "tp", "box_low", "box_high", "leverage", "margin",
        "tpsl_set", "strategy", "result", "pnl", "pnl_pct",
        "exit_reason", "exit_price",
    ]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", restval="")
        w.writeheader()
        for r in rows:
            w.writerow(r)


# ── Task 1: Unit tests for pure helpers ──────────────────────────────────── #

class TestGetOpenCsvRow:
    def test_returns_most_recent_open_row(self, tmp_path):
        """Returns the last _LONG/_SHORT row for the symbol."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        csv_file = tmp_path / "trades.csv"
        _write_csv(csv_file, [
            {"action": "S5_SHORT", "symbol": "LINKUSDT", "qty": "14", "trade_id": "aaa"},
        ])
        row = recover._get_open_csv_row(str(csv_file), "LINKUSDT")
        assert row is not None
        assert row["trade_id"] == "aaa"

    def test_returns_none_when_no_matching_row(self, tmp_path):
        """Returns None when symbol not in CSV."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        csv_file = tmp_path / "trades.csv"
        _write_csv(csv_file, [
            {"action": "S5_SHORT", "symbol": "BTCUSDT", "qty": "1", "trade_id": "bbb"},
        ])
        assert recover._get_open_csv_row(str(csv_file), "LINKUSDT") is None

    def test_returns_none_when_csv_missing(self, tmp_path):
        """Returns None when CSV file does not exist."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        assert recover._get_open_csv_row(str(tmp_path / "missing.csv"), "LINKUSDT") is None

    def test_ignores_close_rows(self, tmp_path):
        """Rows with action S5_CLOSE are not returned."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        csv_file = tmp_path / "trades.csv"
        _write_csv(csv_file, [
            {"action": "S5_CLOSE", "symbol": "LINKUSDT", "qty": "14", "trade_id": "ccc"},
        ])
        assert recover._get_open_csv_row(str(csv_file), "LINKUSDT") is None


class TestIsValidSltp:
    def test_valid_floats_return_true(self):
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        assert recover._is_valid_sltp(9.5, 8.0) is True

    def test_string_question_mark_returns_false(self):
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        assert recover._is_valid_sltp("?", 8.0) is False

    def test_none_returns_false(self):
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        assert recover._is_valid_sltp(None, 8.0) is False

    def test_zero_returns_false(self):
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        assert recover._is_valid_sltp(0, 8.0) is False

    def test_empty_string_returns_false(self):
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        assert recover._is_valid_sltp("", 8.0) is False

    def test_string_float_returns_true(self):
        """String floats (as stored in CSV) should be accepted."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        assert recover._is_valid_sltp("9.777", "8.500") is True
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
python -m pytest tests/test_recover_cli.py::TestGetOpenCsvRow tests/test_recover_cli.py::TestIsValidSltp -v 2>&1 | head -40
```

Expected: `AttributeError` or `ImportError` — `_get_open_csv_row` and `_is_valid_sltp` don't exist in `recover.py` yet.

- [ ] **Step 3: Add helpers to `recover.py`**

Open `recover.py`. Add `_get_open_csv_row` and `_is_valid_sltp` after the `_TRADE_FIELDS` list and before `_log_trade_to_csv`. Also add `import state as st` and `import trader as tr` to the imports at the top.

Replace the imports block (lines 15–28) with:

```python
import argparse
import csv
import json
import sys
import uuid
import logging
from datetime import datetime, timezone
from pathlib import Path

import config
import snapshot
import state as st
import trader as tr
from startup_recovery import fetch_candles_at, estimate_sl_tp, attempt_s5_recovery
```

Then add the two helpers after `_TRADE_FIELDS` (after line 46, before `_log_trade_to_csv`):

```python
def _get_open_csv_row(csv_path: str, symbol: str) -> dict | None:
    """Return the most recent open (_LONG/_SHORT) CSV row for a symbol, or None."""
    if not Path(csv_path).exists():
        return None
    try:
        with open(csv_path, newline="") as f:
            rows = list(csv.DictReader(f))
        for r in reversed(rows):
            action = r.get("action", "")
            if (r.get("symbol") == symbol
                    and any(action.endswith(sfx) for sfx in ("_LONG", "_SHORT"))
                    and r.get("qty")):
                return r
    except Exception:
        pass
    return None


def _is_valid_sltp(sl, tp) -> bool:
    """Return True iff both sl and tp parse as float > 0."""
    try:
        return float(sl) > 0 and float(tp) > 0
    except (TypeError, ValueError):
        return False
```

- [ ] **Step 4: Run tests — expect pass**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
python -m pytest tests/test_recover_cli.py::TestGetOpenCsvRow tests/test_recover_cli.py::TestIsValidSltp -v 2>&1 | tail -20
```

Expected: all 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
git add recover.py tests/test_recover_cli.py
git commit -m "feat(recover): add _get_open_csv_row and _is_valid_sltp helpers"
```

---

### Task 2: Add `_patch_sltp` + tests

The `PATCH_SLTP` path: state.json patch only, no CSV write.

**Files:**
- Modify: `recover.py`
- Modify: `tests/test_recover_cli.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_recover_cli.py` (after the `TestIsValidSltp` class):

```python
class TestPatchSltp:
    """_patch_sltp updates state.json SL/TP without writing a CSV row."""

    def _exchange_pos(self, side="SHORT", entry=9.311):
        return {
            "side": side, "entry_price": entry,
            "qty": 14.0, "margin": 13.05, "leverage": 10,
        }

    def test_patches_state_json_sl_tp(self, tmp_path, monkeypatch):
        """_patch_sltp writes new sl/tp into state.json for the symbol."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        import state as st

        state_file = tmp_path / "state.json"
        _write_state(state_file, [{
            "symbol": "LINKUSDT", "side": "SHORT", "qty": 14.0,
            "entry": 9.311, "sl": "?", "tp": "?", "strategy": "S3",
            "trade_id": "abc123", "opened_at": "2026-04-08T09:00:00+00:00",
            "margin": 13.05, "leverage": 10, "tpsl_set": False,
        }])
        monkeypatch.setattr(st, "STATE_FILE", str(state_file))

        import startup_recovery
        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())
        monkeypatch.setattr(tr, "get_candles",
                            lambda sym, i, limit=100: pd.DataFrame())

        result = recover._patch_sltp(
            "LINKUSDT",
            {"strategy": "S3", "opened_at": "2026-04-08T09:00:00+00:00"},
            self._exchange_pos(),
            str(state_file),
            str(tmp_path / "trades.csv"),
            dry_run=False,
        )

        assert result["action"] == "PATCH_SLTP"
        assert float(result["sl"]) > 0
        assert float(result["tp"]) > 0

        # state.json updated
        data = json.loads(state_file.read_text())
        t = data["open_trades"][0]
        assert float(t["sl"]) > 0
        assert float(t["tp"]) > 0

    def test_dry_run_does_not_write_state(self, tmp_path, monkeypatch):
        """--dry-run: state.json is not modified by _patch_sltp."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        import state as st

        state_file = tmp_path / "state.json"
        _write_state(state_file, [{
            "symbol": "LINKUSDT", "side": "SHORT", "qty": 14.0,
            "entry": 9.311, "sl": "?", "tp": "?", "strategy": "S3",
            "trade_id": "abc123", "opened_at": "2026-04-08T09:00:00+00:00",
            "margin": 13.05, "leverage": 10, "tpsl_set": False,
        }])
        monkeypatch.setattr(st, "STATE_FILE", str(state_file))
        before = state_file.read_text()

        import startup_recovery
        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())
        monkeypatch.setattr(tr, "get_candles",
                            lambda sym, i, limit=100: pd.DataFrame())

        recover._patch_sltp(
            "LINKUSDT",
            {"strategy": "S3", "opened_at": "2026-04-08T09:00:00+00:00"},
            self._exchange_pos(),
            str(state_file),
            str(tmp_path / "trades.csv"),
            dry_run=True,
        )

        assert state_file.read_text() == before

    def test_uses_s5_ob_recovery_for_s5_strategy(self, tmp_path, monkeypatch):
        """For S5 strategy, attempt_s5_recovery is called."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        import state as st
        import startup_recovery

        state_file = tmp_path / "state.json"
        _write_state(state_file, [{
            "symbol": "LINKUSDT", "side": "SHORT", "qty": 14.0,
            "entry": 9.311, "sl": "?", "tp": "?", "strategy": "S5",
            "trade_id": "abc123", "opened_at": "2026-04-08T09:00:00+00:00",
            "margin": 13.05, "leverage": 10, "tpsl_set": False,
        }])
        monkeypatch.setattr(st, "STATE_FILE", str(state_file))

        s5_called = []
        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())
        monkeypatch.setattr(tr, "get_candles",
                            lambda sym, i, limit=100: pd.DataFrame())
        monkeypatch.setattr(
            startup_recovery, "attempt_s5_recovery",
            lambda *a, **kw: s5_called.append(True) or None,
        )

        recover._patch_sltp(
            "LINKUSDT",
            {"strategy": "S5", "opened_at": "2026-04-08T09:00:00+00:00"},
            self._exchange_pos(),
            str(state_file),
            str(tmp_path / "trades.csv"),
            dry_run=False,
        )

        assert s5_called, "attempt_s5_recovery must be called for S5 strategy"

    def test_does_not_write_csv(self, tmp_path, monkeypatch):
        """_patch_sltp never creates or appends to trades.csv."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        import state as st

        state_file = tmp_path / "state.json"
        csv_file   = tmp_path / "trades.csv"
        _write_state(state_file, [{
            "symbol": "LINKUSDT", "side": "SHORT", "qty": 14.0,
            "entry": 9.311, "sl": "?", "tp": "?", "strategy": "S1",
            "trade_id": "abc123", "opened_at": "2026-04-08T09:00:00+00:00",
            "margin": 13.05, "leverage": 10, "tpsl_set": False,
        }])
        monkeypatch.setattr(st, "STATE_FILE", str(state_file))

        import startup_recovery
        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())
        monkeypatch.setattr(tr, "get_candles",
                            lambda sym, i, limit=100: pd.DataFrame())

        recover._patch_sltp(
            "LINKUSDT",
            {"strategy": "S1", "opened_at": "2026-04-08T09:00:00+00:00"},
            self._exchange_pos(),
            str(state_file),
            str(csv_file),
            dry_run=False,
        )

        assert not csv_file.exists(), "_patch_sltp must not write trades.csv"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
python -m pytest tests/test_recover_cli.py::TestPatchSltp -v 2>&1 | head -30
```

Expected: `AttributeError: module 'recover' has no attribute '_patch_sltp'`

- [ ] **Step 3: Implement `_patch_sltp` in `recover.py`**

Add this function after `_is_valid_sltp` and before `_log_trade_to_csv`:

```python
def _patch_sltp(sym: str, state_entry: dict, exchange_pos: dict,
                state_file: str, csv_path: str,
                dry_run: bool = False) -> dict:
    """
    PATCH_SLTP case: CSV open row exists but SL/TP are bad.
    Derives new SL/TP via S5 OB recovery (S5) or estimate_sl_tp (all others).
    Patches state.json only — no CSV write.
    Returns summary dict: {symbol, action, strategy, sl, tp}.
    """
    strategy  = state_entry.get("strategy", "UNKNOWN")
    side      = exchange_pos.get("side", "SHORT")
    entry     = float(exchange_pos.get("entry_price", 0))
    opened_at = state_entry.get("opened_at") or datetime.now(timezone.utc).isoformat()

    sl = tp = ob_low = ob_high = None

    if strategy == "S5":
        try:
            end_ms = int(datetime.fromisoformat(opened_at).timestamp() * 1000) + 60_000
        except Exception:
            import time
            end_ms = int(time.time() * 1000)

        m15_df   = fetch_candles_at(sym, "15m", limit=100, end_ms=end_ms)
        htf_df   = fetch_candles_at(sym, "1H",  limit=50,  end_ms=end_ms)
        daily_df = tr.get_candles(sym, "1D", limit=60)

        if not m15_df.empty and not htf_df.empty and not daily_df.empty:
            result = attempt_s5_recovery(sym, m15_df, htf_df, daily_df, side)
            if result:
                sl, tp, ob_low, ob_high = result

    if sl is None:
        sl, tp, ob_low, ob_high = estimate_sl_tp(entry, side)

    if not dry_run:
        s = st._read()
        for t in s.get("open_trades", []):
            if t["symbol"] == sym:
                t.update({
                    "sl":       round(sl,      8),
                    "tp":       round(tp,      8),
                    "box_high": round(ob_high, 8),
                    "box_low":  round(ob_low,  8),
                    "tpsl_set": False,
                })
                break
        st._write(s)

    return {"symbol": sym, "action": "PATCH_SLTP", "strategy": strategy,
            "sl": round(sl, 8), "tp": round(tp, 8)}
```

- [ ] **Step 4: Run tests — expect pass**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
python -m pytest tests/test_recover_cli.py::TestPatchSltp -v 2>&1 | tail -20
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
git add recover.py tests/test_recover_cli.py
git commit -m "feat(recover): add _patch_sltp for PATCH_SLTP classification"
```

---

### Task 3: Rename `recover_position` → `_full_recovery` + tests

The FULL_RECOVERY path: new trade_id, CSV row, state patch, snapshot.

**Files:**
- Modify: `recover.py`
- Modify: `tests/test_recover_cli.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_recover_cli.py`:

```python
class TestFullRecovery:
    """_full_recovery: new trade_id, CSV open row, state patch, snapshot."""

    def _exchange_pos(self, side="SHORT", entry=9.311):
        return {
            "side": side, "entry_price": entry,
            "qty": 14.0, "margin": 13.05, "leverage": 10,
        }

    def test_writes_csv_row_and_patches_state(self, tmp_path, monkeypatch):
        """FULL_RECOVERY writes a CSV open row and patches state.json."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        import state as st

        state_file = tmp_path / "state.json"
        csv_file   = tmp_path / "trades.csv"
        _write_state(state_file, [])
        monkeypatch.setattr(st, "STATE_FILE", str(state_file))

        import startup_recovery
        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())
        monkeypatch.setattr(tr, "get_candles",
                            lambda sym, i, limit=100: pd.DataFrame())
        monkeypatch.setattr(snapshot, "save_snapshot", lambda **kw: None)

        result = recover._full_recovery(
            "LINKUSDT", self._exchange_pos(),
            str(state_file), str(csv_file),
            dry_run=False,
        )

        assert result["action"] == "FULL"
        assert len(result["trade_id"]) == 8
        assert float(result["sl"]) > 0
        assert float(result["tp"]) > 0

        # CSV row written
        assert csv_file.exists()
        with open(csv_file, newline="") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1
        assert rows[0]["symbol"] == "LINKUSDT"
        assert rows[0]["trade_id"] == result["trade_id"]

        # state.json patched
        data = json.loads(state_file.read_text())
        trades = data["open_trades"]
        assert any(t["symbol"] == "LINKUSDT" for t in trades)

    def test_dry_run_writes_nothing(self, tmp_path, monkeypatch):
        """dry_run=True: no files written."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        import state as st

        state_file = tmp_path / "state.json"
        csv_file   = tmp_path / "trades.csv"
        _write_state(state_file, [])
        monkeypatch.setattr(st, "STATE_FILE", str(state_file))
        state_before = state_file.read_text()

        import startup_recovery
        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())
        monkeypatch.setattr(tr, "get_candles",
                            lambda sym, i, limit=100: pd.DataFrame())

        recover._full_recovery(
            "LINKUSDT", self._exchange_pos(),
            str(state_file), str(csv_file),
            dry_run=True,
        )

        assert state_file.read_text() == state_before
        assert not csv_file.exists()

    def test_snapshot_saved_when_candles_available(self, tmp_path, monkeypatch):
        """snapshot.save_snapshot is called when m15 candles are non-empty."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        import state as st
        import numpy as np

        state_file = tmp_path / "state.json"
        csv_file   = tmp_path / "trades.csv"
        _write_state(state_file, [])
        monkeypatch.setattr(st, "STATE_FILE", str(state_file))

        n = 10
        fake_df = pd.DataFrame({
            "ts": list(range(n)), "open": [9.0]*n, "high": [9.1]*n,
            "low": [8.9]*n, "close": [9.05]*n, "vol": [100.0]*n,
        })

        import startup_recovery
        monkeypatch.setattr(
            startup_recovery, "fetch_candles_at",
            lambda sym, interval, **kw: fake_df if interval == "15m" else fake_df,
        )
        monkeypatch.setattr(tr, "get_candles",
                            lambda sym, i, limit=100: fake_df)

        snap_calls = []
        monkeypatch.setattr(snapshot, "save_snapshot",
                            lambda **kw: snap_calls.append(kw))

        recover._full_recovery(
            "LINKUSDT", self._exchange_pos(),
            str(state_file), str(csv_file),
            dry_run=False,
        )

        assert len(snap_calls) == 1
        assert snap_calls[0]["event"] == "open"
        assert snap_calls[0]["symbol"] == "LINKUSDT"

    def test_snapshot_skipped_when_no_candles(self, tmp_path, monkeypatch):
        """snapshot.save_snapshot is NOT called when m15 candles are empty."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        import state as st

        state_file = tmp_path / "state.json"
        csv_file   = tmp_path / "trades.csv"
        _write_state(state_file, [])
        monkeypatch.setattr(st, "STATE_FILE", str(state_file))

        import startup_recovery
        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())
        monkeypatch.setattr(tr, "get_candles",
                            lambda sym, i, limit=100: pd.DataFrame())

        snap_calls = []
        monkeypatch.setattr(snapshot, "save_snapshot",
                            lambda **kw: snap_calls.append(kw))

        recover._full_recovery(
            "LINKUSDT", self._exchange_pos(),
            str(state_file), str(csv_file),
            dry_run=False,
        )

        assert snap_calls == []
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
python -m pytest tests/test_recover_cli.py::TestFullRecovery -v 2>&1 | head -30
```

Expected: `AttributeError: module 'recover' has no attribute '_full_recovery'`

- [ ] **Step 3: Rename `recover_position` → `_full_recovery` in `recover.py`**

In `recover.py`, rename `def recover_position(` to `def _full_recovery(` and update its signature to accept `exchange_pos: dict` instead of `trade_entry: dict`. Update the body to source fields from `exchange_pos` (same fields, different parameter name). Also replace the `_patch_state(...)` call with `st._read()` / `st._write()` pattern. Replace `st.add_open_trade` call if the symbol isn't already in state.

Full replacement of `recover_position` (lines 92–167) with:

```python
def _full_recovery(sym: str, exchange_pos: dict,
                   state_file: str, csv_path: str,
                   dry_run: bool = False) -> dict:
    """
    FULL_RECOVERY case: no CSV open row exists for this symbol.
    Assigns new trade_id, writes CSV open row, patches/adds state.json entry,
    saves snapshot.
    Returns summary dict: {symbol, action, trade_id, entry, sl, tp, snapshot}.
    """
    entry     = float(exchange_pos.get("entry_price", 0))
    side      = exchange_pos.get("side", "SHORT")
    margin    = float(exchange_pos.get("margin", 0))
    leverage  = int(float(exchange_pos.get("leverage") or 10))
    qty       = float(exchange_pos.get("qty", 0))

    # Use opened_at from state if available, otherwise now
    _ot       = st.get_open_trade(sym)
    opened_at = (_ot or {}).get("opened_at") or datetime.now(timezone.utc).isoformat()
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

    # Patch or add state.json entry
    if not dry_run:
        s = st._read()
        found = False
        for t in s.get("open_trades", []):
            if t["symbol"] == sym:
                t.update({
                    "trade_id": trade_id,
                    "sl":       round(sl,      8),
                    "tp":       round(tp,      8),
                    "box_high": round(ob_high, 8),
                    "box_low":  round(ob_low,  8),
                    "tpsl_set": False,
                })
                found = True
                break
        if not found:
            s.setdefault("open_trades", []).append({
                "symbol":    sym,
                "side":      side,
                "qty":       qty,
                "entry":     entry,
                "sl":        round(sl,      8),
                "tp":        round(tp,      8),
                "box_high":  round(ob_high, 8),
                "box_low":   round(ob_low,  8),
                "leverage":  leverage,
                "margin":    round(margin,  8),
                "strategy":  "UNKNOWN",
                "opened_at": opened_at,
                "trade_id":  trade_id,
                "tpsl_set":  False,
            })
        st._write(s)

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
        "action":   "FULL",
        "trade_id": trade_id,
        "entry":    entry,
        "sl":       sl,
        "tp":       tp,
        "snapshot": snap_saved,
    }
```

Also remove the old `_patch_state` function (no longer needed).

- [ ] **Step 4: Run tests — expect pass**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
python -m pytest tests/test_recover_cli.py::TestFullRecovery -v 2>&1 | tail -20
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
git add recover.py tests/test_recover_cli.py
git commit -m "feat(recover): rename recover_position → _full_recovery, source from exchange_pos"
```

---

### Task 4: Rewrite `main()` with two-pass Bitget-sourced loop + tests

This is the orchestration layer. Exchange is ground truth.

**Files:**
- Modify: `recover.py`
- Modify: `tests/test_recover_cli.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_recover_cli.py`:

```python
import snapshot as snapshot_mod


class TestMainTwoPasses:
    """Integration tests for the rewritten main()."""

    def _bitget_pos(self, sym, side="SHORT", entry=9.311):
        return {
            "side": side, "entry_price": entry,
            "qty": 14.0, "margin": 13.05, "leverage": 10,
        }

    def test_skips_position_with_valid_sltp(self, tmp_path, monkeypatch, capsys):
        """Pass 1: SKIP when CSV row exists and SL/TP are valid."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        import state as st

        state_file = tmp_path / "state.json"
        csv_file   = tmp_path / "trades.csv"
        _write_state(state_file, [{
            "symbol": "LINKUSDT", "side": "SHORT", "qty": 14.0,
            "entry": 9.311, "sl": 9.777, "tp": 8.500, "strategy": "S5",
            "trade_id": "abc123", "opened_at": "2026-04-08T09:00:00+00:00",
            "margin": 13.05, "leverage": 10, "tpsl_set": True,
        }])
        _write_csv(csv_file, [{
            "action": "S5_SHORT", "symbol": "LINKUSDT", "qty": "14",
            "trade_id": "abc123", "sl": "9.777", "tp": "8.500",
        }])
        monkeypatch.setattr(st, "STATE_FILE", str(state_file))
        monkeypatch.setattr(tr, "get_all_open_positions",
                            lambda: {"LINKUSDT": self._bitget_pos("LINKUSDT")})

        recover.STATE_FILE = str(state_file)
        recover.TRADE_LOG  = str(csv_file)
        recover.main([])

        out = capsys.readouterr().out
        assert "SKIP" in out
        assert "LINKUSDT" in out

    def test_patch_sltp_for_known_strategy_missing_sltp(self, tmp_path, monkeypatch, capsys):
        """Pass 1: PATCH_SLTP when CSV exists but SL/TP are bad."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        import state as st
        import startup_recovery

        state_file = tmp_path / "state.json"
        csv_file   = tmp_path / "trades.csv"
        _write_state(state_file, [{
            "symbol": "LINKUSDT", "side": "SHORT", "qty": 14.0,
            "entry": 9.311, "sl": "?", "tp": "?", "strategy": "S3",
            "trade_id": "abc123", "opened_at": "2026-04-08T09:00:00+00:00",
            "margin": 13.05, "leverage": 10, "tpsl_set": False,
        }])
        _write_csv(csv_file, [{
            "action": "S3_SHORT", "symbol": "LINKUSDT", "qty": "14",
            "trade_id": "abc123", "sl": "?", "tp": "?",
        }])
        monkeypatch.setattr(st, "STATE_FILE", str(state_file))
        monkeypatch.setattr(tr, "get_all_open_positions",
                            lambda: {"LINKUSDT": self._bitget_pos("LINKUSDT")})
        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())
        monkeypatch.setattr(tr, "get_candles",
                            lambda sym, i, limit=100: pd.DataFrame())

        recover.STATE_FILE = str(state_file)
        recover.TRADE_LOG  = str(csv_file)
        recover.main([])

        out = capsys.readouterr().out
        assert "PATCH_SLTP" in out
        assert "LINKUSDT" in out

    def test_full_recovery_for_position_with_no_csv(self, tmp_path, monkeypatch, capsys):
        """Pass 1: FULL_RECOVERY when no CSV open row exists."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        import state as st
        import startup_recovery

        state_file = tmp_path / "state.json"
        csv_file   = tmp_path / "trades.csv"
        _write_state(state_file, [])
        monkeypatch.setattr(st, "STATE_FILE", str(state_file))
        monkeypatch.setattr(tr, "get_all_open_positions",
                            lambda: {"LINKUSDT": self._bitget_pos("LINKUSDT")})
        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())
        monkeypatch.setattr(tr, "get_candles",
                            lambda sym, i, limit=100: pd.DataFrame())
        monkeypatch.setattr(snapshot_mod, "save_snapshot", lambda **kw: None)

        recover.STATE_FILE = str(state_file)
        recover.TRADE_LOG  = str(csv_file)
        recover.main([])

        out = capsys.readouterr().out
        assert "FULL" in out
        assert "LINKUSDT" in out

    def test_pass2_warns_for_state_position_not_on_bitget(self, tmp_path, monkeypatch, capsys):
        """Pass 2: WARNING printed for position in state.json but not on Bitget."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        import state as st

        state_file = tmp_path / "state.json"
        csv_file   = tmp_path / "trades.csv"
        _write_state(state_file, [{
            "symbol": "XRPUSDT", "side": "SHORT", "qty": 100.0,
            "entry": 0.5, "sl": 0.525, "tp": 0.45, "strategy": "S3",
            "trade_id": "xyz789", "opened_at": "2026-04-08T09:00:00+00:00",
            "margin": 5.0, "leverage": 10, "tpsl_set": True,
        }])
        monkeypatch.setattr(st, "STATE_FILE", str(state_file))
        # Bitget has NO positions
        monkeypatch.setattr(tr, "get_all_open_positions", lambda: {})

        recover.STATE_FILE = str(state_file)
        recover.TRADE_LOG  = str(csv_file)
        recover.main([])

        out = capsys.readouterr().out
        assert "WARNING" in out or "⚠" in out
        assert "XRPUSDT" in out

    def test_symbols_filter_limits_pass1(self, tmp_path, monkeypatch, capsys):
        """--symbols LINKUSDT: only LINKUSDT processed in Pass 1, ETHUSDT skipped."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        import state as st
        import startup_recovery

        state_file = tmp_path / "state.json"
        csv_file   = tmp_path / "trades.csv"
        _write_state(state_file, [])
        monkeypatch.setattr(st, "STATE_FILE", str(state_file))
        monkeypatch.setattr(tr, "get_all_open_positions", lambda: {
            "LINKUSDT": self._bitget_pos("LINKUSDT"),
            "ETHUSDT":  self._bitget_pos("ETHUSDT", entry=2000.0),
        })
        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())
        monkeypatch.setattr(tr, "get_candles",
                            lambda sym, i, limit=100: pd.DataFrame())
        monkeypatch.setattr(snapshot_mod, "save_snapshot", lambda **kw: None)

        recover.STATE_FILE = str(state_file)
        recover.TRADE_LOG  = str(csv_file)
        recover.main(["--symbols", "LINKUSDT"])

        out = capsys.readouterr().out
        assert "LINKUSDT" in out
        assert "ETHUSDT" not in out

    def test_dry_run_prints_prefix_and_writes_nothing(self, tmp_path, monkeypatch, capsys):
        """--dry-run: output contains [DRY RUN] and no files are written."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        import state as st
        import startup_recovery

        state_file = tmp_path / "state.json"
        csv_file   = tmp_path / "trades.csv"
        _write_state(state_file, [])
        state_before = state_file.read_text()
        monkeypatch.setattr(st, "STATE_FILE", str(state_file))
        monkeypatch.setattr(tr, "get_all_open_positions",
                            lambda: {"LINKUSDT": self._bitget_pos("LINKUSDT")})
        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())
        monkeypatch.setattr(tr, "get_candles",
                            lambda sym, i, limit=100: pd.DataFrame())

        recover.STATE_FILE = str(state_file)
        recover.TRADE_LOG  = str(csv_file)
        recover.main(["--dry-run"])

        out = capsys.readouterr().out
        assert "[DRY RUN]" in out
        assert state_file.read_text() == state_before
        assert not csv_file.exists()

    def test_no_positions_prints_nothing_to_recover(self, tmp_path, monkeypatch, capsys):
        """When Bitget returns no positions and state is empty, prints nothing-to-recover."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        import state as st

        state_file = tmp_path / "state.json"
        csv_file   = tmp_path / "trades.csv"
        _write_state(state_file, [])
        monkeypatch.setattr(st, "STATE_FILE", str(state_file))
        monkeypatch.setattr(tr, "get_all_open_positions", lambda: {})

        recover.STATE_FILE = str(state_file)
        recover.TRADE_LOG  = str(csv_file)
        recover.main([])

        out = capsys.readouterr().out
        assert "nothing" in out.lower() or "0 position" in out.lower()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
python -m pytest tests/test_recover_cli.py::TestMainTwoPasses -v 2>&1 | head -40
```

Expected: failures — `main()` still reads `state.json` as source of truth instead of Bitget.

- [ ] **Step 3: Rewrite `main()` in `recover.py`**

Replace the existing `main()` function (from `def main(args=None):` to the end of the file, excluding `if __name__ == "__main__":`) with:

```python
def main(args=None):
    parser = argparse.ArgumentParser(
        description="Reconcile all Bitget positions against state.json and trades.csv."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would change without writing to disk.")
    parser.add_argument("--symbols", nargs="+", metavar="SYM",
                        help="Limit Pass 1 to specific symbols.")
    parsed = parser.parse_args(args)

    mode = "[DRY RUN] " if parsed.dry_run else ""

    # ── Fetch live positions from Bitget ─────────────────────────── #
    exchange_positions = tr.get_all_open_positions()
    n = len(exchange_positions)
    print(f"Fetched {n} position(s) from Bitget.")

    # Apply --symbols filter to Pass 1
    pass1_symbols = {
        sym: pos for sym, pos in exchange_positions.items()
        if not parsed.symbols or sym in parsed.symbols
    }

    if not pass1_symbols and not st.get_open_trades():
        print("Nothing to recover.")
        return

    skipped = patched = recovered = warnings = 0
    results = []

    # ── Pass 1: Exchange → State ──────────────────────────────────── #
    print(f"\nPass 1 — Exchange → State:")

    for sym, pos in pass1_symbols.items():
        try:
            csv_row   = _get_open_csv_row(TRADE_LOG, sym)
            state_ent = st.get_open_trade(sym)

            if csv_row is not None and _is_valid_sltp(csv_row.get("sl"), csv_row.get("tp")):
                # SKIP
                skipped += 1
                print(f"  {sym:<16s}  SKIP        (CSV + SL/TP intact)")
                results.append({"symbol": sym, "action": "SKIP"})

            elif csv_row is not None:
                # PATCH_SLTP — open row exists but SL/TP missing/bad
                r = _patch_sltp(
                    sym,
                    state_ent or {"strategy": csv_row.get("strategy", "UNKNOWN"),
                                  "opened_at": csv_row.get("timestamp")},
                    pos,
                    STATE_FILE, TRADE_LOG,
                    dry_run=parsed.dry_run,
                )
                patched += 1
                label = f"{mode}PATCH_SLTP  sl={r['sl']:.5f}  tp={r['tp']:.5f}"
                print(f"  {sym:<16s}  {label}")
                results.append(r)

            else:
                # FULL_RECOVERY — no CSV open row
                r = _full_recovery(
                    sym, pos,
                    STATE_FILE, TRADE_LOG,
                    dry_run=parsed.dry_run,
                )
                recovered += 1
                label = (
                    f"{mode}FULL        "
                    f"trade_id={r['trade_id']}  entry={r['entry']:.5f}  "
                    f"sl={r['sl']:.5f}  tp={r['tp']:.5f}"
                )
                print(f"  {sym:<16s}  {label}")
                results.append(r)

        except Exception as e:
            print(f"  {sym:<16s}  ERROR: {e}")
            logger.warning(f"[{sym}] recover main pass1 failed: {e}")

    # ── Pass 2: State → Exchange ──────────────────────────────────── #
    state_trades = st.get_open_trades()
    orphans = [t for t in state_trades if t["symbol"] not in exchange_positions]

    if orphans:
        print(f"\nPass 2 — State → Exchange:")
        for t in orphans:
            sym = t["symbol"]
            warnings += 1
            print(f"  {sym:<16s}  ⚠ WARNING: in state.json but NOT on Bitget — restart bot to close")

    # ── Summary ───────────────────────────────────────────────────── #
    print(f"\nSummary: {skipped} skipped, {patched} patched, {recovered} fully recovered"
          + (f", {warnings} warning(s)" if warnings else "") + ".")

    if patched + recovered > 0:
        if parsed.dry_run:
            print(f"\n[DRY RUN] No files were written.")
        else:
            print(f"\n⚠  tpsl_set=False for recovered/patched positions. "
                  f"Manually set SL/TP on Bitget, or restart the bot.")
```

- [ ] **Step 4: Run all recover tests — expect pass**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
python -m pytest tests/test_recover_cli.py -v 2>&1 | tail -30
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
git add recover.py tests/test_recover_cli.py
git commit -m "feat(recover): rewrite main() with two-pass Bitget-sourced reconciliation"
```

---

### Task 5: Full test suite + DEPENDENCIES.md update

Verify nothing is broken across the suite and document the new dependency.

**Files:**
- Modify: `docs/DEPENDENCIES.md`

- [ ] **Step 1: Run full test suite**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
python -m pytest tests/ -x -q 2>&1 | tail -30
```

Expected: all tests pass. If any fail, fix the root cause before proceeding.

- [ ] **Step 2: Add `recover.py` section to DEPENDENCIES.md**

In `docs/DEPENDENCIES.md`, add a new section after Section 3 (Bot-Specific Files) or append to Section 8 (External Tool Dependencies). Add:

```markdown
### recover.py

**Purpose:** Manual CLI tool to reconcile all live Bitget positions against `state.json`
and `trades.csv`. Treats `tr.get_all_open_positions()` as source of truth.

**Calls:**
- `trader.get_all_open_positions()` — live exchange positions (Pass 1 source of truth)
- `state.get_open_trade(sym)` — read single state entry
- `state.get_open_trades()` — full list for Pass 2 orphan scan
- `state._read()` / `state._write()` — direct state patch (same pattern as bot.py)
- `startup_recovery.fetch_candles_at()` — historical candles for S5 OB recovery
- `startup_recovery.estimate_sl_tp()` — fallback SL/TP for non-S5 strategies
- `startup_recovery.attempt_s5_recovery()` — S5 OB recovery path
- `snapshot.save_snapshot()` — open snapshot for FULL_RECOVERY case
- `config.TRADE_LOG` — trades.csv path

**Does NOT affect:**
- `ig_bot.py`, `ig_state.json` — Bitget-only tool
- `bot.py._startup_recovery()` — independent; recover.py is the manual equivalent

**Breaking scenarios:**
- Changing `tr.get_all_open_positions()` return dict structure → Pass 1 loop breaks
- Changing `state._read()` / `state._write()` → state patch fails
- Changing `startup_recovery.estimate_sl_tp()` return tuple → _patch_sltp and _full_recovery break
```

- [ ] **Step 3: Commit**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
git add docs/DEPENDENCIES.md
git commit -m "docs(deps): add recover.py dependency section"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Covered by |
|---|---|
| Exchange as source of truth | Task 4 — `main()` calls `tr.get_all_open_positions()` first |
| SKIP / PATCH_SLTP / FULL_RECOVERY classification | Task 1 (`_is_valid_sltp`), Task 2 (`_patch_sltp`), Task 3 (`_full_recovery`), Task 4 (`main`) |
| S5 OB recovery in PATCH_SLTP | Task 2, step 3 |
| `estimate_sl_tp` fallback for non-S5 | Task 2, step 3 |
| Pass 2 warns for state-only positions | Task 4, step 3 |
| `--dry-run` suppresses all writes | Tested in Task 1 (helpers no-op), Task 2, Task 3, Task 4 |
| `--symbols` filters Pass 1 only | Task 4, test + implementation |
| State writes use `st._read()`/`st._write()` | Task 2 and Task 3 implementations |
| No raw `json.loads/write` | Task 2 and Task 3 — only `st._read()`/`st._write()` used |
| `_patch_state()` removed | Task 3 — removed in step 3 |
| Output format with SKIP / PATCH_SLTP / FULL / WARNING lines | Task 4 implementation |
| Summary line | Task 4 implementation |
| DEPENDENCIES.md updated | Task 5 |

**No placeholders found.**

**Type consistency:** `_full_recovery` returns `{"action": "FULL", ...}` — tests check `result["action"] == "FULL"` ✓. `_patch_sltp` returns `{"action": "PATCH_SLTP", ...}` — tests check `result["action"] == "PATCH_SLTP"` ✓. All consistent.
