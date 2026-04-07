"""Unit tests for ig_stream.py (no real Lightstreamer connection)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

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
