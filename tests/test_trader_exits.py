import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import bitget_client as bc
import trader


# ── Helpers ───────────────────────────────────────────────────────── #

def _sym_info_stousdt(symbol):
    """STOUSDT: integer qty (volume_place=0, size_mult=1)."""
    return {"price_place": 5, "volume_place": 0, "size_mult": 1.0, "min_trade_num": 1.0}


def _sym_info_btcusdt(symbol):
    """BTCUSDT: 3 decimal qty (volume_place=3, size_mult=0.001)."""
    return {"price_place": 1, "volume_place": 3, "size_mult": 0.001, "min_trade_num": 0.001}


# ── _place_s2_exits: integer half/rest qty ─────────────────────────── #

class TestPlaceS2ExitsQty:
    """half_qty and rest_qty must be exchange-compatible (no fractional sizes)."""

    def test_odd_qty_integer_symbol_no_fractions(self, monkeypatch):
        """qty=209 STOUSDT → half=104, rest=105 (not 104.5)."""
        calls = []
        monkeypatch.setattr(trader, "_sym_info", _sym_info_stousdt)
        monkeypatch.setattr(bc, "post", lambda path, p: calls.append(p) or {})

        trader._place_s2_exits("STOUSDT", "long", "209", 0.13, 0.129, 0.17, 10)

        sizes = [c["size"] for c in calls if "size" in c]
        assert "104.5" not in sizes, "fractional size sent to exchange"
        assert sizes[0] == "104.0"   # profit_plan half
        assert sizes[1] == "105.0"   # moving_plan rest

    def test_even_qty_splits_evenly(self, monkeypatch):
        """qty=200 → half=100, rest=100."""
        calls = []
        monkeypatch.setattr(trader, "_sym_info", _sym_info_stousdt)
        monkeypatch.setattr(bc, "post", lambda path, p: calls.append(p) or {})

        trader._place_s2_exits("STOUSDT", "long", "200", 0.13, 0.129, 0.17, 10)

        sizes = [c["size"] for c in calls if "size" in c]
        assert sizes[0] == "100.0"
        assert sizes[1] == "100.0"

    def test_decimal_symbol_rounds_correctly(self, monkeypatch):
        """qty=0.003 BTC (volume_place=3) → half=0.001, rest=0.002."""
        calls = []
        monkeypatch.setattr(trader, "_sym_info", _sym_info_btcusdt)
        monkeypatch.setattr(bc, "post", lambda path, p: calls.append(p) or {})

        trader._place_s2_exits("BTCUSDT", "long", "0.003", 55000, 54725, 65000, 10)

        sizes = [c["size"] for c in calls if "size" in c]
        assert sizes[0] == "0.001"
        assert sizes[1] == "0.002"


# ── _place_s5_exits: integer half/rest qty ─────────────────────────── #

class TestPlaceS5ExitsQty:
    def test_odd_qty_no_fractions(self, monkeypatch):
        """qty=209 STOUSDT → half=104, rest=105."""
        calls = []
        monkeypatch.setattr(trader, "_sym_info", _sym_info_stousdt)
        monkeypatch.setattr(bc, "post", lambda path, p: calls.append(p) or {})

        # tp_target=0 → trailing stop path
        trader._place_s5_exits("STOUSDT", "long", "209", 0.13, 0.129, 0.17, 0.0, 10.0)

        sizes = [c["size"] for c in calls if "size" in c]
        assert "104.5" not in sizes
        assert sizes[0] == "104.0"   # profit_plan
        assert sizes[1] == "105.0"   # moving_plan (rest)

    def test_hard_tp_path_uses_rest_qty(self, monkeypatch):
        """tp_target > 0 → hard TP uses rest_qty, not a duplicate half."""
        calls = []
        monkeypatch.setattr(trader, "_sym_info", _sym_info_stousdt)
        monkeypatch.setattr(bc, "post", lambda path, p: calls.append(p) or {})

        trader._place_s5_exits("STOUSDT", "long", "209", 0.13, 0.129, 0.17, 0.20, 10.0)

        sizes = [c["size"] for c in calls if "size" in c]
        assert sizes[0] == "104.0"   # partial TP (profit_plan)
        assert sizes[1] == "105.0"   # hard TP (rest)


