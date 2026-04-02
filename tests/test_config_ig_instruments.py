import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

_REQUIRED_KEYS = {
    "epic", "display_name", "currency",
    "contract_size", "partial_size", "point_value",
    "session_start", "session_end",
    "daily_limit", "htf_limit", "m15_limit",
    "s5_enabled", "s5_daily_ema_fast", "s5_daily_ema_med", "s5_daily_ema_slow",
    "s5_htf_bos_lookback", "s5_ltf_interval", "s5_ob_lookback",
    "s5_ob_min_impulse", "s5_ob_min_range_pct", "s5_choch_lookback",
    "s5_max_entry_buffer", "s5_sl_buffer_pct", "s5_ob_invalidation_buffer_pct",
    "s5_swing_lookback", "s5_smc_fvg_filter", "s5_smc_fvg_lookback",
    "s5_leverage", "s5_trade_size_pct", "s5_min_rr",
    "s5_trail_range_pct", "s5_use_candle_stops", "s5_min_sr_clearance",
}

def test_config_ig_us30_has_all_required_keys():
    from config_ig_us30 import CONFIG
    missing = _REQUIRED_KEYS - CONFIG.keys()
    assert not missing, f"Missing keys: {missing}"

def test_config_ig_gold_has_all_required_keys():
    from config_ig_gold import CONFIG
    missing = _REQUIRED_KEYS - CONFIG.keys()
    assert not missing, f"Missing keys: {missing}"

def test_config_ig_instruments_list_has_us30_and_gold():
    from config_ig import INSTRUMENTS
    names = [i["display_name"] for i in INSTRUMENTS]
    assert "US30" in names
    assert "GOLD" in names

def test_config_ig_has_shared_settings():
    import config_ig
    assert hasattr(config_ig, "STATE_FILE")
    assert hasattr(config_ig, "TRADE_LOG")
    assert hasattr(config_ig, "LOG_FILE")
    assert hasattr(config_ig, "POLL_INTERVAL_SEC")
    assert hasattr(config_ig, "SESSION_START")
    assert hasattr(config_ig, "SESSION_END")

def test_config_ig_us30_values():
    from config_ig_us30 import CONFIG
    assert CONFIG["epic"] == "IX.D.DOW.IFD.IP"
    assert CONFIG["display_name"] == "US30"
    assert CONFIG["s5_min_rr"] == 2.0
    assert CONFIG["s5_use_candle_stops"] is True

def test_config_ig_gold_values():
    from config_ig_gold import CONFIG
    assert CONFIG["epic"] == "CS.D.CFDGOLD.CFDGC.IP"
    assert CONFIG["display_name"] == "GOLD"
