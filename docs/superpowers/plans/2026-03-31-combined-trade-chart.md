# Combined Trade Chart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a combined candlestick chart showing all trade lifecycle events (open, scale-in, partial, close) on one canvas using snapshot data — visible on both active trade cards and history rows.

**Architecture:** New `/api/trade-chart` endpoint in `dashboard.py` loads all snapshots for a `trade_id`, merges their candle arrays by timestamp (dedup + sort), and returns a unified `{candles, events}` response. Frontend renders with a new `_drawTradeChart()` canvas function reusing the existing entry chart modal. The existing `openEntryChart` remains untouched as a fallback for old trades without snapshots.

**Tech Stack:** Python/FastAPI (backend), HTML5 Canvas (frontend), existing `snapshot.py` API.

---

## File Map

| File | Change |
|------|--------|
| `dashboard.py` | Add `get_trade_chart()` function + `@app.get("/api/trade-chart")` route |
| `dashboard.html` | Add `openCombinedChart()`, `_drawTradeChart()`, 📊 on active cards, update history 📊 |
| `tests/test_trade_chart.py` | Add 5 new tests for the endpoint |

---

## Task 1: Backend — `/api/trade-chart` endpoint

**Files:**
- Modify: `dashboard.py` (after the `get_entry_chart` route, around line 800)
- Test: `tests/test_trade_chart.py`

---

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_trade_chart.py`:

```python
def test_trade_chart_missing_trade_id(monkeypatch):
    """Missing trade_id must return 400."""
    from fastapi.testclient import TestClient
    import dashboard
    client = TestClient(dashboard.app)
    resp = client.get("/api/trade-chart")
    assert resp.status_code == 400
    assert resp.json()["error"] == "trade_id required"


def test_trade_chart_no_snapshots(tmp_path, monkeypatch):
    """No snapshots for trade_id must return 404."""
    import snapshot
    from fastapi.testclient import TestClient
    import dashboard
    monkeypatch.setattr(snapshot, "_SNAP_DIR", tmp_path)
    client = TestClient(dashboard.app)
    resp = client.get("/api/trade-chart", params={"trade_id": "nosuchid"})
    assert resp.status_code == 404
    assert resp.json()["error"] == "no snapshots found"


