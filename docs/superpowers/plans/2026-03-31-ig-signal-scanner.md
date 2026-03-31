# IG Signal Scanner & Scan Log — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-instrument S5 signal checklist and 20-entry scan log to the IG dashboard panel, matching the Bitget pair scanner visual style.

**Architecture:** `ig_bot.py` writes `scan_signals` (dict keyed by instrument) and `scan_log` (list, 20-cap) into `ig_state.json` after every `evaluate_s5()` call. `dashboard.py` reads and passes both fields through the existing `/api/ig/state` endpoint. `dashboard.html` renders instrument cards + log panel using existing pair-card CSS classes.

**Tech Stack:** Python (ig_bot.py, dashboard.py), vanilla JS + existing CSS (dashboard.html), pytest

---

## Files Changed

| File | Change |
|------|--------|
| `ig_bot.py` | Add `DISPLAY_NAME`, `_scan_signals`, `_scan_log`, `_update_scan_state()`; call after `evaluate_s5()`; update `_save_state()`; restore on startup |
| `dashboard.py` | Read `scan_signals` + `scan_log` from ig_state.json; include in `/api/ig/state` response |
| `dashboard.html` | Add scanner section + scan log panel; add `renderIGScanner()` + `renderIGScanLog()`; call from `renderIG()` |
| `docs/DEPENDENCIES.md` | Update §4.4 ig_state.json with new fields |
| `tests/test_ig_bot_scan_state.py` | New test file for scan state behaviour |

---

## Task 1: ig_bot.py — scan state fields, helper, _tick wiring, _save_state, startup restore

**Files:**
- Modify: `ig_bot.py`
- Create: `tests/test_ig_bot_scan_state.py`

- [ ] **Step 1.1: Write failing tests**

Create `tests/test_ig_bot_scan_state.py`:

