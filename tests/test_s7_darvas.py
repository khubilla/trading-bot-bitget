"""Unit tests for the S7 1H consolidation-box detector and slice helper."""
import pandas as pd

from strategies.s7 import today_h1_slice, detect_consolidation_box


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
        ("2026-04-27 22:00", 100, 95),
        ("2026-04-27 23:00", 99,  94),
        ("2026-04-28 00:00", 98,  94),
        ("2026-04-28 01:00", 99,  93),
        ("2026-04-28 02:00", 96,  93),
    ])
    monkeypatch.setattr(
        "strategies.s7._utcnow",
        lambda: pd.Timestamp("2026-04-28 02:30", tz="UTC"),
    )
    s = today_h1_slice(df)
    assert len(s) == 2
    assert s.index[0] == pd.Timestamp("2026-04-28 00:00", tz="UTC")
    assert s.index[-1] == pd.Timestamp("2026-04-28 01:00", tz="UTC")


def test_today_h1_slice_handles_production_shape(monkeypatch):
    """Production shape from tr.get_candles: RangeIndex + int-ms `ts` column."""
    rows = [
        ("2026-04-27 22:00", 100, 95),
        ("2026-04-27 23:00", 99,  94),
        ("2026-04-28 00:00", 98,  94),
        ("2026-04-28 01:00", 99,  93),
        ("2026-04-28 02:00", 96,  93),
    ]
    df = pd.DataFrame({
        "ts":    [int(pd.Timestamp(r[0], tz="UTC").timestamp() * 1000) for r in rows],
        "open":  [r[1] for r in rows],
        "high":  [r[1] for r in rows],
        "low":   [r[2] for r in rows],
        "close": [(r[1] + r[2]) / 2 for r in rows],
        "vol":   [0.0 for _ in rows],
    })
    df = df.sort_values("ts").reset_index(drop=True)
    assert isinstance(df.index, pd.RangeIndex)

    monkeypatch.setattr(
        "strategies.s7._utcnow",
        lambda: pd.Timestamp("2026-04-28 02:30", tz="UTC"),
    )
    s = today_h1_slice(df)
    assert len(s) == 2
    assert int(s.iloc[0]["ts"]) == int(pd.Timestamp("2026-04-28 00:00", tz="UTC").timestamp() * 1000)
    assert int(s.iloc[-1]["ts"]) == int(pd.Timestamp("2026-04-28 01:00", tz="UTC").timestamp() * 1000)


def test_detector_returns_false_when_too_few_candles():
    """Requires ≥ min_candles + 1 rows so the establishing set has ≥ min_candles."""
    df = _walk_h1([99, 98, 97, 96], [95, 94, 93, 92])  # 4 rows, default min=4 needs 5
    locked, top, low, reason = detect_consolidation_box(df)
    assert locked is False
    assert "Need" in reason


def test_detector_box_excludes_latest_candle():
    """The last row is the breakout test, not part of the box."""
    # Establish window (first 4 rows): high∈[100,99,98,97], low∈[95,94,93,92]
    # Test candle (last row): high=120, low=110 — must NOT contribute to the box
    highs = [100, 99, 98, 97, 120]
    lows  = [ 95, 94, 93, 92, 110]
    df = _walk_h1(highs, lows)
    locked, top, low, _ = detect_consolidation_box(df)
    assert locked is True
    assert top == 100  # max of first 4, not 120
    assert low == 92   # min of first 4, not 110


def test_detector_picks_extremes_across_full_establish_window():
    """Box top/low span the entire establish window (no walking/freezing)."""
    # Establish window has highs reaching 110 (a later push), unlike the broken
    # walking-Darvas algo which would have frozen at 100.
    highs = [100, 98, 99, 110, 105, 107, 108]  # establish = first 6 → max=110
    lows  = [ 95, 94, 93,  98,  96,  95,  94]  # test = last row, ignored
    df = _walk_h1(highs, lows)
    locked, top, low, _ = detect_consolidation_box(df)
    assert locked is True
    assert top == 110
    assert low == 93


def test_detector_min_candles_param():
    """Custom min_candles changes the required row count."""
    # 6 rows total → with min_candles=5, establish = first 5, need ≥6 rows ✓
    highs = [100, 99, 98, 97, 96, 95]
    lows  = [ 90, 91, 92, 93, 94, 92]
    df = _walk_h1(highs, lows)
    locked, top, low, _ = detect_consolidation_box(df, min_candles=5)
    assert locked is True
    assert top == 100
    assert low == 90  # min of first 5

    # min_candles=6 needs 7 rows; only 6 available → fail
    locked2, *_ = detect_consolidation_box(df, min_candles=6)
    assert locked2 is False


def test_detector_rejects_inverted_box():
    """Sanity rejection if establish window is degenerate (low >= top)."""
    # All same prices — high == low across rows
    highs = [100, 100, 100, 100, 100]
    lows  = [100, 100, 100, 100, 100]
    df = _walk_h1(highs, lows)
    locked, *_ = detect_consolidation_box(df)
    assert locked is False
