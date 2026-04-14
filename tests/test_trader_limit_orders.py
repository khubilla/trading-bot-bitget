import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import bitget_client as bc
import trader


# ── place_limit_long ──────────────────────────────────────────────── #

def test_place_limit_long_returns_order_id(monkeypatch):
    monkeypatch.setattr(bc, "post", lambda path, payload: {"code": "00000", "data": {"orderId": "ORD123"}})
    result = trader.place_limit_long("BTCUSDT", 60000.0, 58000.0, 65000.0, "0.001")
    assert result == "ORD123"


def test_place_limit_long_sends_correct_payload(monkeypatch):
    captured = {}

    def fake_post(path, payload):
        captured["path"] = path
        captured["payload"] = payload
        return {"code": "00000", "data": {"orderId": "ORD999"}}

    monkeypatch.setattr(bc, "post", fake_post)
    trader.place_limit_long("BTCUSDT", 60000.0, 58000.0, 65000.0, "0.001")

    p = captured["payload"]
    assert p["orderType"] == "market"
    assert p["triggerType"] == "mark_price"
    assert p["planType"] == "normal_plan"
    assert p["side"] == "buy"
    assert "presetStopLossPrice" in p
    assert "presetTakeProfitPrice" in p
    assert "triggerPrice" in p


def test_place_limit_short_sends_sell_side(monkeypatch):
    captured = {}

    def fake_post(path, payload):
        captured["payload"] = payload
        return {"code": "00000", "data": {"orderId": "ORD888"}}

    monkeypatch.setattr(bc, "post", fake_post)
    trader.place_limit_short("BTCUSDT", 60000.0, 63000.0, 55000.0, "0.001")

    p = captured["payload"]
    assert p["side"] == "sell"
    assert p["orderType"] == "market"
    assert p["triggerType"] == "mark_price"
    assert p["planType"] == "normal_plan"
    assert "presetStopLossPrice" in p
    assert "presetTakeProfitPrice" in p
    assert "triggerPrice" in p


def test_place_limit_long_raises_on_error(monkeypatch):
    monkeypatch.setattr(bc, "post", lambda path, payload: {"code": "50001", "msg": "err"})
    with pytest.raises(RuntimeError):
        trader.place_limit_long("BTCUSDT", 60000.0, 58000.0, 65000.0, "0.001")


# ── cancel_order ──────────────────────────────────────────────────── #

def test_cancel_order_calls_correct_endpoint(monkeypatch):
    captured = {}

    def fake_post(path, payload):
        captured["path"] = path
        captured["payload"] = payload
        return {"code": "00000", "data": {}}

    monkeypatch.setattr(bc, "post", fake_post)
    trader.cancel_order("BTCUSDT", "ORD123")

    assert captured["path"] == "/api/v2/mix/order/cancel-plan-order"
    assert "orderId" in captured["payload"]
    assert captured["payload"]["orderId"] == "ORD123"


def test_cancel_order_raises_on_error(monkeypatch):
    monkeypatch.setattr(bc, "post", lambda path, payload: {"code": "50001", "msg": "err"})
    with pytest.raises(RuntimeError):
        trader.cancel_order("BTCUSDT", "ORD123")


# ── get_order_fill ────────────────────────────────────────────────── #

def test_get_order_fill_live(monkeypatch):
    """Plan order with not_trigger status is treated as live."""
    def fake_get(path, params):
        return {
            "code": "00000",
            "data": {
                "entrustedList": [
                    {"orderId": "ORD123", "planStatus": "not_trigger"}
                ]
            }
        }
    monkeypatch.setattr(bc, "get", fake_get)
    result = trader.get_order_fill("BTCUSDT", "ORD123")
    assert result == {"status": "live", "fill_price": 0.0}


def test_get_order_fill_filled(monkeypatch):
    """Plan order with triggered status fetches fill price from position."""
    call_count = {"n": 0}

    def fake_get(path, params):
        call_count["n"] += 1
        if call_count["n"] == 1:  # First call to plan-orders
            return {
                "code": "00000",
                "data": {
                    "entrustedList": [
                        {"orderId": "ORD123", "planStatus": "triggered"}
                    ]
                }
            }
        else:  # Second call to single-position
            return {
                "code": "00000",
                "data": [{"openPriceAvg": "60000.0"}]
            }

    monkeypatch.setattr(bc, "get", fake_get)
    result = trader.get_order_fill("BTCUSDT", "ORD123")
    assert result == {"status": "filled", "fill_price": 60000.0}


def test_get_order_fill_cancelled(monkeypatch):
    """Plan order with cancel status is treated as cancelled."""
    def fake_get(path, params):
        return {
            "code": "00000",
            "data": {
                "entrustedList": [
                    {"orderId": "ORD123", "planStatus": "cancel"}
                ]
            }
        }
    monkeypatch.setattr(bc, "get", fake_get)
    result = trader.get_order_fill("BTCUSDT", "ORD123")
    assert result == {"status": "cancelled", "fill_price": 0.0}


def test_get_order_fill_new_status_treated_as_live(monkeypatch):
    """Plan order with not_trigger status (pending) is treated as live."""
    def fake_get(path, params):
        return {
            "code": "00000",
            "data": {
                "entrustedList": [
                    {"orderId": "ORD123", "status": "not_trigger"}
                ]
            }
        }
    monkeypatch.setattr(bc, "get", fake_get)
    result = trader.get_order_fill("BTCUSDT", "ORD123")
    assert result == {"status": "live", "fill_price": 0.0}


def test_get_order_fill_raises_on_error(monkeypatch):
    monkeypatch.setattr(bc, "get", lambda path, params: {"code": "50001", "msg": "err"})
    with pytest.raises(RuntimeError):
        trader.get_order_fill("BTCUSDT", "ORD123")
