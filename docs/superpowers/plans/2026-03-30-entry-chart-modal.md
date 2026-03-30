# Entry Chart Modal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 📊 icon to each trade history row that opens a canvas-based entry chart modal showing the 25 candles around trade entry with strategy-specific highlights (S3: last red + uptick; S2: spike candle + box zone; S4: pump candle; S5: OB zone; S1: box zone).

**Architecture:** New `GET /api/entry-chart` endpoint in `dashboard.py` fetches historical candles via Bitget public API, computes strategy highlights, and returns JSON. `dashboard.html` renders the result onto a `<canvas>` element inside a new lightweight modal (no LightweightCharts). The existing full chart modal is untouched.

**Tech Stack:** FastAPI (dashboard.py), Canvas 2D API (dashboard.html), Bitget public REST API, existing `calculate_stoch` from `strategy.py`.

---

## File Map

| File | What changes |
|------|-------------|
| `dashboard.py` | Add `box_low`/`box_high` to `open_rows` dict (line ~70); add `GET /api/entry-chart` endpoint after line 199 |
| `dashboard.html` | Add `#entry-chart-modal` HTML after line 1225; add CSS after `.hist-row` block (line ~441); add `openEntryChart`, `closeEntryChart`, `_drawEntryChart`, `_renderEntryLegend` functions; add 📊 icon to hist-row template (line 1464) |
| `tests/test_ui.py` | Add `TestApiEntryChart` class; add HTML presence assertions for new modal + functions |

---

## Task 1: Add box_low / box_high to the trade record

**Files:**
- Modify: `dashboard.py:70-87`
- Test: `tests/test_ui.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ui.py` after the `test_trade_history_snap_fields_forwarded` test:

```python
def test_trade_history_includes_box_levels(tmp_path, monkeypatch):
    """box_low and box_high from the OPEN CSV row must appear in trade_history entries."""
    import csv, io, json
    import dashboard
    from starlette.testclient import TestClient

    fields = [
        "timestamp", "trade_id", "action", "symbol", "side", "qty", "entry", "sl", "tp",
        "box_low", "box_high", "leverage", "margin", "tpsl_set", "strategy",
        "snap_rsi", "snap_adx", "snap_htf", "snap_coil", "snap_box_range_pct", "snap_sentiment",
        "snap_daily_rsi", "snap_entry_trigger", "snap_sl", "snap_rr",
        "snap_rsi_peak", "snap_spike_body_pct", "snap_rsi_div", "snap_rsi_div_str",
        "snap_s5_ob_low", "snap_s5_ob_high", "snap_s5_tp", "snap_sr_clearance_pct",
        "result", "pnl", "pnl_pct", "exit_reason", "exit_price",
    ]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, restval="", extrasaction="ignore")
    w.writeheader()
    w.writerow({
        "timestamp": "2026-03-30T08:00:00+00:00",
        "trade_id": "box1", "action": "S2_LONG",
        "symbol": "ARIAUSDT", "side": "LONG",
        "entry": "0.35713", "sl": "0.3392", "tp": "0.39284",
        "box_low": "0.3200", "box_high": "0.3550",
    })
    w.writerow({
        "timestamp": "2026-03-30T09:00:00+00:00",
        "trade_id": "box1", "action": "S2_CLOSE",
        "symbol": "ARIAUSDT", "pnl": "3.0", "result": "WIN",
        "pnl_pct": "12.0", "exit_reason": "TP", "exit_price": "0.39284",
    })

    (tmp_path / "trades_paper.csv").write_text(buf.getvalue())
    (tmp_path / "state_paper.json").write_text(json.dumps({
        "status": "RUNNING", "started_at": "", "last_tick": "",
        "balance": 1000.0, "open_trades": {}, "trade_history": [],
        "scan_log": [], "qualified_pairs": [], "pair_states": {}, "sentiment": "NEUTRAL",
    }))
    monkeypatch.setattr(dashboard, "STATE_FILE", str(tmp_path / "state_paper.json"))

    resp = TestClient(dashboard.app, raise_server_exceptions=False).get("/api/state")
    hist = resp.json().get("trade_history", [])
    assert len(hist) >= 1
    entry = hist[0]
    assert entry.get("box_low") == 0.32, f"box_low not in trade record, got {entry.get('box_low')!r}"
    assert entry.get("box_high") == 0.355, f"box_high not in trade record, got {entry.get('box_high')!r}"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
venv/bin/pytest tests/test_ui.py::test_trade_history_includes_box_levels -v
```

Expected: FAIL — `box_low` is `None` or missing.

- [ ] **Step 3: Add box_low / box_high to open_rows in dashboard.py**

In `dashboard.py`, find the `open_rows[tid] = {` block (around line 70). Add two lines after `"tp": _safe_float(r.get("tp")),`:

