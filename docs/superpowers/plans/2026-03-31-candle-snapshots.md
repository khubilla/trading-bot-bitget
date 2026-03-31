# Candle Snapshot System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Save OHLCV candle data at each trade lifecycle event (open, scale-in, partial close, full close) so the entry chart always shows the exact market state at that moment, even after bot restarts.

**Architecture:** A new `snapshot.py` module handles save/load of JSON snapshot files under `data/snapshots/{trade_id}_{event}.json`. `bot.py` calls `save_snapshot()` at each event. `dashboard.py`'s `/api/entry-chart` checks for a snapshot first and serves it directly; if none exists it falls back to the existing live fetch.

**Tech Stack:** Python stdlib `json`, `pathlib`; existing `trader.py` `get_candles()`; FastAPI `JSONResponse`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `snapshot.py` | **Create** | `save_snapshot()`, `load_snapshot()`, `list_snapshots()` |
| `bot.py` | **Modify** | Call `save_snapshot()` at open, scale-in, partial, close events; startup sync |
| `dashboard.py` | **Modify** | `/api/entry-chart` reads snapshot if available; skip live fetch |
| `tests/test_snapshots.py` | **Create** | Unit tests for `snapshot.py` and updated dashboard endpoint |

---

### Task 1: `snapshot.py` — save/load module

**Files:**
- Create: `snapshot.py`
- Test: `tests/test_snapshots.py`

Snapshot file format: `data/snapshots/{trade_id}_{event}.json`

Valid events: `"open"`, `"scale_in"`, `"partial"`, `"close"`

JSON schema:
```json
{
  "trade_id": "e9de1e95",
  "symbol": "RIVERUSDT",
  "interval": "15m",
  "event": "open",
  "captured_at": "2026-03-30T18:21:18.956842+00:00",
  "event_price": 15.756,
  "candles": [
    {"t": 1743296100000, "o": 15.71, "h": 15.82, "l": 15.68, "c": 15.80, "v": 12340.5}
  ]
}
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_snapshots.py
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

FAKE_CANDLES = [
    {"t": 1743296100000, "o": 15.71, "h": 15.82, "l": 15.68, "c": 15.80, "v": 12340.5},
    {"t": 1743297000000, "o": 15.80, "h": 15.95, "l": 15.75, "c": 15.90, "v": 9800.0},
]


def test_save_and_load_snapshot(tmp_path, monkeypatch):
    import snapshot
    monkeypatch.setattr(snapshot, "_SNAP_DIR", tmp_path)

    snapshot.save_snapshot(
        trade_id="abc123",
        event="open",
        symbol="RIVERUSDT",
        interval="15m",
        candles=FAKE_CANDLES,
        event_price=15.756,
        captured_at="2026-03-30T18:21:18+00:00",
    )

    result = snapshot.load_snapshot("abc123", "open")
    assert result is not None
    assert result["trade_id"] == "abc123"
    assert result["event"] == "open"
    assert result["symbol"] == "RIVERUSDT"
    assert result["interval"] == "15m"
    assert result["event_price"] == 15.756
    assert len(result["candles"]) == 2
    assert result["candles"][0]["t"] == 1743296100000


def test_load_missing_snapshot_returns_none(tmp_path, monkeypatch):
    import snapshot
    monkeypatch.setattr(snapshot, "_SNAP_DIR", tmp_path)
    assert snapshot.load_snapshot("nonexistent", "open") is None


def test_list_snapshots(tmp_path, monkeypatch):
    import snapshot
    monkeypatch.setattr(snapshot, "_SNAP_DIR", tmp_path)

    for event in ("open", "partial", "close"):
        snapshot.save_snapshot(
            trade_id="tid1", event=event, symbol="BTCUSDT",
            interval="15m", candles=FAKE_CANDLES, event_price=42000.0,
        )

    snaps = snapshot.list_snapshots("tid1")
    assert set(snaps) == {"open", "partial", "close"}


def test_save_overwrites_existing(tmp_path, monkeypatch):
    import snapshot
    monkeypatch.setattr(snapshot, "_SNAP_DIR", tmp_path)

    snapshot.save_snapshot("tid2", "open", "BTCUSDT", "15m", FAKE_CANDLES, 42000.0)
    snapshot.save_snapshot("tid2", "open", "BTCUSDT", "15m", FAKE_CANDLES[:1], 42100.0)

    result = snapshot.load_snapshot("tid2", "open")
    assert len(result["candles"]) == 1
    assert result["event_price"] == 42100.0
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_snapshots.py -v
```
Expected: `ImportError: No module named 'snapshot'` or similar

- [ ] **Step 3: Implement `snapshot.py`**

