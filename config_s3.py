# ============================================================
#  Strategy 3 Configuration — Daily Swing Pullback
# ============================================================
# Long-only pullback strategy based on daily trend alignment.
#
# Prerequisites (daily chart):
#   - EMA10 > EMA20 > EMA50 > EMA200 (golden alignment)
#   - ADX > 30 (strong trend, not sideways)
#
# Entry (15m chart):
#   - Slow Stochastics (5,3) recently oversold (< 30) — pullback confirmed
#   - First green candle after oversold = uptick signal
#   - Current 15m candle closes above uptick candle's high = entry trigger
#   - MACD (12,26,9) line > signal line (momentum turning up)
#
# Exit:
#   - SL: below pullback pivot low
#   - TP: computed from R:R minimum (S3_MIN_RR × risk)

S3_ENABLED = True

# ── Daily Trend Prerequisites ────────────────────────────── #
S3_DAILY_EMA_FAST   = 10
S3_DAILY_EMA_MED    = 20
S3_DAILY_EMA_SLOW   = 50
S3_DAILY_EMA_TREND  = 200
S3_DAILY_ADX_MIN    = 30        # Minimum ADX for strong trend

# ── 15m Slow Stochastics ─────────────────────────────────── #
S3_STOCH_K_PERIOD   = 5         # Fast %K period
S3_STOCH_D_SMOOTH   = 3         # Smoothing for Slow %K and Slow %D
S3_STOCH_OVERSOLD   = 30        # Oversold threshold
S3_STOCH_LOOKBACK   = 8         # Look back 8 completed 15m candles for oversold

# ── 15m MACD ─────────────────────────────────────────────── #
S3_MACD_FAST        = 12
S3_MACD_SLOW        = 26
S3_MACD_SIGNAL      = 9

# ── Entry ─────────────────────────────────────────────────── #
S3_LTF_INTERVAL     = "15m"
S3_ENTRY_BUFFER_PCT = 0.001     # 0.1% above uptick candle's high

# ── Risk Management ──────────────────────────────────────── #
S3_LEVERAGE         = 10
S3_TRADE_SIZE_PCT   = 0.25      # 25% of balance as margin
S3_SL_BUFFER_PCT    = 0.002     # 0.2% below pivot low for SL
S3_MIN_RR           = 2.0       # Minimum reward:risk ratio
S3_TAKE_PROFIT_PCT  = 0.05      # 5% price move = +50% margin at 10x (fallback TP)
