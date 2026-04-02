"""
dashboard.py — Live Web Dashboard
Serves the trading dashboard at http://localhost:8080

Run in a separate terminal alongside bot.py:
    python dashboard.py
"""

import csv, json, os, re, sys, time, zoneinfo
from pathlib import Path
from datetime import datetime
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import uvicorn

import os as _os
import sys as _sys
from pathlib import Path as _Path
PAPER_MODE = "--paper" in _sys.argv or _os.environ.get("PAPER_MODE", "") == "1"
_DATA_DIR  = _Path(_os.environ.get("DATA_DIR", "."))
STATE_FILE     = str(_DATA_DIR / ("state_paper.json" if PAPER_MODE else "state.json"))
IG_STATE_FILE  = str(_DATA_DIR / "ig_state.json")
IG_TRADES_FILE = str(_DATA_DIR / "ig_trades.csv")
PORT       = int(_os.environ.get("PORT", 8081 if PAPER_MODE else 8080))
app = FastAPI(title="MTF Bot Dashboard" + (" [PAPER]" if PAPER_MODE else ""))

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

# Middleware to validate symbols before Starlette's router rejects path traversal attempts
@app.middleware("http")
async def validate_symbol_middleware(request: Request, call_next):
    """
    Intercept /api/candles/{symbol} requests before router processing.
    Validates symbol against allowlist and returns 400 for invalid symbols.
    This catches path traversal attempts (../etc/passwd, etc.) that Starlette
    would otherwise reject with 404.
    """
    # Use raw_path from ASGI scope to detect path traversal attempts before normalization
    raw_path = request.scope.get("raw_path", b"").decode("utf-8", errors="replace")
    if raw_path.startswith("/api/candles/"):
        # Extract the symbol part after /api/candles/
        from urllib.parse import unquote
        symbol_part = raw_path[len("/api/candles/"):]
        symbol = unquote(symbol_part)
        if not re.fullmatch(r"^[A-Z0-9]{2,20}$", symbol):
            return JSONResponse({"error": "invalid symbol"}, status_code=400)

    response = await call_next(request)
    return response

# Security headers middleware to protect against common web vulnerabilities
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """
    Inject security headers on every response:
    - X-Frame-Options: DENY — prevents clickjacking
    - X-Content-Type-Options: nosniff — prevents MIME-type sniffing
    - Referrer-Policy: no-referrer — restricts referrer information
    - Content-Security-Policy — controls resource loading and prevents XSS
    - Remove server header — prevents version leakage
    """
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' https://unpkg.com 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com"
    )
    # Remove server header to prevent version leakage
    if "server" in response.headers:
        del response.headers["server"]
    return response

# Bearer token authentication middleware
@app.middleware("http")
async def require_api_key(request: Request, call_next):
    """
    Require Authorization: Bearer <token> header on all endpoints.
    Token is read from DASHBOARD_API_KEY environment variable.
    If env var is not set, auth is bypassed (allows existing deployments without token to work).
    """
    if request.url.path in ("/", "/favicon.ico"):
        return await call_next(request)
    # Reject path traversal before routing normalises the URL
    raw_path = request.scope.get("raw_path", b"")
    if b".." in raw_path or b"%2e%2e" in raw_path.lower():
        return JSONResponse({"error": "invalid path"}, status_code=400)
    api_key = os.environ.get("DASHBOARD_API_KEY", "")
    if api_key:
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {api_key}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)


@app.exception_handler(404)
async def _api_not_found(request: Request, exc):
    """Return 400 (not 404) for unknown /api/ paths.

    Path traversal like /api/candles/../etc/passwd is normalised by the HTTP
    client to /api/etc/passwd before it reaches us.  That path has no route,
    so without this handler it would return 404, leaking route topology.
    Returning 400 for any unrecognised /api/ sub-path is also a real security
    improvement: attackers cannot enumerate routes by probing for 404 vs 405.
    Handlers that deliberately return 404 (e.g. 'no snapshots found') use
    JSONResponse directly and are NOT caught by this exception handler.
    """
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "bad request"}, status_code=400)
    return JSONResponse({"detail": "Not Found"}, status_code=404)


# Add bot directory to path so we can import trader + config
sys.path.insert(0, str(Path(__file__).parent))


_STRATEGY_INTERVAL = {
    "S1": "3m", "S2": "1D", "S3": "15m", "S4": "1D", "S5": "15m",
}
_TRADE_EVENT_ORDER = ["open", "scale_in", "partial", "close"]

try:
    from trader import PRODUCT_TYPE
except Exception:
    PRODUCT_TYPE = "USDT-FUTURES"


def _safe_float(val):
    try:
        return float(val) if val is not None and val != "" else None
    except (ValueError, TypeError):
        return None


