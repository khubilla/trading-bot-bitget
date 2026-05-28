"""Gold Spot ($1) (CS.D.CFDGOLD.BMU.IP) instrument configuration for IG bot."""

CONFIG = {
    # Instrument identity
    "epic":         "CS.D.CFDGOLD.BMU.IP",
    "display_name": "GOLD",
    "currency":     "USD",

    # Contract sizing
    "contract_size": 0.1,
    "partial_size":  0.05,
    "point_value":   1.0,  # USD per point per contract

    # Session window (hour, minute) in ET
    "session_start": (0, 0),
    "session_end":   (23, 59),

    # Candle fetch limits
    "daily_limit": 100,
    "htf_limit":   50,
    "m15_limit":   100,

    # Order precision & expiry (per-instrument, used by ig_client + ig_bot)
    "price_decimals":       1,
    "min_deal_distance":    1.0,
    "pending_expiry_hours": 4,

    # S5 strategy parameters (tuned for ~$3200/oz Gold price)
    # Optimised 2026-04-07 via grid search (backtest_ig.py --no-fetch):
    #   Baseline: 25 fills, 64.0% WR, +40.9 pts
    #   Tuned:    22 fills, 90.9% WR, +365.3 pts
    # Key changes: swing_lookback 20→10, fvg_filter False→True,
    #              fvg_lookback 10→15, ob_lookback 40→30
    "s5_enabled":                    True,
    "s5_daily_ema_fast":             10,
    "s5_daily_ema_med":              21,
    "s5_daily_ema_slow":             50,
    "s5_htf_bos_lookback":           20,
    "s5_ltf_interval":               "15m",
    "s5_ob_lookback":                30,    # was 40
    "s5_ob_min_impulse":             0.005,
    "s5_ob_min_range_pct":           0.001,
    "s5_choch_lookback":             10,
    "s5_max_entry_buffer":           0.005,
    "s5_sl_buffer_pct":              0.002,
    "s5_ob_invalidation_buffer_pct": 0.001,
    "s5_swing_lookback":             10,    # was 20
    "s5_smc_fvg_filter":             True,  # was False
    "s5_smc_fvg_lookback":           15,    # was 10
    "s5_leverage":                   1,
    "s5_trade_size_pct":             0.1,
    "s5_min_rr":                     1,
    "s5_trail_range_pct":            5,
    "s5_use_candle_stops":           True,
    "s5_min_sr_clearance":           0.10,
    # ── T18: S1 block (s1_enabled=False; flip per instrument after paper validation) ── #
    "s1_enabled": False,
    "m3_limit": 30,
    "s1_htf_interval":   "1H",
    "s1_ltf_interval":   "3m",
    "s1_daily_interval": "1D",
    "s1_adx_trend_threshold":    25,
    "s1_daily_ema_slow":         20,
    "s1_daily_rsi_long_thresh":  60,
    "s1_daily_rsi_short_thresh": 40,
    "s1_rsi_period":       14,
    "s1_rsi_long_thresh":  65,
    "s1_rsi_short_thresh": 35,
    "s1_consolidation_candles":   2,
    "s1_consolidation_range_pct": 0.003,   # PLACEHOLDER — backtest will tune
    "s1_breakout_buffer_pct":     0.0005,   # PLACEHOLDER — backtest will tune
    "s1_atr_period":            14,
    "s1_sl_atr_mult":           1.5,        # PLACEHOLDER
    "s1_tp_atr_mult":           3.0,        # PLACEHOLDER
    "s1_sl_buffer_pct":         0.001,
    "s1_sr_clearance_atr_mult": 3.0,        # PLACEHOLDER
    "s1_contract_size": 0.1,
    "s1_partial_size":  0.05,
    "s1_use_swing_trail": True,
    "s1_swing_lookback":  20,

}
