# IG Lightstreamer Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `get_mark_price()` REST polling and order-status polling in `ig_bot.py` with a Lightstreamer streaming connection, reducing IG API quota usage.

**Architecture:** A new `ig_stream.py` module owns the Lightstreamer connection, maintains a `_mark_cache` for real-time BID prices (MARKET subscription), and dispatches fill/close events to `ig_bot.py` (TRADE subscription). `ig_client.get_mark_price()` reads the cache first, falling back to REST only when the stream is not available. The 15-second polling loop is kept for candle evaluation; it pauses when the stream is disconnected and restarts the stream on session token expiry (~6 hours).

**Tech Stack:** `lightstreamer-client-lib` (pip install), Python `threading.Lock`, existing `ig_client.py` REST session

**Spec:** `docs/superpowers/specs/2026-04-07-ig-streaming-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `ig_stream.py` | **Create** | Lightstreamer client, MARKET + TRADE subscriptions, mark-price cache, connection state |
| `tests/test_ig_stream.py` | **Create** | Unit tests for ig_stream module (no real LS connection) |
| `ig_client.py` | **Modify** | `login()` captures `ls_endpoint`; `get_stream_credentials()`; `_refresh_session()`; `get_mark_price()` stream-first |
| `ig_bot.py` | **Modify** | `_stream_lock`, `_on_stream_event()`, tick pause/reauth, stream startup in `__main__` |
| `tests/test_ig_bot_streaming.py` | **Create** | Unit tests for streaming bot changes |

---

## Task 1: `ig_stream.py` — Module skeleton with mark-price cache

**Files:**
- Create: `ig_stream.py`
- Create: `tests/test_ig_stream.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ig_stream.py`:

```python
"""Unit tests for ig_stream.py (no real Lightstreamer connection)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import ig_stream


def test_get_mark_price_returns_zero_when_empty():
    ig_stream._mark_cache.clear()
    assert ig_stream.get_mark_price("EPIC1") == 0.0


def test_get_mark_price_returns_cached_value():
    ig_stream._mark_cache["EPIC1"] = 1234.5
    assert ig_stream.get_mark_price("EPIC1") == 1234.5
    ig_stream._mark_cache.clear()


def test_is_connected_false_initially():
    ig_stream._connected = False
    assert ig_stream.is_connected() is False


def test_needs_reauth_false_initially():
    ig_stream._needs_reauth = False
    assert ig_stream.needs_reauth() is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
pytest tests/test_ig_stream.py -v
```

Expected: `ModuleNotFoundError: No module named 'ig_stream'`

- [ ] **Step 3: Create `ig_stream.py` with skeleton**

```python
"""
IG Lightstreamer streaming client.

Subscribes to MARKET and TRADE streams; exposes a mark-price cache and
connection-state flags for ig_bot.py.

Public interface
----------------
start(epics, account_id, cst, xst, ls_endpoint, trade_callback=None)
stop()
is_connected() -> bool
needs_reauth() -> bool
get_mark_price(epic: str) -> float   # 0.0 if not yet received
"""
import json
import logging

logger = logging.getLogger(__name__)

# ── Module-level state ───────────────────────────────────────── #
_mark_cache:   dict = {}    # epic → latest BID float
_connected:    bool = False
_needs_reauth: bool = False
_client              = None  # LightstreamerClient instance


# ── Public interface ─────────────────────────────────────────── #

def get_mark_price(epic: str) -> float:
    """Return cached BID price for epic, or 0.0 if not yet received."""
    return _mark_cache.get(epic, 0.0)


def is_connected() -> bool:
    return _connected


def needs_reauth() -> bool:
    return _needs_reauth


def start(
    epics:         list,
    account_id:    str,
    cst:           str,
    xst:           str,
    ls_endpoint:   str,
    trade_callback=None,
) -> None:
    """Connect to Lightstreamer and set up MARKET (+ optionally TRADE) subscriptions."""
    pass  # implemented in Task 4


def stop() -> None:
    global _client, _connected, _needs_reauth
    if _client is not None:
        try:
            _client.disconnect()
        except Exception:
            pass
        _client = None
    _connected    = False
    _needs_reauth = False
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_ig_stream.py -v
```

Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add ig_stream.py tests/test_ig_stream.py
git commit -m "feat(streaming): ig_stream.py skeleton with mark-price cache and state flags"
```

---

## Task 2: `ig_client.py` — Capture streaming credentials at login

**Files:**
- Modify: `ig_client.py:68-92` (`_IGSession.login()`), `ig_client.py:149-166` (`_get_session()`)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ig_stream.py`:

```python
import ig_client as ig
from unittest.mock import patch, MagicMock
import requests


def test_get_stream_credentials_returns_expected_keys():
    """get_stream_credentials() returns dict with required keys after login."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"CST": "test-cst", "X-SECURITY-TOKEN": "test-xst"}
    mock_resp.json.return_value = {
        "accountId": "ACC123",
        "lightstreamerEndpoint": "https://ls.ig.com",
    }
    with patch("requests.post", return_value=mock_resp):
        with patch.object(ig, "_session", None):
            ig._refresh_session()
            # Trigger login
            session = ig._get_session()
            creds = ig.get_stream_credentials()
    assert creds["account_id"]  == "ACC123"
    assert creds["cst"]         == "test-cst"
    assert creds["xst"]         == "test-xst"
    assert creds["ls_endpoint"] == "https://ls.ig.com"


