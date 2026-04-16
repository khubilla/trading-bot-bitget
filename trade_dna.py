"""
trade_dna.py — Trade fingerprint recorder and (future) pattern-match filter.

Phase 1 (current): snapshot() records trend context at entry into trades.csv.
Phase 2 (future):  lookup() replaces claude_filter.py as the approval gate.
"""
import logging
import pandas as pd

logger = logging.getLogger(__name__)

# ── Bucketing thresholds (internal constants) ──────────────────────────── #
EMA_SLOPE_THRESHOLD = 0.003   # 0.3% change over n candles = rising/falling
ADX_STATE_THRESHOLD = 3       # absolute ADX point change over n candles


def ema_slope(closes: pd.Series, period: int, n: int = 10) -> str:
    """
    Returns "rising" / "falling" / "flat" based on EMA direction over last n candles.
    Returns "" if series is too short to compute.
    """
    if len(closes) < period + n:
        return ""
    from indicators import calculate_ema
    ema = calculate_ema(closes, period)
    v_now  = float(ema.iloc[-1])
    v_prev = float(ema.iloc[-n])
    if v_prev == 0:
        return ""
    change = (v_now - v_prev) / v_prev
    if change > EMA_SLOPE_THRESHOLD:
        return "rising"
    if change < -EMA_SLOPE_THRESHOLD:
        return "falling"
    return "flat"


def price_vs_ema(price: float, ema: float) -> str:
    """Returns "above" if price > ema, else "below"."""
    return "above" if price > ema else "below"


def rsi_bucket(rsi: float) -> str:
    """Bucket RSI value into a labelled range string."""
    if rsi < 50:
        return "<50"
    if rsi < 60:
        return "50-60"
    if rsi < 65:
        return "60-65"
    if rsi < 70:
        return "65-70"
    if rsi < 75:
        return "70-75"
    if rsi < 80:
        return "75-80"
    return ">80"


def adx_state(adx_series: pd.Series, n: int = 10) -> str:
    """
    Returns "rising" / "falling" / "flat" based on ADX direction over last n candles.
    Returns "" if series is too short.
    adx_series: pre-computed ADX values as pd.Series.
    """
    if len(adx_series) < n + 1:
        return ""
    v_now  = float(adx_series.iloc[-1])
    v_prev = float(adx_series.iloc[-n])
    diff = v_now - v_prev
    if diff > ADX_STATE_THRESHOLD:
        return "rising"
    if diff < -ADX_STATE_THRESHOLD:
        return "falling"
    return "flat"


# ── Internal helpers ───────────────────────────────────────────────────── #

def _is_empty(df_or_series) -> bool:
    """Returns True if the value is None or an empty DataFrame/Series."""
    if df_or_series is None:
        return True
    if hasattr(df_or_series, "empty"):
        return df_or_series.empty
    return False


def _closes_from(df_or_series) -> pd.Series:
    """Extract close prices from a DataFrame or return the Series directly."""
    if hasattr(df_or_series, "columns"):
        return df_or_series["close"].astype(float)
    return df_or_series.astype(float)


# ── Per-strategy field computation ────────────────────────────────────── #

def _s1_fields(candles: dict) -> dict:
    out = {}
    daily = candles.get("daily")
    h1    = candles.get("h1")
    m3    = candles.get("m3")

    if not _is_empty(daily):
        from indicators import calculate_ema, calculate_adx
        closes_d = _closes_from(daily)
        ema_d    = calculate_ema(closes_d, 20)
        out["snap_trend_daily_ema_slope"]    = ema_slope(closes_d, 20)
        out["snap_trend_daily_price_vs_ema"] = price_vs_ema(float(closes_d.iloc[-1]), float(ema_d.iloc[-1]))
        if hasattr(daily, "columns") and len(daily) >= 20:
            adx_d = calculate_adx(daily)["adx"]
            out["snap_trend_daily_adx_state"] = adx_state(adx_d)
        else:
            out["snap_trend_daily_adx_state"] = ""

    if not _is_empty(h1):
        from indicators import calculate_ema
        closes_h = _closes_from(h1)
        ema_h    = calculate_ema(closes_h, 20)
        out["snap_trend_h1_ema_slope"]    = ema_slope(closes_h, 20)
        out["snap_trend_h1_price_vs_ema"] = price_vs_ema(float(closes_h.iloc[-1]), float(ema_h.iloc[-1]))

    if not _is_empty(m3):
        from indicators import calculate_ema
        closes_m3 = _closes_from(m3)
        ema_m3    = calculate_ema(closes_m3, 20)
        out["snap_trend_m3_price_vs_ema"] = price_vs_ema(float(closes_m3.iloc[-1]), float(ema_m3.iloc[-1]))

    return out


def _s2_fields(candles: dict) -> dict:
    out = {}
    daily = candles.get("daily")
    if _is_empty(daily):
        return out
    from indicators import calculate_ema, calculate_rsi
    closes_d = _closes_from(daily)
    ema_d    = calculate_ema(closes_d, 20)
    rsi_d    = calculate_rsi(closes_d)
    out["snap_trend_daily_ema_slope"]    = ema_slope(closes_d, 20)
    out["snap_trend_daily_price_vs_ema"] = price_vs_ema(float(closes_d.iloc[-1]), float(ema_d.iloc[-1]))
    out["snap_trend_daily_rsi_bucket"]   = rsi_bucket(float(rsi_d.iloc[-1]))
    return out