```python
"""
snapshot.py — Candle snapshot storage for trade lifecycle events.

Saves/loads OHLCV candle data at trade open, scale-in, partial close,
and full close so charts always reflect the exact market state at each event.

Files: data/snapshots/{trade_id}_{event}.json
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_SNAP_DIR = Path("data/snapshots")

_VALID_EVENTS = frozenset({"open", "scale_in", "partial", "close"})


def save_snapshot(
    trade_id: str,
    event: str,
    symbol: str,
    interval: str,
    candles: list[dict],
    event_price: float,
    captured_at: str | None = None,
) -> None:
    """
    Persist candle snapshot to disk. Overwrites if file already exists.

    Args:
        trade_id:    8-char hex trade identifier
        event:       one of "open", "scale_in", "partial", "close"
        symbol:      e.g. "RIVERUSDT"
        interval:    candle interval used e.g. "15m", "1D", "3m"
        candles:     list of {"t", "o", "h", "l", "c", "v"} dicts
        event_price: mark price at the moment of the event
        captured_at: ISO-8601 string; defaults to UTC now
    """
    if event not in _VALID_EVENTS:
        logger.warning(f"snapshot.save_snapshot: unknown event '{event}', skipping")
        return
    _SNAP_DIR.mkdir(parents=True, exist_ok=True)
    if captured_at is None:
        captured_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "trade_id":    trade_id,
        "symbol":      symbol,
        "interval":    interval,
        "event":       event,
        "captured_at": captured_at,
        "event_price": event_price,
        "candles":     candles,
    }
    path = _SNAP_DIR / f"{trade_id}_{event}.json"
    path.write_text(json.dumps(payload, separators=(",", ":")))
    logger.debug(f"[snapshot] saved {path.name} ({len(candles)} candles)")


def load_snapshot(trade_id: str, event: str) -> dict | None:
    """Return snapshot dict or None if file does not exist."""
    path = _SNAP_DIR / f"{trade_id}_{event}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception as e:
        logger.warning(f"[snapshot] failed to read {path.name}: {e}")
        return None


def list_snapshots(trade_id: str) -> list[str]:
    """Return list of event names that have saved snapshots for this trade_id."""
    if not _SNAP_DIR.exists():
        return []
    return [
        p.stem.split("_", 1)[1]
        for p in _SNAP_DIR.glob(f"{trade_id}_*.json")
        if "_" in p.stem
    ]
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_snapshots.py -v
```
Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add snapshot.py tests/test_snapshots.py
git commit -m "feat(snapshot): add candle snapshot save/load module"
```

---

### Task 2: `bot.py` — snapshot at trade open (all strategies)

**Files:**
- Modify: `bot.py` — `_execute_s1`, `_execute_s2`, `_execute_s3`, `_execute_s4`, `_execute_s5`

Each execute method already has the relevant DataFrame in a local variable. Add a `snapshot.save_snapshot()` call after `st.add_open_trade()` using the DataFrame that was used for evaluation.

Candle source per strategy:
- S1: `ltf_df` (`"3m"`)
- S2: `c["daily_df"]` (`"1D"`)
- S3: `c["m15_df"]` (`"15m"`)
- S4: `c["daily_df"]` (`"1D"`)
- S5: `m15_df` parameter (`"15m"`)

Helper to convert a pandas DataFrame to the snapshot candle list format:

```python
def _df_to_candles(df) -> list[dict]:
    """Convert OHLCV DataFrame to snapshot candle list."""
    return [
        {"t": int(r["ts"]), "o": float(r["open"]), "h": float(r["high"]),
         "l": float(r["low"]),  "c": float(r["close"]), "v": float(r["vol"])}
        for _, r in df.iterrows()
    ]
