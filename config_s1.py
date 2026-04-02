# ============================================================
#  Strategy 1 Configuration — MTF RSI Breakout
# ============================================================
# Entry filters:
#   1D:  ADX > ADX_TREND_THRESHOLD (trending, not sideways)
#   1H:  current HIGH > previous HIGH (bull) / LOW < prev LOW (bear)
#   3m:  RSI > 70 (long) or < 30 (short) + tight consolidation
#   3m:  Candle closes above/below box + buffer

S1_ENABLED = True

# ── Timeframes (Bitget granularity format) ───────────────── #
HTF_INTERVAL   = "1H"     # 1-hour chart
LTF_INTERVAL   = "3m"     # 3-minute chart
DAILY_INTERVAL = "1D"     # Daily chart for trend filter

# ── Daily Trend Filter ───────────────────────────────────── #
ADX_TREND_THRESHOLD = 25   # ADX above this = trending (allow trades)
DAILY_EMA_FAST      = 10
DAILY_EMA_SLOW      = 20

# ── RSI ──────────────────────────────────────────────────── #
RSI_PERIOD       = 14
RSI_LONG_THRESH  = 70   # RSI must be ABOVE this to consider LONG
RSI_SHORT_THRESH = 30   # RSI must be BELOW this to consider SHORT

# ── Consolidation Detection (3m chart) ───────────────────── #
CONSOLIDATION_CANDLES   = 2      # Look back N completed candles
CONSOLIDATION_RANGE_PCT = 0.003  # Max range = 0.3% to qualify as tight consolidation

# ── Breakout Confirmation (3m chart) ─────────────────────── #
BREAKOUT_BUFFER_PCT = 0.005   # 0.5% buffer above/below box edge

# ── Risk Management ──────────────────────────────────────── #
LEVERAGE         = 10
TRADE_SIZE_PCT   = 0.04    # 4% of total portfolio as margin
STOP_LOSS_PCT    = 0.05   # 5% price SL (hard cap)
TAKE_PROFIT_PCT  = 0.10   # 10% price TP

# ── S1-specific SL / S/R ─────────────────────────────────────────── #
S1_SL_BUFFER_PCT    = 0.005  # 0.5% buffer below/above box pivot for SL
S1_MIN_SR_CLEARANCE = 0.15   # 15% daily S/R clearance required to execute
