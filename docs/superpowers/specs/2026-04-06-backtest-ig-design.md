# IG S5 Backtest — Design Spec

**Date:** 2026-04-06  
**Status:** Approved  
**Scope:** New standalone tool `backtest_ig.py` — no changes to any existing file

---

## 1. Overview

Walk-forward backtest of the S5 (SMC Order Block Pullback) strategy as it runs in `ig_bot.py`, using historical candle data sourced from Yahoo Finance via `yfinance` and cached to parquet. Outputs a self-contained HTML report with per-instrument stats and inline candlestick charts for each completed trade.

---

## 2. Architecture

Single new file: `backtest_ig.py`. No modifications to existing files.

```
backtest_ig.py
  ├── fetch layer     — yfinance → parquet cache in data/ig_cache/
  ├── simulation core — 15m bar-by-bar walk-forward per instrument
  └── report builder  — static HTML with inline candle data
```

**Imports (read-only):**
- `strategy.evaluate_s5`, `strategy.calculate_ema`
- `config_ig.INSTRUMENTS` (reads US30 + GOLD CONFIG dicts)

**Yahoo Finance symbol mapping (hardcoded in file):**
```python
_YF_SYMBOLS = {
    "US30": "^DJI",
    "GOLD": "GC=F",
}
```

**CLI:**
```bash
python backtest_ig.py                    # fetch + cache + run both instruments
python backtest_ig.py --no-fetch         # use existing parquet cache only
python backtest_ig.py --instrument US30  # single instrument
python backtest_ig.py --output my.html
```

---

## 3. Data Layer

### 3.1 Yahoo Finance Symbols

No credentials required. Uses `yfinance` (already available on PyPI).

| Instrument | Yahoo ticker | Notes |
|---|---|---|
| US30 | `^DJI` | NYSE market hours only (09:30–16:00 ET) |
| GOLD | `GC=F` | Gold futures front-month, ~23h/day |

### 3.2 Cache Files

```
data/ig_cache/US30_1D.parquet
data/ig_cache/US30_1H.parquet
data/ig_cache/US30_15m.parquet
data/ig_cache/GOLD_1D.parquet
data/ig_cache/GOLD_1H.parquet
data/ig_cache/GOLD_15m.parquet
```

Schema: `ts (int64 ms), open, high, low, close, vol` — identical to `ig_client.get_candles()` output so `evaluate_s5` receives the same data shape as live.

### 3.3 Fetch Behaviour

- yfinance periods used: `10y` (1D), `2y` (1H), `60d` (15m)
- Expected data (verified):
  - 1D → ~10 years (~2,514 bars)
  - 1H → ~2 years (~3,462 bars for ^DJI)
  - 15m → ~3 months (~1,502 bars for ^DJI, ~4,373 for GC=F)
- Backtest window is automatically capped to the available 15m range (no manual date range needed)
- `--no-fetch` skips yfinance entirely and reads parquet only

---

## 4. Simulation Engine

### 4.1 State Machine

Per instrument, three states: `IDLE → PENDING → IN_TRADE → IDLE`

### 4.2 Bar Loop

```
for each 15m bar i (chronological):

  ── IN_TRADE ──────────────────────────────────────────────────
  if partial not yet taken:
    if LONG and bar high >= tp1:  log PARTIAL, sl → entry (break-even)
    if SHORT and bar low  <= tp1: log PARTIAL, sl → entry (break-even)
  check SL:
    LONG:  bar low  <= sl → CLOSE LOSS
    SHORT: bar high >= sl → CLOSE LOSS
  check TP (remainder after partial, or full if no partial):
    LONG:  bar high >= tp → CLOSE WIN
    SHORT: bar low  <= tp → CLOSE WIN
  check session end → force close at bar close price (CLOSE SESSION)
  continue

  ── PENDING ───────────────────────────────────────────────────
  check OB invalidation (using bar low/high as mark proxy):
    LONG:  bar low  < ob_low  * (1 - s5_ob_invalidation_buffer_pct) → CANCEL OB_INVALID
    SHORT: bar high > ob_high * (1 + s5_ob_invalidation_buffer_pct) → CANCEL OB_INVALID
  check expiry:
    bar ts > expires (signal ts + 4h) → CANCEL EXPIRED
  check session end → CANCEL SESSION_END
  check trigger fill:
    LONG:  bar low  <= trigger → fill at trigger, → IN_TRADE
    SHORT: bar high >= trigger → fill at trigger, → IN_TRADE
  continue

  ── IDLE ──────────────────────────────────────────────────────
  if outside session window or weekend: continue
  build windows:
    daily_df = all 1D bars with ts <= bar ts (capped at instrument daily_limit)
    htf_df   = last htf_limit 1H bars with ts <= bar ts
    m15_df   = last m15_limit 15m bars ending at bar i
  derive allowed_direction:
    ema_fast = EMA(daily_df.close, s5_daily_ema_fast).iloc[-1]
    ema_slow = EMA(daily_df.close, s5_daily_ema_slow).iloc[-1]
    allowed_direction = "BULLISH" if ema_fast > ema_slow else "BEARISH"
  call evaluate_s5(epic, daily_df, htf_df, m15_df, allowed_direction, cfg=instrument)
  if sig in (PENDING_LONG, PENDING_SHORT):
    set PENDING state: {side, trigger, sl, tp, ob_low, ob_high, expires=bar_ts+4h}
```

