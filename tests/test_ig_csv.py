"""Tests that ig_trades.csv has the S1 snap columns appended (T13)."""
import ig_bot


def test_trade_fields_includes_snap_strategy_and_s1_snaps():
    fields = ig_bot._TRADE_FIELDS
    for col in (
        "snap_strategy",
        "snap_s1_rsi", "snap_s1_adx",
        "snap_s1_box_high", "snap_s1_box_low",
        "snap_s1_atr", "snap_s1_sr_clearance_atr",
    ):
        assert col in fields, f"missing {col} in _TRADE_FIELDS"


def test_trade_fields_preserves_existing_s5_columns():
    """T13 is additive — no existing column removed."""
    fields = ig_bot._TRADE_FIELDS
    for col in (
        "timestamp", "trade_id", "action", "symbol", "side", "qty",
        "entry", "sl", "tp", "snap_entry_trigger", "snap_sl", "snap_rr",
        "snap_s5_ob_low", "snap_s5_ob_high", "snap_s5_tp",
        "result", "pnl", "exit_reason", "session_date", "mode",
    ):
        assert col in fields


def test_trade_fields_includes_exit_price():
    """exit_price is now persisted (was silently dropped by extrasaction='ignore')."""
    assert "exit_price" in ig_bot._TRADE_FIELDS
