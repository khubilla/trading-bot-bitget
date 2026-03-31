"""
snapshot.py — Candle snapshot storage for trade lifecycle events.

Saves/loads OHLCV candle data at trade open, scale-in, partial close,
and full close so charts always reflect the exact market state at each event.

Files: data/snapshots/{trade_id}_{event}.json
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_SNAP_DIR = Path("data/snapshots")

_VALID_EVENTS = frozenset({"open", "scale_in", "partial", "close"})


def save_snapshot(
    trade_id: str,
    event: str,
    symbol: str,
    interval: str,
    candles: list[dict],
    event_price: float,
    captured_at: str | None = None,
) -> None:
    """
    Persist candle snapshot to disk. Overwrites if file already exists.

    Args:
        trade_id:    8-char hex trade identifier
        event:       one of "open", "scale_in", "partial", "close"
        symbol:      e.g. "RIVERUSDT"
        interval:    candle interval used e.g. "15m", "1D", "3m"
        candles:     list of {"t", "o", "h", "l", "c", "v"} dicts
        event_price: mark price at the moment of the event
        captured_at: ISO-8601 string; defaults to UTC now
    """
    if event not in _VALID_EVENTS:
        logger.warning(f"snapshot.save_snapshot: unknown event '{event}', skipping")
        return
    _SNAP_DIR.mkdir(parents=True, exist_ok=True)
    if captured_at is None:
        captured_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "trade_id":    trade_id,
        "symbol":      symbol,
        "interval":    interval,
        "event":       event,
        "captured_at": captured_at,
        "event_price": event_price,
        "candles":     candles,
    }
    path = _SNAP_DIR / f"{trade_id}_{event}.json"
    path.write_text(json.dumps(payload, separators=(",", ":")))
    logger.debug(f"[snapshot] saved {path.name} ({len(candles)} candles)")


def load_snapshot(trade_id: str, event: str) -> dict | None:
    """Return snapshot dict or None if file does not exist."""
    path = _SNAP_DIR / f"{trade_id}_{event}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception as e:
        logger.warning(f"[snapshot] failed to read {path.name}: {e}")
        return None


def list_snapshots(trade_id: str) -> list[str]:
    """Return list of event names that have saved snapshots for this trade_id."""
    if not _SNAP_DIR.exists():
        return []
    return [
        p.stem.split("_", 1)[1]
        for p in _SNAP_DIR.glob(f"{trade_id}_*.json")
        if "_" in p.stem
    ]
