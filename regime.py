"""
regime.py — Market-regime fingerprint recorder for trade entries.

Companion to trade_dna.py. Where trade_dna records the *trend* fingerprint,
regime records the *context* a trade was opened in: time-of-day/session,
volatility regime, and BTC correlation. These are recording-only fields —
they never block a trade. The (future) filter mechanism that consumes them
is intentionally out of scope here.

Contract (same as trade_dna.snapshot): every public helper returns a flat
dict of snap_* string/number values, and returns {} on any error so a trade
is never blocked by an infrastructure failure.
"""
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# ── Philippine time = UTC+8 (no DST) ───────────────────────────────────── #
_PH_TZ = timezone(timedelta(hours=8))

# ── Session boundaries in PH local hours [start, end) ──────────────────── #
# Derived from major-market opens converted to PH time:
#   Tokyo open  00:00 UTC = 08:00 PH | London open 08:00 UTC = 16:00 PH
#   NY open     13:30 UTC = 21:30 PH | NY close    21:00 UTC = 05:00 PH
_SESSIONS = [
    ("ASIA",   8, 16),
    ("LONDON", 16, 21),
    ("NY",     21, 24),   # NY also wraps into 0–5 (handled below)
]

# ── BTC 24h-change regime buckets (percent) ────────────────────────────── #
_BTC_RISK_ON  = 1.5
_BTC_RISK_OFF = -1.5

# Volatility window: how many trailing bars define the ATR percentile / vol avg.
_VOL_WINDOW = 50
_VOL_AVG_BARS = 20
_MIN_BARS = 20


# ── Public API ─────────────────────────────────────────────────────────── #

def time_fields(iso_ts: str) -> dict:
    """
    Derive session / hour / day-of-week from an ISO-8601 timestamp.

    Returns snap_session ("ASIA"/"LONDON"/"NY"/"OFF"), snap_hour_ph (0-23),
    snap_dow (0=Mon..6=Sun). Returns {} on parse error.
    """
    try:
        dt = datetime.fromisoformat(iso_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        ph = dt.astimezone(_PH_TZ)
        h = ph.hour
        session = "OFF"
        if 0 <= h < 5:
            session = "NY"           # NY session wraps past PH midnight
        else:
            for name, start, end in _SESSIONS:
                if start <= h < end:
                    session = name
                    break
        return {
            "snap_session": session,
            "snap_hour_ph": h,
            "snap_dow":     ph.weekday(),
        }
    except Exception as exc:
        logger.warning("regime.time_fields error for %r — skipping: %s", iso_ts, exc)
        return {}


def btc_regime(btc_change: float | None) -> str:
    """Bucket a BTC 24h percent-change into RISK_ON / FLAT / RISK_OFF / ''."""
    if btc_change is None:
        return ""
    try:
        v = float(btc_change)
    except (TypeError, ValueError):
        return ""
    if v >= _BTC_RISK_ON:
        return "RISK_ON"
    if v <= _BTC_RISK_OFF:
        return "RISK_OFF"
    return "FLAT"


def volatility_fields(ltf_df) -> dict:
    """
    Compute volatility/volume regime from the entry-timeframe candles.

    Returns snap_atr_pct (ATR as % of price), snap_atr_pctile (percentile rank
    of the current ATR within the trailing window, 0-100), and snap_vol_vs_avg
    (last bar volume / trailing average volume). Returns {} if df is missing,
    empty, or too short.
    """
    try:
        if ltf_df is None or getattr(ltf_df, "empty", True) or len(ltf_df) < _MIN_BARS:
            return {}
        from indicators import calculate_atr
        atr = calculate_atr(ltf_df)
        atr_now = float(atr.iloc[-1])
        price = float(ltf_df["close"].astype(float).iloc[-1])
        out = {}
        if price > 0:
            out["snap_atr_pct"] = round(atr_now / price * 100, 4)
        # ATR percentile within the trailing window
        window = atr.dropna().tail(_VOL_WINDOW)
        if len(window) > 1:
            rank = (window <= atr_now).sum() / len(window) * 100
            out["snap_atr_pctile"] = round(float(rank), 1)
        # Volume vs trailing average
        if "volume" in ltf_df.columns:
            vol = ltf_df["volume"].astype(float)
            avg = float(vol.tail(_VOL_AVG_BARS).mean())
            if avg > 0:
                out["snap_vol_vs_avg"] = round(float(vol.iloc[-1]) / avg, 3)
        return out
    except Exception as exc:
        logger.warning("regime.volatility_fields error — skipping: %s", exc)
        return {}
