# Trade History Chart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make closed trade history rows clickable — clicking opens the chart modal showing the full trade lifecycle: entry/scale-in/partial/exit markers with stems+price ticks, SL/TP price lines, a shaded duration region, and automatic navigation to the trade timespan.

**Architecture:** Four files change. `paper_trader.py` surfaces fill price already stored in history. `bot.py` adds `exit_price` to the CSV schema. `dashboard.py` rewrites `_load_csv_history` to 2-pass match OPEN→CLOSE rows by `trade_id`. `dashboard.html` adds `openTradeChart()`, extends `loadChart()` with closed-trade overlay rendering (price lines, markers, SVG stems+ticks, DOM shading), and wires `closeChart()` cleanup.

**Tech Stack:** Python csv.DictReader, LightweightCharts v4 (`setMarkers`, `createPriceLine`, `timeToCoordinate`, `priceToCoordinate`, `setVisibleRange`), SVG DOM overlay, vanilla JS.

---

## Files

| File | Action | Key change |
|------|--------|------------|
| `paper_trader.py` | Modify `:86-90` | `get_last_close()` returns `exit_price` |
| `bot.py` | Modify `:71,240-244,286-293,453-458` | Add `exit_price` to `_TRADE_FIELDS` and three log calls |
| `dashboard.py` | Modify `:31-57` | `_load_csv_history` 2-pass rewrite + `_safe_float` helper |
| `dashboard.html` | Modify | `renderHistory` clickable rows, `openTradeChart`, `_applyClosedTradeOverlay`, `_drawTradeStems`, `_drawTradeShading`, `closeChart` cleanup |
| `tests/test_trade_chart.py` | Create | pytest for `_load_csv_history` enrichment |

---

### Task 1: paper_trader.py — expose exit_price in get_last_close()

**Files:**
- Modify: `paper_trader.py:86-90`
- Test: `tests/test_trade_chart.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trade_chart.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

def test_get_last_close_returns_exit_price(tmp_path, monkeypatch):
    """get_last_close must return exit_price from paper_trader history."""
    import paper_trader
    fake_state = {
        "balance": 1000.0,
        "positions": {},
        "history": [
            {
                "symbol": "BTCUSDT",
                "pnl": 4.2,
                "pnl_pct": 2.1,
                "reason": "TP",
                "exit": 42680.0,   # already stored, not yet surfaced
            }
        ],
        "total_pnl": 4.2,
        "partial_closes": [],
    }
    monkeypatch.setattr(paper_trader, "_load", lambda: dict(fake_state))
    result = paper_trader.get_last_close("BTCUSDT")
    assert result is not None
    assert result["exit_price"] == 42680.0, f"expected 42680.0, got {result.get('exit_price')}"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot && pytest tests/test_trade_chart.py::test_get_last_close_returns_exit_price -v
```
Expected: `FAILED` — `KeyError` or `AssertionError` on `exit_price`

- [ ] **Step 3: Add exit_price to get_last_close return**

In `paper_trader.py`, find `get_last_close` (line 76). Change lines 86-90 from:
```python
            return {
                "pnl":     entry.get("pnl", 0),
                "pnl_pct": entry.get("pnl_pct"),
                "reason":  entry.get("reason", ""),
            }
```
to:
```python
            return {
                "pnl":        entry.get("pnl", 0),
                "pnl_pct":    entry.get("pnl_pct"),
                "reason":     entry.get("reason", ""),
                "exit_price": entry.get("exit", 0),
            }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_trade_chart.py::test_get_last_close_returns_exit_price -v
```
Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git add paper_trader.py tests/test_trade_chart.py
git commit -m "feat(chart): expose exit_price in paper_trader.get_last_close()"
```

---

### Task 2: bot.py — add exit_price to CSV schema and log calls

**Files:**
- Modify: `bot.py:71` (`_TRADE_FIELDS`)
- Modify: `bot.py:240-244` (PARTIAL paper log)
- Modify: `bot.py:286-293` (PARTIAL live log)
- Modify: `bot.py:428-458` (CLOSE log)

- [ ] **Step 1: Add exit_price to _TRADE_FIELDS**

In `bot.py`, find `_TRADE_FIELDS` (line 55). Change the close fields section (line 70-72) from:
```python
    # Close fields
    "result", "pnl", "pnl_pct", "exit_reason",