# ── open_long SL cap ──────────────────────────────────────────────── #

class TestOpenLongSLCap:
    """box_low far below entry must be capped at stop_loss_pct below fill price."""

    def _setup(self, monkeypatch, mark=0.15324, box_low=0.10000, stop_loss_pct=0.05, fill=None):
        sl_calls = []
        monkeypatch.setattr(trader, "_sym_info", _sym_info_stousdt)
        monkeypatch.setattr(trader, "get_usdt_balance", lambda: 100.0)
        monkeypatch.setattr(trader, "_get_total_equity", lambda: 100.0)
        monkeypatch.setattr(trader, "get_mark_price", lambda sym: mark)
        monkeypatch.setattr(trader, "set_leverage", lambda *a: None)
        monkeypatch.setattr(bc, "post", lambda path, p: sl_calls.append(p) or {})

        _fill = fill if fill is not None else mark
        monkeypatch.setattr(trader, "get_all_open_positions",
            lambda: {"STOUSDT": {"entry_price": _fill, "qty": 65.0}})

        import time
        monkeypatch.setattr(time, "sleep", lambda s: None)

        import config_s2
        monkeypatch.setattr(config_s2, "S2_TRAILING_TRIGGER_PCT", 0.10)
        monkeypatch.setattr(config_s2, "S2_TRAILING_RANGE_PCT", 10)

        result = trader.open_long(
            "STOUSDT", box_low=box_low,
            leverage=10, trade_size_pct=0.1,
            stop_loss_pct=stop_loss_pct,
            use_s2_exits=True,
        )
        return result, sl_calls

    def test_box_low_too_far_sl_capped(self, monkeypatch):
        """box_low 35% below entry — SL must be capped at 5% below fill price."""
        result, _ = self._setup(monkeypatch, mark=0.15324, box_low=0.10000, stop_loss_pct=0.05)
        cap = 0.15324 * 0.95
        assert result["sl"] <= cap * 1.001, f"SL {result['sl']} exceeds cap {cap}"

    def test_box_low_close_uses_box_low(self, monkeypatch):
        """box_low only 2% below entry — SL follows box_low * 0.999 (above the cap floor)."""
        result, _ = self._setup(monkeypatch, mark=0.15324, box_low=0.15020, stop_loss_pct=0.05)
        expected_sl = 0.15020 * 0.999   # ≈ 0.15005
        cap = 0.15324 * 0.95            # ≈ 0.14558 — box_low is above this so cap not triggered
        assert result["sl"] >= expected_sl * 0.999, "SL should not be lower than box_low * 0.999"
        assert result["sl"] > cap, "box_low within 5% — cap should not activate"

    def test_fill_price_used_for_trail_trigger(self, monkeypatch):
        """When fill price differs from mark, trail trigger must be based on fill."""
        result, sl_calls = self._setup(monkeypatch, mark=0.15, box_low=0.14, fill=0.151)
        # trail_trig = fill * 1.10 = 0.1661
        expected_trail = round(0.151 * 1.10, 5)
        profit_plan = next(c for c in sl_calls if c.get("planType") == "profit_plan")
        actual_trig = float(profit_plan["triggerPrice"])
        assert abs(actual_trig - expected_trail) < 0.0001, (
            f"trail trigger {actual_trig} != expected {expected_trail}"
        )

    def test_fill_price_returned_as_entry(self, monkeypatch):
        """Return dict entry field must be the fill price, not the pre-order mark."""
        result, _ = self._setup(monkeypatch, mark=0.15, box_low=0.14, fill=0.152)
        assert result["entry"] == 0.152, f"entry {result['entry']} != fill 0.152"

    def test_fill_fallback_to_mark_when_position_not_visible(self, monkeypatch):
        """If get_all_open_positions returns empty, exit levels fall back to mark."""
        sl_calls = []
        monkeypatch.setattr(trader, "_sym_info", _sym_info_stousdt)
        monkeypatch.setattr(trader, "get_usdt_balance", lambda: 100.0)
        monkeypatch.setattr(trader, "_get_total_equity", lambda: 100.0)
        monkeypatch.setattr(trader, "get_mark_price", lambda sym: 0.15)
        monkeypatch.setattr(trader, "set_leverage", lambda *a: None)
        monkeypatch.setattr(bc, "post", lambda path, p: sl_calls.append(p) or {})
        monkeypatch.setattr(trader, "get_all_open_positions", lambda: {})

        import time
        monkeypatch.setattr(time, "sleep", lambda s: None)

        import config_s2
        monkeypatch.setattr(config_s2, "S2_TRAILING_TRIGGER_PCT", 0.10)
        monkeypatch.setattr(config_s2, "S2_TRAILING_RANGE_PCT", 10)

        result = trader.open_long(
            "STOUSDT", box_low=0.14,
            leverage=10, trade_size_pct=0.1,
            stop_loss_pct=0.05,
            use_s2_exits=True,
        )
        # Falls back to mark=0.15 → trail_trig = 0.15 * 1.10 = 0.165
        assert result["entry"] == 0.15
        profit_plan = next(c for c in sl_calls if c.get("planType") == "profit_plan")
        assert abs(float(profit_plan["triggerPrice"]) - 0.165) < 0.0001