```python
"""
Tests for IGBot scan state — scan_signals + scan_log.

Covers:
  1. _update_scan_state() writes correct entry to _scan_signals
  2. _scan_log gets entry prepended, capped at 20
  3. _save_state() persists scan_signals and scan_log to file
  4. Paper-mode startup restores scan_signals and scan_log from existing state file
"""
import sys
import os
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import ig_bot
import config_ig


def _make_bot(monkeypatch, paper=True):
    """Return an IGBot in paper mode with external calls patched out."""
    monkeypatch.setattr(ig_bot, "_in_trading_window", lambda now: True)
    monkeypatch.setattr(ig_bot, "_is_session_end",    lambda now: False)
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    monkeypatch.setattr(config_ig, "STATE_FILE", tmp.name)
    return ig_bot.IGBot(paper=paper)


# ── 1. _update_scan_state writes correct entry ─────────────────────── #

def test_update_scan_state_writes_signal_fields(monkeypatch):
    """_update_scan_state() stores all expected fields in _scan_signals."""
    bot = _make_bot(monkeypatch)
    bot._update_scan_state(
        instrument="US30",
        signal="PENDING_SHORT",
        reason="Daily EMA bearish ✅ | 1H BOS confirmed ✅ | OB ✅",
        ob_low=44210.0,
        ob_high=44380.0,
        entry_trigger=44380.0,
        sl=44450.0,
        tp=44210.0,
    )
    entry = bot._scan_signals["US30"]
    assert entry["signal"] == "PENDING_SHORT"
    assert entry["ema_ok"] is True
    assert entry["bos_ok"] is True
    assert entry["ob_ok"]  is True
    assert entry["ob_low"]  == 44210.0
    assert entry["ob_high"] == 44380.0
    assert entry["entry_trigger"] == 44380.0
    assert entry["sl"] == 44450.0
    assert entry["tp"] == 44210.0
    assert "updated_at" in entry


def test_update_scan_state_ema_false_when_flat(monkeypatch):
    """ema_ok is False when reason starts with 'Daily EMA flat'."""
    bot = _make_bot(monkeypatch)
    bot._update_scan_state("US30", "HOLD", "Daily EMA flat (EMAs not aligned)",
                            0.0, 0.0, 0.0, 0.0, 0.0)
    assert bot._scan_signals["US30"]["ema_ok"] is False
    assert bot._scan_signals["US30"]["bos_ok"] is False
    assert bot._scan_signals["US30"]["ob_ok"]  is False


def test_update_scan_state_bos_ok_but_no_ob(monkeypatch):
    """bos_ok=True, ob_ok=False when reason shows BOS ✅ but no OB found."""
    bot = _make_bot(monkeypatch)
    bot._update_scan_state("US30", "HOLD", "1H BOS ✅ | No bearish OB found (lookback=10)",
                            0.0, 0.0, 0.0, 0.0, 0.0)
    assert bot._scan_signals["US30"]["ema_ok"] is True
    assert bot._scan_signals["US30"]["bos_ok"] is True
    assert bot._scan_signals["US30"]["ob_ok"]  is False


# ── 2. _scan_log capped at 20, newest first ────────────────────────── #

def test_scan_log_prepends_entry(monkeypatch):
    """Each _update_scan_state() call prepends one entry to _scan_log."""
    bot = _make_bot(monkeypatch)
    bot._update_scan_state("US30", "HOLD", "Daily EMA flat", 0.0, 0.0, 0.0, 0.0, 0.0)
    assert len(bot._scan_log) == 1
    assert bot._scan_log[0]["instrument"] == "US30"
    assert bot._scan_log[0]["message"]    == "Daily EMA flat"
    assert "ts" in bot._scan_log[0]


def test_scan_log_newest_first(monkeypatch):
    """Newest entry is at index 0."""
    bot = _make_bot(monkeypatch)
    bot._update_scan_state("US30", "HOLD", "first",  0.0, 0.0, 0.0, 0.0, 0.0)
    bot._update_scan_state("US30", "HOLD", "second", 0.0, 0.0, 0.0, 0.0, 0.0)
    assert bot._scan_log[0]["message"] == "second"
    assert bot._scan_log[1]["message"] == "first"


def test_scan_log_capped_at_20(monkeypatch):
    """_scan_log never exceeds 20 entries."""
    bot = _make_bot(monkeypatch)
    for i in range(25):
        bot._update_scan_state("US30", "HOLD", f"msg-{i}", 0.0, 0.0, 0.0, 0.0, 0.0)
    assert len(bot._scan_log) == 20
    assert bot._scan_log[0]["message"] == "msg-24"


# ── 3. _save_state persists scan fields ───────────────────────────── #

def test_save_state_persists_scan_fields(monkeypatch):
    """_save_state() writes scan_signals and scan_log to the state file."""
    bot = _make_bot(monkeypatch)
    bot._update_scan_state("US30", "HOLD", "EMA flat", 0.0, 0.0, 0.0, 0.0, 0.0)
    bot._save_state()

    with open(config_ig.STATE_FILE) as f:
        data = json.load(f)

    assert "scan_signals" in data
    assert "US30" in data["scan_signals"]
    assert "scan_log" in data
    assert len(data["scan_log"]) == 1
    assert data["scan_log"][0]["message"] == "EMA flat"


# ── 4. Startup restores scan fields ───────────────────────────────── #

def test_startup_restores_scan_fields(monkeypatch):
    """Paper-mode IGBot restores scan_signals and scan_log from existing state file."""
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    json.dump({
        "position": None,
        "pending_order": None,
        "scan_signals": {"US30": {"signal": "HOLD", "reason": "restored", "ema_ok": False,
                                   "bos_ok": False, "ob_ok": False, "ob_low": None,
                                   "ob_high": None, "entry_trigger": None,
                                   "sl": None, "tp": None, "updated_at": "2026-01-01T00:00:00Z"}},
        "scan_log": [{"ts": "09:30", "instrument": "US30", "message": "restored"}],
    }, tmp)
    tmp.close()

    monkeypatch.setattr(ig_bot, "_in_trading_window", lambda now: True)
    monkeypatch.setattr(ig_bot, "_is_session_end",    lambda now: False)
    monkeypatch.setattr(config_ig, "STATE_FILE", tmp.name)

    bot = ig_bot.IGBot(paper=True)
    assert bot._scan_signals["US30"]["reason"] == "restored"
    assert bot._scan_log[0]["message"] == "restored"
```

- [ ] **Step 1.2: Run tests to confirm they all fail**

```bash
pytest tests/test_ig_bot_scan_state.py -v --tb=short
```

Expected: All 8 tests FAIL (AttributeError: 'IGBot' object has no attribute '_scan_signals' or similar)

- [ ] **Step 1.3: Add `DISPLAY_NAME` constant to ig_bot.py**

In `ig_bot.py`, after line 105 (`CONTRACT_SIZE = config_ig.CONTRACT_SIZE`):

