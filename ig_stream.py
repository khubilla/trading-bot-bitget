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
    _mark_cache.clear()


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