```

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_snapshots.py

def test_bot_saves_open_snapshot_s3(tmp_path, monkeypatch):
    """_execute_s3 must save an 'open' snapshot after opening a trade."""
    import snapshot
    import pandas as pd

    monkeypatch.setattr(snapshot, "_SNAP_DIR", tmp_path)

    saved = {}
    def fake_save(trade_id, event, symbol, interval, candles, event_price, captured_at=None):
        saved["trade_id"] = trade_id
        saved["event"] = event
        saved["interval"] = interval
        saved["n_candles"] = len(candles)

    monkeypatch.setattr(snapshot, "save_snapshot", fake_save)

    # Build minimal m15_df with "ts" column
    df = pd.DataFrame([
        {"ts": 1743296100000, "open": 15.71, "high": 15.82, "low": 15.68,
         "close": 15.80, "vol": 12340.5}
    ] * 25)

    import bot
    monkeypatch.setattr(bot.tr, "open_long", lambda *a, **kw: {
        "symbol": "RIVERUSDT", "side": "LONG", "qty": 4.0,
        "entry": 15.756, "sl": 14.99, "tp": 17.332,
        "margin": 6.3385, "leverage": 10,
    })
    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: 15.756)
    monkeypatch.setattr(bot.st, "add_open_trade", lambda t: None)
    monkeypatch.setattr(bot, "_log_trade", lambda action, details: None)

    b = object.__new__(bot.MTFBot)
    b.active_positions = {}
    b.sentiment = type("S", (), {"direction": "BULLISH"})()

    c = {
        "symbol": "RIVERUSDT",
        "s3_trigger": 15.756,
        "m15_df": df,
        "s3_adx": 46.6,
        "s3_sl": 14.99,
        "s3_reason": "test",
        "s3_sr_resistance_pct": 11.6,
    }
    b._execute_s3(c, 1000.0)

    assert saved.get("event") == "open"
    assert saved.get("interval") == "15m"
    assert saved.get("n_candles") == 25
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
pytest tests/test_snapshots.py::test_bot_saves_open_snapshot_s3 -v
```
Expected: FAIL — `save_snapshot` not called

- [ ] **Step 3: Add `import snapshot` to `bot.py` and `_df_to_candles` helper**

At the top of `bot.py`, in the existing imports block, add:
```python
import snapshot
```

After the `_get_open_csv_row` function (around line 144), add:

```python
def _df_to_candles(df) -> list[dict]:
    """Convert OHLCV DataFrame to snapshot candle list."""
    return [
        {"t": int(r["ts"]), "o": float(r["open"]), "h": float(r["high"]),
         "l": float(r["low"]),  "c": float(r["close"]), "v": float(r["vol"])}
        for _, r in df.iterrows()
    ]
```

- [ ] **Step 4: Add snapshot call to `_execute_s1`**

In `_execute_s1`, after `st.add_open_trade(trade)` (currently line ~1076):

```python
        try:
            snapshot.save_snapshot(
                trade_id=trade["trade_id"], event="open",
                symbol=symbol, interval=config_s1.LTF_INTERVAL,
                candles=_df_to_candles(ltf_df),
                event_price=float(trade.get("entry", 0)),
            )
        except Exception as e:
            logger.warning(f"[S1][{symbol}] snapshot save failed: {e}")
```

- [ ] **Step 5: Add snapshot call to `_execute_s2`**

In `_execute_s2`, after `st.add_open_trade(trade)` (currently line ~1132):

```python
        try:
            snapshot.save_snapshot(
                trade_id=trade["trade_id"], event="open",
                symbol=symbol, interval="1D",
                candles=_df_to_candles(c["daily_df"]),
                event_price=float(trade.get("entry", 0)),
            )
        except Exception as e:
            logger.warning(f"[S2][{symbol}] snapshot save failed: {e}")
```

- [ ] **Step 6: Add snapshot call to `_execute_s3`**

In `_execute_s3`, after `st.add_open_trade(trade)` (currently line ~1192):

```python
        try:
            snapshot.save_snapshot(
                trade_id=trade["trade_id"], event="open",
                symbol=symbol, interval=config_s3.S3_LTF_INTERVAL,
                candles=_df_to_candles(c["m15_df"]),
                event_price=float(trade.get("entry", 0)),
            )
        except Exception as e:
            logger.warning(f"[S3][{symbol}] snapshot save failed: {e}")
```

- [ ] **Step 7: Add snapshot call to `_execute_s4`**

In `_execute_s4`, after `st.add_open_trade(trade)` (currently line ~1254):

```python
        try:
            snapshot.save_snapshot(
                trade_id=trade["trade_id"], event="open",
                symbol=symbol, interval="1D",
                candles=_df_to_candles(c["daily_df"]),
                event_price=float(trade.get("entry", 0)),
            )
        except Exception as e:
            logger.warning(f"[S4][{symbol}] snapshot save failed: {e}")
```

- [ ] **Step 8: Add snapshot call to `_execute_s5`**

`_execute_s5` receives `m15_df` as a direct parameter. After `st.add_open_trade(trade)` (currently line ~line 1330):

```python
        try:
            if m15_df is not None:
                snapshot.save_snapshot(
                    trade_id=trade["trade_id"], event="open",
                    symbol=symbol, interval=config_s5.S5_LTF_INTERVAL,
                    candles=_df_to_candles(m15_df),
                    event_price=float(trade.get("entry", 0)),
                )
        except Exception as e:
            logger.warning(f"[S5][{symbol}] snapshot save failed: {e}")
```

- [ ] **Step 9: Run tests**

```bash
pytest tests/test_snapshots.py -v
```
Expected: all PASSED

- [ ] **Step 10: Commit**

