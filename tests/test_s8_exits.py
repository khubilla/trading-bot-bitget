"""S8 exit computation: SL = max(green_low*0.999-floor, fill*(1-5%)), S2-style 2-leg TP."""
import pytest

import strategies.s8 as s8


@pytest.fixture
def patched(monkeypatch):
    import trader
    calls = {}
    monkeypatch.setattr(trader, "_round_price", lambda p, s: f"{p:.5f}")
    placed = []
    import strategies.s2 as s2
    def fake_place(symbol, hold_side, qty_str, sl_trig, sl_exec, trail_trigger, trail_range):
        placed.append({"symbol": symbol, "hold_side": hold_side, "qty": qty_str,
                       "sl_trig": sl_trig, "trail_trigger": trail_trigger,
                       "trail_range": trail_range})
        return True
    monkeypatch.setattr(s2, "_place_partial_trail_exits", fake_place)
    calls["placed"] = placed
    return calls


def test_sl_uses_green_low_floor_when_within_cap(patched):
    # fill=100, sl_floor (green_low*0.999 precomputed) = 97 → above 95 cap → SL=97
    ok, sl, trail = s8.compute_and_place_long_exits("ABCUSDT", "10", 100.0, 97.0, 0.05)
    assert ok is True
    assert sl == pytest.approx(97.0)
    assert trail == pytest.approx(110.0)
    assert patched["placed"][0]["hold_side"] == "long"


def test_sl_capped_at_5pct_when_green_low_too_deep(patched):
    # sl_floor = 80 → below the 95 cap → SL=95
    ok, sl, trail = s8.compute_and_place_long_exits("ABCUSDT", "10", 100.0, 80.0, 0.05)
    assert sl == pytest.approx(95.0)


def test_trail_range_passed_through(patched):
    s8.compute_and_place_long_exits("ABCUSDT", "10", 100.0, 97.0, 0.05)
    import config_s8
    assert patched["placed"][0]["trail_range"] == config_s8.S8_TRAILING_RANGE_PCT


def test_dna_fields_daily_only():
    import pandas as pd
    idx = pd.date_range("2026-01-01", periods=60, freq="D", tz="UTC")
    closes = pd.Series([100 + i for i in range(60)], index=idx, dtype=float)
    df = pd.DataFrame({"open": closes, "high": closes * 1.01,
                       "low": closes * 0.99, "close": closes}, index=idx)
    out = s8.dna_fields({"daily": df})
    assert "snap_trend_daily_ema_slope" in out
    assert "snap_trend_daily_price_vs_ema" in out
    assert "snap_trend_daily_rsi_bucket" in out
    assert s8.dna_fields({}) == {}


def test_trader_open_long_dispatches_s8(monkeypatch):
    """strategy="S8" routes to strategies.s8.compute_and_place_long_exits."""
    import trader
    monkeypatch.setattr(trader, "get_usdt_balance", lambda: 1000.0)
    monkeypatch.setattr(trader, "_get_total_equity", lambda: 1000.0)
    monkeypatch.setattr(trader, "get_mark_price", lambda s: 100.0)
    monkeypatch.setattr(trader, "_round_qty", lambda q, s: str(round(q, 3)))
    monkeypatch.setattr(trader, "_round_price", lambda p, s: f"{p:.5f}")
    monkeypatch.setattr(trader, "set_leverage", lambda s, l: None)
    monkeypatch.setattr(trader, "get_all_open_positions",
                        lambda: {"ABCUSDT": {"entry_price": 100.0}})
    monkeypatch.setattr(trader.bc, "post", lambda *a, **k: {})
    monkeypatch.setattr("time.sleep", lambda s: None)
    called = {}
    import strategies.s8 as s8mod
    monkeypatch.setattr(s8mod, "compute_and_place_long_exits",
                        lambda sym, qty, fill, slf, pct: called.update(
                            dict(sym=sym, fill=fill, slf=slf, pct=pct)) or (True, 97.0, 110.0))
    res = trader.open_long("ABCUSDT", sl_floor=97.0, leverage=10,
                           trade_size_pct=0.04, stop_loss_pct=0.05, strategy="S8")
    assert called["slf"] == 97.0
    assert called["pct"] == 0.05
    assert res["sl"] == 97.0