def _load_csv_history(csv_path: str, limit: int = 50) -> list:
    """Load closed trades from CSV, enriched with open-row data for chart replay.

    2-pass approach:
      Pass 1 — collect OPEN, SCALE_IN, PARTIAL rows keyed by trade_id.
      Pass 2 — for each CLOSE row, look up the matching OPEN row and emit enriched dict.
    """
    if not os.path.exists(csv_path):
        return []

    open_rows    = {}   # trade_id → {entry, sl, tp, open_at, side, strategy, symbol, interval}
    event_rows   = {}   # trade_id → [{type, price, ts}, ...]
    partial_rows = {}   # trade_id → raw CSV row (for standalone display when no CLOSE exists)
    closed_tids  = set()  # trade_ids that have a CLOSE row
    rows = []

    try:
        with open(csv_path, newline="") as f:
            all_rows = list(csv.DictReader(f))

        # ── Pass 1: index OPEN / SCALE_IN / PARTIAL rows ─────────────── #
        for r in all_rows:
            action = r.get("action") or ""
            tid    = r.get("trade_id") or ""
            if not tid:
                continue

            if any(action.endswith(sfx) for sfx in ("_LONG", "_SHORT")):
                strategy = action.split("_")[0]
                open_rows[tid] = {
                    "entry":    _safe_float(r.get("entry")),
                    "sl":       _safe_float(r.get("sl")),
                    "tp":       _safe_float(r.get("tp")),
                    "box_low":  _safe_float(r.get("box_low")),
                    "box_high": _safe_float(r.get("box_high")),
                    "open_at":  r.get("timestamp", ""),
                    "side":     r.get("side", ""),
                    "strategy": strategy,
                    "symbol":   r.get("symbol", ""),
                    "interval": _STRATEGY_INTERVAL.get(strategy, "15m"),
                    **{k: r.get(k, "") for k in (
                        "snap_rsi", "snap_adx", "snap_htf", "snap_coil",
                        "snap_box_range_pct", "snap_sentiment", "snap_daily_rsi",
                        "snap_entry_trigger", "snap_sl", "snap_rr", "snap_rsi_peak",
                        "snap_spike_body_pct", "snap_rsi_div", "snap_rsi_div_str",
                        "snap_s5_ob_low", "snap_s5_ob_high", "snap_s5_tp",
                        "snap_sr_clearance_pct",
                    )},
                }
                continue

            if "_SCALE_IN" in action:
                event_rows.setdefault(tid, []).append({
                    "type":  "scale_in",
                    "price": _safe_float(r.get("entry")),
                    "ts":    r.get("timestamp", ""),
                })
                continue

            if "_PARTIAL" in action:
                event_rows.setdefault(tid, []).append({
                    "type":  "partial",
                    "price": _safe_float(r.get("exit_price")),
                    "ts":    r.get("timestamp", ""),
                })
                partial_rows[tid] = r  # keep raw row for possible standalone display

        # ── Pass 2: enrich CLOSE rows ────────────────────────────────── #
        for r in all_rows:
            action = r.get("action") or ""
            if "_CLOSE" not in action:
                continue

            tid      = r.get("trade_id") or ""
            closed_tids.add(tid)
            pnl      = _safe_float(r.get("pnl")) or 0.0
            partial_pnl = _safe_float((partial_rows.get(tid) or {}).get("pnl")) or 0.0
            pnl     += partial_pnl
            open_row = open_rows.get(tid, {})

            rows.append({
                # existing fields (unchanged contract for dashboard rendering)
                "trade_id":    tid,
                "symbol":      r.get("symbol") or open_row.get("symbol", ""),
                "side":        r.get("side") or open_row.get("side", ""),
                "pnl":         round(pnl, 4),
                "pnl_pct":     r.get("pnl_pct", ""),
                "result":      r.get("result", ""),
                "exit_reason": r.get("exit_reason", ""),
                "strategy":    action.split("_")[0],
                "closed_at":   r.get("timestamp", ""),
                # new fields for chart replay
                "entry":       open_row.get("entry"),
                "sl":          open_row.get("sl"),
                "tp":          open_row.get("tp"),
                "box_low":     open_row.get("box_low"),
                "box_high":    open_row.get("box_high"),
                "exit_price":  _safe_float(r.get("exit_price")),
                "open_at":     open_row.get("open_at"),
                "interval":    open_row.get("interval"),
                "events":      event_rows.get(tid, []),
                **{k: open_row.get(k, "") for k in (
                    "snap_rsi", "snap_adx", "snap_htf", "snap_coil",
                    "snap_box_range_pct", "snap_sentiment", "snap_daily_rsi",
                    "snap_entry_trigger", "snap_sl", "snap_rr", "snap_rsi_peak",
                    "snap_spike_body_pct", "snap_rsi_div", "snap_rsi_div_str",
                    "snap_s5_ob_low", "snap_s5_ob_high", "snap_s5_tp",
                    "snap_sr_clearance_pct",
                )},
            })

        # ── Pass 3: emit PARTIAL rows as standalone entries (only when trade still open) ── #
        for tid, r in partial_rows.items():
            if tid in closed_tids:
                continue  # already shown as overlay on the CLOSE row
            action   = r.get("action") or ""
            pnl      = _safe_float(r.get("pnl")) or 0.0
            open_row = open_rows.get(tid, {})
            rows.append({
                "trade_id":    tid,
                "symbol":      r.get("symbol") or open_row.get("symbol", ""),
                "side":        r.get("side") or open_row.get("side", ""),
                "pnl":         round(pnl, 4),
                "pnl_pct":     r.get("pnl_pct", ""),
                "result":      "PARTIAL",
                "exit_reason": r.get("exit_reason", ""),
                "strategy":    action.split("_")[0],
                "closed_at":   r.get("timestamp", ""),
                "exit_price":  _safe_float(r.get("exit_price")),
                "entry":       open_row.get("entry"),
                "open_at":     open_row.get("open_at"),
                "interval":    open_row.get("interval"),
                "events":      [],
            })

    except Exception:
        pass

    return list(reversed(rows))[:limit]


