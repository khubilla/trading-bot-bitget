"""
dashboard.py — Live Web Dashboard
Serves the trading dashboard at http://localhost:8080

Run in a separate terminal alongside bot.py:
    python dashboard.py
"""

import json, os, sys
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

STATE_FILE = "state.json"
app = FastAPI(title="Bitget MTF Bot Dashboard")

# Add bot directory to path so we can import trader + config
sys.path.insert(0, str(Path(__file__).parent))


@app.get("/api/state")
def get_state():
    if not os.path.exists(STATE_FILE):
        return JSONResponse({"status": "STOPPED", "error": "state.json not found — is bot.py running?"})
    try:
        with open(STATE_FILE, "r") as f:
            return JSONResponse(json.load(f))
    except Exception as e:
        return JSONResponse({"status": "ERROR", "error": str(e)})


@app.get("/api/candles/{symbol}")
def get_candles(symbol: str, interval: str = "3m", limit: int = 80):
    """
    Returns OHLCV candles + consolidation box + trigger lines.
    For 3m (S1): uses RSI-zone consolidation detection.
    For 1D (S2): uses daily RSI + S2 consolidation + entry trigger logic.
    """
    try:
        import trader as tr
        import config
        from strategy import detect_consolidation, calculate_rsi, evaluate_s2

        # Fetch extra candles for indicator warmup (EMA/ADX need history to converge)
        # Then trim to display_limit for the chart
        is_daily      = interval in ("1D", "1d")
        display_limit = 80 if is_daily else limit
        warmup        = 200   # extra candles for indicator warmup
        fetch_total   = display_limit + warmup

        df_full = tr.get_candles(symbol, interval, limit=fetch_total)
        if df_full.empty:
            return JSONResponse({"error": "No candle data"})

        # Compute all indicators on full history
        closes_full = df_full["close"].astype(float)

        # Trim to display window AFTER computing indicators
        df = df_full.tail(display_limit).reset_index(drop=True)
        closes = df["close"].astype(float)

        # Determine price decimal precision from the data
        sample_price = float(df["close"].iloc[-1])
        if sample_price < 0.0001:    price_decimals = 8
        elif sample_price < 0.01:    price_decimals = 6
        elif sample_price < 1:       price_decimals = 5
        elif sample_price < 10:      price_decimals = 4
        elif sample_price < 1000:    price_decimals = 3
        else:                        price_decimals = 2

        # lightweight-charts needs UTC seconds
        candles = [
            {
                "time":  int(row["ts"]) // 1000,
                "open":  float(row["open"]),
                "high":  float(row["high"]),
                "low":   float(row["low"]),
                "close": float(row["close"]),
            }
            for _, row in df.iterrows()
        ]

        # RSI — compute on full history, slice to display window
        rsi_full = calculate_rsi(closes_full)
        rsi_display = rsi_full.tail(display_limit)
        rsi_series = []
        for i, v in enumerate(rsi_display):
            t = int(df["ts"].iloc[i]) // 1000
            if v != v:
                continue
            rsi_series.append({"time": t, "value": round(float(v), 2)})

        from strategy import calculate_ema, calculate_adx as _calc_adx
        ema10_full = calculate_ema(closes_full, 10).tail(display_limit)
        ema20_full = calculate_ema(closes_full, 20).tail(display_limit)
        ema10 = [
            {"time": int(df["ts"].iloc[i]) // 1000, "value": round(float(v), 8)}
            for i, v in enumerate(ema10_full) if not (v != v)
        ]
        ema20 = [
            {"time": int(df["ts"].iloc[i]) // 1000, "value": round(float(v), 8)}
            for i, v in enumerate(ema20_full) if not (v != v)
        ]

        # ADX, +DI, -DI — compute on full history, slice to display window
        adx_result   = _calc_adx(df_full, period=14)
        adx_display  = adx_result["adx"].tail(display_limit)
        pdi_display  = adx_result["plus_di"].tail(display_limit)
        mdi_display  = adx_result["minus_di"].tail(display_limit)
        adx_data = [
            {"time": int(df["ts"].iloc[i]) // 1000, "value": round(float(v), 2)}
            for i, v in enumerate(adx_display) if not (v != v)
        ]
        plus_di_data = [
            {"time": int(df["ts"].iloc[i]) // 1000, "value": round(float(v), 2)}
            for i, v in enumerate(pdi_display) if not (v != v)
        ]
        minus_di_data = [
            {"time": int(df["ts"].iloc[i]) // 1000, "value": round(float(v), 2)}
            for i, v in enumerate(mdi_display) if not (v != v)
        ]

        # Consolidation box + trigger
        is_coil       = False
        box_high      = None
        box_low       = None
        breakout_long = None
        breakout_short= None

        if is_daily:
            # S2 — use evaluate_s2 to get exact box and trigger
            sig, daily_rsi, bh, bl, reason = evaluate_s2(symbol, df)
            # Also check consolidation even if no full signal yet
            # Try to find coil regardless of big candle (for chart display)
            from config_s2 import S2_CONSOL_CANDLES, S2_CONSOL_RANGE_PCT, S2_RSI_LONG_THRESH, S2_BREAKOUT_BUFFER, S2_LONG_WICK_RATIO
            daily_rsi_val = float(rsi_full.iloc[-1])
            if daily_rsi_val > S2_RSI_LONG_THRESH:
                for n in range(1, S2_CONSOL_CANDLES + 1):
                    window = df.iloc[-n - 1:-1]
                    if len(window) < n:
                        continue
                    wh  = float(window["high"].max())
                    wl  = float(window["low"].min())
                    mid = (wh + wl) / 2
                    if mid == 0:
                        continue
                    range_pct = (wh - wl) / mid
                    if range_pct > S2_CONSOL_RANGE_PCT:
                        continue
                    window_rsi = rsi_full.iloc[-n - 1:-1]
                    if not (window_rsi > S2_RSI_LONG_THRESH).all():
                        continue
                    # Found coil
                    is_coil  = True
                    box_high = round(wh, 8)
                    box_low  = round(wl, 8)
                    # Entry trigger
                    high_candle = window.loc[window["high"].idxmax()]
                    uw   = float(high_candle["high"]) - max(float(high_candle["close"]), float(high_candle["open"]))
                    body = abs(float(high_candle["close"]) - float(high_candle["open"]))
                    if uw > S2_LONG_WICK_RATIO * body:
                        body_top      = max(float(high_candle["close"]), float(high_candle["open"]))
                        breakout_long = round(body_top * (1 + S2_BREAKOUT_BUFFER), 8)
                    else:
                        breakout_long = round(float(high_candle["high"]) * (1 + S2_BREAKOUT_BUFFER), 8)
                    break
        else:
            # S1 — RSI-zone aware consolidation on 3m
            rsi_thresh = config.RSI_LONG_THRESH
            direction  = "LONG"  # default; show long setup
            is_coil, bh, bl = detect_consolidation(
                df, rsi_series=rsi_full, rsi_threshold=rsi_thresh, direction=direction
            )
            if bh:
                box_high       = round(bh, 8)
                box_low        = round(bl, 8)
                breakout_long  = round(bh * (1 + config.BREAKOUT_BUFFER_PCT), 8)
                breakout_short = round(bl * (1 - config.BREAKOUT_BUFFER_PCT), 8)

        # Current mark price
        try:
            mark = tr.get_mark_price(symbol)
        except Exception:
            mark = float(df["close"].iloc[-1])

        return JSONResponse({
            "symbol":           symbol,
            "interval":         interval,
            "candles":          candles,
            "rsi":              rsi_series,
            "ema10":            ema10,
            "ema20":            ema20,
            "adx":              adx_data,
            "plus_di":          plus_di_data,
            "minus_di":         minus_di_data,
            "consolidating":    is_coil,
            "box_high":         box_high,
            "box_low":          box_low,
            "breakout_long":    breakout_long,
            "breakout_short":   breakout_short,
            "mark_price":       mark,
            "price_decimals":   price_decimals,
            "rsi_long_thresh":  config.RSI_LONG_THRESH,
            "rsi_short_thresh": config.RSI_SHORT_THRESH,
        })

    except Exception as e:
        import traceback
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()})


@app.get("/", response_class=HTMLResponse)
def index():
    html = Path(__file__).parent / "templates" / "dashboard.html"
    return HTMLResponse(content=html.read_text(encoding="utf-8"))


if __name__ == "__main__":
    print("🚀 Dashboard: http://localhost:8080")
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning")