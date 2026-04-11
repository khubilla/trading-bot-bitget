
"""
backtest.py — Walk-forward backtest for Strategy 1 (S1), Strategy 2 (S2), and Strategy 3 (S3)

FIXES APPLIED:
  B1. fetch_daily() bypasses ccxt entirely — uses Bitget REST API directly
  B2. Pagination uses startTime-forward approach (endTime alone = always 90 candles)
  B3. main() uses --days (default 1095) instead of hardcoded 380
  B4. Inside-bar tolerance relaxed from 2% to 5%, majority rule 70%

Bitget candle API behaviour (confirmed by testing):
  - endTime alone     → always returns same 90 most-recent candles (BROKEN for pagination)
  - startTime alone   → returns up to limit candles AFTER that time ✅
  - startTime+endTime → HTTP 400 error

Usage:
    python backtest.py --s2-only
    python backtest.py --s2-only --debug --symbols BTCUSDT ETHUSDT
    python backtest.py --s2-only --days 365
    python backtest.py --s1-only
    python backtest.py --s3-only
"""

import argparse
import sys
import time
import logging
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import ccxt

sys.path.insert(0, str(Path(__file__).parent))

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
config_mod.PRODUCT_TYPE           = "usdt-futures"
sys.modules["config"] = config_mod

config_s2_mod = types.ModuleType("config_s2")
config_s2_mod.S2_ENABLED            = True
config_s2_mod.S2_BIG_CANDLE_BODY_PCT= 0.20
config_s2_mod.S2_BIG_CANDLE_LOOKBACK= 30
config_s2_mod.S2_RSI_LONG_THRESH    = 70
config_s2_mod.S2_CONSOL_CANDLES     = 5
config_s2_mod.S2_CONSOL_RANGE_PCT   = 0.15
config_s2_mod.S2_BREAKOUT_BUFFER    = 0.1
config_s2_mod.S2_LONG_WICK_RATIO    = 2.0
config_s2_mod.S2_LEVERAGE           = 10
config_s2_mod.S2_TRADE_SIZE_PCT     = 0.05
config_s2_mod.S2_TAKE_PROFIT_PCT    = 0.10
config_s2_mod.S2_STOP_LOSS_PCT      = 0.05
sys.modules["config_s2"] = config_s2_mod

config_s3_mod = types.ModuleType("config_s3")
config_s3_mod.S3_ENABLED            = True
config_s3_mod.S3_EMA_FAST           = 10
config_s3_mod.S3_EMA_MED            = 20
config_s3_mod.S3_EMA_SLOW           = 50
config_s3_mod.S3_EMA_TREND          = 200
config_s3_mod.S3_ADX_MIN            = 30
config_s3_mod.S3_STOCH_K_PERIOD     = 5
config_s3_mod.S3_STOCH_D_SMOOTH     = 3
config_s3_mod.S3_STOCH_OVERSOLD     = 30
config_s3_mod.S3_STOCH_LOOKBACK     = 8
config_s3_mod.S3_MACD_FAST          = 12
config_s3_mod.S3_MACD_SLOW          = 26
config_s3_mod.S3_MACD_SIGNAL        = 9
config_s3_mod.S3_LTF_INTERVAL       = "15m"
config_s3_mod.S3_ENTRY_BUFFER_PCT   = 0.001
config_s3_mod.S3_LEVERAGE           = 10
config_s3_mod.S3_TRADE_SIZE_PCT     = 0.25
config_s3_mod.S3_SL_BUFFER_PCT      = 0.002
config_s3_mod.S3_MIN_RR               = 2.0
config_s3_mod.S3_TRAILING_TRIGGER_PCT = 0.10
config_s3_mod.S3_TRAILING_RANGE_PCT   = 10
sys.modules["config_s3"] = config_s3_mod

