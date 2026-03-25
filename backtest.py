"""
backtest.py — Walk-forward backtest for Strategy 1 (S1) and Strategy 2 (S2)

S2: 1 year of daily candles via ccxt (UTC midnight, matches TradingView)
S1: Up to 83 days of 1H + 3m candles via Bitget API

Usage:
    python backtest.py
    python backtest.py --symbols BTCUSDT ETHUSDT   # specific symbols
    python backtest.py --s1-only
    python backtest.py --s2-only
"""

import argparse
import sys
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import ccxt

sys.path.insert(0, str(Path(__file__).parent))

# Minimal config stubs so we can import strategy without full config
import types
config_mod = types.ModuleType("config")
config_mod.RSI_PERIOD             = 14
config_mod.RSI_LONG_THRESH        = 70
config_mod.RSI_SHORT_THRESH       = 30
config_mod.CONSOLIDATION_CANDLES  = 8
config_mod.CONSOLIDATION_RANGE_PCT= 0.003
config_mod.BREAKOUT_BUFFER_PCT    = 0.001
config_mod.ADX_TREND_THRESHOLD    = 25
config_mod.DAILY_EMA_SLOW         = 20
config_mod.LEVERAGE               = 30
config_mod.TRADE_SIZE_PCT         = 0.05
config_mod.TAKE_PROFIT_PCT        = 0.10
config_mod.STOP_LOSS_PCT          = 0.05
sys.modules["config"] = config_mod

config_s2_mod = types.ModuleType("config_s2")
config_s2_mod.S2_ENABLED            = True
config_s2_mod.S2_BIG_CANDLE_BODY_PCT= 0.20
config_s2_mod.S2_BIG_CANDLE_LOOKBACK= 30
config_s2_mod.S2_RSI_LONG_THRESH    = 70
config_s2_mod.S2_CONSOL_CANDLES     = 5
config_s2_mod.S2_CONSOL_RANGE_PCT   = 0.15
config_s2_mod.S2_BREAKOUT_BUFFER    = 0.001
config_s2_mod.S2_LONG_WICK_RATIO    = 2.0
config_s2_mod.S2_LEVERAGE           = 10
config_s2_mod.S2_TRADE_SIZE_PCT     = 0.05
config_s2_mod.S2_TAKE_PROFIT_PCT    = 0.10
config_s2_mod.S2_STOP_LOSS_PCT      = 0.05
sys.modules["config_s2"] = config_s2_mod

from strategy import (
    calculate_rsi, calculate_ema, calculate_adx,
    detect_consolidation, _body_pct, _upper_wick, _body_size,
)

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# ── ccxt exchange ─────────────────────────────────────────────
_ex = None
def get_exchange():
    global _ex
    if _ex is None:
        _ex = ccxt.bitget({"options": {"defaultType": "swap"}})
        _ex.load_markets()
    return _ex

def bitget_symbol(sym: str) -> str:
    base = sym.replace("USDT", "")
    return f"{base}/USDT:USDT"

def fetch_daily(sym: str, days: int = 1095) -> pd.DataFrame:
    ex = get_exchange()
    ohlcv = ex.fetch_ohlcv(bitget_symbol(sym), "1d", limit=days + 50)
    if not ohlcv:
        return pd.DataFrame()
    df = pd.DataFrame(ohlcv, columns=["ts","open","high","low","close","vol"])
    df[["open","high","low","close","vol"]] = df[["open","high","low","close","vol"]].astype(float)
    return df.sort_values("ts").reset_index(drop=True)

def fetch_ohlcv_bitget(sym: str, granularity: str, limit: int) -> pd.DataFrame:
    """Fetch intraday via Bitget API directly."""
    try:
        import bitget_client as bc
        from config import PRODUCT_TYPE
        data = bc.get_public(
            "/api/v2/mix/market/candles",
            params={"symbol": sym, "productType": PRODUCT_TYPE,
                    "granularity": granularity, "limit": str(limit)}
        )
        rows = data.get("data", [])
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=["ts","open","high","low","close","vol","quote_vol"])
        df[["open","high","low","close","vol"]] = df[["open","high","low","close","vol"]].astype(float)
        df["ts"] = df["ts"].astype(int)
        return df.sort_values("ts").reset_index(drop=True)
    except Exception:
        return pd.DataFrame()

