# S1 on IG Bot — Design Spec

**Date:** 2026-05-22
**Status:** Approved (pending user spec review)
**Scope:** Add Strategy 1 (MTF RSI Breakout) to the IG CFD bot alongside the existing S5 strategy, across all 6 instruments (US30, US100, Gold, EUR/USD, GBP/USD, USD/JPY). Includes paper + live trading and backtest support.

---

## 1. Goals & Constraints

### Goals
- Run S1 alongside S5 on every IG instrument.
- One position per instrument across both strategies — first non-HOLD signal each tick wins, no second entry until that position closes (matches Bitget/Bybit semantics).
- Use ATR-based risk math on the IG path (SL / TP / SR clearance), independent of S1's percentage-based math on the Bitget path.
- Extend `backtest_ig.py` so per-instrument ATR multiples can be tuned before going live.
- Preserve all existing S5 behaviour and all existing Bitget S1 behaviour.

### Constraints
- IG REST historical-price quota: 10,000 points/week per account. Naive 3m polling on 6 instruments would exhaust this — design uses lazy 3m fetch (only after Daily ADX + 1H BOS pre-check passes) and capped `m3_limit` per instrument.
- `evaluate_s1` is consumed by `bot.py`, `backtest.py`, and `optimize.py` today. Any signature change must remain backward-compatible (optional `cfg` parameter).
- IG's `ig_client` does not expose exchange-side trailing stops; bot-side swing trail is the only trail mechanism. This matches S1's existing `S1_USE_SWING_TRAIL` shape.

---

## 2. Architecture

### Strategy dispatcher in `ig_bot.IGBot._tick_instrument`

`_tick_instrument` becomes a dispatcher that walks an ordered list of enabled strategies per instrument:

```
_tick_instrument(instrument, now):
    if (pos or pending) and session_end_for(instrument, now):
        _session_end_close(instrument); return
    if pos:
        _monitor_position(instrument)        # routes by pos["strategy"]
        return                                # one position rule — done
    if not _in_trading_window(now): return
    if pending_order: _check_pending_order(...); return

    for strategy in _enabled_strategies(instrument):  # CONFIG order
        result = strategy.evaluate(instrument, candles_for(strategy))
        strategy.update_scan_state(instrument, result)
        if result.signal != "HOLD":
            strategy.handle_signal(instrument, result)
            return                            # first non-HOLD wins
```

### `_StrategyAdapter` (in `ig_bot.py`, not a new file)

A lightweight namespace per strategy with four methods:

- `evaluate(instrument, candles) → SignalResult`
- `handle_signal(instrument, result)` — places market order (S1) or pending limit (S5) and updates `_positions[name]` / `_pending_orders[name]` with `"strategy": "S1"|"S5"` tag
- `monitor_position(instrument, pos)` — strategy-specific tick logic while a position is open (S5: OB partial + swing trail; S1: structural swing trail)
- `dna_snapshot(instrument)` — entry-time fingerprint for CSV `snap_*` columns

Two adapters instantiated at startup: `_S5_ADAPTER` (existing logic moved into it) and `_S1_ADAPTER` (new).

### `_get_candles_for(strategy, instrument)`

Each strategy declares its timeframe needs:
- **S5:** 1D + 1H + 15m (fetched eagerly each tick — already cached-delta in `_get_candles`)
- **S1:** 1D + 1H eagerly; 3m **lazily** — fetched only when `evaluate_s1`'s daily + HTF pre-check passes

`_get_candles`'s interval map gains `"3m": 180_000` ms.

### Position monitor dispatch

`_positions[name]` gains a required `"strategy": "S1"|"S5"` field at open time. `_monitor_position` reads that field and calls the matching adapter's `monitor_position` method. If `s1_enabled` flips False while an S1 trade is open, the monitor still routes correctly — the tag is on the position dict, not from config.

---

## 3. Strategy module changes (`strategies/s1.py`)

### Backward-compatible `cfg=` parameter

`evaluate_s1` and all internal helpers (`check_daily_trend`, `detect_consolidation`, `check_ltf_long`, `check_ltf_short`) gain an optional `cfg` parameter. When `cfg is not None` (IG path), all parameters are read from the instrument CONFIG dict. When `cfg is None` (Bitget/backtest path), the existing `from config_s1 import ...` imports run unchanged.

