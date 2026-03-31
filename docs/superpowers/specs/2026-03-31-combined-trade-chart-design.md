# Combined Trade Chart Design

**Date:** 2026-03-31
**Feature:** Combined multi-event trade lifecycle chart using snapshot data
**Status:** Approved

---

## Goal

Show all trade lifecycle events (open, scale-in, partial close, full close) on a single candlestick chart using snapshot data. Works for both active trades (open snapshot only) and historical trades (all available snapshots).

---

## Architecture

New endpoint `/api/trade-chart` in `dashboard.py`. It loads all available snapshots for a `trade_id`, merges candles from all snapshots into one unified candle array, and returns event metadata. Frontend renders with a new `_drawTradeChart()` canvas function.

---

## Backend

### Endpoint

```
GET /api/trade-chart?trade_id={trade_id}
```

**Response schema:**

```json
{
  "symbol": "RIVERUSDT",
  "strategy": "S3",
  "interval": "15m",
  "side": "LONG",
  "candles": [
    {"t": 1743300000000, "o": 15.71, "h": 15.82, "l": 15.69, "c": 15.78, "v": 9200}
  ],
  "events": [
    {"type": "open",     "candle_idx": 8,  "price": 15.756, "sl": 14.990, "tp": 17.332},
    {"type": "scale_in", "candle_idx": 18, "price": 15.801},
    {"type": "partial",  "candle_idx": 26, "price": 17.332},
    {"type": "close",    "candle_idx": 35, "price": 16.975}
  ]
}
```

**Error responses:**
- `trade_id` missing or empty → `400 {"error": "trade_id required"}`
- No snapshots found → `404 {"error": "no snapshots found"}`

### Candle merge algorithm

1. Load all available snapshots for `trade_id` via `snapshot.list_snapshots(trade_id)`
2. For each snapshot event, call `snapshot.load_snapshot(trade_id, event)`
3. Union all `candles` arrays across snapshots, keyed by timestamp `t`
4. Where duplicate timestamps exist (overlap between snapshots), keep the candle from the **later** snapshot (more recent data is more accurate)
5. Sort unified candle list by `t` ascending
6. For each event, find `candle_idx` by matching `snap["captured_at"]` to the nearest candle timestamp (closest `t`)

### Event ordering

Events are emitted in this order: `open`, `scale_in`, `partial`, `close`. Only events with saved snapshots appear. An active trade with only an `open` snapshot returns one event.

### Location in dashboard.py

Add `get_trade_chart()` function and register route `app.get("/api/trade-chart")(get_trade_chart)` alongside the existing `entry-chart` route.

---

## Frontend

### Button placement

**Active trades card** (currently no chart button):
- Add 📊 button to each active trade card
- Calls `openTradeChart(trade)` with `trade.trade_id`

**History rows** (existing 📊 button):
- When `trade.trade_id` is present → call `openTradeChart(trade)` (new combined chart)
- When `trade.trade_id` is absent (older trades without snapshots) → fall back to existing `openEntryChart(trade)` behavior

### `openTradeChart(trade)` function

1. Fetch `/api/trade-chart?trade_id={trade.trade_id}`
2. On success: open modal, render with `_drawTradeChart(candles, events, meta)`
3. On 404: fall back to `openEntryChart(trade)`
4. On other error: show error message in modal

### `_drawTradeChart(candles, events, meta)` function

Canvas-based rendering using the same approach as existing `_drawEntryChart()`.

**Arrow direction table:**

| Event type   | LONG          | SHORT         |
|--------------|---------------|---------------|
| open         | ▲ green below | ▼ red above   |
| scale_in     | ▲ amber below | ▼ amber above |
| partial      | ▼ purple above| ▲ purple below|
| close        | ▼ red above   | ▲ green below |

**Color assignments:**
- `open`: `#3fb950` (green)
- `scale_in`: `#e3b341` (amber)
- `partial`: `#a371f7` (purple)
- `close`: `#f85149` (red)

**Per-event candle rendering:**
- Background tint on event candle (8% opacity of event color)
- Candle body uses event color
- White horizontal tick (`#ffffff`, 2px) at `event.price` spanning full candle width

**Price lines** (from `open` event):
- Entry: `#58a6ff` dashed, labeled `ENT`
- SL: `#f85149` dashed, labeled `SL`
- TP: `#3fb950` dashed, labeled `TP`

**Active trade indicator:**
- When only `open` event present: render `● OPEN` badge in top-right of chart
- No close/partial arrows rendered (they don't exist yet)

**Chart title:**
- Left: `{SYMBOL} · {STRATEGY} · {INTERVAL} · {SIDE}`
- Right: PnL summary if available from trade data (optional, show blank if not)

---

## Fallback Behavior

| Scenario | Behavior |
|----------|----------|
| `trade_id` not present on trade object | Call existing `openEntryChart()` |
| `/api/trade-chart` returns 404 | Fall back to `openEntryChart()` |
| Only `open` snapshot exists | Show entry arrow + SL/TP lines, `● OPEN` badge |
| `scale_in` snapshot missing | Skip scale-in arrow, no gap or error |

---

## Files Modified

| File | Change |
|------|--------|
| `dashboard.py` | Add `get_trade_chart()` function + route |
| `dashboard.html` | Add `openTradeChart()`, `_drawTradeChart()`, 📊 button on active cards, update history 📊 button logic |
| `tests/test_trade_chart.py` | Add tests for new endpoint |

---

## Testing

- `test_trade_chart_returns_merged_candles` — 4 snapshots → single sorted candle array, correct `candle_idx` values
- `test_trade_chart_active_trade` — only `open` snapshot → single event, no error
- `test_trade_chart_missing_trade_id` → 400
- `test_trade_chart_no_snapshots` → 404
- `test_trade_chart_candle_dedup` — overlapping timestamps between snapshots → later snapshot wins
