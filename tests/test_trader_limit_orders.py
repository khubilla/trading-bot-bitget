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
    assert p["orderType"] == "limit"
    assert p["timeInForceValue"] == "gtc"
    assert p["side"] == "buy"
    assert "presetStopLossPrice" in p
    assert "presetTakeProfitPrice" in p


def test_place_limit_short_sends_sell_side(monkeypatch):
    captured = {}

    def fake_post(path, payload):
        captured["payload"] = payload
        return {"code": "00000", "data": {"orderId": "ORD888"}}

    monkeypatch.setattr(bc, "post", fake_post)
    trader.place_limit_short("BTCUSDT", 60000.0, 63000.0, 55000.0, "0.001")

    p = captured["payload"]
    assert p["side"] == "sell"
    assert p["orderType"] == "limit"
    assert p["timeInForceValue"] == "gtc"
    assert "presetStopLossPrice" in p
    assert "presetTakeProfitPrice" in p


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

    assert captured["path"] == "/api/v2/mix/order/cancel-order"
    assert "orderId" in captured["payload"]
    assert captured["payload"]["orderId"] == "ORD123"


def test_cancel_order_raises_on_error(monkeypatch):
    monkeypatch.setattr(bc, "post", lambda path, payload: {"code": "50001", "msg": "err"})
    with pytest.raises(RuntimeError):
        trader.cancel_order("BTCUSDT", "ORD123")


# ── get_order_fill ────────────────────────────────────────────────── #

def test_get_order_fill_live(monkeypatch):
    monkeypatch.setattr(bc, "get", lambda path, params: {"code": "00000", "data": {"status": "live"}})
    result = trader.get_order_fill("BTCUSDT", "ORD123")
    assert result == {"status": "live", "fill_price": 0.0}


def test_get_order_fill_filled(monkeypatch):
    monkeypatch.setattr(bc, "get", lambda path, params: {
        "code": "00000",
        "data": {"status": "filled", "priceAvg": "0.07951"}
    })
    result = trader.get_order_fill("BTCUSDT", "ORD123")
    assert result == {"status": "filled", "fill_price": 0.07951}


def test_get_order_fill_cancelled(monkeypatch):
    monkeypatch.setattr(bc, "get", lambda path, params: {"code": "00000", "data": {"status": "cancelled"}})
    result = trader.get_order_fill("BTCUSDT", "ORD123")
    assert result == {"status": "cancelled", "fill_price": 0.0}


def test_get_order_fill_raises_on_error(monkeypatch):
    monkeypatch.setattr(bc, "get", lambda path, params: {"code": "50001", "msg": "err"})
    with pytest.raises(RuntimeError):
        trader.get_order_fill("BTCUSDT", "ORD123")
