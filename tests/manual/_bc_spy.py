# tests/manual/_bc_spy.py
"""
Spy context manager for manual strategy tests.

Usage:
    with bc_spy(symbol="BTCUSDT", mark_price=50_000.0, hold_side="long"):
        b = make_bot()
        b._fire_s2("BTCUSDT", sig, mark=50_000.0, balance=10_000.0)

Every bc.post / bc.get call is printed with its full payload.
All state/logging side-effects (state.json writes, CSV, snapshots) are suppressed.
"""
import json
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from contextlib import contextmanager
from unittest.mock import patch, MagicMock

import trader


def _print_call(method: str, endpoint: str, payload):
    sep = "─" * 60
    print(f"\n{sep}")
    print(f"[API] {method} {endpoint}")
    if payload:
        print("      " + json.dumps(payload, indent=2).replace("\n", "\n      "))
    print(sep)


@contextmanager
def bc_spy(
    symbol: str = "BTCUSDT",
    mark_price: float = 50_000.0,
    fill_price: float = None,     # defaults to mark_price
    init_qty: float = 0.002,      # qty returned after initial place-order
    scale_in_qty: float = 0.004,  # qty returned on 2nd+ all-position calls (scale-in poll)
    hold_side: str = "long",
):
    """
    Context manager that:
    1. Pre-populates trader._sym_cache for `symbol` so _round_qty/_round_price work.
    2. Patches trader.bc.post and trader.bc.get with spies that print + return canned data.
    3. Patches time.sleep to 0 (avoids 2-second post-order wait).
    4. Suppresses all bot state/logging side-effects.
    """
    _fill = fill_price if fill_price is not None else mark_price

    # Pre-populate symbol cache so _round_qty/_round_price don't need real API
    trader._sym_cache[symbol] = {
        "price_place":   1,      # 1 decimal place (e.g. 50000.1)
        "volume_place":  3,      # 3 decimal places for qty
        "size_mult":     0.001,
        "min_trade_num": 0.001,
    }

    # Track all-position call count so scale-in poll gets updated qty
    _pos_call = [0]

    def _mock_post(endpoint, payload=None, **kw):
        _print_call("POST", endpoint, payload)
        if "place-order" in endpoint:
            return {"data": {"orderId": "mock-order-001"}, "code": "00000"}
        if "place-pos-tpsl" in endpoint:
            return {"msg": "success", "code": "00000"}
        if "place-tpsl-order" in endpoint:
            return {"data": {"orderId": "plan-mock-001"}, "code": "00000"}
        if "cancel-plan-order" in endpoint:
            return {"msg": "success", "code": "00000"}
        if "cancel-all-orders" in endpoint:
            return {"msg": "success", "code": "00000"}
        if "cancel-order" in endpoint:
            return {"msg": "success", "code": "00000"}
        if "set-leverage" in endpoint:
            return {"msg": "success", "code": "00000"}
        return {"msg": "success", "code": "00000"}

    def _mock_get(endpoint, params=None, **kw):
        _print_call("GET ", endpoint, params)
        if "all-position" in endpoint:
            _pos_call[0] += 1
            qty = init_qty if _pos_call[0] <= 1 else scale_in_qty
            return {"data": [{
                "symbol":       symbol,
                "holdSide":     hold_side,
                "openPriceAvg": str(_fill),
                "total":        str(qty),
                "available":    str(qty),
                "unrealizedPL": "0",
                "markPrice":    str(mark_price),
                "marginSize":   str(round(_fill * qty / 10, 4)),
                "leverage":     "10",
            }]}
        if "accounts" in endpoint:
            return {"data": [{"available": "10000", "equity": "10000"}]}
        if "symbol-price" in endpoint:
            return {"data": [{"markPrice": str(mark_price)}]}
        if "plan-orders" in endpoint:
            # Return two existing plan orders so refresh_plan_exits can cancel + replace
            return {"data": {"entrustedList": [
                {
                    "orderId":      "pp-existing-001",
                    "planType":     "profit_plan",
                    "holdSide":     hold_side,
                    "triggerPrice": str(round(mark_price * 1.10, 1)),
                    "size":         str(init_qty / 2),
                },
                {
                    "orderId":      "mp-existing-002",
                    "planType":     "moving_plan",
                    "holdSide":     hold_side,
                    "triggerPrice": str(round(mark_price * 1.10, 1)),
                    "size":         str(init_qty / 2),
                    "rangeRate":    "15",
                },
            ]}}
        if "order/detail" in endpoint:
            return {"data": {"state": "filled", "fillPrice": str(_fill)}}
        if "history-position" in endpoint:
            return {"data": {"list": []}}
        return {"data": [], "code": "00000"}

    def _mock_get_public(endpoint, params=None, **kw):
        _print_call("GET*", endpoint, params)
        if "contracts" in endpoint:
            return {"data": []}
        if "candles" in endpoint:
            return {"data": []}
        if "symbol-price" in endpoint:
            return {"data": [{"markPrice": str(mark_price)}]}
        return {"data": []}

    patches = [
        patch.object(trader.bc, "post",       side_effect=_mock_post),
        patch.object(trader.bc, "get",        side_effect=_mock_get),
        patch.object(trader.bc, "get_public", side_effect=_mock_get_public),
        patch("time.sleep"),                          # suppress all sleeps
        # Suppress bot state / logging side-effects
        patch("bot.st.add_scan_log",          new=MagicMock()),
        patch("bot.st.add_open_trade",        new=MagicMock()),
        patch("bot.st.save_pending_signals",  new=MagicMock()),
        patch("bot.st.get_pair_state",        return_value={}),
        patch("bot.st.patch_pair_state",      new=MagicMock()),
        patch("bot.st.update_open_trade_margin",    new=MagicMock()),
        patch("bot.st.update_position_memory",      new=MagicMock()),
        patch("bot.st.is_pair_paused",        return_value=False),
        patch("bot._log_trade",               new=MagicMock()),
        patch("bot.snapshot.save_snapshot",   new=MagicMock()),
        patch("bot.config.CLAUDE_FILTER_ENABLED", new=False),
        patch("bot.PAPER_MODE",               new=False),
    ]

    started = []
    try:
        for p in patches:
            started.append(p.start())
        yield
    finally:
        for p in reversed(patches):
            p.stop()
