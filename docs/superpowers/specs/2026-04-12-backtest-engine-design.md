# Backtest Engine — Design Spec

**Date:** 2026-04-12  
**Status:** Approved  
**Scope:** New file `backtest_engine.py` + 3m parquet cache in `load_3m()` added to `backtest.py`. No existing files modified except `backtest.py` (add `load_3m`).

---

## 1. Goal

Replace the current per-strategy hand-coded simulations in `backtest.py` with a single unified engine that:

- Runs the **actual `bot.py` tick loop** against historical parquet data
- Covers **all strategies (S1–S6)** simultaneously
- Simulates **all execution rules**: scale-in (S2/S4/S6), partial TP (50%), trailing stop, concurrent trade limit, sentiment gate, pair pause rule
- Uses **3m candles as the execution clock** — each 3m bar = one simulated tick
- Entry signals still use their native timeframes (1D for S2/S4/S6, 15m for S3/S5, 3m/1H/1D for S1) — `get_candles()` returns the correct timeframe slice up to the current sim time
- Respects `MAX_CONCURRENT_TRADES` and all config values from the actual `config_sN.py` files

---

## 2. Architecture

```
backtest_engine.py
├── MockTrader          — fake trader.py, feeds parquet data, simulates positions
├── BacktestState       — fake state.py writes (in-memory, no state.json touched)
├── MockScanner         — fake scanner.py, returns fixed symbol universe + synthetic sentiment
├── BacktestEngine      — orchestrates the time loop, patches modules, runs bot._tick()
└── main()              — CLI entry point
```

### Module monkey-patching (before bot.py is imported)

```python
import sys
sys.modules["trader"]          = MockTrader(universe, parquet_data)
sys.modules["state"]           = BacktestState()
sys.modules["scanner"]         = MockScanner(universe, parquet_data)
sys.modules["snapshot"]        = MockSnapshot()       # all methods are no-ops
sys.modules["claude_filter"]   = MockClaudeFilter()   # claude_approve() always returns True
sys.modules["startup_recovery"]= MockStartupRecovery()# no-ops
```

`bot.py` is imported **after** all patches are in place — it picks up the mocks transparently.

`PAPER_MODE` in `bot.py` is set from `sys.argv` at import time. The backtest ensures `"--paper"` is not in `sys.argv` before importing `bot` — so `bot.py` uses the `trader` import path (which is already mocked).

`MTFBot.__init__` calls `tr.get_all_open_positions()` at startup — MockTrader returns `{}` (no positions at sim start). The startup recovery, Pass A/B, and `_startup_recovery()` blocks are all guarded by `not PAPER_MODE` — since we run in non-paper mode, these blocks will execute but MockTrader's `get_all_open_positions()` returns `{}` so they exit cleanly with nothing to reconcile.

---

## 3. Data Layer

### Parquet caches

| Cache | Path | Granularity | Used by |
|---|---|---|---|
| Daily | `data/daily/<SYM>.parquet` | 1D | S2, S4, S5, S6 signal detection |
| 15m | `data/15m/<SYM>.parquet` | 15m | S3, S5 signal detection |
| 1H | `data/1h/<SYM>.parquet` | 1H | S1 HTF filter, S4 low filter |
| 3m | `data/3m/<SYM>.parquet` | 3m | Execution clock, S1 entry/exit, all exit simulation |

All caches use the same incremental pattern as `load_daily()`:
- Load existing parquet, find last `ts`, fetch only newer candles from Bitget, append + save
- Functions: `load_daily()`, `load_15m()`, `load_1h()`, `load_3m()` — all in `backtest.py`

### Simulation window

Default: **365 days** (configurable via `--days`). 3m data for 365 days across 100 symbols is ~4.7M rows per symbol — stored in parquet, loaded once per symbol into memory.

### Symbol universe

Fixed list from `data/daily/*.parquet` filenames — no live scanner call. Symbols that lack 3m data are skipped with a warning.

---

## 4. MockTrader

Implements the full `trader.py` public interface. Internal state is a dict of open positions keyed by symbol.

### Position state per symbol

```python
{
    "side":           "LONG" | "SHORT",
    "entry":          float,       # fill price
    "qty":            float,       # current qty (reduced after partial TP)
    "initial_qty":    float,       # qty at open (for partial TP detection)
    "sl":             float,       # current SL price
    "tp_trig":        float,       # partial TP trigger price
    "trail_pct":      float,       # trailing callback % (e.g. 10.0)
    "trail_active":   bool,        # True after partial TP fired
    "trail_peak":     float,       # best price seen since trail activated
    "trail_sl":       float,       # current trailing SL level
    "partial_done":   bool,        # True after 50% closed
    "scale_in_after": float,       # sim_time (epoch ms) when scale-in is allowed
    "scale_in_done":  bool,
    "margin":         float,       # margin used
    "leverage":       int,
    "strategy":       str,
    "trade_id":       str,
}
```