def _s3_fields(candles: dict) -> dict:
    out = {}
    m15 = candles.get("m15")
    if _is_empty(m15):
        return out
    from indicators import calculate_ema, calculate_adx
    closes_m15 = _closes_from(m15)
    ema_m15    = calculate_ema(closes_m15, 20)
    out["snap_trend_m15_ema_slope"]    = ema_slope(closes_m15, 20)
    out["snap_trend_m15_price_vs_ema"] = price_vs_ema(float(closes_m15.iloc[-1]), float(ema_m15.iloc[-1]))
    if hasattr(m15, "columns") and len(m15) >= 20:
        adx_m15 = calculate_adx(m15)["adx"]
        out["snap_trend_m15_adx_state"] = adx_state(adx_m15)
    else:
        out["snap_trend_m15_adx_state"] = ""
    return out


def _s4_fields(candles: dict) -> dict:
    out = {}
    daily = candles.get("daily")
    h1    = candles.get("h1")
    if _is_empty(daily):
        return out
    from indicators import calculate_ema, calculate_rsi
    closes_d = _closes_from(daily)
    ema_d    = calculate_ema(closes_d, 20)
    rsi_d    = calculate_rsi(closes_d)
    out["snap_trend_daily_ema_slope"]    = ema_slope(closes_d, 20)
    out["snap_trend_daily_price_vs_ema"] = price_vs_ema(float(closes_d.iloc[-1]), float(ema_d.iloc[-1]))
    out["snap_trend_daily_rsi_bucket"]   = rsi_bucket(float(rsi_d.iloc[-1]))
    if not _is_empty(h1):
        from indicators import calculate_ema as _ema
        closes_h = _closes_from(h1)
        ema_h    = _ema(closes_h, 20)
        out["snap_trend_h1_ema_slope"]    = ema_slope(closes_h, 20)
        out["snap_trend_h1_price_vs_ema"] = price_vs_ema(float(closes_h.iloc[-1]), float(ema_h.iloc[-1]))
    return out


def _s5_fields(candles: dict) -> dict:
    out = {}
    daily = candles.get("daily")
    h1    = candles.get("h1")
    m15   = candles.get("m15")
    if not _is_empty(daily):
        from indicators import calculate_ema
        closes_d = _closes_from(daily)
        ema_d    = calculate_ema(closes_d, 20)
        out["snap_trend_daily_ema_slope"]    = ema_slope(closes_d, 20)
        out["snap_trend_daily_price_vs_ema"] = price_vs_ema(float(closes_d.iloc[-1]), float(ema_d.iloc[-1]))
    if not _is_empty(h1):
        from indicators import calculate_ema
        closes_h = _closes_from(h1)
        ema_h    = calculate_ema(closes_h, 20)
        out["snap_trend_h1_ema_slope"]    = ema_slope(closes_h, 20)
        out["snap_trend_h1_price_vs_ema"] = price_vs_ema(float(closes_h.iloc[-1]), float(ema_h.iloc[-1]))
    if not _is_empty(m15):
        from indicators import calculate_ema
        closes_m15 = _closes_from(m15)
        ema_m15    = calculate_ema(closes_m15, 20)
        out["snap_trend_m15_ema_slope"]    = ema_slope(closes_m15, 20)
        out["snap_trend_m15_price_vs_ema"] = price_vs_ema(float(closes_m15.iloc[-1]), float(ema_m15.iloc[-1]))
    return out


def _s6_fields(candles: dict) -> dict:
    out = {}
    daily = candles.get("daily")
    if _is_empty(daily):
        return out
    from indicators import calculate_ema, calculate_rsi
    closes_d = _closes_from(daily)
    ema_d    = calculate_ema(closes_d, 20)
    rsi_d    = calculate_rsi(closes_d)
    out["snap_trend_daily_ema_slope"]    = ema_slope(closes_d, 20)
    out["snap_trend_daily_price_vs_ema"] = price_vs_ema(float(closes_d.iloc[-1]), float(ema_d.iloc[-1]))
    out["snap_trend_daily_rsi_bucket"]   = rsi_bucket(float(rsi_d.iloc[-1]))
    return out


_STRATEGY_HANDLERS = {
    "S1": _s1_fields,
    "S2": _s2_fields,
    "S3": _s3_fields,
    "S4": _s4_fields,
    "S5": _s5_fields,
    "S6": _s6_fields,
}


# ── Public API ─────────────────────────────────────────────────────────── #

def snapshot(strategy: str, symbol: str, candles: dict) -> dict:
    """
    Compute trend fingerprint fields for the given strategy at entry time.

    candles: dict with keys "daily" / "h1" / "m15" / "m3".
             Values are pd.DataFrame (OHLCV) or pd.Series (closes).
             Pass only the timeframes available — missing keys are skipped.

    Returns flat dict of snap_trend_* keys → bucketed string values.
    On any error: logs warning and returns {} so trades are never blocked.
    """
    handler = _STRATEGY_HANDLERS.get(strategy)
    if handler is None:
        logger.warning("trade_dna.snapshot: unknown strategy %s — skipping", strategy)
        return {}
    try:
        return handler(candles)
    except Exception as exc:
        logger.warning(
            "trade_dna.snapshot error for %s %s — skipping fingerprint: %s",
            strategy, symbol, exc,
        )
        return {}


def lookup(strategy: str, symbol: str, fingerprint: dict) -> dict:
    """
    Future drop-in replacement for claude_approve().
    Returns {"approved": bool, "reason": str, "matches": int, "win_rate": float}.
    Not yet implemented — raises NotImplementedError.
    """
    raise NotImplementedError(
        "trade_dna.lookup() is not yet implemented. "
        "Enable claude_filter or disable CLAUDE_FILTER_ENABLED instead."
    )
