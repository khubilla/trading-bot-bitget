"""Unit tests for S7 1H Darvas-box detector and helpers."""
import pandas as pd
import pytest

from strategies.s7 import today_h1_slice, detect_darvas_box


def _make_h1_df(rows):
    """rows: list of (open_ts, high, low). Returns a DataFrame indexed by UTC ts."""
    idx = pd.DatetimeIndex([r[0] for r in rows], tz="UTC")
    return pd.DataFrame(
        {"high": [r[1] for r in rows], "low": [r[2] for r in rows],
         "open": [r[1] for r in rows], "close": [(r[1] + r[2]) / 2 for r in rows]},
        index=idx,
    )


def test_today_h1_slice_drops_yesterday_and_forming_hour(monkeypatch):
    df = _make_h1_df([
        ("2026-04-27 22:00", 100, 95),  # yesterday — drop
        ("2026-04-27 23:00", 99,  94),  # yesterday — drop
        ("2026-04-28 00:00", 98,  94),  # today closed
        ("2026-04-28 01:00", 99,  93),  # today closed
        ("2026-04-28 02:00", 96,  93),  # today, currently forming — drop
    ])
    monkeypatch.setattr(
        "strategies.s7._utcnow",
        lambda: pd.Timestamp("2026-04-28 02:30", tz="UTC"),
    )
    s = today_h1_slice(df)
    assert len(s) == 2
    assert s.index[0] == pd.Timestamp("2026-04-28 00:00", tz="UTC")
    assert s.index[-1] == pd.Timestamp("2026-04-28 01:00", tz="UTC")