@app.get("/api/state")
def get_state():
    if not os.path.exists(STATE_FILE):
        return JSONResponse({"status": "STOPPED", "error": "state.json not found — is bot.py running?"})
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
        # Always use CSV as authoritative trade history (survives restarts)
        csv_path = STATE_FILE.replace("state_paper.json", "trades_paper.csv").replace("state.json", "trades.csv")
        csv_history = _load_csv_history(csv_path)
        if csv_history:
            state["trade_history"] = csv_history
        # Inject live enabled/disabled flags from config so the dashboard can
        # hide tabs for strategies that are turned off.
        try:
            import config_s1, config_s2, config_s3, config_s4, config_s5
            state["strategy_enabled"] = {
                "S1": bool(config_s1.S1_ENABLED),
                "S2": bool(config_s2.S2_ENABLED),
                "S3": bool(config_s3.S3_ENABLED),
                "S4": bool(config_s4.S4_ENABLED),
                "S5": bool(config_s5.S5_ENABLED),
            }
        except Exception:
            pass
        try:
            import config
            state["max_concurrent"] = int(config.MAX_CONCURRENT_TRADES)
        except Exception:
            pass
        return JSONResponse(state)
    except Exception as e:
        return JSONResponse({"status": "ERROR", "error": str(e)})


def _json_depth(obj, current=0):
    """Return the maximum nesting depth of a JSON-compatible object."""
    if current > 20:
        return current
    if isinstance(obj, dict):
        if not obj:
            return current
        return max(_json_depth(v, current + 1) for v in obj.values())
    if isinstance(obj, list):
        if not obj:
            return current
        return max(_json_depth(item, current + 1) for item in obj)
    return current


@app.post("/api/chat")
@limiter.limit("10/minute")
async def chat(request: Request):
    """Stream a Claude trade analysis response via SSE."""
    import claude_analyst
    body      = await request.json()

    # --- Payload size validation (prevent Anthropic API billing abuse) ---
    if len(json.dumps(body.get("messages", []))) > 10_000:
        return JSONResponse({"error": "messages payload too large"}, status_code=413)
    if len(json.dumps(body.get("trade", {}))) > 5_000:
        return JSONResponse({"error": "trade payload too large"}, status_code=413)
    if _json_depth(body) > 20:
        return JSONResponse({"error": "payload nesting too deep"}, status_code=400)
    # --- End payload validation ---

    trade     = body.get("trade", {})
    messages  = body.get("messages", [])

    def generate():
        try:
            system = claude_analyst.build_system_prompt(trade)
            for token in claude_analyst.stream_response(system, messages):
                import json as _json
                yield f"data: {_json.dumps(token)}\n\n"
        except Exception as e:
            yield f"data: ⚠ Error: {e}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/api/candles/{symbol}")