```python
            if any(action.endswith(sfx) for sfx in ("_LONG", "_SHORT")):
                strategy = action.split("_")[0]
                open_rows[tid] = {
                    "entry":    _safe_float(r.get("entry")),
                    "sl":       _safe_float(r.get("sl")),
                    "tp":       _safe_float(r.get("tp")),
                    "box_low":  _safe_float(r.get("box_low")),
                    "box_high": _safe_float(r.get("box_high")),
                    "open_at":  r.get("timestamp", ""),
```

(Only the two new `box_low`/`box_high` lines are added — everything else is unchanged.)

- [ ] **Step 4: Run test to verify it passes**

```bash
venv/bin/pytest tests/test_ui.py::test_trade_history_includes_box_levels -v
```

Expected: PASS.

- [ ] **Step 5: Run full test suite to confirm no regressions**

```bash
venv/bin/pytest tests/test_ui.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add dashboard.py tests/test_ui.py
git commit -m "feat(dashboard): add box_low/box_high to trade history record"
```

---

## Task 2: Add /api/entry-chart endpoint

**Files:**
- Modify: `dashboard.py` (after line 199 — after existing `/api/candles` route)
- Test: `tests/test_ui.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ui.py` after the chat endpoint tests:

```python
# ── Entry chart endpoint tests ─────────────────────────────────────────────── #

class TestApiEntryChart:
    """Tests for GET /api/entry-chart."""

    # 35 mock candle rows (newest first, as Bitget returns them)
    # Timestamp base: 1774856700000 = 2026-03-30 07:45 UTC, step 900_000 ms (15m)
    MOCK_CANDLES = [
        [str(1774856700000 + i * 900_000), "3.477", "3.534", "3.477", "3.534", "1000.0", "0"]
        for i in range(35)
    ][::-1]  # newest first

    @pytest.fixture(autouse=True)
    def mock_bc(self, monkeypatch):
        import bitget_client as bc
        def _mock(path, params=None):
            if "/candles" in path:
                return {"data": self.MOCK_CANDLES}
            return {"data": []}
        monkeypatch.setattr(bc, "get_public", _mock)

    def test_returns_200(self):
        """/api/entry-chart returns HTTP 200."""
        resp = client.get("/api/entry-chart", params={
            "symbol": "UNIUSDT",
            "open_at": "2026-03-30T08:00:00+00:00",
            "strategy": "S3",
            "entry": "3.54",
            "sl": "3.462",
            "snap_sl": "3.452",
            "tp": "3.894",
            "snap_entry_trigger": "3.537",
        })
        assert resp.status_code == 200

    def test_response_shape(self):
        """Response has candles list, entry_ts int, highlights dict."""
        resp = client.get("/api/entry-chart", params={
            "symbol": "UNIUSDT",
            "open_at": "2026-03-30T08:00:00+00:00",
            "strategy": "S3",
            "entry": "3.54",
            "sl": "3.462",
            "snap_sl": "3.452",
            "tp": "3.894",
            "snap_entry_trigger": "3.537",
        })
        data = resp.json()
        assert "candles" in data, f"missing 'candles': {list(data.keys())}"
        assert "entry_ts" in data, f"missing 'entry_ts': {list(data.keys())}"
        assert "highlights" in data, f"missing 'highlights': {list(data.keys())}"
        assert isinstance(data["candles"], list)
        assert isinstance(data["highlights"], dict)
        assert len(data["candles"]) <= 25, f"expected ≤25 candles, got {len(data['candles'])}"

    def test_candle_ohlcv_fields(self):
        """Each candle has t, o, h, l, c, v fields."""
        resp = client.get("/api/entry-chart", params={
            "symbol": "UNIUSDT",
            "open_at": "2026-03-30T08:00:00+00:00",
            "strategy": "S3",
        })
        data = resp.json()
        assert data["candles"], "candles list is empty"
        c = data["candles"][0]
        for field in ("t", "o", "h", "l", "c", "v"):
            assert field in c, f"candle missing field '{field}': {list(c.keys())}"

    def test_s5_uses_ob_params(self):
        """S5 highlights come directly from snap_s5_ob_low/ob_high params."""
        resp = client.get("/api/entry-chart", params={
            "symbol": "WLDUSDT",
            "open_at": "2026-03-30T08:00:00+00:00",
            "strategy": "S5",
            "entry": "0.2849",
            "sl": "0.2817",
            "tp": "0.3134",
            "snap_s5_ob_low":  "0.2723",
            "snap_s5_ob_high": "0.2741",
        })
        data = resp.json()
        h = data.get("highlights", {})
        assert h.get("ob_low")  == 0.2723, f"ob_low wrong: {h.get('ob_low')}"
        assert h.get("ob_high") == 0.2741, f"ob_high wrong: {h.get('ob_high')}"

    def test_s1_uses_box_params(self):
        """S1 highlights come directly from box_low/box_high params."""
        resp = client.get("/api/entry-chart", params={
            "symbol": "BTCUSDT",
            "open_at": "2026-03-30T08:00:00+00:00",
            "strategy": "S1",
            "entry": "85000",
            "box_low": "84000",
            "box_high": "85500",
        })
        data = resp.json()
        h = data.get("highlights", {})
        assert h.get("box_low")  == 84000.0
        assert h.get("box_high") == 85500.0

    def test_missing_symbol_returns_error(self):
        """/api/entry-chart with no candle data returns JSON with error key."""
        import bitget_client as bc
        # Override to return empty for this one test
        original = bc.get_public
        bc.get_public = lambda path, params=None: {"data": []}
        try:
            resp = client.get("/api/entry-chart", params={
                "symbol": "FAKECOIN",
                "open_at": "2026-03-30T08:00:00+00:00",
                "strategy": "S3",
            })
            assert resp.status_code == 200
            assert "error" in resp.json()
        finally:
            bc.get_public = original
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
venv/bin/pytest tests/test_ui.py::TestApiEntryChart -v
```