### 4.3 Partial TP Price

```python
# LONG
tp1 = entry + (entry - sl)   # 1:1 R:R

# SHORT
tp1 = entry - (sl - entry)   # 1:1 R:R
```

Same as live bot. After partial: SL moves to entry (break-even). Remainder runs to `tp` or `sl`.

### 4.4 Session Windows

Uses `session_start` / `session_end` tuples from each instrument CONFIG dict, evaluated in ET timezone — same as `_in_trading_window_for()` and `_is_session_end_for()` in `ig_bot.py`.

### 4.5 Trade Record

Each completed trade (filled → closed) produces:
```python
{
  "instrument":    str,           # "US30" | "GOLD"
  "side":          str,           # "LONG" | "SHORT"
  "entry_dt":      datetime,
  "exit_dt":       datetime,
  "trigger":       float,
  "entry":         float,         # = trigger (limit fill)
  "sl":            float,
  "tp":            float,
  "tp1":           float,         # partial TP price
  "ob_low":        float,
  "ob_high":       float,
  "partial_hit":   bool,
  "exit_reason":   str,           # "TP" | "SL" | "SESSION_END"
  "exit_price":    float,
  "pnl_pts":       float,         # in instrument points
  "pnl_pct":       float,         # as % of entry
  "candles":       list[dict],    # ~100 15m candles ts/o/h/l/c/v for chart
}
```

Cancelled signals (OB_INVALID, EXPIRED, SESSION_END before fill) are counted in stats but have no chart and no `candles` field.

---

## 5. Report

### 5.1 Structure

Dark-theme HTML (same palette as `backtest_report.html`). Tabs: **Overall | US30 | GOLD**.

### 5.2 Summary Stats (per instrument + overall)

- Total signals generated
- Filled (fill rate %)
- Cancelled breakdown: OB invalidated / expired / session end
- Win rate (of filled trades)
- Partial TP hit rate
- Avg win (points), avg loss (points)
- Profit factor
- Total PnL (points)

### 5.3 Trade Table

Columns: Date | Side | Entry | SL | TP | Partial? | Exit Reason | Exit Price | PnL (pts) | Chart

**Chart** button appears only for filled+closed trades. On click → modal overlay with canvas chart.

### 5.4 Trade Snapshot Chart

Candle data embedded inline as JSON in the HTML at report-generation time (no server needed).

Modal shows:
- 15m candlesticks (~100 bars, ~50 before entry through exit)
- OB zone shaded (ob_low → ob_high)
- Entry marker (trigger fill)
- Partial TP line (if hit)
- SL and TP as horizontal dashed lines
- Exit marker (TP / SL / session close)

Canvas rendering adapted from `_drawEntryChart` / `_drawTradeChart` in `dashboard.html` — same dark theme, same style.

---

## 6. Dependencies & Constraints

| Constraint | Detail |
|---|---|
| No existing file modified | `backtest_ig.py` is standalone |
| evaluate_s5 signature | Must pass `cfg=instrument` (not None) |
| allowed_direction | Derived from daily EMA fast/slow per bar (same as ig_bot.py line 646-648) |
| Candle schema | `ts, open, high, low, close, vol` — matches ig_client output |
| Session windows | ET timezone, from instrument CONFIG session_start/session_end |
| OB invalidation buffer | Read from `instrument["s5_ob_invalidation_buffer_pct"]` |
| 15m data limit | ~3 months via yfinance — backtest window is data-constrained |
| New .env keys | None required |
| New pip dependency | `yfinance` |

---

## 7. Out of Scope

- No changes to `ig_bot.py`, `strategy.py`, `config_ig*.py`, `dashboard.py`, `dashboard.html`
- No live trading integration
- No parameter optimisation (that's `optimize_ig.py`)
- No multi-timeframe chart views (15m only for snapshots)
