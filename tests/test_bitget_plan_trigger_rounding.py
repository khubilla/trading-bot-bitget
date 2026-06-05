"""
Regression: Bitget place_profit_plan / place_moving_plan must round the
trigger price to the symbol's price tick before posting.

Root cause (HUSDT 4c326063, 2026-06-04): `bitget.refresh_plan_exits` called
`place_profit_plan` / `place_moving_plan` with a raw float trail trigger
computed as `new_avg * (1 - S4_TRAILING_TRIGGER_PCT)` =
`0.55776 * 0.9 = 0.5019798103443…` (13 decimals). Both functions did
`"triggerPrice": str(trigger)`, sending the unrounded value. Bitget rejected:

    HTTP 400 [40808]: Parameter verification exception trigger price
    checkBDScale error value=0.5019798103443 checkScale=5

All 3 retries failed; the cancel-then-replace in `refresh_plan_exits` had
already cancelled the old `profit_plan` + `moving_plan`, leaving the
post-scale-in position without partial-TP or trailing protection. When HUSDT
gapped through the SL trigger during a market-wide pump, the position
auto-liquidated at -99.4% margin.

Fix: round the trigger via `round_price(trigger, symbol)` exactly the way
`bybit.place_profit_plan` / `binance.place_profit_plan` already do.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import bitget
import bitget_client as bc


# HUSDT has price_place=5 on Bitget (tick = 0.00001). Stub sym_info so the
# tests don't depend on the live symbol cache.
@pytest.fixture(autouse=True)
def _husdt_price_tick(monkeypatch):
    monkeypatch.setattr(
        bitget, "sym_info",
        lambda symbol: {"price_place": 5, "volume_place": 0,
                        "size_mult": 1.0, "min_trade_num": 1.0},
    )


def test_place_profit_plan_rounds_trigger_to_symbol_tick(monkeypatch):
    """The trigger price posted to Bitget must be rounded to the symbol's
    price tick. Sending 0.5019798103443 with checkScale=5 triggers HTTP
    400 [40808] and silently breaks scale-in exits."""
    posts = []
    monkeypatch.setattr(bc, "post", lambda path, body: posts.append((path, body)) or {})

    bitget.place_profit_plan("HUSDT", "short", "116", 0.5019798103443)

    path, body = posts[-1]
    assert path == "/api/v2/mix/order/place-tpsl-order"
    assert body["planType"] == "profit_plan"
    # 0.5019798103443 → 0.50198 at 5-decimal tick
    assert body["triggerPrice"] == "0.50198", body["triggerPrice"]
    # Bitget rejects any value whose decimal count exceeds checkScale.
    decimals = len(body["triggerPrice"].split(".")[1])
    assert decimals <= 5, f"triggerPrice has {decimals} decimals, max 5"


def test_place_moving_plan_rounds_trigger_to_symbol_tick(monkeypatch):
    """Same precision requirement applies to the trailing-stop plan."""
    posts = []
    monkeypatch.setattr(bc, "post", lambda path, body: posts.append((path, body)) or {})

    bitget.place_moving_plan("HUSDT", "short", "116", 0.5019798103443, "10")

    path, body = posts[-1]
    assert path == "/api/v2/mix/order/place-tpsl-order"
    assert body["planType"] == "moving_plan"
    assert body["triggerPrice"] == "0.50198", body["triggerPrice"]
    decimals = len(body["triggerPrice"].split(".")[1])
    assert decimals <= 5, f"triggerPrice has {decimals} decimals, max 5"


def test_place_profit_plan_already_rounded_value_is_idempotent(monkeypatch):
    """When the caller already passes a tick-aligned value, rounding is a
    no-op — no spurious last-digit drift."""
    posts = []
    monkeypatch.setattr(bc, "post", lambda path, body: posts.append((path, body)) or {})

    bitget.place_profit_plan("HUSDT", "long", "100", 0.55776)

    _, body = posts[-1]
    assert body["triggerPrice"] == "0.55776"