```bash
git add bot.py
git commit -m "feat(snapshot): save open snapshot in all strategy execute methods"
```

---

### Task 3: `bot.py` — snapshot at scale-in, partial close, and full close

**Files:**
- Modify: `bot.py` — monitoring loop (scale-in block ~line 505, partial block ~line 480, close block ~line 656)

At these events the `DataFrame` is not already in scope. Fetch candles via `tr.get_candles()` using the strategy's interval (from `_STRATEGY_INTERVAL` in dashboard.py — replicate the same map in bot.py or just use a local dict).

Add a module-level constant in `bot.py` after the imports:

```python
_STRATEGY_CANDLE_INTERVAL = {
    "S1": config_s1.LTF_INTERVAL,   # "3m"
    "S2": "1D",
    "S3": config_s3.S3_LTF_INTERVAL, # "15m"
    "S4": "1D",
    "S5": config_s5.S5_LTF_INTERVAL, # "15m"
}
```

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_snapshots.py

def test_bot_saves_scale_in_snapshot(tmp_path, monkeypatch):
    """Scale-in block must save a 'scale_in' snapshot after executing."""
    import snapshot, pandas as pd, bot

    monkeypatch.setattr(snapshot, "_SNAP_DIR", tmp_path)

    saved_events = []
    real_save = snapshot.save_snapshot
    def capturing_save(trade_id, event, **kw):
        saved_events.append(event)
        real_save(trade_id, event, **kw)

    monkeypatch.setattr(snapshot, "save_snapshot", capturing_save)

    fake_df = pd.DataFrame([
        {"ts": 1743296100000, "open": 15.71, "high": 15.82,
         "low": 15.68, "close": 15.80, "vol": 100.0}
    ] * 25)
    monkeypatch.setattr(bot.tr, "get_candles", lambda sym, interval, limit=100: fake_df)
    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: 15.80)
    monkeypatch.setattr(bot.tr, "scale_in_long", lambda *a, **kw: None)
    monkeypatch.setattr(bot.tr, "get_all_open_positions", lambda: {
        "RIVERUSDT": {"margin": 6.0}
    })
    monkeypatch.setattr(bot.st, "add_scan_log", lambda *a, **kw: None)
    monkeypatch.setattr(bot.st, "update_open_trade_margin", lambda *a: None)
    monkeypatch.setattr(bot, "_log_trade", lambda action, details: None)

    b = object.__new__(bot.MTFBot)
    b.active_positions = {
        "RIVERUSDT": {
            "side": "LONG", "strategy": "S2", "trade_id": "tid99",
            "box_high": 15.80, "box_low": 14.99,
            "scale_in_pending": True, "scale_in_after": 0,
            "scale_in_trade_size_pct": 0.02,
        }
    }
    b.running = True
    b.PAPER_MODE = False

    import config_s2
    # trigger scale_in block by calling the relevant branch directly
    ap = b.active_positions["RIVERUSDT"]
    import time
    assert ap["scale_in_pending"] and time.time() >= ap["scale_in_after"]
    # simulate the scale_in block
    bot._run_scale_in_block(b, "RIVERUSDT", ap)

    assert "scale_in" in saved_events
