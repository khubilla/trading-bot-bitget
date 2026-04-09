# tests/manual/_bot_factory.py
"""
Builds a bare MTFBot instance without starting the run loop.

Usage (always inside a bc_spy context):
    with bc_spy(...):
        b = make_bot()
        b._fire_s2("BTCUSDT", sig, mark=50_000.0, balance=10_000.0)
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import threading
import bot


def make_bot() -> bot.MTFBot:
    """
    Creates MTFBot via object.__new__ (bypasses __init__/run loop).
    Call inside bc_spy() — that context manager handles all state patches.
    """
    b = object.__new__(bot.MTFBot)
    b.pending_signals  = {}
    b.active_positions = {}
    b._trade_lock      = threading.Lock()
    b.running          = True
    b.sentiment        = type("Sentiment", (), {"direction": "NEUTRAL"})()
    return b
