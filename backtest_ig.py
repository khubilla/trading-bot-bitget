"""
backtest_ig.py — Walk-forward backtest for IG S5 and S1 strategies.

Data source: yfinance (^DJI for US30, GC=F for GOLD)
Cache: data/ig_cache/<NAME>_<INTERVAL>.parquet

Note: yfinance limits 3m candle history to ~60 days.  For S1 backtests
the 3m window is therefore short; this is expected and documented here.

Usage:
    python backtest_ig.py                        # fetch + run all instruments (both strategies)
    python backtest_ig.py --no-fetch             # use cached parquet only
    python backtest_ig.py --instrument US30      # single instrument
    python backtest_ig.py --strategy s5          # S5 only (walk 15m bars, existing behavior)
    python backtest_ig.py --strategy s1          # S1 only (walk 3m bars)
    python backtest_ig.py --strategy both        # S5 + S1, 3m walk with 15m boundary check
    python backtest_ig.py --grid s1              # S1 parameter grid search
    python backtest_ig.py --output my.html
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from itertools import product
from pathlib import Path

import pandas as pd
import pytz
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent))

from strategies.s5 import evaluate_s5
from strategies.s1 import evaluate_s1, compute_s1_sl_atr, compute_s1_tp_atr
from indicators import calculate_ema, calculate_atr
from config_ig import INSTRUMENTS

# ── Constants ──────────────────────────────────────────────────────── #

_CACHE_DIR  = Path("data/ig_cache")
_ET         = pytz.timezone("America/New_York")
_YF_SYMBOLS = {
    "US30":   "^DJI",
    "US100":  "^IXIC",  # NASDAQ Composite
    "GOLD":   "GC=F",
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
}
_YF_PERIODS   = {"1D": "10y",  "1H": "2y",  "15m": "60d", "3m": "60d"}
_YF_INTERVALS = {"1D": "1d",   "1H": "1h",  "15m": "15m", "3m": "3m"}

# ── S1 grid search parameter space ────────────────────────────────── #

S1_GRID_PARAMS = {
    "s1_sl_atr_mult":             [1.0, 1.5, 2.0, 2.5],
    "s1_tp_atr_mult":             [2.0, 3.0, 4.0, 5.0],
    "s1_consolidation_range_pct": [0.001, 0.002, 0.003, 0.005],
    "s1_breakout_buffer_pct":     [0.0002, 0.0005, 0.001],
}


# ── Data fetch ─────────────────────────────────────────────────────── #

def _cache_path(name: str, interval: str) -> Path:
    return _CACHE_DIR / f"{name}_{interval}.parquet"


def _fetch_yf(name: str, interval: str) -> pd.DataFrame:
    import yfinance as yf
    yf_sym = _YF_SYMBOLS[name]
    ticker = yf.Ticker(yf_sym)
    raw = ticker.history(
        period=_YF_PERIODS[interval],
        interval=_YF_INTERVALS[interval],
    )
    if raw is None or raw.empty:
        return pd.DataFrame()
    raw = raw.reset_index()
    ts_col = "Datetime" if "Datetime" in raw.columns else "Date"
    raw["ts"] = pd.to_datetime(raw[ts_col], utc=True).dt.as_unit("ms").astype("int64")
    raw = raw.rename(columns={"Open": "open", "High": "high",
                               "Low": "low", "Close": "close", "Volume": "vol"})
    df = raw[["ts", "open", "high", "low", "close", "vol"]].copy()
    df = df.dropna().sort_values("ts").reset_index(drop=True)
    return df


def load_candles(name: str, interval: str, no_fetch: bool = False) -> pd.DataFrame:
    """Load candles from parquet cache or fetch from yfinance."""
    path = _cache_path(name, interval)
    if no_fetch:
        if path.exists():
            return pd.read_parquet(path)
        raise FileNotFoundError(
            f"No cache at {path}. Run without --no-fetch first."
        )
    df = _fetch_yf(name, interval)
    if not df.empty:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, index=False)
    return df


# ── Session helpers ────────────────────────────────────────────────── #

def _bar_et(ts_ms: int) -> datetime:
    """Convert Unix ms timestamp to ET-aware datetime."""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).astimezone(_ET)


def _in_session(ts_ms: int, instrument: dict) -> bool:
    """True if this bar's timestamp falls within the instrument's trading window."""
    now = _bar_et(ts_ms)
    if now.weekday() >= 5:          # Saturday=5, Sunday=6
        return False
    sh, sm = instrument["session_start"]
    eh, em = instrument["session_end"]
    start = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end   = now.replace(hour=eh, minute=em, second=0, microsecond=0)
    return start <= now < end


def _is_session_end(ts_ms: int, instrument: dict) -> bool:
    """True if this bar's timestamp is at or past the session_end hour:minute."""
    now = _bar_et(ts_ms)
    if now.weekday() >= 5:
        return False
    eh, em = instrument["session_end"]
    return now.hour > eh or (now.hour == eh and now.minute >= em)


# ── Simulation: PENDING state ──────────────────────────────────────── #

def _check_pending(bar: dict, pending: dict, instrument: dict) -> tuple[str, float]:
    """
    Evaluate one 15m bar against a pending S5 signal.

    Returns (action, fill_price):
      action: "fill" | "ob_invalid" | "expired" | "session_end" | "hold"
      fill_price: trigger price if action=="fill", else 0.0
    """
    ts   = int(bar["ts"])
    lo   = float(bar["low"])
    hi   = float(bar["high"])
    buf  = instrument["s5_ob_invalidation_buffer_pct"]
    side = pending["side"]

    if _is_session_end(ts, instrument):
        return "session_end", 0.0

    if side == "LONG" and lo < pending["ob_low"] * (1 - buf):
        return "ob_invalid", 0.0
    if side == "SHORT" and hi > pending["ob_high"] * (1 + buf):
        return "ob_invalid", 0.0

    if ts > pending["expires"]:
        return "expired", 0.0

    if side == "LONG" and lo <= pending["trigger"]:
        return "fill", pending["trigger"]
    if side == "SHORT" and hi >= pending["trigger"]:
        return "fill", pending["trigger"]

    return "hold", 0.0


# ── Simulation: IN_TRADE state ─────────────────────────────────────── #