```python
DISPLAY_NAME  = "US30"          # Human-readable instrument label used in scan_signals keys
```

- [ ] **Step 1.4: Add `_scan_signals` and `_scan_log` to `IGBot.__init__`**

In `ig_bot.py`, in `IGBot.__init__` (around line 314, after `self._candle_cache: dict[str, pd.DataFrame] = {}`), add:

```python
        self._scan_signals: dict = {}   # keyed by DISPLAY_NAME; latest evaluate_s5 output per instrument
        self._scan_log: list     = []   # last 20 scan entries, newest first
```

- [ ] **Step 1.5: Add `_update_scan_state()` helper method to `IGBot`**

Add this method to `IGBot`, after `_heartbeat()` (around line 421):

```python
    def _update_scan_state(
        self,
        instrument: str,
        signal: str,
        reason: str,
        ob_low: float,
        ob_high: float,
        entry_trigger: float,
        sl: float,
        tp: float,
    ) -> None:
        """Update per-instrument signal entry and prepend to scan log (capped at 20)."""
        _ET = zoneinfo.ZoneInfo("America/New_York")
        now_et = datetime.now(_ET)

        ema_ok = "Daily EMA flat" not in reason and "S5 disabled" not in reason
        bos_ok = "BOS \u2705" in reason or signal in ("PENDING_LONG", "PENDING_SHORT")
        ob_ok  = "OB \u2705"  in reason or signal in ("PENDING_LONG", "PENDING_SHORT")

        self._scan_signals[instrument] = {
            "signal":        signal,
            "reason":        reason,
            "ema_ok":        ema_ok,
            "bos_ok":        bos_ok,
            "ob_ok":         ob_ok,
            "ob_low":        ob_low  if ob_low  else None,
            "ob_high":       ob_high if ob_high else None,
            "entry_trigger": entry_trigger if entry_trigger else None,
            "sl":            sl if sl else None,
            "tp":            tp if tp else None,
            "updated_at":    datetime.now(timezone.utc).isoformat(),
        }

        self._scan_log.insert(0, {
            "ts":         now_et.strftime("%H:%M"),
            "instrument": instrument,
            "message":    reason,
        })
        self._scan_log = self._scan_log[:20]
```

- [ ] **Step 1.6: Update `_save_state()` to write scan fields**

Replace the existing `_save_state()` method (currently lines 409–411):

```python
    def _save_state(self) -> None:
        with open(config_ig.STATE_FILE, "w") as f:
            json.dump({
                "position":      self.position,
                "pending_order": self.pending_order,
                "scan_signals":  self._scan_signals,
                "scan_log":      self._scan_log,
            }, f, indent=2)
```

- [ ] **Step 1.7: Restore scan fields on startup — paper mode**

In `IGBot.__init__`, find the paper-mode block (around lines 333–342) that reads from the state file. Replace it:

```python
        if paper:
            # Restore position from paper state if any
            self.position = self._paper.position
            # Restore pending order and scan state from state file if present
            if os.path.exists(config_ig.STATE_FILE):
                try:
                    with open(config_ig.STATE_FILE) as _f:
                        _data = json.load(_f)
                    self.pending_order  = _data.get("pending_order")
                    self._scan_signals  = _data.get("scan_signals", {})
                    self._scan_log      = _data.get("scan_log", [])
                except Exception:
                    pass
```

- [ ] **Step 1.8: Restore scan fields on startup — live mode**

In `_sync_live_position()`, after restoring `pending_order` (around line 403–407), add:

```python
            self._scan_signals = data.get("scan_signals", {})
            self._scan_log     = data.get("scan_log", [])
```

The full updated `_sync_live_position()` tail should be:

```python
            saved_pending = data.get("pending_order")
            if saved_pending:
                self.pending_order = saved_pending
                logger.info(f"Restored pending order from state file: {saved_pending.get('deal_id')}")
            self._scan_signals = data.get("scan_signals", {})
            self._scan_log     = data.get("scan_log", [])
        except Exception as e:
            logger.warning(f"Could not restore state: {e}")
```

- [ ] **Step 1.9: Call `_update_scan_state()` + `_save_state()` in `_tick()` after `evaluate_s5()`**

In `_tick()`, find the evaluate_s5 call (around lines 488–494):

