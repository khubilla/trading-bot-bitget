# Entry Chart Modal — Trade History Integration

**Date:** 2026-03-30
**Status:** Approved for implementation

---

## Overview

Add a 📊 icon button to each trade history row in the dashboard. Clicking it opens a new entry-focused chart modal showing the candlesticks around trade entry, with strategy-specific candle highlights and key price lines. This is additive — the existing full chart (LightweightCharts + indicators, opened via ↗ icon / double-click) is unchanged.

---

## Interaction Model

- Each `hist-row` gains a **📊 icon button** (shown only when `entry` and `open_at` are available)
- Clicking 📊 → opens a new **entry chart modal**
- Existing row click behavior (single = chat, double = full chart via ↗) is unchanged
- The 💬 and ↗ icons remain as-is

---

## New API Endpoint

```
GET /api/entry-chart
  ?symbol=UNIUSDT
  &open_at=2026-03-30T08:05:35%2B00:00   (ISO, URL-encoded)
  &strategy=S3
  &snap_s5_ob_low=...                    (optional, for S5)
  &snap_s5_ob_high=...                   (optional, for S5)
  &box_low=...                           (optional, for S1/S2)
  &box_high=...                          (optional, for S1/S2)
```

**Response:**
```json
{
  "candles": [...],          // 25 candles centred around entry (15–20 before, 5–10 after)
  "entry_ts": 1774857600000, // timestamp of entry candle
  "highlights": {
    "last_red_ts":   ...,    // S3 only — last red candle before uptick
    "last_green_ts": ...,    // S3 only — uptick (trigger) candle
    "spike_ts":      ...,    // S2/S4 only — biggest body candle in last 30 days
    "ob_low":        ...,    // S5 only — order block lower bound
    "ob_high":       ...,    // S5 only — order block upper bound
    "box_low":       ...,    // S1/S2 — consolidation box lower bound
    "box_high":      ...     // S1/S2 — consolidation box upper bound
  }
}
```

**Per-strategy server logic:**

| Strategy | Candle fetch | Highlights computed how |
|----------|-------------|------------------------|
| S1 | 3m candles, limit=300, endTime=open_at+buffer | `box_low`/`box_high` passed from query params (from trade record) |
| S2 | 1D candles, limit=60, endTime=open_at+buffer | `box_low`/`box_high` from query params; `spike_ts` re-identified as largest body candle in last 30 1D candles |
| S3 | 15m candles, limit=300, endTime=open_at+buffer | `last_red_ts`/`last_green_ts` computed via Stoch(5,3) lookback (same logic as `evaluate_s3`) |
| S4 | 1D candles, limit=60, endTime=open_at+buffer | `spike_ts` re-identified as largest body candle in last 30 1D candles |
| S5 | 15m candles, limit=300, endTime=open_at+buffer | `ob_low`/`ob_high` passed from query params (already in CSV as `snap_s5_ob_low`/`snap_s5_ob_high`) |

`endTime` = `open_at` timestamp + 10 candle-widths (so 5–10 post-entry candles are included).
Response trims to **25 candles** (last 20 pre-entry + first 5 post-entry window).

---

## Frontend — Entry Chart Modal

### New modal element

A second chart modal (`#entry-chart-modal`) separate from the existing `#chart-modal`. Structurally similar (overlay + centered panel), but simpler — no LightweightCharts, no indicator subcharts. Uses **Canvas 2D rendering** (same approach as `open_trades_chart.html`).

Modal contains:
- Header: `SYMBOL · STRATEGY · TIMEFRAME · opened_at`
- Canvas: 100% width, 320px height
- Legend row: colour-coded labels for each highlight type
- Close button

### Canvas rendering

Reuses the drawing logic from `open_trades_chart.html`:
- 25 candlesticks with wick + body
- Volume bars below (40px)
- Time labels on x-axis (every 4 candles)
- Price labels on right y-axis

**Price lines (all strategies):**

| Line | Colour | Style |
|------|--------|-------|
| Entry | `#58a6ff` blue | dashed |
| Trigger | `#e3b341` gold | dashed |
| SL current | `#f85149` red | dashed |
| SL original (snap_sl) | `#7d3535` dark red | dashed |
| TP | `#3fb950` green | dashed |

**Candle highlights per strategy:**

| Strategy | Highlight | Colour |
|----------|-----------|--------|
| S3 | Last red candle | Rose `#ff6b6b` + background tint |
| S3 | Uptick (trigger) candle | Gold `#f0c060` + background tint |
| All | Entry candle | White `#f0f6fc` + blue background tint |
| S2/S4 | Spike/pump candle | Gold `#e3b341` (S2) / Purple `#a371f7` (S4) + tint |

**Zone shading (S1/S2/S5):**

| Strategy | Zone | Colour |
|----------|------|--------|
| S1/S2 | box_low → box_high | `rgba(88,166,255,0.08)` with dashed borders |
| S5 | ob_low → ob_high | `rgba(57,197,207,0.12)` with solid borders |

### Labels below candles

```
LAST RED   UPTICK   ▲ ENTRY
```
Positioned below the candle, colour-matched to highlight.

---

## Frontend — Row Icon

In `dashboard.html`, the hist-row template gains a 📊 button:

```html
<!-- added alongside existing 💬 and ↗ icons -->
<span class="hist-icon" onclick="event.stopPropagation(); openEntryChart(window._histTrades[${i}])"
      title="Entry chart">📊</span>
```

`openEntryChart(trade)` function:
1. Calls `GET /api/entry-chart` with trade fields as query params
2. On success: populates and shows `#entry-chart-modal`, renders canvas
3. On error: shows a brief inline error in the modal header

---

## Files Changed

| File | Change |
|------|--------|
| `dashboard.py` | Add `GET /api/entry-chart` endpoint; add `box_low` and `box_high` to the `open_rows` record (currently omitted — needed by S1/S2 chart) |
| `dashboard.html` | Add `#entry-chart-modal` HTML + CSS; add `openEntryChart()` JS function + canvas renderer; add 📊 icon to `hist-row` template |

No changes to: `bot.py`, `strategy.py`, `state.json` fields, CSV columns, `trader.py`, or either config file.

---

## Out of Scope

- S1 not yet active in live trading — implement 📊 support but it will only appear when S1 trades exist in history
- No indicator subcharts (RSI, MACD) in the entry modal — those remain in the full chart modal only
- No interactive zoom/pan — static 25-candle snapshot only
