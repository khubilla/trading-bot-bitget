"""
backtest_ig.py — Walk-forward backtest for IG S5 strategy.

Data source: yfinance (^DJI for US30, GC=F for GOLD)
Cache: data/ig_cache/<NAME>_<INTERVAL>.parquet

Usage:
    python backtest_ig.py                    # fetch + run all instruments
    python backtest_ig.py --no-fetch         # use cached parquet only
    python backtest_ig.py --instrument US30  # single instrument
    python backtest_ig.py --output my.html
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytz
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent))

from strategy import evaluate_s5, calculate_ema
from config_ig import INSTRUMENTS

# ── Constants ──────────────────────────────────────────────────────── #

_CACHE_DIR  = Path("data/ig_cache")
_ET         = pytz.timezone("America/New_York")
_YF_SYMBOLS = {
    "US30": "^DJI",
    "GOLD": "GC=F",
}
_YF_PERIODS   = {"1D": "10y",  "1H": "2y",  "15m": "60d"}
_YF_INTERVALS = {"1D": "1d",   "1H": "1h",  "15m": "15m"}


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
                   df_15m: pd.DataFrame) -> dict:
    """
    Walk every 15m bar in chronological order.
    Returns {"instrument": name, "trades": [...], "cancelled": [...]}.
    """
    name   = instrument["display_name"]
    epic   = instrument["epic"]
    trades:    list[dict] = []
    cancelled: list[dict] = []

    state   = "IDLE"
    pending: dict | None = None
    trade:   dict | None = None

    # Skip first daily_limit+10 bars so EMA calculations have enough history
    min_i = instrument["daily_limit"] + 10

    for i in range(min_i, len(df_15m)):
        bar = df_15m.iloc[i].to_dict()
        ts  = int(bar["ts"])

        # ── IN_TRADE ──────────────────────────────────────────────── #
        if state == "IN_TRADE":
            action, price = _check_trade(bar, trade, instrument)

            if action == "partial_tp":
                trade["partial_hit"]   = True
                trade["partial_price"] = price
                trade["sl_current"]    = trade["entry"]   # break-even

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

        # ── PENDING ───────────────────────────────────────────────── #
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
                    "reason":     action.upper(),
                    "dt":         datetime.fromtimestamp(ts / 1000, tz=timezone.utc),
                    "side":       pending["side"],
                })
                state   = "IDLE"
                pending = None

        # ── IDLE ──────────────────────────────────────────────────── #
        else:
            if not _in_session(ts, instrument):
                continue

            daily_df, htf_df, m15_df = _slice_windows(i, df_1d, df_1h, df_15m, instrument)

            if len(daily_df) < instrument["s5_daily_ema_slow"] + 5:
                continue
            if htf_df.empty or m15_df.empty:
                continue

            ema_fast = float(calculate_ema(
                daily_df["close"].astype(float), instrument["s5_daily_ema_fast"]
            ).iloc[-1])
            ema_slow = float(calculate_ema(
                daily_df["close"].astype(float), instrument["s5_daily_ema_slow"]
            ).iloc[-1])
            allowed = "BULLISH" if ema_fast > ema_slow else "BEARISH"

            try:
                sig, trigger, sl, tp, ob_low, ob_high, _ = evaluate_s5(
                    epic, daily_df, htf_df, m15_df, allowed, cfg=instrument
                )
            except Exception:
                continue

            if sig in ("PENDING_LONG", "PENDING_SHORT"):
                pending = {
                    "side":    "LONG" if sig == "PENDING_LONG" else "SHORT",
                    "trigger": trigger,
                    "sl":      sl,
                    "tp":      tp,
                    "ob_low":  ob_low,
                    "ob_high": ob_high,
                    "expires": ts + 4 * 3_600_000,   # 4h in ms
                }
                state = "PENDING"

    return {"instrument": name, "trades": trades, "cancelled": cancelled}


# ── Stats aggregation ──────────────────────────────────────────────── #

def _compute_stats(result: dict) -> dict:
    trades    = result["trades"]
    cancelled = result["cancelled"]
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
        "name":          result["instrument"],
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
        "trades":        trades,
        "cancelled_list":cancelled,
    }