def _check_trade(bar: dict, trade: dict, instrument: dict) -> tuple[str, float]:
    """
    Evaluate one 15m bar against an open trade.

    Returns (action, price):
      action: "partial_tp" | "sl" | "tp" | "session_end" | "hold"
      price:  the level that was hit (or bar close for session_end)
    """
    ts   = int(bar["ts"])
    lo   = float(bar["low"])
    hi   = float(bar["high"])
    cl   = float(bar["close"])
    side = trade["side"]
    sl   = trade.get("sl_current", trade["sl"])

    if _is_session_end(ts, instrument):
        return "session_end", cl

    # Partial TP check (1:1 R:R) — takes priority before SL
    if not trade.get("partial_hit"):
        if side == "LONG"  and hi >= trade["tp1"]:
            return "partial_tp", trade["tp1"]
        if side == "SHORT" and lo <= trade["tp1"]:
            return "partial_tp", trade["tp1"]

    # SL check (uses sl_current which may be break-even after partial)
    if side == "LONG"  and lo <= sl:
        return "sl", sl
    if side == "SHORT" and hi >= sl:
        return "sl", sl

    # Full TP check
    if side == "LONG"  and hi >= trade["tp"]:
        return "tp", trade["tp"]
    if side == "SHORT" and lo <= trade["tp"]:
        return "tp", trade["tp"]

    return "hold", 0.0


# ── Simulation helpers ─────────────────────────────────────────────── #

def _slice_windows(i: int, df_1d: pd.DataFrame, df_1h: pd.DataFrame,
                   df_15m: pd.DataFrame, instrument: dict) -> tuple:
    """Slice daily/1H/15m DataFrames to the view available at bar i."""
    bar_ts = int(df_15m.iloc[i]["ts"])
    daily  = df_1d[df_1d["ts"] <= bar_ts].tail(instrument["daily_limit"])
    htf    = df_1h[df_1h["ts"] <= bar_ts].tail(instrument["htf_limit"])
    m15    = df_15m.iloc[max(0, i - instrument["m15_limit"] + 1): i + 1]
    return (
        daily.reset_index(drop=True),
        htf.reset_index(drop=True),
        m15.reset_index(drop=True),
    )


def _slice_windows_3m(i: int, df_1d: pd.DataFrame, df_1h: pd.DataFrame,
                      df_3m: pd.DataFrame, instrument: dict) -> tuple:
    """Slice daily/1H/3m DataFrames for an S1 evaluation at 3m bar i."""
    bar_ts = int(df_3m.iloc[i]["ts"])
    daily  = df_1d[df_1d["ts"] <= bar_ts].tail(instrument["daily_limit"])
    htf    = df_1h[df_1h["ts"] <= bar_ts].tail(instrument["htf_limit"])
    # For S1 LTF window use the same m15_limit key (it just bounds rows)
    ltf_limit = instrument.get("m15_limit", 300)
    ltf    = df_3m.iloc[max(0, i - ltf_limit + 1): i + 1]
    return (
        daily.reset_index(drop=True),
        htf.reset_index(drop=True),
        ltf.reset_index(drop=True),
    )


def _eval_s1_at_idle(
    i: int,
    bar_ts: int,
    df_1d: pd.DataFrame,
    df_1h: pd.DataFrame,
    df_3m: pd.DataFrame,
    instrument: dict,
) -> dict | None:
    """
    Evaluate S1 signal at 3m bar i.  Returns a pending dict on signal, else None.

    The instrument cfg must have s1_enabled=True; if not, returns None immediately.
    SL and TP are computed via compute_s1_sl_atr / compute_s1_tp_atr using the ATR
    stored in cfg["_last_atr"] by evaluate_s1.
    """
    if not instrument.get("s1_enabled", False):
        return None

    daily_df, htf_df, ltf_df = _slice_windows_3m(i, df_1d, df_1h, df_3m, instrument)

    if len(daily_df) < instrument.get("s1_adx_trend_threshold", 25) + 5:
        return None
    if htf_df.empty or ltf_df.empty:
        return None

    # Daily trend direction via EMA crossover (same as S5 path — reuse existing keys)
    ema_fast = float(calculate_ema(
        daily_df["close"].astype(float), instrument["s5_daily_ema_fast"]
    ).iloc[-1])
    ema_slow = float(calculate_ema(
        daily_df["close"].astype(float), instrument["s5_daily_ema_slow"]
    ).iloc[-1])
    allowed = "BULLISH" if ema_fast > ema_slow else "BEARISH"

    try:
        sig, _rsi, box_high, box_low, _adx, _drsi = evaluate_s1(
            instrument["epic"], htf_df, ltf_df, daily_df, allowed, cfg=instrument
        )
    except Exception:
        return None

    if sig not in ("LONG", "SHORT"):
        return None

    atr_val = instrument.get("_last_atr", 0.0)
    if atr_val <= 0:
        return None

    direction = sig   # "LONG" or "SHORT"
    # Entry = last closed bar close
    entry = float(df_3m.iloc[i]["close"])
    sl  = compute_s1_sl_atr(direction, entry, box_high, box_low, atr_val, instrument)
    tp  = compute_s1_tp_atr(direction, entry, atr_val, instrument)
    tp1 = tp   # S1 uses ATR-based TP directly (single leg for backtest)

    expires = bar_ts + instrument.get("pending_expiry_hours", 4) * 3_600_000

    return {
        "strategy": "S1",
        "side":     direction,
        "entry":    entry,
        "sl":       sl,
        "tp":       tp,
        "tp1":      tp1,
        "ob_low":   box_low,
        "ob_high":  box_high,
        "trigger":  entry,   # immediate market order semantics for S1
        "expires":  expires,
    }


def _eval_s5_at_idle(
    i: int,
    df_1d: pd.DataFrame,
    df_1h: pd.DataFrame,
    df_15m: pd.DataFrame,
    instrument: dict,
) -> dict | None:
    """
    Evaluate S5 signal at 15m bar i.  Returns a pending dict on signal, else None.
    Extracted from the original run_instrument IDLE block.
    """
    daily_df, htf_df, m15_df = _slice_windows(i, df_1d, df_1h, df_15m, instrument)

    if len(daily_df) < instrument["s5_daily_ema_slow"] + 5:
        return None
    if htf_df.empty or m15_df.empty:
        return None

    ema_fast = float(calculate_ema(
        daily_df["close"].astype(float), instrument["s5_daily_ema_fast"]
    ).iloc[-1])
    ema_slow = float(calculate_ema(
        daily_df["close"].astype(float), instrument["s5_daily_ema_slow"]
    ).iloc[-1])
    allowed = "BULLISH" if ema_fast > ema_slow else "BEARISH"

    try:
        sig, trigger, sl, tp, ob_low, ob_high, _ = evaluate_s5(
            instrument["epic"], daily_df, htf_df, m15_df, allowed, cfg=instrument
        )
    except Exception:
        return None

    if sig not in ("PENDING_LONG", "PENDING_SHORT"):
        return None

    bar_ts = int(df_15m.iloc[i]["ts"])
    return {
        "strategy": "S5",
        "side":     "LONG" if sig == "PENDING_LONG" else "SHORT",
        "trigger":  trigger,
        "sl":       sl,
        "tp":       tp,
        "tp1":      None,   # computed at fill time (1:1 R:R)
        "ob_low":   ob_low,
        "ob_high":  ob_high,
        "expires":  bar_ts + instrument.get("pending_expiry_hours", 4) * 3_600_000,
    }