def test_refresh_session_clears_cached_session():
    import ig_client
    ig_client._session = object()  # put something in the cache
    ig_client._refresh_session()
    assert ig_client._session is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_ig_stream.py::test_get_stream_credentials_returns_expected_keys tests/test_ig_stream.py::test_refresh_session_clears_cached_session -v
```

Expected: `AttributeError: module 'ig_client' has no attribute '_refresh_session'`

- [ ] **Step 3: Modify `ig_client.py`**

In `_IGSession.__init__`, add after `self._token = ""`:
```python
        self._ls_endpoint  = ""
        self._account_id_from_login = ""
```

In `_IGSession.login()`, after `self._token = resp.headers.get("X-SECURITY-TOKEN", "")`, add:
```python
        resp_json = resp.json()
        self._ls_endpoint           = resp_json.get("lightstreamerEndpoint", "")
        self._account_id_from_login = resp_json.get("accountId", self._account_id)
```

After `_get_session()` (around line 166), add:

```python
def get_stream_credentials() -> dict:
    """Return streaming auth details captured during the last login.

    Returns a dict with keys: account_id, cst, xst, ls_endpoint.
    Call after _get_session() has been invoked.
    """
    sess = _get_session()
    return {
        "account_id":  sess._account_id_from_login or sess._account_id,
        "cst":         sess._cst,
        "xst":         sess._token,
        "ls_endpoint": sess._ls_endpoint,
    }


def _refresh_session() -> None:
    """Clear the cached session so the next _get_session() performs a fresh login."""
    global _session
    _session = None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_ig_stream.py::test_get_stream_credentials_returns_expected_keys tests/test_ig_stream.py::test_refresh_session_clears_cached_session -v
```

Expected: 2 PASSED

- [ ] **Step 5: Commit**

```bash
git add ig_client.py tests/test_ig_stream.py
git commit -m "feat(streaming): ig_client captures ls_endpoint/accountId at login; adds get_stream_credentials() and _refresh_session()"
```

---

## Task 3: `ig_client.py` — Stream-first `get_mark_price()`

**Files:**
- Modify: `ig_client.py:248-262` (`get_mark_price()`)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ig_stream.py`:

```python
def test_get_mark_price_uses_stream_cache_when_available(monkeypatch):
    """ig_client.get_mark_price() returns stream value without REST call when cache has price."""
    import ig_stream as stream_mod
    monkeypatch.setattr(stream_mod, "_mark_cache", {"EPIC_TEST": 9999.5})

    rest_called = []
    def _fake_rest(epic):
        rest_called.append(epic)
        return 1234.0
    with patch.object(ig._get_session(), "get", side_effect=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("REST should not be called"))):
        result = ig.get_mark_price("EPIC_TEST")
    assert result == 9999.5
    assert not rest_called


def test_get_mark_price_falls_back_to_rest_when_stream_zero(monkeypatch):
    """ig_client.get_mark_price() falls back to REST when stream cache is empty (returns 0.0)."""
    import ig_stream as stream_mod
    monkeypatch.setattr(stream_mod, "_mark_cache", {})  # cache empty → returns 0.0

    mock_resp = {"snapshot": {"bid": 100.0, "offer": 102.0}}
    with patch.object(ig._get_session(), "get", return_value=mock_resp):
        result = ig.get_mark_price("EPIC_NOCACHE")
    assert result == 101.0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_ig_stream.py::test_get_mark_price_uses_stream_cache_when_available tests/test_ig_stream.py::test_get_mark_price_falls_back_to_rest_when_stream_zero -v
```

