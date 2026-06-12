"""Unit tests for evaluate_s8() — post-S2 bounce at tri-confluence.

Structure (no Darvas coil): a big momentum candle "flies" up out of a
pre-flight base; box_top/box_low come from that base (the S8_BASE_LOOKBACK
candles immediately BEFORE the big candle). Price retraces to the
box_top / 20MA / 61.8%-fib confluence and a small green candle arms entry.
"""
import pandas as pd
import pytest

from strategies.s8 import evaluate_s8


def _mk_df(rows):
    """rows: list of (open, high, low, close). Daily UTC index."""
    idx = pd.date_range("2026-01-01", periods=len(rows), freq="D", tz="UTC")
    return pd.DataFrame(
        {"open":  [r[0] for r in rows], "high": [r[1] for r in rows],
         "low":   [r[2] for r in rows], "close": [r[3] for r in rows]},
        index=idx,
    )


# Fixture geometry constants — keep tests and fixture in sync.
_WARMUP   = 40
_BOX_LOW  = 100.0      # pre-flight base low
_BOX_TOP  = 104.0      # pre-flight base high
_BIG_IDX  = _WARMUP + 10   # big momentum candle index (40 warmup + 10 base candles)

# The 10-candle pre-flight base: rising 100 -> 104, lows floored at 100,
# highs capped at 104 so box_low=100 and box_top=104 exactly.
_BASE_ROWS = [
    (100.0, 100.5, 100.0, 100.3),   # low touches box_low (100)
    (100.3, 101.0, 100.2, 100.8),
    (100.8, 101.5, 100.6, 101.2),
    (101.2, 102.0, 101.0, 101.8),
    (101.8, 102.5, 101.6, 102.3),
    (102.3, 103.0, 102.0, 102.8),
    (102.8, 103.5, 102.6, 103.2),
    (103.2, 104.0, 103.0, 103.8),   # high touches box_top (104)
    (103.8, 104.0, 103.5, 103.6),
    (103.6, 103.9, 103.0, 103.3),   # last base candle; big candle opens from here
]


def _post_s2_bounce_rows(big_body=0.23, green_body=0.02, green_low_on_zone=True,
                         flew=True, pullback_steps=8):
    """
    Build: 40 rising warmup candles, a 10-candle base [100,104], a big
    momentum candle that flies up out of the base, a retrace toward the
    confluence, a small green candle on the zone, then the forming candle.
    """
    rows = []
    for i in range(_WARMUP):
        px = 96.0 + (100.0 - 96.0) * i / (_WARMUP - 1)   # gentle rise 96 -> 100
        rows.append((px, px + 0.3, px - 0.3, px))
    rows += list(_BASE_ROWS)

    b_open = 103.3
    b_close = b_open * (1 + big_body) if flew else b_open * 1.005   # 23% -> ~127
    b_high = b_close + 1.0
    rows.append((b_open, b_high, 103.0, b_close))         # the "flight" candle

    swing_high = b_high
    fib = swing_high - 0.618 * (swing_high - _BOX_LOW)
    # retrace from near swing_high down toward the zone top (~fib)
    top = swing_high * 0.98
    target = fib * 1.01
    for i in range(pullback_steps):
        px = top - (top - target) * (i + 1) / pullback_steps
        rows.append((px * 1.005, px * 1.01, px * 0.995, px))

    # small green candle landing on the zone (or floating above when not on-zone)
    g_low = fib * (1.001 if green_low_on_zone else 1.12)
    g_open = g_low * 1.003
    g_close = g_open * (1 + green_body)
    rows.append((g_open, g_close * 1.002, g_low, g_close))
    # live forming candle
    rows.append((g_close, g_close * 1.002, g_close * 0.997, g_close * 1.001))
    return rows


def test_disabled_returns_hold(monkeypatch):
    monkeypatch.setattr("config_s8.S8_ENABLED", False)
    sig, *_, reason = evaluate_s8("BTCUSDT", _mk_df(_post_s2_bounce_rows()))
    assert sig == "HOLD"
    assert "disabled" in reason.lower()


def test_not_enough_candles_returns_hold():
    sig, *_, reason = evaluate_s8("BTCUSDT", _mk_df([(100, 101, 99, 100)] * 10))
    assert sig == "HOLD"
    assert "not enough" in reason.lower()


def test_full_setup_returns_long_with_levels(monkeypatch):
    # widen tolerance: synthetic 20MA isn't engineered to the exact zone
    monkeypatch.setattr("config_s8.S8_CONFLUENCE_TOL", 0.10)
    df = _mk_df(_post_s2_bounce_rows())
    sig, rsi_b, trigger, green_low, zone_low, zone_high, box_top, ma, fib, reason = \
        evaluate_s8("BTCUSDT", df)
    assert sig == "LONG", reason
    g = df.iloc[-2]
    assert green_low == pytest.approx(float(g["low"]))
    assert trigger == pytest.approx(float(g["high"]) * 1.005, rel=1e-6)
    assert zone_low <= green_low
    assert rsi_b > 70


