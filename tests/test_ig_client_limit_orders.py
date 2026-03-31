import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import ig_client


# ── Helpers ───────────────────────────────────────────────────────── #

def _make_session(monkeypatch, *, get_side_effect=None, post_side_effect=None,
                  delete_side_effect=None):
    """
    Return a fake _IGSession whose get/post/delete methods are controlled
    by the given callables (or a default no-op).
    """
    class FakeSession:
        def get(self, endpoint, params=None, version="1"):
            if get_side_effect:
                return get_side_effect(endpoint, params, version)
            return {}

        def post(self, endpoint, body=None, version="1"):
            if post_side_effect:
                return post_side_effect(endpoint, body, version)
            return {}

        def delete(self, endpoint, body=None, version="1"):
            if delete_side_effect:
                return delete_side_effect(endpoint, body, version)
            return {}

    fake = FakeSession()
    monkeypatch.setattr(ig_client, "_get_session", lambda: fake)
    return fake


# ── place_limit_long ──────────────────────────────────────────────── #

def test_place_limit_long_returns_deal_id(monkeypatch):
    """POST response contains dealReference; _poll_confirm resolves to dealId."""
    def fake_post(endpoint, body, version):
        return {"dealReference": "REF001"}

    def fake_get(endpoint, params, version):
        # Simulates _poll_confirm GET /confirms/REF001
        return {"dealStatus": "ACCEPTED", "dealId": "DEAL_LONG_001", "level": 50000.0}

    _make_session(monkeypatch, post_side_effect=fake_post, get_side_effect=fake_get)
    deal_id = ig_client.place_limit_long("CS.D.BITCOIN.CFD.IP", 50000.0, 48000.0, 55000.0, 1.0)
    assert deal_id == "DEAL_LONG_001"


def test_place_limit_long_sends_buy_direction(monkeypatch):
    """place_limit_long must send direction=BUY and type=LIMIT."""
    captured = {}

    def fake_post(endpoint, body, version):
        captured["body"] = body
        return {"dealReference": "REF002"}

    def fake_get(endpoint, params, version):
        return {"dealStatus": "ACCEPTED", "dealId": "DEAL002", "level": 50000.0}

    _make_session(monkeypatch, post_side_effect=fake_post, get_side_effect=fake_get)
    ig_client.place_limit_long("CS.D.BITCOIN.CFD.IP", 50000.0, 48000.0, 55000.0, 1.0)

    assert captured["body"]["direction"] == "BUY"
    assert captured["body"]["type"] == "LIMIT"


def test_place_limit_short_sends_sell_direction(monkeypatch):
    """place_limit_short must send direction=SELL and type=LIMIT."""
    captured = {}

    def fake_post(endpoint, body, version):
        captured["body"] = body
        return {"dealReference": "REF003"}

    def fake_get(endpoint, params, version):
        return {"dealStatus": "ACCEPTED", "dealId": "DEAL003", "level": 50000.0}

    _make_session(monkeypatch, post_side_effect=fake_post, get_side_effect=fake_get)
    ig_client.place_limit_short("CS.D.BITCOIN.CFD.IP", 50000.0, 52000.0, 46000.0, 1.0)

    assert captured["body"]["direction"] == "SELL"
    assert captured["body"]["type"] == "LIMIT"


def test_place_limit_long_includes_sl_tp(monkeypatch):
    """Payload must contain stopLevel and limitLevel."""
    captured = {}

    def fake_post(endpoint, body, version):
        captured["body"] = body
        return {"dealReference": "REF004"}

    def fake_get(endpoint, params, version):
        return {"dealStatus": "ACCEPTED", "dealId": "DEAL004", "level": 50000.0}

    _make_session(monkeypatch, post_side_effect=fake_post, get_side_effect=fake_get)
    ig_client.place_limit_long("CS.D.BITCOIN.CFD.IP", 50000.0, 48000.0, 55000.0, 1.0)

    assert "stopLevel" in captured["body"]
    assert "limitLevel" in captured["body"]
    assert captured["body"]["stopLevel"] == 48000.0
    assert captured["body"]["limitLevel"] == 55000.0


# ── cancel_working_order ──────────────────────────────────────────── #

def test_cancel_working_order_calls_delete(monkeypatch):
    """cancel_working_order must DELETE /workingorders/otc/{deal_id}."""
    captured = {}

    def fake_delete(endpoint, body, version):
        captured["endpoint"] = endpoint
        return {}

    _make_session(monkeypatch, delete_side_effect=fake_delete)
    ig_client.cancel_working_order("DEAL_XYZ")

    assert captured["endpoint"] == "/workingorders/otc/DEAL_XYZ"


# ── get_working_order_status ──────────────────────────────────────── #

def test_get_working_order_status_open(monkeypatch):
    """Order found in /workingorders → status=open, fill_price=None."""
    def fake_get(endpoint, params, version):
        if "workingorders" in endpoint:
            return {
                "workingOrders": [
                    {"workingOrderData": {"dealId": "DEAL_OPEN"}}
                ]
            }
        return {}

    _make_session(monkeypatch, get_side_effect=fake_get)
    result = ig_client.get_working_order_status("DEAL_OPEN")
    assert result == {"status": "open", "fill_price": None}


def test_get_working_order_status_filled(monkeypatch):
    """Order not in workingorders but found in /positions → status=filled with price."""
    def fake_get(endpoint, params, version):
        if "workingorders" in endpoint:
            return {"workingOrders": []}
        if "positions" in endpoint:
            return {
                "positions": [
                    {
                        "position": {
                            "dealId": "DEAL_FILLED",
                            "openLevel": 49500.0,
                        }
                    }
                ]
            }
        return {}

    _make_session(monkeypatch, get_side_effect=fake_get)
    result = ig_client.get_working_order_status("DEAL_FILLED")
    assert result == {"status": "filled", "fill_price": 49500.0}


def test_get_working_order_status_deleted(monkeypatch):
    """Order not in workingorders and not in positions → status=deleted."""
    def fake_get(endpoint, params, version):
        if "workingorders" in endpoint:
            return {"workingOrders": []}
        if "positions" in endpoint:
            return {"positions": []}
        return {}

    _make_session(monkeypatch, get_side_effect=fake_get)
    result = ig_client.get_working_order_status("DEAL_GONE")
    assert result == {"status": "deleted", "fill_price": None}