Expected: both FAIL (REST is still called even when cache has a value)

- [ ] **Step 3: Update `get_mark_price()` in `ig_client.py`**

Replace the current `get_mark_price` function (lines 248-262) with:

```python
def get_mark_price(epic: str) -> float:
    """Current mid price. Reads from Lightstreamer cache when available; falls back to REST."""
    try:
        import ig_stream
        price = ig_stream.get_mark_price(epic)
        if price > 0:
            return price
    except ImportError:
        pass
    # REST fallback (paper mode, backtest, stream not yet connected)
    try:
        data = _get_session().get(f"/markets/{epic}", version="1")
        snap = data.get("snapshot", {})
        bid  = snap.get("bid")
        ask  = snap.get("offer")
        if bid is not None and ask is not None:
            return (float(bid) + float(ask)) / 2.0
        last = snap.get("lastTradedPrice", {}).get("bid")
        if last is not None:
            return float(last)
    except Exception as e:
        logger.error(f"get_mark_price({epic}): {e}")
    return 0.0
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_ig_stream.py::test_get_mark_price_uses_stream_cache_when_available tests/test_ig_stream.py::test_get_mark_price_falls_back_to_rest_when_stream_zero -v
```

Expected: 2 PASSED

- [ ] **Step 5: Run full test suite to verify no regressions**

```bash
pytest tests/ -v --ignore=tests/test_ig_stream.py -x -q 2>&1 | tail -20
```

Expected: all previously passing tests still pass

- [ ] **Step 6: Commit**

```bash
git add ig_client.py
git commit -m "feat(streaming): get_mark_price() reads stream cache first, falls back to REST"
```

---

## Task 4: `ig_stream.py` — MARKET subscription + connection state

**Files:**
- Modify: `ig_stream.py` (`start()`, `_StatusListener`, `_MarketListener`)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ig_stream.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_ig_stream.py::test_market_listener_updates_cache tests/test_ig_stream.py::test_market_listener_ignores_none_bid tests/test_ig_stream.py::test_status_connected_sets_connected_true tests/test_ig_stream.py::test_status_disconnected_will_retry_sets_connected_false tests/test_ig_stream.py::test_status_disconnected_will_not_retry_sets_needs_reauth -v
```

Expected: `AttributeError: module 'ig_stream' has no attribute '_MarketListener'`

- [ ] **Step 3: Implement `_StatusListener`, `_MarketListener`, and `start()` in `ig_stream.py`**

Replace the `pass` stub in `start()` and add the two listener classes. Full file content:

```python
"""
IG Lightstreamer streaming client.

Subscribes to MARKET and TRADE streams; exposes a mark-price cache and
connection-state flags for ig_bot.py.

Public interface
----------------
start(epics, account_id, cst, xst, ls_endpoint, trade_callback=None)
stop()
is_connected() -> bool
needs_reauth() -> bool
get_mark_price(epic: str) -> float   # 0.0 if not yet received
"""
import json
import logging

logger = logging.getLogger(__name__)

# ── Module-level state ───────────────────────────────────────── #
_mark_cache:   dict = {}    # epic → latest BID float
_connected:    bool = False
_needs_reauth: bool = False
_client              = None  # LightstreamerClient instance


# ── Public interface ─────────────────────────────────────────── #

def get_mark_price(epic: str) -> float:
    """Return cached BID price for epic, or 0.0 if not yet received."""
    return _mark_cache.get(epic, 0.0)


def is_connected() -> bool:
    return _connected


def needs_reauth() -> bool:
    return _needs_reauth


