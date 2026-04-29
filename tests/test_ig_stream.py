"""Unit tests for ig_stream.py (no real Lightstreamer connection)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json as _json
import pytest
import ig_stream

@pytest.fixture(autouse=True)
def reset_ig_stream_state():
    ig_stream._mark_cache.clear()
    ig_stream._connected    = False
    ig_stream._needs_reauth = False
    ig_stream._client       = None
    yield
    ig_stream._mark_cache.clear()


def test_get_mark_price_returns_zero_when_empty():
    ig_stream._mark_cache.clear()
    assert ig_stream.get_mark_price("EPIC1") == 0.0


def test_get_mark_price_returns_cached_value():
    ig_stream._mark_cache["EPIC1"] = 1234.5
    assert ig_stream.get_mark_price("EPIC1") == 1234.5


def test_is_connected_false_initially():
    ig_stream._connected = False
    assert ig_stream.is_connected() is False


def test_needs_reauth_false_initially():
    ig_stream._needs_reauth = False
    assert ig_stream.needs_reauth() is False


# ── Streaming credentials capture (ig_client integration) ────────────── #

def test_get_stream_credentials_returns_expected_keys():
    """get_stream_credentials() returns dict with required keys after login."""
    from unittest.mock import patch, MagicMock
    import ig_client as ig_c

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"CST": "test-cst", "X-SECURITY-TOKEN": "test-xst"}
    mock_resp.json.return_value = {
        "accountId": "ACC123",
        "lightstreamerEndpoint": "https://ls.ig.com",
    }

    original_session = ig_c._session
    try:
        ig_c._session = None
        with patch("requests.post", return_value=mock_resp):
            ig_c._get_session()
            creds = ig_c.get_stream_credentials()
    finally:
        ig_c._session = original_session

    assert creds["account_id"]  == "ACC123"
    assert creds["cst"]         == "test-cst"
    assert creds["xst"]         == "test-xst"
    assert creds["ls_endpoint"] == "https://ls.ig.com"


def test_refresh_session_clears_cached_session():
    """_refresh_session() clears the cached session."""
    import ig_client
    ig_client._session = object()  # put something in the cache
    ig_client._refresh_session()
    assert ig_client._session is None


def test_get_mark_price_uses_stream_cache_when_available():
    """ig_client.get_mark_price() returns stream value without REST call when cache has price."""
    from unittest.mock import patch
    import ig_client as ig

    ig_stream._mark_cache["EPIC_TEST"] = 9999.5

    with patch.object(ig._get_session(), "get", side_effect=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("REST should not be called"))):
        result = ig.get_mark_price("EPIC_TEST")
    assert result == 9999.5


def test_get_mark_price_falls_back_to_rest_when_stream_zero():
    """ig_client.get_mark_price() falls back to REST when stream cache is empty (returns 0.0)."""
    from unittest.mock import patch
    import ig_client as ig

    ig_stream._mark_cache.clear()  # cache empty → returns 0.0

    mock_resp = {"snapshot": {"bid": 100.0, "offer": 102.0}}
    with patch.object(ig._get_session(), "get", return_value=mock_resp):
        result = ig.get_mark_price("EPIC_NOCACHE")
    assert result == 101.0


def test_market_listener_updates_cache():
    """_MarketListener.onItemUpdate() writes BID to _mark_cache."""
    ig_stream._mark_cache.clear()

    class FakeUpdate:
        def getItemName(self): return "MARKET:GOLD_EPIC"
        def getValue(self, field):
            return "4650.5" if field == "BID" else "4651.0"

    listener = ig_stream._MarketListener()
    listener.onItemUpdate(FakeUpdate())
    assert ig_stream._mark_cache["GOLD_EPIC"] == 4650.5


def test_market_listener_ignores_none_bid():
    """_MarketListener.onItemUpdate() does not write cache when BID is None."""
    ig_stream._mark_cache.clear()

    class FakeUpdate:
        def getItemName(self): return "MARKET:EPIC_NULL"
        def getValue(self, field): return None

    listener = ig_stream._MarketListener()
    listener.onItemUpdate(FakeUpdate())
    assert ig_stream._mark_cache.get("EPIC_NULL") is None


def test_status_connected_sets_connected_true():
    ig_stream._connected    = False
    ig_stream._needs_reauth = False
    listener = ig_stream._StatusListener()
    listener.onStatusChange("CONNECTED:WS-STREAMING")
    assert ig_stream._connected is True
    assert ig_stream._needs_reauth is False


def test_status_disconnected_will_retry_sets_connected_false():
    ig_stream._connected = True
    listener = ig_stream._StatusListener()
    listener.onStatusChange("DISCONNECTED:WILL-RETRY")
    assert ig_stream._connected is False
    assert ig_stream._needs_reauth is False


def test_status_disconnected_will_not_retry_sets_needs_reauth():
    ig_stream._connected    = True
    ig_stream._needs_reauth = False
    listener = ig_stream._StatusListener()
    listener.onStatusChange("DISCONNECTED:WILL-NOT-RETRY")
    assert ig_stream._connected is False
    assert ig_stream._needs_reauth is True


def _make_trade_update(wou_data=None, opu_data=None):
    """Build a fake LS update object for the TRADE item."""
    class FakeUpdate:
        def getItemName(self): return "TRADE:ACC123"
        def getValue(self, field):
            if field == "WOU":
                return _json.dumps(wou_data) if wou_data else None
            if field == "OPU":
                return _json.dumps(opu_data) if opu_data else None
            return None
    return FakeUpdate()


def test_trade_listener_fires_wou_fill_callback():
    """WOU status=DELETED + dealStatus=ACCEPTED → callback('WOU_FILL', deal_id, fill_price)."""
    received = []
    listener = ig_stream._TradeListener(lambda *a: received.append(a))

    update = _make_trade_update(wou_data={
        "status": "DELETED",
        "dealStatus": "ACCEPTED",
        "dealId": "DEAL001",
        "level": 4650.5,
    })
    listener.onItemUpdate(update)
    assert received == [("WOU_FILL", "DEAL001", 4650.5)]


def test_trade_listener_ignores_wou_non_fill():
    """WOU status=OPEN → no callback."""
    received = []
    listener = ig_stream._TradeListener(lambda *a: received.append(a))

    update = _make_trade_update(wou_data={"status": "OPEN", "dealId": "DEAL001"})
    listener.onItemUpdate(update)
    assert received == []


def test_trade_listener_fires_opu_close_callback():
    """OPU status=DELETED → callback('OPU_CLOSE', deal_id, None)."""
    received = []
    listener = ig_stream._TradeListener(lambda *a: received.append(a))

    update = _make_trade_update(opu_data={"status": "DELETED", "dealId": "POS001"})
    listener.onItemUpdate(update)
    assert received == [("OPU_CLOSE", "POS001", None)]


def test_trade_listener_ignores_opu_non_close():
    """OPU status=OPEN → no callback."""
    received = []
    listener = ig_stream._TradeListener(lambda *a: received.append(a))

    update = _make_trade_update(opu_data={"status": "OPEN", "dealId": "POS001"})
    listener.onItemUpdate(update)
    assert received == []


def test_trade_listener_handles_none_fields_gracefully():
    """Update with no WOU/OPU fields → no error, no callback."""
    received = []
    listener = ig_stream._TradeListener(lambda *a: received.append(a))

    update = _make_trade_update()  # both None
    listener.onItemUpdate(update)
    assert received == []


def test_trade_listener_handles_invalid_json_gracefully():
    """Malformed JSON in WOU field → warning logged, no error raised."""
    received = []
    listener = ig_stream._TradeListener(lambda *a: received.append(a))

    class BadUpdate:
        def getItemName(self): return "TRADE:ACC123"
        def getValue(self, field):
            return "{not valid json" if field == "WOU" else None

    listener.onItemUpdate(BadUpdate())
    assert received == []  # no crash, no callback


def test_trade_listener_ignores_wou_deleted_rejected():
    received = []
    listener = ig_stream._TradeListener(lambda *a: received.append(a))
    update = _make_trade_update(wou_data={
        "status": "DELETED", "dealStatus": "REJECTED",
        "dealId": "DEAL_CANCELLED", "level": 4650.0,
    })
    listener.onItemUpdate(update)
    assert received == []


def test_trade_listener_handles_non_numeric_level_gracefully():
    received = []
    listener = ig_stream._TradeListener(lambda *a: received.append(a))
    update = _make_trade_update(wou_data={
        "status": "DELETED", "dealStatus": "ACCEPTED",
        "dealId": "DEAL001", "level": "N/A",
    })
    listener.onItemUpdate(update)
    assert len(received) == 1
    assert received[0][2] == 0.0


def test_is_streaming_available_returns_true_when_enabled():
    """is_streaming_available() returns True for epics with streaming enabled."""
    from unittest.mock import patch
    import ig_client as ig_c

    mock_resp = {
        "instrument": {
            "streamingPricesAvailable": True,
            "epic": "TEST.EPIC",
        }
    }

    original_session = ig_c._session
    try:
        with patch.object(ig_c._get_session(), "get", return_value=mock_resp):
            result = ig_c.is_streaming_available("TEST.EPIC")
    finally:
        ig_c._session = original_session

    assert result is True


def test_is_streaming_available_returns_false_when_disabled():
    """is_streaming_available() returns False for epics without streaming."""
    from unittest.mock import patch
    import ig_client as ig_c

    mock_resp = {
        "instrument": {
            "streamingPricesAvailable": False,
            "epic": "NO.STREAM",
        }
    }

    original_session = ig_c._session
    try:
        with patch.object(ig_c._get_session(), "get", return_value=mock_resp):
            result = ig_c.is_streaming_available("NO.STREAM")
    finally:
        ig_c._session = original_session

    assert result is False
