"""
Strategy 7 — Post-Pump 1H Darvas Breakdown Short.

Setup gates mirror S4 (spike body ≥ 20% within last 30D, RSI peak ≥ 75 within
last 10D, RSI still hot ≥ 70). Entry trigger is a confirmed 1H close below a
locked Darvas-box low formed within the current UTC day.

Sentiment gate: BEARISH only (gated upstream in bot.py).
"""

import logging
from typing import Literal

import pandas as pd

logger = logging.getLogger(__name__)
Signal = Literal["LONG", "SHORT", "HOLD", "PENDING_LONG", "PENDING_SHORT"]

SNAPSHOT_INTERVAL = "1D"


def _utcnow() -> pd.Timestamp:
    """Wrapper for monkeypatch-friendly current-UTC timestamp."""
    return pd.Timestamp.utcnow()


def today_h1_slice(h1_df: pd.DataFrame) -> pd.DataFrame:
    """Closed 1H candles since the most recent UTC midnight (forming hour excluded)."""
    if h1_df.empty:
        return h1_df
    today_utc = _utcnow().floor("1D")
    if today_utc.tzinfo is None:
        today_utc = today_utc.tz_localize("UTC")
    mask = h1_df.index >= today_utc
    return h1_df[mask].iloc[:-1]


def detect_darvas_box(
    h1_slice: pd.DataFrame,
    confirm: int = 2,
) -> tuple[bool, float, float, int, int, str]:
    """
    Walk the 1H slice forward and lock a top-box high then a low-box low using
    classic Darvas mechanics: each new lower-low / higher-high resets the
    confirmation counter; the box locks once `confirm` consecutive candles
    hold above/below the establishing candle.

    Returns (locked, top_high, low_low, top_idx, low_idx, reason).
    """
    min_needed = 2 * confirm + 2
    if len(h1_slice) < min_needed:
        return (False, 0.0, 0.0, -1, -1,
                f"Need ≥ {min_needed} 1H candles since UTC midnight (have {len(h1_slice)})")

    rows = list(h1_slice.itertuples())

    # --- top-box pass ---
    top_high, top_idx, conf, top_locked = float("-inf"), -1, 0, False
    for i, row in enumerate(rows):
        if row.high > top_high:
            top_high, top_idx, conf = float(row.high), i, 0
        else:
            conf += 1
            if conf >= confirm:
                top_locked = True
                break
    if not top_locked:
        return (False, top_high, 0.0, top_idx, -1,
                "Top box not yet confirmed (running high still pushing)")

    # --- low-box pass over rows after top_idx ---
    low_low, low_off, conf, low_locked = float("+inf"), -1, 0, False
    for j, row in enumerate(rows[top_idx + 1:]):
        if row.low < low_low:
            low_low, low_off, conf = float(row.low), j, 0
        else:
            conf += 1
            if conf >= confirm:
                low_locked = True
                break
    if not low_locked:
        return (False, top_high, low_low, top_idx, -1,
                "Low box not yet confirmed (running low still falling)")

    if low_low >= top_high:
        return (False, top_high, low_low, top_idx, top_idx + 1 + low_off,
                f"Sanity: low_low {low_low} >= top_high {top_high}")

    low_idx = top_idx + 1 + low_off
    return (True, top_high, low_low, top_idx, low_idx,
            f"Darvas box ✅ top={top_high} low={low_low}")