def test_trade_chart_single_snapshot_active_trade(tmp_path, monkeypatch):
    """Active trade with only open snapshot → 200 with 1 event, candles present."""
    import snapshot
    from fastapi.testclient import TestClient
    import dashboard

    monkeypatch.setattr(snapshot, "_SNAP_DIR", tmp_path)

    candles = [
        {"t": i * 900_000, "o": 1.0, "h": 1.1, "l": 0.9, "c": 1.05, "v": 100.0}
        for i in range(25)
    ]
    snapshot.save_snapshot(
        "active01", "open", "ARCUSDT", "15m", candles, 1.0,
        captured_at="1970-01-01T02:00:00+00:00",  # t=7200000ms = index 8
    )

    client = TestClient(dashboard.app)
    resp = client.get("/api/trade-chart", params={
        "trade_id": "active01", "side": "LONG",
        "sl": 0.9, "tp": 1.15, "strategy": "S3",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["symbol"] == "ARCUSDT"
    assert data["interval"] == "15m"
    assert data["side"] == "LONG"
    assert data["strategy"] == "S3"
    assert len(data["candles"]) == 25
    assert len(data["events"]) == 1
    ev = data["events"][0]
    assert ev["type"] == "open"
    assert ev["candle_idx"] == 8
    assert ev["price"] == 1.0
    assert ev["sl"] == 0.9
    assert ev["tp"] == 1.15


def test_trade_chart_merges_multiple_snapshots(tmp_path, monkeypatch):
    """Open + close snapshots → merged candle array with correct candle_idx values."""
    import snapshot
    from fastapi.testclient import TestClient
    import dashboard

    monkeypatch.setattr(snapshot, "_SNAP_DIR", tmp_path)

    # open: candles t=0..18*900000 (19 candles), captured at t=8*900000=7200000ms
    candles_open = [
        {"t": i * 900_000, "o": 1.0, "h": 1.1, "l": 0.9, "c": 1.05, "v": 100.0}
        for i in range(19)
    ]
    # close: candles t=8..26*900000 (19 candles, overlap with open), captured at t=26*900000=23400000ms
    candles_close = [
        {"t": (8 + i) * 900_000, "o": 1.2, "h": 1.3, "l": 1.1, "c": 1.25, "v": 200.0}
        for i in range(19)
    ]
    snapshot.save_snapshot(
        "t1", "open", "BTCUSDT", "15m", candles_open, 1.0,
        captured_at="1970-01-01T02:00:00+00:00",  # 7200s = 7200000ms = t=8*900000
    )
    snapshot.save_snapshot(
        "t1", "close", "BTCUSDT", "15m", candles_close, 1.25,
        captured_at="1970-01-01T06:30:00+00:00",  # 23400s = 23400000ms = t=26*900000
    )

    client = TestClient(dashboard.app)
    resp = client.get("/api/trade-chart", params={
        "trade_id": "t1", "side": "LONG", "sl": 0.85, "tp": 1.4, "strategy": "S3",
    })
    assert resp.status_code == 200
    data = resp.json()
    # union: 0..18 from open + 8..26 from close → 0..26 = 27 unique timestamps
    assert len(data["candles"]) == 27
    assert data["events"][0]["type"] == "open"
    assert data["events"][0]["candle_idx"] == 8
    assert data["events"][1]["type"] == "close"
    assert data["events"][1]["candle_idx"] == 26


def test_trade_chart_later_snapshot_wins_on_overlap(tmp_path, monkeypatch):
    """When two snapshots share a timestamp, the later snapshot's candle is used."""
    import snapshot
    from fastapi.testclient import TestClient
    import dashboard

    monkeypatch.setattr(snapshot, "_SNAP_DIR", tmp_path)

    # Both have t=0 candle; close snapshot should overwrite open snapshot's version
    candles_open  = [{"t": 0, "o": 1.0, "h": 1.1, "l": 0.9, "c": 1.05, "v": 100.0}]
    candles_close = [{"t": 0, "o": 2.0, "h": 2.1, "l": 1.9, "c": 2.05, "v": 999.0}]

    snapshot.save_snapshot("dup", "open",  "BTCUSDT", "15m", candles_open,  1.0,
                           captured_at="1970-01-01T00:00:00+00:00")
    snapshot.save_snapshot("dup", "close", "BTCUSDT", "15m", candles_close, 2.0,
                           captured_at="1970-01-01T00:00:00+00:00")

    client = TestClient(dashboard.app)
    resp = client.get("/api/trade-chart", params={"trade_id": "dup", "side": "LONG"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["candles"]) == 1
    assert data["candles"][0]["v"] == 999.0  # close snapshot overwrote open
```

- [ ] **Step 2: Run tests to verify they all fail**

```bash
pytest tests/test_trade_chart.py::test_trade_chart_missing_trade_id tests/test_trade_chart.py::test_trade_chart_no_snapshots tests/test_trade_chart.py::test_trade_chart_single_snapshot_active_trade tests/test_trade_chart.py::test_trade_chart_merges_multiple_snapshots tests/test_trade_chart.py::test_trade_chart_later_snapshot_wins_on_overlap -v
```

Expected: all 5 FAIL with `404 Not Found` (route not yet defined).

- [ ] **Step 3: Implement `get_trade_chart` in `dashboard.py`**

Find the line after `get_entry_chart` ends (look for `@app.get("/api/ig/state")`) and insert before it:

```python
@app.get("/api/trade-chart")
def get_trade_chart(
    trade_id: str = "",
    side:     str   = "",
    sl:       float = 0.0,
    tp:       float = 0.0,
    strategy: str   = "",
):
    """
    Returns merged candle array + event list for all available snapshots of a trade.
    Candles from multiple snapshots are unioned by timestamp; later snapshot wins on overlap.
    """
    if not trade_id:
        return JSONResponse({"error": "trade_id required"}, status_code=400)

    import snapshot as _snap
    from datetime import datetime as _dt

    events_found = _snap.list_snapshots(trade_id)
    if not events_found:
        return JSONResponse({"error": "no snapshots found"}, status_code=404)

    EVENT_ORDER = ["open", "scale_in", "partial", "close"]
    candle_map: dict = {}   # t (int ms) → candle dict; later snapshot overwrites earlier
    loaded: list    = []    # [{event, snap}] in canonical order

    for event in EVENT_ORDER:
        if event not in events_found:
            continue
        snap = _snap.load_snapshot(trade_id, event)
        if not snap:
            continue
        for c in snap["candles"]:
            candle_map[int(c["t"])] = c
        loaded.append({"event": event, "snap": snap})

    if not loaded:
        return JSONResponse({"error": "no snapshots found"}, status_code=404)

    # Build sorted candle list
    candles = sorted(candle_map.values(), key=lambda c: int(c["t"]))
    ts_list = [int(c["t"]) for c in candles]

    # Map each event's captured_at to nearest candle index
    def _nearest_idx(captured_at: str) -> int:
        ts_ms = int(_dt.fromisoformat(captured_at).timestamp() * 1000)
        return min(range(len(ts_list)), key=lambda i: abs(ts_list[i] - ts_ms))

    events_out = []
    first_snap = loaded[0]["snap"]
    for item in loaded:
        ev_type = item["event"]
        snap    = item["snap"]
        ev: dict = {
            "type":       ev_type,
            "candle_idx": _nearest_idx(snap["captured_at"]),
            "price":      snap["event_price"],
        }
        if ev_type == "open":
            if sl:  ev["sl"] = sl
            if tp:  ev["tp"] = tp
        events_out.append(ev)

    return JSONResponse({
        "symbol":   first_snap["symbol"],
        "interval": first_snap["interval"],
        "strategy": strategy,
        "side":     side,
        "candles":  candles,
        "events":   events_out,
    })
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_trade_chart.py::test_trade_chart_missing_trade_id tests/test_trade_chart.py::test_trade_chart_no_snapshots tests/test_trade_chart.py::test_trade_chart_single_snapshot_active_trade tests/test_trade_chart.py::test_trade_chart_merges_multiple_snapshots tests/test_trade_chart.py::test_trade_chart_later_snapshot_wins_on_overlap -v
```

Expected: all 5 PASS.

- [ ] **Step 5: Run full test suite**

```bash
pytest tests/ -v --tb=short
```

Expected: all existing tests still PASS, 5 new tests PASS.

- [ ] **Step 6: Commit**

```bash
git add dashboard.py tests/test_trade_chart.py
git commit -m "feat: add /api/trade-chart endpoint for combined lifecycle chart"
```

---

## Task 2: Frontend — `_drawTradeChart` and `openCombinedChart`

**Files:**
- Modify: `dashboard.html`
  - Add `_drawTradeChart()` after `_drawEntryChart()` (around line 1723)
  - Add `openCombinedChart()` after `openEntryChart()` (around line 1556)

---

- [ ] **Step 1: Add `_drawTradeChart(canvas, data)` to `dashboard.html`**

Find the closing brace of `_drawEntryChart` (search for the last `}` before `// ── Entry Chart Modal` or the next top-level function). Insert the new function immediately after:

```js
// ── Combined Trade Lifecycle Chart ────────────────────────────────
function _drawTradeChart(canvas, data) {
  const { candles, events = [], side = 'LONG', symbol = '', strategy = '', interval = '15m' } = data;
  if (!candles || !candles.length) return;

  const dpr  = window.devicePixelRatio || 1;
  const W    = canvas.clientWidth;
  const H    = canvas.clientHeight;
  if (!W || !H) return;
  canvas.width  = W * dpr;
  canvas.height = H * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);

  const PAD_L = 8, PAD_R = 88, PAD_T = 24, PAD_B = 55, VOL_H = 30;
  const chartH = H - PAD_T - PAD_B - VOL_H - 6;

  const n       = candles.length;
  const candleW = Math.floor((W - PAD_L - PAD_R) / n);
  const gap     = Math.max(1, Math.round(candleW * 0.15));
  const bodyW   = candleW - gap * 2;
  const idxToX  = i => PAD_L + i * candleW + Math.floor(candleW / 2);

  // Event index map
  const evMap = {};
  events.forEach(e => { evMap[e.candle_idx] = e; });
  const openEv = events.find(e => e.type === 'open');

  // Price range
  const evPrices = events.map(e => e.price).filter(Boolean);
  const slTp = openEv ? [openEv.sl, openEv.tp].filter(p => p > 0) : [];
  const allPrices = [
    ...candles.map(c => c.l), ...candles.map(c => c.h),
    ...evPrices, ...slTp,
  ].filter(Boolean);
  let minP = Math.min(...allPrices);
  let maxP = Math.max(...allPrices);
  const padP = (maxP - minP) * 0.12;
  minP -= padP; maxP += padP;
  const p2y = p => PAD_T + chartH * (1 - (p - minP) / (maxP - minP));

  // Background
  ctx.fillStyle = '#0d1117';
  ctx.fillRect(0, 0, W, H);

  // Grid
  for (let gi = 0; gi <= 5; gi++) {
    const gy = PAD_T + gi * chartH / 5;
    const gp = maxP - gi * (maxP - minP) / 5;
    ctx.strokeStyle = '#21262d'; ctx.lineWidth = 0.5; ctx.setLineDash([]);
    ctx.beginPath(); ctx.moveTo(PAD_L, gy); ctx.lineTo(W - PAD_R, gy); ctx.stroke();
    ctx.fillStyle = '#484f58'; ctx.font = '9px monospace'; ctx.textAlign = 'left';
    ctx.fillText(gp.toFixed(4), W - PAD_R + 4, gy + 3);
  }

  // SL / TP / Entry price lines
  const hLine = (price, color, label, dash = [4, 3]) => {
    if (!price || price <= 0) return;
    const hy = p2y(price);
    ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.setLineDash(dash);
    ctx.beginPath(); ctx.moveTo(PAD_L, hy); ctx.lineTo(W - PAD_R, hy); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = color; ctx.font = 'bold 9px monospace'; ctx.textAlign = 'left';
    ctx.fillText(`${label} ${price.toFixed(4)}`, W - PAD_R + 4, hy - 2);
  };
  if (openEv) {
    hLine(openEv.sl, '#f85149', 'SL');
    hLine(openEv.tp, '#3fb950', 'TP');
    hLine(openEv.price, 'rgba(88,166,255,0.6)', 'ENT', [4, 3]);
  }

  // Event styling
  const EV_STYLE = {
    open:     { body: '#3fb950', wick: '#2ea043', bg: 'rgba(63,185,80,0.08)',   vol: 'rgba(63,185,80,0.5)'   },
    scale_in: { body: '#e3b341', wick: '#c9a030', bg: 'rgba(227,179,65,0.08)',  vol: 'rgba(227,179,65,0.5)'  },
    partial:  { body: '#a371f7', wick: '#7c4dff', bg: 'rgba(163,113,247,0.08)', vol: 'rgba(163,113,247,0.5)' },
    close:    { body: '#f85149', wick: '#da3633', bg: 'rgba(248,81,73,0.08)',   vol: 'rgba(248,81,73,0.5)'   },
  };

  // Candles + volume
  const volMax = Math.max(...candles.map(c => c.v));
  candles.forEach((c, i) => {
    const ev  = evMap[i];
    const x   = idxToX(i);
    const bull = c.c >= c.o;
    const sty  = ev ? EV_STYLE[ev.type] : null;

    if (sty) {
      ctx.fillStyle = sty.bg;
      ctx.fillRect(PAD_L + i * candleW, PAD_T, candleW, chartH);
    }

    ctx.strokeStyle = sty ? sty.wick : (bull ? '#2ea043' : '#da3633');
    ctx.lineWidth = 1; ctx.setLineDash([]);
    ctx.beginPath(); ctx.moveTo(x, p2y(c.h)); ctx.lineTo(x, p2y(c.l)); ctx.stroke();

    const yO   = p2y(c.o), yC = p2y(c.c);
    const bTop = Math.min(yO, yC);
    const bH   = Math.max(Math.abs(yC - yO), 1);
    ctx.fillStyle  = sty ? sty.body : (bull ? '#3fb950' : '#f85149');
    ctx.globalAlpha = sty ? 0.9 : (bull ? 0.75 : 0.6);
    ctx.fillRect(PAD_L + i * candleW + gap, bTop, bodyW, bH);
    ctx.globalAlpha = 1;

    const vBase = H - PAD_B;
    const vh    = Math.max(1, (c.v / volMax) * VOL_H);
    ctx.fillStyle = sty ? sty.vol : (bull ? 'rgba(62,175,84,0.25)' : 'rgba(248,81,73,0.25)');
    ctx.fillRect(PAD_L + i * candleW + gap, vBase - vh, bodyW, vh);
  });

  // White price tick at exact event_price inside each event candle
  events.forEach(e => {
    if (e.candle_idx >= n) return;
    const x1 = PAD_L + e.candle_idx * candleW;
    ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 2; ctx.setLineDash([]);
    ctx.beginPath(); ctx.moveTo(x1 + 1, p2y(e.price)); ctx.lineTo(x1 + candleW - 1, p2y(e.price)); ctx.stroke();
  });

  // Arrows
  const ARROW_H = 10, ARROW_W = 9, ARROW_GAP = 5;
  const isLong  = (side || '').toUpperCase() === 'LONG';

  // LONG: open/scale_in ▲ below candle, partial/close ▼ above candle
  // SHORT: reversed
  const opensEntry = t => t === 'open' || t === 'scale_in';
  const arrowUp = (i, color, label) => {
    const x   = idxToX(i);
    const tip = p2y(candles[i].l) + ARROW_GAP + ARROW_H;
    ctx.fillStyle = color;
    ctx.beginPath(); ctx.moveTo(x, tip - ARROW_H); ctx.lineTo(x - ARROW_W/2, tip); ctx.lineTo(x + ARROW_W/2, tip); ctx.closePath(); ctx.fill();
    ctx.fillStyle = color; ctx.font = 'bold 8px monospace'; ctx.textAlign = 'center';
    ctx.fillText(label, x, tip + 10);
  };
  const arrowDown = (i, color, label) => {
    const x   = idxToX(i);
    const tip = p2y(candles[i].h) - ARROW_GAP - ARROW_H;
    ctx.fillStyle = color;
    ctx.beginPath(); ctx.moveTo(x, tip + ARROW_H); ctx.lineTo(x - ARROW_W/2, tip); ctx.lineTo(x + ARROW_W/2, tip); ctx.closePath(); ctx.fill();
    ctx.fillStyle = color; ctx.font = 'bold 8px monospace'; ctx.textAlign = 'center';
    ctx.fillText(label, x, tip - 4);
  };

  const LABELS = { open: 'ENTRY', scale_in: 'SCALE', partial: 'PARTIAL', close: 'CLOSE' };
  events.forEach(e => {
    if (e.candle_idx >= n) return;
    const sty   = EV_STYLE[e.type] || { body: '#ffffff' };
    const goUp  = isLong ? opensEntry(e.type) : !opensEntry(e.type);
    if (goUp) arrowUp  (e.candle_idx, sty.body, LABELS[e.type] || e.type.toUpperCase());
    else      arrowDown(e.candle_idx, sty.body, LABELS[e.type] || e.type.toUpperCase());
  });

  // X-axis time labels
  ctx.fillStyle = '#6e7681'; ctx.font = '9px monospace'; ctx.textAlign = 'center';
  const step = Math.max(1, Math.floor(n / 8));
  candles.forEach((c, i) => {
    if (i % step !== 0) return;
    const d     = new Date(c.t);
    const label = interval === '1D'
      ? d.toISOString().slice(5, 10)    // "03-31"
      : d.toISOString().slice(11, 16);  // "18:21"
    ctx.fillText(label, idxToX(i), H - 4);
  });

  // Chart title
  ctx.fillStyle = '#8b949e'; ctx.font = 'bold 10px monospace'; ctx.textAlign = 'left';
  ctx.fillText(`${symbol} · ${strategy} · ${interval} · ${side}`, PAD_L + 4, PAD_T - 6);

  // Active trade badge (only open event present)
  if (events.length === 1 && events[0].type === 'open') {
    ctx.fillStyle = '#3fb950'; ctx.font = 'bold 9px monospace'; ctx.textAlign = 'right';
    ctx.fillText('● OPEN', W - PAD_R - 4, PAD_T - 6);
  }
}
```

- [ ] **Step 2: Add `openCombinedChart(trade)` to `dashboard.html`**

Find `async function openEntryChart(trade)` (around line 1493) and insert the following immediately **before** it:

```js
async function openCombinedChart(trade) {
  if (!trade || !trade.trade_id) return openEntryChart(trade);

  const interval = _ENTRY_CHART_INTERVAL[trade.strategy] || '15m';
  document.getElementById('entryChartTitle').textContent =
    `${trade.symbol} · ${trade.strategy || '?'} · ${interval} · ${(trade.open_at || '').slice(0,16).replace('T',' ')} UTC`;
  document.getElementById('entryChartLegend').innerHTML =
    '<span style="color:var(--muted)">Loading…</span>';
  document.getElementById('entryChartOverlay').classList.add('open');

  const params = new URLSearchParams({
    trade_id: trade.trade_id,
    side:     trade.side     || '',
    sl:       trade.sl       || 0,
    tp:       trade.tp       || 0,
    strategy: trade.strategy || '',
  });

  try {
    const resp = await fetch(`/api/trade-chart?${params}`);

    if (resp.status === 404) {
      // No snapshots yet — fall back to the existing entry chart
      document.getElementById('entryChartOverlay').classList.remove('open');
      return openEntryChart(trade);
    }

    const data = await resp.json();

    if (data.error) {
      document.getElementById('entryChartLegend').innerHTML =
        `<span style="color:var(--rose)">Error: ${data.error}</span>`;
      return;
    }

    // Legend
    const EV_META = {
      open:     { color: '#3fb950', label: (data.side||'').toUpperCase() === 'LONG' ? 'Entry ▲' : 'Entry ▼' },
      scale_in: { color: '#e3b341', label: 'Scale-in' },
      partial:  { color: '#a371f7', label: 'Partial' },
      close:    { color: '#f85149', label: (data.side||'').toUpperCase() === 'LONG' ? 'Close ▼' : 'Close ▲' },
    };
    const legendHtml = (data.events || []).map(e => {
      const m = EV_META[e.type] || { color: '#aaa', label: e.type };
      return `<span style="display:flex;align-items:center;gap:4px">` +
        `<span style="width:8px;height:8px;background:${m.color};border-radius:2px;display:inline-block"></span>` +
        `<span>${m.label} ${parseFloat(e.price).toFixed(4)}</span></span>`;
    }).join('');
    document.getElementById('entryChartLegend').innerHTML =
      legendHtml || '<span style="color:var(--muted)">No events</span>';

    const canvas = document.getElementById('entryChartCanvas');
    canvas.style.width  = '100%';
    canvas.style.height = '420px';

    requestAnimationFrame(() => { _drawTradeChart(canvas, data); });

  } catch (err) {
    document.getElementById('entryChartLegend').innerHTML =
      `<span style="color:var(--rose)">Failed to load chart: ${err.message}</span>`;
  }
}
```

- [ ] **Step 3: Verify no syntax errors**

Open the dashboard in a browser (or run `python -c "import dashboard"`) and confirm no JS console errors.

- [ ] **Step 4: Commit**

```bash
git add dashboard.html
git commit -m "feat: add _drawTradeChart and openCombinedChart to dashboard"
```

---

## Task 3: Frontend — wire 📊 buttons

**Files:**
- Modify: `dashboard.html`
  - History 📊 button: `openEntryChart` → `openCombinedChart`
  - Active trade cards: add 📊 button

---

- [ ] **Step 1: Update history 📊 button**

Find (around line 1481):
```js
${canChart ? `<span onclick="openEntryChart(window._histTrades[${i}])" title="Entry chart" style="cursor:pointer">📊</span>` : ''}
```

Replace with:
```js
${canChart ? `<span onclick="openCombinedChart(window._histTrades[${i}])" title="Trade chart" style="cursor:pointer">📊</span>` : ''}
```

The `openCombinedChart` function falls back to `openEntryChart` automatically when `trade_id` is absent (old trades without snapshots).

- [ ] **Step 2: Add 📊 button to active trade cards**

Find the active trade card template (around line 1419). The card currently ends with the `</div>` after the trade grid. Find this section:

```js
          <div class="trade-grid">
            <div class="tg-row"><span class="tg-key">Entry</span><span class="tg-val">$${fmtPrice(t.entry)}</span></div>
            <div class="tg-row"><span class="tg-key">uPnL</span><span class="tg-val ${pnlClass}">${fmtUSD(pnl)} <span style="font-size:9px;opacity:0.8">(${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(1)}%)</span></span></div>
            <div class="tg-row"><span class="tg-key">SL</span><span class="tg-val neg">$${fmtPrice(t.sl)}</span></div>
            <div class="tg-row"><span class="tg-key">TP</span><span class="tg-val pos">$${fmtPrice(t.tp)}</span></div>
            <div class="tg-row" style="grid-column:1/-1"><span class="tg-key">Margin</span><span class="tg-val">$${fmt(margin,2)} @ ${lev}x</span></div>
          </div>
        </div>`;
```

Replace with:
```js
          <div class="trade-grid">
            <div class="tg-row"><span class="tg-key">Entry</span><span class="tg-val">$${fmtPrice(t.entry)}</span></div>
            <div class="tg-row"><span class="tg-key">uPnL</span><span class="tg-val ${pnlClass}">${fmtUSD(pnl)} <span style="font-size:9px;opacity:0.8">(${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(1)}%)</span></span></div>
            <div class="tg-row"><span class="tg-key">SL</span><span class="tg-val neg">$${fmtPrice(t.sl)}</span></div>
            <div class="tg-row"><span class="tg-key">TP</span><span class="tg-val pos">$${fmtPrice(t.tp)}</span></div>
            <div class="tg-row" style="grid-column:1/-1"><span class="tg-key">Margin</span><span class="tg-val">$${fmt(margin,2)} @ ${lev}x</span></div>
          </div>
          ${t.trade_id ? `<div style="text-align:right;padding:4px 0 0;font-size:11px">
            <span onclick="openCombinedChart(${JSON.stringify({
              trade_id: t.trade_id, symbol: t.symbol, strategy: strat,
              side: t.side, entry: t.entry, sl: t.sl, tp: t.tp,
              open_at: t.open_at || '',
            })})" title="Trade chart" style="cursor:pointer;color:var(--muted)">📊</span>
          </div>` : ''}
        </div>`;
```

**Note:** Active trades in state have `trade_id` because `bot.py` sets `trade["trade_id"]` before calling `st.add_open_trade(trade)`. The `open_at` field may not be in the active trade state object — passing `''` is safe since `openCombinedChart` uses it only for the modal title.

- [ ] **Step 3: Run full test suite**

```bash
pytest tests/ -v --tb=short
```

Expected: all tests PASS.

- [ ] **Step 4: Smoke test in browser**

1. Open the dashboard
2. If there are active trades with `trade_id`, click their 📊 button → should open combined chart modal
3. Click history 📊 button on a trade with snapshots → should show combined chart
4. Click history 📊 button on an older trade without `trade_id` → should fall back to entry chart (no error)

- [ ] **Step 5: Commit**

```bash
git add dashboard.html
git commit -m "feat: wire 📊 buttons for combined trade chart on active + history"
```
