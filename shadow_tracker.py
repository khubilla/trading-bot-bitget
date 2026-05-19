"""
shadow_tracker.py — Sentiment-Blocked "Shadow" Trade Tracker

Records the trades the bot WOULD have taken if all market-sentiment gates
were ignored, then tracks each virtual position through SL/TP using live
prices. The live bot keeps trading exactly as today — this layer is purely
observability, no real orders ever fire from here.

Two output files (paths set by bot.py via set_files()):

  • shadow_state.json     — open virtual positions, persists across restarts
  • shadow_trades.csv     — append-only audit log; columns mirror bot._TRADE_FIELDS
                            with one extra `blocked_reason` column
  • shadow_scale_ins.csv  — event log for sentiment-blocked scale-ins
                            (no PnL — just timestamps + prices)

Used by both Bitget bot (bot.py) and Bybit bot (bybit_bot.py via aliasing).
"""

from __future__ import annotations

import csv
import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()

# ── File paths (set by set_files() at startup) ──────────────────── #

_STATE_FILE: Optional[str] = None
_CSV_FILE:   Optional[str] = None
_SCALEIN_CSV_FILE: Optional[str] = None

# ── In-memory open virtual positions ────────────────────────────── #
# Schema: trade_id → {
#   strategy, symbol, side ("LONG"|"SHORT"),
#   entry, sl, tp, leverage, margin,
#   opened_at (ISO), snapshot (dict), blocked_reason (str),
# }
_open: dict[str, dict] = {}

# ── CSV schema — mirrors bot._TRADE_FIELDS + blocked_reason ─────── #
# Kept in this module rather than imported from bot.py to avoid a
# circular-import / coupling hazard. If bot._TRADE_FIELDS changes,
# update this list to match (the field is checked in tests).

_FIELDS: list[str] = [
    "timestamp", "trade_id", "action", "symbol", "side", "qty", "entry", "sl", "tp",
    "box_low", "box_high", "leverage", "margin", "tpsl_set", "strategy",
    # S1 snapshot
    "snap_rsi", "snap_adx", "snap_htf", "snap_coil", "snap_box_range_pct", "snap_sentiment",
    # S2 snapshot
    "snap_daily_rsi",
    # S3 snapshot
    "snap_entry_trigger", "snap_sl", "snap_rr",
    # S4 snapshot
    "snap_rsi_peak", "snap_spike_body_pct", "snap_rsi_div", "snap_rsi_div_str",
    # S5 snapshot
    "snap_s5_ob_low", "snap_s5_ob_high", "snap_s5_tp",
    # S6 snapshot
    "snap_s6_peak", "snap_s6_drop_pct", "snap_s6_rsi_at_peak",
    # S/R clearance
    "snap_sr_clearance_pct",
    # Trade DNA trend fingerprint
    "snap_trend_daily_ema_slope", "snap_trend_daily_price_vs_ema",
    "snap_trend_daily_rsi_bucket", "snap_trend_daily_adx_state",
    "snap_trend_h1_ema_slope", "snap_trend_h1_price_vs_ema",
    "snap_trend_m15_ema_slope", "snap_trend_m15_price_vs_ema",
    "snap_trend_m15_adx_state",
    "snap_trend_m3_price_vs_ema",
    # Close fields
    "result", "pnl", "pnl_pct", "exit_reason", "exit_price",
    # Shadow-only addition
    "blocked_reason",
]

_SCALEIN_FIELDS: list[str] = [
    "timestamp", "real_trade_id", "strategy", "symbol", "side",
    "price", "blocked_sentiment",
]


# ── Public API ──────────────────────────────────────────────────── #

def set_files(state_path: str, csv_path: str, scaleins_csv_path: str) -> None:
    """Wire the per-bot output files and rehydrate _open from disk."""
    global _STATE_FILE, _CSV_FILE, _SCALEIN_CSV_FILE
    _STATE_FILE = state_path
    _CSV_FILE = csv_path
    _SCALEIN_CSV_FILE = scaleins_csv_path
    _load_state()
    logger.info(
        f"[SHADOW] tracker wired | state={_STATE_FILE} | "
        f"csv={_CSV_FILE} | open_positions={len(_open)}"
    )


def is_enabled() -> bool:
    """True once set_files() has been called (i.e. SHADOW_TRACKING_ENABLED)."""
    return _STATE_FILE is not None and _CSV_FILE is not None