```python
        sig, trigger, sl, tp, ob_low, ob_high, reason = evaluate_s5(
            EPIC, daily_df, htf_df, m15_df, allowed_direction,
        )
        logger.info(f"[S5] {reason}")

        if sig not in ("PENDING_LONG", "PENDING_SHORT"):
            return
```

Replace with:

```python
        sig, trigger, sl, tp, ob_low, ob_high, reason = evaluate_s5(
            EPIC, daily_df, htf_df, m15_df, allowed_direction,
        )
        logger.info(f"[S5] {reason}")

        # Update scan state so dashboard always shows latest signal — save regardless of signal
        self._update_scan_state(DISPLAY_NAME, sig, reason, ob_low, ob_high, trigger, sl, tp)
        self._save_state()

        if sig not in ("PENDING_LONG", "PENDING_SHORT"):
            return
```

- [ ] **Step 1.10: Run tests — all 8 should pass**

```bash
pytest tests/test_ig_bot_scan_state.py -v --tb=short
```

Expected: 8 passed

- [ ] **Step 1.11: Run full test suite**

```bash
pytest tests/ -v --tb=short -q
```

Expected: All previously-passing tests still pass. No regressions.

- [ ] **Step 1.12: Commit**

```bash
git add ig_bot.py tests/test_ig_bot_scan_state.py
git commit -m "feat(ig_bot): add scan_signals + scan_log to state — S5 scanner for dashboard"
```

---

## Task 2: dashboard.py — pass scan_signals + scan_log through /api/ig/state

**Files:**
- Modify: `dashboard.py`
- Modify: `tests/test_ig_bot_scan_state.py` (add endpoint test)

- [ ] **Step 2.1: Write failing test**

Append to `tests/test_ig_bot_scan_state.py`:

```python
import httpx


def test_ig_state_endpoint_includes_scan_fields(live_server_url):
    """/api/ig/state response includes scan_signals and scan_log keys."""
    r = httpx.get(f"{live_server_url}/api/ig/state", timeout=5.0)
    assert r.status_code == 200
    data = r.json()
    assert "scan_signals" in data, "scan_signals missing from /api/ig/state response"
    assert "scan_log" in data,     "scan_log missing from /api/ig/state response"
    assert isinstance(data["scan_signals"], dict)
    assert isinstance(data["scan_log"], list)
```

- [ ] **Step 2.2: Run test to confirm it fails**

```bash
pytest tests/test_ig_bot_scan_state.py::test_ig_state_endpoint_includes_scan_fields -v --tb=short
```

Expected: FAIL — `scan_signals` not in response (KeyError or assertion error)

- [ ] **Step 2.3: Update `get_ig_state()` in dashboard.py**

In `dashboard.py`, find `get_ig_state()`. Replace the block that reads position (around lines 904–911):

```python
    # Read ig_state.json once — position, scan_signals, scan_log
    ig_state = {}
    if os.path.exists(IG_STATE_FILE):
        try:
            with open(IG_STATE_FILE) as f:
                ig_state = json.load(f)
        except Exception:
            pass

    position     = ig_state.get("position")
    scan_signals = ig_state.get("scan_signals", {})
    scan_log     = ig_state.get("scan_log", [])
```

Then update the `return JSONResponse(...)` at the end of `get_ig_state()` to include both new fields:

```python
    return JSONResponse({
        "bot_running":    bot_running,
        "session_active": session_active,
        "et_time":        now_et.strftime("%H:%M ET"),
        "position":       position,
        "trade_history":  trade_history,
        "stats":          stats,
        "scan_signals":   scan_signals,
        "scan_log":       scan_log,
    })
```

- [ ] **Step 2.4: Run test to confirm it passes**

```bash
pytest tests/test_ig_bot_scan_state.py::test_ig_state_endpoint_includes_scan_fields -v --tb=short
```

Expected: PASS

- [ ] **Step 2.5: Run full test suite**

```bash
pytest tests/ -v --tb=short -q
```

Expected: All previously-passing tests still pass.

- [ ] **Step 2.6: Commit**

```bash
git add dashboard.py tests/test_ig_bot_scan_state.py
git commit -m "feat(dashboard): pass scan_signals + scan_log through /api/ig/state"
```

---

## Task 3: dashboard.html — scanner section, scan log panel, render functions

**Files:**
- Modify: `dashboard.html`

No automated test for HTML rendering. Verify visually with the dashboard open and ig_bot running (or with a seeded ig_state.json).

