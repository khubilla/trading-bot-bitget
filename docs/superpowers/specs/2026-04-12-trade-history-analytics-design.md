# Trade History Analytics Dashboard — Design

**Date:** 2026-04-12
**Status:** Design approved, ready for implementation plan
**Scope:** Bitget bot only (strategies S1–S6). IG bot is out of scope.

## Goal

Add a new "Analytics" tab to the dashboard that lets the user review closed-trade performance **per strategy**. Each strategy gets a combined cumulative-P&L curve + per-trade P&L bars chart, a sortable trade table, and an expandable per-trade parameter card showing the `snap_*` values recorded at entry.

This is a read-only, additive feature. It does not change `trades.csv`, `state.json`, `_TRADE_FIELDS`, or any existing endpoint.

## Non-goals

- No changes to how trades are recorded.
- No IG-bot analytics (`ig_trades.csv` not touched).
- No auto-refresh — analytics is for reviewing history, not live monitoring. Manual refresh only.
- No writing of analytics results back to any file.

## User requirements (from brainstorming)

| # | Decision | Choice |
|---|----------|--------|
| 1 | Bot scope | Bitget only |
| 2 | Chart type | Cumulative P&L line **+** per-trade P&L bars (combined) |
| 3a | X-axis | Toggle between trade number and close timestamp |
| 3b | Range | User-selectable: All / 30d / 90d / last N |
| 4 | Parameter view | Sortable table below chart + expandable detail card on row/bar click |
| 5a | Placement | New top-level tab ("Live" / "Analytics") at dashboard top |
| 5b | Data source | Follows existing `--paper` flag (`trades_paper.csv` vs `trades.csv`) |

## Architecture

### New files
- `analytics.py` — pure-function aggregation module.
- `tests/test_analytics.py` — pytest unit + integration tests.

### Modified files
- `dashboard.py` — add `/api/analytics` endpoint.
- `dashboard.html` — add top-level tab bar and Analytics tab content.

### Data flow

```
trades.csv / trades_paper.csv
        │
        ▼
dashboard.py  GET /api/analytics?range=all|30d|90d|lastN&x=trade|time&n=<N>
        │     (PAPER_MODE picks the right CSV; same logic as dashboard.py:300)
        ▼
analytics.build_analytics()
        │  load_closed_trades → group_by_strategy → filter_range → build_series / summarize
        ▼
JSON response:
{
  "mode": "paper" | "live",
  "strategies": {
    "S1": {
      "trades":  [ <one row per closed trade, with relevant snap_* fields> ],
      "series":  {
        "cum_pnl": [{"x": <trade# or ISO>, "y": <running total>}, ...],
        "bars":    [{"x": ..., "y": <pnl>, "color": "green"|"red"}, ...]
      },
      "summary": {"count": 42, "wins": 25, "losses": 17,
                  "win_rate": 0.595, "total_pnl": 123.4,
                  "avg_win": ..., "avg_loss": ..., "best": ..., "worst": ...}
    },
    "S2": {...}, "S3": {...}, "S4": {...}, "S5": {...}, "S6": {...}
  }
}
        │
        ▼
dashboard.html  Analytics tab → per-strategy sub-tab → lightweight-charts + table
```

### Read-only contract

- Does not mutate any CSV.
- Does not alter `_TRADE_FIELDS` in `bot.py` (§4.2 in DEPENDENCIES.md).
- Does not alter the existing `/state` endpoint or the current "Trade History" panel.
- Does not depend on `state.json` fields.
- Compatible with `optimize.py` and `bot.py:85 _rebuild_stats_from_csv()` — they read the same CSV, our code only reads it too.

## `analytics.py` module

Pure functions. No FastAPI imports. No I/O except reading the CSV path it is handed.

```python
STRATEGIES = ("S1", "S2", "S3", "S4", "S5", "S6")

STRATEGY_SNAP_FIELDS = {
    "S1": ("snap_rsi", "snap_adx", "snap_htf", "snap_coil",
           "snap_box_range_pct", "snap_sentiment"),
    "S2": ("snap_daily_rsi",),
    "S3": ("snap_entry_trigger", "snap_sl", "snap_rr"),
    "S4": ("snap_rsi_peak", "snap_spike_body_pct",
           "snap_rsi_div", "snap_rsi_div_str"),
    "S5": ("snap_s5_ob_low", "snap_s5_ob_high", "snap_s5_tp"),
    "S6": ("snap_s6_peak", "snap_s6_drop_pct", "snap_s6_rsi_at_peak"),
}
SHARED_SNAP = ("snap_sr_clearance_pct",)

COMMON_FIELDS = ("timestamp", "trade_id", "symbol", "side",
                 "entry", "exit_price", "pnl", "pnl_pct",
                 "result", "exit_reason", "leverage", "margin")
```

