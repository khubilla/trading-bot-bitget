# ── Strategy 4 — Post-Pump RSI Divergence Short ─────────────────── #
#
# Fires SHORT when:
#   1. Big momentum spike (≥20% body) in last 30 daily candles
#   2. RSI peaked above 75 within last 10 candles (was overbought)
#   3. (Optional) RSI bearish divergence — 2nd push lower than 1st
#   4. Entry: intraday breach of previous day's low
#
# Exit mirrors S2: 50% close at -10%, trailing stop on remainder.
# Sentiment gate: only fires when market is NOT BULLISH.

S4_ENABLED              = True

# ── Detection ────────────────────────────────────────────────────── #
S4_BIG_CANDLE_BODY_PCT  = 0.20   # ≥20% body to count as spike
S4_BIG_CANDLE_LOOKBACK  = 30     # search last 30 daily candles
S4_RSI_PEAK_THRESH      = 75     # RSI must have hit this level
S4_RSI_PEAK_LOOKBACK    = 10     # candles to search for RSI peak
S4_RSI_DIV_MIN_DROP     = 5      # divergence: 2nd RSI push ≥5 pts lower (optional)
S4_RSI_STILL_HOT_THRESH = 70    # previous candle RSI must still be above this — else setup invalidated

# ── Risk / exits ─────────────────────────────────────────────────── #
S4_ENTRY_BUFFER         = 0.01   # entry = prev_low * (1 - 0.01), i.e. 1% below prev low
S4_MAX_ENTRY_BUFFER     = 0.04   # max drop from prev_low to still enter; beyond this = missed entry
S4_TRAILING_TRIGGER_PCT = 0.10   # activation trigger: close 50% when price is 10% below entry
S4_TRAILING_RANGE_PCT   = 10     # trailing callback % on remaining 50%
S4_USE_SWING_TRAIL      = False  # S4 uses exchange-side % trailing stop instead
S4_SWING_LOOKBACK       = 30     # daily candles to scan for structural swing high
S4_LEVERAGE             = 10
S4_TRADE_SIZE_PCT       = 0.04   # 4% of total portfolio as margin

# ── S/R Clearance ─────────────────────────────────────── #
S4_MIN_SR_CLEARANCE = 0.15       # Skip SHORT if support < 15% below entry

# ── 1H Low Filter ─────────────────────────────────────── #
S4_LOW_LOOKBACK = 5              # Entry trigger must be ≤ lowest low of last N 1H candles
