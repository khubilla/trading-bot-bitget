# Trade History Chart Design

**Date:** 2026-03-29

---

## Problem

The trade history panel shows closed trades (symbol, side, PnL, result) but rows are inert — there is no way to review where a trade entered, how it progressed, or where it exited on a chart.

## Solution

Make every trade history row clickable. Clicking opens the existing chart modal for that symbol and interval, with the full trade lifecycle overlaid: entry/scale-in/partial-TP/exit markers, SL/TP price lines, a shaded region spanning the trade duration, and stems with horizontal price ticks connecting arrows to the exact candle.

---

## Architecture

```
trades.csv
  ├── OPEN row       entry, sl, tp, open_at, side, strategy
  ├── SCALE_IN row   entry price (fill), ts
  ├── PARTIAL row    exit_price (NEW), ts
  └── CLOSE row      exit_price (NEW), closed_at

        ↓  _load_csv_history (2-pass)

/api/state → trade_history[N].{
  entry, sl, tp, exit_price, open_at, closed_at, interval, events[]
}

        ↓  openTradeChart(trade)

window._tradeOverlay { closed:true, open_ts, close_ts, ... }

        ↓  loadChart()

candleSeries.setMarkers([entry, scale_ins, partials, exit])
createPriceLine × 4 (entry, SL, TP, exit)
SVG overlay: stems + price ticks on candle bars
DOM overlay: shaded region between open_ts and close_ts
chart.timeScale().setVisibleRange(open_ts ± padding, close_ts ± padding)
```

---

## Section 1 — Data Changes

### `paper_trader.py`

`get_last_close()` currently returns `{pnl, pnl_pct, reason}`. The paper_trader history already stores `"exit"` (fill price) at line 437. Add it to the return dict:

```python
return {
    "pnl":        entry.get("pnl", 0),
    "pnl_pct":    entry.get("pnl_pct"),
    "reason":     entry.get("reason", ""),
    "exit_price": entry.get("exit", 0),   # NEW
}
```

### `bot.py` — CLOSE row

At `_log_trade(f"{ap['strategy']}_CLOSE", {...})` (line 453), add `exit_price`:

```python
_log_trade(f"{ap['strategy']}_CLOSE", {
    "trade_id":   ap.get("trade_id", ""),
    "symbol":     sym,
    "side":       ap["side"],
    "pnl":        round(last_pnl, 4),
    "result":     result,
    "pnl_pct":    pnl_pct,
    "exit_reason": exit_reason,
    "exit_price": _lc.get("exit_price", 0) if PAPER_MODE else tr.get_mark_price(sym),  # NEW
})
```

Paper mode: exact fill price from `paper_trader.get_last_close()`.
Live mode: mark price captured at close-detection moment (within one poll interval).

### `bot.py` — PARTIAL row (paper mode, line 240)

`drain_partial_closes()` already returns `pc["exit"]`. Add it:

```python
_log_trade(f"{pc['strategy']}_PARTIAL", {
    "trade_id":   ...,
    "symbol":     pc["symbol"],
    "side":       pc["side"],
    "pnl":        pc["pnl"],
    "result":     "WIN" if pc["pnl"] >= 0 else "LOSS",
    "pnl_pct":    pc["pnl_pct"],
    "exit_reason": "PARTIAL_TP",
    "exit_price": pc["exit"],   # NEW
})
```

### `bot.py` — PARTIAL row (live mode, line 286)

`mark_now` is already in scope at that point:

```python
_log_trade(f"{ap['strategy']}_PARTIAL", {
    ...
    "exit_price": mark_now,   # NEW — mark_now already computed above
})
```

### SCALE_IN rows

Already log `"entry"` (fill price) and `"timestamp"` via `_log_trade`. No change needed.

### Impact on optimize.py / optimize_ig.py

Both use `csv.DictReader` and read only named columns — adding `exit_price` to the CSV is additive. Zero breakage.

---

## Section 2 — Backend (`dashboard.py`)

### `_load_csv_history` — 2-pass rewrite

**Pass 1**: scan all rows, collect by `trade_id`:
- `open_rows[trade_id]` → `{entry, sl, tp, open_at, side, strategy, symbol}`
- `event_rows[trade_id]` → ordered list of `{type, price, ts}` for SCALE_IN and PARTIAL rows

**Pass 2**: for each CLOSE row, look up `open_rows[trade_id]` and emit enriched dict:

```python
{
    # existing fields (unchanged)
    "symbol":      str,
    "side":        str,
    "pnl":         float,
    "pnl_pct":     str,
    "result":      str,
    "exit_reason": str,
    "strategy":    str,
    "closed_at":   str,   # ISO timestamp of CLOSE row

    # new fields
    "entry":       float,
    "sl":          float,
    "tp":          float,
    "exit_price":  float,
    "open_at":     str,   # ISO timestamp of OPEN row
    "interval":    str,   # derived from strategy: S1→"3m", S2/S4→"1D", S3/S5→"15m"
    "events": [
        {"type": "scale_in", "price": float, "ts": str},
        {"type": "partial",  "price": float, "ts": str},
    ],
}
```

### Interval mapping

```python
_STRATEGY_INTERVAL = {
    "S1": "3m",
    "S2": "1D",
    "S3": "15m",
    "S4": "1D",
    "S5": "15m",
}
```

### Graceful degradation

If `trade_id` is missing or the OPEN row is not found (e.g. CSV truncated), emit the existing fields only with `entry/sl/tp/exit_price = None`. The frontend skips chart markers when these are null.

---

## Section 3 — Frontend (`dashboard.html`)

### Clickable hist rows

Each `.hist-row` gains `cursor:pointer` and stores its trade data in a `data-trade` attribute (JSON-encoded). Click calls `openTradeChart(t)`:

```html
<div class="hist-row" style="cursor:pointer"
     onclick='openTradeChart(${JSON.stringify(t)})'>
  ...existing content...
</div>
```

### `openTradeChart(trade)`

```javascript
function openTradeChart(trade) {
    const tabMap      = {S1:'s1', S2:'s2', S3:'s3', S4:'s4', S5:'s5'};
    chartActiveTab    = tabMap[trade.strategy] || 's1';
    currentInterval   = trade.interval || '15m';

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

function isoToUnix(iso) {
    return iso ? Math.floor(new Date(iso).getTime() / 1000) : null;
}
```

### Chart rendering — closed trade overlay

In `loadChart()`, after candle data is applied and before returning, check `window._tradeOverlay?.closed`:

#### 1. Price lines
```javascript
candlePriceLines.push(candleSeries.createPriceLine({
    price: ov.entry, color: '#4a90d9', lineWidth: 1, lineStyle: 2, title: 'Entry'
}));
candlePriceLines.push(candleSeries.createPriceLine({
    price: ov.sl, color: '#ff4d6a', lineWidth: 1, lineStyle: 2, title: 'SL'
}));
candlePriceLines.push(candleSeries.createPriceLine({
    price: ov.tp, color: '#00d68f', lineWidth: 1, lineStyle: 2, title: 'TP'
}));
candlePriceLines.push(candleSeries.createPriceLine({
    price: ov.exit_price, color: '#f0a500', lineWidth: 1, lineStyle: 2, title: 'Exit'
}));
```

#### 2. Candle markers
```javascript
const isLong = ov.side === 'LONG';
const markers = [];

// Entry
markers.push({
    time: ov.open_ts,
    position: isLong ? 'belowBar' : 'aboveBar',
    color: '#00d68f',
    shape: isLong ? 'arrowUp' : 'arrowDown',
    text: `Entry $${fmtPrice(ov.entry)}`,
});

// Scale-ins
for (const ev of ov.events.filter(e => e.type === 'scale_in')) {
    markers.push({
        time: isoToUnix(ev.ts),
        position: isLong ? 'belowBar' : 'aboveBar',
        color: '#4a90d9',
        shape: isLong ? 'arrowUp' : 'arrowDown',
        text: `Scale-in $${fmtPrice(ev.price)}`,
        size: 0.8,
    });
}

// Partials
for (const ev of ov.events.filter(e => e.type === 'partial')) {
    markers.push({
        time: isoToUnix(ev.ts),
        position: isLong ? 'aboveBar' : 'belowBar',
        color: '#f0a500',
        shape: 'circle',
        text: `Partial $${fmtPrice(ev.price)}`,
    });
}

// Exit
markers.push({
    time: ov.close_ts,
    position: isLong ? 'aboveBar' : 'belowBar',
    color: '#f0a500',
    shape: 'square',
    text: `Exit $${fmtPrice(ov.exit_price)}`,
});

markers.sort((a, b) => a.time - b.time);
candleSeries.setMarkers(markers);
```

#### 3. Stems + price ticks (SVG overlay)

After a `requestAnimationFrame` (to let the chart render first):

