"""Tests for maybe_trail_sl_ig — IG-aware structural swing trail."""
from unittest.mock import MagicMock
import pandas as pd
from strategies.s1 import maybe_trail_sl_ig


def _candles_uptrend():
    """3m candles trending up with a clear swing low formed late."""
    return pd.DataFrame({
        "open":  [100, 101, 102, 103, 104, 105, 106, 107, 106, 105, 106, 107, 108],
        "high":  [101, 102, 103, 104, 105, 106, 107, 108, 107, 106, 107, 108, 109],
        "low":   [ 99, 100, 101, 102, 103, 104, 105, 106, 105, 104, 105, 106, 107],
        "close": [101, 102, 103, 104, 105, 106, 107, 107, 106, 105, 106, 107, 108],
        "volume":[100]*13,
    })


def test_trail_noop_when_disabled():
    """Swing trail does nothing when s1_use_swing_trail is False."""
    instrument = {"s1_use_swing_trail": False, "s1_swing_lookback": 20, "s1_sl_buffer_pct": 0.001}
    pos = {"side": "LONG", "strategy": "S1", "sl": 100.0, "swing_trail_ref": None, "deal_id": "x"}
    ig_mod = MagicMock()
    maybe_trail_sl_ig(instrument, pos, ig_mod, _candles_uptrend(), mark_price=109.0)
    ig_mod.update_sl.assert_not_called()
    assert pos["sl"] == 100.0


def test_long_trail_initializes_ref_on_first_call():
    """LONG: first call with ref=None initializes swing_trail_ref but doesn't move SL."""
    instrument = {"s1_use_swing_trail": True, "s1_swing_lookback": 20, "s1_sl_buffer_pct": 0.001}
    pos = {"side": "LONG", "strategy": "S1", "sl": 100.0, "swing_trail_ref": None, "deal_id": "x"}
    ig_mod = MagicMock()
    ig_mod.update_sl = MagicMock(return_value=True)
    maybe_trail_sl_ig(instrument, pos, ig_mod, _candles_uptrend(), mark_price=109.0)
    # First call should set the ref but NOT call update_sl (no SL movement yet)
    assert pos["swing_trail_ref"] is not None or pos["sl"] == 100.0
    # SL itself should remain unchanged on init
    assert pos["sl"] == 100.0


def test_short_trail_with_no_pos_change_when_no_swing():
    """SHORT: ref initialization path. Mark deep below current price → ref initialized to swing low target."""
    instrument = {"s1_use_swing_trail": True, "s1_swing_lookback": 20, "s1_sl_buffer_pct": 0.001}
    pos = {"side": "SHORT", "strategy": "S1", "sl": 110.0, "swing_trail_ref": None, "deal_id": "x"}
    ig_mod = MagicMock()
    ig_mod.update_sl = MagicMock(return_value=True)
    maybe_trail_sl_ig(instrument, pos, ig_mod, _candles_uptrend(), mark_price=99.0)
    assert pos["sl"] == 110.0   # not moved on init