def start(
    epics:         list,
    account_id:    str,
    cst:           str,
    xst:           str,
    ls_endpoint:   str,
    trade_callback=None,
) -> None:
    """Connect to Lightstreamer and set up MARKET (+ optionally TRADE) subscriptions."""
    global _client, _connected, _needs_reauth, _mark_cache
    _mark_cache   = {}
    _connected    = False
    _needs_reauth = False

    from lightstreamer.client import LightstreamerClient, Subscription

    ls_password = f"CST-{cst}|XST-{xst}"
    client = LightstreamerClient(ls_endpoint)
    client.connectionDetails.setUser(account_id)
    client.connectionDetails.setPassword(ls_password)
    client.addListener(_StatusListener())

    # MARKET subscription — one item per epic
    market_items = [f"MARKET:{e}" for e in epics]
    market_sub   = Subscription(mode="MERGE", items=market_items, fields=["BID", "OFFER"])
    market_sub.addListener(_MarketListener())
    client.subscribe(market_sub)

    # TRADE subscription — only when trade_callback is provided (live mode)
    if trade_callback is not None:
        trade_sub = Subscription(
            mode="DISTINCT",
            items=[f"TRADE:{account_id}"],
            fields=["CONFIRMS", "WOU", "OPU"],
        )
        trade_sub.addListener(_TradeListener(trade_callback))
        client.subscribe(trade_sub)

    client.connect()
    _client = client
    logger.info(f"ig_stream: connecting to {ls_endpoint} | epics={epics}")


def stop() -> None:
    global _client, _connected, _needs_reauth
    if _client is not None:
        try:
            _client.disconnect()
        except Exception:
            pass
        _client = None
    _connected    = False
    _needs_reauth = False


# ── Listeners ────────────────────────────────────────────────── #

class _StatusListener:
    def onStatusChange(self, status: str) -> None:
        global _connected, _needs_reauth
        if status.startswith("CONNECTED"):
            _connected    = True
            _needs_reauth = False
            logger.info(f"ig_stream: {status}")
        elif status == "DISCONNECTED:WILL-NOT-RETRY":
            _connected    = False
            _needs_reauth = True
            logger.warning(f"ig_stream: {status} — needs reauth")
        else:
            _connected = False
            logger.info(f"ig_stream: {status}")

    def onServerError(self, errorCode: int, errorMessage: str) -> None:
        logger.error(f"ig_stream: server error {errorCode}: {errorMessage}")

    def onPropertyChange(self, property: str) -> None:
        pass


class _MarketListener:
    def onItemUpdate(self, update) -> None:
        try:
            epic = update.getItemName().replace("MARKET:", "")
            bid  = update.getValue("BID")
            if bid:
                _mark_cache[epic] = float(bid)
        except Exception as e:
            logger.warning(f"ig_stream: MARKET update parse error: {e}")

    def onSubscription(self) -> None:
        logger.info("ig_stream: MARKET subscription active")

    def onSubscriptionError(self, code: int, message: str) -> None:
        logger.error(f"ig_stream: MARKET subscription error {code}: {message}")

    def onUnsubscription(self) -> None:
        pass


class _TradeListener:
    """Placeholder — implemented in Task 5."""
    def __init__(self, callback):
        self._callback = callback

    def onItemUpdate(self, update) -> None:
        pass

    def onSubscription(self) -> None:
        logger.info("ig_stream: TRADE subscription active")

    def onSubscriptionError(self, code: int, message: str) -> None:
        logger.error(f"ig_stream: TRADE subscription error {code}: {message}")

    def onUnsubscription(self) -> None:
        pass
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_ig_stream.py -v
```

Expected: all tests PASSED (including the 4 from Task 1 and 2 from Task 2)

- [ ] **Step 5: Commit**

```bash
git add ig_stream.py
git commit -m "feat(streaming): MARKET subscription listener and connection status tracking"
```

---

## Task 5: `ig_stream.py` — TRADE subscription listener

**Files:**
- Modify: `ig_stream.py` (`_TradeListener.onItemUpdate`)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ig_stream.py`:

```python
import json as _json


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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_ig_stream.py::test_trade_listener_fires_wou_fill_callback tests/test_ig_stream.py::test_trade_listener_ignores_wou_non_fill tests/test_ig_stream.py::test_trade_listener_fires_opu_close_callback tests/test_ig_stream.py::test_trade_listener_ignores_opu_non_close tests/test_ig_stream.py::test_trade_listener_handles_none_fields_gracefully tests/test_ig_stream.py::test_trade_listener_handles_invalid_json_gracefully -v
```

Expected: all FAIL (current `_TradeListener.onItemUpdate` is a pass stub)

- [ ] **Step 3: Implement `_TradeListener.onItemUpdate` in `ig_stream.py`**

Replace the `_TradeListener` class with:

```python
class _TradeListener:
    def __init__(self, callback):
        self._callback = callback

    def onItemUpdate(self, update) -> None:
        self._handle_wou(update.getValue("WOU"))
        self._handle_opu(update.getValue("OPU"))

    def _handle_wou(self, raw) -> None:
        if not raw:
            return
        try:
            data = json.loads(raw)
        except Exception as e:
            logger.warning(f"ig_stream: WOU JSON parse error: {e}")
            return
        if not data:
            return
        if data.get("status") == "DELETED" and data.get("dealStatus") == "ACCEPTED":
            deal_id    = data.get("dealId", "")
            fill_price = float(data.get("level") or 0)
            logger.info(f"ig_stream: WOU fill — dealId={deal_id} level={fill_price}")
            try:
                self._callback("WOU_FILL", deal_id, fill_price)
            except Exception as e:
                logger.error(f"ig_stream: trade_callback error on WOU_FILL: {e}")

    def _handle_opu(self, raw) -> None:
        if not raw:
            return
        try:
            data = json.loads(raw)
        except Exception as e:
            logger.warning(f"ig_stream: OPU JSON parse error: {e}")
            return
        if not data:
            return
        if data.get("status") == "DELETED":
            deal_id = data.get("dealId", "")
            logger.info(f"ig_stream: OPU close — dealId={deal_id}")
            try:
                self._callback("OPU_CLOSE", deal_id, None)
            except Exception as e:
                logger.error(f"ig_stream: trade_callback error on OPU_CLOSE: {e}")

    def onSubscription(self) -> None:
        logger.info("ig_stream: TRADE subscription active")

    def onSubscriptionError(self, code: int, message: str) -> None:
        logger.error(f"ig_stream: TRADE subscription error {code}: {message}")

    def onUnsubscription(self) -> None:
        pass
```

- [ ] **Step 4: Run all ig_stream tests**

```bash
pytest tests/test_ig_stream.py -v
```

Expected: all tests PASSED

- [ ] **Step 5: Commit**

```bash
git add ig_stream.py
git commit -m "feat(streaming): TRADE subscription listener — WOU fill and OPU close dispatch"
```

---

## Task 6: `ig_bot.py` — `_stream_lock` + `_on_stream_event()`

**Files:**
- Modify: `ig_bot.py` (`IGBot.__init__`, add `_on_stream_event()`)
- Create: `tests/test_ig_bot_streaming.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ig_bot_streaming.py`:

```python
"""
Tests for IGBot streaming integration.
Covers _on_stream_event() dispatch and _tick() pause/reauth behaviour.
"""
import sys, os, time, tempfile, json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import ig_bot
import ig_client as ig
import config_ig


def _make_bot(monkeypatch):
    """Return an IGBot in paper mode with no external calls."""
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    monkeypatch.setattr(config_ig, "STATE_FILE", tmp.name)
    bot = ig_bot.IGBot(paper=True)
    return bot


# ── _on_stream_event ──────────────────────────────────────────── #

def test_on_stream_event_wou_fill_calls_handle_pending_filled(monkeypatch):
    """WOU_FILL event for known deal_id calls _handle_pending_filled and clears pending."""
    bot = _make_bot(monkeypatch)
    inst = config_ig.INSTRUMENTS[0]
    name = inst["display_name"]

    bot._pending_orders[name] = {
        "deal_id":  "DEAL_WOU_001",
        "side":     "SHORT",
        "ob_low":   4600.0,
        "ob_high":  4650.0,
        "sl":       4700.0,
        "tp":       4500.0,
        "trigger":  4640.0,
        "size":     0.1,
        "expires":  time.time() + 3600,
    }

    filled_calls = []
    monkeypatch.setattr(bot, "_handle_pending_filled", lambda fp: filled_calls.append(fp))

    bot._on_stream_event("WOU_FILL", "DEAL_WOU_001", 4640.0)

    assert filled_calls == [4640.0]
    assert bot._pending_orders[name] is None


def test_on_stream_event_wou_fill_ignores_unknown_deal_id(monkeypatch):
    """WOU_FILL for unknown deal_id causes no error and no state mutation."""
    bot = _make_bot(monkeypatch)
    inst = config_ig.INSTRUMENTS[0]
    name = inst["display_name"]

    bot._pending_orders[name] = {
        "deal_id": "DEAL_KNOWN",
        "side": "LONG", "ob_low": 100.0, "ob_high": 110.0,
        "sl": 90.0, "tp": 130.0, "trigger": 105.0, "size": 1, "expires": time.time() + 3600,
    }

    filled_calls = []
    monkeypatch.setattr(bot, "_handle_pending_filled", lambda fp: filled_calls.append(fp))

    bot._on_stream_event("WOU_FILL", "DEAL_UNKNOWN_XYZ", 105.0)

    assert filled_calls == []  # not called
    assert bot._pending_orders[name]["deal_id"] == "DEAL_KNOWN"  # unchanged


def test_on_stream_event_opu_close_calls_handle_position_closed(monkeypatch):
    """OPU_CLOSE event for known deal_id calls _handle_position_closed."""
    bot = _make_bot(monkeypatch)
    inst = config_ig.INSTRUMENTS[0]
    name = inst["display_name"]

    bot._positions[name] = {
        "deal_id":    "POS_OPU_001",
        "side":       "LONG",
        "entry":      4600.0,
        "sl":         4550.0,
        "tp1":        4650.0,
        "tp":         4700.0,
        "initial_qty":  0.1,
        "current_qty":  0.1,
        "partial_done": False,
        "trade_id":   "abc123",
        "opened_at":  "2026-04-07T10:00:00",
        "ob_low":     4580.0,
        "ob_high":    4610.0,
    }

    closed_calls = []
    monkeypatch.setattr(
        bot, "_handle_position_closed",
        lambda mark, inst, exit_reason=None: closed_calls.append((mark, exit_reason))
    )
    monkeypatch.setattr(ig, "get_mark_price", lambda epic: 4620.0)

    bot._on_stream_event("OPU_CLOSE", "POS_OPU_001", None)

    assert len(closed_calls) == 1
    assert closed_calls[0][1] == "SL_OR_TP"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_ig_bot_streaming.py::test_on_stream_event_wou_fill_calls_handle_pending_filled tests/test_ig_bot_streaming.py::test_on_stream_event_wou_fill_ignores_unknown_deal_id tests/test_ig_bot_streaming.py::test_on_stream_event_opu_close_calls_handle_position_closed -v
```

Expected: `AttributeError: 'IGBot' object has no attribute '_on_stream_event'`

- [ ] **Step 3: Add `_stream_lock` and `_on_stream_event()` to `ig_bot.py`**

In `IGBot.__init__`, after `self._scan_log: list = []`, add:
```python
        self._stream_lock = threading.Lock()
```

Add this method after `_update_scan_state()`:

```python
    def _on_stream_event(self, event_type: str, deal_id: str, fill_price: float) -> None:
        """
        Called from the Lightstreamer thread when a fill or position close arrives.
        Acquires _stream_lock to prevent concurrent mutation with _tick().
        """
        with self._stream_lock:
            if event_type == "WOU_FILL":
                for inst in config_ig.INSTRUMENTS:
                    name = inst["display_name"]
                    po   = self._pending_orders.get(name)
                    if po and po.get("deal_id") == deal_id:
                        self._current_instrument = inst
                        self._handle_pending_filled(fill_price)
                        self._pending_orders[name] = None
                        self._save_state()
                        logger.info(f"[{name}] [STREAM] WOU fill handled: {deal_id} @ {fill_price}")
                        break
                else:
                    logger.warning(f"[STREAM] WOU_FILL for unknown deal_id={deal_id}, ignoring")
            elif event_type == "OPU_CLOSE":
                for inst in config_ig.INSTRUMENTS:
                    name = inst["display_name"]
                    pos  = self._positions.get(name)
                    if pos and pos.get("deal_id") == deal_id:
                        self._current_instrument = inst
                        mark = ig.get_mark_price(inst["epic"])
                        self._handle_position_closed(mark, inst, exit_reason="SL_OR_TP")
                        logger.info(f"[{name}] [STREAM] OPU close handled: {deal_id}")
                        break
                else:
                    logger.warning(f"[STREAM] OPU_CLOSE for unknown deal_id={deal_id}, ignoring")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_ig_bot_streaming.py::test_on_stream_event_wou_fill_calls_handle_pending_filled tests/test_ig_bot_streaming.py::test_on_stream_event_wou_fill_ignores_unknown_deal_id tests/test_ig_bot_streaming.py::test_on_stream_event_opu_close_calls_handle_position_closed -v
```

Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add ig_bot.py tests/test_ig_bot_streaming.py
git commit -m "feat(streaming): IGBot._stream_lock and _on_stream_event() for fill/close dispatch"
```

---

## Task 7: `ig_bot.py` — Tick pause, reauth, and `_stream_lock` wrap

**Files:**
- Modify: `ig_bot.py` (`_tick()`, `__main__`)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ig_bot_streaming.py`:

```python
import ig_stream


def test_tick_pauses_when_stream_disconnected_live_mode(monkeypatch):
    """_tick() returns early without calling _tick_instrument when stream is down (live, non-paper)."""
    bot = _make_bot(monkeypatch)
    bot.paper = False  # pretend live mode

    monkeypatch.setattr(ig_stream, "_connected", False)
    monkeypatch.setattr(ig_stream, "_needs_reauth", False)

    called = []
    monkeypatch.setattr(bot, "_tick_instrument", lambda inst, now: called.append(inst))

    bot._tick()

    assert called == [], "_tick_instrument must not be called when stream is disconnected"


def test_tick_runs_normally_in_paper_mode_regardless_of_stream(monkeypatch):
    """_tick() in paper mode ignores stream state entirely."""
    bot = _make_bot(monkeypatch)
    assert bot.paper is True

    monkeypatch.setattr(ig_stream, "_connected", False)  # stream "down"

    called = []
    monkeypatch.setattr(bot, "_tick_instrument", lambda inst, now: called.append(inst))

    bot._tick()

    assert len(called) == len(config_ig.INSTRUMENTS), "all instruments should tick in paper mode"


def test_tick_triggers_reauth_when_needs_reauth(monkeypatch):
    """When needs_reauth() is True, _tick() refreshes session and restarts stream."""
    bot = _make_bot(monkeypatch)
    bot.paper = False

    monkeypatch.setattr(ig_stream, "_needs_reauth", True)
    monkeypatch.setattr(ig_stream, "_connected", False)

    refresh_called = []
    start_called   = []
    monkeypatch.setattr(ig, "_refresh_session", lambda: refresh_called.append(1))
    monkeypatch.setattr(ig_stream, "stop", lambda: None)
    monkeypatch.setattr(ig_stream, "start", lambda **kw: start_called.append(kw))

    # Provide fake session credentials
    monkeypatch.setattr(ig, "get_stream_credentials", lambda: {
        "account_id": "ACC1", "cst": "cst1", "xst": "xst1",
        "ls_endpoint": "https://ls.ig.com",
    })

    tick_instrument_calls = []
    monkeypatch.setattr(bot, "_tick_instrument", lambda inst, now: tick_instrument_calls.append(inst))

    bot._tick()

    assert refresh_called == [1], "_refresh_session must be called"
    assert len(start_called) == 1, "ig_stream.start must be called"
    assert tick_instrument_calls == [], "_tick_instrument must NOT run this tick"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_ig_bot_streaming.py::test_tick_pauses_when_stream_disconnected_live_mode tests/test_ig_bot_streaming.py::test_tick_runs_normally_in_paper_mode_regardless_of_stream tests/test_ig_bot_streaming.py::test_tick_triggers_reauth_when_needs_reauth -v
```

Expected: all FAIL (current `_tick()` has no stream check)

- [ ] **Step 3: Modify `_tick()` in `ig_bot.py`**

Replace the existing `_tick` method (starting at line 585) with:

```python
    def _tick(self) -> None:
        if not self.paper:
            if ig_stream.needs_reauth():
                logger.info("Stream token expired — refreshing session")
                ig._refresh_session()
                creds = ig.get_stream_credentials()
                ig_stream.stop()
                ig_stream.start(
                    epics          = [i["epic"] for i in config_ig.INSTRUMENTS],
                    account_id     = creds["account_id"],
                    cst            = creds["cst"],
                    xst            = creds["xst"],
                    ls_endpoint    = creds["ls_endpoint"],
                    trade_callback = self._on_stream_event,
                )
                return  # skip this tick; resume on next poll
            if not ig_stream.is_connected():
                logger.warning("Stream disconnected — pausing tick")
                return
        with self._stream_lock:
            self._heartbeat()
            now = _now_et()
            for instrument in config_ig.INSTRUMENTS:
                try:
                    self._tick_instrument(instrument, now)
                except Exception:
                    logger.exception("tick error for %s", instrument.get("display_name", "?"))
                finally:
                    self._current_instrument = None
```