def _calc_pnl(trade: dict) -> float:
    """PnL in points. Accounts for 2-leg structure (partial + remainder)."""
    side   = trade["side"]
    entry  = trade["entry"]
    exit_p = trade["exit_price"]
    sign   = 1 if side == "LONG" else -1

    if trade.get("partial_hit"):
        partial_pts = sign * (trade["tp1"] - entry)
        remain_pts  = sign * (exit_p - entry)
        return partial_pts * 0.5 + remain_pts * 0.5
    return sign * (exit_p - entry)


def _collect_candles(df_15m: pd.DataFrame, entry_i: int, exit_i: int,
                     before: int = 50) -> list[dict]:
    """Return ~100 15m candles centred around the trade for the chart."""
    start = max(0, entry_i - before)
    end   = min(len(df_15m), exit_i + 5)
    rows  = df_15m.iloc[start:end]
    return [
        {
            "t": int(r["ts"]),
            "o": round(float(r["open"]),  2),
            "h": round(float(r["high"]),  2),
            "l": round(float(r["low"]),   2),
            "c": round(float(r["close"]), 2),
        }
        for _, r in rows.iterrows()
    ]


# ── Simulation: full bar loop ──────────────────────────────────────── #

def run_instrument(instrument: dict,
                   df_1d: pd.DataFrame, df_1h: pd.DataFrame,
                   df_15m: pd.DataFrame,
                   df_3m: pd.DataFrame | None = None,
                   strategy_mode: str = "s5") -> dict:
    """
    Walk bars in chronological order.

    strategy_mode:
      "s5"   — walk 15m bars, evaluate S5 only (original behavior)
      "s1"   — walk 3m bars, evaluate S1 only (requires df_3m)
      "both" — walk 3m bars; at 15m boundaries also evaluate S5;
               first-non-HOLD wins; one position across both strategies

    Returns {"instrument": name, "trades": [...], "cancelled": [...]}.
    Each trade dict includes "strategy": "S1"|"S5".
    """
    name = instrument["display_name"]

    # For s5-only: keep original 15m walk
    if strategy_mode == "s5":
        return _run_s5_loop(instrument, df_1d, df_1h, df_15m, name)

    # For s1 or both: walk 3m bars
    if df_3m is None or df_3m.empty:
        return {"instrument": name, "trades": [], "cancelled": []}
    return _run_3m_loop(instrument, df_1d, df_1h, df_15m, df_3m, name, strategy_mode)


def _run_s5_loop(instrument: dict,
                 df_1d: pd.DataFrame, df_1h: pd.DataFrame,
                 df_15m: pd.DataFrame, name: str) -> dict:
    """Original 15m S5 walk loop — unchanged behavior."""
    trades:    list[dict] = []
    cancelled: list[dict] = []

    state   = "IDLE"
    pending: dict | None = None
    trade:   dict | None = None

    min_i = instrument["daily_limit"] + 10

    for i in range(min_i, len(df_15m)):
        bar = df_15m.iloc[i].to_dict()
        ts  = int(bar["ts"])

        # ── IN_TRADE ────────────────────────────────────────────── #
        if state == "IN_TRADE":
            action, price = _check_trade(bar, trade, instrument)

            if action == "partial_tp":
                trade["partial_hit"]   = True
                trade["partial_price"] = price
                trade["sl_current"]    = trade["entry"]

            elif action in ("sl", "tp", "session_end"):
                trade["exit_reason"] = action.upper()
                trade["exit_price"]  = price
                trade["exit_dt"]     = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                trade["pnl_pts"]     = _calc_pnl(trade)
                trade["pnl_pct"]     = round(trade["pnl_pts"] / trade["entry"] * 100, 3)
                trade["candles"]     = _collect_candles(df_15m, trade["entry_i"], i)
                trades.append(trade)
                state = "IDLE"
                trade = None

        # ── PENDING ─────────────────────────────────────────────── #
        elif state == "PENDING":
            action, fill_price = _check_pending(bar, pending, instrument)

            if action == "fill":
                entry = fill_price
                sl    = pending["sl"]
                tp    = pending["tp"]
                side  = pending["side"]
                tp1   = (entry + (entry - sl)) if side == "LONG" else (entry - (sl - entry))
                trade = {
                    "instrument":  name,
                    "strategy":    pending.get("strategy", "S5"),
                    "side":        side,
                    "entry_dt":    datetime.fromtimestamp(ts / 1000, tz=timezone.utc),
                    "entry_i":     i,
                    "trigger":     pending["trigger"],
                    "entry":       entry,
                    "sl":          sl,
                    "tp":          tp,
                    "tp1":         tp1,
                    "ob_low":      pending["ob_low"],
                    "ob_high":     pending["ob_high"],
                    "partial_hit": False,
                    "sl_current":  sl,
                }
                state   = "IN_TRADE"
                pending = None

            elif action in ("ob_invalid", "expired", "session_end"):
                cancelled.append({
                    "instrument": name,
                    "strategy":   pending.get("strategy", "S5"),
                    "reason":     action.upper(),
                    "dt":         datetime.fromtimestamp(ts / 1000, tz=timezone.utc),
                    "side":       pending["side"],
                })
                state   = "IDLE"
                pending = None

        # ── IDLE ────────────────────────────────────────────────── #
        else:
            if not _in_session(ts, instrument):
                continue

            sig_dict = _eval_s5_at_idle(i, df_1d, df_1h, df_15m, instrument)
            if sig_dict is not None:
                pending = sig_dict
                state   = "PENDING"

    return {"instrument": name, "trades": trades, "cancelled": cancelled}


