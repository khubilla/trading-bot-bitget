# IG Lightstreamer Streaming Integration — Design Spec

**Date:** 2026-04-07  
**Status:** Approved  
**Driver:** Reduce IG REST API quota usage (mark price + order status polling)

---

## Goal

Replace frequent REST calls in `ig_bot.py` with a Lightstreamer streaming connection. The 15-second polling loop is kept for candle evaluation, heartbeat, and session-end checks — but `get_mark_price()` reads from a streaming cache and fill/close events arrive as push notifications rather than being polled.

**Not changing:** candle fetching (OHLCV bars require REST), order placement/cancellation, paper mode TRADE subscription.

---

## Architecture Overview

```
[Lightstreamer thread]        [15s tick thread]
  MARKET:{epic}  → BID  →  _mark_cache[epic]  ←  ig_stream.get_mark_price()
  TRADE update   → WOU  →  _on_stream_event("WOU_FILL", deal_id, fill_price)
  TRADE update   → OPU  →  _on_stream_event("OPU_CLOSE", deal_id, None)

  On disconnect  →  is_connected() = False  →  tick loop pauses
  On reconnect   →  is_connected() = True   →  tick loop resumes
  Token expiry   →  needs_reauth() = True   →  session refresh + restart stream
```

Subscriptions:
- One `MARKET:{epic}` per instrument — MERGE mode, fields: `BID`, `OFFER`
- One `TRADE:{accountId}` per account — DISTINCT mode, fields: `WOU`, `OPU`, `CONFIRMS`

---

## Files Changed

| File | Change type |
|------|-------------|
| `ig_stream.py` | **New** — Lightstreamer client module |
| `ig_client.py` | `get_mark_price()` delegates to stream cache; `_refresh_session()` added |
| `ig_bot.py` | Stream startup, `_stream_lock`, `_on_stream_event()`, tick pause/reauth checks |

---

## `ig_stream.py` — New Module

### Public interface

```python
def start(
    epics: list[str],
    account_id: str,
    cst: str,
    xst: str,
    ls_endpoint: str,
    trade_callback=None,   # None in paper mode (skips TRADE subscription)
) -> None: ...

def stop() -> None: ...

def is_connected() -> bool: ...

def needs_reauth() -> bool: ...

def get_mark_price(epic: str) -> float: ...  # returns 0.0 if not yet received
```

### Internal state

```python
_mark_cache: dict[str, float] = {}   # epic → latest BID
_connected:  bool = False
_needs_reauth: bool = False
_client: LSClient | None = None
```

### Connection lifecycle

1. `start()` builds `ls_password = f"CST-{cst}|XST-{xst}"`
2. Creates `LSClient(ls_endpoint, account_id, ls_password)`
3. Registers status listener — sets `_connected` / `_needs_reauth` on status changes
4. Calls `client.connect()`
5. Subscribes `MARKET:{epic}` for each epic (MERGE, BID+OFFER)
6. If `trade_callback` is not None, subscribes `TRADE:{account_id}` (DISTINCT, WOU+OPU+CONFIRMS)

Status transitions:
- `CONNECTED:*` → `_connected = True`, `_needs_reauth = False`
- `DISCONNECTED:WILL-RETRY` → `_connected = False` (SDK handles retry)
- `DISCONNECTED:WILL-NOT-RETRY` → `_connected = False`, `_needs_reauth = True`

### MARKET subscription listener

```python
def on_item_update(update):
    epic = update.item_name.replace("MARKET:", "")
    bid  = update.getValue("BID")
    if bid:
        _mark_cache[epic] = float(bid)
```

### TRADE subscription listener

WOU (working order update):
```python
wou_json = json.loads(update.getValue("WOU") or "null")
if wou_json and wou_json.get("status") == "DELETED" \
             and wou_json.get("dealStatus") == "ACCEPTED":
    deal_id    = wou_json["dealId"]
    fill_price = float(wou_json.get("level", 0))
    trade_callback("WOU_FILL", deal_id, fill_price)
```

OPU (open position update):
```python
opu_json = json.loads(update.getValue("OPU") or "null")
if opu_json and opu_json.get("status") == "DELETED":
    deal_id = opu_json["dealId"]
    trade_callback("OPU_CLOSE", deal_id, None)
```

`trade_callback` is a callable supplied by `ig_bot.py`. It is responsible for acquiring the lock before mutating bot state.

---

## `ig_client.py` Changes

### `get_mark_price()` — stream-first

```python
def get_mark_price(epic: str) -> float:
    try:
        import ig_stream
        price = ig_stream.get_mark_price(epic)
        if price > 0:
            return price
    except ImportError:
        pass
    # existing REST call unchanged ...
```

`ImportError` guard keeps `ig_client.py` decoupled from `ig_stream.py` — backtest, unit tests, and paper mode without streaming all work without change.

### `_refresh_session()` — token expiry helper

Clears the cached session object so the next `_get_session()` call performs a fresh login and returns new CST/XST tokens.

---

## `ig_bot.py` Changes

### 1. Lock + callback

```python
class IGBot:
    def __init__(self, ...):
        ...
        self._stream_lock = threading.Lock()

    def _on_stream_event(self, event_type: str, deal_id: str, fill_price: float) -> None:
        with self._stream_lock:
            if event_type == "WOU_FILL":
                for inst in config_ig.INSTRUMENTS:
                    po = self._pending_orders.get(inst["display_name"])
                    if po and po["deal_id"] == deal_id:
                        self._current_instrument = inst
                        self._handle_pending_filled(fill_price)
                        self._pending_orders[inst["display_name"]] = None
                        self._save_state()
                        break
            elif event_type == "OPU_CLOSE":
                for inst in config_ig.INSTRUMENTS:
                    pos = self._positions.get(inst["display_name"])
                    if pos and pos.get("deal_id") == deal_id:
                        self._current_instrument = inst
                        mark = ig_stream.get_mark_price(inst["epic"])
                        self._handle_position_closed(mark, inst, exit_reason="SL_OR_TP")
                        break
```

### 2. Tick pause + token refresh

```python
def _tick(self) -> None:
    if not self.paper:
        if ig_stream.needs_reauth():
            logger.info("Stream token expired — refreshing session")
            ig._refresh_session()
            session = ig._get_session()
            ig_stream.stop()
            ig_stream.start(
                epics          = [i["epic"] for i in config_ig.INSTRUMENTS],
                account_id     = session["account_id"],
                cst            = session["cst"],
                xst            = session["xst"],
                ls_endpoint    = session["ls_endpoint"],
                trade_callback = self._on_stream_event,
            )
            return
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

### 3. Stream startup in `__main__`

```python
# After ig._get_session() succeeds:
session = ig._get_session()
ig_stream.start(
    epics          = [i["epic"] for i in config_ig.INSTRUMENTS],
    account_id     = session["account_id"],
    cst            = session["cst"],
    xst            = session["xst"],
    ls_endpoint    = session["ls_endpoint"],
    trade_callback = bot._on_stream_event,
)
```

Paper mode: streaming is **not started**. The `__main__` block skips `ig._get_session()` in paper mode, so no IG credentials are available. `get_mark_price()` continues to use its existing REST call path in paper mode (the `ImportError` guard in `ig_client.py` means no crash). The tick pause check is also skipped in paper mode (`if not self.paper`).

---

## `ig._get_session()` Return Value

The current `_get_session()` must return (or expose) `account_id`, `cst`, `xst`, and `ls_endpoint`. The IG `POST /session` response contains all four:

```json
{
  "accountId": "...",
  "lightstreamerEndpoint": "https://...",
  "X-IG-API-KEY": "...",
  "CST": "...",
  "X-SECURITY-TOKEN": "..."
}
```

`_get_session()` will be updated to return a dict with these fields using the following keys:

```python
{
    "account_id":  str,   # from response["accountId"]
    "cst":         str,   # from response header "CST"
    "xst":         str,   # from response header "X-SECURITY-TOKEN"
    "ls_endpoint": str,   # from response["lightstreamerEndpoint"]
}
```

The existing session object (used for subsequent REST calls) is unchanged — this dict is returned in addition.

---

## Error Handling

| Scenario | Behaviour |
|----------|-----------|
| Network blip | SDK auto-retries; `_connected=False` pauses tick; resumes on reconnect |
| Token expiry (~6h) | `needs_reauth=True`; next tick refreshes session and restarts stream |
| Stream update parse error | Log warning, skip update; stream stays connected |
| WOU/OPU for unknown deal_id | Log warning, ignore; no state mutation |
| `_handle_pending_filled` called twice (stream + poll race) | Second call is a no-op because `pending_orders[name]` is already None |

---

## Out of Scope

- CHART stream subscription (would need candle assembly logic; REST cache already minimises calls)
- Streaming for backtest (`backtest_ig.py` is unchanged)
- Streaming for `bot.py` / Bitget (IG-only change)

---

## Dependencies

```
pip install lightstreamer-client-lib
```

No other new dependencies.