def test_box_levels_come_from_preflight_base(monkeypatch):
    """box_top/box_low are the base high/low BEFORE the big candle —
    NOT the big momentum candle's own high/low."""
    monkeypatch.setattr("config_s8.S8_CONFLUENCE_TOL", 0.10)
    df = _mk_df(_post_s2_bounce_rows())
    sig, _, _, _, _, _, box_top, _, _, reason = evaluate_s8("BTCUSDT", df)
    assert sig == "LONG", reason
    assert box_top == pytest.approx(_BOX_TOP, rel=1e-9)        # 104, base high
    big_high = float(df["high"].iloc[_BIG_IDX])
    assert box_top < big_high                                  # NOT the flight candle's high (~128)


def test_no_big_momentum_candle_returns_hold(monkeypatch):
    monkeypatch.setattr("config_s8.S8_CONFLUENCE_TOL", 0.10)
    # flew=False → no candle with body ≥20% → no structure
    sig, *_, reason = evaluate_s8("BTCUSDT", _mk_df(_post_s2_bounce_rows(flew=False)))
    assert sig == "HOLD"
    assert "structure" in reason.lower()


def test_big_candle_outside_phase_lookback_returns_hold(monkeypatch):
    monkeypatch.setattr("config_s8.S8_CONFLUENCE_TOL", 0.10)
    monkeypatch.setattr("config_s8.S8_PHASE_LOOKBACK", 3)  # big candle is older than 3 candles
    sig, *_, reason = evaluate_s8("BTCUSDT", _mk_df(_post_s2_bounce_rows()))
    assert sig == "HOLD"
    assert "structure" in reason.lower()


def test_red_candle_returns_hold(monkeypatch):
    monkeypatch.setattr("config_s8.S8_CONFLUENCE_TOL", 0.10)
    rows = _post_s2_bounce_rows()
    o, h, l, c = rows[-2]
    rows[-2] = (c, h, l, o)        # invert: red candle
    sig, *_, reason = evaluate_s8("BTCUSDT", _mk_df(rows))
    assert sig == "HOLD"
    assert "green" in reason.lower() or "red" in reason.lower()


def test_big_green_candle_returns_hold(monkeypatch):
    monkeypatch.setattr("config_s8.S8_CONFLUENCE_TOL", 0.10)
    sig, *_, reason = evaluate_s8(
        "BTCUSDT", _mk_df(_post_s2_bounce_rows(green_body=0.12)))
    assert sig == "HOLD"
    assert "body" in reason.lower()


def test_candle_floating_above_zone_returns_hold(monkeypatch):
    monkeypatch.setattr("config_s8.S8_CONFLUENCE_TOL", 0.10)
    sig, *_, reason = evaluate_s8(
        "BTCUSDT", _mk_df(_post_s2_bounce_rows(green_low_on_zone=False)))
    assert sig == "HOLD"
    assert "zone" in reason.lower()


def test_confluence_spread_too_wide_returns_hold(monkeypatch):
    monkeypatch.setattr("config_s8.S8_CONFLUENCE_TOL", 0.001)  # impossible tolerance
    sig, *_, reason = evaluate_s8("BTCUSDT", _mk_df(_post_s2_bounce_rows()))
    assert sig == "HOLD"
    assert "confluence" in reason.lower()


def test_leg_too_small_returns_hold(monkeypatch):
    monkeypatch.setattr("config_s8.S8_CONFLUENCE_TOL", 0.10)
    monkeypatch.setattr("config_s8.S8_MIN_EXTENSION", 0.50)  # demand a 50% flight
    sig, *_, reason = evaluate_s8("BTCUSDT", _mk_df(_post_s2_bounce_rows()))
    assert sig == "HOLD"
    assert "leg" in reason.lower() or "extension" in reason.lower()


def test_fib_arithmetic_exact(monkeypatch):
    monkeypatch.setattr("config_s8.S8_CONFLUENCE_TOL", 0.10)
    df = _mk_df(_post_s2_bounce_rows())
    sig, _, _, _, _, _, box_top, _, fib, reason = evaluate_s8("BTCUSDT", df)
    assert sig == "LONG", reason
    swing_high = float(df["high"].iloc[_BIG_IDX:-1].max())   # high from big candle on
    assert fib == pytest.approx(swing_high - 0.618 * (swing_high - _BOX_LOW), rel=1e-9)