def _run_3m_loop(instrument: dict,
                 df_1d: pd.DataFrame, df_1h: pd.DataFrame,
                 df_15m: pd.DataFrame, df_3m: pd.DataFrame,
                 name: str, strategy_mode: str) -> dict:
    """
    Walk 3m bars.  At 15m boundaries (ts % 900_000 == 0) also evaluates S5 when
    strategy_mode == "both".  First-non-HOLD signal wins.  One position per instrument.

    S1 signals are filled immediately at the close of the signal bar (market order).
    S5 signals are filled when the trigger price is touched (limit/stop order).
    Both trade types use _check_trade / _check_pending for exit management on 3m bars.
    """
    trades:    list[dict] = []
    cancelled: list[dict] = []

    state   = "IDLE"
    pending: dict | None = None
    trade:   dict | None = None

    min_i = instrument["daily_limit"] + 10   # same warm-up guard

    for i in range(min_i, len(df_3m)):
        bar = df_3m.iloc[i].to_dict()
        ts  = int(bar["ts"])

        # ── IN_TRADE ────────────────────────────────────────────── #
        if state == "IN_TRADE":
            action, price = _check_trade(bar, trade, instrument)

            if action == "partial_tp":
                trade["partial_hit"]   = True
                trade["partial_price"] = price
                trade["sl_current"]    = trade["entry"]

            elif action in ("sl", "tp", "session_end"):
                trade["exit_reason"] = action.upper()
                trade["exit_price"]  = price
                trade["exit_dt"]     = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                trade["pnl_pts"]     = _calc_pnl(trade)
                trade["pnl_pct"]     = round(trade["pnl_pts"] / trade["entry"] * 100, 3)
                trade["candles"]     = _collect_candles(df_3m, trade["entry_i"], i)
                trades.append(trade)
                state = "IDLE"
                trade = None

        # ── PENDING ─────────────────────────────────────────────── #
        elif state == "PENDING":
            if pending.get("strategy") == "S1":
                # S1: immediate fill at trigger (market order semantics)
                if ts > pending["expires"]:
                    cancelled.append({
                        "instrument": name,
                        "strategy":   "S1",
                        "reason":     "EXPIRED",
                        "dt":         datetime.fromtimestamp(ts / 1000, tz=timezone.utc),
                        "side":       pending["side"],
                    })
                    state   = "IDLE"
                    pending = None
                elif _is_session_end(ts, instrument):
                    cancelled.append({
                        "instrument": name,
                        "strategy":   "S1",
                        "reason":     "SESSION_END",
                        "dt":         datetime.fromtimestamp(ts / 1000, tz=timezone.utc),
                        "side":       pending["side"],
                    })
                    state   = "IDLE"
                    pending = None
                else:
                    # Fill immediately on next bar open (simulate as close of signal bar)
                    entry = pending["entry"]
                    sl    = pending["sl"]
                    tp    = pending["tp"]
                    tp1   = pending.get("tp1", tp)
                    side  = pending["side"]
                    trade = {
                        "instrument":  name,
                        "strategy":    "S1",
                        "side":        side,
                        "entry_dt":    datetime.fromtimestamp(ts / 1000, tz=timezone.utc),
                        "entry_i":     i,
                        "trigger":     entry,
                        "entry":       entry,
                        "sl":          sl,
                        "tp":          tp,
                        "tp1":         tp1,
                        "ob_low":      pending["ob_low"],
                        "ob_high":     pending["ob_high"],
                        "partial_hit": False,
                        "sl_current":  sl,
                    }
                    state   = "IN_TRADE"
                    pending = None
            else:
                # S5 pending in 3m loop: use same check_pending logic
                action, fill_price = _check_pending(bar, pending, instrument)

                if action == "fill":
                    entry = fill_price
                    sl    = pending["sl"]
                    tp    = pending["tp"]
                    side  = pending["side"]
                    tp1   = (entry + (entry - sl)) if side == "LONG" else (entry - (sl - entry))
                    trade = {
                        "instrument":  name,
                        "strategy":    "S5",
                        "side":        side,
                        "entry_dt":    datetime.fromtimestamp(ts / 1000, tz=timezone.utc),
                        "entry_i":     i,
                        "trigger":     pending["trigger"],
                        "entry":       entry,
                        "sl":          sl,
                        "tp":          tp,
                        "tp1":         tp1,
                        "ob_low":      pending["ob_low"],
                        "ob_high":     pending["ob_high"],
                        "partial_hit": False,
                        "sl_current":  sl,
                    }
                    state   = "IN_TRADE"
                    pending = None

                elif action in ("ob_invalid", "expired", "session_end"):
                    cancelled.append({
                        "instrument": name,
                        "strategy":   "S5",
                        "reason":     action.upper(),
                        "dt":         datetime.fromtimestamp(ts / 1000, tz=timezone.utc),
                        "side":       pending["side"],
                    })
                    state   = "IDLE"
                    pending = None

        # ── IDLE ────────────────────────────────────────────────── #
        else:
            if not _in_session(ts, instrument):
                continue

            sig_dict = None

            # At 15m boundaries, try S5 first (when mode is "both")
            if strategy_mode == "both" and ts % 900_000 == 0 and not df_15m.empty:
                # Find the matching 15m bar index
                m15_idx_mask = df_15m["ts"] == ts
                if m15_idx_mask.any():
                    m15_i = int(df_15m.index[m15_idx_mask][0])
                    sig_dict = _eval_s5_at_idle(m15_i, df_1d, df_1h, df_15m, instrument)

            # Try S1 if S5 returned nothing (or mode is s1)
            if sig_dict is None and instrument.get("s1_enabled", False):
                sig_dict = _eval_s1_at_idle(i, ts, df_1d, df_1h, df_3m, instrument)

            if sig_dict is not None:
                pending = sig_dict
                state   = "PENDING"

    return {"instrument": name, "trades": trades, "cancelled": cancelled}


# ── Stats aggregation ──────────────────────────────────────────────── #

def _stats_from_trades(trades: list[dict], cancelled: list[dict]) -> dict:
    """Build a metrics dict from a (possibly filtered) trade + cancelled list."""
    wins      = [t for t in trades if t["pnl_pts"] > 0]
    losses    = [t for t in trades if t["pnl_pts"] <= 0]
    partials  = [t for t in trades if t.get("partial_hit")]

    cancel_counts = {
        "OB_INVALID":  sum(1 for c in cancelled if c["reason"] == "OB_INVALID"),
        "EXPIRED":     sum(1 for c in cancelled if c["reason"] == "EXPIRED"),
        "SESSION_END": sum(1 for c in cancelled if c["reason"] == "SESSION_END"),
    }
    total_signals = len(trades) + len(cancelled)
    gross_win     = sum(t["pnl_pts"] for t in wins)  if wins   else 0.0
    gross_loss    = abs(sum(t["pnl_pts"] for t in losses)) if losses else 0.0

    return {
        "signals":       total_signals,
        "filled":        len(trades),
        "fill_rate":     round(len(trades) / total_signals * 100, 1) if total_signals else 0.0,
        "cancelled":     cancel_counts,
        "wins":          len(wins),
        "losses":        len(losses),
        "win_rate":      round(len(wins) / len(trades) * 100, 1) if trades else 0.0,
        "partial_rate":  round(len(partials) / len(trades) * 100, 1) if trades else 0.0,
        "avg_win_pts":   round(gross_win  / len(wins),   1) if wins   else 0.0,
        "avg_loss_pts":  round(-gross_loss / len(losses), 1) if losses else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else float("inf"),
        "total_pnl_pts": round(sum(t["pnl_pts"] for t in trades), 1),
    }


