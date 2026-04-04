# config_s6.py
# ============================================================
#  Strategy 6 Configuration — V-Formation Liquidity Sweep Short
# ============================================================
S6_ENABLED = True

# ── Pattern detection ─────────────────────────────────────── #
S6_RSI_LOOKBACK      = 14      # RSI period
S6_SPIKE_LOOKBACK    = 30      # Max daily candles to scan for a V-formation
S6_OVERBOUGHT_RSI    = 70.0    # Minimum RSI at the swing-high candle
S6_MIN_DROP_PCT      = 0.30    # Minimum drop from peak_level to spike low (30%)

# ── Exit levels ───────────────────────────────────────────── #
S6_SL_PCT               = 0.50  # SL = fill * (1 + 0.50), i.e. 50% above entry
S6_TRAILING_TRIGGER_PCT = 1.00  # Partial-TP trigger = fill * (1 - 1.00), i.e. 100% below entry
S6_TRAIL_RANGE_PCT      = 10    # 10% trailing range on remainder (Bitget rangeRate integer, same units as S5_TRAIL_RANGE_PCT=5)

# ── Position sizing ───────────────────────────────────────── #
S6_LEVERAGE       = 10
S6_TRADE_SIZE_PCT = 0.04
