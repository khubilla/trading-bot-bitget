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
    assert CONFIG["s5_min_rr"] == 1
    assert CONFIG["s5_use_candle_stops"] is True

def test_config_ig_gold_values():
    from config_ig_gold import CONFIG
    assert CONFIG["epic"] == "CS.D.CFDGOLD.BMU.IP"
    assert CONFIG["display_name"] == "GOLD"


def test_validate_requires_s1_keys_when_s1_enabled(monkeypatch):
    """When s1_enabled=True, validator errors on missing S1 keys."""
    import pytest
    import ig_bot, config_ig
    bad = [{
        "epic": "X.X.X", "display_name": "X", "currency": "USD",
        "contract_size": 0.01, "partial_size": 0.005, "point_value": 1.0,
        "session_start": (0, 0), "session_end": (23, 59),
        "daily_limit": 100, "htf_limit": 50, "m15_limit": 100,
        "price_decimals": 1, "min_deal_distance": 1.0, "pending_expiry_hours": 4,
        "s5_enabled": False, "s5_daily_ema_fast": 10, "s5_daily_ema_med": 20, "s5_daily_ema_slow": 50,
        "s5_htf_bos_lookback": 10, "s5_ltf_interval": "15m", "s5_ob_lookback": 30,
        "s5_ob_min_impulse": 0.005, "s5_ob_min_range_pct": 0.001, "s5_choch_lookback": 10,
        "s5_max_entry_buffer": 0.01, "s5_sl_buffer_pct": 0.002, "s5_ob_invalidation_buffer_pct": 0.001,
        "s5_swing_lookback": 20, "s5_smc_fvg_filter": False, "s5_smc_fvg_lookback": 10,
        "s5_leverage": 1, "s5_trade_size_pct": 0.1, "s5_min_rr": 1.0,
        "s5_trail_range_pct": 5, "s5_use_candle_stops": True, "s5_min_sr_clearance": 0.10,
        "s1_enabled": True,   # but no S1 keys present
    }]
    monkeypatch.setattr(config_ig, "INSTRUMENTS", bad)
    with pytest.raises(KeyError) as exc:
        ig_bot._validate_instruments()
    assert "s1_enabled=True" in str(exc.value)


def test_validate_accepts_s1_disabled_without_s1_keys(monkeypatch):
    """When s1_enabled=False, S1 keys are not required."""
    import ig_bot, config_ig
    ok = [{
        "epic": "X.X.X", "display_name": "X", "currency": "USD",
        "contract_size": 0.01, "partial_size": 0.005, "point_value": 1.0,
        "session_start": (0, 0), "session_end": (23, 59),
        "daily_limit": 100, "htf_limit": 50, "m15_limit": 100,
        "price_decimals": 1, "min_deal_distance": 1.0, "pending_expiry_hours": 4,
        "s5_enabled": False, "s5_daily_ema_fast": 10, "s5_daily_ema_med": 20, "s5_daily_ema_slow": 50,
        "s5_htf_bos_lookback": 10, "s5_ltf_interval": "15m", "s5_ob_lookback": 30,
        "s5_ob_min_impulse": 0.005, "s5_ob_min_range_pct": 0.001, "s5_choch_lookback": 10,
        "s5_max_entry_buffer": 0.01, "s5_sl_buffer_pct": 0.002, "s5_ob_invalidation_buffer_pct": 0.001,
        "s5_swing_lookback": 20, "s5_smc_fvg_filter": False, "s5_smc_fvg_lookback": 10,
        "s5_leverage": 1, "s5_trade_size_pct": 0.1, "s5_min_rr": 1.0,
        "s5_trail_range_pct": 5, "s5_use_candle_stops": True, "s5_min_sr_clearance": 0.10,
        "s1_enabled": False,
    }]
    monkeypatch.setattr(config_ig, "INSTRUMENTS", ok)
    ig_bot._validate_instruments()   # no raise