```
to:
```python
    # Close fields
    "result", "pnl", "pnl_pct", "exit_reason", "exit_price",
```

- [ ] **Step 2: Add exit_price to PARTIAL log — paper mode**

Find the paper partial log call at line ~240 (action `_PARTIAL` inside `if PAPER_MODE:`). Change from:
```python
                    _log_trade(f"{pc['strategy']}_PARTIAL", {
                        "trade_id": self.active_positions.get(pc["symbol"], {}).get("trade_id", ""),
                        "symbol": pc["symbol"], "side": pc["side"],
                        "pnl": pc["pnl"], "result": "WIN" if pc["pnl"] >= 0 else "LOSS",
                        "pnl_pct": pc["pnl_pct"], "exit_reason": "PARTIAL_TP",
                    })
```
to:
```python
                    _log_trade(f"{pc['strategy']}_PARTIAL", {
                        "trade_id": self.active_positions.get(pc["symbol"], {}).get("trade_id", ""),
                        "symbol": pc["symbol"], "side": pc["side"],
                        "pnl": pc["pnl"], "result": "WIN" if pc["pnl"] >= 0 else "LOSS",
                        "pnl_pct": pc["pnl_pct"], "exit_reason": "PARTIAL_TP",
                        "exit_price": pc.get("exit"),
                    })
