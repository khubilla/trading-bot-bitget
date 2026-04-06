# Entry Watcher for All Strategies — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route S2/S3/S4/S6 trade execution through the 4-second entry watcher thread, giving faster entry resolution, signal-based cancellation, and restart survival via state.json persistence.

**Architecture:** Queue functions store lightweight payloads to `pending_signals`; the entry watcher reads `pair_states` from state.json every 4s for signal validity, checks per-strategy price triggers and invalidation conditions, then calls `_fire_sX()` which runs S/R + Claude checks at fire time before opening the trade. S6 is merged into `pending_signals`; `s6_watchers` dict is removed.

**Tech Stack:** Python 3.11, pytest, existing bot.py/state.py/trader.py patterns

**Branch:** `feat/entry-watcher-all-strategies`

---

## File Map

| File | Changes |
|------|---------|
| `state.py` | Add `pending_signals` to `_default`; add `save_pending_signals()`, `load_pending_signals()`; preserve in `reset()` |
| `bot.py` | Add `_queue_s2/s3/s4/s6_pending()`; add `_fire_s2/s3/s4/s6()`; update `_execute_best_candidate()`; update `_entry_watcher_loop()`; load pending on startup; remove `s6_watchers`, `_queue_s6_watcher()`, `_process_s6_watchers()`, remove call to `_process_s6_watchers()` |
| `tests/test_bot_entry_watcher_all.py` | New test file for S2/S3/S4/S6 queue, watcher, and fire functions |
| `tests/test_state_pending_signals.py` | New test file for state.py persistence functions |

---

## Task 1: state.py — persist pending_signals

**Files:**
- Modify: `state.py`
- Test: `tests/test_state_pending_signals.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_state_pending_signals.py`:

```python
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import state as st


@pytest.fixture(autouse=True)
def tmp_state(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "STATE_FILE", str(tmp_path / "state.json"))
    st.reset()


def test_save_and_load_pending_signals():
    signals = {
        "BTCUSDT": {"strategy": "S2", "side": "LONG", "trigger": 50000.0, "s2_bl": 48000.0},
    }
    st.save_pending_signals(signals)
    loaded = st.load_pending_signals()
    assert loaded == signals


def test_load_pending_signals_returns_empty_when_missing():
    loaded = st.load_pending_signals()
    assert loaded == {}


def test_pending_signals_preserved_across_reset():
    signals = {"ETHUSDT": {"strategy": "S3", "side": "LONG", "trigger": 2000.0}}
    st.save_pending_signals(signals)
    st.reset()
    loaded = st.load_pending_signals()
    assert loaded == signals


def test_save_pending_signals_overwrites():
    st.save_pending_signals({"AAVEUSDT": {"strategy": "S4", "trigger": 100.0}})
    st.save_pending_signals({"DOTUSDT": {"strategy": "S2", "trigger": 10.0}})
    loaded = st.load_pending_signals()
    assert "AAVEUSDT" not in loaded
    assert "DOTUSDT" in loaded
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
pytest tests/test_state_pending_signals.py -v
```

Expected: `FAILED` — `save_pending_signals` not defined.

- [ ] **Step 3: Implement in state.py**

Add `"pending_signals": {}` to `_default` dict after the `"stats"` block:

```python
_default: dict = {
    ...
    "stats": { ... },
    "pending_signals": {},   # ← add this
}
```

Update `reset()` to preserve `pending_signals`:

```python
def reset():
    s = _read()
    fresh = dict(_default)
    fresh["trade_history"]   = s.get("trade_history", [])
    fresh["stats"]           = s.get("stats", dict(_default["stats"]))
    fresh["position_memory"] = s.get("position_memory", {})
    fresh["pending_signals"] = s.get("pending_signals", {})   # ← add this line
    _write(fresh)
```

Add after `update_open_trade_mark_price`:

```python
def save_pending_signals(signals: dict) -> None:
    """Persist the full pending_signals dict to state.json."""
    s = _read()
    s["pending_signals"] = signals
    _write(s)

def load_pending_signals() -> dict:
    """Load pending_signals from state.json. Returns {} if not present."""
    return _read().get("pending_signals", {})
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_state_pending_signals.py -v
```

Expected: all 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add state.py tests/test_state_pending_signals.py
git commit -m "feat: persist pending_signals to state.json with save/load functions"
```

---

## Task 2: bot.py — queue functions for S2/S3/S4/S6

**Files:**
- Modify: `bot.py` (add `_queue_s2_pending`, `_queue_s3_pending`, `_queue_s4_pending`, `_queue_s6_pending`)
- Test: `tests/test_bot_entry_watcher_all.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_bot_entry_watcher_all.py`:

```python
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import threading
import pytest
import bot
import state as st


def _make_bot(monkeypatch) -> bot.MTFBot:
    b = object.__new__(bot.MTFBot)
    b.pending_signals = {}
    b.active_positions = {}
    b._trade_lock = threading.Lock()
    b.running = True
    b.sentiment = type("S", (), {"direction": "NEUTRAL"})()
    monkeypatch.setattr(bot.st, "add_scan_log", lambda *a, **kw: None)
    monkeypatch.setattr(bot.st, "save_pending_signals", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "PAPER_MODE", False)
    return b


def _make_s2_candidate():
    return {
        "symbol": "BTCUSDT", "strategy": "S2", "sig": "LONG",
        "s2_bh": 50000.0, "s2_bl": 47000.0,
        "s2_rsi": 62.5, "s2_reason": "breakout",
        "daily_df": None,
        "priority_rank": 1, "priority_score": 35.0,
        "rr": 2.5, "sr_pct": 10.0,
    }


def _make_s3_candidate():
    return {
        "symbol": "ETHUSDT", "strategy": "S3", "sig": "LONG",
        "s3_trigger": 2000.0, "s3_sl": 1900.0,
        "s3_adx": 28.5, "s3_reason": "pullback",
        "s3_sr_resistance_pct": 8.0, "m15_df": None,
        "priority_rank": 2, "priority_score": 28.0,
        "rr": 2.0, "sr_pct": 8.0,
    }


def _make_s4_candidate():
    return {
        "symbol": "SOLUSDT", "strategy": "S4", "sig": "SHORT",
        "s4_trigger": 95.0, "s4_sl": 105.0,
        "s4_rsi": 45.0, "s4_rsi_peak": 85.0,
        "s4_body_pct": 0.65, "s4_div": True, "s4_div_str": "RSI divergence",
        "daily_df": None,
        "priority_rank": 3, "priority_score": 22.0,
        "rr": 2.0, "sr_pct": 6.0,
    }


def _make_s6_candidate():
    return {
        "symbol": "BNBUSDT", "strategy": "S6", "sig": "PENDING_SHORT",
        "s6_peak_level": 400.0, "s6_sl": 420.0,
        "s6_drop_pct": 0.35, "s6_rsi_at_peak": 78.0,
        "s6_reason": "V-formation",
        "daily_df": None,
        "priority_rank": 4, "priority_score": 18.0,
        "rr": 2.0, "sr_pct": 5.0,
    }


def test_queue_s2_pending_stores_payload(monkeypatch):
    b = _make_bot(monkeypatch)
    c = _make_s2_candidate()
    b._queue_s2_pending(c)
    assert "BTCUSDT" in b.pending_signals
    sig = b.pending_signals["BTCUSDT"]
    assert sig["strategy"] == "S2"
    assert sig["side"] == "LONG"
    assert sig["trigger"] == 50000.0
    assert sig["s2_bh"] == 50000.0
    assert sig["s2_bl"] == 47000.0
    assert sig["priority_rank"] == 1


def test_queue_s3_pending_stores_payload(monkeypatch):
    b = _make_bot(monkeypatch)
    c = _make_s3_candidate()
    b._queue_s3_pending(c)
    assert "ETHUSDT" in b.pending_signals
    sig = b.pending_signals["ETHUSDT"]
    assert sig["strategy"] == "S3"
    assert sig["trigger"] == 2000.0
    assert sig["s3_sl"] == 1900.0


