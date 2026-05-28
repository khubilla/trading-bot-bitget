"""Verify HMAC-SHA256 signing matches Binance's published example."""

import binance_client as bc


def test_sign_known_vector():
    # Binance USDT-M Futures official signing example:
    # https://binance-docs.github.io/apidocs/futures/en/#signed-trade-and-user_data-endpoint-security
    # secret = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"
    secret = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"
    qs = "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1&recvWindow=5000&timestamp=1499827319559"
    expected = "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"
    assert bc._sign(qs, secret) == expected


def test_canonical_query_preserves_insertion_order():
    qs = bc._canonical_qs({"symbol": "BTCUSDT", "side": "BUY", "quantity": "0.001"})
    assert qs == "symbol=BTCUSDT&side=BUY&quantity=0.001"


def test_canonical_query_empty():
    assert bc._canonical_qs({}) == ""
    assert bc._canonical_qs(None) == ""


def test_signed_params_includes_signature():
    signed = bc._signed_params(
        {"symbol": "BTCUSDT"},
        "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j",
    )
    assert "signature" in signed
    assert "timestamp" in signed
    assert "recvWindow" in signed
    assert signed["symbol"] == "BTCUSDT"
    # signature must be the LAST key (Binance docs require it appended last)
    assert list(signed.keys())[-1] == "signature"
