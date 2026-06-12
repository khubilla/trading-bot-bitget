"""Unit tests for evaluate_s8() — post-S2 bounce at tri-confluence."""
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
_WARMUP = 50          # flat candles at 100 (RSI/MA warm-up; evaluator needs ≥66 rows)
_B_IDX = _WARMUP + 4  # breakout day index: warmup, big candle, 3 coil candles, B
_BOX_LOW = 128.0      # smallest-window coil (n=1) → box_low = min body of last coil candle


def _post_s2_bounce_rows(green_body=0.02, green_low_on_zone=True,
                         extension=0.30, pullback_steps=9):
    """
    Synthetic post-S2 bounce:
      50 flat warm-up candles around 100 (RSI/MA seed)
      big candle: 100 -> 130 (30% body)
      coil: 3 tight candles 126-130; smallest valid window is n=1 →
            box_top=130 (Darvas wick high), box_low=128 (body bottom)
      breakout day B (idx 54): closes at 136 (> box_top), RSI pumped high
      impulse leg up to swing_high = box_top*(1+extension)
      slow 9-candle pullback toward the fib (drags the 20MA up near the zone)
      last completed candle: small green candle sitting on the zone
      live forming candle on top
    fib618 = swing_high - 0.618*(swing_high - 128).
    """
    rows = [(100, 100.5, 99.5, 100.0)] * _WARMUP
    rows += [(100, 131, 100, 130)]                      # big candle, body 30%
    rows += [(129, 130, 126, 128), (128, 130, 126, 129),
             (129, 130, 126, 128)]                      # coil; n=1 box: 130/128
    rows += [(130, 137, 129, 136)]                      # breakout day B
    swing_high = 130 * (1 + extension)                  # e.g. 169
    rows += [(136, swing_high, 135, swing_high * 0.99)] # impulse peak
    fib = swing_high - 0.618 * (swing_high - _BOX_LOW)
    # pullback: drift down towards fib — enough candles to pull the 20MA near the zone
    top = swing_high * 0.985
    for i in range(pullback_steps):
        px = top - (top - fib * 1.01) * (i + 1) / pullback_steps
        rows += [(px * 1.01, px * 1.02, px * 0.995, px)]
    # green candle on the zone (or floating just above it when green_low_on_zone=False)
    g_low = fib * (1.001 if green_low_on_zone else 1.06)
    g_open = g_low * 1.002
    g_close = g_open * (1 + green_body)
    rows += [(g_open, g_close * 1.003, g_low, g_close)]
    # live forming candle
    rows += [(g_close, g_close * 1.002, g_close * 0.998, g_close * 1.001)]
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
    assert box_top == pytest.approx(130, rel=0.01)
    g = df.iloc[-2]
    assert green_low == pytest.approx(float(g["low"]))
    assert trigger == pytest.approx(float(g["high"]) * 1.005, rel=1e-6)
    assert zone_low <= green_low
    assert rsi_b > 70


def test_no_breakout_in_lookback_returns_hold(monkeypatch):
    monkeypatch.setattr("config_s8.S8_CONFLUENCE_TOL", 0.10)
    monkeypatch.setattr("config_s8.S8_PHASE_LOOKBACK", 3)  # breakout is older than 3 candles
    sig, *_, reason = evaluate_s8("BTCUSDT", _mk_df(_post_s2_bounce_rows()))
    assert sig == "HOLD"
    assert "structure" in reason.lower()


def test_continuation_candle_is_not_breakout_day(monkeypatch):
    """The impulse-peak candle closes above the breakout day, but its 'coil'
    (the breakout candle) closes ABOVE the big candle body top — the S2
    containment rule must reject it, anchoring B at the true coil breakout."""
    monkeypatch.setattr("config_s8.S8_CONFLUENCE_TOL", 0.10)
    df = _mk_df(_post_s2_bounce_rows())
    sig, _, _, _, _, _, box_top, _, _, reason = evaluate_s8("BTCUSDT", df)
    assert sig == "LONG", reason
    assert box_top == pytest.approx(130, rel=0.01)  # NOT the breakout candle's high (137)


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
    monkeypatch.setattr("config_s8.S8_MIN_EXTENSION", 0.50)  # demand 50% leg
    sig, *_, reason = evaluate_s8(
        "BTCUSDT", _mk_df(_post_s2_bounce_rows(extension=0.30)))
    assert sig == "HOLD"
    assert "leg" in reason.lower() or "extension" in reason.lower()


def test_fib_arithmetic_exact(monkeypatch):
    monkeypatch.setattr("config_s8.S8_CONFLUENCE_TOL", 0.10)
    df = _mk_df(_post_s2_bounce_rows())
    sig, _, _, _, _, _, box_top, _, fib, reason = evaluate_s8("BTCUSDT", df)
    assert sig == "LONG", reason
    swing_high = float(df["high"].iloc[_B_IDX:-1].max())   # high from breakout day on
    assert fib == pytest.approx(swing_high - 0.618 * (swing_high - _BOX_LOW), rel=1e-9)