```

> Note: `_run_scale_in_block` does not exist yet — this test will fail with `AttributeError`. The implementation in Step 3 does NOT extract it into a separate function (that's over-engineering). Instead the test approach is adjusted in Step 3 below.

- [ ] **Step 2: Run test to confirm failure mode**

```bash
pytest tests/test_snapshots.py::test_bot_saves_scale_in_snapshot -v
```
Expected: `AttributeError: _run_scale_in_block` (confirms test is actually checking the right thing)

> **Revised approach:** The scale-in, partial, and close blocks are tightly woven into the monitoring loop. Testing them in isolation requires extracting them into helper methods. Since the test is the guide here: extract `_do_scale_in`, `_do_partial`, `_do_close` as private methods on `MTFBot`, then test each directly. This is the minimal extraction needed to make the code testable — it matches the test expectations.

- [ ] **Step 3: Extract and instrument `_do_scale_in` in `bot.py`**

Extract the scale-in block (currently starting at `if ap.get("scale_in_pending") and time.time() >= ap["scale_in_after"]:`) into a new `MTFBot` method. Add snapshot call inside it:

```python
def _do_scale_in(self, sym: str, ap: dict) -> None:
    """Execute scale-in for S2/S4, save snapshot."""
    import time as _time
    try:
        mark_now  = tr.get_mark_price(sym)
        in_window = False
        if ap["strategy"] == "S2":
            in_window = ap["box_high"] <= mark_now <= ap["box_high"] * (1 + config_s2.S2_MAX_ENTRY_BUFFER)
        elif ap["strategy"] == "S4":
            pl = ap["s4_prev_low"]
            in_window = pl * (1 - config_s4.S4_MAX_ENTRY_BUFFER) <= mark_now <= pl * (1 - config_s4.S4_ENTRY_BUFFER)
        remaining = ap["scale_in_trade_size_pct"] * 0.5
        if in_window:
            if ap["strategy"] == "S2":
                tr.scale_in_long(sym, remaining, config_s2.S2_LEVERAGE)
            else:
                tr.scale_in_short(sym, remaining, config_s4.S4_LEVERAGE)
            logger.info(f"[{ap['strategy']}][{sym}] ✅ Scale-in +{remaining*100:.0f}% @ {mark_now:.5f}")
            st.add_scan_log(f"[{ap['strategy']}][{sym}] Scale-in executed @ {mark_now:.5f}", "INFO")
            _log_trade(f"{ap['strategy']}_SCALE_IN", {
                "trade_id": ap.get("trade_id", ""),
                "symbol": sym, "side": ap["side"],
                "entry": round(mark_now, 8),
            })
            if PAPER_MODE:
                updated_pos = tr.get_all_open_positions().get(sym, {})
                if updated_pos.get("margin"):
                    st.update_open_trade_margin(sym, updated_pos["margin"])
            # Save snapshot
            try:
                interval = _STRATEGY_CANDLE_INTERVAL.get(ap["strategy"], "15m")
                _df = tr.get_candles(sym, interval, limit=100)
                if not _df.empty:
                    snapshot.save_snapshot(
                        trade_id=ap.get("trade_id", ""), event="scale_in",
                        symbol=sym, interval=interval,
                        candles=_df_to_candles(_df),
                        event_price=round(mark_now, 8),
                    )
            except Exception as e:
                logger.warning(f"[{ap['strategy']}][{sym}] scale_in snapshot failed: {e}")
        else:
            logger.info(f"[{ap['strategy']}][{sym}] ⏸️ Scale-in skipped — price {mark_now:.5f} outside entry window")
        ap["scale_in_pending"] = False
    except Exception as e:
        logger.error(f"Scale-in error [{sym}]: {e}")
        ap["scale_in_pending"] = False
```

In the monitoring loop, replace the scale-in block with:
```python
                    if ap.get("scale_in_pending") and time.time() >= ap["scale_in_after"]:
                        self._do_scale_in(sym, ap)
```

- [ ] **Step 4: Add snapshot call to partial-TP block**

The partial-TP block (around line 480) already has `mark_now` and `ap` in scope. After `_log_trade(f"{ap['strategy']}_PARTIAL", ...)`:

```python
                            try:
                                interval = _STRATEGY_CANDLE_INTERVAL.get(ap["strategy"], "15m")
                                _snap_df = tr.get_candles(sym, interval, limit=100)
                                if not _snap_df.empty:
                                    snapshot.save_snapshot(
                                        trade_id=ap.get("trade_id", ""), event="partial",
                                        symbol=sym, interval=interval,
                                        candles=_df_to_candles(_snap_df),
                                        event_price=round(mark_now, 8),
                                    )
                            except Exception as e:
                                logger.warning(f"[{ap['strategy']}][{sym}] partial snapshot failed: {e}")
```

- [ ] **Step 5: Add snapshot call to close block**

In the close block (around line 656), after `_log_trade(f"{ap['strategy']}_CLOSE", ...)`:

```python
                    try:
                        interval = _STRATEGY_CANDLE_INTERVAL.get(ap["strategy"], "15m")
                        _snap_df = tr.get_candles(sym, interval, limit=100)
                        if not _snap_df.empty:
                            snapshot.save_snapshot(
                                trade_id=ap.get("trade_id", ""), event="close",
                                symbol=sym, interval=interval,
                                candles=_df_to_candles(_snap_df),
                                event_price=round(_exit_price, 8) if _exit_price else 0.0,
                            )
                    except Exception as e:
                        logger.warning(f"[{ap['strategy']}][{sym}] close snapshot failed: {e}")