def get_candles(symbol: str, interval: str = "3m", limit: int = 80):
    """
    Returns OHLCV candles + consolidation box + trigger lines.
    For 3m (S1): uses RSI-zone consolidation detection.
    For 1D (S2): uses daily RSI + S2 consolidation + entry trigger logic.
    For 15m (S3): uses Slow Stochastics + MACD + S3 pullback evaluation.
    """
    if not re.fullmatch(r"^[A-Z0-9]{2,20}$", symbol):
        return JSONResponse({"error": "invalid symbol"}, status_code=400)
    try:
        import trader as tr
        import config
        import config_s1
        from strategy import detect_consolidation, calculate_rsi, evaluate_s2, evaluate_s4, find_nearest_resistance, find_nearest_support

        # Fetch extra candles for indicator warmup (EMA/ADX need history to converge)
        # Then trim to display_limit for the chart
        is_daily      = interval in ("1D", "1d")
        is_15m        = interval == "15m"
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

        # Bitget 1D candle opens at 16:00 UTC = 00:00 UTC+8.
        # Add 8h to 1D timestamps so lightweight-charts places candles
        # on the correct UTC+8 date (matching TradingView).
        # All series (candles, RSI, EMA, ADX) use the same offset.
        def ts(row_ts):
            return int(row_ts) // 1000

        candles = [
            {
                "time":  ts(row["ts"]),
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
            t = ts(df["ts"].iloc[i])
            if v != v:
                continue
            rsi_series.append({"time": t, "value": round(float(v), 2)})

        from strategy import calculate_ema, calculate_adx as _calc_adx
        ema10_full = calculate_ema(closes_full, 10).tail(display_limit)
        ema20_full = calculate_ema(closes_full, 20).tail(display_limit)
        ema10 = [
            {"time": ts(df["ts"].iloc[i]), "value": round(float(v), 8)}
            for i, v in enumerate(ema10_full) if not (v != v)
        ]
        ema20 = [
            {"time": ts(df["ts"].iloc[i]), "value": round(float(v), 8)}
            for i, v in enumerate(ema20_full) if not (v != v)
        ]

        adx_result   = _calc_adx(df_full, period=14)
        adx_display  = adx_result["adx"].tail(display_limit)
        pdi_display  = adx_result["plus_di"].tail(display_limit)
        mdi_display  = adx_result["minus_di"].tail(display_limit)
        adx_data = [
            {"time": ts(df["ts"].iloc[i]), "value": round(float(v), 2)}
            for i, v in enumerate(adx_display) if not (v != v)
        ]
        plus_di_data = [
            {"time": ts(df["ts"].iloc[i]), "value": round(float(v), 2)}
            for i, v in enumerate(pdi_display) if not (v != v)
        ]
        minus_di_data = [
            {"time": ts(df["ts"].iloc[i]), "value": round(float(v), 2)}
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
                    # Inside-bar check: all candles must be within mother candle's range
                    mother = df.iloc[-n - 2] if len(df) > n + 1 else None
                    if mother is not None:
                        mh = float(mother["high"])
                        ml = float(mother["low"])
                        all_inside = all(
                            float(r["high"]) <= mh * 1.02 and float(r["low"]) >= ml * 0.98
                            for _, r in window.iterrows()
                        )
                        if not all_inside:
                            continue
                    else:
                        if (wh - wl) / mid > S2_CONSOL_RANGE_PCT:
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
            rsi_thresh = config_s1.RSI_LONG_THRESH
            direction  = "LONG"  # default; show long setup
            is_coil, bh, bl = detect_consolidation(
                df, rsi_series=rsi_full, rsi_threshold=rsi_thresh, direction=direction
            )
            if bh:
                box_high       = round(bh, 8)
                box_low        = round(bl, 8)
                breakout_long  = round(bh * (1 + config_s1.BREAKOUT_BUFFER_PCT), 8)
                breakout_short = round(bl * (1 - config_s1.BREAKOUT_BUFFER_PCT), 8)

        # ── S4 indicators (daily only) ───────────────────────────── #
        s4_entry_trigger = None
        s4_sl_price      = None
        s4_rsi_peak_val  = None
        s4_base_support  = None   # open of spike candle = pre-pump base (acts as S/R support)
        if is_daily:
            try:
                _, _, entry_t, sl_p, _, rsi_pk, _, _, _ = evaluate_s4(symbol, df_full)
                if entry_t > 0:
                    s4_entry_trigger = round(entry_t, max(2, price_decimals + 1))
                if sl_p > 0:
                    s4_sl_price = round(sl_p, max(2, price_decimals + 1))
                if rsi_pk > 0:
                    s4_rsi_peak_val = round(rsi_pk, 1)
            except Exception:
                pass
            # Pre-pump base support: open of the biggest spike candle in lookback
            try:
                from config_s4 import S4_BIG_CANDLE_BODY_PCT, S4_BIG_CANDLE_LOOKBACK
                lookback_df = df_full.iloc[-(S4_BIG_CANDLE_LOOKBACK + 1):-1]
                best_bp, best_open = 0.0, None
                for _, row in lookback_df.iterrows():
                    o, c = float(row["open"]), float(row["close"])
                    bp = abs(c - o) / o if o else 0
                    if bp >= S4_BIG_CANDLE_BODY_PCT and bp > best_bp:
                        best_bp  = bp
                        best_open = o
                if best_open:
                    s4_base_support = round(best_open, max(2, price_decimals))
            except Exception:
                pass

        # ── S3 indicators (15m only) ──────────────────────────── #
        stoch_k_series    = []
        stoch_d_series    = []
        macd_line_series  = []
        macd_sig_series   = []
        macd_hist_series  = []
        s3_entry_trigger  = None
        s3_sl_price       = None
        s3_stoch_last     = None
        s3_macd_last      = None
        s3_signal_live    = "HOLD"

        # ── S5 indicators (15m only) ──────────────────────────── #
        s5_ob_low_val    = None
        s5_ob_high_val   = None
        s5_entry_trigger = None
        s5_sl_price      = None
        s5_tp_price      = None

        if is_15m:
            import numpy as _np
            from strategy import calculate_stoch, calculate_macd, evaluate_s3, evaluate_s5
            from config_s3 import (
                S3_STOCH_K_PERIOD, S3_STOCH_D_SMOOTH,
                S3_MACD_FAST, S3_MACD_SLOW, S3_MACD_SIGNAL,
            )

            slow_k_full, slow_d_full = calculate_stoch(
                df_full, S3_STOCH_K_PERIOD, S3_STOCH_D_SMOOTH
            )
            ml_full, ms_full, mh_full = calculate_macd(
                closes_full, S3_MACD_FAST, S3_MACD_SLOW, S3_MACD_SIGNAL
            )

            sk_disp = slow_k_full.tail(display_limit)
            sd_disp = slow_d_full.tail(display_limit)
            ml_disp = ml_full.tail(display_limit)
            ms_disp = ms_full.tail(display_limit)
            mh_disp = mh_full.tail(display_limit)

            for i, v in enumerate(sk_disp):
                if v == v:  # not NaN
                    stoch_k_series.append({"time": ts(df["ts"].iloc[i]), "value": round(float(v), 2)})
            for i, v in enumerate(sd_disp):
                if v == v:
                    stoch_d_series.append({"time": ts(df["ts"].iloc[i]), "value": round(float(v), 2)})
            for i, v in enumerate(ml_disp):
                if v == v:
                    macd_line_series.append({"time": ts(df["ts"].iloc[i]), "value": round(float(v), 8)})
            for i, v in enumerate(ms_disp):
                if v == v:
                    macd_sig_series.append({"time": ts(df["ts"].iloc[i]), "value": round(float(v), 8)})
            for i, v in enumerate(mh_disp):
                if v == v:
                    color = "rgba(0,214,143,0.65)" if v >= 0 else "rgba(255,77,106,0.65)"
                    macd_hist_series.append({
                        "time": ts(df["ts"].iloc[i]),
                        "value": round(float(v), 8),
                        "color": color,
                    })

            if stoch_k_series:
                s3_stoch_last = stoch_k_series[-1]["value"]
            if macd_hist_series:
                s3_macd_last = macd_hist_series[-1]["value"]

            # Run S3 evaluator to get entry trigger + SL levels
            try:
                _s3sig, _, entry_t, sl_p, _ = evaluate_s3(symbol, df_full)
                s3_signal_live = _s3sig
                if entry_t > 0:
                    s3_entry_trigger = round(entry_t, max(2, price_decimals + 1))
                if sl_p > 0:
                    s3_sl_price = round(sl_p, max(2, price_decimals + 1))
            except Exception:
                pass

            # ── S5 indicators (15m OB zone) ───────────────────────── #
            try:
                import config_s5 as _cs5
                import trader as _tr5
                _htf_df = _tr5.get_candles(symbol, "1H", limit=15)
                _daily_df = _tr5.get_candles(symbol, "1D", limit=200)
                if not _htf_df.empty and not _daily_df.empty:
                    _, et, sl, tp, obl, obh, _ = evaluate_s5(symbol, _daily_df, _htf_df, df_full, "BULLISH")
                    if obh > 0:
                        s5_ob_low_val  = round(obl, max(2, price_decimals + 1))
                        s5_ob_high_val = round(obh, max(2, price_decimals + 1))
                    if et > 0:
                        s5_entry_trigger = round(et, max(2, price_decimals + 1))
                    if sl > 0:
                        s5_sl_price = round(sl, max(2, price_decimals + 1))
                    if tp > 0:
                        s5_tp_price = round(tp, max(2, price_decimals + 1))
            except Exception:
                pass

        # Current mark price
        try:
            mark = tr.get_mark_price(symbol)
        except Exception:
            mark = float(df["close"].iloc[-1])

        # Nearest S/R from the chart's own timeframe data
        try:
            sr_resistance = find_nearest_resistance(df_full, mark)
            sr_support    = find_nearest_support(df_full, mark)
        except Exception:
            sr_resistance = None
            sr_support    = None
        # For S2/S3: use spike/peak-adjusted resistance stored by bot
        # (avoids drawing R at the spike/pre-pullback high that created the signal)
        try:
            with open(STATE_FILE, "r") as _sf:
                _ps = json.load(_sf).get("pair_states", {}).get(symbol, {})
            if interval == "1D" and (_ps.get("s2_coiling") or _ps.get("s2_signal", "HOLD") != "HOLD"):
                # S2 setup active — always use spike-adjusted value (None = no R line drawn)
                sr_resistance = _ps.get("s2_sr_resistance_price")
                # Support = coil box bottom (SL level), not historical swing low
                if _ps.get("s2_box_low"):
                    sr_support = _ps["s2_box_low"]
            elif interval == "15m" and "s3_sr_resistance_price" in _ps:
                # S3 setup active — always use peak-adjusted value (None = no R line drawn)
                sr_resistance = _ps.get("s3_sr_resistance_price")
        except Exception:
            pass

        # For 1H: return previous candle high for HTF break visualisation
        prev_high = None
        if interval == "1H" and len(df) >= 2:
            prev_high = round(float(df["high"].iloc[-2]), price_decimals)

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
            "prev_high":        prev_high,
            "price_decimals":   price_decimals,
            "rsi_long_thresh":  config_s1.RSI_LONG_THRESH,
            "rsi_short_thresh": config_s1.RSI_SHORT_THRESH,
            # S3 — 15m indicators
            "stoch_k":          stoch_k_series,
            "stoch_d":          stoch_d_series,
            "macd_line":        macd_line_series,
            "macd_signal":      macd_sig_series,
            "macd_hist":        macd_hist_series,
            "s3_entry_trigger": s3_entry_trigger,
            "s3_signal_live":   s3_signal_live,
            "s3_sl_price":      s3_sl_price,
            "s3_stoch_last":    s3_stoch_last,
            "s3_macd_last":     s3_macd_last,
            # S4 — daily short signals
            "s4_entry_trigger": s4_entry_trigger,
            "s4_sl_price":      s4_sl_price,
            "s4_rsi_peak":      s4_rsi_peak_val,
            "s4_base_support":  s4_base_support,
            # S5 — SMC Order Block
            "s5_ob_low":        s5_ob_low_val,
            "s5_ob_high":       s5_ob_high_val,
            "s5_entry_trigger": s5_entry_trigger,
            "s5_sl_price":      s5_sl_price,
            "s5_tp_price":      s5_tp_price,
            # S/R levels from chart timeframe
            "sr_resistance":    round(sr_resistance, max(2, price_decimals)) if sr_resistance else None,
            "sr_support":       round(sr_support,    max(2, price_decimals)) if sr_support    else None,
        })

    except Exception:
        return JSONResponse({"error": "internal server error"}, status_code=500)


@app.get("/api/entry-chart")
def get_entry_chart(
    symbol:             str,
    open_at:            str,
    strategy:           str   = "S3",
    entry:              float = 0.0,
    sl:                 float = 0.0,
    snap_sl:            str   = "",
    tp:                 float = 0.0,
    snap_entry_trigger: str   = "",
    box_low:            str   = "",
    box_high:           str   = "",
    snap_s5_ob_low:     str   = "",
    snap_s5_ob_high:    str   = "",
    trade_id:           str   = "",
):
    """
    Returns 25 candles centred around entry (20 before + 5 after) plus
    strategy-specific highlight timestamps / zone levels.
    """
    try:
        import numpy as np
        import pandas as pd
        import bitget_client as bc
        import snapshot as _snap
        from datetime import datetime
        from strategy import calculate_stoch

        interval = _STRATEGY_INTERVAL.get(strategy, "15m")
        interval_ms = {"3m": 180_000, "15m": 900_000, "1D": 86_400_000}.get(interval, 900_000)

        # Parse open_at ISO → ms
        open_ts_ms = int(datetime.fromisoformat(open_at).timestamp() * 1000)

        # ── Snapshot fast-path ────────────────────────────────────── #
        if trade_id:
            snap = _snap.load_snapshot(trade_id, "open")
            if snap:
                _df = pd.DataFrame(snap["candles"])
                _df = _df.rename(columns={"t": "ts"})
                entry_idx = len(_df) - 1
                for i, row in _df.iterrows():
                    if int(row["ts"]) >= open_ts_ms:
                        entry_idx = i
                        break
                start = max(0, entry_idx - 20)
                end   = min(len(_df), entry_idx + 5)
                view  = _df.iloc[start:end].reset_index(drop=True)
                candles_out = [
                    {"t": int(r["ts"]), "o": r["o"], "h": r["h"],
                     "l": r["l"],  "c": r["c"], "v": r["v"]}
                    for _, r in view.iterrows()
                ]
                entry_ts = int(_df.iloc[entry_idx]["ts"])
                return JSONResponse({
                    "candles":      candles_out,
                    "entry_ts":     entry_ts,
                    "highlights":   {},
                    "from_snapshot": True,
                })

        end_ts_ms  = open_ts_ms + 10 * interval_ms
        fetch_limit = 300 if interval != "1D" else 60
        granularity = "1Dutc" if interval == "1D" else interval

        raw = bc.get_public(
            "/api/v2/mix/market/candles",
            params={
                "symbol":      symbol,
                "productType": PRODUCT_TYPE,
                "granularity": granularity,
                "limit":       str(fetch_limit),
                "endTime":     str(end_ts_ms),
            },
        )
        rows = raw.get("data", [])
        if not rows:
            return JSONResponse({"error": "No candle data returned from exchange"})

        df = pd.DataFrame(rows, columns=["ts","open","high","low","close","vol","qvol"])
        df = df.astype({"ts": int, "open": float, "high": float,
                        "low": float, "close": float, "vol": float})
        df = df.sort_values("ts").reset_index(drop=True)

        # Find index of entry candle (first candle whose ts >= open_at)
        entry_idx = len(df) - 1
        for i, row in df.iterrows():
            if int(row["ts"]) >= open_ts_ms:
                entry_idx = i
                break

        # Trim to 25-candle window: 20 before + entry + 4 after
        start = max(0, entry_idx - 20)
        end   = min(len(df), entry_idx + 5)
        view  = df.iloc[start:end].reset_index(drop=True)

        candles = [
            {"t": int(r["ts"]), "o": r["open"], "h": r["high"],
             "l": r["low"],  "c": r["close"], "v": r["vol"]}
            for _, r in view.iterrows()
        ]
        entry_ts = int(df.iloc[entry_idx]["ts"])

        # ── Highlights ────────────────────────────────────────── #
        highlights: dict = {}

        if strategy == "S3":
            work = df.iloc[: entry_idx + 1].reset_index(drop=True)
            if len(work) >= 10:
                slow_k, _ = calculate_stoch(work, 5, 3)
                lookback8 = slow_k.iloc[-9:-1]
                os_pos = [i for i, v in enumerate(lookback8)
                          if not np.isnan(v) and v < 30]
                if os_pos:
                    last_os  = -(8 + 1) + os_pos[-1]
                    first_os = -(8 + 1) + os_pos[0]
                    after_os = work.iloc[last_os + 1: -1].reset_index(drop=True)
                    last_green = None
                    lg_idx = None
                    for j, (_, row) in enumerate(after_os.iloc[::-1].iterrows()):
                        if float(row["close"]) > float(row["open"]):
                            last_green = row
                            lg_idx = len(after_os) - 1 - j
                            break
                    if last_green is not None:
                        highlights["last_green_ts"] = int(last_green["ts"])
                        # Last red candle before the uptick
                        found_red = False
                        if lg_idx is not None:
                            for j in range(lg_idx - 1, -1, -1):
                                r2 = after_os.iloc[j]
                                if float(r2["close"]) < float(r2["open"]):
                                    highlights["last_red_ts"] = int(r2["ts"])
                                    found_red = True
                                    break
                        if not found_red:
                            # Fallback: last red in oversold period
                            os_period = work.iloc[first_os: last_os + 1]
                            for _, r2 in os_period.iloc[::-1].iterrows():
                                if float(r2["close"]) < float(r2["open"]):
                                    highlights["last_red_ts"] = int(r2["ts"])
                                    break

        elif strategy in ("S2", "S4"):
            # Spike = most recent qualifying big candle (body >= 20%) before entry
            # Matches strategy logic: any candle >= S2_BIG_CANDLE_BODY_PCT in last 30 days
            BIG_BODY_THRESH = 0.20
            lookback = df.iloc[max(0, entry_idx - 30): entry_idx]
            spike_ts = None
            for _, row in lookback.iloc[::-1].iterrows():  # newest first
                o = float(row["open"])
                body = abs(float(row["close"]) - o) / o if o > 0 else 0.0
                if body >= BIG_BODY_THRESH:
                    spike_ts = int(row["ts"])
                    break
            if spike_ts:
                highlights["spike_ts"] = spike_ts
            if strategy == "S2":
                bl, bh = _safe_float(box_low), _safe_float(box_high)
                if bl:  highlights["box_low"]  = bl
                if bh:  highlights["box_high"] = bh

        elif strategy == "S5":
            ol, oh = _safe_float(snap_s5_ob_low), _safe_float(snap_s5_ob_high)
            if ol: highlights["ob_low"]  = ol
            if oh: highlights["ob_high"] = oh

        elif strategy == "S1":
            bl, bh = _safe_float(box_low), _safe_float(box_high)
            if bl: highlights["box_low"]  = bl
            if bh: highlights["box_high"] = bh

        return JSONResponse({
            "candles":   candles,
            "entry_ts":  entry_ts,
            "highlights": highlights,
        })

    except Exception as exc:
        import traceback
        return JSONResponse({"error": str(exc), "detail": traceback.format_exc()}, status_code=200)


@app.get("/api/trade-chart")
def get_trade_chart(
    trade_id: str = "",
    side:     str   = "",
    sl:       float | None = None,
    tp:       float | None = None,
    strategy: str   = "",
):
    """
    Returns merged candle array + event list for all available snapshots of a trade.
    Candles from multiple snapshots are unioned by timestamp; later snapshot wins on overlap.
    """
    if not trade_id:
        return JSONResponse({"error": "trade_id required"}, status_code=400)

    import re as _re
    if not _re.fullmatch(r'[A-Za-z0-9_-]{1,64}', trade_id):
        return JSONResponse({"error": "trade_id required"}, status_code=400)

    import snapshot as _snap
    from datetime import datetime as _dt

    events_found = _snap.list_snapshots(trade_id)
    if not events_found:
        return JSONResponse({"error": "no snapshots found"}, status_code=404)

    candle_map: dict = {}   # t (int ms) → candle dict; later snapshot overwrites earlier
    loaded: list    = []    # [{event, snap}] in canonical order

    for event in _TRADE_EVENT_ORDER:
        if event not in events_found:
            continue
        snap = _snap.load_snapshot(trade_id, event)
        if not snap:
            continue
        for c in snap["candles"]:
            candle_map[int(c["t"])] = c
        loaded.append({"event": event, "snap": snap})

    if not loaded:
        return JSONResponse({"error": "no snapshots found"}, status_code=404)

    # Build sorted candle list
    candles = sorted(candle_map.values(), key=lambda c: int(c["t"]))
    candles = candles[-60:]
    if not candles:
        return JSONResponse({"error": "no snapshots found"}, status_code=404)
    ts_list = [int(c["t"]) for c in candles]

    # Map each event's captured_at to nearest candle index
    def _nearest_idx(captured_at: str) -> int:
        ts_ms = int(_dt.fromisoformat(captured_at).timestamp() * 1000)
        return min(range(len(ts_list)), key=lambda i: abs(ts_list[i] - ts_ms))

    events_out = []
    meta_snap = loaded[0]["snap"]
    for item in loaded:
        ev_type = item["event"]
        snap    = item["snap"]
        ev: dict = {
            "type":       ev_type,
            "candle_idx": _nearest_idx(snap["captured_at"]),
            "price":      snap["event_price"],
        }
        if ev_type == "open":
            if sl is not None:
                ev["sl"] = sl
            if tp is not None:
                ev["tp"] = tp
        events_out.append(ev)

    return JSONResponse({
        "symbol":   meta_snap["symbol"],
        "interval": meta_snap["interval"],
        "strategy": strategy,
        "side":     side,
        "candles":  candles,
        "events":   events_out,
    })


@app.get("/api/ig/state")
def get_ig_state():
    _ET = zoneinfo.ZoneInfo("America/New_York")
    now_et = datetime.now(_ET)
    h, m = now_et.hour, now_et.minute
    import config_ig as _cfg_ig
    session_active = (
        now_et.weekday() < 5 and
        (h, m) >= _cfg_ig.SESSION_START and
        (h, m) < _cfg_ig.SESSION_END
    )

    # Bot running: state file touched within last 120s
    bot_running = False
    if os.path.exists(IG_STATE_FILE):
        bot_running = (time.time() - os.path.getmtime(IG_STATE_FILE)) < 120

    # Read ig_state.json once — position, scan_signals, scan_log
    ig_state = {}
    if os.path.exists(IG_STATE_FILE):
        try:
            with open(IG_STATE_FILE) as f:
                ig_state = json.load(f)
        except Exception:
            pass

    if "position" in ig_state and "positions" not in ig_state:
        import config_ig as _cfg_ig_inst
        positions = {_cfg_ig_inst.INSTRUMENTS[0]["display_name"]: ig_state.get("position")}
    else:
        positions = ig_state.get("positions", {})
    scan_signals = ig_state.get("scan_signals", {})
    scan_log     = ig_state.get("scan_log", [])

    # Trade history
    trade_history = []
    stats = {"total": 0, "wins": 0, "total_pnl": 0.0, "win_rate": "—"}
    if os.path.exists(IG_TRADES_FILE):
        try:
            with open(IG_TRADES_FILE, newline="") as f:
                for r in csv.DictReader(f):
                    if "_CLOSE" not in (r.get("action") or ""):
                        continue
                    try:
                        pnl = float(r["pnl"]) if r.get("pnl") else None
                    except (ValueError, TypeError):
                        pnl = None
                    trade_history.append({
                        "side":        r.get("side", ""),
                        "result":      r.get("result", ""),
                        "pnl":         round(pnl, 2) if pnl is not None else None,
                        "exit_reason": r.get("exit_reason", ""),
                        "closed_at":   r.get("timestamp", ""),
                        "qty":         r.get("qty", ""),
                        "mode":        r.get("mode", ""),
                    })
            closed = [t for t in trade_history if t["pnl"] is not None]
            wins   = [t for t in closed if (t["pnl"] or 0) > 0]
            total_pnl = sum(t["pnl"] for t in closed)
            stats = {
                "total":     len(closed),
                "wins":      len(wins),
                "total_pnl": round(total_pnl, 2),
                "win_rate":  f"{round(len(wins)/len(closed)*100)}%" if closed else "—",
            }
            trade_history = list(reversed(trade_history))[:30]
        except Exception:
            pass

    return JSONResponse({
        "bot_running":    bot_running,
        "session_active": session_active,
        "et_time":        now_et.strftime("%H:%M ET"),
        "positions":      positions,
        "trade_history":  trade_history,
        "stats":          stats,
        "scan_signals":   scan_signals,
        "scan_log":       scan_log,
    })


@app.get("/", response_class=HTMLResponse)
def index():
    html = Path(__file__).parent / "dashboard.html"
    return HTMLResponse(content=html.read_text(encoding="utf-8"))


if __name__ == "__main__":
    label = " [PAPER]" if PAPER_MODE else ""
    print(f"🚀 Dashboard{label}: http://localhost:{PORT}")
    # nosec B104 — intentional: dashboard is a local LAN service, binding to all interfaces is by design
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning", server_header=False)  # nosec B104