### Exit simulation per 3m bar (called before bot._tick())

For each open position, given the current 3m bar `(open, high, low, close)`:

**LONG:**
1. If `low <= sl` → SL hit: close at `sl`, result=LOSS
2. Else if not `partial_done` and `high >= tp_trig` → partial TP: close 50% at `tp_trig`, set `trail_active=True`, `trail_peak=tp_trig`, compute initial `trail_sl = tp_trig * (1 - trail_pct/100)`
3. Else if `trail_active`:
   - Update `trail_peak = max(trail_peak, high)`
   - `trail_sl = trail_peak * (1 - trail_pct/100)`
   - If `low <= trail_sl` → trailing stop hit: close remaining at `trail_sl`, result=WIN

**SHORT (mirror):**
1. If `high >= sl` → SL hit: close at `sl`, result=LOSS
2. Else if not `partial_done` and `low <= tp_trig` → partial TP: close 50% at `tp_trig`, set `trail_active=True`, `trail_peak=tp_trig`, compute initial `trail_sl = tp_trig * (1 + trail_pct/100)`
3. Else if `trail_active`:
   - Update `trail_peak = min(trail_peak, low)`
   - `trail_sl = trail_peak * (1 + trail_pct/100)`
   - If `high >= trail_sl` → trailing stop hit: close remaining at `trail_sl`, result=WIN

**SL vs TP same bar:** if both SL and TP are hit in the same bar (low <= sl AND high >= tp_trig), assume SL first (conservative).

### Scale-in simulation

- `open_long/open_short` with 50% size sets `scale_in_after = sim_time + 3600_000` (1 hour in ms)
- `scale_in_long/scale_in_short` called by bot.py adds to qty, recomputes avg entry, updates `tp_trig` and `trail_sl`

### Limit orders (S5)

S5 places limit orders. MockTrader stores pending limit orders:
```python
_pending_orders: dict[str, dict]  # order_id → {symbol, side, limit_price, sl, tp, qty_str}
```
`get_order_fill(sym, order_id)` checks if any 3m bar since placement crossed `limit_price` — returns `"filled"` with fill_price if so, else `"live"`.

### Key function signatures (identical to trader.py)

```python
get_candles(symbol, interval, limit) → pd.DataFrame   # slice from parquet up to sim_time
get_mark_price(symbol) → float                         # current 3m bar close
get_usdt_balance() → float                             # simulated balance
_get_total_equity() → float                            # same as balance
get_all_open_positions() → dict[str, dict]             # in-memory positions
open_long(...) → dict                                  # record position, return fill info
open_short(...) → dict
scale_in_long(symbol, additional_trade_size_pct, leverage) → None
scale_in_short(symbol, additional_trade_size_pct, leverage) → None
refresh_plan_exits(symbol, hold_side, new_trail_trigger) → bool  # update tp_trig in memory
update_position_sl(symbol, new_sl, hold_side) → bool             # update sl in memory
cancel_all_orders(symbol) → None                                  # no-op
place_limit_long(symbol, limit_price, sl, tp, qty_str) → str     # return fake order_id
place_limit_short(symbol, limit_price, sl, tp, qty_str) → str
cancel_order(symbol, order_id) → None                            # remove from pending
get_order_fill(symbol, order_id) → dict                          # {"status", "fill_price"}
get_history_position(symbol, ...) → dict | None                  # return closed pos pnl
get_realized_pnl(symbol, ...) → float | None
is_partial_closed(symbol) → bool                                 # always False
set_leverage(symbol, leverage) → None                            # no-op
drain_partial_closes() → list[dict]                              # bot.py PAPER_MODE path
```

---

## 5. BacktestState

In-memory replacement for `state.py`. All writes go to Python dicts — no `state.json`, `paper_state.json`, or `position_memory.json` touched during backtest.

Implements only what `bot.py` actually calls:
- `reset()`, `set_status()`, `set_stats()`, `add_scan_log()`
- `update_balance()`, `update_sentiment()`, `update_qualified_pairs()`
- `add_open_trade()`, `get_open_trade()`, `get_open_trades()`, `close_trade()`
- `update_open_trade_margin()`, `update_position_memory()`, `get_position_memory()`, `clear_position_memory()`
- `load_pending_signals()` → returns `{}`
- `save_pending_signals()` → no-op

---

## 6. MockScanner

Replaces `get_qualified_pairs_and_sentiment()` from `scanner.py`.

