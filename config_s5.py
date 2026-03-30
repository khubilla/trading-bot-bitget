# ============================================================
#  Strategy 5 Configuration — SMC Order Block Pullback
# ============================================================
# Long-only or Short-only depending on market direction.
# All entry logic on 15m chart, bias confirmed on daily + 1H.
#
# Entry conditions:
#   1D:  EMA10 > EMA20 > EMA50 (bullish) or reverse (bearish)
#   1H:  Break of Structure — current close above/below prior
#        swing high/low (last S5_HTF_BOS_LOOKBACK candles)
#   15m: Find Order Block (last opposing candle before impulse)
#   15m: Pullback touches OB zone
#   15m: Change of Character (ChoCH) — close back above/below OB
#        boundary confirms entry
#
# Exit (standard SMC):
#   Partial (50%) at 1:1 R:R → SL moves to breakeven
#   Remaining (50%) targets the next structural swing high/low
#   Trailing stop (S5_TRAIL_RANGE_PCT) activates after partial
#   as a fallback if no structural target found

S5_ENABLED = True

# ── Daily EMA Bias ────────────────────────────────────────── #
S5_DAILY_EMA_FAST = 10
S5_DAILY_EMA_MED  = 20
S5_DAILY_EMA_SLOW = 50

# ── 1H Break of Structure ─────────────────────────────────── #
S5_HTF_BOS_LOOKBACK = 10    # look at last N completed 1H candles for prior swing

# ── 15m Order Block Detection ─────────────────────────────── #
S5_LTF_INTERVAL    = "15m"
S5_OB_LOOKBACK     = 50     # candles to scan for the most recent OB
S5_OB_MIN_IMPULSE  = 0.01   # impulse must move price ≥1% to qualify
S5_OB_MIN_RANGE_PCT = 0.005  # OB candle range (high-low)/low must be ≥0.5%; filters narrow SL sweeps
S5_CHOCH_LOOKBACK  = 20     # candles to check for OB touch + ChoCH confirmation

# ── Entry / SL ────────────────────────────────────────────── #
S5_ENTRY_BUFFER_PCT = 0.005  # 0.5% beyond OB boundary for entry trigger
S5_MAX_ENTRY_BUFFER = 0.04   # skip if price already >4% past entry trigger
S5_SL_BUFFER_PCT    = 0.003  # 0.3% beyond OB outer edge for SL

# ── Take Profit (structural) ───────────────────────────────── #
S5_SWING_LOOKBACK   = 50     # 15m candles to scan for the structural TP swing target

# ── SMC confluence (opt-in) ───────────────────────────────── #
S5_SMC_FVG_FILTER   = False  # require an unfilled FVG in the OB impulse window
S5_SMC_FVG_LOOKBACK = 20     # 15m candles to search for the FVG

# ── Risk Management ───────────────────────────────────────── #
S5_LEVERAGE         = 10
S5_TRADE_SIZE_PCT   = 0.04   # 2% of total portfolio as margin
S5_MIN_RR           = 2.0    # minimum reward:risk ratio (structural target vs SL)
S5_TRAIL_RANGE_PCT  = 5      # fallback trailing callback % after partial close
S5_USE_CANDLE_STOPS = True   # after partial close, trail SL to prev completed 15m candle low/high

# ── S/R Clearance ─────────────────────────────────────────── #
S5_MIN_SR_CLEARANCE = 0.10   # skip if resistance/support < 10% away