```

- [ ] **Step 3: Add exit_price to PARTIAL log — live mode**

Find the live partial log call at line ~286 (action `_PARTIAL` inside `not PAPER_MODE` block, where `mark_now` is already in scope). Change from:
```python
                            _log_trade(f"{ap['strategy']}_PARTIAL", {
                                "trade_id": ap.get("trade_id", ""),
                                "symbol": sym, "side": side,
                                "pnl": round(partial_pnl, 4),
```
to:
```python
                            _log_trade(f"{ap['strategy']}_PARTIAL", {
                                "trade_id": ap.get("trade_id", ""),
                                "symbol": sym, "side": side,
                                "pnl": round(partial_pnl, 4),
                                "exit_price": round(mark_now, 8),
```
(keep all remaining fields unchanged — only insert the new line after `pnl`)

- [ ] **Step 4: Add exit_price to CLOSE log**

Find the close log call at line ~453. The `_lc` dict is from `tr.get_last_close(sym)` in paper mode. Change the `_log_trade` call from:
```python
                    _log_trade(f"{ap['strategy']}_CLOSE", {
                        "trade_id": ap.get("trade_id", ""),
                        "symbol": sym, "side": ap["side"],
                        "pnl": round(last_pnl, 4), "result": result,
                        "pnl_pct": pnl_pct, "exit_reason": exit_reason,
                    })
```
to:
```python
                    _exit_price = _lc.get("exit_price") if (PAPER_MODE and _lc) else tr.get_mark_price(sym)
                    _log_trade(f"{ap['strategy']}_CLOSE", {
                        "trade_id": ap.get("trade_id", ""),
                        "symbol": sym, "side": ap["side"],
                        "pnl": round(last_pnl, 4), "result": result,
                        "pnl_pct": pnl_pct, "exit_reason": exit_reason,
                        "exit_price": _exit_price,
                    })
```

Note: `_lc` is already declared at line ~429 (`_lc = tr.get_last_close(sym)`) inside the `if PAPER_MODE:` block, but the `_log_trade` call is OUTSIDE that block. Declare `_exit_price` just before the `_log_trade` call using a ternary that references `_lc` (which is `None` in live mode since `get_last_close` is only called in paper mode above).

- [ ] **Step 5: Verify bot imports cleanly**

```bash
python -c "import bot; print('OK')"
```
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add bot.py
git commit -m "feat(chart): add exit_price to trades CSV schema (CLOSE + PARTIAL rows)"
```

---

### Task 3: dashboard.py — _load_csv_history 2-pass rewrite

**Files:**
- Modify: `dashboard.py:31-57`
- Test: `tests/test_trade_chart.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_trade_chart.py`:

```python
import csv, io, types

def _make_csv(rows: list[dict]) -> str:
    """Helper: render a list of dicts to CSV string using bot's _TRADE_FIELDS order."""
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
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def test_load_csv_history_enriches_with_open_row(tmp_path):
    """_load_csv_history must match CLOSE rows to their OPEN row via trade_id."""
    import dashboard
    csv_rows = [
        {
            "timestamp": "2026-03-29T14:22:00+00:00",
            "trade_id": "abc123", "action": "S5_LONG",
            "symbol": "BTCUSDT", "side": "LONG",
            "entry": "42100.0", "sl": "41800.0", "tp": "43200.0",
        },
        {
            "timestamp": "2026-03-29T16:05:00+00:00",
            "trade_id": "abc123", "action": "S5_CLOSE",
            "symbol": "BTCUSDT", "side": "LONG",
            "pnl": "4.2", "result": "WIN", "pnl_pct": "2.1",
            "exit_reason": "TP", "exit_price": "42680.0",
        },
    ]
    csv_file = tmp_path / "trades.csv"
    csv_file.write_text(_make_csv(csv_rows))

    hist = dashboard._load_csv_history(str(csv_file), limit=10)
    assert len(hist) == 1
    t = hist[0]
    assert t["entry"] == 42100.0,      f"entry: {t['entry']}"
    assert t["sl"] == 41800.0,         f"sl: {t['sl']}"
    assert t["tp"] == 43200.0,         f"tp: {t['tp']}"
    assert t["exit_price"] == 42680.0, f"exit_price: {t['exit_price']}"
    assert t["open_at"] == "2026-03-29T14:22:00+00:00"
    assert t["interval"] == "15m",     f"interval: {t['interval']}"
    assert t["events"] == []


def test_load_csv_history_includes_scale_in_and_partial_events(tmp_path):
    """events list must include scale_in and partial rows in order."""
    import dashboard
    csv_rows = [
        {
            "timestamp": "2026-03-29T10:00:00+00:00",
            "trade_id": "xyz", "action": "S2_LONG",
            "symbol": "ETHUSDT", "side": "LONG",
            "entry": "3000.0", "sl": "2900.0", "tp": "3300.0",
        },
        {
            "timestamp": "2026-03-29T11:00:00+00:00",
            "trade_id": "xyz", "action": "S2_SCALE_IN",
            "symbol": "ETHUSDT", "side": "LONG", "entry": "3050.0",
        },
        {
            "timestamp": "2026-03-29T12:00:00+00:00",
            "trade_id": "xyz", "action": "S2_PARTIAL",
            "symbol": "ETHUSDT", "side": "LONG",
            "exit_price": "3200.0", "pnl": "1.5",
        },
        {
            "timestamp": "2026-03-29T13:00:00+00:00",
            "trade_id": "xyz", "action": "S2_CLOSE",
            "symbol": "ETHUSDT", "side": "LONG",
            "pnl": "3.0", "result": "WIN", "exit_price": "3250.0",
        },
    ]
    csv_file = tmp_path / "trades.csv"
    csv_file.write_text(_make_csv(csv_rows))

    hist = dashboard._load_csv_history(str(csv_file), limit=10)
    assert len(hist) == 1
    evts = hist[0]["events"]
    assert len(evts) == 2
    assert evts[0]["type"] == "scale_in"
    assert evts[0]["price"] == 3050.0
    assert evts[1]["type"] == "partial"
    assert evts[1]["price"] == 3200.0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_trade_chart.py::test_load_csv_history_enriches_with_open_row tests/test_trade_chart.py::test_load_csv_history_includes_scale_in_and_partial_events -v
```
Expected: both `FAILED`

- [ ] **Step 3: Rewrite _load_csv_history in dashboard.py**

Replace lines 31–57 of `dashboard.py` with:

```python
_STRATEGY_INTERVAL = {
    "S1": "3m", "S2": "1D", "S3": "15m", "S4": "1D", "S5": "15m",
}


def _safe_float(val):
    try:
        return float(val) if val else None
    except (ValueError, TypeError):
        return None


def _load_csv_history(csv_path: str, limit: int = 50) -> list:
    """Load closed trades from CSV, enriched with open-row data for chart replay.

    2-pass approach:
      Pass 1 — collect OPEN, SCALE_IN, PARTIAL rows keyed by trade_id.
      Pass 2 — for each CLOSE row, look up the matching OPEN row and emit enriched dict.
    """
    if not os.path.exists(csv_path):
        return []

    open_rows  = {}   # trade_id → {entry, sl, tp, open_at, side, strategy, symbol, interval}
    event_rows = {}   # trade_id → [{type, price, ts}, ...]
    rows = []

    try:
        with open(csv_path, newline="") as f:
            all_rows = list(csv.DictReader(f))

        # ── Pass 1: index OPEN / SCALE_IN / PARTIAL rows ─────────────── #
        for r in all_rows:
            action = r.get("action") or ""
            tid    = r.get("trade_id") or ""
            if not tid:
                continue

            if any(action.endswith(sfx) for sfx in ("_LONG", "_SHORT")):
                strategy = action.split("_")[0]
                open_rows[tid] = {
                    "entry":    _safe_float(r.get("entry")),
                    "sl":       _safe_float(r.get("sl")),
                    "tp":       _safe_float(r.get("tp")),
                    "open_at":  r.get("timestamp", ""),
                    "side":     r.get("side", ""),
                    "symbol":   r.get("symbol", ""),
                    "interval": _STRATEGY_INTERVAL.get(strategy, "15m"),
                }
                continue

            if "_SCALE_IN" in action:
                event_rows.setdefault(tid, []).append({
                    "type":  "scale_in",
                    "price": _safe_float(r.get("entry")),
                    "ts":    r.get("timestamp", ""),
                })
                continue

            if "_PARTIAL" in action:
                event_rows.setdefault(tid, []).append({
                    "type":  "partial",
                    "price": _safe_float(r.get("exit_price")),
                    "ts":    r.get("timestamp", ""),
                })

        # ── Pass 2: enrich CLOSE rows ────────────────────────────────── #
        for r in all_rows:
            action = r.get("action") or ""
            if "_CLOSE" not in action:
                continue

            tid      = r.get("trade_id") or ""
            pnl      = _safe_float(r.get("pnl")) or 0.0
            open_row = open_rows.get(tid, {})

            rows.append({
                # existing fields (unchanged contract for dashboard rendering)
                "symbol":      r.get("symbol") or open_row.get("symbol", ""),
                "side":        r.get("side") or open_row.get("side", ""),
                "pnl":         round(pnl, 4),
                "pnl_pct":     r.get("pnl_pct", ""),
                "result":      r.get("result", ""),
                "exit_reason": r.get("exit_reason", ""),
                "strategy":    action.split("_")[0],
                "closed_at":   r.get("timestamp", ""),
                # new fields for chart replay
                "entry":       open_row.get("entry"),
                "sl":          open_row.get("sl"),
                "tp":          open_row.get("tp"),
                "exit_price":  _safe_float(r.get("exit_price")),
                "open_at":     open_row.get("open_at"),
                "interval":    open_row.get("interval"),
                "events":      event_rows.get(tid, []),
            })

    except Exception:
        pass

    return list(reversed(rows))[:limit]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_trade_chart.py -v
```
Expected: all 3 tests `PASSED`

- [ ] **Step 5: Verify dashboard imports cleanly**

```bash
python -c "import dashboard; print('OK')"
```
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add dashboard.py tests/test_trade_chart.py
git commit -m "feat(chart): rewrite _load_csv_history with 2-pass trade_id enrichment"
```

---

### Task 4: dashboard.html — clickable history rows + openTradeChart()

**Files:**
- Modify: `dashboard.html` — `renderHistory` function (~line 1382) and new JS function

- [ ] **Step 1: Add window._histTrades store and onclick to renderHistory**

Find the `renderHistory` function at ~line 1382. Replace the `else` block (lines 1386–1401):

```javascript
  } else {
    $('trade-history').innerHTML = hist.slice(0, 30).map(t => {
      const pnl = t.pnl || 0;
      const pnlClass = pnl >= 0 ? 'pos' : 'neg';
      const res = t.result || '—';
      const sym = (t.symbol || '').replace('USDT','');
      return `
        <div class="hist-row">
          <span class="hist-sym">${sym}</span>
          <span class="side-tag ${(t.side||'').toLowerCase()}" style="font-size:8px;">${t.side||'?'}</span>
          <span class="hist-badge ${res}">${res}</span>
          <span class="hist-pnl ${pnlClass}">${fmtUSD(pnl)}</span>
          <span class="hist-time">${relTime(t.closed_at)}</span>
        </div>`;
    }).join('');
  }
```

with:

```javascript
  } else {
    window._histTrades = hist.slice(0, 30);
    $('trade-history').innerHTML = window._histTrades.map((t, i) => {
      const pnl = t.pnl || 0;
      const pnlClass = pnl >= 0 ? 'pos' : 'neg';
      const res = t.result || '—';
      const sym = (t.symbol || '').replace('USDT','');
      const canChart = !!(t.entry && t.open_at && t.closed_at);
      return `
        <div class="hist-row" ${canChart ? `style="cursor:pointer" onclick="openTradeChart(window._histTrades[${i}])"` : ''}>
          <span class="hist-sym">${sym}</span>
          <span class="side-tag ${(t.side||'').toLowerCase()}" style="font-size:8px;">${t.side||'?'}</span>
          <span class="hist-badge ${res}">${res}</span>
          <span class="hist-pnl ${pnlClass}">${fmtUSD(pnl)}</span>
          <span class="hist-time">${relTime(t.closed_at)}</span>
          ${canChart ? '<span style="font-size:9px;color:var(--muted);margin-left:auto">↗</span>' : ''}
        </div>`;
    }).join('');
  }
```

- [ ] **Step 2: Add openTradeChart() and isoToUnix() helper**

Find the `// ── Chart Modal ────` comment at ~line 1434. Insert the two new functions just before it:

```javascript
// ── Trade History Chart ────────────────────────────────
function isoToUnix(iso) {
  return iso ? Math.floor(new Date(iso).getTime() / 1000) : null;
}

function openTradeChart(trade) {
  if (!trade || !trade.entry || !trade.open_at) return;
  const tabMap = { S1: 's1', S2: 's2', S3: 's3', S4: 's4', S5: 's5' };
  chartActiveTab  = tabMap[trade.strategy] || 's1';
  currentInterval = trade.interval || '15m';
  window._tradeOverlay = {
    closed:     true,
    entry:      trade.entry,
    exit_price: trade.exit_price,
    sl:         trade.sl,
    tp:         trade.tp,
    side:       trade.side,
    open_ts:    isoToUnix(trade.open_at),
    close_ts:   isoToUnix(trade.closed_at),
    events:     trade.events || [],
  };
  openChart(trade.symbol);
}

```

- [ ] **Step 3: Manual smoke test — verify rows render with ↗ icon**

Start the dashboard: `python dashboard.py`
Open http://localhost:8080 in a browser.
In the Trade History panel, if any closed trades exist, rows with chart data show a `↗` icon and change cursor on hover. Rows without `open_at`/`entry` (old CSV rows pre-this-feature) have no icon and no cursor.

- [ ] **Step 4: Commit**

```bash
git add dashboard.html
git commit -m "feat(chart): add clickable trade history rows and openTradeChart()"
```

---

### Task 5: dashboard.html — closed trade chart overlay

**Files:**
- Modify: `dashboard.html` — `loadChart()`, `closeChart()`, and new helper functions

- [ ] **Step 1: Add _applyClosedTradeOverlay() after the existing _tradeOverlay block**

Find the existing `_tradeOverlay` block in `loadChart` at ~line 2322:

```javascript
    // ── Active trade overlay lines (entry / SL / TP) ──
    if (window._tradeOverlay) {
      const ov   = window._tradeOverlay;
      ...
    }
```

Replace that entire block with:

```javascript
    // ── Trade overlay lines ──
    if (window._tradeOverlay) {
      const ov = window._tradeOverlay;
      if (ov.closed) {
        // Closed trade: full chart replay (price lines + markers + stems + shading)
        // Called after fitContent/setVisibleRange so coordinate system is ready
        setTimeout(() => _applyClosedTradeOverlay(ov), 50);
      } else {
        // Live trade: entry / SL / TP lines only
        const isLong = (ov.side || '').toUpperCase() === 'LONG';
        if (ov.entry) candlePriceLines.push(candleSeries.createPriceLine({
          price: ov.entry, color: '#e2e8f0', lineWidth: 1, lineStyle: 0,
          axisLabelVisible: true, title: 'Entry',
        }));
        if (ov.sl) candlePriceLines.push(candleSeries.createPriceLine({
          price: ov.sl, color: '#f43f5e', lineWidth: 1, lineStyle: 0,
          axisLabelVisible: true, title: 'SL',
        }));
        if (ov.tp) candlePriceLines.push(candleSeries.createPriceLine({
          price: ov.tp, color: '#10b981', lineWidth: 1, lineStyle: 0,
          axisLabelVisible: true, title: 'TP',
        }));
      }
    }
```

- [ ] **Step 2: Add _applyClosedTradeOverlay(), _drawTradeStems(), _drawTradeShading()**

Find the `attachCardClicks` function at ~line 2359. Insert the three new functions just before it:

```javascript
// ── Closed trade chart overlay ─────────────────────────
function _applyClosedTradeOverlay(ov) {
  if (!ov.open_ts || !ov.close_ts) return;
  const isLong = (ov.side || '').toUpperCase() === 'LONG';

  // 1. Navigate: show full trade span with 20% padding on each side
  const pad = Math.max(Math.round((ov.close_ts - ov.open_ts) * 0.2), 3600);
  try {
    candleChart.timeScale().setVisibleRange({
      from: ov.open_ts - pad,
      to:   ov.close_ts + pad,
    });
  } catch (_) {}

  // 2. Price lines
  if (ov.entry) candlePriceLines.push(candleSeries.createPriceLine({
    price: ov.entry, color: '#4a90d9', lineWidth: 1, lineStyle: 2,
    axisLabelVisible: true, title: 'Entry',
  }));
  if (ov.sl) candlePriceLines.push(candleSeries.createPriceLine({
    price: ov.sl, color: '#ff4d6a', lineWidth: 1, lineStyle: 2,
    axisLabelVisible: true, title: 'SL',
  }));
  if (ov.tp) candlePriceLines.push(candleSeries.createPriceLine({
    price: ov.tp, color: '#00d68f', lineWidth: 1, lineStyle: 2,
    axisLabelVisible: true, title: 'TP',
  }));
  if (ov.exit_price) candlePriceLines.push(candleSeries.createPriceLine({
    price: ov.exit_price, color: '#f0a500', lineWidth: 1, lineStyle: 2,
    axisLabelVisible: true, title: 'Exit',
  }));

  // 3. Markers
  const markers = [];
  if (ov.entry) markers.push({
    time:     ov.open_ts,
    position: isLong ? 'belowBar' : 'aboveBar',
    color:    '#00d68f',
    shape:    isLong ? 'arrowUp' : 'arrowDown',
    text:     'Entry' + (ov.entry ? ' $' + fmtPrice(ov.entry) : ''),
  });
  for (const ev of (ov.events || []).filter(e => e.type === 'scale_in')) {
    const ts = isoToUnix(ev.ts);
    if (ts) markers.push({
      time:     ts,
      position: isLong ? 'belowBar' : 'aboveBar',
      color:    '#4a90d9',
      shape:    isLong ? 'arrowUp' : 'arrowDown',
      text:     'Scale-in' + (ev.price ? ' $' + fmtPrice(ev.price) : ''),
      size:     0.8,
    });
  }
  for (const ev of (ov.events || []).filter(e => e.type === 'partial')) {
    const ts = isoToUnix(ev.ts);
    if (ts) markers.push({
      time:     ts,
      position: isLong ? 'aboveBar' : 'belowBar',
      color:    '#f0a500',
      shape:    'circle',
      text:     'Partial' + (ev.price ? ' $' + fmtPrice(ev.price) : ''),
    });
  }
  if (ov.exit_price) markers.push({
    time:     ov.close_ts,
    position: isLong ? 'aboveBar' : 'belowBar',
    color:    '#f0a500',
    shape:    'square',
    text:     'Exit' + (ov.exit_price ? ' $' + fmtPrice(ov.exit_price) : ''),
  });
  markers.sort((a, b) => a.time - b.time);
  candleSeries.setMarkers(markers);

  // 4. Stems + shading — after chart has rendered the new range
  requestAnimationFrame(() => {
    _drawTradeShading(ov);
    _drawTradeStems(ov);
  });
}

function _drawTradeStems(ov) {
  const chartEl = $('chart-candles');
  chartEl.querySelectorAll('.trade-stems-svg').forEach(el => el.remove());
  if (!candleChart || !candleSeries) return;

  const isLong = (ov.side || '').toUpperCase() === 'LONG';
  const ts  = candleChart.timeScale();
  const ps  = candleSeries;
  const w   = chartEl.offsetWidth;
  const h   = chartEl.offsetHeight;

  // Points that get a stem: entry, scale-ins, partials, exit
  const pts = [];
  if (ov.open_ts && ov.entry)
    pts.push({ time: ov.open_ts,  price: ov.entry,      color: '#00d68f', dir: isLong ? 'below' : 'above' });
  for (const ev of (ov.events || []).filter(e => e.type === 'scale_in')) {
    const t = isoToUnix(ev.ts);
    if (t && ev.price) pts.push({ time: t, price: ev.price, color: '#4a90d9', dir: isLong ? 'below' : 'above' });
  }
  for (const ev of (ov.events || []).filter(e => e.type === 'partial')) {
    const t = isoToUnix(ev.ts);
    if (t && ev.price) pts.push({ time: t, price: ev.price, color: '#f0a500', dir: isLong ? 'above' : 'below' });
  }
  if (ov.close_ts && ov.exit_price)
    pts.push({ time: ov.close_ts, price: ov.exit_price, color: '#f0a500', dir: isLong ? 'above' : 'below' });

  const svgNS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(svgNS, 'svg');
  svg.classList.add('trade-stems-svg');
  svg.style.cssText = 'position:absolute;top:0;left:0;pointer-events:none;z-index:2;overflow:visible;';
  svg.setAttribute('width', w);
  svg.setAttribute('height', h);

  for (const pt of pts) {
    const x = ts.timeToCoordinate(pt.time);
    const y = ps.priceToCoordinate(pt.price);
    if (x == null || y == null) continue;

    const stemLen = 16;
    const tickLen = 7;
    const y2 = pt.dir === 'below' ? y + stemLen : y - stemLen;

    // Vertical stem
    const line = document.createElementNS(svgNS, 'line');
    line.setAttribute('x1', x);  line.setAttribute('y1', y);
    line.setAttribute('x2', x);  line.setAttribute('y2', y2);
    line.setAttribute('stroke', pt.color);
    line.setAttribute('stroke-width', '1.5');
    svg.appendChild(line);

    // Horizontal price tick (dashed, centred on x)
    const tick = document.createElementNS(svgNS, 'line');
    tick.setAttribute('x1', x - tickLen); tick.setAttribute('y1', y);
    tick.setAttribute('x2', x + tickLen); tick.setAttribute('y2', y);
    tick.setAttribute('stroke', pt.color);
    tick.setAttribute('stroke-width', '1.5');
    tick.setAttribute('stroke-dasharray', '2,2');
    tick.setAttribute('opacity', '0.85');
    svg.appendChild(tick);
  }

  chartEl.style.position = 'relative';
  chartEl.appendChild(svg);
}

function _drawTradeShading(ov) {
  const chartEl = $('chart-candles');
  chartEl.querySelectorAll('.trade-shade').forEach(el => el.remove());
  if (!candleChart || !ov.open_ts || !ov.close_ts) return;

  const ts = candleChart.timeScale();
  const x1 = ts.timeToCoordinate(ov.open_ts);
  const x2 = ts.timeToCoordinate(ov.close_ts);
  if (x1 == null || x2 == null) return;

  const shade = document.createElement('div');
  shade.className = 'trade-shade';
  shade.style.cssText = `
    position:absolute; top:0; bottom:0; pointer-events:none; z-index:1;
    left:${Math.min(x1, x2)}px; width:${Math.abs(x2 - x1)}px;
    background:rgba(74,144,217,0.07);
    border-left:1px solid rgba(0,214,143,0.35);
    border-right:1px solid rgba(240,165,0,0.35);
  `;
  chartEl.style.position = 'relative';
  chartEl.appendChild(shade);
}

```

- [ ] **Step 3: Update closeChart() to clean up overlay elements**

Find `closeChart` at ~line 1889:
```javascript
function closeChart(e) {
  if (e && e.target !== $('chartOverlay')) return;
  $('chartOverlay').classList.remove('open');
  clearInterval(chartRefreshTimer);
  currentSymbol = null;
  window._tradeOverlay = null;
}
```

Replace with:
```javascript
function closeChart(e) {
  if (e && e.target !== $('chartOverlay')) return;
  $('chartOverlay').classList.remove('open');
  clearInterval(chartRefreshTimer);
  currentSymbol = null;
  window._tradeOverlay = null;
  // Clean up closed-trade overlay elements
  if (candleSeries) try { candleSeries.setMarkers([]); } catch(_) {}
  document.querySelectorAll('.trade-stems-svg, .trade-shade').forEach(el => el.remove());
}
```

- [ ] **Step 4: Add resize handler to redraw stems+shading on chart resize**

Find the `// Close on Escape key` listener at ~line 2388. Insert just before it:

```javascript
// Redraw trade overlay stems/shading when chart container resizes
(function() {
  const chartEl = $('chart-candles');
  if (!chartEl || !window.ResizeObserver) return;
  new ResizeObserver(() => {
    const ov = window._tradeOverlay;
    if (ov?.closed) {
      _drawTradeShading(ov);
      _drawTradeStems(ov);
    }
  }).observe(chartEl);
})();
```

- [ ] **Step 5: End-to-end manual test**

With trades in `trades_paper.csv` (run `python bot.py --paper` first if needed):

1. Start dashboard: `python dashboard.py`
2. Open http://localhost:8080
3. Find a closed trade row with `↗` icon — click it
4. Verify: chart modal opens for correct symbol
5. Verify: chart scrolls to show the trade timespan
6. Verify: green `▲` (LONG) or red `▼` (SHORT) entry arrow at entry timestamp
7. Verify: orange `✕` exit square at close timestamp
8. Verify: blue/green/red/orange dashed price lines for Entry/TP/SL/Exit
9. Verify: short horizontal dashed tick at each arrow's price level
10. Verify: blue tinted shading between entry and exit timestamps
11. Click a different interval button — verify shading and stems redraw
12. Close chart (Escape or ✕) — verify no leftover markers on next chart open

- [ ] **Step 6: Commit**

```bash
git add dashboard.html
git commit -m "feat(chart): closed trade chart overlay — markers, stems, shading, navigation"
```

---

### Task 6: Final push and release prep

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/test_trade_chart.py -v
```
Expected: 3 PASSED

- [ ] **Step 2: Verify all four changed files import cleanly**

```bash
python -c "import paper_trader; print('paper_trader OK')"
python -c "import bot; print('bot OK')"
python -c "import dashboard; print('dashboard OK')"
```
Expected: three `OK` lines

- [ ] **Step 3: Push**

```bash
git push
```

---

## Self-Review

**Spec coverage:**
- [x] `paper_trader.get_last_close()` returns `exit_price` → Task 1
- [x] `exit_price` in `_TRADE_FIELDS` + CLOSE log → Task 2
- [x] `exit_price` in PARTIAL log (paper + live) → Task 2
- [x] `_load_csv_history` 2-pass enrichment → Task 3
- [x] `interval` derived from strategy → Task 3 (`_STRATEGY_INTERVAL`)
- [x] `events[]` with scale_in and partial rows → Task 3
- [x] Clickable hist rows (only when chart data available) → Task 4
- [x] `openTradeChart()` + `isoToUnix()` → Task 4
- [x] Price lines: Entry (blue), SL (red), TP (green), Exit (orange) → Task 5
- [x] Markers: entry arrow, scale-in arrow, partial circle, exit square → Task 5
- [x] Stem + dashed horizontal price tick at each marker → Task 5 (`_drawTradeStems`)
- [x] Shaded region between open_ts and close_ts → Task 5 (`_drawTradeShading`)
- [x] Navigation: `setVisibleRange` with 20% padding → Task 5
- [x] `closeChart()` cleanup (markers, stems, shade) → Task 5
- [x] ResizeObserver redraws on resize → Task 5
- [x] Live trade overlay unchanged → Task 5 (kept in `else` branch)
- [x] Graceful degradation: rows without `open_at`/`entry` not clickable → Task 4

**Placeholder scan:** No TBDs or incomplete sections. All code blocks are complete.

**Type consistency:**
- `isoToUnix` defined in Task 4, used in Task 5 ✅
- `_tradeOverlay.closed` flag checked in Task 5 loadChart block ✅
- `window._histTrades[i]` referenced in Task 4 renderHistory, consumed by `openTradeChart` in Task 4 ✅
- `fmtPrice` used in Task 5 marker text — already defined in dashboard.html ✅
- `candlePriceLines` array used for cleanup in Task 5 — already tracked in dashboard.html ✅