def test_queue_s4_pending_stores_payload(monkeypatch):
    b = _make_bot(monkeypatch)
    c = _make_s4_candidate()
    b._queue_s4_pending(c)
    assert "SOLUSDT" in b.pending_signals
    sig = b.pending_signals["SOLUSDT"]
    assert sig["strategy"] == "S4"
    assert sig["side"] == "SHORT"
    assert sig["trigger"] == 95.0
    assert sig["s4_sl"] == 105.0


def test_queue_s6_pending_stores_payload(monkeypatch):
    b = _make_bot(monkeypatch)
    c = _make_s6_candidate()
    b._queue_s6_pending(c)
    assert "BNBUSDT" in b.pending_signals
    sig = b.pending_signals["BNBUSDT"]
    assert sig["strategy"] == "S6"
    assert sig["peak_level"] == 400.0
    assert sig["fakeout_seen"] == False


def test_queue_s6_pending_patches_pair_state(monkeypatch):
    b = _make_bot(monkeypatch)
    patched = {}
    monkeypatch.setattr(bot.st, "patch_pair_state",
                        lambda sym, d: patched.update({sym: d}))
    b._queue_s6_pending(_make_s6_candidate())
    assert patched.get("BNBUSDT", {}).get("s6_fakeout_seen") == False


def test_queue_saves_pending_signals(monkeypatch):
    saved = {}
    monkeypatch.setattr(bot.st, "save_pending_signals",
                        lambda signals: saved.update(signals))
    b = _make_bot(monkeypatch)
    b._queue_s2_pending(_make_s2_candidate())
    assert "BTCUSDT" in saved
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_bot_entry_watcher_all.py::test_queue_s2_pending_stores_payload \
       tests/test_bot_entry_watcher_all.py::test_queue_s3_pending_stores_payload \
       tests/test_bot_entry_watcher_all.py::test_queue_s4_pending_stores_payload \
       tests/test_bot_entry_watcher_all.py::test_queue_s6_pending_stores_payload \
       tests/test_bot_entry_watcher_all.py::test_queue_s6_pending_patches_pair_state \
       tests/test_bot_entry_watcher_all.py::test_queue_saves_pending_signals -v
```

Expected: `FAILED` — `_queue_s2_pending` not defined.

- [ ] **Step 3: Add queue functions to bot.py**

Add these four methods after `_queue_s5_pending` (around line 1928), before `_queue_s6_watcher`:

```python
def _queue_s2_pending(self, c: dict) -> None:
    """Queue an S2 LONG breakout for the entry watcher."""
    symbol = c["symbol"]
    self.pending_signals[symbol] = {
        "strategy":           "S2",
        "side":               "LONG",
        "trigger":            c["s2_bh"],
        "s2_bh":              c["s2_bh"],
        "s2_bl":              c["s2_bl"],
        "priority_rank":      c.get("priority_rank", 999),
        "priority_score":     c.get("priority_score", 0.0),
        "snap_daily_rsi":     round(c["s2_rsi"], 1),
        "snap_box_range_pct": round((c["s2_bh"] - c["s2_bl"]) / c["s2_bl"] * 100, 3)
                              if c["s2_bh"] and c["s2_bl"] else None,
        "snap_sentiment":     self.sentiment.direction if self.sentiment else "?",
    }
    st.save_pending_signals(self.pending_signals)
    logger.info(
        f"[S2][{symbol}] 🕐 PENDING LONG queued | "
        f"trigger={c['s2_bh']:.5f} | SL={c['s2_bl']:.5f}"
    )
    st.add_scan_log(
        f"[S2][{symbol}] 🕐 PENDING LONG | trigger={c['s2_bh']:.5f}", "SIGNAL"
    )


def _queue_s3_pending(self, c: dict) -> None:
    """Queue an S3 LONG pullback for the entry watcher."""
    symbol = c["symbol"]
    s3_trigger = c["s3_trigger"]
    s3_sl      = c["s3_sl"]
    rr = round(config_s3.S3_TRAILING_TRIGGER_PCT * s3_trigger / (s3_trigger - s3_sl), 2) \
         if s3_trigger and s3_sl and s3_trigger > s3_sl else None
    self.pending_signals[symbol] = {
        "strategy":           "S3",
        "side":               "LONG",
        "trigger":            s3_trigger,
        "s3_sl":              s3_sl,
        "priority_rank":      c.get("priority_rank", 999),
        "priority_score":     c.get("priority_score", 0.0),
        "snap_adx":           round(c["s3_adx"], 1) if c.get("s3_adx") else None,
        "snap_entry_trigger": round(s3_trigger, 8),
        "snap_sl":            round(s3_sl, 8),
        "snap_rr":            rr,
        "snap_sentiment":     self.sentiment.direction if self.sentiment else "?",
        "snap_sr_clearance_pct": c.get("s3_sr_resistance_pct"),
    }
    st.save_pending_signals(self.pending_signals)
    logger.info(
        f"[S3][{symbol}] 🕐 PENDING LONG queued | "
        f"trigger={s3_trigger:.5f} | SL={s3_sl:.5f}"
    )
    st.add_scan_log(
        f"[S3][{symbol}] 🕐 PENDING LONG | trigger={s3_trigger:.5f}", "SIGNAL"
    )


def _queue_s4_pending(self, c: dict) -> None:
    """Queue an S4 SHORT spike-reversal for the entry watcher."""
    symbol = c["symbol"]
    s4_trigger = c["s4_trigger"]
    s4_sl      = c["s4_sl"]
    prev_low_approx = s4_trigger / (1 - config_s4.S4_ENTRY_BUFFER)
    self.pending_signals[symbol] = {
        "strategy":             "S4",
        "side":                 "SHORT",
        "trigger":              s4_trigger,
        "s4_sl":                s4_sl,
        "prev_low":             prev_low_approx,
        "priority_rank":        c.get("priority_rank", 999),
        "priority_score":       c.get("priority_score", 0.0),
        "snap_rsi":             round(c["s4_rsi"], 1),
        "snap_rsi_peak":        round(c["s4_rsi_peak"], 1),
        "snap_spike_body_pct":  round(c["s4_body_pct"] * 100, 1),
        "snap_rsi_div":         c["s4_div"],
        "snap_rsi_div_str":     c["s4_div_str"],
        "snap_sentiment":       self.sentiment.direction if self.sentiment else "?",
    }
    st.save_pending_signals(self.pending_signals)
    logger.info(
        f"[S4][{symbol}] 🕐 PENDING SHORT queued | "
        f"trigger≤{s4_trigger:.5f} | SL={s4_sl:.5f}"
    )
    st.add_scan_log(
        f"[S4][{symbol}] 🕐 PENDING SHORT | trigger≤{s4_trigger:.5f}", "SIGNAL"
    )