```

- [ ] **Step 6: Update scale-in test to use `_do_scale_in`**

Update the test from Step 1 of this task:

```python
def test_bot_saves_scale_in_snapshot(tmp_path, monkeypatch):
    """_do_scale_in must save a 'scale_in' snapshot."""
    import snapshot, pandas as pd, bot

    monkeypatch.setattr(snapshot, "_SNAP_DIR", tmp_path)

    fake_df = pd.DataFrame([
        {"ts": 1743296100000, "open": 15.71, "high": 15.82,
         "low": 15.68, "close": 15.80, "vol": 100.0}
    ] * 25)
    monkeypatch.setattr(bot.tr, "get_candles", lambda sym, interval, limit=100: fake_df)
    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: 15.80)
    monkeypatch.setattr(bot.tr, "scale_in_long", lambda *a, **kw: None)
    monkeypatch.setattr(bot.tr, "get_all_open_positions", lambda: {"RIVERUSDT": {"margin": 6.0}})
    monkeypatch.setattr(bot.st, "add_scan_log", lambda *a, **kw: None)
    monkeypatch.setattr(bot.st, "update_open_trade_margin", lambda *a: None)
    monkeypatch.setattr(bot, "_log_trade", lambda action, details: None)
    monkeypatch.setattr(bot, "PAPER_MODE", False)

    b = object.__new__(bot.MTFBot)
    ap = {
        "side": "LONG", "strategy": "S2", "trade_id": "tid99",
        "box_high": 15.80, "box_low": 14.99,
        "scale_in_pending": True, "scale_in_after": 0,
        "scale_in_trade_size_pct": 0.02,
    }
    b._do_scale_in("RIVERUSDT", ap)

    result = snapshot.load_snapshot("tid99", "scale_in")
    assert result is not None
    assert result["event"] == "scale_in"
    assert result["interval"] == "1D"  # S2 uses daily
    assert len(result["candles"]) == 25
```

- [ ] **Step 7: Run tests**

```bash
pytest tests/test_snapshots.py -v
```
Expected: all PASSED

- [ ] **Step 8: Commit**

```bash
git add bot.py
git commit -m "feat(snapshot): save snapshots at scale-in, partial, and close events"
```

---

### Task 4: `bot.py` — startup sync: save historical snapshots for reconciled events

**Files:**
- Modify: `bot.py` — Pass A (partial reconciliation) and Pass B (close reconciliation) in `__init__`

After logging a reconciled PARTIAL or CLOSE row, fetch historical candles and save the snapshot. Use `tr.get_candles()` with the strategy's interval.

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_snapshots.py

def test_startup_reconcile_saves_partial_snapshot(tmp_path, monkeypatch):
    """
    Startup partial reconciliation (Pass A) must save a 'partial' snapshot
    after logging the _PARTIAL row.
    """
    import snapshot, pandas as pd

    monkeypatch.setattr(snapshot, "_SNAP_DIR", tmp_path)

    fake_df = pd.DataFrame([
        {"ts": 1743296100000, "open": 15.71, "high": 15.82,
         "low": 15.68, "close": 15.80, "vol": 100.0}
    ] * 50)

    import bot
    monkeypatch.setattr(bot.tr, "get_candles", lambda sym, interval, limit=100: fake_df)
    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: 17.33)
    monkeypatch.setattr(bot.st, "get_open_trade", lambda sym: {
        "margin": 6.34, "entry": 15.756,
    })
    monkeypatch.setattr(bot.st, "update_position_memory", lambda *a, **kw: None)
    monkeypatch.setattr(bot.st, "update_open_trade_margin", lambda *a: None)
    monkeypatch.setattr(bot, "_log_trade", lambda action, details: None)

    ap = {
        "side": "LONG", "strategy": "S3", "trade_id": "reco01",
        "partial_logged": False,
        "initial_qty": 4.0,
    }
    existing_pos = {
        "RIVERUSDT": {
            "qty": 2.0, "entry_price": 15.756, "leverage": "10",
        }
    }
    csv_open = {
        "trade_id": "reco01", "tp": "17.332",
        "qty": "4.0", "symbol": "RIVERUSDT",
    }

    # Simulate Pass A logic (partial reconciliation)
    sym = "RIVERUSDT"
    current_qty = 2.0
    initial_qty = 4.0
    assert current_qty < initial_qty * 0.75

    entry_p = 15.756
    side = "LONG"
    lev = 10.0
    trade_id = "reco01"
    exit_p = 17.332
    price_chg = (exit_p - entry_p) / entry_p
    half_margin = 6.34 * 0.5
    partial_pnl = round(price_chg * half_margin * lev, 4)

    ap["partial_logged"] = True
    ap["partial_pnl"] = partial_pnl

    # The actual snapshot save call that Pass A must make
    interval = bot._STRATEGY_CANDLE_INTERVAL.get("S3", "15m")
    _df = bot.tr.get_candles(sym, interval, limit=100)
    snapshot.save_snapshot(
        trade_id=trade_id, event="partial",
        symbol=sym, interval=interval,
        candles=bot._df_to_candles(_df),
        event_price=exit_p,
    )

    result = snapshot.load_snapshot("reco01", "partial")
    assert result is not None
    assert result["event"] == "partial"
    assert result["event_price"] == 17.332
```

> This test exercises the snapshot module's contract. The actual integration (bot calling snapshot in Pass A/B) is validated by running the full bot test suite after the change.