```javascript
function drawTradeStems(ov) {
    const chartEl = $('chart-candles');
    const existingSvg = chartEl.querySelector('.trade-stems-svg');
    if (existingSvg) existingSvg.remove();

    const rect = chartEl.getBoundingClientRect();
    const w = rect.width, h = rect.height;
    const ts = candleChart.timeScale();
    const ps = candleSeries;
    const isLong = ov.side === 'LONG';

    const stemPoints = [
        { ts: ov.open_ts,   price: ov.entry,      color: '#00d68f', dir: isLong ? 'below' : 'above' },
        ...ov.events.filter(e => e.type === 'scale_in').map(e => ({
            ts: isoToUnix(e.ts), price: e.price, color: '#4a90d9', dir: isLong ? 'below' : 'above'
        })),
        { ts: ov.close_ts,  price: ov.exit_price, color: '#f0a500', dir: isLong ? 'above' : 'below' },
    ];

    const svgNS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(svgNS, 'svg');
    svg.classList.add('trade-stems-svg');
    svg.style.cssText = 'position:absolute;top:0;left:0;pointer-events:none;z-index:2;';
    svg.setAttribute('width', w);
    svg.setAttribute('height', h);

    for (const pt of stemPoints) {
        const x = ts.timeToCoordinate(pt.ts);
        const y = ps.priceToCoordinate(pt.price);
        if (x == null || y == null) continue;

        const stemLen = 14;
        const tickLen = 8;
        const y2 = pt.dir === 'below' ? y + stemLen : y - stemLen;

        // Vertical stem
        const line = document.createElementNS(svgNS, 'line');
        line.setAttribute('x1', x); line.setAttribute('y1', y);
        line.setAttribute('x2', x); line.setAttribute('y2', y2);
        line.setAttribute('stroke', pt.color);
        line.setAttribute('stroke-width', '1.5');
        svg.appendChild(line);

        // Horizontal price tick
        const tick = document.createElementNS(svgNS, 'line');
        tick.setAttribute('x1', x - tickLen); tick.setAttribute('y1', y);
        tick.setAttribute('x2', x + tickLen); tick.setAttribute('y2', y);
        tick.setAttribute('stroke', pt.color);
        tick.setAttribute('stroke-width', '1.5');
        tick.setAttribute('stroke-dasharray', '2,2');
        tick.setAttribute('opacity', '0.8');
        svg.appendChild(tick);
    }

    chartEl.style.position = 'relative';
    chartEl.appendChild(svg);
}
```

#### 4. Shaded region (DOM overlay)

```javascript
function drawTradeShading(ov) {
    const chartEl = $('chart-candles');
    const existing = chartEl.querySelector('.trade-shade');
    if (existing) existing.remove();

    const ts = candleChart.timeScale();
    const x1 = ts.timeToCoordinate(ov.open_ts);
    const x2 = ts.timeToCoordinate(ov.close_ts);
    if (x1 == null || x2 == null) return;

    const shade = document.createElement('div');
    shade.className = 'trade-shade';
    shade.style.cssText = `
        position:absolute; top:0; bottom:0; pointer-events:none; z-index:1;
        left:${Math.min(x1,x2)}px; width:${Math.abs(x2-x1)}px;
        background:rgba(74,144,217,0.07);
        border-left:1px solid rgba(0,214,143,0.3);
        border-right:1px solid rgba(240,165,0,0.3);
    `;
    chartEl.appendChild(shade);
}
```

Both `drawTradeStems` and `drawTradeShading` are called after `requestAnimationFrame` in `loadChart`, and again on window resize.

#### 5. Navigation

```javascript
const pad = Math.round((ov.close_ts - ov.open_ts) * 0.2) || 3600;
candleChart.timeScale().setVisibleRange({
    from: ov.open_ts - pad,
    to:   ov.close_ts + pad,
});
```

### Cleanup on close

In `closeChart()`, add:
```javascript
document.querySelectorAll('.trade-stems-svg, .trade-shade').forEach(el => el.remove());
candleSeries.setMarkers([]);
```

---

## What Is NOT in Scope

- IG trade history chart (ig_trades.csv has no `trade_id` linking opens to closes — separate feature)
- Historical candle availability: if the trade is older than the exchange's candle history, the chart shows present candles and markers render at the edges
- Mobile: the chart modal already goes full-screen on mobile; stems/shading use the same coordinate system

---

## Files Changed

| File | Change |
|------|--------|
| `paper_trader.py` | `get_last_close()` returns `exit_price` |
| `bot.py` | Add `exit_price` to CLOSE and PARTIAL log rows |
| `dashboard.py` | `_load_csv_history` 2-pass rewrite |
| `dashboard.html` | Clickable hist rows, `openTradeChart()`, chart overlay rendering |
