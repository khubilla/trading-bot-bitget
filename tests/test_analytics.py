"""Tests for analytics.py — trade history aggregation module."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import analytics


def test_module_imports_and_exposes_constants():
    assert analytics.STRATEGIES == ("S1", "S2", "S3", "S4", "S5", "S6")
    assert "snap_rsi" in analytics.STRATEGY_SNAP_FIELDS["S1"]
    assert analytics.SHARED_SNAP == ("snap_sr_clearance_pct",)
    assert "pnl" in analytics.COMMON_FIELDS