- [ ] **Step 3.1: Add `.ig-scan-log-row` CSS**

In `dashboard.html`, find `.ig-pos-row` (around line 883). Insert after `.ig-pos-item` block (around line 888):

```css
  .ig-scan-log-row {
    display: flex; gap: 8px; align-items: baseline;
    padding: 3px 12px; border-bottom: 1px solid var(--border);
    font-size: 9px;
  }
  .ig-scan-log-row:last-child { border-bottom: none; }
  .ig-log-ts   { color: var(--muted); min-width: 34px; flex-shrink: 0; }
  .ig-log-inst { min-width: 32px; flex-shrink: 0; font-weight: 600; font-size: 8px; }
  .ig-log-inst.US30 { color: var(--blue); }
  .ig-log-inst.Gold { color: var(--amber); }
  .ig-log-msg  { color: var(--text2); line-height: 1.4; }
```

- [ ] **Step 3.2: Restructure IG panel HTML**

Find the `<div class="ig-grid">` block in `dashboard.html` (lines 1124–1148). Replace it entirely with:

```html
  <!-- Instrument Scanner -->
  <div class="panel" style="margin-top:8px;">
    <div class="panel-header">
      <span class="panel-title">Instrument Scanner</span>
    </div>
    <div class="panel-body" style="padding:8px;">
      <div class="pair-grid" id="ig-scanner-body"></div>
    </div>
  </div>

  <!-- Position + Scan Log -->
  <div class="ig-grid">

    <!-- Position card -->
    <div class="panel">
      <div class="panel-header">
        <span class="panel-title">Open Position</span>
        <span class="panel-sub" style="font-size:9px; color:var(--muted);">Wall Street Cash · US30</span>
      </div>
      <div id="ig-pos-body" class="panel-body">
        <div style="color:var(--muted); text-align:center; padding:40px 0; font-size:11px;">No open position</div>
      </div>
    </div>

    <!-- Scan Log -->
    <div class="panel">
      <div class="panel-header">
        <span class="panel-title">Scan Log</span>
        <span class="panel-sub" id="ig-log-count" style="font-size:9px; color:var(--muted);"></span>
      </div>
      <div id="ig-log-body" class="panel-body" style="padding:0; overflow-y:auto; max-height:260px;"></div>
    </div>

  </div>

  <!-- Trade History -->
  <div class="panel" style="margin-top:8px;">
    <div class="panel-header">
      <span class="panel-title">Trade History</span>
      <span class="panel-sub" id="ig-hist-count" style="font-size:9px; color:var(--muted);"></span>
    </div>
    <div id="ig-hist-body" class="panel-body" style="padding:0; overflow-y:auto; max-height:300px;">
      <div style="color:var(--muted); text-align:center; padding:40px 0; font-size:11px;">No trades yet</div>
    </div>
  </div>
```

- [ ] **Step 3.3: Add `renderIGScanner()` JS function**

In `dashboard.html`, find `function renderIGPosition(pos)` (around line 3388). Insert the two new functions immediately before it:

