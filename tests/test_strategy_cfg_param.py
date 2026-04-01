import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pandas as pd

# Minimal DataFrame — enough rows for EMA calculations to not crash
_DF = pd.DataFrame({
    "open":   [100.0] * 60,
    "high":   [101.0] * 60,
    "low":    [99.0]  * 60,
    "close":  [100.0] * 60,
    "volume": [1000.0]* 60,
})

_BASE_CFG = {
    "s5_enabled":                    False,  # disabled → fast return
    "s5_daily_ema_fast":             10,
    "s5_daily_ema_med":              20,
    "s5_daily_ema_slow":             50,
    "s5_htf_bos_lookback":           5,
    "s5_ob_lookback":                20,
    "s5_ob_min_impulse":             0.005,
    "s5_ob_min_range_pct":           0.002,
    "s5_choch_lookback":             10,
    "s5_max_entry_buffer":           0.01,
    "s5_sl_buffer_pct":              0.002,
    "s5_min_rr":                     2.0,
    "s5_swing_lookback":             20,
    "s5_smc_fvg_filter":             False,
    "s5_smc_fvg_lookback":           10,
}


def test_evaluate_s5_accepts_cfg_keyword():
    """evaluate_s5() accepts a cfg= keyword argument without raising TypeError."""
    from strategy import evaluate_s5
    result = evaluate_s5("TEST", _DF, _DF, _DF, "LONG", cfg=_BASE_CFG)
    assert result is not None



def test_evaluate_s5_cfg_disabled_returns_hold():
    """When cfg has s5_enabled=False, returns 'HOLD'."""
    from strategy import evaluate_s5
    sig, *_ = evaluate_s5("TEST", _DF, _DF, _DF, "LONG", cfg={**_BASE_CFG, "s5_enabled": False})
    assert sig == "HOLD"


def test_evaluate_s5_no_cfg_uses_bitget_path(monkeypatch):
    """When cfg=None (default), the Bitget path is used (config_s5 module)."""
    import config_s5
    original = config_s5.S5_ENABLED
    try:
        config_s5.S5_ENABLED = False
        from strategy import evaluate_s5
        sig, *_ = evaluate_s5("TEST", _DF, _DF, _DF, "LONG")
        assert sig == "HOLD"
    finally:
        config_s5.S5_ENABLED = original