def get_qualified_symbols(min_vol: float = 1_000_000) -> list[str]:
    """Get symbols with >$5M 24h volume."""
    ex = get_exchange()
    tickers = ex.fetch_tickers()
    qualified = []
    for sym, t in tickers.items():
        if not sym.endswith("/USDT:USDT"):
            continue
        vol = float(t.get("quoteVolume") or 0)
        if vol >= min_vol:
            qualified.append(sym.replace("/USDT:USDT", "") + "USDT")
    return sorted(qualified)


# ══════════════════════════════════════════════════════════════
#  S2 BACKTEST
# ══════════════════════════════════════════════════════════════

def backtest_s2_symbol(sym: str, df: pd.DataFrame) -> list[dict]:
    """Walk-forward S2 backtest on daily candles."""
    from config_s2 import (
        S2_BIG_CANDLE_BODY_PCT, S2_BIG_CANDLE_LOOKBACK,
        S2_RSI_LONG_THRESH, S2_CONSOL_CANDLES,
        S2_BREAKOUT_BUFFER, S2_LONG_WICK_RATIO,
        S2_TAKE_PROFIT_PCT, S2_STOP_LOSS_PCT,
        S2_LEVERAGE, S2_TRADE_SIZE_PCT,
    )
    trades = []
    closes = df["close"].astype(float)
    rsi_ser = calculate_rsi(closes)

    min_i = S2_BIG_CANDLE_LOOKBACK + S2_CONSOL_CANDLES + 16  # RSI warmup

    i = min_i
    while i < len(df) - 1:
        window_df = df.iloc[:i+1]
        rsi_window = rsi_ser.iloc[:i+1]

        daily_rsi = float(rsi_window.iloc[-1])
        if daily_rsi <= S2_RSI_LONG_THRESH:
            i += 1
            continue

        # Big candle check
        lookback = window_df.iloc[-(S2_BIG_CANDLE_LOOKBACK + 1):-1]
        big_found = any(_body_pct(row) >= S2_BIG_CANDLE_BODY_PCT
                        for _, row in lookback.iterrows())
        if not big_found:
            i += 1
            continue

        # Inside-bar consolidation
        consol_found = False
        box_high = box_low = entry_trigger = 0.0
        for n in range(1, S2_CONSOL_CANDLES + 1):
            cw = window_df.iloc[-n-1:-1]
            if len(cw) < n:
                continue
            wh = float(cw["high"].max())
            wl = float(cw["low"].min())
            mid = (wh + wl) / 2
            if mid == 0:
                continue
            # Inside-bar: all candles within mother candle
            if len(window_df) > n + 1:
                mother = window_df.iloc[-n-2]
                mh, ml = float(mother["high"]), float(mother["low"])
                if not all(float(r["high"]) <= mh * 1.02 and float(r["low"]) >= ml * 0.98
                           for _, r in cw.iterrows()):
                    continue
            else:
                if (wh - wl) / mid > 0.15:
                    continue

            rsi_ok = (rsi_window.iloc[-n-1:-1] > S2_RSI_LONG_THRESH).all()
            if not rsi_ok:
                continue

            consol_found = True
            box_high = wh
            box_low  = wl
            hc = cw.loc[cw["high"].idxmax()]
            uw   = _upper_wick(hc)
            body = _body_size(hc)
            if uw > S2_LONG_WICK_RATIO * body:
                entry_trigger = max(float(hc["close"]), float(hc["open"])) * (1 + S2_BREAKOUT_BUFFER)
            else:
                entry_trigger = float(hc["high"]) * (1 + S2_BREAKOUT_BUFFER)
            break

        if not consol_found:
            i += 1
            continue

        # Check breakout on next candle
        next_candle = df.iloc[i+1]
        entry_price = None
        if float(next_candle["open"]) > entry_trigger:
            entry_price = float(next_candle["open"])  # gap up open
        elif float(next_candle["high"]) > entry_trigger:
            entry_price = entry_trigger  # intraday breakout

        if entry_price is None:
            i += 1
            continue

        # Simulate trade outcome using subsequent daily candles
        sl_price = box_low * 0.999
        tp_price = entry_price * (1 + S2_TAKE_PROFIT_PCT)
        result = "OPEN"
        exit_price = None
        exit_i = None

        for j in range(i+2, min(i+60, len(df))):
            c = df.iloc[j]
            # Check SL first (intraday low hits SL before high hits TP is conservative)
            if float(c["low"]) <= sl_price:
                result = "LOSS"
                exit_price = sl_price
                exit_i = j
                break
            if float(c["high"]) >= tp_price:
                result = "WIN"
                exit_price = tp_price
                exit_i = j
                break

        if result == "OPEN":
            exit_price = float(df.iloc[min(i+60, len(df)-1)]["close"])
            result = "WIN" if exit_price > entry_price else "LOSS"
            exit_i = min(i+60, len(df)-1)

        pnl_pct = (exit_price - entry_price) / entry_price
        margin_pnl_pct = pnl_pct * S2_LEVERAGE

        entry_dt = datetime.fromtimestamp(int(df.iloc[i+1]["ts"]) / 1000, tz=timezone.utc)
        exit_dt  = datetime.fromtimestamp(int(df.iloc[exit_i]["ts"]) / 1000, tz=timezone.utc)

        trades.append({
            "strategy":    "S2",
            "symbol":      sym,
            "entry_date":  entry_dt.strftime("%Y-%m-%d"),
            "exit_date":   exit_dt.strftime("%Y-%m-%d"),
            "entry_price": round(entry_price, 8),
            "exit_price":  round(exit_price, 8),
            "sl":          round(sl_price, 8),
            "tp":          round(tp_price, 8),
            "result":      result,
            "pnl_pct":     round(pnl_pct * 100, 2),
            "margin_pnl":  round(margin_pnl_pct * 100, 2),
            "daily_rsi":   round(daily_rsi, 1),
        })

        # Skip to after trade closes to avoid re-entry
        i = exit_i + 1

    return trades