### Functions

| Function | Purpose |
|----------|---------|
| `load_closed_trades(csv_path) -> list[dict]` | Read CSV, pair each `*_CLOSE` row with its matching `*_LONG`/`*_SHORT` open via `trade_id`. Returns closed-trade dicts enriched with open-side `snap_*` values. Empty list if file missing. Strategy is parsed from the OPEN action prefix. |
| `group_by_strategy(trades) -> dict[str, list[dict]]` | Bucket into all 6 strategy keys (always all 6, even if empty, so UI can render empty states). |
| `filter_range(trades, range_spec, now=None) -> list[dict]` | `range_spec`: `"all"` \| `"30d"` \| `"90d"` \| `int` (last N). Time ranges filter by close timestamp; int keeps most recent N. `now` injectable for tests. |
| `build_series(trades, x_mode) -> dict` | Returns `{"cum_pnl": [...], "bars": [...]}`. `x="trade"` → integer index 1..N. `x="time"` → ISO close timestamp. Bars include `"color"`. |
| `summarize(trades) -> dict` | count, wins, losses, win_rate, total_pnl, avg_win, avg_loss, best, worst. Safe with 0 trades. |
| `build_analytics(csv_path, range_spec, x_mode) -> dict` | Top-level orchestrator. Returns full `{"mode", "strategies"}` payload. |

### Pairing logic

Mirrors `dashboard.py:_load_csv_history` (2-pass):

1. Pass 1 — index OPEN / SCALE_IN / PARTIAL rows by `trade_id`; capture open-side `snap_*` fields.
2. Pass 2 — for each `*_CLOSE` row:
   - Sum any PARTIAL `pnl` into the final `pnl`.
   - Join open-side fields by `trade_id`.
   - Derive `strategy` from the OPEN action prefix (e.g. `S3_LONG` → `"S3"`).
   - Skip orphan closes (no matching OPEN).
3. SCALE_IN rows are not counted as separate trades; their entry is part of the same `trade_id`.

### Edge cases

- Missing CSV → empty result, UI shows per-strategy empty state.
- Orphan OPEN (still live) → excluded (only CLOSE rows drive output).
- Orphan CLOSE (no matching OPEN) → skipped silently.
- Malformed `pnl` or timestamp → row skipped via `_safe_float`-style coercion.
- Unknown strategy prefix → skipped, not errored.
- Large CSV (10k+ rows) → single-pass O(n) parse is acceptable. Per-strategy fetch is a future optimization if needed.

## `dashboard.py` endpoint

```python
@app.get("/api/analytics")
@limiter.limit("30/minute")
async def get_analytics(request: Request,
                        range: str = "all",
                        x: str = "trade",
                        n: int | None = None):
    # Validation:
    #   range ∈ {"all","30d","90d","lastN"}
    #   x     ∈ {"trade","time"}
    #   if range == "lastN": 1 ≤ n ≤ 10000
    # Returns 400 on invalid params.
    # Resolves csv_path from PAPER_MODE (reuses logic at dashboard.py:300).
    # range_spec = n if range == "lastN" else range
    # return JSONResponse(analytics.build_analytics(csv_path, range_spec, x))
```

- Rate-limited like other analytics-style endpoints (30/min).
- Returns 200 with empty-but-well-formed payload when CSV is missing.
- No new env vars, no new config entries.
- Protected by the same bearer-token auth middleware as every other `/api/*` endpoint.

## `dashboard.html` UI

### Top-level tabs

A new tab strip at the very top of the main container:

```
┌─────────────────────────────────────────────┐
│  [ Live ]  [ Analytics ]                    │
├─────────────────────────────────────────────┤
│  … existing content in "Live" tab …         │
│                                             │
│  Analytics tab:                             │
│  ┌─────────────────────────────────────┐    │
│  │ Range: [All ▾]   X-axis: [Trade# ▾] │    │
│  │ [ S1 ][ S2 ][ S3 ][ S4 ][ S5 ][ S6 ]│    │
│  ├─────────────────────────────────────┤    │
│  │ Summary: 42 trades · 59% win · +123 │    │
│  │ ┌─ chart ─────────────────────────┐ │    │
│  │ │ equity line + p&l bars          │ │    │
│  │ └─────────────────────────────────┘ │    │
│  │ ┌─ table ─────────────────────────┐ │    │
│  │ │ time | sym | side | pnl | snap…│ │    │
│  │ └─────────────────────────────────┘ │    │
│  │ ┌─ detail card (expanded row) ────┐ │    │
│  │ │ full snap_* key/value grid     │ │    │
│  │ └─────────────────────────────────┘ │    │
│  └─────────────────────────────────────┘    │
└─────────────────────────────────────────────┘
```

