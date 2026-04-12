"""
Pure-function aggregation module for the Dashboard → Analytics tab.

Reads trades.csv / trades_paper.csv, pairs OPEN rows with their matching
CLOSE rows via trade_id, groups by strategy, filters by time range, and
builds chart series + summary stats. No I/O beyond reading the CSV path
it is handed.
"""
from __future__ import annotations

import csv
import os
from datetime import datetime, timedelta, timezone
from typing import Literal, Union

STRATEGIES = ("S1", "S2", "S3", "S4", "S5", "S6")

STRATEGY_SNAP_FIELDS = {
    "S1": ("snap_rsi", "snap_adx", "snap_htf", "snap_coil",
           "snap_box_range_pct", "snap_sentiment"),
    "S2": ("snap_daily_rsi",),
    "S3": ("snap_entry_trigger", "snap_sl", "snap_rr"),
    "S4": ("snap_rsi_peak", "snap_spike_body_pct",
           "snap_rsi_div", "snap_rsi_div_str"),
    "S5": ("snap_s5_ob_low", "snap_s5_ob_high", "snap_s5_tp"),
    "S6": ("snap_s6_peak", "snap_s6_drop_pct", "snap_s6_rsi_at_peak"),
}

SHARED_SNAP = ("snap_sr_clearance_pct",)

COMMON_FIELDS = ("timestamp", "trade_id", "symbol", "side",
                 "entry", "exit_price", "pnl", "pnl_pct",
                 "result", "exit_reason", "leverage", "margin")

RangeSpec = Union[Literal["all", "30d", "90d"], int]


def _safe_float(v) -> float | None:
    """Coerce CSV string to float; return None on empty/invalid."""
    if v in (None, "", "None"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _open_side_fields(row: dict) -> dict:
    """Extract fields from an OPEN row that should be carried onto the close."""
    snap_keys = set()
    for keys in STRATEGY_SNAP_FIELDS.values():
        snap_keys.update(keys)
    snap_keys.update(SHARED_SNAP)
    out = {
        "entry":     _safe_float(row.get("entry")),
        "leverage":  row.get("leverage", ""),
        "margin":    row.get("margin", ""),
        "box_low":   _safe_float(row.get("box_low")),
        "box_high":  _safe_float(row.get("box_high")),
        "open_ts":   row.get("timestamp", ""),
    }
    for k in snap_keys:
        out[k] = row.get(k, "")
    return out


def load_closed_trades(csv_path: str) -> list[dict]:
    """Read CSV and return one dict per *_CLOSE row, joined with its matching OPEN.

    Rules:
      - Strategy is derived from the OPEN action prefix (e.g. "S3_LONG" -> "S3").
      - Only strategies in STRATEGIES are kept; unknown prefixes are skipped.
      - PARTIAL rows for the same trade_id have their pnl summed into the close pnl.
      - SCALE_IN rows are not counted as separate trades (same trade_id).
      - Orphan CLOSE (no matching OPEN) is skipped.
      - Orphan OPEN (no CLOSE yet — live position) is excluded.
      - Missing file returns []. Malformed pnl coerces to 0.0.
    """
    if not os.path.exists(csv_path):
        return []

    try:
        with open(csv_path, newline="") as f:
            rows = list(csv.DictReader(f))
    except (OSError, csv.Error):
        return []

    opens: dict[str, dict] = {}          # trade_id -> open-side fields + strategy
    partial_pnl: dict[str, float] = {}   # trade_id -> summed PARTIAL pnl

    # Pass 1: index OPEN, PARTIAL
    for r in rows:
        action = r.get("action") or ""
        tid = r.get("trade_id") or ""
        if not tid:
            continue

        if action.endswith("_LONG") or action.endswith("_SHORT"):
            strategy = action.split("_", 1)[0]
            if strategy not in STRATEGIES:
                continue
            opens[tid] = {
                **_open_side_fields(r),
                "strategy": strategy,
                "symbol":   r.get("symbol", ""),
                "side":     r.get("side", ""),
            }
        elif "_PARTIAL" in action:
            p = _safe_float(r.get("pnl")) or 0.0
            partial_pnl[tid] = partial_pnl.get(tid, 0.0) + p

    # Pass 2: emit one output row per CLOSE
    out: list[dict] = []
    for r in rows:
        action = r.get("action") or ""
        if "_CLOSE" not in action:
            continue
        tid = r.get("trade_id") or ""
        open_fields = opens.get(tid)
        if not open_fields:
            continue    # orphan close

        pnl = (_safe_float(r.get("pnl")) or 0.0) + partial_pnl.get(tid, 0.0)

        record = {
            "timestamp":  r.get("timestamp", ""),    # close timestamp
            "trade_id":   tid,
            "symbol":     open_fields["symbol"],
            "side":       open_fields["side"],
            "strategy":   open_fields["strategy"],
            "entry":      open_fields["entry"],
            "leverage":   open_fields["leverage"],
            "margin":     open_fields["margin"],
            "box_low":    open_fields["box_low"],
            "box_high":   open_fields["box_high"],
            "open_ts":    open_fields["open_ts"],
            "exit_price": _safe_float(r.get("exit_price")),
            "pnl":        pnl,
            "pnl_pct":    _safe_float(r.get("pnl_pct")),
            "result":     r.get("result", ""),
            "exit_reason": r.get("exit_reason", ""),
        }
        # Carry snap_* fields from the open row verbatim
        for k, v in open_fields.items():
            if k.startswith("snap_"):
                record[k] = v
        out.append(record)

    return out