# ══════════════════════════════════════════════════════════════
#  S1 BACKTEST
# ══════════════════════════════════════════════════════════════

def backtest_s1_symbol(sym: str) -> list[dict]:
    """Walk-forward S1 backtest on 1H + 3m candles."""
    from config import (
        RSI_LONG_THRESH, RSI_SHORT_THRESH, CONSOLIDATION_CANDLES,
        BREAKOUT_BUFFER_PCT, ADX_TREND_THRESHOLD,
        LEVERAGE, TAKE_PROFIT_PCT,
    )

    df_1h = fetch_ohlcv_bitget(sym, "1H", 500)
    df_3m = fetch_ohlcv_bitget(sym, "3m", 1000)
    df_1d = fetch_daily(sym, 150)

    if df_1h.empty or df_3m.empty or df_1d.empty:
        return []

    trades = []
    closes_3m = df_3m["close"].astype(float)
    rsi_3m = calculate_rsi(closes_3m)

    # ADX on daily
    adx_res  = calculate_adx(df_1d)
    adx_vals = adx_res["adx"]

    min_i = config_mod.RSI_PERIOD + config_mod.CONSOLIDATION_CANDLES + 5

    i = min_i
    while i < len(df_3m) - 1:
        candle_ts = int(df_3m.iloc[i]["ts"])

        # Get latest daily ADX
        daily_slice = df_1d[df_1d["ts"] <= candle_ts]
        if len(daily_slice) < 30:
            i += 1
            continue
        adx_idx   = adx_vals.iloc[:len(daily_slice)].iloc[-1]
        if pd.isna(adx_idx) or float(adx_idx) < ADX_TREND_THRESHOLD:
            i += 1
            continue

        # 1H break check
        htf_slice = df_1h[df_1h["ts"] <= candle_ts].tail(3)
        if len(htf_slice) < 2:
            i += 1
            continue
        prev_high = float(htf_slice.iloc[-2]["high"])
        prev_low  = float(htf_slice.iloc[-2]["low"])
        cur_high  = float(htf_slice.iloc[-1]["high"])
        cur_low   = float(htf_slice.iloc[-1]["low"])
        htf_bull  = cur_high > prev_high
        htf_bear  = cur_low  < prev_low

        if not htf_bull and not htf_bear:
            i += 1
            continue

        # 3m RSI check
        rsi_val = float(rsi_3m.iloc[i])
        direction = None
        if htf_bull and rsi_val > RSI_LONG_THRESH:
            direction = "LONG"
        elif htf_bear and rsi_val < RSI_SHORT_THRESH:
            direction = "SHORT"
        else:
            i += 1
            continue

        # Consolidation
        ltf_window = df_3m.iloc[:i+1]
        rsi_window = rsi_3m.iloc[:i+1]
        is_coil, box_high, box_low = detect_consolidation(
            ltf_window, rsi_series=rsi_window,
            rsi_threshold=RSI_LONG_THRESH if direction == "LONG" else RSI_SHORT_THRESH,
            direction=direction
        )
        if not is_coil or not box_high or not box_low:
            i += 1
            continue

        entry_trigger = (box_high * (1 + BREAKOUT_BUFFER_PCT) if direction == "LONG"
                         else box_low * (1 - BREAKOUT_BUFFER_PCT))

        # Check breakout on next candle
        next_c = df_3m.iloc[i+1]
        entry_price = None
        if direction == "LONG":
            if float(next_c["open"]) > entry_trigger:
                entry_price = float(next_c["open"])
            elif float(next_c["high"]) > entry_trigger:
                entry_price = entry_trigger
        else:
            if float(next_c["open"]) < entry_trigger:
                entry_price = float(next_c["open"])
            elif float(next_c["low"]) < entry_trigger:
                entry_price = entry_trigger

        if entry_price is None:
            i += 1
            continue

        sl_price = box_low * 0.999 if direction == "LONG" else box_high * 1.001
        tp_price = (entry_price * (1 + TAKE_PROFIT_PCT) if direction == "LONG"
                    else entry_price * (1 - TAKE_PROFIT_PCT))

        result = "OPEN"
        exit_price = None
        exit_i = None

        for j in range(i+2, min(i+500, len(df_3m))):
            c = df_3m.iloc[j]
            if direction == "LONG":
                if float(c["low"])  <= sl_price:
                    result = "LOSS"; exit_price = sl_price; exit_i = j; break
                if float(c["high"]) >= tp_price:
                    result = "WIN";  exit_price = tp_price; exit_i = j; break
            else:
                if float(c["high"]) >= sl_price:
                    result = "LOSS"; exit_price = sl_price; exit_i = j; break
                if float(c["low"])  <= tp_price:
                    result = "WIN";  exit_price = tp_price; exit_i = j; break

        if result == "OPEN":
            exit_price = float(df_3m.iloc[min(i+500, len(df_3m)-1)]["close"])
            result = "WIN" if (direction == "LONG" and exit_price > entry_price) or \
                              (direction == "SHORT" and exit_price < entry_price) else "LOSS"
            exit_i = min(i+500, len(df_3m)-1)

        pnl_pct       = (exit_price - entry_price) / entry_price
        if direction == "SHORT":
            pnl_pct = -pnl_pct
        margin_pnl_pct = pnl_pct * LEVERAGE

        entry_dt = datetime.fromtimestamp(int(df_3m.iloc[i+1]["ts"]) / 1000, tz=timezone.utc)
        exit_dt  = datetime.fromtimestamp(int(df_3m.iloc[exit_i]["ts"]) / 1000, tz=timezone.utc)

        trades.append({
            "strategy":    "S1",
            "symbol":      sym,
            "direction":   direction,
            "entry_date":  entry_dt.strftime("%Y-%m-%d %H:%M"),
            "exit_date":   exit_dt.strftime("%Y-%m-%d %H:%M"),
            "entry_price": round(entry_price, 8),
            "exit_price":  round(exit_price, 8),
            "sl":          round(sl_price, 8),
            "tp":          round(tp_price, 8),
            "result":      result,
            "pnl_pct":     round(pnl_pct * 100, 2),
            "margin_pnl":  round(margin_pnl_pct * 100, 2),
            "rsi_entry":   round(rsi_val, 1),
        })

        i = exit_i + 1

    return trades