```python
def evaluate_s1(symbol, htf_df, ltf_df, daily_df, allowed_direction, cfg=None):
    if cfg is not None:
        S1_ENABLED              = cfg["s1_enabled"]
        ADX_TREND_THRESHOLD     = cfg["s1_adx_trend_threshold"]
        DAILY_EMA_SLOW          = cfg["s1_daily_ema_slow"]
        DAILY_RSI_LONG_THRESH   = cfg["s1_daily_rsi_long_thresh"]
        DAILY_RSI_SHORT_THRESH  = cfg["s1_daily_rsi_short_thresh"]
        RSI_PERIOD              = cfg["s1_rsi_period"]
        RSI_LONG_THRESH         = cfg["s1_rsi_long_thresh"]
        RSI_SHORT_THRESH        = cfg["s1_rsi_short_thresh"]
        CONSOLIDATION_CANDLES   = cfg["s1_consolidation_candles"]
        CONSOLIDATION_RANGE_PCT = cfg["s1_consolidation_range_pct"]
        BREAKOUT_BUFFER_PCT     = cfg["s1_breakout_buffer_pct"]
    else:
        from config_s1 import (...)   # existing Bitget path
```

### New ATR-based exit helpers (IG-only)

Added to `strategies/s1.py`. Called only when `cfg is not None`:

```python
def compute_s1_sl_atr(direction, entry, box_high, box_low, atr_value, cfg):
    """Structural SL with ATR cap. Floor = box ± buffer; cap = entry ± atr_mult × ATR."""
    sl_buffer = cfg["s1_sl_buffer_pct"]
    if direction == "LONG":
        return max(entry - cfg["s1_sl_atr_mult"] * atr_value,
                   box_low * (1 - sl_buffer))
    return min(entry + cfg["s1_sl_atr_mult"] * atr_value,
               box_high * (1 + sl_buffer))

def compute_s1_tp_atr(direction, entry, atr_value, cfg):
    """TP1 (50% partial) trigger at entry ± tp_atr_mult × ATR."""
    delta = cfg["s1_tp_atr_mult"] * atr_value
    return entry + delta if direction == "LONG" else entry - delta
```

The Bitget exit functions `compute_and_place_long_exits` / `compute_and_place_short_exits` remain unchanged — they keep the 5%/10% percentage math.

### S/R clearance — ATR-based on IG path

The 15% S/R clearance gate becomes `s1_sr_clearance_atr_mult` on the IG path: skip if nearest daily S/R level is closer than `N × daily_ATR`. The check moves into `evaluate_s1` itself, gated on `cfg is not None`.

### Swing trail — IG variant

A new `maybe_trail_sl_ig(instrument, pos, ig_mod, candles_df)` mirrors the structural step-up logic of the existing `maybe_trail_sl` but:
- Reads CONFIG keys (`s1_swing_lookback`, `s1_sl_buffer_pct`, `s1_use_swing_trail`) from `instrument`
- Calls `ig_mod.update_position_sl` instead of `tr_mod.update_position_sl`
- Mutates `pos["sl"]` directly (caller persists `_save_state()`)

The existing Bitget `maybe_trail_sl` remains untouched.

---

## 4. Per-instrument CONFIG additions

Each `config_ig_*.py` instrument CONFIG dict gains:

```python
# Strategy enablement
"s5_enabled": True,
"s1_enabled": True,

# 3m candle limit (S1; lazy-fetched)
"m3_limit": 30,

# ── S1 params ────────────────────────────────────
# Timeframes
"s1_htf_interval":   "1H",
"s1_ltf_interval":   "3m",
"s1_daily_interval": "1D",

# Daily trend filter
"s1_adx_trend_threshold":    25,
"s1_daily_ema_slow":         20,
"s1_daily_rsi_long_thresh":  60,
"s1_daily_rsi_short_thresh": 40,

# RSI gate
"s1_rsi_period":       14,
"s1_rsi_long_thresh":  65,
"s1_rsi_short_thresh": 35,

# Consolidation box
"s1_consolidation_candles":   2,
"s1_consolidation_range_pct": 0.003,   # tuned per instrument

# Breakout
"s1_breakout_buffer_pct": 0.0005,      # tuned per instrument

# ATR-based risk (IG-only)
"s1_atr_period":            14,
"s1_sl_atr_mult":           1.5,
"s1_tp_atr_mult":           3.0,
"s1_sl_buffer_pct":         0.001,
"s1_sr_clearance_atr_mult": 3.0,

# Sizing
"s1_contract_size": 0.04,
"s1_partial_size":  0.02,

# Swing trail
"s1_use_swing_trail": True,
"s1_swing_lookback":  20,
```

All starting values are **placeholders** — grid-search via the extended backtester (§7) lands the final per-instrument values before live launch.

For FX pairs (EUR/USD, GBP/USD, USD/JPY), `s1_consolidation_range_pct` and `s1_breakout_buffer_pct` will be much smaller given pip-scaled price magnitudes. Backtest tuning lands the values.

`_validate_instruments()` in `config_ig.py` adds required-key checks for the S1 block, gated on `s1_enabled=True`. Instruments that omit S1 keys and set `s1_enabled=False` still load — backward-compatible.

---

## 5. Data contracts

### `ig_trades.csv` (additive)

7 new columns appended to `_TRADE_FIELDS`:

| Column | Written on | Source |
|---|---|---|
| `snap_strategy` | every event | `"S1"` or `"S5"` |
| `snap_s1_rsi` | S1_LONG/S1_SHORT | 3m RSI at entry |
| `snap_s1_adx` | S1_LONG/S1_SHORT | daily ADX at entry |
| `snap_s1_box_high` | S1_LONG/S1_SHORT | 3m coil box upper |
| `snap_s1_box_low` | S1_LONG/S1_SHORT | 3m coil box lower |
| `snap_s1_atr` | S1_LONG/S1_SHORT | daily ATR used for SL/TP sizing |
| `snap_s1_sr_clearance_atr` | S1_LONG/S1_SHORT | nearest daily S/R clearance in ATR-multiples |

Action prefixes: `S1_LONG`, `S1_SHORT`, `S1_PARTIAL`, `S1_CLOSE`. Total column count 20 → 27. Existing S5 rows leave S1 columns blank; backward-parseable.

### `ig_state.json` (restructure with migration)

`scan_signals[name]` becomes strategy-keyed:

```json
"scan_signals": {
  "US100": {
    "S5": { "signal": "PENDING_LONG", "reason": "...", "ob_low": ..., "ob_high": ..., "entry_trigger": ..., "sl": ..., "tp": ..., "updated_at": ... },
    "S1": { "signal": "HOLD",         "reason": "...", "rsi": ..., "adx": ..., "htf_bull": ..., "htf_bear": ..., "consolidating": ..., "box_high": ..., "box_low": ..., "updated_at": ... }
  }
}
```

`positions[name]` and `pending_orders[name]` remain single-slotted. `positions[name]` gains required `"strategy": "S1"|"S5"` field at open.

**Migration** (in `_load_state`): if `scan_signals[name]` is a dict with `signal` at top level (old flat shape), wrap as `{"S5": <old>}`. Wrapped in try/except — on any failure, scan_signals starts fresh; never blocks bot startup.

### Dashboard

`dashboard.html` `renderIGScanner` becomes strategy-aware: render S5 and S1 panels side-by-side per instrument, each reading its respective sub-key from `scan_signals[name]`. The existing strategy-filter dropdown extends to filter IG trade rows by `snap_strategy`.

`dashboard.py` `/api/ig/state` passes `scan_signals` through unmodified — no backend change.

---

## 6. Error handling

- **Lazy 3m fetch failure.** If 3m fetch fails after daily + HTF pre-check passes, S1 returns `HOLD` with `"3m fetch failed — skipping S1 this tick"` reason. Dispatcher moves on; no exception bubbles up.
- **ATR=0 guard.** If daily ATR computes to 0 (insufficient candles, flat market), S1 returns `HOLD` with reason. Prevents `SL == entry` edge case.
- **Concurrent-position race.** Dispatcher walks strategies in CONFIG order; first non-HOLD wins. Pending orders treated as positions for "no second entry" purposes (existing behavior).
- **Strategy disabled mid-trade.** Monitor dispatch reads `pos["strategy"]`, not config. Disabling `s1_enabled` only blocks *new* entries — open trades continue to close normally.
- **Migration safety.** State migration wrapped in try/except; failure starts scan_signals fresh.
- **ATR helper imports.** All new ATR helpers live in `strategies/s1.py` to keep S1 logic colocated. No new cross-file dependencies.