def _queue_s6_pending(self, candidate: dict) -> None:
    """Queue an S6 two-phase V-formation watcher into pending_signals."""
    symbol = candidate["symbol"]
    self.pending_signals[symbol] = {
        "strategy":           "S6",
        "side":               "SHORT",
        "peak_level":         candidate["s6_peak_level"],
        "sl":                 candidate["s6_sl"],
        "drop_pct":           candidate["s6_drop_pct"],
        "rsi_at_peak":        candidate["s6_rsi_at_peak"],
        "fakeout_seen":       False,
        "detected_at":        time.time(),
        "snap_s6_peak":       round(candidate["s6_peak_level"], 8),
        "snap_s6_drop_pct":   round(candidate["s6_drop_pct"] * 100, 2),
        "snap_s6_rsi_at_peak": round(candidate["s6_rsi_at_peak"], 1),
        "snap_sentiment":     self.sentiment.direction if self.sentiment else None,
        "priority_rank":      candidate.get("priority_rank", 999),
        "priority_score":     candidate.get("priority_score", 0.0),
    }
    st.patch_pair_state(symbol, {"s6_fakeout_seen": False})
    st.save_pending_signals(self.pending_signals)
    logger.info(
        f"[S6][{symbol}] 🕐 V-formation watcher queued | "
        f"peak={candidate['s6_peak_level']:.5f} | SL={candidate['s6_sl']:.5f}"
    )
    st.add_scan_log(
        f"[S6][{symbol}] 🕐 V-formation watcher | peak={candidate['s6_peak_level']:.5f}",
        "SIGNAL"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_bot_entry_watcher_all.py::test_queue_s2_pending_stores_payload \
       tests/test_bot_entry_watcher_all.py::test_queue_s3_pending_stores_payload \
       tests/test_bot_entry_watcher_all.py::test_queue_s4_pending_stores_payload \
       tests/test_bot_entry_watcher_all.py::test_queue_s6_pending_stores_payload \
       tests/test_bot_entry_watcher_all.py::test_queue_s6_pending_patches_pair_state \
       tests/test_bot_entry_watcher_all.py::test_queue_saves_pending_signals -v
```

Expected: all 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add bot.py tests/test_bot_entry_watcher_all.py
git commit -m "feat: add _queue_s2/s3/s4/s6_pending — lightweight payload, saves to state.json"
```

---

## Task 3: bot.py — fire functions for S2/S3/S4/S6

**Files:**
- Modify: `bot.py` (add `_fire_s2`, `_fire_s3`, `_fire_s4`, `_fire_s6`)
- Test: `tests/test_bot_entry_watcher_all.py` (extend)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_bot_entry_watcher_all.py`:

```python
def _make_pair_state(s2_sr=None, s3_sr=None, s4_sr_pct=None,
                     s2_signal="LONG", s3_signal="LONG", s4_signal="SHORT"):
    return {
        "s2_sr_resistance_price": s2_sr,
        "s2_signal": s2_signal,
        "s3_sr_resistance_price": s3_sr,
        "s3_signal": s3_signal,
        "s4_sr_support_pct": s4_sr_pct,
        "s4_signal": s4_signal,
    }


def test_fire_s2_opens_long_when_sr_clear(monkeypatch):
    b = _make_bot(monkeypatch)
    monkeypatch.setattr(bot.st, "get_pair_state",
        lambda sym: _make_pair_state(s2_sr=55000.0))  # 10% above mark=50000
    opened = {}
    monkeypatch.setattr(bot.tr, "open_long",
        lambda sym, **kw: opened.update({"sym": sym}) or
        {"symbol": sym, "side": "LONG", "entry": 50000.0, "sl": 47000.0,
         "tp": None, "qty": "0.001", "leverage": 10, "margin": 0.5})
    monkeypatch.setattr(bot.st, "add_open_trade", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "_log_trade", lambda *a, **kw: None)
    monkeypatch.setattr(bot.snapshot, "save_snapshot", lambda **kw: None)

    sig = _make_s2_candidate()
    sig.update({"strategy": "S2", "side": "LONG", "trigger": 50000.0,
                "s2_bh": 50000.0, "s2_bl": 47000.0,
                "snap_daily_rsi": 62.5, "snap_box_range_pct": 6.3,
                "snap_sentiment": "NEUTRAL"})
    b._fire_s2("BTCUSDT", sig, mark=50000.0, balance=1000.0)
    assert opened.get("sym") == "BTCUSDT"


def test_fire_s2_skips_when_sr_too_close(monkeypatch):
    b = _make_bot(monkeypatch)
    monkeypatch.setattr(bot.st, "get_pair_state",
        lambda sym: _make_pair_state(s2_sr=50200.0))  # only 0.4% clearance
    opened = {}
    monkeypatch.setattr(bot.tr, "open_long",
        lambda sym, **kw: opened.update({"sym": sym}) or {})

    sig = {"strategy": "S2", "side": "LONG", "trigger": 50000.0,
           "s2_bh": 50000.0, "s2_bl": 47000.0,
           "snap_daily_rsi": 62.5, "snap_box_range_pct": 6.3,
           "snap_sentiment": "NEUTRAL"}
    b._fire_s2("BTCUSDT", sig, mark=50000.0, balance=1000.0)
    assert "sym" not in opened, "Must not open trade when S/R too close"


def test_fire_s3_opens_long_when_sr_clear(monkeypatch):
    b = _make_bot(monkeypatch)
    monkeypatch.setattr(bot.st, "get_pair_state",
        lambda sym: _make_pair_state(s3_sr=2200.0))  # 10% above mark=2000
    opened = {}
    monkeypatch.setattr(bot.tr, "open_long",
        lambda sym, **kw: opened.update({"sym": sym}) or
        {"symbol": sym, "side": "LONG", "entry": 2000.0, "sl": 1900.0,
         "tp": None, "qty": "0.1", "leverage": 10, "margin": 20.0})
    monkeypatch.setattr(bot.st, "add_open_trade", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "_log_trade", lambda *a, **kw: None)
    monkeypatch.setattr(bot.snapshot, "save_snapshot", lambda **kw: None)

    sig = {"strategy": "S3", "side": "LONG", "trigger": 2000.0,
           "s3_sl": 1900.0, "snap_adx": 28.5, "snap_entry_trigger": 2000.0,
           "snap_sl": 1900.0, "snap_rr": 2.0, "snap_sentiment": "NEUTRAL",
           "snap_sr_clearance_pct": 10.0}
    b._fire_s3("ETHUSDT", sig, mark=2000.0, balance=1000.0)
    assert opened.get("sym") == "ETHUSDT"


def test_fire_s4_opens_short_when_sr_clear(monkeypatch):
    b = _make_bot(monkeypatch)
    monkeypatch.setattr(bot.st, "get_pair_state",
        lambda sym: _make_pair_state(s4_sr_pct=8.0))  # 8% > min clearance
    opened = {}
    monkeypatch.setattr(bot.tr, "open_short",
        lambda sym, **kw: opened.update({"sym": sym}) or
        {"symbol": sym, "side": "SHORT", "entry": 95.0, "sl": 105.0,
         "tp": None, "qty": "10", "leverage": 10, "margin": 5.0})
    monkeypatch.setattr(bot.st, "add_open_trade", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "_log_trade", lambda *a, **kw: None)
    monkeypatch.setattr(bot.snapshot, "save_snapshot", lambda **kw: None)

    sig = {"strategy": "S4", "side": "SHORT", "trigger": 95.0,
           "s4_sl": 105.0, "prev_low": 100.0,
           "snap_rsi": 45.0, "snap_rsi_peak": 85.0,
           "snap_spike_body_pct": 65.0, "snap_rsi_div": True,
           "snap_rsi_div_str": "RSI divergence", "snap_sentiment": "NEUTRAL"}
    b._fire_s4("SOLUSDT", sig, mark=95.0, balance=1000.0)
    assert opened.get("sym") == "SOLUSDT"


def test_fire_s6_opens_short(monkeypatch):
    b = _make_bot(monkeypatch)
    opened = {}
    monkeypatch.setattr(bot.tr, "open_short",
        lambda sym, **kw: opened.update({"sym": sym}) or
        {"symbol": sym, "side": "SHORT", "entry": 390.0, "sl": 410.0,
         "tp": None, "qty": "5", "leverage": 10, "margin": 20.0})
    monkeypatch.setattr(bot.st, "add_open_trade", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "_log_trade", lambda *a, **kw: None)
    monkeypatch.setattr(bot.snapshot, "save_snapshot", lambda **kw: None)

    sig = {"strategy": "S6", "side": "SHORT", "peak_level": 400.0,
           "sl": 420.0, "drop_pct": 0.35, "rsi_at_peak": 78.0,
           "snap_s6_peak": 400.0, "snap_s6_drop_pct": 35.0,
           "snap_s6_rsi_at_peak": 78.0, "snap_sentiment": "BEARISH"}
    b._fire_s6("BNBUSDT", sig, mark=390.0, balance=1000.0)
    assert opened.get("sym") == "BNBUSDT"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_bot_entry_watcher_all.py::test_fire_s2_opens_long_when_sr_clear \
       tests/test_bot_entry_watcher_all.py::test_fire_s2_skips_when_sr_too_close \
       tests/test_bot_entry_watcher_all.py::test_fire_s3_opens_long_when_sr_clear \
       tests/test_bot_entry_watcher_all.py::test_fire_s4_opens_short_when_sr_clear \
       tests/test_bot_entry_watcher_all.py::test_fire_s6_opens_short -v
```

Expected: `FAILED` — `_fire_s2` not defined, `get_pair_state` not defined.

- [ ] **Step 3: Add get_pair_state to state.py**

Add after `patch_pair_state`:

```python
def get_pair_state(symbol: str) -> dict:
    """Return the pair_states entry for symbol, or {} if not present."""
    return _read().get("pair_states", {}).get(symbol, {})
```

- [ ] **Step 4: Add fire functions to bot.py**

Add these methods after `_queue_s6_pending`, before `_entry_watcher_loop`:

```python
def _fire_s2(self, symbol: str, sig: dict, mark: float, balance: float) -> None:
    """Open S2 LONG at fire time. Runs S/R check against pair_states."""
    ps = st.get_pair_state(symbol)
    sr_resistance = ps.get("s2_sr_resistance_price")
    if sr_resistance is not None:
        clearance = (sr_resistance - mark) / mark
        if clearance < config_s2.S2_MIN_SR_CLEARANCE:
            logger.info(
                f"[S2][{symbol}] ⏸️ Fire skipped — resistance {sr_resistance:.5f} "
                f"only {clearance*100:.1f}% away"
            )
            st.add_scan_log(
                f"[S2][{symbol}] ⛔ Fire: resistance too close ({clearance*100:.1f}%)", "WARN"
            )
            self.pending_signals.pop(symbol, None)
            st.save_pending_signals(self.pending_signals)
            return
    if config.CLAUDE_FILTER_ENABLED:
        _sr_str = f"{round((sr_resistance - mark) / mark * 100, 1)}%" if sr_resistance else "none found"
        _cd = claude_approve("S2", symbol, {
            "RSI": sig.get("snap_daily_rsi", "?"),
            "S/R clearance": _sr_str,
            "Sentiment": sig.get("snap_sentiment", "?"),
            "Entry": round(mark, 5), "SL": round(sig["s2_bl"], 5),
        })
        if not _cd["approved"]:
            logger.info(f"[S2][{symbol}] 🤖 Claude rejected: {_cd['reason']}")
            st.add_scan_log(f"[S2][{symbol}] 🤖 Rejected: {_cd['reason']}", "WARN")
            self.pending_signals.pop(symbol, None)
            st.save_pending_signals(self.pending_signals)
            return
    st.add_scan_log(f"[S2][{symbol}] 🟢 LONG fired @ {mark:.5f}", "SIGNAL")
    trade = tr.open_long(
        symbol, box_low=sig["s2_bl"], leverage=config_s2.S2_LEVERAGE,
        trade_size_pct=config_s2.S2_TRADE_SIZE_PCT * 0.5,
        take_profit_pct=config_s2.S2_TAKE_PROFIT_PCT,
        stop_loss_pct=config_s2.S2_STOP_LOSS_PCT,
        use_s2_exits=True,
    )
    trade["strategy"]              = "S2"
    trade["snap_daily_rsi"]        = sig.get("snap_daily_rsi")
    trade["snap_box_range_pct"]    = sig.get("snap_box_range_pct")
    trade["snap_sentiment"]        = sig.get("snap_sentiment")
    trade["snap_sr_clearance_pct"] = round((sr_resistance - mark) / mark * 100, 1) \
                                     if sr_resistance else None
    trade["trade_id"] = uuid.uuid4().hex[:8]
    _log_trade("S2_LONG", trade)
    st.add_open_trade(trade)
    try:
        snapshot.save_snapshot(
            trade_id=trade["trade_id"], event="open",
            symbol=symbol, interval="1D", candles=[],
            event_price=float(trade.get("entry", 0)),
        )
    except Exception as e:
        logger.warning(f"[S2][{symbol}] snapshot save failed: {e}")
    if PAPER_MODE: tr.tag_strategy(symbol, "S2")
    self.active_positions[symbol] = {
        "side": "LONG", "strategy": "S2",
        "box_high": sig["s2_bh"], "box_low": sig["s2_bl"],
        "scale_in_pending": True, "scale_in_after": time.time() + 3600,
        "scale_in_trade_size_pct": config_s2.S2_TRADE_SIZE_PCT,
        "trade_id": trade["trade_id"],
    }


def _fire_s3(self, symbol: str, sig: dict, mark: float, balance: float) -> None:
    """Open S3 LONG at fire time. Runs S/R check against pair_states."""
    ps = st.get_pair_state(symbol)
    sr_resistance = ps.get("s3_sr_resistance_price")
    if sr_resistance is not None:
        clearance = (sr_resistance - mark) / mark
        if clearance < config_s3.S3_MIN_SR_CLEARANCE:
            logger.info(
                f"[S3][{symbol}] ⏸️ Fire skipped — resistance {sr_resistance:.5f} "
                f"only {clearance*100:.1f}% away"
            )
            st.add_scan_log(
                f"[S3][{symbol}] ⛔ Fire: resistance too close ({clearance*100:.1f}%)", "WARN"
            )
            self.pending_signals.pop(symbol, None)
            st.save_pending_signals(self.pending_signals)
            return
    if config.CLAUDE_FILTER_ENABLED:
        _sr_str = f"{round((sr_resistance - mark) / mark * 100, 1)}%" if sr_resistance else "none found"
        _cd = claude_approve("S3", symbol, {
            "ADX": sig.get("snap_adx", "?"),
            "S/R clearance (15m)": _sr_str,
            "Sentiment": sig.get("snap_sentiment", "?"),
            "Entry": round(mark, 5), "SL": round(sig["s3_sl"], 5),
        })
        if not _cd["approved"]:
            logger.info(f"[S3][{symbol}] 🤖 Claude rejected: {_cd['reason']}")
            st.add_scan_log(f"[S3][{symbol}] 🤖 Rejected: {_cd['reason']}", "WARN")
            self.pending_signals.pop(symbol, None)
            st.save_pending_signals(self.pending_signals)
            return
    st.add_scan_log(f"[S3][{symbol}] 🟢 LONG fired @ {mark:.5f}", "SIGNAL")
    trade = tr.open_long(
        symbol, sl_floor=sig["s3_sl"], leverage=config_s3.S3_LEVERAGE,
        trade_size_pct=config_s3.S3_TRADE_SIZE_PCT, use_s3_exits=True,
    )
    trade["strategy"]              = "S3"
    trade["snap_adx"]              = sig.get("snap_adx")
    trade["snap_entry_trigger"]    = sig.get("snap_entry_trigger")
    trade["snap_sl"]               = sig.get("snap_sl")
    trade["snap_rr"]               = sig.get("snap_rr")
    trade["snap_sentiment"]        = sig.get("snap_sentiment")
    trade["snap_sr_clearance_pct"] = sig.get("snap_sr_clearance_pct")
    trade["trade_id"] = uuid.uuid4().hex[:8]
    _log_trade("S3_LONG", trade)
    st.add_open_trade(trade)
    try:
        snapshot.save_snapshot(
            trade_id=trade["trade_id"], event="open",
            symbol=symbol, interval=config_s3.S3_LTF_INTERVAL, candles=[],
            event_price=float(trade.get("entry", 0)),
        )
    except Exception as e:
        logger.warning(f"[S3][{symbol}] snapshot save failed: {e}")
    if PAPER_MODE: tr.tag_strategy(symbol, "S3")
    self.active_positions[symbol] = {
        "side": "LONG", "strategy": "S3",
        "box_high": sig["trigger"], "box_low": sig["s3_sl"],
        "trade_id": trade["trade_id"],
    }


def _fire_s4(self, symbol: str, sig: dict, mark: float, balance: float) -> None:
    """Open S4 SHORT at fire time. Runs S/R check against pair_states."""
    ps = st.get_pair_state(symbol)
    sr_support_pct = ps.get("s4_sr_support_pct")
    if sr_support_pct is not None and sr_support_pct < config_s4.S4_MIN_SR_CLEARANCE * 100:
        logger.info(
            f"[S4][{symbol}] ⏸️ Fire skipped — support clearance {sr_support_pct:.1f}% too small"
        )
        st.add_scan_log(
            f"[S4][{symbol}] ⛔ Fire: support too close ({sr_support_pct:.1f}%)", "WARN"
        )
        self.pending_signals.pop(symbol, None)
        st.save_pending_signals(self.pending_signals)
        return
    if config.CLAUDE_FILTER_ENABLED:
        _sr_str = f"{sr_support_pct:.1f}%" if sr_support_pct else "none found"
        _cd = claude_approve("S4", symbol, {
            "RSI peak": sig.get("snap_rsi_peak", "?"),
            "RSI divergence": str(sig.get("snap_rsi_div", "?")),
            "S/R clearance (spike base)": _sr_str,
            "Sentiment": sig.get("snap_sentiment", "?"),
            "Entry": round(mark, 5), "SL": round(sig["s4_sl"], 5),
        })
        if not _cd["approved"]:
            logger.info(f"[S4][{symbol}] 🤖 Claude rejected: {_cd['reason']}")
            st.add_scan_log(f"[S4][{symbol}] 🤖 Rejected: {_cd['reason']}", "WARN")
            self.pending_signals.pop(symbol, None)
            st.save_pending_signals(self.pending_signals)
            return
    s4_sl_actual = mark * (1 + 0.50 / config_s4.S4_LEVERAGE)
    st.add_scan_log(
        f"[S4][{symbol}] 🔴 SHORT fired @ {mark:.5f} | entry≤{sig['trigger']:.5f}", "SIGNAL"
    )
    trade = tr.open_short(
        symbol, sl_floor=s4_sl_actual, leverage=config_s4.S4_LEVERAGE,
        trade_size_pct=config_s4.S4_TRADE_SIZE_PCT * 0.5, use_s4_exits=True,
    )
    trade["strategy"]              = "S4"
    trade["snap_rsi"]              = sig.get("snap_rsi")
    trade["snap_rsi_peak"]         = sig.get("snap_rsi_peak")
    trade["snap_spike_body_pct"]   = sig.get("snap_spike_body_pct")
    trade["snap_rsi_div"]          = sig.get("snap_rsi_div")
    trade["snap_rsi_div_str"]      = sig.get("snap_rsi_div_str")
    trade["snap_sl"]               = round(s4_sl_actual, 8)
    trade["snap_sentiment"]        = sig.get("snap_sentiment")
    trade["snap_sr_clearance_pct"] = sr_support_pct
    trade["trade_id"] = uuid.uuid4().hex[:8]
    _log_trade("S4_SHORT", trade)
    st.add_open_trade(trade)
    try:
        snapshot.save_snapshot(
            trade_id=trade["trade_id"], event="open",
            symbol=symbol, interval="1D", candles=[],
            event_price=float(trade.get("entry", 0)),
        )
    except Exception as e:
        logger.warning(f"[S4][{symbol}] snapshot save failed: {e}")
    if PAPER_MODE: tr.tag_strategy(symbol, "S4")
    self.active_positions[symbol] = {
        "side": "SHORT", "strategy": "S4",
        "box_high": sig["s4_sl"], "box_low": sig["trigger"],
        "scale_in_pending": True, "scale_in_after": time.time() + 3600,
        "scale_in_trade_size_pct": config_s4.S4_TRADE_SIZE_PCT,
        "s4_prev_low": sig["prev_low"],
        "trade_id": trade["trade_id"],
    }


def _fire_s6(self, symbol: str, sig: dict, mark: float, balance: float) -> None:
    """Open S6 SHORT after two-phase fakeout confirmed."""
    sl_price = mark * (1 + config_s6.S6_SL_PCT / config_s6.S6_LEVERAGE)
    st.add_scan_log(
        f"[S6][{symbol}] 🔴 SHORT | peak={sig['peak_level']:.5f} | "
        f"fakeout confirmed → entry @ {mark:.5f}", "SIGNAL"
    )
    trade = tr.open_short(
        symbol, sl_floor=sl_price, leverage=config_s6.S6_LEVERAGE,
        trade_size_pct=config_s6.S6_TRADE_SIZE_PCT, use_s6_exits=True,
    )
    trade["strategy"]              = "S6"
    trade["snap_s6_peak"]          = sig.get("snap_s6_peak")
    trade["snap_s6_drop_pct"]      = sig.get("snap_s6_drop_pct")
    trade["snap_s6_rsi_at_peak"]   = sig.get("snap_s6_rsi_at_peak")
    trade["snap_sentiment"]        = sig.get("snap_sentiment")
    trade["snap_sr_clearance_pct"] = None
    trade["trade_id"] = uuid.uuid4().hex[:8]
    _log_trade("S6_SHORT", trade)
    st.add_open_trade(trade)
    try:
        snapshot.save_snapshot(
            trade_id=trade["trade_id"], event="open",
            symbol=symbol, interval="1D", candles=[],
            event_price=float(trade.get("entry", 0)),
        )
    except Exception as e:
        logger.warning(f"[S6][{symbol}] snapshot save failed: {e}")
    if PAPER_MODE: tr.tag_strategy(symbol, "S6")
    self.active_positions[symbol] = {
        "side": "SHORT", "strategy": "S6",
        "box_high": sl_price, "box_low": sig["peak_level"],
        "trade_id": trade["trade_id"],
    }
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_bot_entry_watcher_all.py::test_fire_s2_opens_long_when_sr_clear \
       tests/test_bot_entry_watcher_all.py::test_fire_s2_skips_when_sr_too_close \
       tests/test_bot_entry_watcher_all.py::test_fire_s3_opens_long_when_sr_clear \
       tests/test_bot_entry_watcher_all.py::test_fire_s4_opens_short_when_sr_clear \
       tests/test_bot_entry_watcher_all.py::test_fire_s6_opens_short -v
```

Expected: all 5 PASS.

- [ ] **Step 6: Commit**

```bash
git add bot.py state.py tests/test_bot_entry_watcher_all.py
git commit -m "feat: add _fire_s2/s3/s4/s6 — S/R check from pair_states at fire time"
```

---

## Task 4: bot.py — update _entry_watcher_loop for S2/S3/S4/S6

**Files:**
- Modify: `bot.py` (`_entry_watcher_loop`)
- Test: `tests/test_bot_entry_watcher_all.py` (extend)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_bot_entry_watcher_all.py`:

```python
import time as _time

def _make_s2_sig(mark_at_trigger=50000.0):
    return {
        "strategy": "S2", "side": "LONG",
        "trigger": 50000.0, "s2_bh": 50000.0, "s2_bl": 47000.0,
        "priority_rank": 1, "priority_score": 35.0,
        "snap_daily_rsi": 62.5, "snap_box_range_pct": 6.3, "snap_sentiment": "NEUTRAL",
    }


def _make_s3_sig():
    return {
        "strategy": "S3", "side": "LONG",
        "trigger": 2000.0, "s3_sl": 1900.0,
        "priority_rank": 2, "priority_score": 25.0,
        "snap_adx": 28.5, "snap_entry_trigger": 2000.0, "snap_sl": 1900.0,
        "snap_rr": 2.0, "snap_sentiment": "NEUTRAL", "snap_sr_clearance_pct": 10.0,
    }


def _make_s4_sig():
    return {
        "strategy": "S4", "side": "SHORT",
        "trigger": 95.0, "s4_sl": 105.0, "prev_low": 100.0,
        "priority_rank": 3, "priority_score": 20.0,
        "snap_rsi": 45.0, "snap_rsi_peak": 85.0, "snap_spike_body_pct": 65.0,
        "snap_rsi_div": True, "snap_rsi_div_str": "RSI div", "snap_sentiment": "NEUTRAL",
    }


def _make_s6_sig(fakeout_seen=False):
    return {
        "strategy": "S6", "side": "SHORT",
        "peak_level": 400.0, "sl": 420.0,
        "drop_pct": 0.35, "rsi_at_peak": 78.0,
        "fakeout_seen": fakeout_seen,
        "detected_at": _time.time(),
        "snap_s6_peak": 400.0, "snap_s6_drop_pct": 35.0,
        "snap_s6_rsi_at_peak": 78.0, "snap_sentiment": "BEARISH",
    }


def test_watcher_fires_s2_when_price_at_trigger(monkeypatch):
    """When mark is in S2 trigger window, _fire_s2 is called and signal removed."""
    b = _make_bot(monkeypatch)
    b.pending_signals = {"BTCUSDT": _make_s2_sig()}
    monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)
    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: 50000.0)
    # pair_states shows signal still valid
    monkeypatch.setattr(bot.st, "get_pair_state",
        lambda sym: {"s2_signal": "LONG"})
    fired = []
    monkeypatch.setattr(b, "_fire_s2",
        lambda sym, sig, mark, bal: fired.append(sym) or
        b.active_positions.update({sym: {"strategy": "S2"}}))
    monkeypatch.setattr(bot.st, "save_pending_signals", lambda *a: None)

    # Run one watcher iteration
    import threading
    b.running = False   # stop after first loop
    b._entry_watcher_loop()

    assert "BTCUSDT" in fired
    assert "BTCUSDT" not in b.pending_signals


def test_watcher_cancels_s2_when_signal_gone(monkeypatch):
    """When pair_states shows s2_signal='HOLD', pending is cancelled."""
    b = _make_bot(monkeypatch)
    b.pending_signals = {"BTCUSDT": _make_s2_sig()}
    monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)
    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: 50000.0)
    monkeypatch.setattr(bot.st, "get_pair_state",
        lambda sym: {"s2_signal": "HOLD"})   # ← signal gone
    monkeypatch.setattr(bot.st, "save_pending_signals", lambda *a: None)
    b.running = False
    b._entry_watcher_loop()
    assert "BTCUSDT" not in b.pending_signals


def test_watcher_cancels_s2_when_price_below_invalidation(monkeypatch):
    """When mark < s2_bl, pending S2 is cancelled regardless of signal."""
    b = _make_bot(monkeypatch)
    b.pending_signals = {"BTCUSDT": _make_s2_sig()}
    monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)
    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: 46000.0)  # below s2_bl=47000
    monkeypatch.setattr(bot.st, "get_pair_state",
        lambda sym: {"s2_signal": "LONG"})
    monkeypatch.setattr(bot.st, "save_pending_signals", lambda *a: None)
    b.running = False
    b._entry_watcher_loop()
    assert "BTCUSDT" not in b.pending_signals


def test_watcher_s6_phase1_sets_fakeout_seen(monkeypatch):
    """When mark > peak_level and fakeout_seen=False, fakeout_seen becomes True."""
    b = _make_bot(monkeypatch)
    b.pending_signals = {"BNBUSDT": _make_s6_sig(fakeout_seen=False)}
    monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)
    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: 410.0)  # above peak 400
    monkeypatch.setattr(bot.st, "get_pair_state",
        lambda sym: {"s6_signal": "PENDING_SHORT"})
    patched = {}
    monkeypatch.setattr(bot.st, "patch_pair_state",
        lambda sym, d: patched.update({sym: d}))
    monkeypatch.setattr(bot.st, "save_pending_signals", lambda *a: None)
    b.running = False
    b._entry_watcher_loop()
    assert b.pending_signals["BNBUSDT"]["fakeout_seen"] == True
    assert patched.get("BNBUSDT", {}).get("s6_fakeout_seen") == True


def test_watcher_s6_phase2_fires_when_below_peak(monkeypatch):
    """When fakeout_seen=True and mark < peak_level, _fire_s6 is called."""
    b = _make_bot(monkeypatch)
    b.pending_signals = {"BNBUSDT": _make_s6_sig(fakeout_seen=True)}
    monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)
    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: 390.0)  # below peak 400
    monkeypatch.setattr(bot.st, "get_pair_state",
        lambda sym: {"s6_signal": "PENDING_SHORT"})
    fired = []
    monkeypatch.setattr(b, "_fire_s6",
        lambda sym, sig, mark, bal: fired.append(sym) or
        b.active_positions.update({sym: {"strategy": "S6"}}))
    monkeypatch.setattr(bot.st, "save_pending_signals", lambda *a: None)
    b.running = False
    b._entry_watcher_loop()
    assert "BNBUSDT" in fired
    assert "BNBUSDT" not in b.pending_signals


def test_watcher_s6_cancels_on_bullish_sentiment(monkeypatch):
    """S6 watcher cancels when sentiment is BULLISH."""
    b = _make_bot(monkeypatch)
    b.sentiment = type("S", (), {"direction": "BULLISH"})()
    b.pending_signals = {"BNBUSDT": _make_s6_sig(fakeout_seen=True)}
    monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)
    monkeypatch.setattr(bot.tr, "get_mark_price", lambda sym: 390.0)
    monkeypatch.setattr(bot.st, "get_pair_state",
        lambda sym: {"s6_signal": "PENDING_SHORT"})
    monkeypatch.setattr(bot.st, "save_pending_signals", lambda *a: None)
    b.running = False
    b._entry_watcher_loop()
    assert "BNBUSDT" not in b.pending_signals
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_bot_entry_watcher_all.py -k "watcher_fires_s2 or watcher_cancels_s2 or watcher_s6" -v
```

Expected: `FAILED` — watcher doesn't handle S2/S3/S4/S6 yet.

- [ ] **Step 3: Update _entry_watcher_loop in bot.py**

Replace the `else:` block (non-S5 legacy path starting at line 2065) with the full strategy dispatch. The new `else` block becomes:

```python
                    elif strategy == "S2":
                        # ── S2: breakout trigger + invalidation ───────── #
                        ps = st.get_pair_state(symbol)
                        if ps.get("s2_signal", "HOLD") not in ("LONG",):
                            logger.info(f"[S2][{symbol}] 🚫 Signal gone — cancelling pending")
                            st.add_scan_log(f"[S2][{symbol}] 🚫 Pending cancelled (signal gone)", "INFO")
                            self.pending_signals.pop(symbol, None)
                            st.save_pending_signals(self.pending_signals)
                            continue
                        try:
                            mark = tr.get_mark_price(symbol)
                        except Exception:
                            continue
                        s2_bh = sig["s2_bh"]
                        s2_bl = sig["s2_bl"]
                        if mark < s2_bl:
                            logger.info(f"[S2][{symbol}] ❌ Invalidated — mark {mark:.5f} < box_low {s2_bl:.5f}")
                            st.add_scan_log(f"[S2][{symbol}] ❌ Pending cancelled (price below box)", "INFO")
                            self.pending_signals.pop(symbol, None)
                            st.save_pending_signals(self.pending_signals)
                            continue
                        in_window = s2_bh <= mark <= s2_bh * (1 + config_s2.S2_MAX_ENTRY_BUFFER)
                        if in_window:
                            with self._trade_lock:
                                if symbol in self.active_positions:
                                    self.pending_signals.pop(symbol, None)
                                    st.save_pending_signals(self.pending_signals)
                                    continue
                                if len(self.active_positions) >= config.MAX_CONCURRENT_TRADES:
                                    break
                                if st.is_pair_paused(symbol):
                                    continue
                                self._fire_s2(symbol, sig, mark, balance)
                            self.pending_signals.pop(symbol, None)
                            st.save_pending_signals(self.pending_signals)

                    elif strategy == "S3":
                        # ── S3: pullback trigger + invalidation ───────── #
                        ps = st.get_pair_state(symbol)
                        if ps.get("s3_signal", "HOLD") not in ("LONG",):
                            logger.info(f"[S3][{symbol}] 🚫 Signal gone — cancelling pending")
                            st.add_scan_log(f"[S3][{symbol}] 🚫 Pending cancelled (signal gone)", "INFO")
                            self.pending_signals.pop(symbol, None)
                            st.save_pending_signals(self.pending_signals)
                            continue
                        try:
                            mark = tr.get_mark_price(symbol)
                        except Exception:
                            continue
                        s3_sl = sig["s3_sl"]
                        if mark < s3_sl:
                            logger.info(f"[S3][{symbol}] ❌ Invalidated — mark {mark:.5f} < SL {s3_sl:.5f}")
                            st.add_scan_log(f"[S3][{symbol}] ❌ Pending cancelled (price below SL)", "INFO")
                            self.pending_signals.pop(symbol, None)
                            st.save_pending_signals(self.pending_signals)
                            continue
                        s3_trigger = sig["trigger"]
                        in_window = s3_trigger <= mark <= s3_trigger * (1 + config_s3.S3_MAX_ENTRY_BUFFER)
                        if in_window:
                            with self._trade_lock:
                                if symbol in self.active_positions:
                                    self.pending_signals.pop(symbol, None)
                                    st.save_pending_signals(self.pending_signals)
                                    continue
                                if len(self.active_positions) >= config.MAX_CONCURRENT_TRADES:
                                    break
                                if st.is_pair_paused(symbol):
                                    continue
                                self._fire_s3(symbol, sig, mark, balance)
                            self.pending_signals.pop(symbol, None)
                            st.save_pending_signals(self.pending_signals)

                    elif strategy == "S4":
                        # ── S4: spike-reversal trigger + invalidation ─── #
                        ps = st.get_pair_state(symbol)
                        if ps.get("s4_signal", "HOLD") not in ("SHORT",):
                            logger.info(f"[S4][{symbol}] 🚫 Signal gone — cancelling pending")
                            st.add_scan_log(f"[S4][{symbol}] 🚫 Pending cancelled (signal gone)", "INFO")
                            self.pending_signals.pop(symbol, None)
                            st.save_pending_signals(self.pending_signals)
                            continue
                        try:
                            mark = tr.get_mark_price(symbol)
                        except Exception:
                            continue
                        s4_sl = sig["s4_sl"]
                        if mark > s4_sl:
                            logger.info(f"[S4][{symbol}] ❌ Invalidated — mark {mark:.5f} > SL {s4_sl:.5f}")
                            st.add_scan_log(f"[S4][{symbol}] ❌ Pending cancelled (price above SL)", "INFO")
                            self.pending_signals.pop(symbol, None)
                            st.save_pending_signals(self.pending_signals)
                            continue
                        s4_trigger = sig["trigger"]
                        prev_low   = sig["prev_low"]
                        in_window  = (mark <= s4_trigger and
                                      mark >= prev_low * (1 - config_s4.S4_MAX_ENTRY_BUFFER))
                        if in_window:
                            with self._trade_lock:
                                if symbol in self.active_positions:
                                    self.pending_signals.pop(symbol, None)
                                    st.save_pending_signals(self.pending_signals)
                                    continue
                                if len(self.active_positions) >= config.MAX_CONCURRENT_TRADES:
                                    break
                                if st.is_pair_paused(symbol):
                                    continue
                                self._fire_s4(symbol, sig, mark, balance)
                            self.pending_signals.pop(symbol, None)
                            st.save_pending_signals(self.pending_signals)

                    elif strategy == "S6":
                        # ── S6: two-phase V-formation ─────────────────── #
                        if self.sentiment and self.sentiment.direction == "BULLISH":
                            logger.info(f"[S6][{symbol}] 🚫 Cancelled — sentiment BULLISH")
                            st.add_scan_log(f"[S6][{symbol}] 🚫 Cancelled (BULLISH)", "WARN")
                            self.pending_signals.pop(symbol, None)
                            st.save_pending_signals(self.pending_signals)
                            continue
                        ps = st.get_pair_state(symbol)
                        if ps.get("s6_signal", "HOLD") not in ("PENDING_SHORT",):
                            logger.info(f"[S6][{symbol}] 🚫 Signal gone — cancelling watcher")
                            self.pending_signals.pop(symbol, None)
                            st.save_pending_signals(self.pending_signals)
                            continue
                        try:
                            mark = tr.get_mark_price(symbol)
                        except Exception:
                            continue
                        peak = sig["peak_level"]
                        if not sig.get("fakeout_seen"):
                            if mark > peak:
                                sig["fakeout_seen"] = True
                                st.patch_pair_state(symbol, {"s6_fakeout_seen": True})
                                st.save_pending_signals(self.pending_signals)
                                logger.info(f"[S6][{symbol}] 🚀 Phase 1 — fakeout above peak {peak:.5f}")
                                st.add_scan_log(f"[S6][{symbol}] Phase 1 fakeout above {peak:.5f}", "INFO")
                        else:
                            if mark < peak:
                                with self._trade_lock:
                                    if symbol in self.active_positions:
                                        self.pending_signals.pop(symbol, None)
                                        st.save_pending_signals(self.pending_signals)
                                        continue
                                    if len(self.active_positions) >= config.MAX_CONCURRENT_TRADES:
                                        break
                                    if st.is_pair_paused(symbol):
                                        continue
                                    self._fire_s6(symbol, sig, mark, balance)
                                self.pending_signals.pop(symbol, None)
                                st.save_pending_signals(self.pending_signals)

                    else:
                        # ── Unknown strategy: expire stale signals ──── #
                        if time.time() > sig.get("expires", 0):
                            logger.info(f"[{strategy}][{symbol}] ⏰ Pending signal expired — removing")
                            st.add_scan_log(f"[{strategy}][{symbol}] ⏰ Pending expired", "INFO")
                            self.pending_signals.pop(symbol, None)
                            st.save_pending_signals(self.pending_signals)
```

Also remove the `balance = tr.get_usdt_balance()` call that was only needed for the non-S5 legacy path — move it to just before any fire call. Actually, keep it at the top of the pending_signals loop since all strategies need it.

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_bot_entry_watcher_all.py -k "watcher_fires_s2 or watcher_cancels_s2 or watcher_s6" -v
```

Expected: all 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add bot.py tests/test_bot_entry_watcher_all.py
git commit -m "feat: entry watcher handles S2/S3/S4/S6 with trigger, invalidation, signal check"
```

---

## Task 5: bot.py — update _execute_best_candidate to queue S2/S3/S4/S6

**Files:**
- Modify: `bot.py` (`_execute_best_candidate`)

- [ ] **Step 1: Write failing test**

Append to `tests/test_bot_entry_watcher_all.py`:

```python
def _make_full_bot(monkeypatch) -> bot.MTFBot:
    b = _make_bot(monkeypatch)
    b.candidates = []
    b.sentiment  = type("S", (), {"direction": "NEUTRAL"})()
    monkeypatch.setattr(bot, "config", bot.config)
    monkeypatch.setattr(bot.st, "is_pair_paused", lambda sym: False)
    monkeypatch.setattr(bot.st, "patch_pair_state", lambda *a, **kw: None)
    return b


def test_execute_best_candidate_queues_s2(monkeypatch):
    """S2 LONG candidate is queued (not executed immediately) via _execute_best_candidate."""
    b = _make_full_bot(monkeypatch)
    queued = []
    monkeypatch.setattr(b, "_queue_s2_pending", lambda c: queued.append(c["symbol"]))
    b.candidates = [dict(_make_s2_candidate(), sig="LONG")]
    b._execute_best_candidate("BULLISH", 1000.0)
    assert "BTCUSDT" in queued


def test_execute_best_candidate_does_not_requeue_existing_s2(monkeypatch):
    """If S2 already in pending_signals, _execute_best_candidate skips re-queuing."""
    b = _make_full_bot(monkeypatch)
    b.pending_signals["BTCUSDT"] = _make_s2_sig()
    queued = []
    monkeypatch.setattr(b, "_queue_s2_pending", lambda c: queued.append(c["symbol"]))
    b.candidates = [dict(_make_s2_candidate(), sig="LONG")]
    b._execute_best_candidate("BULLISH", 1000.0)
    assert queued == [], "Should not re-queue if already pending"


def test_execute_best_candidate_queues_s6(monkeypatch):
    """S6 PENDING_SHORT uses _queue_s6_pending (not _queue_s6_watcher)."""
    b = _make_full_bot(monkeypatch)
    queued = []
    monkeypatch.setattr(b, "_queue_s6_pending", lambda c: queued.append(c["symbol"]))
    b.candidates = [dict(_make_s6_candidate(), sig="PENDING_SHORT")]
    b._execute_best_candidate("BEARISH", 1000.0)
    assert "BNBUSDT" in queued
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_bot_entry_watcher_all.py -k "execute_best_candidate_queues" -v
```

Expected: `FAILED` — `_execute_best_candidate` still calls `_execute_s2` directly.

- [ ] **Step 3: Update _execute_best_candidate in bot.py**

Replace the S2/S3/S4 dispatch and S6 routing. Find the block starting at line ~1216 and update:

```python
        _dispatchers = {
            "S1": self._execute_s1,
        }

        for candidate in ranked:
            sym      = candidate["symbol"]
            sig      = candidate["sig"]
            strategy = candidate["strategy"]

            if sig in ("PENDING_LONG", "PENDING_SHORT"):
                if strategy == "S6":
                    if sym not in self.pending_signals and not st.is_pair_paused(sym):
                        self._queue_s6_pending(candidate)
                elif strategy == "S5":
                    if sym not in self.pending_signals and not st.is_pair_paused(sym):
                        self._queue_s5_pending(
                            sym, sig, candidate["trigger"], candidate["sl"], candidate["tp"],
                            candidate["ob_low"], candidate["ob_high"], candidate["m15_df"],
                            priority_rank=candidate["priority_rank"],
                            priority_score=candidate["priority_score"],
                        )
                continue

            # Immediate LONG/SHORT — stop if slots full
            if len(self.active_positions) >= config.MAX_CONCURRENT_TRADES:
                break
            if sym in self.active_positions or st.is_pair_paused(sym):
                continue

            if strategy == "S2":
                if sym not in self.pending_signals:
                    min_bal = 5.0 / (config_s2.S2_TRADE_SIZE_PCT * config_s2.S2_LEVERAGE)
                    if balance >= min_bal:
                        self._queue_s2_pending(candidate)
            elif strategy == "S3":
                if sym not in self.pending_signals:
                    min_bal = 5.0 / (config_s3.S3_TRADE_SIZE_PCT * config_s3.S3_LEVERAGE)
                    if balance >= min_bal:
                        self._queue_s3_pending(candidate)
            elif strategy == "S4":
                if sym not in self.pending_signals:
                    min_bal = 5.0 / (config_s4.S4_TRADE_SIZE_PCT * config_s4.S4_LEVERAGE)
                    if balance >= min_bal:
                        self._queue_s4_pending(candidate)
            elif strategy == "S5":
                min_bal = 5.0 / (config_s5.S5_TRADE_SIZE_PCT * config_s5.S5_LEVERAGE)
                if balance < min_bal:
                    continue
                self._execute_s5(
                    sym, sig, candidate["trigger"], candidate["sl"], candidate["tp"],
                    candidate["ob_low"], candidate["ob_high"], candidate["reason"],
                    candidate["m15_df"], balance,
                )
            elif strategy in _dispatchers:
                min_bal = 5.0 / (config_s1.TRADE_SIZE_PCT * config_s1.LEVERAGE)
                if balance < min_bal:
                    continue
                _dispatchers[strategy](candidate, balance)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_bot_entry_watcher_all.py -k "execute_best_candidate_queues" -v
```

Expected: all 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add bot.py tests/test_bot_entry_watcher_all.py
git commit -m "feat: _execute_best_candidate queues S2/S3/S4/S6 instead of executing immediately"
```

---

## Task 6: bot.py — startup: load pending_signals + remove s6_watchers

**Files:**
- Modify: `bot.py` (`__init__`, `run`, `_tick`)

- [ ] **Step 1: Update `__init__` in bot.py**

Remove `self.s6_watchers` and load pending_signals from state:

```python
def __init__(self):
    self.running         = True
    self.active_positions: dict[str, dict] = {}
    self.last_scan_time  = 0
    self.qualified_pairs : list[str] = []
    self.sentiment       = None
    # Entry watcher — pending signals waiting for price trigger (all strategies)
    self.pending_signals: dict[str, dict] = st.load_pending_signals()
    self._trade_lock = threading.Lock()
    self.candidates: list = []
    # s6_watchers removed — S6 now uses pending_signals
    ...
```

Remove `self.s6_watchers: dict[str, dict] = {}` (line 265).

- [ ] **Step 2: Remove _process_s6_watchers call from _tick**

In `_tick`, remove:
```python
        # ── 8. Process S6 two-phase entry watchers ────────────────── #
        self._process_s6_watchers()
```

- [ ] **Step 3: Remove dead code**

Delete these methods entirely from bot.py:
- `_queue_s6_watcher()` (~lines 1929–1953)
- `_process_s6_watchers()` (~lines 1820–1861)
- `_execute_s6()` (~lines 1774–1818) — logic now in `_fire_s6`
- `_execute_s2()`, `_execute_s3()`, `_execute_s4()` (~lines 1433–1639) — logic now in `_fire_s2/s3/s4`

- [ ] **Step 4: Run full test suite**

```bash
pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: all existing tests pass, no reference to deleted methods.

- [ ] **Step 5: Commit**

```bash
git add bot.py
git commit -m "refactor: remove s6_watchers + dead execute/queue methods; load pending_signals on startup"
```

---

## Task 7: Final verification and merge prep

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/ -v 2>&1 | tail -40
```

Expected: all PASS, no FAIL.

- [ ] **Step 2: Smoke test imports**

```bash
python -c "import bot; print('bot OK')"
python -c "import state; print('state OK')"
```

Expected: both print OK without errors.

- [ ] **Step 3: Verify s6_watchers completely removed**

```bash
grep -n "s6_watchers\|_process_s6_watchers\|_queue_s6_watcher\|_execute_s6\|_execute_s2\|_execute_s3\|_execute_s4" bot.py
```

Expected: no matches (these are all replaced).

- [ ] **Step 4: Commit final cleanup**

```bash
git add -u
git commit -m "chore: verify entry watcher refactor complete — all strategies route through 4s watcher"
```

- [ ] **Step 5: Open PR**

```bash
gh pr create \
  --title "feat: entry watcher for all strategies (S2/S3/S4/S6)" \
  --body "Routes S2/S3/S4/S6 through the 4s entry watcher. Signal-based cancellation via pair_states. Lightweight payloads persisted to state.json for restart survival. S6 merged into pending_signals."
```