def open_count() -> int:
    return len(_open)


def open_symbols() -> list[str]:
    """Distinct symbols currently held in virtual positions."""
    return list({p["symbol"] for p in _open.values()})


def has_open(symbol: str, strategy: Optional[str] = None) -> bool:
    """True if there's already an open virtual position for symbol (optionally + strategy)."""
    for p in _open.values():
        if p["symbol"] != symbol:
            continue
        if strategy is None or p["strategy"] == strategy:
            return True
    return False


def open_virtual(
    strategy: str,
    symbol: str,
    side: str,
    entry: float,
    sl: float,
    tp: float,
    leverage: int,
    margin: float,
    snapshot: dict,
    blocked_reason: str,
) -> Optional[str]:
    """
    Record a virtual trade entry. Returns the trade_id, or None if it was
    rejected (duplicate or invalid SL/TP).
    """
    if not is_enabled():
        return None
    if side not in ("LONG", "SHORT"):
        logger.warning(f"[SHADOW] reject open: bad side={side!r}")
        return None
    if entry <= 0 or sl <= 0 or tp <= 0:
        logger.warning(f"[SHADOW] reject open: non-positive prices entry={entry} sl={sl} tp={tp}")
        return None
    # Skip duplicates — one virtual position per (strategy, symbol).
    if has_open(symbol, strategy):
        return None

    trade_id = f"SHADOW-{strategy}-{symbol}-{int(datetime.now(timezone.utc).timestamp() * 1000)}"
    now = _now_iso()
    pos = {
        "trade_id":       trade_id,
        "strategy":       strategy,
        "symbol":         symbol,
        "side":           side,
        "entry":          float(entry),
        "sl":             float(sl),
        "tp":             float(tp),
        "leverage":       int(leverage) if leverage else 1,
        "margin":         float(margin) if margin else 0.0,
        "opened_at":      now,
        "snapshot":       dict(snapshot or {}),
        "blocked_reason": blocked_reason,
    }
    with _lock:
        _open[trade_id] = pos
        _save_state()

    action = f"{strategy}_{side}"
    row = {
        **pos["snapshot"],
        "trade_id":       trade_id,
        "symbol":         symbol,
        "side":           side,
        "entry":          entry,
        "sl":             sl,
        "tp":             tp,
        "leverage":       pos["leverage"],
        "margin":         pos["margin"],
        "strategy":       strategy,
        "blocked_reason": blocked_reason,
    }
    _write_row(action, row)
    logger.info(
        f"[SHADOW][{strategy}][{symbol}] 👻 virtual {side} opened @ {entry} "
        f"SL={sl} TP={tp} | reason: {blocked_reason}"
    )
    return trade_id


def tick(price_map: dict[str, float]) -> None:
    """
    For each open virtual position, check current price vs SL/TP.
    Closes positions that hit either side and writes a CLOSE row.
    """
    if not is_enabled() or not _open:
        return

    closed_ids: list[str] = []
    for tid, pos in list(_open.items()):
        price = price_map.get(pos["symbol"])
        if price is None or price <= 0:
            continue

        side = pos["side"]
        sl   = pos["sl"]
        tp   = pos["tp"]

        exit_price: Optional[float] = None
        exit_reason: Optional[str] = None
        result: Optional[str] = None

        if side == "LONG":
            if price <= sl:
                exit_price, exit_reason, result = sl, "SL", "LOSS"
            elif price >= tp:
                exit_price, exit_reason, result = tp, "TP", "WIN"
        else:  # SHORT
            if price >= sl:
                exit_price, exit_reason, result = sl, "SL", "LOSS"
            elif price <= tp:
                exit_price, exit_reason, result = tp, "TP", "WIN"

        if exit_price is None:
            continue

        pnl, pnl_pct = _compute_pnl(
            side=side, entry=pos["entry"], exit_price=exit_price,
            leverage=pos["leverage"], margin=pos["margin"],
        )
        action = f"{pos['strategy']}_CLOSE"
        row = {
            **pos["snapshot"],
            "trade_id":       tid,
            "symbol":         pos["symbol"],
            "side":           side,
            "entry":          pos["entry"],
            "sl":             sl,
            "tp":             tp,
            "leverage":       pos["leverage"],
            "margin":         pos["margin"],
            "strategy":       pos["strategy"],
            "result":         result,
            "pnl":            round(pnl, 6),
            "pnl_pct":        round(pnl_pct, 6),
            "exit_reason":    exit_reason,
            "exit_price":     exit_price,
            "blocked_reason": pos["blocked_reason"],
        }
        _write_row(action, row)
        logger.info(
            f"[SHADOW][{pos['strategy']}][{pos['symbol']}] 👻 virtual {side} {result} "
            f"@ {exit_price} ({exit_reason}) | pnl={pnl:.4f} ({pnl_pct*100:.2f}%)"
        )
        closed_ids.append(tid)

    if closed_ids:
        with _lock:
            for tid in closed_ids:
                _open.pop(tid, None)
            _save_state()