---

## 7. Testing

Tests in `tests/`, mirroring existing patterns:

- **`tests/test_strategies_s1.py`** (extend)
  - `cfg=None` path identical to current behavior (regression guard)
  - `cfg=instrument` path returns same signals when CONFIG mirrors `config_s1` constants
  - `compute_s1_sl_atr` / `compute_s1_tp_atr` unit tests with known inputs
  - ATR=0 returns HOLD
- **`tests/test_ig_bot.py`** (new tests)
  - Dispatcher walks enabled strategies in CONFIG order
  - First non-HOLD wins; second strategy not evaluated
  - `pos["strategy"]` set correctly at open
  - `_monitor_position` dispatches to right monitor by `pos["strategy"]`
  - Pending order blocks new evaluation
  - Lazy 3m fetch: 3m not fetched when daily ADX fails
  - State migration: old flat `scan_signals` shape loads cleanly
  - `_validate_instruments` errors on missing S1 keys when `s1_enabled=True`
- **`tests/test_ig_csv.py`** (extend)
  - New S1 columns written on `S1_LONG` / `S1_PARTIAL` / `S1_CLOSE`
  - Existing S5 rows still parse (additive columns blank)
- **No new Bitget tests required** — strategy module changes are guarded by `cfg is not None`. The existing `test_strategies_s1.py` Bitget tests must continue passing unchanged as the regression guarantee.

`qa-trading-bot` skill runs after every change set.

---

## 8. Backtest extension (`backtest_ig.py`)

In scope for this work:

- `--strategy s1|s5|both` flag (default `both`)
- For each instrument, walk the dispatcher logic over historical candles
- Reuse the new ATR helpers from `strategies/s1.py`
- Outputs to `backtest_ig_report.html`: extended template with a strategy column and a per-strategy summary section
- Grid-search support: parameter sweeps over `s1_sl_atr_mult`, `s1_tp_atr_mult`, `s1_consolidation_range_pct`, `s1_breakout_buffer_pct` per instrument
- Run grid search per instrument before promoting CONFIG values from placeholders to tuned values

---

## 9. Rollout sequence

1. Land strategy module changes (`cfg=instrument` path) + ATR helpers + unit tests. Bitget regression-safe by construction (Bitget tests must pass unchanged).
2. Land dispatcher in `ig_bot.py` with S1 adapter; ship with `s1_enabled=False` for every instrument by default.
3. Land CONFIG additions for all 6 instruments with placeholder values; `s1_enabled=False` everywhere.
4. Land backtest extension; run grid search per instrument; promote tuned values into CONFIG.
5. Enable `s1_enabled=True` per instrument **in paper mode** for ~1 week. Verify CSV/state/dashboard rendering and trade behavior.
6. Promote to live one instrument at a time, US100 first.

---

## 10. Dependency-doc updates (required post-implementation)

Per the `check-trading-bot-dependencies` rule, the following sections of `docs/DEPENDENCIES.md` must be updated before commit:

- **§2.1** — add `evaluate_s1(..., cfg=...)` to cross-bot section; note `strategies/s1.py` is now shared like `strategies/s5.py`
- **§4.3** — `ig_trades.csv` column count 20 → 27, new S1 snap fields
- **§4.4** — `ig_state.json` `scan_signals` shape change + migration note; `positions[name]` gains `"strategy"` field
- **§5** — CONFIG keys for S1 (per-instrument, gated on `s1_enabled`)
- **§7** — strategy implementations table: S1 IG path
- **§10** — Bitget vs IG S1 risk-math difference (% vs ATR); add `s1_contract_size` (per-strategy) vs `contract_size` (S5/default) to confusing-names section

---

## 11. Open items deferred

- **Per-strategy sizing.** `s1_contract_size` defaults to mirror `contract_size`; a future ticket may let it diverge per instrument.
- **Cross-strategy exposure caps.** Currently one position per instrument prevents this from mattering. If concurrent positions are ever allowed, an exposure cap layer is needed.
- **Stream subscriptions for 3m.** Out of scope; lazy REST fetch is the launch mechanism. If 3m quota becomes a problem at scale, streaming is the path forward.