def _compute_stats(result: dict) -> dict:
    trades    = result["trades"]
    cancelled = result["cancelled"]

    overall = _stats_from_trades(trades, cancelled)

    # Per-strategy breakdown
    for strat in ("S1", "S5"):
        st_trades    = [t for t in trades    if t.get("strategy") == strat]
        st_cancelled = [c for c in cancelled if c.get("strategy") == strat]
        overall[f"{strat.lower()}_stats"] = _stats_from_trades(st_trades, st_cancelled)

    return {
        "name":          result["instrument"],
        **overall,
        "trades":        trades,
        "cancelled_list":cancelled,
    }


# ── Report builder ────────────────────────────────────────────────── #

def build_report(all_stats: list[dict], run_time: str) -> str:
    """Build a self-contained dark-theme HTML report with inline chart data."""

    def col(v):
        if isinstance(v, (int, float)):
            return "#00d68f" if v > 0 else "#ff4d6a" if v < 0 else "#8899aa"
        return "#c9d8e8"

    def card(label, val, sfx=""):
        return (f'<div class="stat"><div class="stat-label">{label}</div>'
                f'<div class="stat-val" style="color:{col(val)}">{val}{sfx}</div></div>')

    def stats_grid(s):
        return (
            f'<div class="grid">'
            f'{card("Signals",       s["signals"])}'
            f'{card("Filled",        s["filled"])}'
            f'{card("Fill Rate",     s["fill_rate"], "%")}'
            f'{card("Win Rate",      s["win_rate"],  "%")}'
            f'{card("Partial Rate",  s["partial_rate"], "%")}'
            f'{card("Total PnL",     s["total_pnl_pts"], " pts")}'
            f'{card("Avg Win",       s["avg_win_pts"],  " pts")}'
            f'{card("Avg Loss",      s["avg_loss_pts"], " pts")}'
            f'{card("Profit Factor", s["profit_factor"])}'
            f'<div class="stat"><div class="stat-label">Cancelled</div>'
            f'<div style="font-size:11px;color:#8899aa;padding-top:4px">'
            f'OB: {s["cancelled"]["OB_INVALID"]}<br>'
            f'Exp: {s["cancelled"]["EXPIRED"]}<br>'
            f'Sess: {s["cancelled"]["SESSION_END"]}'
            f'</div></div>'
            f'</div>'
        )

    def per_strategy_summary(s):
        """Render a compact per-strategy summary panel (S1 vs S5)."""
        rows = ""
        for strat, label in (("s1", "S1"), ("s5", "S5")):
            ss = s.get(f"{strat}_stats", {})
            if not ss or ss.get("filled", 0) == 0:
                continue
            sc = "#a371f7" if strat == "s1" else "#60a5fa"
            rows += (
                f'<tr>'
                f'<td><span style="color:{sc};font-weight:700">{label}</span></td>'
                f'<td>{ss["filled"]}</td>'
                f'<td style="color:{col(ss["win_rate"] - 50)}">{ss["win_rate"]}%</td>'
                f'<td style="color:{col(ss["total_pnl_pts"])}">{ss["total_pnl_pts"]:+.1f}</td>'
                f'<td>{ss["avg_win_pts"]:+.1f}</td>'
                f'<td style="color:#ff4d6a">{ss["avg_loss_pts"]:+.1f}</td>'
                f'<td>{ss["profit_factor"]}</td>'
                f'</tr>'
            )
        if not rows:
            return ""
        return (
            f'<h2 style="font-size:13px;margin:18px 0 8px">Per-Strategy Summary</h2>'
            f'<div style="overflow-x:auto;margin-bottom:16px"><table><thead><tr>'
            f'<th>Strategy</th><th>Trades</th><th>Win%</th><th>Total PnL</th>'
            f'<th>Avg Win</th><th>Avg Loss</th><th>PF</th>'
            f'</tr></thead><tbody>{rows}</tbody></table></div>'
        )

    def trade_table(trades, inst_id):
        if not trades:
            return '<p style="color:#8899aa;padding:20px">No completed trades</p>'
        rows = ""
        for idx, t in enumerate(sorted(trades, key=lambda x: x["entry_dt"], reverse=True)):
            rc  = "#00d68f" if t["pnl_pts"] > 0 else "#ff4d6a"
            pc  = col(t["pnl_pts"])
            sid = "LONG" if t["side"] == "LONG" else "SHORT"
            sc  = "#00d68f" if sid == "LONG" else "#ff4d6a"
            prt = "✓" if t.get("partial_hit") else "—"
            edt = t["exit_dt"].strftime("%Y-%m-%d %H:%M") if t.get("exit_dt") else "—"
            edt_entry = t["entry_dt"].strftime("%Y-%m-%d %H:%M") if t.get("entry_dt") else "—"
            strat     = t.get("strategy", "S5")
            strat_col = "#a371f7" if strat == "S1" else "#60a5fa"
            chart_btn = ""
            if t.get("candles"):
                cdata = json.dumps(t["candles"])
                entry_ts_ms = int(t["entry_dt"].timestamp() * 1000) if t.get("entry_dt") else 0
                exit_ts_ms  = int(t["exit_dt"].timestamp() * 1000)  if t.get("exit_dt")  else 0
                meta  = json.dumps({
                    "side":    t["side"],
                    "entry":   t["entry"],
                    "sl":      t["sl"],
                    "tp":      t["tp"],
                    "tp1":     t.get("tp1", t["tp"]),
                    "ob_low":  t.get("ob_low", 0),
                    "ob_high": t.get("ob_high", 0),
                    "exit_price":  t.get("exit_price", 0),
                    "exit_reason": t.get("exit_reason", ""),
                    "partial_hit": t.get("partial_hit", False),
                    "partial_price": t.get("partial_price", 0),
                    "entry_ts": entry_ts_ms,
                    "exit_ts":  exit_ts_ms,
                })
                chart_btn = (
                    f'<button class="chart-btn" '
                    f'onclick=\'openChart("{inst_id}",{idx},{cdata},{meta})\'>Chart</button>'
                )
            rows += (
                f'<tr>'
                f'<td>{edt_entry}</td>'
                f'<td style="color:{strat_col}">{strat}</td>'
                f'<td style="color:{sc}">{sid}</td>'
                f'<td>{t["entry"]:.1f}</td>'
                f'<td>{t["sl"]:.1f}</td>'
                f'<td>{t["tp"]:.1f}</td>'
                f'<td>{prt}</td>'
                f'<td>{t.get("exit_reason","—")}</td>'
                f'<td>{edt}</td>'
                f'<td>{t.get("exit_price",0):.1f}</td>'
                f'<td style="color:{pc}">{t["pnl_pts"]:+.1f}</td>'
                f'<td>{chart_btn}</td>'
                f'</tr>'
            )
        return (
            f'<div style="overflow-x:auto"><table><thead><tr>'
            f'<th>Entry</th><th>Strategy</th><th>Side</th><th>Entry$</th><th>SL</th><th>TP</th>'
            f'<th>Partial</th><th>Exit Reason</th><th>Exit Time</th>'
            f'<th>Exit$</th><th>PnL (pts)</th><th></th>'
            f'</tr></thead><tbody>{rows}</tbody></table></div>'
        )

    def grid_table(grid_rows: list[dict]) -> str:
        """Render a ranked combo table for grid search results."""
        if not grid_rows:
            return ""
        header = (
            f'<h2 style="font-size:13px;margin:20px 0 8px">Grid Search Results (S1 — ranked by PnL)</h2>'
            f'<div style="overflow-x:auto"><table><thead><tr>'
            f'<th>#</th><th>SL mult</th><th>TP mult</th>'
            f'<th>Consol%</th><th>BrkBuf%</th>'
            f'<th>Trades</th><th>Win%</th><th>Total PnL</th><th>Max DD</th>'
            f'</tr></thead><tbody>'
        )
        rows = ""
        for rank, r in enumerate(grid_rows, 1):
            pc = col(r["total_pnl"])
            rows += (
                f'<tr>'
                f'<td>{rank}</td>'
                f'<td>{r["s1_sl_atr_mult"]}</td>'
                f'<td>{r["s1_tp_atr_mult"]}</td>'
                f'<td>{r["s1_consolidation_range_pct"]*100:.2f}</td>'
                f'<td>{r["s1_breakout_buffer_pct"]*100:.3f}</td>'
                f'<td>{r["trade_count"]}</td>'
                f'<td>{r["win_rate"]}%</td>'
                f'<td style="color:{pc}">{r["total_pnl"]:+.1f}</td>'
                f'<td style="color:#ff4d6a">{r["max_drawdown"]:.1f}</td>'
                f'</tr>'
            )
        return header + rows + "</tbody></table></div>"

    # Build per-instrument sections
    inst_sections = ""
    tab_headers   = '<div class="tab active" onclick="sw(\'overall\')">Overall</div>'
    for s in all_stats:
        iid = s["name"].lower()
        tab_headers += f'<div class="tab" onclick="sw(\'{iid}\')">{s["name"]} ({s["filled"]})</div>'

    overall_trades = []
    for s in all_stats:
        overall_trades.extend(s["trades"])
    ovr_pnl  = round(sum(t["pnl_pts"] for t in overall_trades), 1)
    ovr_wins = sum(1 for t in overall_trades if t["pnl_pts"] > 0)
    ovr_wr   = round(ovr_wins / len(overall_trades) * 100, 1) if overall_trades else 0

    for s in all_stats:
        iid = s["name"].lower()
        inst_grid_rows = s.get("grid_rows", [])
        inst_sections += (
            f'<div id="t{iid}" class="tc">'
            f'<h2>{s["name"]}</h2>'
            f'{stats_grid(s)}'
            f'{per_strategy_summary(s)}'
            f'<h2 style="font-size:13px;margin:18px 0 8px">Trades</h2>'
            f'{trade_table(s["trades"], iid)}'
            f'{grid_table(inst_grid_rows)}'
            f'</div>'
        )

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>IG Backtest Report — {run_time}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0d1117;color:#c9d8e8;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:13px;padding:24px}}
h1{{font-size:22px;color:#e8f0f8;margin-bottom:4px}}
h2{{font-size:15px;color:#8899aa;margin:28px 0 14px;border-bottom:1px solid #1e2d3d;padding-bottom:8px}}
.meta{{color:#8899aa;font-size:12px;margin-bottom:28px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:12px;margin-bottom:20px}}
.stat{{background:#111827;border:1px solid #1e2d3d;border-radius:8px;padding:14px}}
.stat-label{{font-size:10px;color:#8899aa;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}}
.stat-val{{font-size:20px;font-weight:700}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{background:#0d1117;color:#8899aa;padding:8px 12px;text-align:left;font-size:11px;text-transform:uppercase;position:sticky;top:0}}
td{{padding:8px 12px;border-bottom:1px solid #1a2535}}
tr:hover td{{background:#1a2535}}
.tabs{{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap}}
.tab{{padding:8px 20px;border-radius:8px;cursor:pointer;border:1px solid #1e2d3d;background:#111827;color:#8899aa;font-size:13px}}
.tab.active{{background:#1e3a5f;border-color:#60a5fa;color:#60a5fa}}
.tc{{display:none}}.tc.active{{display:block}}
.chart-btn{{background:#1e2d3d;border:1px solid #334155;color:#60a5fa;padding:3px 10px;border-radius:5px;cursor:pointer;font-size:11px}}
.chart-btn:hover{{background:#1e3a5f}}
.overlay{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:100;align-items:center;justify-content:center}}
.overlay.open{{display:flex}}
.modal{{background:#111827;border:1px solid #1e2d3d;border-radius:12px;padding:20px;max-width:900px;width:95%;position:relative}}
.modal-header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}}
.modal-title{{font-size:14px;font-weight:600;color:#e8f0f8}}
.close-btn{{background:none;border:1px solid #334155;color:#8899aa;border-radius:6px;padding:4px 12px;cursor:pointer;font-size:12px}}
.close-btn:hover{{border-color:#f85149;color:#f85149}}
canvas{{display:block;width:100%;height:380px}}
</style></head><body>
<h1>IG Backtest Report</h1>
<div class="meta">Run: {run_time} | Instruments: {", ".join(s["name"] for s in all_stats)}</div>

<h2>Overall</h2>
<div class="grid">
{card("Total Trades", len(overall_trades))}
{card("Win Rate", ovr_wr, "%")}
{card("Total PnL", ovr_pnl, " pts")}
</div>

<div class="tabs">{tab_headers}</div>

<div id="toverall" class="tc active">
<p style="color:#8899aa;font-size:12px;padding:4px 0 16px">Combined across all instruments</p>
{"".join(f'<h3 style="color:#8899aa;font-size:13px;margin:16px 0 8px">{s["name"]}</h3>{stats_grid(s)}' for s in all_stats)}
</div>
{inst_sections}

<!-- Chart modal -->
<div class="overlay" id="chartOverlay" onclick="closeChart(event)">
  <div class="modal" id="chartModal" onclick="event.stopPropagation()">
    <div class="modal-header">
      <div class="modal-title" id="chartTitle">Chart</div>
      <button class="close-btn" onclick="closeChart()">Close</button>
    </div>
    <div id="chartLegend" style="font-size:11px;color:#8899aa;margin-bottom:8px;display:flex;gap:16px;flex-wrap:wrap"></div>
    <canvas id="chartCanvas"></canvas>
  </div>
</div>

<script>
function sw(t){{
  document.querySelectorAll('.tab').forEach(e=>e.classList.remove('active'));
  document.querySelectorAll('.tc').forEach(e=>e.classList.remove('active'));
  document.querySelector('.tab[onclick="sw(\\''+t+'\\')"]').classList.add('active');
  document.getElementById('t'+t).classList.add('active');
}}

function closeChart(e){{
  if(!e||e.target===document.getElementById('chartOverlay'))
    document.getElementById('chartOverlay').classList.remove('open');
}}

function openChart(instId, tradeIdx, candles, meta){{
  const side = meta.side;
  document.getElementById('chartTitle').textContent =
    instId.toUpperCase()+' · '+side+' · Entry '+meta.entry.toFixed(1);
  const items=[
    ['#3fb950','Entry '+meta.entry.toFixed(1)],
    ['#ff4d6a','SL '+meta.sl.toFixed(1)],
    ['#60a5fa','TP '+meta.tp.toFixed(1)],
    ['#a371f7','TP1 '+meta.tp1.toFixed(1)],
  ];
  if(meta.partial_hit) items.push(['#e3b341','Partial '+meta.partial_price.toFixed(1)]);
  items.push([meta.exit_reason==='TP'?'#00d68f':'#ff4d6a',
    meta.exit_reason+' '+meta.exit_price.toFixed(1)]);
  document.getElementById('chartLegend').innerHTML=items.map(([c,l])=>
    `<span style="display:flex;align-items:center;gap:4px">
      <span style="width:8px;height:8px;background:${{c}};border-radius:2px;display:inline-block"></span>
      <span>${{l}}</span></span>`).join('');
  document.getElementById('chartOverlay').classList.add('open');
  requestAnimationFrame(()=>_drawBacktestChart(
    document.getElementById('chartCanvas'), candles, meta));
}}

function _drawBacktestChart(canvas, candles, meta){{
  const dpr=window.devicePixelRatio||1;
  const W=canvas.offsetWidth||800, H=canvas.offsetHeight||380;
  canvas.width=W*dpr; canvas.height=H*dpr;
  const ctx=canvas.getContext('2d');
  ctx.scale(dpr,dpr);
  const PAD_L=10,PAD_R=10,PAD_T=20,PAD_B=20;
  const chartW=W-PAD_L-PAD_R, chartH=H-PAD_T-PAD_B;

  const levels=[meta.sl,meta.tp,meta.tp1,meta.entry,
    meta.exit_price,(meta.partial_hit?meta.partial_price:null)].filter(Boolean);
  const allPrices=[...candles.flatMap(c=>[c.h,c.l]),...levels].filter(Boolean);
  const priceMin=Math.min(...allPrices)*0.9995;
  const priceMax=Math.max(...allPrices)*1.0005;
  const priceRange=priceMax-priceMin||1;

  function yp(p){{return PAD_T+chartH*(1-(p-priceMin)/priceRange);}}
  function xp(i){{return PAD_L+i*(chartW/candles.length);}}
  const candleW=Math.max(1,chartW/candles.length-1);

  ctx.fillStyle='#0d1117';
  ctx.fillRect(0,0,W,H);

  // OB zone
  if(meta.ob_low&&meta.ob_high){{
    ctx.fillStyle='rgba(96,165,250,0.08)';
    ctx.fillRect(PAD_L,yp(meta.ob_high),chartW,yp(meta.ob_low)-yp(meta.ob_high));
  }}

  // Levels
  const lvls=[
    {{p:meta.sl,       c:'#ff4d6a', dash:[4,3]}},
    {{p:meta.tp,       c:'#60a5fa', dash:[4,3]}},
    {{p:meta.tp1,      c:'#a371f7', dash:[3,3]}},
    {{p:meta.entry,    c:'#3fb950', dash:[]}},
    {{p:meta.exit_price,c:meta.exit_reason==='TP'?'#00d68f':'#ff4d6a',dash:[2,2]}},
  ];
  if(meta.partial_hit)lvls.push({{p:meta.partial_price,c:'#e3b341',dash:[3,3]}});
  lvls.forEach(l=>{{
    if(!l.p)return;
    ctx.strokeStyle=l.c; ctx.lineWidth=1;
    ctx.setLineDash(l.dash);
    ctx.beginPath();
    ctx.moveTo(PAD_L,yp(l.p)); ctx.lineTo(W-PAD_R,yp(l.p));
    ctx.stroke();
  }});
  ctx.setLineDash([]);

  // Candles
  candles.forEach((c,i)=>{{
    const x=xp(i);
    const isGreen=c.c>=c.o;
    const bodyColor=isGreen?'#3fb950':'#f85149';
    ctx.strokeStyle=bodyColor; ctx.lineWidth=1;
    ctx.beginPath();
    ctx.moveTo(x+candleW/2,yp(c.h));
    ctx.lineTo(x+candleW/2,yp(c.l));
    ctx.stroke();
    const bTop=yp(Math.max(c.o,c.c));
    const bH=Math.max(1,Math.abs(yp(c.o)-yp(c.c)));
    ctx.fillStyle=bodyColor;
    ctx.fillRect(x,bTop,candleW,bH);
  }});

  // Entry marker (green highlight)
  const entryIdx=candles.findIndex(c=>c.t===meta.entry_ts);
  if(entryIdx>=0){{
    ctx.fillStyle='rgba(63,185,80,0.15)';
    ctx.fillRect(xp(entryIdx),PAD_T,candleW,chartH);
  }}

  // Exit marker (colored highlight)
  const exitColor=meta.exit_reason==='TP'?'rgba(0,214,143,0.15)':'rgba(255,77,106,0.15)';
  const exitIdx=candles.findIndex(c=>c.t===meta.exit_ts);
  if(exitIdx>=0){{
    ctx.fillStyle=exitColor;
    ctx.fillRect(xp(exitIdx),PAD_T,candleW,chartH);
  }}
}}
</script>
<script>
// Tab switch fix for overall
document.querySelectorAll('.tab').forEach((el,i)=>{{
  const fn=el.getAttribute('onclick');
  if(fn)el.addEventListener('click',function(){{
    document.querySelectorAll('.tab').forEach(e=>e.classList.remove('active'));
    this.classList.add('active');
  }},true);
}});
</script>
</body></html>"""


# ── CLI ────────────────────────────────────────────────────────────── #

def _run_grid_s1(instrument: dict, df_1d: pd.DataFrame, df_1h: pd.DataFrame,
                 df_15m: pd.DataFrame, df_3m: pd.DataFrame) -> list[dict]:
    """
    Run S1 backtest across S1_GRID_PARAMS cartesian product.
    Returns rows sorted by total_pnl descending.
    """
    keys   = list(S1_GRID_PARAMS.keys())
    values = list(S1_GRID_PARAMS.values())
    rows: list[dict] = []

    combos = list(product(*values))
    total  = len(combos)
    print(f"  Grid search: {total} S1 combos...")

    for idx, combo in enumerate(combos):
        cfg = dict(instrument)
        cfg["s1_enabled"] = True   # force enabled for grid
        for k, v in zip(keys, combo):
            cfg[k] = v

        result = run_instrument(cfg, df_1d, df_1h, df_15m, df_3m, strategy_mode="s1")
        trades = result["trades"]
        wins   = [t for t in trades if t["pnl_pts"] > 0]

        # Compute max drawdown (running cumulative PnL series)
        pnl_series = [t["pnl_pts"] for t in sorted(trades, key=lambda x: x["entry_dt"])]
        max_dd = 0.0
        peak   = 0.0
        cum    = 0.0
        for p in pnl_series:
            cum  += p
            peak  = max(peak, cum)
            max_dd = max(max_dd, peak - cum)

        rows.append({
            "s1_sl_atr_mult":             combo[keys.index("s1_sl_atr_mult")],
            "s1_tp_atr_mult":             combo[keys.index("s1_tp_atr_mult")],
            "s1_consolidation_range_pct": combo[keys.index("s1_consolidation_range_pct")],
            "s1_breakout_buffer_pct":     combo[keys.index("s1_breakout_buffer_pct")],
            "trade_count": len(trades),
            "win_rate":    round(len(wins) / len(trades) * 100, 1) if trades else 0.0,
            "total_pnl":   round(sum(t["pnl_pts"] for t in trades), 1),
            "max_drawdown": round(max_dd, 1),
        })

    rows.sort(key=lambda r: r["total_pnl"], reverse=True)
    return rows


def main():
    parser = argparse.ArgumentParser(description="IG walk-forward backtest (S5 + S1)")
    parser.add_argument("--no-fetch",    action="store_true",
                        help="Use cached parquet only (skip yfinance)")
    parser.add_argument("--instrument",  default=None,
                        help="Run single instrument only (e.g. US30)")
    parser.add_argument("--output",      default="backtest_ig_report.html",
                        help="Output HTML file path")
    parser.add_argument("--strategy",    default="both",
                        choices=["s5", "s1", "both"],
                        help="Strategy to backtest: s5 (15m walk, original behavior), "
                             "s1 (3m walk; yfinance limits 3m history to ~60 days), "
                             "both (3m walk, S5 at 15m boundaries + S1 every bar). "
                             "Default: both")
    parser.add_argument("--grid",        default=None,
                        choices=["s1"],
                        help="Run grid search over S1 parameters (cartesian product of "
                             "sl/tp ATR multiples, consolidation pct, breakout buffer pct). "
                             "Appends ranked table to report per instrument.")
    args = parser.parse_args()

    instruments = [
        inst for inst in INSTRUMENTS
        if args.instrument is None or inst["display_name"] == args.instrument
    ]
    if not instruments:
        print(f"No instruments matched '{args.instrument}'. Available: "
              f"{[i['display_name'] for i in INSTRUMENTS]}")
        return

    strategy_mode = args.strategy   # "s5", "s1", or "both"
    run_grid      = args.grid == "s1"

    all_stats = []
    for instrument in instruments:
        name = instrument["display_name"]
        print(f"\n[{name}] Loading candles...")
        try:
            df_1d  = load_candles(name, "1D",  no_fetch=args.no_fetch)
            df_1h  = load_candles(name, "1H",  no_fetch=args.no_fetch)
            df_15m = load_candles(name, "15m", no_fetch=args.no_fetch)
        except Exception as e:
            print(f"  Failed to load candles: {e}")
            continue

        # Load 3m candles when S1 is in scope
        df_3m: pd.DataFrame | None = None
        if strategy_mode in ("s1", "both") or run_grid:
            try:
                df_3m = load_candles(name, "3m", no_fetch=args.no_fetch)
                print(f"  3m:  {len(df_3m)} bars")
            except FileNotFoundError as e:
                print(f"  Warning: 3m cache missing ({e}). "
                      f"S1 will be skipped for {name}.")
                df_3m = None
            except Exception as e:
                print(f"  Warning: could not load 3m candles: {e}")
                df_3m = None

        print(f"  1D:  {len(df_1d)} bars")
        print(f"  1H:  {len(df_1h)} bars")
        print(f"  15m: {len(df_15m)} bars")

        if df_1d.empty or df_1h.empty or df_15m.empty:
            print(f"  Empty data — skipping {name}")
            continue

        print(f"[{name}] Running simulation (strategy={strategy_mode})...")
        result = run_instrument(
            instrument, df_1d, df_1h, df_15m,
            df_3m=df_3m, strategy_mode=strategy_mode
        )
        stats = _compute_stats(result)

        # Optionally run grid search
        if run_grid and df_3m is not None and not df_3m.empty:
            grid_rows = _run_grid_s1(instrument, df_1d, df_1h, df_15m, df_3m)
            stats["grid_rows"] = grid_rows
            print(f"  Grid search complete. Top combo: "
                  f"SL={grid_rows[0]['s1_sl_atr_mult']} "
                  f"TP={grid_rows[0]['s1_tp_atr_mult']} "
                  f"→ PnL={grid_rows[0]['total_pnl']:+.1f} pts")
        else:
            stats["grid_rows"] = []

        all_stats.append(stats)

        print(f"  Signals:    {stats['signals']}")
        print(f"  Filled:     {stats['filled']}  ({stats['fill_rate']}%)")
        print(f"  Win rate:   {stats['win_rate']}%")
        print(f"  Total PnL:  {stats['total_pnl_pts']:+.1f} pts")
        print(f"  Cancelled:  OB={stats['cancelled']['OB_INVALID']}  "
              f"Exp={stats['cancelled']['EXPIRED']}  "
              f"Sess={stats['cancelled']['SESSION_END']}")

    if not all_stats:
        print("\nNo results to report.")
        return

    run_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html     = build_report(all_stats, run_time)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nReport written -> {args.output}")


if __name__ == "__main__":
    main()