# ══════════════════════════════════════════════════════════════
#  HTML REPORT
# ══════════════════════════════════════════════════════════════

def build_html_report(all_trades: list[dict], run_time: str) -> str:
    df = pd.DataFrame(all_trades) if all_trades else pd.DataFrame()

    def stats(trades_list):
        if not trades_list:
            return {"count": 0, "wins": 0, "losses": 0, "win_rate": 0,
                    "total_margin_pnl": 0, "avg_win": 0, "avg_loss": 0, "best": 0, "worst": 0}
        t = pd.DataFrame(trades_list)
        wins   = t[t["result"] == "WIN"]
        losses = t[t["result"] == "LOSS"]
        return {
            "count":            len(t),
            "wins":             len(wins),
            "losses":           len(losses),
            "win_rate":         round(len(wins) / len(t) * 100, 1) if len(t) else 0,
            "total_margin_pnl": round(t["margin_pnl"].sum(), 1),
            "avg_win":          round(wins["margin_pnl"].mean(), 1) if len(wins) else 0,
            "avg_loss":         round(losses["margin_pnl"].mean(), 1) if len(losses) else 0,
            "best":             round(t["margin_pnl"].max(), 1) if len(t) else 0,
            "worst":            round(t["margin_pnl"].min(), 1) if len(t) else 0,
        }

    s1_trades = [t for t in all_trades if t["strategy"] == "S1"]
    s2_trades = [t for t in all_trades if t["strategy"] == "S2"]
    s1 = stats(s1_trades)
    s2 = stats(s2_trades)
    overall = stats(all_trades)

    def color(val):
        if val > 0: return "#00d68f"
        if val < 0: return "#ff4d6a"
        return "#8899aa"

    def stat_card(label, val, suffix=""):
        c = color(val) if isinstance(val, (int, float)) else "#c9d8e8"
        return f'<div class="stat"><div class="stat-label">{label}</div><div class="stat-val" style="color:{c}">{val}{suffix}</div></div>'

    def trades_table(trades_list, strat):
        if not trades_list:
            return f'<p style="color:#8899aa;padding:20px">No {strat} trades found</p>'
        rows = ""
        for t in sorted(trades_list, key=lambda x: x["entry_date"], reverse=True):
            rc = "#00d68f" if t["result"] == "WIN" else "#ff4d6a"
            pc = color(t["margin_pnl"])
            dir_badge = f'<span style="background:{"#00d68f22" if t.get("direction","LONG")=="LONG" else "#ff4d6a22"};color:{"#00d68f" if t.get("direction","LONG")=="LONG" else "#ff4d6a"};padding:2px 6px;border-radius:4px;font-size:11px">{t.get("direction","LONG")}</span>' if strat == "S1" else ""
            rows += f"""<tr>
                <td>{t["symbol"].replace("USDT","")}</td>
                <td>{dir_badge}</td>
                <td>{t["entry_date"]}</td>
                <td>{t["exit_date"]}</td>
                <td>{t["entry_price"]}</td>
                <td>{t["exit_price"]}</td>
                <td style="color:{rc};font-weight:600">{t["result"]}</td>
                <td style="color:{pc}">{t["margin_pnl"]:+.1f}%</td>
            </tr>"""
        return f"""<table><thead><tr>
            <th>Symbol</th><th>Dir</th><th>Entry</th><th>Exit</th>
            <th>Entry $</th><th>Exit $</th><th>Result</th><th>Margin PnL</th>
        </tr></thead><tbody>{rows}</tbody></table>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Backtest Report — {run_time}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #0d1117; color: #c9d8e8; font-family: -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; font-size: 13px; padding: 24px; }}
  h1 {{ font-size: 22px; color: #e8f0f8; margin-bottom: 4px; }}
  h2 {{ font-size: 15px; color: #8899aa; margin: 32px 0 16px; border-bottom: 1px solid #1e2d3d; padding-bottom: 8px; }}
  h3 {{ font-size: 13px; color: #60a5fa; margin: 20px 0 10px; }}
  .meta {{ color: #8899aa; font-size: 12px; margin-bottom: 32px; }}
  .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 12px; margin-bottom: 24px; }}
  .stat {{ background: #111827; border: 1px solid #1e2d3d; border-radius: 8px; padding: 14px; }}
  .stat-label {{ font-size: 10px; color: #8899aa; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px; }}
  .stat-val {{ font-size: 20px; font-weight: 700; }}
  .section {{ background: #111827; border: 1px solid #1e2d3d; border-radius: 12px; padding: 20px; margin-bottom: 24px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  th {{ background: #0d1117; color: #8899aa; padding: 8px 12px; text-align: left; font-weight: 500; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; position: sticky; top: 0; }}
  td {{ padding: 8px 12px; border-bottom: 1px solid #1a2535; }}
  tr:hover td {{ background: #1a2535; }}
  .tabs {{ display: flex; gap: 8px; margin-bottom: 16px; }}
  .tab {{ padding: 8px 20px; border-radius: 8px; cursor: pointer; border: 1px solid #1e2d3d; background: #111827; color: #8899aa; font-size: 13px; }}
  .tab.active {{ background: #1e3a5f; border-color: #60a5fa; color: #60a5fa; }}
  .tab-content {{ display: none; }}
  .tab-content.active {{ display: block; }}
  .badge {{ display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600; }}
  .win {{ background:#00d68f22;color:#00d68f; }}
  .loss {{ background:#ff4d6a22;color:#ff4d6a; }}
  .overflow {{ overflow-x: auto; }}
</style>
</head>
<body>
<h1>📊 Backtest Report</h1>
<div class="meta">Run: {run_time} &nbsp;|&nbsp; S1: {s1["count"]} trades &nbsp;|&nbsp; S2: {s2["count"]} trades</div>

<h2>Overall Performance</h2>
<div class="stats-grid">
  {stat_card("Total Trades", overall["count"])}
  {stat_card("Win Rate", overall["win_rate"], "%")}
  {stat_card("Total Margin PnL", overall["total_margin_pnl"], "%")}
  {stat_card("Avg Win", overall["avg_win"], "%")}
  {stat_card("Avg Loss", overall["avg_loss"], "%")}
  {stat_card("Best Trade", overall["best"], "%")}
  {stat_card("Worst Trade", overall["worst"], "%")}
</div>

<div class="tabs">
  <div class="tab active" onclick="switchTab('s1')">S1 — MTF RSI Breakout ({s1["count"]} trades)</div>
  <div class="tab" onclick="switchTab('s2')">S2 — Daily Coil Breakout ({s2["count"]} trades)</div>
</div>

<div id="tab-s1" class="tab-content active">
  <div class="stats-grid">
    {stat_card("Trades", s1["count"])}
    {stat_card("Win Rate", s1["win_rate"], "%")}
    {stat_card("Total Margin PnL", s1["total_margin_pnl"], "%")}
    {stat_card("Avg Win", s1["avg_win"], "%")}
    {stat_card("Avg Loss", s1["avg_loss"], "%")}
    {stat_card("Best", s1["best"], "%")}
    {stat_card("Worst", s1["worst"], "%")}
  </div>
  <div class="overflow">{trades_table(s1_trades, "S1")}</div>
</div>

<div id="tab-s2" class="tab-content">
  <div class="stats-grid">
    {stat_card("Trades", s2["count"])}
    {stat_card("Win Rate", s2["win_rate"], "%")}
    {stat_card("Total Margin PnL", s2["total_margin_pnl"], "%")}
    {stat_card("Avg Win", s2["avg_win"], "%")}
    {stat_card("Avg Loss", s2["avg_loss"], "%")}
    {stat_card("Best", s2["best"], "%")}
    {stat_card("Worst", s2["worst"], "%")}
  </div>
  <div class="overflow">{trades_table(s2_trades, "S2")}</div>
</div>

<script>
function switchTab(t) {{
  document.querySelectorAll('.tab').forEach((el,i) => el.classList.toggle('active', ['s1','s2'][i]===t));
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-'+t).classList.add('active');
}}
</script>
</body>
</html>"""
    return html


# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="*", help="Specific symbols to test")
    parser.add_argument("--s1-only", action="store_true")
    parser.add_argument("--s2-only", action="store_true")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of symbols")
    parser.add_argument("--output", default="backtest_report.html")
    args = parser.parse_args()

    run_s1 = not args.s2_only
    run_s2 = not args.s1_only

    print("🔍 Loading qualified symbols...")
    if args.symbols:
        symbols = args.symbols
    else:
        symbols = get_qualified_symbols()
        if args.limit:
            symbols = symbols[:args.limit]
    print(f"   {len(symbols)} symbols to test")

    all_trades = []
    run_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    for idx, sym in enumerate(symbols):
        print(f"[{idx+1}/{len(symbols)}] {sym}", end="  ", flush=True)

        # ── S2 ──
        if run_s2:
            try:
                df_daily = fetch_daily(sym, days=380)
                if not df_daily.empty and len(df_daily) >= 50:
                    trades = backtest_s2_symbol(sym, df_daily)
                    all_trades.extend(trades)
                    print(f"S2:{len(trades)}", end="  ", flush=True)
                else:
                    print("S2:skip", end="  ", flush=True)
            except Exception as e:
                print(f"S2:err({e})", end="  ", flush=True)
            time.sleep(0.3)

        # ── S1 ──
        if run_s1:
            try:
                trades = backtest_s1_symbol(sym)
                all_trades.extend(trades)
                print(f"S1:{len(trades)}", end="  ", flush=True)
            except Exception as e:
                print(f"S1:err({e})", end="  ", flush=True)
            time.sleep(0.3)

        print()

    print(f"\n✅ Total trades: {len(all_trades)}")
    wins = sum(1 for t in all_trades if t["result"] == "WIN")
    if all_trades:
        print(f"   Win rate: {wins/len(all_trades)*100:.1f}%")
        print(f"   Total margin PnL: {sum(t['margin_pnl'] for t in all_trades):+.1f}%")

    print(f"\n📄 Writing report to {args.output}...")
    html = build_html_report(all_trades, run_time)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ Done → {args.output}")


if __name__ == "__main__":
    main()