def scale_in_event(
    strategy: str,
    symbol: str,
    side: str,
    price: float,
    real_trade_id: str,
    blocked_sentiment: str,
) -> None:
    """Log a single sentiment-blocked scale-in event. No PnL tracking."""
    if not is_enabled() or not _SCALEIN_CSV_FILE:
        return
    row = {
        "timestamp":         _now_iso(),
        "real_trade_id":     real_trade_id,
        "strategy":          strategy,
        "symbol":            symbol,
        "side":              side,
        "price":             price,
        "blocked_sentiment": blocked_sentiment,
    }
    _append_csv(_SCALEIN_CSV_FILE, _SCALEIN_FIELDS, row)
    logger.info(
        f"[SHADOW][{strategy}][{symbol}] 👻 scale-in skipped (live) — would have added "
        f"@ {price} | sentiment={blocked_sentiment}"
    )


# ── PnL formula (mirrors paper_trader._compute_pnl conceptually) ── #

def _compute_pnl(side: str, entry: float, exit_price: float,
                 leverage: int, margin: float) -> tuple[float, float]:
    """Return (pnl_usdt, pnl_pct_of_margin) for a closed virtual trade."""
    if entry <= 0:
        return 0.0, 0.0
    raw_pct = (exit_price - entry) / entry  # signed return on notional
    if side == "SHORT":
        raw_pct = -raw_pct
    pnl_pct = raw_pct * max(int(leverage or 1), 1)
    pnl = margin * pnl_pct
    return pnl, pnl_pct


# ── Persistence helpers ─────────────────────────────────────────── #

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_state() -> None:
    """Rehydrate _open from JSON on startup. Failures are tolerated (empty state)."""
    global _open
    if not _STATE_FILE or not os.path.exists(_STATE_FILE):
        _open = {}
        return
    try:
        with open(_STATE_FILE, "r") as f:
            data = json.load(f)
        if isinstance(data, dict) and "open" in data and isinstance(data["open"], dict):
            _open = data["open"]
        else:
            _open = {}
    except Exception as e:
        logger.warning(f"[SHADOW] failed to load state from {_STATE_FILE}: {e}")
        _open = {}


def _save_state() -> None:
    """Atomic write of _open + metadata to _STATE_FILE."""
    if not _STATE_FILE:
        return
    payload = {
        "version":    1,
        "updated_at": _now_iso(),
        "open":       _open,
    }
    try:
        dir_ = os.path.dirname(os.path.abspath(_STATE_FILE))
        os.makedirs(dir_, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=dir_, delete=False, suffix=".tmp") as tmp:
            json.dump(payload, tmp, indent=2, default=str)
            tmp_path = tmp.name
        os.replace(tmp_path, _STATE_FILE)
    except Exception as e:
        logger.warning(f"[SHADOW] failed to save state to {_STATE_FILE}: {e}")


def _write_row(action: str, details: dict) -> None:
    """Append a row to the main shadow_trades CSV."""
    if not _CSV_FILE:
        return
    row = {"timestamp": _now_iso(), "action": action, **details}
    _append_csv(_CSV_FILE, _FIELDS, row)


def _append_csv(path: str, fieldnames: list[str], row: dict) -> None:
    try:
        dir_ = os.path.dirname(os.path.abspath(path))
        os.makedirs(dir_, exist_ok=True)
        write_header = not os.path.exists(path)
        with open(path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore", restval="")
            if write_header:
                w.writeheader()
            w.writerow(row)
    except Exception as e:
        logger.warning(f"[SHADOW] failed to append CSV {path}: {e}")
