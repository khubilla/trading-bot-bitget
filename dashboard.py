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
    Returns OHLCV candles for a symbol, used by the chart modal.
    Also returns consolidation box and breakout trigger line levels.
    """
    try:
        import trader as tr
        import config
        from strategy import detect_consolidation, calculate_rsi

        df = tr.get_candles(symbol, interval, limit=limit)
        if df.empty:
            return JSONResponse({"error": "No candle data"})

        # Build lightweight-charts compatible format
        candles = [
            {
                "time": int(row["ts"]) // 1000,  # seconds
                "open":  float(row["open"]),
                "high":  float(row["high"]),
                "low":   float(row["low"]),
                "close": float(row["close"]),
            }
            for _, row in df.iterrows()
        ]

        # RSI series
        closes = df["close"].astype(float)
        rsi_series_raw = calculate_rsi(closes)
        rsi_series = [
            {"time": int(df["ts"].iloc[i]) // 1000, "value": round(float(v), 2)}
            for i, v in enumerate(rsi_series_raw)
            if not (v != v)  # skip NaN
        ]

        # Consolidation box
        is_coil, box_high, box_low = detect_consolidation(df)
        breakout_long  = round(box_high * (1 + config.BREAKOUT_BUFFER_PCT), 8) if box_high else None
        breakout_short = round(box_low  * (1 - config.BREAKOUT_BUFFER_PCT), 8) if box_low  else None

        # Current mark price
        try:
            mark = tr.get_mark_price(symbol)
        except Exception:
            mark = float(df["close"].iloc[-1])

        return JSONResponse({
            "symbol":          symbol,
            "interval":        interval,
            "candles":         candles,
            "rsi":             rsi_series,
            "consolidating":   is_coil,
            "box_high":        round(box_high,  8) if box_high  else None,
            "box_low":         round(box_low,   8) if box_low   else None,
            "breakout_long":   breakout_long,
            "breakout_short":  breakout_short,
            "mark_price":      mark,
            "rsi_long_thresh":  config.RSI_LONG_THRESH,
            "rsi_short_thresh": config.RSI_SHORT_THRESH,
        })

    except Exception as e:
        return JSONResponse({"error": str(e)})


@app.get("/", response_class=HTMLResponse)
def index():
    html = Path(__file__).parent / "templates" / "dashboard.html"
    return HTMLResponse(content=html.read_text(encoding="utf-8"))


if __name__ == "__main__":
    print("🚀 Dashboard: http://localhost:8080")
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning")
