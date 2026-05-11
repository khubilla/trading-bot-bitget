"""US100 (NASDAQ 100 Cash) instrument configuration for IG bot."""

CONFIG = {
    # Instrument identity
    "epic":         "IX.D.NASDAQ.IFD.IP",
    "display_name": "US100",
    "currency":     "USD",

    # Contract sizing
    "contract_size": 0.04,    # opening size (contracts)
    "partial_size":  0.02,  # close at TP1 (50%)
    "point_value":   1.0,  # USD per point per contract

    # Session window (hour, minute) in ET
    "session_start": (0, 0),
    "session_end":   (23, 59),

    # Candle fetch limits
    "daily_limit": 100,
    "htf_limit":   50,
    "m15_limit":   100,

    # S5 strategy parameters (optimized via grid search 2026-04-30)
    # Backtest result: 4 fills, 50% WR, -3 pts (near breakeven)
    # Key changes from US30 baseline: ob_lookback 40→30, min_rr 1.0→1.2
    "s5_enabled":                    True,
    "s5_daily_ema_fast":             10,
    "s5_daily_ema_med":              20,
    "s5_daily_ema_slow":             50,
    "s5_htf_bos_lookback":           10,
    "s5_ltf_interval":               "15m",
    "s5_ob_lookback":                30,   # ← Optimized: 40 → 30 (tighter OB recency)
    "s5_ob_min_impulse":             0.005,
    "s5_ob_min_range_pct":           0.001,
    "s5_choch_lookback":             10,
    "s5_max_entry_buffer":           0.01,
    "s5_sl_buffer_pct":              0.002,
    "s5_ob_invalidation_buffer_pct": 0.001,
    "s5_swing_lookback":             20,
    "s5_smc_fvg_filter":             False,
    "s5_smc_fvg_lookback":           10,
    "s5_leverage":                   1,
    "s5_trade_size_pct":             0.1,
    "s5_min_rr":                     1.2,  # ← Optimized: 1.0 → 1.2 (stricter R:R filter)
    "s5_trail_range_pct":            5,
    "s5_use_candle_stops":           True,
    "s5_min_sr_clearance":           0.10,
}