```javascript
function renderIGScanner(scanSignals) {
  const body = $('ig-scanner-body');
  if (!scanSignals || !Object.keys(scanSignals).length) {
    body.innerHTML = '<div style="color:var(--muted);font-size:10px;padding:8px 0;">No signal data yet — waiting for first scan…</div>';
    return;
  }
  body.innerHTML = Object.entries(scanSignals).map(([name, ps]) => {
    const sig = ps.signal || 'HOLD';
    // Normalise PENDING_LONG/SHORT → PENDING for badge
    const badgeSig = sig.startsWith('PENDING') ? 'PENDING' : sig;
    const cardClass = badgeSig === 'LONG'  ? 'signal-long'
                    : badgeSig === 'SHORT' ? 'signal-short' : '';
    const emaOk  = ps.ema_ok;
    const bosOk  = ps.bos_ok;
    const obOk   = ps.ob_ok;
    const limit  = sig.startsWith('PENDING');
    const obLabel = (ps.ob_low && ps.ob_high)
      ? `${(+ps.ob_low).toFixed(1)}–${(+ps.ob_high).toFixed(1)} ✓`
      : (bosOk ? 'Not found' : '—');
    const reason = (ps.reason || '').replace(/[✅]/g, '✓').slice(0, 80);
    const age = ps.updated_at
      ? (() => { const s = Math.floor((Date.now() - new Date(ps.updated_at)) / 1000);
                 return s < 60 ? s + 's ago' : Math.floor(s/60) + 'm ago'; })()
      : '';
    return `<div class="pair-card ${cardClass}">
      <div class="pair-top">
        <div class="pair-name">${name}</div>
        <span class="pair-sig-badge ${badgeSig}">${badgeSig}</span>
      </div>
      <div class="pair-checks">
        <div class="pair-check">
          <span class="check-label">Daily EMA</span>
          <span class="check-val ${emaOk ? 'pass' : 'muted'}">${emaOk ? 'Bias ✓' : 'No bias'}</span>
        </div>
        <div class="pair-check">
          <span class="check-label">1H BOS</span>
          <span class="check-val ${!emaOk ? 'muted' : bosOk ? 'pass' : 'muted'}">${!emaOk ? '—' : bosOk ? 'Confirmed ✓' : 'Not broken'}</span>
        </div>
        <div class="pair-check">
          <span class="check-label">15m OB</span>
          <span class="check-val ${!bosOk ? 'muted' : obOk ? 'pass' : 'muted'}">${!bosOk ? '—' : obLabel}</span>
        </div>
        <div class="pair-check">
          <span class="check-label">Limit Order</span>
          <span class="check-val ${!obOk ? 'muted' : limit ? 'warn' : 'muted'}">${!obOk ? '—' : limit ? 'Watching…' : 'None'}</span>
        </div>
      </div>
      <div class="pair-reason">${reason}${age ? ' · ' + age : ''}</div>
    </div>`;
  }).join('');
}

function renderIGScanLog(scanLog) {
  const body  = $('ig-log-body');
  const count = $('ig-log-count');
  if (!scanLog || !scanLog.length) {
    if (count) count.textContent = '';
    body.innerHTML = '<div style="color:var(--muted);text-align:center;padding:24px 0;font-size:10px;">No scan entries yet</div>';
    return;
  }
  if (count) count.textContent = `last ${scanLog.length}`;
  body.innerHTML = scanLog.map(e => {
    const inst = (e.instrument || '').replace(/[^A-Za-z0-9]/g, '');
    return `<div class="ig-scan-log-row">
      <span class="ig-log-ts">${e.ts || ''}</span>
      <span class="ig-log-inst ${inst}">${e.instrument || ''}</span>
      <span class="ig-log-msg">${(e.message || '').replace(/</g,'&lt;').replace(/>/g,'&gt;')}</span>
    </div>`;
  }).join('');
}
```

- [ ] **Step 3.4: Wire new render functions into `renderIG()`**

Find `function renderIG(d)` (around line 3367). Add two calls at the end:

```javascript
function renderIG(d) {
  const pnl = d.stats?.total_pnl ?? 0;
  const pnlEl = $('ig-h-pnl');
  pnlEl.textContent = (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(2);
  pnlEl.style.color = pnl >= 0 ? 'var(--emerald)' : 'var(--rose)';
  $('ig-h-trades').textContent = d.stats?.total ?? 0;
  $('ig-h-wr').textContent = d.stats?.win_rate ?? '—';
  $('ig-h-et').textContent = d.et_time ?? '—';

  const sb = $('ig-sess-badge'), st = $('ig-sess-text');
  sb.className = 'status-badge ' + (d.session_active ? 'running' : 'stopped');
  st.textContent = d.session_active ? 'In Session' : 'Closed';

  const bb = $('ig-bot-badge'), bt = $('ig-bot-text');
  bb.className = 'status-badge ' + (d.bot_running ? 'running' : 'stopped');
  bt.textContent = d.bot_running ? 'Running' : 'Stopped';

  renderIGScanner(d.scan_signals || {});
  renderIGScanLog(d.scan_log || []);
  renderIGPosition(d.position);
  renderIGHistory(d.trade_history);
}
```

- [ ] **Step 3.5: Verify visually with seeded data**

Seed ig_state.json for manual check:

