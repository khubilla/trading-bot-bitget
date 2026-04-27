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


def _walk_h1(highs, lows, start="2026-04-28 00:00"):
    """Build a 1H DataFrame from parallel highs/lows lists."""
    base = pd.Timestamp(start, tz="UTC")
    rows = [(base + pd.Timedelta(hours=i), h, l) for i, (h, l) in enumerate(zip(highs, lows))]
    return _make_h1_df(rows)


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


def test_detector_returns_false_when_too_few_candles():
    df = _walk_h1([99, 98, 97, 96], [95, 94, 93, 92])  # 4 < 6
    locked, top, low, ti, li, reason = detect_darvas_box(df, confirm=2)
    assert locked is False
    assert "Need" in reason or "candles" in reason.lower()


def test_detector_locks_top_then_low_on_canonical_example():
    # Example from spec §5.3: top locks at 99 (idx 1), low locks at 84 (idx 7)
    highs = [98, 99, 96, 95, 92, 91, 88, 87, 86, 85]
    lows  = [95, 94, 93, 90, 88, 87, 85, 84, 84, 85]
    df = _walk_h1(highs, lows)
    locked, top, low, ti, li, reason = detect_darvas_box(df, confirm=2)
    assert locked is True
    assert top == 99
    assert low == 84
    assert ti == 1
    assert li == 7


def test_detector_top_not_locked_when_high_keeps_pushing():
    # Highs keep making new highs — top never confirms
    highs = [90, 91, 92, 93, 94, 95, 96, 97]
    lows  = [85, 86, 87, 88, 89, 90, 91, 92]
    df = _walk_h1(highs, lows)
    locked, top, low, ti, li, reason = detect_darvas_box(df, confirm=2)
    assert locked is False
    assert "Top box not yet confirmed" in reason


def test_detector_low_not_locked_when_low_keeps_falling():
    # Top locks early, but lows keep falling after — low never confirms
    highs = [99, 98, 97, 96, 95, 94, 93, 92]
    lows  = [95, 94, 93, 92, 91, 90, 89, 88]
    df = _walk_h1(highs, lows)
    locked, top, low, ti, li, reason = detect_darvas_box(df, confirm=2)
    assert locked is False
    assert "Low box not yet confirmed" in reason


def test_detector_rejects_inverted_structure():
    # Low ends up >= top (degenerate) — sanity rejection
    highs = [100, 99, 98, 97, 96, 95]
    lows  = [99, 98, 97, 96, 95, 95]
    df = _walk_h1(highs, lows)
    locked, top, low, *_ = detect_darvas_box(df, confirm=2)
    assert locked is False
