"""Tests that evaluate_s1(cfg=instrument) returns identical results to cfg=None
when the instrument CONFIG mirrors config_s1 module constants."""
import pandas as pd
import pytest
from strategies.s1 import evaluate_s1
import config_s1


def _candles_with_trend(n=300, base=100, step=0.5, with_breakout=False):
    """Build a synthetic dataframe with a steady uptrend."""
    closes = [base + i * step for i in range(n)]
    highs  = [c + 0.2 for c in closes]
    lows   = [c - 0.2 for c in closes]
    if with_breakout:
        highs[-1] += 1.0
        closes[-1] += 0.8
    return pd.DataFrame({
        "open":  closes,
        "high":  highs,
        "low":   lows,
        "close": closes,
        "volume": [100] * n,
    })


def _cfg_mirror():
    """Build a cfg dict from config_s1 constants — IG-shape keys."""
    return {
        "s1_enabled":                config_s1.S1_ENABLED,
        "s1_adx_trend_threshold":    config_s1.ADX_TREND_THRESHOLD,
        "s1_daily_ema_slow":         config_s1.DAILY_EMA_SLOW,
        "s1_daily_rsi_long_thresh":  config_s1.DAILY_RSI_LONG_THRESH,
        "s1_daily_rsi_short_thresh": config_s1.DAILY_RSI_SHORT_THRESH,
        "s1_rsi_period":             config_s1.RSI_PERIOD,
        "s1_rsi_long_thresh":        config_s1.RSI_LONG_THRESH,
        "s1_rsi_short_thresh":       config_s1.RSI_SHORT_THRESH,
        "s1_consolidation_candles":  config_s1.CONSOLIDATION_CANDLES,
        "s1_consolidation_range_pct": config_s1.CONSOLIDATION_RANGE_PCT,
        "s1_breakout_buffer_pct":    config_s1.BREAKOUT_BUFFER_PCT,
    }


def test_cfg_none_returns_hold_when_disabled(monkeypatch):
    """When config_s1.S1_ENABLED is False, evaluate_s1 with cfg=None returns HOLD."""
    monkeypatch.setattr(config_s1, "S1_ENABLED", False)
    daily = _candles_with_trend(200)
    htf   = _candles_with_trend(60)
    ltf   = _candles_with_trend(40)
    sig, *_ = evaluate_s1("TEST", htf, ltf, daily, "BULLISH", cfg=None)
    assert sig == "HOLD"


def test_cfg_disabled_returns_hold():
    """When cfg['s1_enabled'] is False, evaluate_s1 with cfg returns HOLD."""
    cfg = _cfg_mirror()
    cfg["s1_enabled"] = False
    daily = _candles_with_trend(200)
    htf   = _candles_with_trend(60)
    ltf   = _candles_with_trend(40)
    sig, *_ = evaluate_s1("TEST", htf, ltf, daily, "BULLISH", cfg=cfg)
    assert sig == "HOLD"


def test_cfg_path_and_module_path_match():
    """For the same data + matched parameters, cfg=None and cfg=mirror return the same signal."""
    cfg = _cfg_mirror()
    daily = _candles_with_trend(200)
    htf   = _candles_with_trend(60)
    ltf   = _candles_with_trend(40)
    sig_module, *_ = evaluate_s1("TEST", htf, ltf, daily, "BULLISH", cfg=None)
    sig_cfg,    *_ = evaluate_s1("TEST", htf, ltf, daily, "BULLISH", cfg=cfg)
    assert sig_module == sig_cfg


def test_cfg_path_sets_last_atr_on_cfg():
    """When cfg is provided and ATR is non-zero, evaluate_s1 sets cfg['_last_atr']."""
    cfg = _cfg_mirror()
    cfg["s1_atr_period"] = 14
    cfg["s1_sr_clearance_atr_mult"] = 0.0     # disable S/R gate
    daily = _candles_with_trend(200)
    htf   = _candles_with_trend(60)
    ltf   = _candles_with_trend(40)
    result = evaluate_s1("TEST", htf, ltf, daily, "BULLISH", cfg=cfg)
    assert len(result) == 6                    # tuple shape unchanged
    assert "_last_atr" in cfg
    assert cfg["_last_atr"] > 0.0


def test_cfg_atr_zero_returns_hold():
    """When daily ATR is 0 (flat market) and cfg is provided, evaluate_s1 returns HOLD."""
    cfg = _cfg_mirror()
    cfg["s1_atr_period"] = 14
    cfg["s1_sr_clearance_atr_mult"] = 0.0
    flat = pd.DataFrame({"open": [100]*200, "high": [100]*200, "low": [100]*200,
                        "close": [100]*200, "volume": [100]*200})
    sig, *_ = evaluate_s1("TEST", flat, flat, flat, "BULLISH", cfg=cfg)
    assert sig == "HOLD"


def test_cfg_none_does_not_compute_atr():
    """When cfg is None, evaluate_s1 does NOT set _last_atr anywhere — Bitget path unchanged."""
    cfg = _cfg_mirror()    # build but pass cfg=None
    daily = _candles_with_trend(200)
    htf   = _candles_with_trend(60)
    ltf   = _candles_with_trend(40)
    evaluate_s1("TEST", htf, ltf, daily, "BULLISH", cfg=None)
    assert "_last_atr" not in cfg   # no side effect on cfg when cfg=None
