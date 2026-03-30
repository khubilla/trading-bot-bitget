import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import claude_analyst


TRADE_S3 = {
    "symbol": "ONTUSDT", "side": "LONG", "strategy": "S3",
    "entry": "0.07458", "sl": "0.07132", "tp": "0.08204",
    "exit_price": "0.07134", "result": "LOSS", "pnl": "-2.8391",
    "pnl_pct": "-43.44", "exit_reason": "SL",
    "snap_adx": "62.5", "snap_daily_rsi": "73.8",
    "snap_sentiment": "BULLISH", "snap_rsi": "",       # empty — should be skipped
    "snap_htf": "", "snap_coil": "",
    "snap_entry_trigger": "0.0738209", "snap_sl": "0.07131708", "snap_rr": "2.95",
    "snap_rsi_peak": "", "snap_spike_body_pct": "",
}

TRADE_UNKNOWN = {
    "symbol": "XYZUSDT", "side": "SHORT", "strategy": "UNKNOWN",
    "entry": "1.0", "sl": "1.05", "tp": "0.90",
    "exit_price": "1.03", "result": "LOSS", "pnl": "-1.5",
    "pnl_pct": "-15.0", "exit_reason": "SL",
}


def test_trade_block_present():
    prompt = claude_analyst.build_system_prompt(TRADE_S3)
    assert "ONTUSDT" in prompt
    assert "LONG" in prompt
    assert "0.07458" in prompt
    assert "-2.8391" in prompt
    assert "-43.44" in prompt


def test_snap_fields_non_null_only():
    prompt = claude_analyst.build_system_prompt(TRADE_S3)
    assert "ADX: 62.5" in prompt
    assert "Daily RSI: 73.8" in prompt
    # empty snap fields must not appear
    assert "snap_rsi" not in prompt
    assert "snap_htf" not in prompt


def test_strategy_config_included_for_s3():
    prompt = claude_analyst.build_system_prompt(TRADE_S3)
    # decision-relevant S3 constants must appear
    assert "S3_ADX_MIN" in prompt
    assert "S3_MIN_RR" in prompt
    assert "S3_USE_SWING_TRAIL" in prompt
    assert "S3_ENABLED" in prompt


def test_config_excludes_non_threshold_constants():
    prompt = claude_analyst.build_system_prompt(TRADE_S3)
    # leverage and trade size are not decision-relevant
    assert "S3_LEVERAGE" not in prompt
    assert "S3_TRADE_SIZE_PCT" not in prompt
    assert "S3_LTF_INTERVAL" not in prompt


def test_unknown_strategy_no_config_crash():
    # must not raise even when strategy has no config file
    prompt = claude_analyst.build_system_prompt(TRADE_UNKNOWN)
    assert "XYZUSDT" in prompt
    assert "Strategy config unavailable" in prompt