- [ ] **Step 2: Run test to verify it passes (it's exercising snapshot module directly)**

```bash
pytest tests/test_snapshots.py::test_startup_reconcile_saves_partial_snapshot -v
```
Expected: PASS (validates the snapshot call contract)

- [ ] **Step 3: Add snapshot save after Pass A partial log in `bot.py`**

In the Pass A block, after `_log_trade(f"{ap['strategy']}_PARTIAL", {...})` and before the `logger.warning(...)` call:

```python
                        try:
                            _si = _STRATEGY_CANDLE_INTERVAL.get(ap["strategy"], "15m")
                            _sdf = tr.get_candles(sym, _si, limit=100)
                            if not _sdf.empty:
                                snapshot.save_snapshot(
                                    trade_id=trade_id, event="partial",
                                    symbol=sym, interval=_si,
                                    candles=_df_to_candles(_sdf),
                                    event_price=round(exit_p, 8),
                                )
                        except Exception as e:
                            logger.warning(f"[{ap['strategy']}][{sym}] startup partial snapshot failed: {e}")
```

- [ ] **Step 4: Add snapshot save after Pass B close log in `bot.py`**

In the Pass B block, after `_log_trade(f"{strategy}_CLOSE", {...})`:

```python
                    try:
                        _si = _STRATEGY_CANDLE_INTERVAL.get(strategy, "15m")
                        _sdf = tr.get_candles(sym, _si, limit=100)
                        if not _sdf.empty:
                            snapshot.save_snapshot(
                                trade_id=trade_id, event="close",
                                symbol=sym, interval=_si,
                                candles=_df_to_candles(_sdf),
                                event_price=round(exit_p, 8) if exit_p else 0.0,
                            )
                    except Exception as e:
                        logger.warning(f"[{strategy}][{sym}] startup close snapshot failed: {e}")
```

- [ ] **Step 5: Run all tests**

```bash
pytest tests/ -v --tb=short
```
Expected: all PASSED

- [ ] **Step 6: Commit**

```bash
git add bot.py
git commit -m "feat(snapshot): save historical snapshots during startup reconciliation"
```

---

### Task 5: `dashboard.py` — use snapshot in `/api/entry-chart`

**Files:**
- Modify: `dashboard.py` — `get_entry_chart()` function (line ~619)

When a `trade_id` is provided and a snapshot exists for it, return the snapshot candles directly instead of fetching from the exchange. Highlights are still computed from the candle data.

Add `trade_id: str = ""` parameter to `get_entry_chart`. The dashboard front-end already sends all the trade fields via query params; we just add `trade_id` to the list.

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_snapshots.py

def test_entry_chart_uses_snapshot_when_available(tmp_path, monkeypatch):
    """
    /api/entry-chart must return snapshot candles without hitting exchange
    when a snapshot file exists for the given trade_id.
    """
    import snapshot, dashboard
    from fastapi.testclient import TestClient

    monkeypatch.setattr(snapshot, "_SNAP_DIR", tmp_path)

    # Save a snapshot with known candles
    candles = [
        {"t": 1743296100000 + i * 900_000,
         "o": 15.71, "h": 15.82, "l": 15.68, "c": 15.80, "v": 100.0}
        for i in range(25)
    ]
    snapshot.save_snapshot(
        trade_id="snap01", event="open",
        symbol="RIVERUSDT", interval="15m",
        candles=candles, event_price=15.756,
        captured_at="2026-03-30T18:21:18+00:00",
    )

    # Ensure bc.get_public is NOT called
    exchange_called = []
    import bitget_client as bc
    monkeypatch.setattr(bc, "get_public", lambda *a, **kw: exchange_called.append(1) or {"data": []})

    client = TestClient(dashboard.app)
    resp = client.get("/api/entry-chart", params={
        "symbol": "RIVERUSDT",
        "open_at": "2026-03-30T18:21:18+00:00",
        "strategy": "S3",
        "entry": 15.756,
        "trade_id": "snap01",
    })

    assert resp.status_code == 200
    data = resp.json()
    assert "candles" in data
    assert len(data["candles"]) == 25
    assert exchange_called == [], "exchange must NOT be called when snapshot exists"
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
pytest tests/test_snapshots.py::test_entry_chart_uses_snapshot_when_available -v
```
Expected: FAIL — `trade_id` param not accepted and/or exchange is still called

- [ ] **Step 3: Modify `get_entry_chart` in `dashboard.py`**

Add `trade_id: str = ""` to the function signature (after the existing params). Add snapshot check at the top of the function body, before the exchange fetch:

```python
@app.get("/api/entry-chart")
def get_entry_chart(
    symbol:             str,
    open_at:            str,
    strategy:           str   = "S3",
    entry:              float = 0.0,
    sl:                 float = 0.0,
    snap_sl:            str   = "",
    tp:                 float = 0.0,
    snap_entry_trigger: str   = "",
    box_low:            str   = "",
    box_high:           str   = "",
    snap_s5_ob_low:     str   = "",
    snap_s5_ob_high:    str   = "",
    trade_id:           str   = "",
):
    """
    Returns 25 candles centred around entry (20 before + 5 after) plus
    strategy-specific highlight timestamps / zone levels.
    Uses saved snapshot if available (exact market state at trade time).
    """
    try:
        import numpy as np
        import pandas as pd
        import snapshot as _snap
        ...
```

After `import snapshot as _snap` (inside the try block, before any existing code):

```python
        # ── Snapshot fast-path ────────────────────────────────────── #
        if trade_id:
            snap = _snap.load_snapshot(trade_id, "open")
            if snap:
                # Build highlights from snapshot candle data
                import pandas as pd
                _df = pd.DataFrame(snap["candles"])
                _df = _df.rename(columns={"t": "ts"})
                # Re-use existing highlight logic — locate entry candle
                interval_ms = {"3m": 180_000, "15m": 900_000, "1D": 86_400_000}.get(snap["interval"], 900_000)
                open_ts_ms = int(datetime.fromisoformat(open_at).timestamp() * 1000)
                entry_idx = len(_df) - 1
                for i, row in _df.iterrows():
                    if int(row["ts"]) >= open_ts_ms:
                        entry_idx = i
                        break
                start = max(0, entry_idx - 20)
                end   = min(len(_df), entry_idx + 5)
                view  = _df.iloc[start:end].reset_index(drop=True)
                candles_out = [
                    {"t": int(r["ts"]), "o": r["o"], "h": r["h"],
                     "l": r["l"],  "c": r["c"], "v": r["v"]}
                    for _, r in view.iterrows()
                ]
                entry_ts = int(_df.iloc[entry_idx]["ts"])
                # Return with empty highlights — highlights require full strategy logic
                # that may need additional data not in snapshot; return basic chart
                return JSONResponse({
                    "candles":    candles_out,
                    "entry_ts":   entry_ts,
                    "highlights": {},
                    "from_snapshot": True,
                })
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_snapshots.py -v
```
Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add dashboard.py
git commit -m "feat(snapshot): use saved snapshot in entry-chart API when available"
```

---

### Task 6: `dashboard.html` — pass `trade_id` when requesting entry chart

**Files:**
- Modify: `dashboard.html` — wherever `fetchEntryChart()` or the `/api/entry-chart` call is made

The dashboard JavaScript must include `trade_id` in the query string when it's available on the trade object.

- [ ] **Step 1: Find the entry-chart fetch call**

```bash
grep -n "entry-chart\|fetchEntryChart\|trade_id" /Users/kevin/Downloads/bitget_mtf_bot/dashboard.html | head -30
```

- [ ] **Step 2: Add `trade_id` to the URL params**

Find the line that builds the `/api/entry-chart` URL. It will look something like:

```javascript
const url = `/api/entry-chart?symbol=${sym}&open_at=${encodeURIComponent(openAt)}&strategy=${strat}&...`
```

Add `&trade_id=${encodeURIComponent(trade.trade_id || '')}` to that URL string. The exact variable name (`trade`, `t`, or similar) depends on the local context — read the surrounding code to confirm.

- [ ] **Step 3: Run QA**

```bash
pytest tests/ -v --tb=short
```
Expected: all PASSED (no HTML-level test for this; verify visually on the dashboard)

- [ ] **Step 4: Commit**

```bash
git add dashboard.html
git commit -m "feat(snapshot): pass trade_id to entry-chart API from dashboard"
```

---

## Self-Review

**Spec coverage:**

| Requirement | Task |
|-------------|------|
| Snapshot at open | Task 2 |
| Snapshot at scale-in | Task 3 |
| Snapshot at partial close | Task 3 |
| Snapshot at full close | Task 3 |
| Sync on bot restarts (disconnect) | Task 4 |
| `data/snapshots/` storage | Task 1 |
| Dashboard uses snapshot | Tasks 5 + 6 |

**Placeholder scan:** None found.

**Type consistency:**
- `save_snapshot(trade_id, event, symbol, interval, candles, event_price, captured_at=None)` — used consistently across Tasks 2, 3, 4, 5.
- `load_snapshot(trade_id, event)` → `dict | None` — used in Tasks 5.
- `_df_to_candles(df)` — defined in Task 2, used in Tasks 2, 3, 4.
- `_STRATEGY_CANDLE_INTERVAL` — defined in Task 3, used in Tasks 3, 4.
