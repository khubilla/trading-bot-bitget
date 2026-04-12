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