Expected: all FAIL with 404 or attribute errors — endpoint doesn't exist yet.

- [ ] **Step 3: Implement the endpoint in dashboard.py**

Add after the `@app.get("/api/candles/{symbol}")` function (after its closing line, around line 580):

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
):
    """
    Returns 25 candles centred around entry (20 before + 5 after) plus
    strategy-specific highlight timestamps / zone levels.
    """
    try:
        import numpy as np
        import pandas as pd
        import bitget_client as bc
        from datetime import datetime
        from strategy import calculate_stoch

        interval = _STRATEGY_INTERVAL.get(strategy, "15m")
        interval_ms = {"3m": 180_000, "15m": 900_000, "1D": 86_400_000}.get(interval, 900_000)

        # Parse open_at ISO → ms
        open_ts_ms = int(datetime.fromisoformat(open_at).timestamp() * 1000)
        end_ts_ms  = open_ts_ms + 10 * interval_ms

        fetch_limit = 300 if interval != "1D" else 60
        granularity = "1Dutc" if interval == "1D" else interval

        raw = bc.get_public(
            "/api/v2/mix/market/candles",
            params={
                "symbol":      symbol,
                "productType": PRODUCT_TYPE,
                "granularity": granularity,
                "limit":       str(fetch_limit),
                "endTime":     str(end_ts_ms),
            },
        )
        rows = raw.get("data", [])
        if not rows:
            return JSONResponse({"error": "No candle data returned from exchange"})

        df = pd.DataFrame(rows, columns=["ts","open","high","low","close","vol","qvol"])
        df = df.astype({"ts": int, "open": float, "high": float,
                        "low": float, "close": float, "vol": float})
        df = df.sort_values("ts").reset_index(drop=True)

        # Find index of entry candle (first candle whose ts >= open_at)
        entry_idx = len(df) - 1
        for i, row in df.iterrows():
            if int(row["ts"]) >= open_ts_ms:
                entry_idx = i
                break

        # Trim to 25-candle window: 20 before + entry + 4 after
        start = max(0, entry_idx - 20)
        end   = min(len(df), entry_idx + 5)
        view  = df.iloc[start:end].reset_index(drop=True)

        candles = [
            {"t": int(r["ts"]), "o": r["open"], "h": r["high"],
             "l": r["low"],  "c": r["close"], "v": r["vol"]}
            for _, r in view.iterrows()
        ]
        entry_ts = int(df.iloc[entry_idx]["ts"])

        # ── Highlights ────────────────────────────────────────── #
        highlights: dict = {}

        if strategy == "S3":
            work = df.iloc[: entry_idx + 1].reset_index(drop=True)
            if len(work) >= 10:
                slow_k, _ = calculate_stoch(work, 5, 3)
                lookback8 = slow_k.iloc[-9:-1]
                os_pos = [i for i, v in enumerate(lookback8)
                          if not np.isnan(v) and v < 30]
                if os_pos:
                    last_os  = -(8 + 1) + os_pos[-1]
                    first_os = -(8 + 1) + os_pos[0]
                    after_os = work.iloc[last_os + 1: -1].reset_index(drop=True)
                    last_green = None
                    lg_idx = None
                    for j, (_, row) in enumerate(after_os.iloc[::-1].iterrows()):
                        if float(row["close"]) > float(row["open"]):
                            last_green = row
                            lg_idx = len(after_os) - 1 - j
                            break
                    if last_green is not None:
                        highlights["last_green_ts"] = int(last_green["ts"])
                        # Last red candle before the uptick
                        found_red = False
                        if lg_idx is not None:
                            for j in range(lg_idx - 1, -1, -1):
                                r2 = after_os.iloc[j]
                                if float(r2["close"]) < float(r2["open"]):
                                    highlights["last_red_ts"] = int(r2["ts"])
                                    found_red = True
                                    break
                        if not found_red:
                            # Fallback: last red in oversold period
                            os_period = work.iloc[first_os: last_os + 1]
                            for _, r2 in os_period.iloc[::-1].iterrows():
                                if float(r2["close"]) < float(r2["open"]):
                                    highlights["last_red_ts"] = int(r2["ts"])
                                    break

        elif strategy in ("S2", "S4"):
            # Spike = largest body candle in last 30 candles before entry
            lookback = df.iloc[max(0, entry_idx - 30): entry_idx]
            best, spike_ts = 0.0, None
            for _, row in lookback.iterrows():
                o = float(row["open"])
                body = abs(float(row["close"]) - o) / o if o > 0 else 0.0
                if body > best:
                    best, spike_ts = body, int(row["ts"])
            if spike_ts:
                highlights["spike_ts"] = spike_ts
            if strategy == "S2":
                bl, bh = _safe_float(box_low), _safe_float(box_high)
                if bl:  highlights["box_low"]  = bl
                if bh:  highlights["box_high"] = bh

        elif strategy == "S5":
            ol, oh = _safe_float(snap_s5_ob_low), _safe_float(snap_s5_ob_high)
            if ol: highlights["ob_low"]  = ol
            if oh: highlights["ob_high"] = oh

        elif strategy == "S1":
            bl, bh = _safe_float(box_low), _safe_float(box_high)
            if bl: highlights["box_low"]  = bl
            if bh: highlights["box_high"] = bh

        return JSONResponse({
            "candles":   candles,
            "entry_ts":  entry_ts,
            "highlights": highlights,
        })

    except Exception as exc:
        import traceback
        return JSONResponse({"error": str(exc), "detail": traceback.format_exc()}, status_code=200)