- Returns the fixed universe list (all parquet symbols) every call
- Sentiment is **synthetic**: derived from the ratio of symbols whose 3m close is above their 1D open at sim_time
  - `> 60%` above → BULLISH
  - `< 40%` above → BEARISH
  - else → NEUTRAL
- This keeps sentiment gates (S2 scale-in needs BULLISH, S4/S6 scale-in needs BEARISH) meaningful without live API calls

---

## 7. Time Loop

```python
# Build unified 3m timeline across all symbols
all_ts = sorted(set of all 3m bar timestamps across all symbols)

for ts in all_ts:
    engine.sim_time = ts
    
    # 1. Run exit checks on all open positions for this bar
    mock_trader.process_bar(ts)   # checks SL/TP/trail/scale-in for each open pos
    
    # 2. Advance sim_time so get_candles() slices up to ts
    # 3. Run bot tick (entry signal detection, new position opening)
    bot._tick()
```

`bot._tick()` calls `get_qualified_pairs_and_sentiment()` (mocked), `get_usdt_balance()` (mocked), `get_all_open_positions()` (mocked), and `evaluate_sN()` (real) — all transparently.

The `SCAN_INTERVAL_SEC` check inside `_tick()` is bypassed: `last_scan_time` is reset to 0 before each tick so the scan always runs. `POLL_INTERVAL_SEC` sleep is never called (no `time.sleep` in the loop).

---

## 8. Balance Simulation

Starting balance: configurable via `--balance` (default: 1000 USDT).

On each trade open:
- `margin = balance * trade_size_pct`  
- `balance` is **not** reduced during a trade (margin is locked but balance stays for new trade sizing — matches how Bitget isolated margin works for concurrent trades)

On each trade close:
- `pnl = price_change * margin * leverage`
- `balance += pnl` (compound — each subsequent trade is sized off updated balance)

On partial TP:
- `partial_pnl = price_change_to_tp * (margin * 0.5) * leverage`
- `balance += partial_pnl`

---

## 9. Trade Log Output

Each closed trade recorded as a dict:

```python
{
    "strategy":     str,           # "S1"–"S6"
    "symbol":       str,
    "side":         "LONG"|"SHORT",
    "entry_date":   str,           # ISO from sim_time at open
    "exit_date":    str,           # ISO from sim_time at close
    "entry_price":  float,
    "exit_price":   float,
    "sl":           float,
    "tp_trig":      float,
    "result":       "WIN"|"LOSS",
    "exit_reason":  "SL"|"TRAIL"|"TIMEOUT",
    "partial_pnl":  float,         # PnL from the 50% partial close
    "close_pnl":    float,         # PnL from the remaining 50%
    "total_pnl":    float,         # partial_pnl + close_pnl
    "margin_pnl_pct": float,       # total_pnl / margin * 100
    "scale_in":     bool,
    "candles_held": int,           # 3m bars from open to close
}
```

HTML report reuses `build_html_report()` from existing `backtest.py` with extended stats.

---

## 10. CLI

```bash
python backtest_engine.py --days 365 --balance 1000 --symbols BTCUSDT ETHUSDT
python backtest_engine.py --days 90  --s2-only
python backtest_engine.py --days 180 --no-fetch   # skip cache update, use existing parquet
```

Flags:
- `--days` — lookback window (default 365)
- `--balance` — starting USDT balance (default 1000)
- `--symbols` — override symbol universe
- `--s1-only / --s2-only / ... / --s6-only` — run single strategy
- `--no-fetch` — skip parquet cache updates (use existing data only)
- `--output` — HTML report filename (default `backtest_engine_report.html`)

---

## 11. What Does NOT Change

- `bot.py` — zero modifications
- `trader.py` — zero modifications
- `strategy.py` — zero modifications
- `config_sN.py` — read-only, no changes
- `state.json`, `trades.csv` — never touched during backtest
- Existing `backtest.py` — kept as-is; `load_3m()` / `load_15m()` / `load_1h()` added

---

## 12. Known Simplifications vs Live

| Aspect | Live | Backtest |
|---|---|---|
| Exit price precision | Exchange order fill | 3m bar SL/TP/trail level (accurate to bar) |
| SL+TP same bar | Exchange resolves | Assume SL first (conservative) |
| Swing trail (S1/S3) | Bot-side per tick | Simulated in MockTrader per 3m bar |
| S5 limit order fill | Exchange match | 3m bar cross of limit price |
| Sentiment | Live scanner ratio | Synthetic from parquet price vs daily open |
| Slippage / fees | Real fills | Not modelled (acceptable for strategy validation) |
| Pair pause rule | 3 losses/day → pause | Implemented (tracked per symbol per UTC day) |
