"""USD/JPY (CS.D.USDJPY.TODAY.IP) instrument configuration for IG bot.

S5 SMC strategy on USD/JPY spot CFD.

IG epic verified 2026-05-19: price quoted in pip-scaled units
(e.g. 9947.0 means 99.47). 1 pip = 1.0 in IG's price feed.
minDealSize=0.04, minNormalStopOrLimitDistance=2.0 pips.
"""

CONFIG = {
    # Instrument identity
    "epic":         "CS.D.USDJPY.TODAY.IP",
    "display_name": "USDJPY",
    "currency":     "USD",

    # Contract sizing — IG minimum (matches US30 sizing convention)
    "contract_size": 0.04,   # opening size (contracts)
    "partial_size":  0.02,   # close at TP1 (50%)
    "point_value":   1.0,    # USD per point per contract

    # Session window (hour, minute) in ET — 24h to match other IG instruments;
    # ig_bot.py handles forex weekend close (Sun <6PM ET) automatically
    "session_start": (0, 0),
    "session_end":   (23, 59),

    # Candle fetch limits
    "daily_limit": 100,
    "htf_limit":   50,
    "m15_limit":   100,

    # Order precision & expiry (per-instrument)
    # IG quotes USD/JPY in pip-scaled units to 0.1-pip precision (e.g. 9947.0).
    # Min stop distance per /markets API: 2 pips = 2.0 in IG units.
    "price_decimals":       1,
    "min_deal_distance":    2.0,
    # 48h expiry vs indices' 4h — forex pullbacks to OB take longer
    "pending_expiry_hours": 48,

    # S5 strategy parameters — forex-scaled from US30 baseline (~4x tighter
    # to match forex's ~0.5% daily range vs indices' ~1.5%)
    "s5_enabled":                    True,
    "s5_daily_ema_fast":             10,
    "s5_daily_ema_med":              20,
    "s5_daily_ema_slow":             50,
    "s5_htf_bos_lookback":           10,
    "s5_ltf_interval":               "15m",
    "s5_ob_lookback":                40,
    "s5_ob_min_impulse":             0.0015,   # was 0.005  (~23 pips on USD/JPY)
    "s5_ob_min_range_pct":           0.00025,  # was 0.001  (~4 pips OB range)
    "s5_choch_lookback":             10,
    "s5_max_entry_buffer":           0.0025,   # was 0.01   (~39 pips above OB)
    "s5_sl_buffer_pct":              0.0005,   # was 0.002  (~8 pips below OB)
    "s5_ob_invalidation_buffer_pct": 0.00025,  # was 0.001  (~4 pips)
    "s5_swing_lookback":             20,
    "s5_smc_fvg_filter":             False,
    "s5_smc_fvg_lookback":           10,
    "s5_leverage":                   1,
    "s5_trade_size_pct":             0.1,
    "s5_min_rr":                     1,
    "s5_trail_range_pct":            1.25,     # was 5      (5% = ~775 pips, way wide)
    "s5_use_candle_stops":           True,
    "s5_min_sr_clearance":           0.025,    # was 0.10   (10% S/R clearance is huge)
}