```

Also add `PRODUCT_TYPE` import at the top of the endpoint — check if it's already imported from trader/config. If not, add near the top of `dashboard.py` (after the existing `_STRATEGY_INTERVAL` dict):

```python
try:
    from trader import PRODUCT_TYPE
except Exception:
    PRODUCT_TYPE = "USDT-FUTURES"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
venv/bin/pytest tests/test_ui.py::TestApiEntryChart -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Run full test suite**

```bash
venv/bin/pytest tests/test_ui.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add dashboard.py tests/test_ui.py
git commit -m "feat(dashboard): add /api/entry-chart endpoint with strategy highlights"
```

---

## Task 3: Modal HTML + CSS + 📊 icon + JS skeleton

**Files:**
- Modify: `dashboard.html` (modal HTML, CSS, hist-row template, JS stub)
- Test: `tests/test_ui.py`

- [ ] **Step 1: Write the failing HTML presence tests**

Add to the `TestDashboardHtml` class in `tests/test_ui.py`:

```python
    def test_entry_chart_modal_exists(self):
        """#entry-chart-modal overlay must exist in dashboard.html."""
        assert 'id="entryChartOverlay"' in _html(), \
            '#entryChartOverlay element missing from dashboard.html'

    def test_openEntryChart_defined(self):
        """openEntryChart() function must be defined in dashboard.html."""
        assert "function openEntryChart(" in _html(), \
            "openEntryChart() missing from dashboard.html"

    def test_closeEntryChart_defined(self):
        """closeEntryChart() function must be defined in dashboard.html."""
        assert "function closeEntryChart(" in _html(), \
            "closeEntryChart() missing from dashboard.html"

    def test_drawEntryChart_defined(self):
        """_drawEntryChart() canvas renderer must be defined in dashboard.html."""
        assert "function _drawEntryChart(" in _html(), \
            "_drawEntryChart() missing from dashboard.html"

    def test_entry_chart_icon_in_hist_row(self):
        """📊 icon button must be present in the hist-row template."""
        assert "openEntryChart" in _html(), \
            "openEntryChart call missing from hist-row template"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
venv/bin/pytest tests/test_ui.py::TestDashboardHtml::test_entry_chart_modal_exists \
               tests/test_ui.py::TestDashboardHtml::test_openEntryChart_defined \
               tests/test_ui.py::TestDashboardHtml::test_closeEntryChart_defined \
               tests/test_ui.py::TestDashboardHtml::test_drawEntryChart_defined \
               tests/test_ui.py::TestDashboardHtml::test_entry_chart_icon_in_hist_row -v
```

Expected: all FAIL.

- [ ] **Step 3: Add modal HTML to dashboard.html**

Insert the following block in `dashboard.html` immediately after the closing `</div>` of the existing chart modal (after line 1225, before the `<script src="...lightweight-charts...">` tag):

