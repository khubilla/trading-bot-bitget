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
