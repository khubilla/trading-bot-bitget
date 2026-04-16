"""
startup_recovery.py — Shared helpers for recovering positions that filled
while the bot was stopped (crashed-before-log scenario).

Used by:
  - Bot._startup_recovery() in bot.py (automatic, at startup)
  - recover.py CLI (manual, when bot is already running)
"""
import logging
import pandas as pd

import bitget_client as bc
from config import PRODUCT_TYPE
from strategies.s5 import evaluate_s5

logger = logging.getLogger(__name__)


def fetch_candles_at(symbol: str, interval: str, limit: int, end_ms: int) -> pd.DataFrame:
    """
    Fetch up to `limit` candles ending at `end_ms` (epoch milliseconds).
    Uses Bitget's endTime query param — not exposed by trader.get_candles().
    Returns empty DataFrame on error or no data.
    """
    try:
        data = bc.get_public(
            "/api/v2/mix/market/candles",
            params={
                "symbol":      symbol,
                "productType": PRODUCT_TYPE,
                "granularity": interval,
                "limit":       str(limit),
                "endTime":     str(end_ms),
            },
        )
        rows = data.get("data", [])
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(
            rows,
            columns=["ts", "open", "high", "low", "close", "vol", "quote_vol"],
        )
        df[["open", "high", "low", "close", "vol"]] = (
            df[["open", "high", "low", "close", "vol"]].astype(float)
        )
        df["ts"] = df["ts"].astype(int)
        return df.sort_values("ts").reset_index(drop=True)
    except Exception as e:
        logger.warning(f"[{symbol}] fetch_candles_at error: {e}")
        return pd.DataFrame()


def estimate_sl_tp(
    entry: float, side: str
) -> tuple[float, float, float, float]:
    """
    Fallback SL/TP estimation when original signal data is unavailable.
    Returns (sl, tp, ob_low, ob_high).

    SHORT: SL = entry * 1.05  |  TP = entry * 0.90  |  OB ≈ entry band
    LONG:  SL = entry * 0.95  |  TP = entry * 1.10  |  OB ≈ entry band
    """
    if side == "SHORT":
        sl      = round(entry * 1.05, 8)
        tp      = round(entry * 0.90, 8)
        ob_high = round(entry,        8)
        ob_low  = round(entry * 0.99, 8)
    else:  # LONG
        sl      = round(entry * 0.95, 8)
        tp      = round(entry * 1.10, 8)
        ob_high = round(entry * 1.01, 8)
        ob_low  = round(entry,        8)
    return sl, tp, ob_low, ob_high


def attempt_s5_recovery(
    symbol: str,
    m15_df: pd.DataFrame,
    htf_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    side: str,
) -> tuple[float, float, float, float] | None:
    """
    Run evaluate_s5() on historical candles to recover OB/SL/TP.
    Returns (sl, tp, ob_low, ob_high) if a usable signal is found, else None.

    `side` must be 'SHORT' or 'LONG' — only the matching signal direction is
    accepted (prevents using a stale opposing OB as SL/TP).
    """
    try:
        _accepted = ("PENDING_SHORT", "SHORT") if side == "SHORT" else ("PENDING_LONG", "LONG")
        sig, _trigger, sl, tp, ob_low, ob_high, reason = evaluate_s5(
            symbol, daily_df, htf_df, m15_df, allowed_direction=side,
        )
        if sig in _accepted and sl > 0:
            logger.info(
                f"[{symbol}] attempt_s5_recovery: found OB | "
                f"sl={sl:.5f} tp={tp:.5f} | {reason[:60]}"
            )
            return round(sl, 8), round(tp, 8), round(ob_low, 8), round(ob_high, 8)
        logger.debug(f"[{symbol}] attempt_s5_recovery: sig={sig} — no usable signal")
        return None
    except Exception as e:
        logger.warning(f"[{symbol}] attempt_s5_recovery error: {e}")
        return None