```html
<!-- ── ENTRY CHART MODAL ── -->
<div class="chart-modal-overlay" id="entryChartOverlay" onclick="closeEntryChart(event)">
  <div class="chart-modal" id="entryChartModal" style="max-width:880px">
    <div class="chart-modal-header">
      <div class="chart-modal-title">
        <span id="entryChartTitle" style="font-size:13px">—</span>
      </div>
      <button class="chart-close-btn" onclick="closeEntryChart()">✕</button>
    </div>
    <div id="entryChartLegend"
         style="display:flex;flex-wrap:wrap;gap:12px;padding:4px 14px 0;font-size:10px"></div>
    <div style="padding:10px 12px 10px">
      <canvas id="entryChartCanvas" style="width:100%;height:320px;display:block"></canvas>
    </div>
  </div>
</div>
```

- [ ] **Step 4: Replace hist-row with explicit action buttons (no default click)**

The user's requirement: remove the default single-click=chat behavior; all actions must be explicit icon buttons.

In `dashboard.html`, find the entire hist-row template block (lines 1456–1465):

```javascript
        <div class="hist-row" style="cursor:pointer"
             onclick="if(event.detail===1)openChat(window._histTrades[${i}])"
             ondblclick="${canChart ? `openTradeChart(window._histTrades[${i}])` : ''}">
          <span class="hist-sym">${sym}</span>
          <span class="side-tag ${(t.side||'').toLowerCase()}" style="font-size:8px;">${t.side||'?'}</span>
          <span class="hist-badge ${res}">${res}</span>
          <span class="hist-pnl ${pnlClass}">${fmtUSD(pnl)}</span>
          <span class="hist-time">${relTime(t.closed_at)}</span>
          <span style="font-size:9px;color:var(--muted);margin-left:auto">💬${canChart ? ' ↗' : ''}</span>
        </div>
```

Replace it with (no onclick/ondblclick on the row div; separate icon buttons):

```javascript
        <div class="hist-row">
          <span class="hist-sym">${sym}</span>
          <span class="side-tag ${(t.side||'').toLowerCase()}" style="font-size:8px;">${t.side||'?'}</span>
          <span class="hist-badge ${res}">${res}</span>
          <span class="hist-pnl ${pnlClass}">${fmtUSD(pnl)}</span>
          <span class="hist-time">${relTime(t.closed_at)}</span>
          <span style="font-size:9px;color:var(--muted);margin-left:auto;display:flex;gap:7px;align-items:center">
            ${canChart ? `<span onclick="openEntryChart(window._histTrades[${i}])" title="Entry chart" style="cursor:pointer">📊</span>` : ''}
            <span onclick="openChat(window._histTrades[${i}])" title="AI analysis" style="cursor:pointer">💬</span>
            ${canChart ? `<span onclick="openTradeChart(window._histTrades[${i}])" title="Full chart" style="cursor:pointer">↗</span>` : ''}
          </span>
        </div>
```

- [ ] **Step 5: Add JS skeleton functions**

In `dashboard.html`, find the `// ── Trade Chat ──` comment block (around line 1470) and insert the following three function stubs **before** it:

```javascript
// ── Entry Chart Modal ──────────────────────────────────
async function openEntryChart(trade) {
  if (!trade || !trade.entry || !trade.open_at) return;
  document.getElementById('entryChartTitle').textContent =
    `${trade.symbol} · ${trade.strategy || '?'} · ${trade.interval || '15m'} · ${trade.open_at.slice(0,16).replace('T',' ')} UTC`;
  document.getElementById('entryChartLegend').innerHTML =
    '<span style="color:var(--muted)">Loading…</span>';
  document.getElementById('entryChartOverlay').classList.add('open');
  // Full implementation in Task 4
}

function closeEntryChart(e) {
  if (e && e.target !== document.getElementById('entryChartOverlay')) return;
  document.getElementById('entryChartOverlay').classList.remove('open');
}

function _drawEntryChart(canvas, opts) {
  // Full implementation in Task 4
}

function _renderEntryLegend(el, strategy, highlights) {
  // Full implementation in Task 4
}
```

- [ ] **Step 6: Run the new HTML tests**

```bash
venv/bin/pytest tests/test_ui.py::TestDashboardHtml -v
```

Expected: all pass including the 5 new tests.

- [ ] **Step 7: Commit**

```bash
git add dashboard.html tests/test_ui.py
git commit -m "feat(dashboard): add entry chart modal HTML/CSS + 📊 icon + JS stubs"
```

---

## Task 4: Complete canvas renderer and wire openEntryChart

**Files:**
- Modify: `dashboard.html` (replace stub functions with full implementations)

- [ ] **Step 1: Replace `_drawEntryChart` with the full canvas renderer**

In `dashboard.html`, replace the stub `function _drawEntryChart(canvas, opts) { /* ... */ }` with:

```javascript
function _drawEntryChart(canvas, opts) {
  const { candles, entry_ts, highlights = {}, entry = 0, sl = 0,
          snap_sl = 0, tp = 0, trigger = 0, strategy = '' } = opts;
  if (!candles || !candles.length) return;

  const dpr   = window.devicePixelRatio || 1;
  const W     = canvas.clientWidth;
  const H     = canvas.clientHeight;
  canvas.width  = W * dpr;
  canvas.height = H * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);

  const PAD_L = 8, PAD_R = 74, PAD_T = 18, PAD_B = 44, VOL_H = 35;
  const chartH = H - PAD_T - PAD_B - VOL_H - 6;

  // Price range — include all price lines
  const prices = [
    ...candles.map(c => c.l), ...candles.map(c => c.h),
    entry, sl, snap_sl, tp, trigger,
    highlights.ob_low, highlights.ob_high,
    highlights.box_low, highlights.box_high,
  ].filter(p => p && p > 0);
  let minP = Math.min(...prices);
  let maxP = Math.max(...prices);
  const padP = (maxP - minP) * 0.06;
  minP -= padP; maxP += padP;

  const priceToY = p => PAD_T + chartH * (1 - (p - minP) / (maxP - minP));
  const n       = candles.length;
  const candleW = Math.max(5, Math.floor((W - PAD_L - PAD_R) / n));
  const gap     = Math.max(1, Math.round(candleW * 0.15));
  const bodyW   = candleW - gap * 2;
  const idxToX  = i => PAD_L + i * candleW + Math.floor(candleW / 2);

  // Background
  ctx.fillStyle = '#0d1117';
  ctx.fillRect(0, 0, W, H);

  // Grid lines + price labels
  for (let gi = 0; gi <= 5; gi++) {
    const gy = PAD_T + gi * chartH / 5;
    const gp = maxP - gi * (maxP - minP) / 5;
    ctx.strokeStyle = '#21262d'; ctx.lineWidth = 0.5; ctx.setLineDash([]);
    ctx.beginPath(); ctx.moveTo(PAD_L, gy); ctx.lineTo(W - PAD_R, gy); ctx.stroke();
    ctx.fillStyle = '#484f58'; ctx.font = '9px monospace'; ctx.textAlign = 'left';
    ctx.fillText(_fmtEntryPrice(gp), W - PAD_R + 4, gy + 3);
  }

  // Zone shading (S1/S2 box or S5 OB)
  const zLow  = highlights.ob_low  || highlights.box_low;
  const zHigh = highlights.ob_high || highlights.box_high;
  if (zLow && zHigh) {
    const isOB    = !!highlights.ob_low;
    const yZHigh  = priceToY(zHigh);
    const yZLow   = priceToY(zLow);
    ctx.fillStyle   = isOB ? 'rgba(57,197,207,0.10)' : 'rgba(88,166,255,0.07)';
    ctx.fillRect(PAD_L, yZHigh, W - PAD_L - PAD_R, yZLow - yZHigh);
    ctx.strokeStyle = isOB ? 'rgba(57,197,207,0.5)' : 'rgba(88,166,255,0.35)';
    ctx.lineWidth   = 1; ctx.setLineDash(isOB ? [] : [4,3]);
    ctx.beginPath(); ctx.moveTo(PAD_L, yZHigh); ctx.lineTo(W - PAD_R, yZHigh); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(PAD_L, yZLow);  ctx.lineTo(W - PAD_R, yZLow);  ctx.stroke();
    ctx.setLineDash([]);
  }

  // Dashed price lines helper
  const hLine = (price, color, label) => {
    if (!price || price <= 0) return;
    const hy = priceToY(price);
    if (hy < PAD_T - 5 || hy > PAD_T + chartH + 5) return;
    ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.setLineDash([4,3]);
    ctx.beginPath(); ctx.moveTo(PAD_L, hy); ctx.lineTo(W - PAD_R, hy); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = color; ctx.font = 'bold 9px monospace'; ctx.textAlign = 'left';
    ctx.fillText(`${label} ${_fmtEntryPrice(price)}`, W - PAD_R + 4, hy - 2);
  };
  hLine(snap_sl,  '#7d3535', 'SL₀');
  hLine(sl,       '#f85149', 'SL');
  hLine(trigger,  '#e3b341', 'TRG');
  hLine(entry,    '#58a6ff', 'ENT');
  hLine(tp,       '#3fb950', 'TP');

  // Candles + labels
  const volMax = Math.max(...candles.map(c => c.v));
  candles.forEach((c, i) => {
    const x         = idxToX(i);
    const isEntry   = c.t === entry_ts;
    const isLastRed = c.t === highlights.last_red_ts;
    const isUptick  = c.t === highlights.last_green_ts;
    const isSpike   = c.t === highlights.spike_ts;
    const bull      = c.c >= c.o;

    // Background tint
    if (isEntry)   { ctx.fillStyle='rgba(88,166,255,0.09)';  ctx.fillRect(PAD_L+i*candleW,PAD_T,candleW,chartH); }
    if (isLastRed) { ctx.fillStyle='rgba(248,81,73,0.10)';   ctx.fillRect(PAD_L+i*candleW,PAD_T,candleW,chartH); }
    if (isUptick)  { ctx.fillStyle='rgba(227,179,65,0.10)';  ctx.fillRect(PAD_L+i*candleW,PAD_T,candleW,chartH); }
    if (isSpike)   {
      ctx.fillStyle = strategy==='S4' ? 'rgba(163,113,247,0.12)' : 'rgba(227,179,65,0.12)';
      ctx.fillRect(PAD_L+i*candleW,PAD_T,candleW,chartH);
    }

    const bodyColor = isEntry   ? '#f0f6fc'
                    : isUptick  ? '#f0c060'
                    : isLastRed ? '#ff6b6b'
                    : isSpike   ? (strategy==='S4' ? '#a371f7' : '#e3b341')
                    : bull      ? '#3fb950' : '#f85149';
    const wickColor = isEntry   ? '#c9d1d9'
                    : isUptick  ? '#c9a030'
                    : isLastRed ? '#cc4444'
                    : isSpike   ? (strategy==='S4' ? '#8b5cf6' : '#c9a030')
                    : bull      ? '#2ea043' : '#da3633';

    ctx.strokeStyle = wickColor; ctx.lineWidth = 1; ctx.setLineDash([]);
    ctx.beginPath(); ctx.moveTo(x, priceToY(c.h)); ctx.lineTo(x, priceToY(c.l)); ctx.stroke();

    const yO = priceToY(c.o), yC = priceToY(c.c);
    const bTop = Math.min(yO, yC);
    const bH   = Math.max(Math.abs(yC - yO), 1);
    ctx.fillStyle = bodyColor;
    ctx.fillRect(PAD_L + i*candleW + gap, bTop, bodyW, bH);
    if (!bull && !isEntry && !isLastRed && !isUptick && !isSpike) {
      ctx.strokeStyle='#da3633'; ctx.lineWidth=0.5;
      ctx.strokeRect(PAD_L + i*candleW + gap, bTop, bodyW, bH);
    }

    // Labels below candle
    ctx.font = 'bold 8px monospace'; ctx.textAlign = 'center';
    const lY = PAD_T + chartH + 12;
    if (isLastRed) { ctx.fillStyle='#ff6b6b'; ctx.fillText('LAST RED', x, lY); }
    if (isUptick)  { ctx.fillStyle='#e3b341'; ctx.fillText('UPTICK',   x, lY); }
    if (isEntry)   { ctx.fillStyle='#58a6ff'; ctx.fillText('▲ ENTRY',  x, lY); }
    if (isSpike)   { ctx.fillStyle = strategy==='S4' ? '#a371f7' : '#e3b341'; ctx.fillText('SPIKE', x, lY); }

    // Volume bar
    const vBase = H - PAD_B;
    const vh    = Math.max(1, (c.v / volMax) * VOL_H);
    ctx.fillStyle = isEntry ? 'rgba(88,166,255,0.5)'
                  : bull    ? 'rgba(62,175,84,0.3)' : 'rgba(248,81,73,0.3)';
    ctx.fillRect(PAD_L + i*candleW + gap, vBase - vh, bodyW, vh);
  });

  // X-axis time labels (every 4 candles)
  ctx.fillStyle='#6e7681'; ctx.font='9px monospace'; ctx.textAlign='center';
  candles.forEach((c, i) => {
    if (i % 4 === 0) {
      ctx.fillText(new Date(c.t).toISOString().slice(11,16), idxToX(i), H - 4);
    }
  });
}

function _fmtEntryPrice(p) {
  if (!p) return '0';
  if (p < 0.001) return p.toFixed(8);
  if (p < 0.01)  return p.toFixed(6);
  if (p < 1)     return p.toFixed(5);
  if (p < 10)    return p.toFixed(4);
  if (p < 1000)  return p.toFixed(3);
  return p.toFixed(2);
}
```