Add `import ig_stream` near the top of `ig_bot.py` (after `import ig_client as ig`):
```python
import ig_stream
```

- [ ] **Step 4: Run streaming-related tests**

```bash
pytest tests/test_ig_bot_streaming.py -v
```

Expected: all 6 tests PASSED

- [ ] **Step 5: Run full test suite**

```bash
pytest tests/ -v -x -q 2>&1 | tail -30
```

Expected: all previously passing tests still pass

- [ ] **Step 6: Commit**

```bash
git add ig_bot.py
git commit -m "feat(streaming): _tick() pauses when stream down, restarts stream on token expiry"
```

---

## Task 8: `ig_bot.py` — Stream startup in `__main__`

**Files:**
- Modify: `ig_bot.py` (`__main__` block)

No unit test for this task — it's integration wiring that requires a real IG session to verify.

- [ ] **Step 1: Update `__main__` in `ig_bot.py`**

Replace the existing `__main__` block (from `if __name__ == "__main__":` to end of file) with:

```python
if __name__ == "__main__":
    _check_disclaimer()
    paper_mode = "--paper" in sys.argv
    config_ig.PAPER_MODE = paper_mode

    if not paper_mode:
        # Validate credentials are set
        missing = [k for k in ("IG_API_KEY", "IG_USERNAME", "IG_PASSWORD")
                   if not getattr(config_ig, k)]
        if missing:
            print(f"ERROR: Missing credentials: {', '.join(missing)}")
            print("Set them via environment variables or config_ig.py")
            sys.exit(1)

        # Establish IG session before starting the loop
        try:
            ig._get_session()
        except Exception as e:
            print(f"ERROR: Could not connect to IG: {e}")
            sys.exit(1)

    bot = IGBot(paper=paper_mode)

    if not paper_mode:
        # Start Lightstreamer streaming (live mode only)
        try:
            creds = ig.get_stream_credentials()
            ig_stream.start(
                epics          = [i["epic"] for i in config_ig.INSTRUMENTS],
                account_id     = creds["account_id"],
                cst            = creds["cst"],
                xst            = creds["xst"],
                ls_endpoint    = creds["ls_endpoint"],
                trade_callback = bot._on_stream_event,
            )
            logger.info("Lightstreamer streaming started")
        except Exception as e:
            logger.error(f"Failed to start streaming: {e} — running without streaming (REST polling only)")
            # Bot remains functional using REST fallback via get_mark_price()

    bot.run()
```

- [ ] **Step 2: Run full test suite one final time**

```bash
pytest tests/ -v -x -q 2>&1 | tail -30
```

Expected: all previously passing tests still pass

- [ ] **Step 3: Verify `lightstreamer-client-lib` is installed**

```bash
pip show lightstreamer-client-lib
```

If not installed:
```bash
pip install lightstreamer-client-lib
```

- [ ] **Step 4: Commit**

```bash
git add ig_bot.py
git commit -m "feat(streaming): start Lightstreamer in __main__ after IG login; graceful fallback on error"
```

---

## Self-Review Checklist

**Spec coverage:**

| Spec requirement | Covered by task |
|-----------------|-----------------|
| `ig_stream.py` new module with `start/stop/is_connected/needs_reauth/get_mark_price` | Tasks 1, 4, 5 |
| MARKET subscription → `_mark_cache` | Task 4 |
| TRADE subscription → WOU/OPU callbacks | Task 5 |
| `ig_client.get_mark_price()` stream-first | Task 3 |
| `ig_client.get_stream_credentials()` | Task 2 |
| `ig_client._refresh_session()` | Task 2 |
| `IGBot._stream_lock` | Task 6 |
| `IGBot._on_stream_event()` | Task 6 |
| `_tick()` pauses when disconnected | Task 7 |
| `_tick()` restarts stream on `needs_reauth` | Task 7 |
| Stream startup in `__main__` (live mode) | Task 8 |
| Paper mode: no streaming started | Task 8 (explicit guard) |
| `ImportError` guard in `get_mark_price` | Task 3 |
| `_IGSession.login()` captures `lightstreamerEndpoint` | Task 2 |
| `DISCONNECTED:WILL-NOT-RETRY` → `_needs_reauth=True` | Task 4 |

All requirements covered. ✓