# ── refresh_plan_exits ────────────────────────────────────────────── #

class TestRefreshPlanExits:

    def _make_plan_orders(self):
        return {
            "data": {
                "entrustedList": [
                    {"orderId": "PP1", "planType": "profit_plan",
                     "holdSide": "long", "triggerPrice": "0.17", "size": "104"},
                    {"orderId": "MP1", "planType": "moving_plan",
                     "holdSide": "long", "triggerPrice": "0.17", "rangeRate": "10", "size": "105"},
                ]
            }
        }

    def test_cancels_then_replaces(self, monkeypatch):
        """Existing orders are cancelled and new ones placed with updated sizes."""
        monkeypatch.setattr(trader, "_sym_info", _sym_info_stousdt)
        monkeypatch.setattr(bc, "get",
            lambda path, params=None: self._make_plan_orders() if "plan-orders" in path else {})
        monkeypatch.setattr(trader, "get_all_open_positions",
            lambda: {"STOUSDT": {"side": "LONG", "qty": 314.0, "entry_price": 0.154}})

        import time
        monkeypatch.setattr(time, "sleep", lambda s: None)

        cancel_ids = []
        place_calls = []

        def fake_post(path, payload):
            if "cancel" in path:
                cancel_ids.append(payload.get("orderId"))
            else:
                place_calls.append(payload)
            return {}

        monkeypatch.setattr(bc, "post", fake_post)

        result = trader.refresh_plan_exits("STOUSDT", "long")

        assert result is True
        assert set(cancel_ids) == {"PP1", "MP1"}

        placed_sizes = [p["size"] for p in place_calls if "size" in p]
        assert placed_sizes[0] == "157.0"   # profit_plan half of 314
        assert placed_sizes[1] == "157.0"   # moving_plan rest of 314 (even split)

    def test_odd_total_qty_correct_split(self, monkeypatch):
        """Total qty=315 → half=157, rest=158."""
        monkeypatch.setattr(trader, "_sym_info", _sym_info_stousdt)
        monkeypatch.setattr(bc, "get",
            lambda path, params=None: self._make_plan_orders() if "plan-orders" in path else {})
        monkeypatch.setattr(trader, "get_all_open_positions",
            lambda: {"STOUSDT": {"side": "LONG", "qty": 315.0, "entry_price": 0.154}})

        import time
        monkeypatch.setattr(time, "sleep", lambda s: None)

        place_calls = []

        def fake_post(path, payload):
            if "cancel" not in path:
                place_calls.append(payload)
            return {}

        monkeypatch.setattr(bc, "post", fake_post)

        trader.refresh_plan_exits("STOUSDT", "long")

        sizes = [p["size"] for p in place_calls if "size" in p]
        assert sizes[0] == "157.0"   # profit_plan half of 315
        assert sizes[1] == "158.0"   # moving_plan rest of 315

    def test_returns_false_if_no_plan_orders(self, monkeypatch):
        monkeypatch.setattr(bc, "get", lambda path, params=None: {"data": {"entrustedList": []}})

        result = trader.refresh_plan_exits("STOUSDT", "long")
        assert result is False

    def test_preserves_trigger_price_from_existing_order(self, monkeypatch):
        """New orders must use the trigger price from the existing profit_plan."""
        monkeypatch.setattr(trader, "_sym_info", _sym_info_stousdt)
        monkeypatch.setattr(bc, "get",
            lambda path, params=None: self._make_plan_orders() if "plan-orders" in path else {})
        monkeypatch.setattr(trader, "get_all_open_positions",
            lambda: {"STOUSDT": {"qty": 200.0}})

        import time
        monkeypatch.setattr(time, "sleep", lambda s: None)

        place_calls = []

        def fake_post(path, payload):
            if "cancel" not in path:
                place_calls.append(payload)
            return {}

        monkeypatch.setattr(bc, "post", fake_post)

        trader.refresh_plan_exits("STOUSDT", "long")

        for p in place_calls:
            assert p.get("triggerPrice") == "0.17"


    def test_new_trail_trigger_overrides_existing_order_trigger(self, monkeypatch):
        """When new_trail_trigger is passed, placed orders must use it, not the old order's trigger."""
        monkeypatch.setattr(trader, "_sym_info", _sym_info_stousdt)
        monkeypatch.setattr(bc, "get",
            lambda path, params=None: self._make_plan_orders() if "plan-orders" in path else {})
        monkeypatch.setattr(trader, "get_all_open_positions",
            lambda: {"STOUSDT": {"qty": 200.0}})

        import time
        monkeypatch.setattr(time, "sleep", lambda s: None)

        place_calls = []

        def fake_post(path, payload):
            if "cancel" not in path:
                place_calls.append(payload)
            return {}

        monkeypatch.setattr(bc, "post", fake_post)

        # Pass a new trigger (0.19) — existing profit_plan has 0.17
        trader.refresh_plan_exits("STOUSDT", "long", new_trail_trigger=0.19)

        for p in place_calls:
            assert p.get("triggerPrice") == "0.19", (
                f"Expected triggerPrice 0.19 but got {p.get('triggerPrice')}"
            )

    def test_zero_new_trail_trigger_preserves_existing_trigger(self, monkeypatch):
        """new_trail_trigger=0 (default) must fall back to the existing profit_plan trigger."""
        monkeypatch.setattr(trader, "_sym_info", _sym_info_stousdt)
        monkeypatch.setattr(bc, "get",
            lambda path, params=None: self._make_plan_orders() if "plan-orders" in path else {})
        monkeypatch.setattr(trader, "get_all_open_positions",
            lambda: {"STOUSDT": {"qty": 200.0}})

        import time
        monkeypatch.setattr(time, "sleep", lambda s: None)

        place_calls = []

        def fake_post(path, payload):
            if "cancel" not in path:
                place_calls.append(payload)
            return {}

        monkeypatch.setattr(bc, "post", fake_post)

        trader.refresh_plan_exits("STOUSDT", "long")  # no new_trail_trigger

        for p in place_calls:
            assert p.get("triggerPrice") == "0.17"