- [ ] **Step 2: Replace `_renderEntryLegend` stub with full implementation**

Replace the stub `function _renderEntryLegend(el, strategy, highlights) { }` with:

```javascript
function _renderEntryLegend(el, strategy, highlights) {
  const items = [
    ['#58a6ff', 'Entry'],
    ['#e3b341', 'Trigger'],
    ['#f85149', 'SL'],
    ['#7d3535', 'SL₀'],
    ['#3fb950', 'TP'],
  ];
  if (strategy === 'S3') {
    items.push(['#ff6b6b', 'Last red'], ['#f0c060', 'Uptick']);
  } else if (strategy === 'S2') {
    items.push(['#e3b341', 'Spike candle']);
    if (highlights.box_low) items.push(['rgba(88,166,255,0.5)', 'Box zone']);
  } else if (strategy === 'S4') {
    items.push(['#a371f7', 'Pump candle']);
  } else if (strategy === 'S5') {
    if (highlights.ob_low) items.push(['#39c5cf', 'OB zone']);
  } else if (strategy === 'S1') {
    if (highlights.box_low) items.push(['rgba(88,166,255,0.5)', 'Box zone']);
  }
  el.innerHTML = items.map(([color, label]) =>
    `<span style="display:flex;align-items:center;gap:4px">
       <span style="display:inline-block;width:14px;height:2px;background:${color};border-radius:1px"></span>
       <span style="color:var(--muted)">${label}</span>
     </span>`
  ).join('');
}
```

