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


# ── Report builder ─────────────────────────────────────────────────── #

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
            chart_btn = ""
            if t.get("candles"):
                cdata = json.dumps(t["candles"])
                meta  = json.dumps({
                    "side":    t["side"],
                    "entry":   t["entry"],
                    "sl":      t["sl"],
                    "tp":      t["tp"],
                    "tp1":     t["tp1"],
                    "ob_low":  t["ob_low"],
                    "ob_high": t["ob_high"],
                    "exit_price":  t.get("exit_price", 0),
                    "exit_reason": t.get("exit_reason", ""),
                    "partial_hit": t.get("partial_hit", False),
                    "partial_price": t.get("partial_price", 0),
                })
                chart_btn = (
                    f'<button class="chart-btn" '
                    f'onclick=\'openChart("{inst_id}",{idx},{cdata},{meta})\'>Chart</button>'
                )
            rows += (
                f'<tr>'
                f'<td>{edt_entry}</td>'
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
            f'<th>Entry</th><th>Side</th><th>Entry$</th><th>SL</th><th>TP</th>'
            f'<th>Partial</th><th>Exit Reason</th><th>Exit Time</th>'
            f'<th>Exit$</th><th>PnL (pts)</th><th></th>'
            f'</tr></thead><tbody>{rows}</tbody></table></div>'
        )

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
        inst_sections += (
            f'<div id="t{iid}" class="tc">'
            f'<h2>{s["name"]}</h2>'
            f'{stats_grid(s)}'
            f'{trade_table(s["trades"], iid)}'
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
<h1>IG S5 Backtest Report</h1>
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

  // Entry marker
  const entryTs=meta.entry;
  const entryIdx=candles.findIndex(c=>Math.abs(c.c-entryTs)<entryTs*0.001);
  if(entryIdx>=0){{
    ctx.fillStyle='rgba(63,185,80,0.15)';
    ctx.fillRect(xp(entryIdx),PAD_T,candleW,chartH);
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
