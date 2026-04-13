# Trade DNA Fingerprint — Design Spec

**Date:** 2026-04-13
**Status:** Approved

---

## Overview

Each trade entry is augmented with a trend fingerprint — a set of bucketed string fields describing market context at entry time. These fields are recorded in `trades.csv` now (recording-only phase). A future `lookup()` function will use them to answer: *"Has this exact setup appeared before on this symbol, and what was the outcome?"* — replacing `claude_filter.py` entirely.

---

## Goals

- Record trend context at entry for S1–S6 without changing trade logic
- Use only timeframes each strategy already reads — no extra API calls
- Lay the groundwork for a per-symbol, hierarchical pattern-match filter
- Never block a trade due to DNA recording failures

---

## Architecture

### New file: `trade_dna.py`

Two public functions:

```python
def snapshot(strategy: str, symbol: str, candles: dict[str, pd.Series | pd.DataFrame]) -> dict:
    """
    Compute trend fingerprint fields for the given strategy.
    candles: keys are timeframe strings ("daily", "h1", "m15", "m3").
             Values are pd.Series of closes OR pd.DataFrame with OHLCV columns
             (required for ADX state, which needs high/low/close).
    Returns flat dict of snap_trend_* keys → bucketed string values.
    On any error: logs warning, returns {}.
    """

def lookup(strategy: str, symbol: str, fingerprint: dict) -> dict:
    """
    Future drop-in replacement for claude_approve().
    Returns {"approved": bool, "reason": str, "matches": int, "win_rate": float}.
    Raises NotImplementedError until implemented.
    """
```

### Changes to `bot.py`

1. Add `snap_trend_*` fields to `_TRADE_FIELDS` (see full list below)
2. At each strategy entry point, call `snapshot()` just before `_log_trade()`:

```python
from trade_dna import snapshot as dna_snapshot

trend = dna_snapshot("S2", symbol, {"daily": daily_closes})
trade.update(trend)
_log_trade("S2_LONG", trade)
```

Each strategy passes only the candle series already in scope.

---

## Fingerprint Fields

All values are bucketed strings. Empty string `""` for strategies that don't use a given timeframe.

| Field | S1 | S2 | S3 | S4 | S5 | S6 |
|---|---|---|---|---|---|---|
| `snap_trend_daily_ema_slope` | ✓ | ✓ | — | ✓ | ✓ | ✓ |
| `snap_trend_daily_price_vs_ema` | ✓ | ✓ | — | ✓ | ✓ | ✓ |
| `snap_trend_daily_rsi_bucket` | — | ✓ | — | ✓ | — | ✓ |
| `snap_trend_daily_adx_state` | ✓ | — | — | — | — | — |
| `snap_trend_h1_ema_slope` | ✓ | — | — | ✓ | ✓ | — |
| `snap_trend_h1_price_vs_ema` | ✓ | — | — | ✓ | ✓ | — |
| `snap_trend_m15_ema_slope` | — | — | ✓ | — | ✓ | — |
| `snap_trend_m15_price_vs_ema` | — | — | ✓ | — | ✓ | — |
| `snap_trend_m15_adx_state` | — | — | ✓ | — | — | — |
| `snap_trend_m3_price_vs_ema` | ✓ | — | — | — | — | — |

---

## Bucketing Helpers

All helpers are internal to `trade_dna.py`. Thresholds are module-level constants.

### `ema_slope(closes: pd.Series, period: int, n: int = 10) -> str`
- Computes EMA, compares `EMA[-1]` vs `EMA[-10]` as % change
- `> +0.3%` → `"rising"` | `< -0.3%` → `"falling"` | otherwise → `"flat"`
- Constant: `EMA_SLOPE_THRESHOLD = 0.003`
- Returns `""` if series shorter than `period + n`

### `price_vs_ema(price: float, ema: float) -> str`
- `price > ema` → `"above"` | otherwise → `"below"`

### `rsi_bucket(rsi: float) -> str`
Buckets: `"<50"` / `"50-60"` / `"60-65"` / `"65-70"` / `"70-75"` / `"75-80"` / `">80"`

### `adx_bucket(adx: float) -> str`
- `< 25` → `"weak"` | `25–35` → `"moderate"` | `35–50` → `"strong"` | `≥ 50` → `"extreme"`

### `adx_state(adx_series: pd.Series, n: int = 10) -> str`
- Compares `ADX[-1]` vs `ADX[-10]`, absolute difference
- `> +3` → `"rising"` | `< -3` → `"falling"` | otherwise → `"flat"`
- Constant: `ADX_STATE_THRESHOLD = 3`
- Returns `""` if series shorter than `n`

### EMA period used per timeframe
- Daily EMA: 20 (matches existing S1/S2/S5 usage)
- 1H EMA: 20
- 15m EMA: 20
- 3m EMA: 20

---

## Data Flow at Entry

```
strategy entry block (bot.py)
  │
  ├─ candles already in scope (daily_closes, h1_closes, etc.)
  │
  ├─ trend = dna_snapshot("SX", symbol, {"daily": ..., "h1": ..., ...})
  │     └─ trade_dna.py computes snap_trend_* fields
  │          └─ on error: logs warning, returns {}
  │
  ├─ trade.update(trend)
  │
  └─ _log_trade("SX_LONG", trade)
```

---

## Future: `lookup()` Hierarchical Match

When the filter goes live, `lookup()` will:

1. Load `trades.csv`, filter to `strategy == S` and `symbol == X` with a CLOSE row
2. Try fingerprint match at 4 levels (tightest → broadest), stopping when `matches >= 3`:
   - **Level 1**: all applicable fields match
   - **Level 2**: drop `snap_sr_clearance_pct` bucket
   - **Level 3**: daily trend fields + sentiment only
   - **Level 4**: no match → approve (first time seen)
3. Win rate `>= 60%` → approve | `< 40%` → reject | `40–60%` or `< 3 matches` → approve
4. Returns same signature as `claude_approve()`: `{"approved": bool, "reason": str}`

`claude_filter.py` is replaced entirely — `bot.py` switches `claude_approve` import to `trade_dna.lookup`.

---

## Error Handling

- `snapshot()` wraps all computation in `try/except` — any failure returns `{}`, trade proceeds normally
- Individual helpers return `""` for undersized series rather than raising
- `lookup()` raises `NotImplementedError` until implemented — surfaces immediately if called prematurely

---

## Backwards Compatibility

- Existing `trades.csv` rows have `""` in all new columns — CSV reader uses `restval=""` already
- Future `lookup()` treats `""` fingerprint fields as wildcards (old rows won't penalise new trades)
- No changes to existing tests — new fields are additive

---

## Testing

- Unit tests for each bucketing helper with synthetic `pd.Series`
  - `ema_slope`: flat, rising, falling, too-short series
  - `rsi_bucket`: boundary values at each cut-point
  - `adx_state`: rising, falling, flat, too-short series
- Integration test per strategy: `snapshot()` with minimal fake candle data → assert correct keys and valid bucket values
- Error path test: `snapshot()` with empty series → returns `{}`, no exception

---

## Files Changed

| File | Change |
|---|---|
| `trade_dna.py` | New file |
| `bot.py` | Add 10 fields to `_TRADE_FIELDS`; add `dna_snapshot()` call at each of 8 entry points (S1–S6, including S5 long/short and S6) |
| `tests/test_trade_dna.py` | New test file |