```bash
python - <<'EOF'
import json, datetime
data = {
  "position": None,
  "pending_order": None,
  "scan_signals": {
    "US30": {
      "signal": "PENDING_SHORT",
      "reason": "Daily EMA bearish ✅ | 1H BOS confirmed ✅ | OB ✅ 44210–44380",
      "ema_ok": True, "bos_ok": True, "ob_ok": True,
      "ob_low": 44210, "ob_high": 44380,
      "entry_trigger": 44380, "sl": 44450, "tp": 44100,
      "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }
  },
  "scan_log": [
    {"ts": "10:24", "instrument": "US30", "message": "Daily EMA bearish ✅ | 1H BOS confirmed ✅ | OB ✅ 44210–44380"},
    {"ts": "10:19", "instrument": "US30", "message": "Daily EMA bearish ✅ | 1H BOS not confirmed (need close 44380 < swing low 44210)"},
    {"ts": "10:14", "instrument": "US30", "message": "Daily EMA flat (EMAs not aligned)"},
  ]
}
with open("ig_state.json", "w") as f:
    json.dump(data, f, indent=2)
print("Seeded ig_state.json")
EOF
python dashboard.py &
```

Open dashboard in browser, switch to IG tab. Verify:
- Instrument Scanner section shows US30 card with PENDING badge, all 4 checks ticked correctly
- Scan Log shows 3 entries with timestamps and instrument label
- Open Position and Trade History sections still work

Kill the dashboard: `pkill -f dashboard.py`

- [ ] **Step 3.6: Run full test suite**

```bash
pytest tests/ -v --tb=short -q
```

Expected: All previously-passing tests still pass.

- [ ] **Step 3.7: Commit**

```bash
git add dashboard.html
git commit -m "feat(dashboard): IG instrument scanner cards + scan log panel"
```

---

## Task 4: Update DEPENDENCIES.md — §4.4 ig_state.json

**Files:**
- Modify: `docs/DEPENDENCIES.md`

- [ ] **Step 4.1: Update §4.4 ig_state.json structure**

In `docs/DEPENDENCIES.md`, find `### 4.4 ig_state.json` and update the **Structure** block to add the two new top-level fields:

```python
{
  "position": {
    "trade_id": str,
    "side": "LONG" | "SHORT",
    "qty": float,
    "entry": float,
    "sl": float,
    "tp": float,
    "opened_at": str,  # ISO timestamp
  } | None,
  "pending_order": {
    "deal_id": str,
    "side": "LONG" | "SHORT",
    "ob_low": float,
    "ob_high": float,
    "sl": float,
    "tp": float,
    "trigger": float,
    "size": float,
    "expires": float,   # Unix timestamp
  } | None,
  "scan_signals": {
    "<DISPLAY_NAME>": {   # e.g. "US30"
      "signal":        str,   # "PENDING_LONG" | "PENDING_SHORT" | "HOLD"
      "reason":        str,   # full evaluate_s5 reason string
      "ema_ok":        bool,
      "bos_ok":        bool,
      "ob_ok":         bool,
      "ob_low":        float | None,
      "ob_high":       float | None,
      "entry_trigger": float | None,
      "sl":            float | None,
      "tp":            float | None,
      "updated_at":    str,   # ISO timestamp (UTC)
    }
  },
  "scan_log": [           # last 20 entries, newest first
    {
      "ts":         str,  # "HH:MM" in ET timezone
      "instrument": str,  # e.g. "US30"
      "message":    str,  # evaluate_s5 reason string
    }
  ]
}
```

Also add two new **Breaking scenarios** entries:

```
4. **Renaming "scan_signals" key** → Dashboard IG scanner panel shows no cards
   - Fix: Update dashboard.py get_ig_state() `.get("scan_signals", {})` and renderIGScanner() call

5. **Changing scan_signals entry field names** → Dashboard cards show wrong or missing check values
   - Fix: Update renderIGScanner() in dashboard.html to match new field names

6. **Renaming "scan_log" key** → Dashboard scan log panel stays empty
   - Fix: Update dashboard.py get_ig_state() `.get("scan_log", [])` and renderIGScanLog() call
```

Update the **Document History** entry:

```
- 2026-03-31: Updated Section 4.4 — ig_state.json gained scan_signals (per-instrument S5 signal state) and scan_log (last 20 scan entries) fields written by ig_bot.py after each evaluate_s5() call
```

- [ ] **Step 4.2: Commit**

```bash
git add docs/DEPENDENCIES.md
git commit -m "docs(deps): update §4.4 ig_state.json — add scan_signals + scan_log fields"
```

---

## Final Verification

- [ ] Run full test suite one more time:

```bash
pytest tests/ -v --tb=short -q
```

Expected: All previously-passing tests still pass, 8 new scan state tests pass.

- [ ] Push

```bash
git push origin master
```
