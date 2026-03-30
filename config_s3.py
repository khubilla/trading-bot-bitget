# ============================================================
#  Strategy 3 Configuration — 15m Swing Pullback
# ============================================================
# Long-only pullback strategy — all indicators on 15m chart.
#
# Prerequisites (15m):
#   - EMA10 > EMA20 > EMA50 > EMA200 (golden alignment)
#   - ADX > 30 (strong trend, not sideways)
#
# Entry (15m):
#   - Slow Stochastics (5,3) recently oversold (< 30) — pullback confirmed
#   - First green candle after oversold = uptick signal
#   - Current 15m candle closes above uptick candle's high = entry trigger
#   - MACD (12,26,9) line > signal line (momentum turning up)
#
# Exit (same as S2):
#   - SL: below pullback pivot low
#   - Partial TP: close 50% at +10% price move (+100% margin at 10x)
#   - Trailing stop: 10% callback on remaining 50%

S3_ENABLED = True

# ── 15m Trend Prerequisites ──────────────────────────────── #
S3_EMA_FAST   = 10
S3_EMA_MED    = 20
S3_EMA_SLOW   = 50
S3_EMA_TREND  = 200
S3_ADX_MIN    = 30              # Minimum ADX for strong trend

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
S3_ENTRY_BUFFER_PCT = 0.001     # 0.1% above uptick candle's high (confirm breakout only)
S3_MAX_ENTRY_BUFFER = 0.01      # Skip entry if price already >1% above entry trigger

# ── Risk Management ──────────────────────────────────────── #
S3_LEVERAGE         = 10
S3_TRADE_SIZE_PCT   = 0.04      # 4% of total portfolio as margin
S3_SL_BUFFER_PCT    = 0.002     # 0.2% below pivot low for SL
S3_MIN_RR               = 2.0   # Minimum reward:risk ratio (vs partial TP level)
S3_TRAILING_TRIGGER_PCT = 0.10  # 10% price move → close 50% (+100% margin at 10x)
S3_TRAILING_RANGE_PCT   = 10    # 10% trailing callback on remaining 50%
S3_USE_SWING_TRAIL      = True  # trail SL to nearest 15m swing low from entry (SMC style)
S3_SWING_LOOKBACK       = 50    # 15m candles to scan for structural swing low

# ── Daily Momentum Gate ──────────────────────────────────── #
S3_DAILY_GAIN_MIN   = 0.10      # Skip unless price is ≥10% above today's daily open

# ── S/R Clearance ─────────────────────────────────────── #
S3_MIN_SR_CLEARANCE = 0.15      # Skip LONG if resistance < 15% above entry