- [ ] **Step 3: Replace `openEntryChart` stub with full implementation**

Replace the stub `async function openEntryChart(trade) { ... }` with:

```javascript
async function openEntryChart(trade) {
  if (!trade || !trade.entry || !trade.open_at) return;
  document.getElementById('entryChartTitle').textContent =
    `${trade.symbol} · ${trade.strategy || '?'} · ${trade.interval || '15m'} · ${trade.open_at.slice(0,16).replace('T',' ')} UTC`;
  document.getElementById('entryChartLegend').innerHTML =
    '<span style="color:var(--muted)">Loading…</span>';
  document.getElementById('entryChartOverlay').classList.add('open');

  const params = new URLSearchParams({
    symbol:             trade.symbol             || '',
    open_at:            trade.open_at            || '',
    strategy:           trade.strategy           || '',
    entry:              trade.entry              || 0,
    sl:                 trade.sl                 || 0,
    snap_sl:            trade.snap_sl            || '',
    tp:                 trade.tp                 || 0,
    snap_entry_trigger: trade.snap_entry_trigger || '',
    box_low:            trade.box_low            || '',
    box_high:           trade.box_high           || '',
    snap_s5_ob_low:     trade.snap_s5_ob_low     || '',
    snap_s5_ob_high:    trade.snap_s5_ob_high    || '',
  });

  try {
    const resp = await fetch(`/api/entry-chart?${params}`);
    const data = await resp.json();

    if (data.error) {
      document.getElementById('entryChartLegend').innerHTML =
        `<span style="color:var(--rose)">Error: ${data.error}</span>`;
      return;
    }

    const canvas = document.getElementById('entryChartCanvas');
    // Force layout so clientWidth is valid
    canvas.style.width  = '100%';
    canvas.style.height = '320px';

    requestAnimationFrame(() => {
      _drawEntryChart(canvas, {
        ...data,
        entry:    trade.entry,
        sl:       trade.sl,
        snap_sl:  parseFloat(trade.snap_sl) || 0,
        tp:       trade.tp,
        trigger:  parseFloat(trade.snap_entry_trigger) || 0,
        strategy: trade.strategy,
      });
      _renderEntryLegend(
        document.getElementById('entryChartLegend'),
        trade.strategy,
        data.highlights || {},
      );
    });

  } catch (err) {
    document.getElementById('entryChartLegend').innerHTML =
      `<span style="color:var(--rose)">Failed to load chart: ${err.message}</span>`;
  }
}
```

- [ ] **Step 4: Run full test suite**

```bash
venv/bin/pytest tests/ -v
```

Expected: all pass.

- [ ] **Step 5: Manual smoke test**

```bash
# Ensure dashboard is running
cd /Users/kevin/Downloads/bitget_mtf_bot
venv/bin/python -m uvicorn dashboard:app --reload --port 8000
```

Open http://localhost:8000, click 📊 on any trade history row with S3 strategy. Verify:
- Modal opens with title showing symbol + strategy + interval + date
- 25 candles visible and readable
- LAST RED (rose), UPTICK (gold), ▲ ENTRY (white/blue) labels below their candles
- Price lines visible: ENT (blue), TRG (gold), SL (red), SL₀ (dark red), TP (green)
- Legend items match strategy

- [ ] **Step 6: Commit**

```bash
git add dashboard.html
git commit -m "feat(dashboard): implement entry chart canvas renderer for all strategies"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|-----------------|------|
| 📊 icon on hist-row | Task 3 step 4 |
| No default row click — all actions explicit buttons (📊 💬 ↗) | Task 3 step 4 |
| New modal `#entry-chart-modal` | Task 3 step 3 |
| `GET /api/entry-chart` endpoint | Task 2 step 3 |
| S3: last_red_ts + last_green_ts via Stoch | Task 2 step 3 |
| S2: spike candle + box zone | Task 2 step 3 |
| S4: spike candle | Task 2 step 3 |
| S5: ob_low/ob_high from params | Task 2 step 3 |
| S1: box_low/box_high from params | Task 2 + Task 1 |
| Canvas renderer: 25 candles | Task 4 step 1 |
| Canvas: LAST RED/UPTICK/ENTRY/SPIKE labels | Task 4 step 1 |
| Canvas: price lines (ENT/TRG/SL/SL₀/TP) | Task 4 step 1 |
| Zone shading S1/S2 box + S5 OB | Task 4 step 1 |
| Legend per strategy | Task 4 step 2 |
| box_low/box_high in trade record | Task 1 |
| No changes to bot.py, strategy.py, CSV columns | confirmed — not touched |
