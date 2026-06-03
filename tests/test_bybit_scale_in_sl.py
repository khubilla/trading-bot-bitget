"""
Regression: Bybit scale-in must NOT leave the position without a stop-loss.

Root cause (HUSDT 5ddb3c71, 2026-06-01): both `bybit.update_position_sl` and
`bybit.place_moving_plan` post to /v5/position/trading-stop with tpslMode="Full",
a REPLACE that clears any field not present in the body. After a scale-in,
`refresh_plan_exits` -> `place_moving_plan` re-placed the trailing stop and
relied on a best-effort /v5/position/list read-back to re-include the SL. The
read-back raced (returned no SL right after the SL write), so the Full REPLACE
wiped the stop-loss. HUSDT then ran to the 10x liquidation price (-10.4%)
instead of stopping out at the -50%-margin level.

Fix: the authoritative new SL flows from bot._do_scale_in ->
refresh_plan_exits(sl_price=...) -> place_moving_plan(sl_price=...), so the SL
and the trailing stop are written together in ONE atomic trading-stop body —
no read-back race.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import bybit
import bybit_client as bc


@pytest.fixture(autouse=True)
def _no_dry_run(monkeypatch):
    # Exercise the real write path regardless of config_bybit.DRY_RUN.
    monkeypatch.setattr(bybit, "_dry_run_active", lambda: False)
    # Passthrough price formatting so assertions are exact (no tick rounding).
    monkeypatch.setattr(bybit, "round_price", lambda v, s: f"{float(v)}")


def test_place_moving_plan_with_sl_price_writes_sl_atomically(monkeypatch):
    """When sl_price > 0 is given, the trading-stop body carries that exact SL
    and no /v5/position/list read-back is performed."""
    posts = []
    monkeypatch.setattr(bc, "post", lambda path, body: posts.append((path, body)) or {})

    def _no_get(*a, **k):
        raise AssertionError("place_moving_plan must NOT read position when sl_price is given")
    monkeypatch.setattr(bc, "get", _no_get)

    bybit.place_moving_plan("HUSDT", "long", "80", 0.77831, "0.10", sl_price=0.77831)

    path, body = posts[-1]
    assert path == "/v5/position/trading-stop"
    assert "trailingStop" in body, "trailing stop must still be set"
    assert "stopLoss" in body, "SL must be written in the SAME atomic body"
    assert abs(float(body["stopLoss"]) - 0.77831) < 1e-9


def test_place_moving_plan_without_sl_price_keeps_readback(monkeypatch):
    """Backward-compat: with no sl_price (entry-time calls), the read-back
    preservation path still re-includes the position's current SL."""
    posts = []
    monkeypatch.setattr(bc, "post", lambda path, body: posts.append((path, body)) or {})
    monkeypatch.setattr(
        bc, "get",
        lambda path, params=None: {"result": {"list": [{"symbol": "HUSDT", "stopLoss": "0.78163"}]}},
    )

    bybit.place_moving_plan("HUSDT", "long", "80", 0.9007, "0.10")

    _, body = posts[-1]
    assert "stopLoss" in body
    assert abs(float(body["stopLoss"]) - 0.78163) < 1e-9


def test_refresh_plan_exits_forwards_sl_price_to_moving_plan(monkeypatch):
    """refresh_plan_exits(sl_price=X) must pass X through to place_moving_plan
    so the SL is re-asserted atomically with the new trailing stop."""
    # One existing conditional reduce-only partial-TP order to refresh.
    monkeypatch.setattr(
        bc, "get",
        lambda path, params=None: {
            "result": {"list": [
                {"orderId": "PP1", "reduceOnly": True, "side": "Sell",
                 "triggerPrice": "0.9007", "qty": "40"},
            ]}
        },
    )
    monkeypatch.setattr(bc, "post", lambda path, body: {})
    monkeypatch.setattr(bybit, "get_all_open_positions",
                        lambda: {"HUSDT": {"qty": 80.0}})
    monkeypatch.setattr(bybit, "_time", __import__("types").SimpleNamespace(sleep=lambda s: None))

    captured = {}

    def fake_moving_plan(symbol, hold_side, qty_str, trigger, range_rate, sl_price=0):
        captured["sl_price"] = sl_price
    monkeypatch.setattr(bybit, "place_moving_plan", fake_moving_plan)

    ok = bybit.refresh_plan_exits("HUSDT", "long", new_trail_trigger=0.9007, sl_price=0.77831)

    assert ok is True
    assert captured.get("sl_price") == 0.77831, \
        "refresh_plan_exits must forward sl_price to place_moving_plan"