from strategy import (
    calculate_rsi, calculate_ema, calculate_adx,
    detect_consolidation, _body_pct, _upper_wick, _body_size,
    calculate_stoch, calculate_macd,
)

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

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
    """
    Fetch daily candles via Bitget REST API directly.
    Pages forward using startTime only (Bitget returns 90 candles per call max).
    """
    BASE     = "https://api.bitget.com"
    ENDPOINT = "/api/v2/mix/market/candles"
    GRAN     = "1Dutc"
    DAY_MS   = 86_400_000

    now_ms    = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms  = now_ms - (days + 5) * DAY_MS
    cursor_ms = start_ms
    all_rows  = []
    batch_num = 0

    while cursor_ms < now_ms:
        params = {
            "symbol":      sym,
            "productType": "usdt-futures",
            "granularity": GRAN,
            "startTime":   str(cursor_ms),
            "limit":       "200",
        }
        try:
            resp = requests.get(BASE + ENDPOINT, params=params, timeout=15)
            data = resp.json()
        except Exception as e:
            print(f"  ❌ [{sym}] batch {batch_num} error: {e}")
            break

        rows_raw = data.get("data") or []
        first_dt = datetime.fromtimestamp(cursor_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        print(f"  📦 batch {batch_num} | cursor={first_dt} | "
              f"HTTP {resp.status_code} | code={data.get('code')} | rows={len(rows_raw)}")

        if data.get("code") != "00000":
            print(f"  ⚠️  API error: {data.get('msg')}")
            break

        if not rows_raw:
            print(f"  ⛔ no rows returned — end of history")
            break

        parsed = []
        for r in rows_raw:
            try:
                parsed.append([int(r[0]), float(r[1]), float(r[2]),
                                float(r[3]), float(r[4]), float(r[5])])
            except (IndexError, ValueError):
                continue

        if not parsed:
            break

        c_first = datetime.fromtimestamp(parsed[0][0]  / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        c_last  = datetime.fromtimestamp(parsed[-1][0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        print(f"      candles {c_first} → {c_last}  (running total: {len(all_rows) + len(parsed)})")

        all_rows.extend(parsed)
        batch_num += 1

        newest_ts = max(r[0] for r in parsed)
        cursor_ms = newest_ts + DAY_MS

        if cursor_ms >= now_ms:
            print(f"  ✅ reached today — done after {batch_num} batches")
            break

        time.sleep(0.2)

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows, columns=["ts", "open", "high", "low", "close", "vol"])
    df = df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    df = df[df["ts"] >= start_ms].reset_index(drop=True)
    return df


_DAILY_CACHE = Path("data/daily")


def load_daily(sym: str, days: int = 1095) -> pd.DataFrame:
    """
    Load daily candles for sym, updating the parquet cache incrementally.

    - If data/daily/<sym>.parquet exists: load it, fetch only candles newer
      than the last cached ts, append, save back.
    - If not: full fetch via fetch_daily() and save.
    - Returns the cached+updated DataFrame trimmed to `days` days of history.
    """
    _DAILY_CACHE.mkdir(parents=True, exist_ok=True)
    path = _DAILY_CACHE / f"{sym}.parquet"
    DAY_MS = 86_400_000

    if path.exists():
        cached = pd.read_parquet(path)
        # validate structure
        if set(["ts", "open", "high", "low", "close", "vol"]).issubset(cached.columns) and len(cached):
            last_ts = int(cached["ts"].max())
            cursor_ms = last_ts + DAY_MS
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

            if cursor_ms >= now_ms:
                print(f"  📂 cache up-to-date ({sym})")
            else:
                from_dt = datetime.fromtimestamp(cursor_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                print(f"  📂 cache hit — fetching from {from_dt} ({sym})")
                # reuse fetch_daily's pagination but starting from cursor
                new_rows = []
                batch_num = 0
                BASE     = "https://api.bitget.com"
                ENDPOINT = "/api/v2/mix/market/candles"
                while cursor_ms < now_ms:
                    params = {
                        "symbol":      sym,
                        "productType": "usdt-futures",
                        "granularity": "1Dutc",
                        "startTime":   str(cursor_ms),
                        "limit":       "200",
                    }
                    try:
                        resp = requests.get(BASE + ENDPOINT, params=params, timeout=15)
                        data = resp.json()
                    except Exception as e:
                        print(f"  ❌ [{sym}] incremental batch {batch_num} error: {e}")
                        break
                    rows_raw = data.get("data") or []
                    if data.get("code") != "00000" or not rows_raw:
                        break
                    parsed = []
                    for r in rows_raw:
                        try:
                            parsed.append([int(r[0]), float(r[1]), float(r[2]),
                                           float(r[3]), float(r[4]), float(r[5])])
                        except (IndexError, ValueError):
                            continue
                    if not parsed:
                        break
                    new_rows.extend(parsed)
                    batch_num += 1
                    newest_ts = max(r[0] for r in parsed)
                    cursor_ms = newest_ts + DAY_MS
                    if cursor_ms >= now_ms:
                        break
                    time.sleep(0.2)

                if new_rows:
                    new_df = pd.DataFrame(new_rows, columns=["ts", "open", "high", "low", "close", "vol"])
                    cached = pd.concat([cached, new_df], ignore_index=True)
                    cached = cached.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
                    cached.to_parquet(path, index=False)
                    print(f"  💾 saved {len(new_rows)} new candles → {path.name}")

            # trim to requested days window
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            cutoff = now_ms - (days + 5) * DAY_MS
            return cached[cached["ts"] >= cutoff].reset_index(drop=True)
        else:
            print(f"  ⚠️  corrupt cache for {sym}, re-fetching")

    # no cache or corrupt — full fetch + save
    print(f"  🌐 no cache — full fetch ({sym})")
    df = fetch_daily(sym, days=days)
    if not df.empty:
        df.to_parquet(path, index=False)
        print(f"  💾 saved {len(df)} candles → {path.name}")
    return df


def fetch_m15(sym: str, days: int = 90) -> pd.DataFrame:
    """
    Fetch 15m candles via Bitget REST API with forward pagination.
    15m candles: 96 per day. 200 per request max.
    """
    BASE     = "https://api.bitget.com"
    ENDPOINT = "/api/v2/mix/market/candles"
    GRAN     = "15m"
    MIN_MS   = 15 * 60 * 1000

    now_ms    = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms  = now_ms - days * 86_400_000
    cursor_ms = start_ms
    all_rows  = []
    batch_num = 0

    while cursor_ms < now_ms:
        params = {
            "symbol":      sym,
            "productType": "usdt-futures",
            "granularity": GRAN,
            "startTime":   str(cursor_ms),
            "limit":       "200",
        }
        try:
            resp = requests.get(BASE + ENDPOINT, params=params, timeout=15)
            data = resp.json()
        except Exception as e:
            print(f"  ❌ [{sym}] 15m batch {batch_num} error: {e}")
            break

        if data.get("code") != "00000":
            break

        rows_raw = data.get("data") or []
        if not rows_raw:
            break

        parsed = []
        for r in rows_raw:
            try:
                parsed.append([int(r[0]), float(r[1]), float(r[2]),
                                float(r[3]), float(r[4]), float(r[5])])
            except (IndexError, ValueError):
                continue

        if not parsed:
            break

        all_rows.extend(parsed)
        batch_num += 1

        newest_ts = max(r[0] for r in parsed)
        cursor_ms = newest_ts + MIN_MS

        if cursor_ms >= now_ms:
            break

        time.sleep(0.1)

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows, columns=["ts", "open", "high", "low", "close", "vol"])
    df = df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    return df


def fetch_ohlcv_bitget(sym: str, granularity: str, limit: int) -> pd.DataFrame:
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


def diagnose_symbols(symbols: list[str]):
    ex = get_exchange()
    print("\n🔬 Symbol Diagnosis:")
    print(f"   {'Symbol':<16} {'ccxt name':<22} {'In markets':<12} {'Candles':<10} {'From':<12} {'To'}")
    print("   " + "─" * 85)
    for sym in symbols:
        ccxt_sym   = bitget_symbol(sym)
        in_markets = ccxt_sym in ex.markets
        try:
            df = fetch_daily(sym, days=1095)
            if df.empty:
                print(f"   {sym:<16} {ccxt_sym:<22} {'✓' if in_markets else '✗':<12} EMPTY")
            else:
                fd = datetime.fromtimestamp(df["ts"].iloc[0]  / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                ld = datetime.fromtimestamp(df["ts"].iloc[-1] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                print(f"   {sym:<16} {ccxt_sym:<22} {'✓' if in_markets else '✗':<12} {len(df):<10} {fd:<12} {ld}")
        except Exception as e:
            print(f"   {sym:<16} {ccxt_sym:<22} {'✓' if in_markets else '✗':<12} ERROR: {e}")
        time.sleep(0.3)
    print()


def get_qualified_symbols(min_vol: float = 1_000_000) -> list[str]:
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


def _inside_bar_check(window_df: pd.DataFrame, mother_row: pd.Series,
                      tolerance: float = 0.05,
                      majority_pct: float = 0.70) -> bool:
    mh = float(mother_row["high"])
    ml = float(mother_row["low"])
    upper = mh * (1 + tolerance)
    lower = ml * (1 - tolerance)
    inside = sum(
        1 for _, row in window_df.iterrows()
        if float(row["high"]) <= upper and float(row["low"]) >= lower
    )
    return inside >= len(window_df) * majority_pct


def backtest_s2_symbol(sym: str, df: pd.DataFrame,
                       ib_tolerance: float = 0.05,
                       ib_majority: float = 0.70,
                       debug: bool = False) -> list[dict]:
    from config_s2 import (
        S2_BIG_CANDLE_BODY_PCT, S2_BIG_CANDLE_LOOKBACK,
        S2_RSI_LONG_THRESH, S2_CONSOL_CANDLES,
        S2_BREAKOUT_BUFFER, S2_LONG_WICK_RATIO,
        S2_TAKE_PROFIT_PCT, S2_LEVERAGE, S2_STOP_LOSS_PCT
    )
    trades = []
    closes  = df["close"].astype(float)
    rsi_ser = calculate_rsi(closes)
    min_i   = S2_BIG_CANDLE_LOOKBACK + S2_CONSOL_CANDLES + 16

    dbg = dict(total=len(df), rsi_pass=0, rsi_fail=0,
               big_pass=0, big_fail=0,
               con_pass=0, con_ib=0, con_rsi=0,
               brk_pass=0, brk_fail=0)

    i = min_i
    while i < len(df) - 1:
        window    = df.iloc[:i+1]
        rsi_win   = rsi_ser.iloc[:i+1]
        daily_rsi = float(rsi_win.iloc[-1])

        if daily_rsi <= S2_RSI_LONG_THRESH:
            dbg["rsi_fail"] += 1; i += 1; continue
        dbg["rsi_pass"] += 1

        lookback  = window.iloc[-(S2_BIG_CANDLE_LOOKBACK + 1):-1]
        best_body = max((_body_pct(r) for _, r in lookback.iterrows()), default=0)
        if best_body < S2_BIG_CANDLE_BODY_PCT:
            dbg["big_fail"] += 1; i += 1; continue
        dbg["big_pass"] += 1

        consol_found  = False
        box_high = box_low = entry_trigger = 0.0

        for n in range(1, S2_CONSOL_CANDLES + 1):
            cw = window.iloc[-n - 1:-1]
            if len(cw) < n:
                continue
            wh  = float(cw["high"].max())
            wl  = float(cw["low"].min())
            mid = (wh + wl) / 2
            if mid == 0:
                continue
            if len(window) > n + 1:
                mother = window.iloc[-n - 2]
                if not _inside_bar_check(cw, mother, ib_tolerance, ib_majority):
                    dbg["con_ib"] += 1; continue
            else:
                if (wh - wl) / mid > 0.15:
                    dbg["con_ib"] += 1; continue
            rsi_slice = rsi_win.iloc[-n - 1:-1]
            if not (rsi_slice > S2_RSI_LONG_THRESH).all():
                dbg["con_rsi"] += 1; continue
            consol_found = True
            box_high = wh
            box_low  = wl
            hc   = cw.loc[cw["high"].idxmax()]
            uw   = _upper_wick(hc)
            body = _body_size(hc)
            if uw > S2_LONG_WICK_RATIO * body:
                entry_trigger = max(float(hc["close"]), float(hc["open"])) * (1 + S2_BREAKOUT_BUFFER)
            else:
                entry_trigger = float(hc["high"]) * (1 + S2_BREAKOUT_BUFFER)
            break

        if not consol_found:
            i += 1; continue
        dbg["con_pass"] += 1

        nxt = df.iloc[i + 1]
        entry_price = None
        if float(nxt["open"]) > entry_trigger:
            entry_price = float(nxt["open"])
        elif float(nxt["high"]) > entry_trigger:
            entry_price = entry_trigger

        if entry_price is None:
            dbg["brk_fail"] += 1; i += 1; continue
        dbg["brk_pass"] += 1

        sl_price = entry_price * (1 - S2_STOP_LOSS_PCT) 
        tp_price = entry_price * (1 + S2_TAKE_PROFIT_PCT)
        result   = "OPEN"
        exit_price = exit_i = None

        for j in range(i + 2, min(i + 60, len(df))):
            c = df.iloc[j]
            if float(c["low"]) <= sl_price:
                result = "LOSS"; exit_price = sl_price; exit_i = j; break
            if float(c["high"]) >= tp_price:
                result = "WIN";  exit_price = tp_price; exit_i = j; break

        if result == "OPEN":
            exit_i     = min(i + 60, len(df) - 1)
            exit_price = float(df.iloc[exit_i]["close"])
            result     = "WIN" if exit_price > entry_price else "LOSS"

        pnl_pct = (exit_price - entry_price) / entry_price
        entry_dt = datetime.fromtimestamp(int(df.iloc[i+1]["ts"]) / 1000, tz=timezone.utc)
        exit_dt  = datetime.fromtimestamp(int(df.iloc[exit_i]["ts"]) / 1000, tz=timezone.utc)

        trades.append({
            "strategy":     "S2",
            "symbol":       sym,
            "entry_date":   entry_dt.strftime("%Y-%m-%d"),
            "exit_date":    exit_dt.strftime("%Y-%m-%d"),
            "entry_price":  round(entry_price, 8),
            "exit_price":   round(exit_price,  8),
            "sl":           round(sl_price, 8),
            "tp":           round(tp_price, 8),
            "result":       result,
            "pnl_pct":      round(pnl_pct * 100, 2),
            "margin_pnl":   round(pnl_pct * S2_LEVERAGE * 100, 2),
            "daily_rsi":    round(daily_rsi, 1),
            "candles_held": exit_i - (i + 1),
        })
        i = exit_i + 1

    if debug:
        scanned = dbg["total"] - min_i
        print(f"\n  📊 {sym} funnel ({dbg['total']}d, {scanned} scanned):")
        print(f"     RSI>70     : {dbg['rsi_pass']:>5} pass  {dbg['rsi_fail']:>5} fail")
        print(f"     BigCandle  : {dbg['big_pass']:>5} pass  {dbg['big_fail']:>5} fail")
        print(f"     Consol     : {dbg['con_pass']:>5} pass  (ib={dbg['con_ib']} rsi={dbg['con_rsi']})")
        print(f"     Breakout   : {dbg['brk_pass']:>5} pass  {dbg['brk_fail']:>5} fail")
        print(f"     Trades     : {len(trades)}")

    return trades


def backtest_s1_symbol(sym: str) -> list[dict]:
    from config_s1 import (
        RSI_LONG_THRESH, RSI_SHORT_THRESH, CONSOLIDATION_CANDLES,
        BREAKOUT_BUFFER_PCT, ADX_TREND_THRESHOLD, LEVERAGE, TAKE_PROFIT_PCT,
    )
    df_1h = fetch_ohlcv_bitget(sym, "1H", 500)
    df_3m = fetch_ohlcv_bitget(sym, "3m", 1000)
    df_1d = load_daily(sym, days=150)
    if df_1h.empty or df_3m.empty or df_1d.empty:
        return []

    trades    = []
    rsi_3m    = calculate_rsi(df_3m["close"].astype(float))
    adx_vals  = calculate_adx(df_1d)["adx"]
    min_i     = config_mod.RSI_PERIOD + config_mod.CONSOLIDATION_CANDLES + 5

    i = min_i
    while i < len(df_3m) - 1:
        ts = int(df_3m.iloc[i]["ts"])
        daily_slice = df_1d[df_1d["ts"] <= ts]
        if len(daily_slice) < 30:
            i += 1; continue
        adx_val = adx_vals.iloc[:len(daily_slice)].iloc[-1]
        if pd.isna(adx_val) or float(adx_val) < ADX_TREND_THRESHOLD:
            i += 1; continue

        htf = df_1h[df_1h["ts"] <= ts].tail(3)
        if len(htf) < 2:
            i += 1; continue
        htf_bull = float(htf.iloc[-1]["high"]) > float(htf.iloc[-2]["high"])
        htf_bear = float(htf.iloc[-1]["low"])  < float(htf.iloc[-2]["low"])
        if not htf_bull and not htf_bear:
            i += 1; continue

        rsi_val   = float(rsi_3m.iloc[i])
        direction = None
        if htf_bull and rsi_val > RSI_LONG_THRESH:
            direction = "LONG"
        elif htf_bear and rsi_val < RSI_SHORT_THRESH:
            direction = "SHORT"
        else:
            i += 1; continue

        ltf_win = df_3m.iloc[:i+1]
        rsi_win = rsi_3m.iloc[:i+1]
        is_coil, bh, bl = detect_consolidation(
            ltf_win, rsi_series=rsi_win,
            rsi_threshold=RSI_LONG_THRESH if direction == "LONG" else RSI_SHORT_THRESH,
            direction=direction
        )
        if not is_coil:
            i += 1; continue

        trigger = bh * (1 + BREAKOUT_BUFFER_PCT) if direction == "LONG" else bl * (1 - BREAKOUT_BUFFER_PCT)
        nxt = df_3m.iloc[i + 1]
        entry_price = None
        if direction == "LONG":
            if float(nxt["open"]) > trigger:   entry_price = float(nxt["open"])
            elif float(nxt["high"]) > trigger:  entry_price = trigger
        else:
            if float(nxt["open"]) < trigger:   entry_price = float(nxt["open"])
            elif float(nxt["low"]) < trigger:   entry_price = trigger
        if entry_price is None:
            i += 1; continue

        sl_price = bl * 0.999 if direction == "LONG" else bh * 1.001
        tp_price = entry_price * (1 + TAKE_PROFIT_PCT) if direction == "LONG" else entry_price * (1 - TAKE_PROFIT_PCT)
        result   = "OPEN"
        exit_price = exit_i = None

        for j in range(i + 2, min(i + 500, len(df_3m))):
            c = df_3m.iloc[j]
            if direction == "LONG":
                if float(c["low"])  <= sl_price: result = "LOSS"; exit_price = sl_price; exit_i = j; break
                if float(c["high"]) >= tp_price: result = "WIN";  exit_price = tp_price; exit_i = j; break
            else:
                if float(c["high"]) >= sl_price: result = "LOSS"; exit_price = sl_price; exit_i = j; break
                if float(c["low"])  <= tp_price: result = "WIN";  exit_price = tp_price; exit_i = j; break

        if result == "OPEN":
            exit_i     = min(i + 500, len(df_3m) - 1)
            exit_price = float(df_3m.iloc[exit_i]["close"])
            result     = "WIN" if (direction == "LONG" and exit_price > entry_price) or \
                                  (direction == "SHORT" and exit_price < entry_price) else "LOSS"

        pnl_pct = (exit_price - entry_price) / entry_price
        if direction == "SHORT": pnl_pct = -pnl_pct

        entry_dt = datetime.fromtimestamp(int(df_3m.iloc[i+1]["ts"]) / 1000, tz=timezone.utc)
        exit_dt  = datetime.fromtimestamp(int(df_3m.iloc[exit_i]["ts"]) / 1000, tz=timezone.utc)
        trades.append({
            "strategy": "S1", "symbol": sym, "direction": direction,
            "entry_date": entry_dt.strftime("%Y-%m-%d %H:%M"),
            "exit_date":  exit_dt.strftime("%Y-%m-%d %H:%M"),
            "entry_price": round(entry_price, 8), "exit_price": round(exit_price, 8),
            "sl": round(sl_price, 8), "tp": round(tp_price, 8),
            "result": result,
            "pnl_pct":    round(pnl_pct * 100, 2),
            "margin_pnl": round(pnl_pct * LEVERAGE * 100, 2),
            "rsi_entry":  round(rsi_val, 1),
        })
        i = exit_i + 1
    return trades


def backtest_s3_symbol(sym: str, days: int = 180, debug: bool = False) -> list[dict]:
    """
    S3 — 15m Swing Pullback (Long-only). All indicators on 15m.
    Prerequisites: 15m EMA alignment + ADX > 30
    Entry: 15m Stoch oversold → green uptick → break above entry trigger + MACD bullish
    """
    from config_s3 import (
        S3_EMA_FAST, S3_EMA_MED, S3_EMA_SLOW, S3_EMA_TREND,
        S3_ADX_MIN, S3_STOCH_K_PERIOD, S3_STOCH_D_SMOOTH, S3_STOCH_OVERSOLD,
        S3_STOCH_LOOKBACK, S3_MACD_FAST, S3_MACD_SLOW, S3_MACD_SIGNAL,
        S3_ENTRY_BUFFER_PCT, S3_SL_BUFFER_PCT, S3_MIN_RR,
        S3_TRAILING_TRIGGER_PCT, S3_LEVERAGE,
    )

    # Fetch 15m data — need 210+ candles for EMA200 warmup
    df_15m = fetch_m15(sym, days=days)
    if df_15m.empty or len(df_15m) < 210:
        return []

    trades = []
    min_15m = max(210, S3_STOCH_K_PERIOD + S3_STOCH_D_SMOOTH + S3_STOCH_LOOKBACK + S3_MACD_SLOW + 10)

    dbg = dict(total=len(df_15m), ema=0, adx=0,
               stoch_os=0, green_uptick=0, breakout=0, macd_ok=0, rr_ok=0)

    i = min_15m
    while i < len(df_15m) - 1:
        # ── 15m prerequisites ─────────────────────────────────── #
        m15_window = df_15m.iloc[:i+1]
        closes_15  = m15_window["close"].astype(float)

        ema10  = float(calculate_ema(closes_15, S3_EMA_FAST).iloc[-1])
        ema20  = float(calculate_ema(closes_15, S3_EMA_MED).iloc[-1])
        ema50  = float(calculate_ema(closes_15, S3_EMA_SLOW).iloc[-1])
        ema200 = float(calculate_ema(closes_15, S3_EMA_TREND).iloc[-1])

        if not (ema10 > ema20 > ema50 > ema200):
            i += 1
            continue
        dbg["ema"] += 1

        adx_val = float(calculate_adx(m15_window)["adx"].iloc[-1])
        if adx_val < S3_ADX_MIN:
            i += 1
            continue
        dbg["adx"] += 1

        slow_k, _ = calculate_stoch(m15_window, S3_STOCH_K_PERIOD, S3_STOCH_D_SMOOTH)
        macd_line, sig_line, _ = calculate_macd(closes_15, S3_MACD_FAST, S3_MACD_SLOW, S3_MACD_SIGNAL)

        # Look for oversold in last S3_STOCH_LOOKBACK completed candles
        if i < S3_STOCH_LOOKBACK + 1:
            i += 1
            continue
        lookback_k = slow_k.iloc[-S3_STOCH_LOOKBACK - 1:-1]
        oversold_positions = [idx for idx, v in enumerate(lookback_k) if not np.isnan(v) and v < S3_STOCH_OVERSOLD]

        if not oversold_positions:
            i += 1
            continue
        dbg["stoch_os"] += 1

        # Find pivot low
        last_os_rel  = oversold_positions[-1]
        first_os_rel = oversold_positions[0]
        abs_last_os  = i - S3_STOCH_LOOKBACK + last_os_rel
        abs_first_os = i - S3_STOCH_LOOKBACK + first_os_rel

        os_period_df = df_15m.iloc[abs_first_os : abs_last_os + 1]
        pivot_low    = float(os_period_df["low"].min())
        sl_price     = pivot_low * (1 - S3_SL_BUFFER_PCT)

        # First green candle after last oversold
        after_os_df = df_15m.iloc[abs_last_os + 1 : i]
        if after_os_df.empty:
            i += 1
            continue

        first_green = None
        for _, row in after_os_df.iterrows():
            if float(row["close"]) > float(row["open"]):
                first_green = row
                break

        if first_green is None:
            i += 1
            continue
        dbg["green_uptick"] += 1

        entry_trigger = float(first_green["high"]) * (1 + S3_ENTRY_BUFFER_PCT)

        # Check breakout in next candle
        nxt = df_15m.iloc[i + 1]
        entry_price = None
        if float(nxt["open"]) > entry_trigger:
            entry_price = float(nxt["open"])
        elif float(nxt["high"]) > entry_trigger:
            entry_price = entry_trigger

        if entry_price is None:
            i += 1
            continue
        dbg["breakout"] += 1

        # Check MACD at entry
        # Use window INCLUDING the entry candle (i+1)
        m15_at_entry = df_15m.iloc[:i+2]
        closes_at_entry = m15_at_entry["close"].astype(float)
        ml_entry, ms_entry, _ = calculate_macd(closes_at_entry, S3_MACD_FAST, S3_MACD_SLOW, S3_MACD_SIGNAL)
        macd_ok = float(ml_entry.iloc[-1]) > float(ms_entry.iloc[-1])

        if not macd_ok:
            i += 1
            continue
        dbg["macd_ok"] += 1

        # Check R:R
        risk = entry_price - sl_price
        if risk <= 0:
            i += 1
            continue
        reward = S3_TRAILING_TRIGGER_PCT * entry_price
        rr = reward / risk
        if rr < S3_MIN_RR:
            i += 1
            continue
        dbg["rr_ok"] += 1

        # Execute trade simulation (partial TP at +10%, trailing stop on rest)
        tp_price = entry_price * (1 + S3_TRAILING_TRIGGER_PCT)
        result   = "OPEN"
        exit_price = exit_i = None

        for j in range(i + 2, min(i + 200, len(df_15m))):
            c = df_15m.iloc[j]
            if float(c["low"]) <= sl_price:
                result = "LOSS"; exit_price = sl_price; exit_i = j; break
            if float(c["high"]) >= tp_price:
                result = "WIN";  exit_price = tp_price; exit_i = j; break

        if result == "OPEN":
            exit_i     = min(i + 200, len(df_15m) - 1)
            exit_price = float(df_15m.iloc[exit_i]["close"])
            result     = "WIN" if exit_price > entry_price else "LOSS"

        pnl_pct = (exit_price - entry_price) / entry_price
        entry_dt = datetime.fromtimestamp(int(df_15m.iloc[i+1]["ts"]) / 1000, tz=timezone.utc)
        exit_dt  = datetime.fromtimestamp(int(df_15m.iloc[exit_i]["ts"]) / 1000, tz=timezone.utc)

        trades.append({
            "strategy":     "S3",
            "symbol":       sym,
            "entry_date":   entry_dt.strftime("%Y-%m-%d %H:%M"),
            "exit_date":    exit_dt.strftime("%Y-%m-%d %H:%M"),
            "entry_price":  round(entry_price, 8),
            "exit_price":   round(exit_price,  8),
            "sl":           round(sl_price, 8),
            "tp":           round(tp_price, 8),
            "result":       result,
            "pnl_pct":      round(pnl_pct * 100, 2),
            "margin_pnl":   round(pnl_pct * S3_LEVERAGE * 100, 2),
            "adx":          round(adx_val, 1),
            "rr":           round(rr, 1),
            "candles_held": exit_i - (i + 1),
        })
        i = exit_i + 1

    if debug:
        scanned = dbg["total"] - min_15m
        print(f"\n  📊 {sym} S3 funnel ({dbg['total']} 15m candles, {scanned} scanned):")
        print(f"     15m EMA    : {dbg['ema']:>5} pass")
        print(f"     15m ADX    : {dbg['adx']:>5} pass")
        print(f"     Stoch OS   : {dbg['stoch_os']:>5} pass")
        print(f"     Green uptick: {dbg['green_uptick']:>5} pass")
        print(f"     Breakout   : {dbg['breakout']:>5} pass")
        print(f"     MACD       : {dbg['macd_ok']:>5} pass")
        print(f"     R:R >= 2.0 : {dbg['rr_ok']:>5} pass")
        print(f"     Trades     : {len(trades)}")

    return trades


def build_html_report(all_trades: list[dict], run_time: str) -> str:
    def stats(tlist):
        if not tlist:
            return dict(count=0, wins=0, losses=0, win_rate=0,
                        total_margin_pnl=0, avg_win=0, avg_loss=0, best=0, worst=0)
        t = pd.DataFrame(tlist)
        w = t[t["result"] == "WIN"];  l = t[t["result"] == "LOSS"]
        return dict(
            count=len(t), wins=len(w), losses=len(l),
            win_rate=round(len(w)/len(t)*100, 1),
            total_margin_pnl=round(t["margin_pnl"].sum(), 1),
            avg_win=round(w["margin_pnl"].mean(), 1) if len(w) else 0,
            avg_loss=round(l["margin_pnl"].mean(), 1) if len(l) else 0,
            best=round(t["margin_pnl"].max(), 1),
            worst=round(t["margin_pnl"].min(), 1),
        )

    s1 = stats([t for t in all_trades if t["strategy"] == "S1"])
    s2 = stats([t for t in all_trades if t["strategy"] == "S2"])
    s3 = stats([t for t in all_trades if t["strategy"] == "S3"])
    ov = stats(all_trades)

    def col(v):
        if isinstance(v, (int,float)):
            return "#00d68f" if v > 0 else "#ff4d6a" if v < 0 else "#8899aa"
        return "#c9d8e8"

    def card(label, val, sfx=""):
        return (f'<div class="stat"><div class="stat-label">{label}</div>'
                f'<div class="stat-val" style="color:{col(val)}">{val}{sfx}</div></div>')

    def tbl(tlist, strat):
        if not tlist:
            return f'<p style="color:#8899aa;padding:20px">No {strat} trades</p>'
        rows = ""
        for t in sorted(tlist, key=lambda x: x["entry_date"], reverse=True):
            rc = "#00d68f" if t["result"]=="WIN" else "#ff4d6a"
            pc = col(t["margin_pnl"])
            held = f'{t.get("candles_held","?")}' if strat in ["S2", "S3"] else "—"
            held_unit = "d" if strat == "S2" else ("×15m" if strat == "S3" else "")
            dir_html = ""
            if strat == "S1":
                d = t.get("direction","LONG")
                bg = "#00d68f22" if d=="LONG" else "#ff4d6a22"
                fc = "#00d68f"   if d=="LONG" else "#ff4d6a"
                dir_html = f'<span style="background:{bg};color:{fc};padding:2px 6px;border-radius:4px;font-size:11px">{d}</span>'
            extra_info = ""
            if strat == "S3":
                adx_v = t.get("adx", 0)
                rr_v  = t.get("rr", 0)
                extra_info = f'ADX={adx_v} R:R={rr_v}'
            rows += (f'<tr><td>{t["symbol"].replace("USDT","")}</td><td>{dir_html}</td>'
                     f'<td>{t["entry_date"]}</td><td>{t["exit_date"]}</td><td>{held}{held_unit}</td>'
                     f'<td>{t["entry_price"]}</td><td>{t["exit_price"]}</td>'
                     f'<td style="color:{rc};font-weight:600">{t["result"]}</td>'
                     f'<td style="color:{pc}">{t["margin_pnl"]:+.1f}%</td><td style="font-size:10px;color:#8899aa">{extra_info}</td></tr>')
        return (f'<div style="overflow-x:auto"><table><thead><tr>'
                f'<th>Symbol</th><th>Dir</th><th>Entry</th><th>Exit</th>'
                f'<th>Held</th><th>Entry$</th><th>Exit$</th><th>Result</th><th>Margin PnL</th><th>Info</th>'
                f'</tr></thead><tbody>{rows}</tbody></table></div>')

    s1t = [t for t in all_trades if t["strategy"]=="S1"]
    s2t = [t for t in all_trades if t["strategy"]=="S2"]
    s3t = [t for t in all_trades if t["strategy"]=="S3"]

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Backtest Report — {run_time}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0d1117;color:#c9d8e8;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:13px;padding:24px}}
h1{{font-size:22px;color:#e8f0f8;margin-bottom:4px}}
h2{{font-size:15px;color:#8899aa;margin:32px 0 16px;border-bottom:1px solid #1e2d3d;padding-bottom:8px}}
.meta{{color:#8899aa;font-size:12px;margin-bottom:32px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:12px;margin-bottom:24px}}
.stat{{background:#111827;border:1px solid #1e2d3d;border-radius:8px;padding:14px}}
.stat-label{{font-size:10px;color:#8899aa;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}}
.stat-val{{font-size:20px;font-weight:700}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{background:#0d1117;color:#8899aa;padding:8px 12px;text-align:left;font-size:11px;text-transform:uppercase;position:sticky;top:0}}
td{{padding:8px 12px;border-bottom:1px solid #1a2535}}
tr:hover td{{background:#1a2535}}
.tabs{{display:flex;gap:8px;margin-bottom:16px}}
.tab{{padding:8px 20px;border-radius:8px;cursor:pointer;border:1px solid #1e2d3d;background:#111827;color:#8899aa;font-size:13px}}
.tab.active{{background:#1e3a5f;border-color:#60a5fa;color:#60a5fa}}
.tc{{display:none}}.tc.active{{display:block}}
</style></head><body>
<h1>📊 Backtest Report</h1>
<div class="meta">Run: {run_time} | S1: {s1["count"]} | S2: {s2["count"]} | S3: {s3["count"]} trades</div>
<h2>Overall</h2>
<div class="grid">
{card("Total Trades",ov["count"])}{card("Win Rate",ov["win_rate"],"%")}
{card("Total Margin PnL",ov["total_margin_pnl"],"%")}{card("Avg Win",ov["avg_win"],"%")}
{card("Avg Loss",ov["avg_loss"],"%")}{card("Best",ov["best"],"%")}{card("Worst",ov["worst"],"%")}
</div>
<div class="tabs">
<div class="tab active" onclick="sw('s1')">S1 — MTF RSI ({s1["count"]})</div>
<div class="tab"        onclick="sw('s2')">S2 — Daily Coil ({s2["count"]})</div>
<div class="tab"        onclick="sw('s3')">S3 — Pullback ({s3["count"]})</div>
</div>
<div id="ts1" class="tc active">
<div class="grid">
{card("Trades",s1["count"])}{card("Win Rate",s1["win_rate"],"%")}
{card("Total Margin",s1["total_margin_pnl"],"%")}{card("Avg Win",s1["avg_win"],"%")}
{card("Avg Loss",s1["avg_loss"],"%")}{card("Best",s1["best"],"%")}{card("Worst",s1["worst"],"%")}
</div>{tbl(s1t,"S1")}</div>
<div id="ts2" class="tc">
<div class="grid">
{card("Trades",s2["count"])}{card("Win Rate",s2["win_rate"],"%")}
{card("Total Margin",s2["total_margin_pnl"],"%")}{card("Avg Win",s2["avg_win"],"%")}
{card("Avg Loss",s2["avg_loss"],"%")}{card("Best",s2["best"],"%")}{card("Worst",s2["worst"],"%")}
</div>{tbl(s2t,"S2")}</div>
<div id="ts3" class="tc">
<div class="grid">
{card("Trades",s3["count"])}{card("Win Rate",s3["win_rate"],"%")}
{card("Total Margin",s3["total_margin_pnl"],"%")}{card("Avg Win",s3["avg_win"],"%")}
{card("Avg Loss",s3["avg_loss"],"%")}{card("Best",s3["best"],"%")}{card("Worst",s3["worst"],"%")}
</div>{tbl(s3t,"S3")}</div>
<script>
function sw(t){{
  document.querySelectorAll('.tab').forEach((e,i)=>e.classList.toggle('active',['s1','s2','s3'][i]===t));
  document.querySelectorAll('.tc').forEach(e=>e.classList.remove('active'));
  document.getElementById('t'+t).classList.add('active');
}}
</script></body></html>"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols",      nargs="*")
    parser.add_argument("--s1-only",      action="store_true")
    parser.add_argument("--s2-only",      action="store_true")
    parser.add_argument("--s3-only",      action="store_true")
    parser.add_argument("--limit",        type=int,   default=None)
    parser.add_argument("--days",         type=int,   default=1095)
    parser.add_argument("--output",       default="backtest_report.html")
    parser.add_argument("--ib-tolerance", type=float, default=0.05)
    parser.add_argument("--ib-majority",  type=float, default=0.70)
    parser.add_argument("--debug",        action="store_true")
    args = parser.parse_args()

    run_s1 = not (args.s2_only or args.s3_only)
    run_s2 = not (args.s1_only or args.s3_only)
    run_s3 = not (args.s1_only or args.s2_only)

    print("🔍 Loading qualified symbols...")
    symbols = args.symbols if args.symbols else get_qualified_symbols()
    if args.limit:
        symbols = symbols[:args.limit]
    print(f"   {len(symbols)} symbols | {args.days} days of history")
    print(f"   Inside-bar: tolerance={args.ib_tolerance*100:.0f}%  majority={args.ib_majority*100:.0f}%")

    diagnose_symbols(symbols)

    all_trades = []
    run_time   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    for idx, sym in enumerate(symbols):
        print(f"\n[{idx+1}/{len(symbols)}] {sym}", end="  ", flush=True)

        if run_s2:
            try:
                df = load_daily(sym, days=args.days)
                if len(df) >= 50:
                    trades = backtest_s2_symbol(
                        sym, df,
                        ib_tolerance=args.ib_tolerance,
                        ib_majority=args.ib_majority,
                        debug=args.debug,
                    )
                    all_trades.extend(trades)
                    print(f"S2:{len(trades)}({len(df)}d)", end="  ", flush=True)
                else:
                    print(f"S2:skip({len(df)}d)", end="  ", flush=True)
            except Exception as e:
                print(f"S2:err({e})", end="  ", flush=True)
                if args.debug:
                    import traceback; traceback.print_exc()
            time.sleep(0.3)

        if run_s1:
            try:
                trades = backtest_s1_symbol(sym)
                all_trades.extend(trades)
                print(f"S1:{len(trades)}", end="  ", flush=True)
            except Exception as e:
                print(f"S1:err({e})", end="  ", flush=True)
            time.sleep(0.3)

        if run_s3:
            try:
                trades = backtest_s3_symbol(sym, days=args.days, debug=args.debug)
                all_trades.extend(trades)
                print(f"S3:{len(trades)}", end="  ", flush=True)
            except Exception as e:
                print(f"S3:err({e})", end="  ", flush=True)
                if args.debug:
                    import traceback; traceback.print_exc()
            time.sleep(0.3)

    print(f"\n\n{'='*50}")
    print(f"✅ Total trades: {len(all_trades)}")
    if all_trades:
        wins = sum(1 for t in all_trades if t["result"] == "WIN")
        pnl  = sum(t["margin_pnl"] for t in all_trades)
        print(f"   Win rate        : {wins/len(all_trades)*100:.1f}%  ({wins}W / {len(all_trades)-wins}L)")
        print(f"   Total margin PnL: {pnl:+.1f}%")
        s1l = [t for t in all_trades if t["strategy"]=="S1"]
        s2l = [t for t in all_trades if t["strategy"]=="S2"]
        s3l = [t for t in all_trades if t["strategy"]=="S3"]
        if s1l: print(f"   S1: {len(s1l)} trades  win rate {sum(1 for t in s1l if t['result']=='WIN')/len(s1l)*100:.1f}%")
        if s2l: print(f"   S2: {len(s2l)} trades  win rate {sum(1 for t in s2l if t['result']=='WIN')/len(s2l)*100:.1f}%")
        if s3l: print(f"   S3: {len(s3l)} trades  win rate {sum(1 for t in s3l if t['result']=='WIN')/len(s3l)*100:.1f}%")

    print(f"\n📄 Writing → {args.output}")
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(build_html_report(all_trades, run_time))
    print(f"✅ Done → {args.output}")


if __name__ == "__main__":
    main()