- Active top-tab, active strategy sub-tab, range, and x-axis mode are all persisted to `sessionStorage`.
- Analytics tab fetches `/api/analytics` once on activation and caches in memory; a manual **Refresh** button re-fetches. No auto-refresh.
- Changing Range or X-axis triggers a re-fetch with the new params.

### Chart

- Reuses the already-loaded `lightweight-charts@4.1.3` lib (see [dashboard.html:1282](dashboard.html#L1282)).
- Container ~320px tall, per-strategy (one chart instance per active strategy tab).
- `addLineSeries()` for cumulative P&L (amber, 2px, left price scale).
- `addHistogramSeries()` for per-trade P&L bars on a separate overlay price scale (right).
- `x="time"` → UTC unix-seconds timestamps (native lib support).
- `x="trade"` → synthetic integer axis via `timeScale` with formatted tick labels `#1, #2, …`.
- `subscribeClick` → map click → trade → highlight matching table row and expand detail card.

### Table

- Columns: `time`, `symbol`, `side`, `entry`, `exit`, `pnl`, `pnl_pct`, `result`, `exit_reason`, then strategy-specific `STRATEGY_SNAP_FIELDS[strategy]` + `SHARED_SNAP` columns.
- Click a column header → client-side sort (toggle asc/desc).
- Click a row → toggles detail card for that trade and syncs the chart highlight.
- Scrollable; no server-side pagination (range selector is sufficient).

### Detail card

- Appears below the table when a row or chart bar is selected.
- Labeled key/value grid listing every `snap_*` field present on the row, plus all `COMMON_FIELDS`.
- Close button collapses it.

## Error handling

| Condition | Behavior |
|-----------|----------|
| CSV missing | 200 with all-empty strategies payload; UI shows "No closed trades yet" per strategy. |
| Invalid query param | 400 with error body. |
| Malformed CSV row | Row skipped; other rows rendered. |
| Orphan CLOSE / OPEN | Skipped silently (matches existing `_load_csv_history` behavior). |
| Fetch error in browser | Toast/banner "Failed to load analytics — retry?". |
| Auth failure | Existing middleware returns 401; UI surfaces as fetch error. |

## Testing

`tests/test_analytics.py` (pytest):

- `load_closed_trades`: happy path, orphan CLOSE, orphan OPEN, PARTIAL summing, SCALE_IN not double-counted, empty file, missing file.
- `group_by_strategy`: all 6 keys always present; unknown strategy prefix skipped.
- `filter_range`: "all" / "30d" boundary / "90d" / int with N > len / int with N < len; injectable `now`.
- `build_series`: trade-mode indices are 1..N; time-mode timestamps are ISO; bar colors match sign; cum_pnl is the running sum of bars.
- `summarize`: 0 trades (no division by zero), all wins, all losses, mixed.
- `build_analytics`: integration — fixture CSV → expected JSON shape.

No existing tests are modified. The existing `qa-trading-bot` loop must still pass.

## Dependencies impact (per DEPENDENCIES.md)

- **§ 4.2 trades.csv** — read-only consumer added. No column changes. Existing readers (`dashboard.py:68`, `optimize.py:229`, `bot.py:85`) unaffected.
- **§ 9 Dashboard Integration** — currently deferred. This design adds a new endpoint and tab; DEPENDENCIES.md should be updated after implementation to list `/api/analytics` as a new CSV consumer.
- No changes to shared files, state files, or CSV columns → no cross-bot risk.

## Out-of-scope / future

- IG analytics (`ig_trades.csv`, per-instrument S5).
- CSV export of the filtered table.
- Custom date-range picker (beyond 30d / 90d / last N / all).
- Server-side pagination (not needed at current data volumes).
- Auto-refresh.

## Files touched summary

| File | Change |
|------|--------|
| `analytics.py` | **New.** Pure-function aggregation module. |
| `tests/test_analytics.py` | **New.** Unit + integration tests. |
| `dashboard.py` | Add `/api/analytics` endpoint. No other changes. |
| `dashboard.html` | Add top-level tab bar; add Analytics tab content (CSS + HTML + JS). |
| `docs/DEPENDENCIES.md` | Update §4.2 readers list and §9 Dashboard Integration after implementation. |
