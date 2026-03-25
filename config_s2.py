# ============================================================
#  Strategy 2 Configuration — Daily Momentum Coil Breakout
# ============================================================
# Pure daily chart strategy:
#   1. Big momentum candle(s) ≥20% body within last N days
#   2. Daily RSI > 70
#   3. 1–5 tight daily candles consolidating (RSI > 70 throughout)
#   4. Current daily candle breaks above consolidation high
#      (above wick if long wick, above body close if short wick)

S2_ENABLED = True

# ── Big Candle Detection ─────────────────────────────────── #
S2_BIG_CANDLE_BODY_PCT  = 0.20   # Min 20% body size to qualify as momentum candle
S2_BIG_CANDLE_LOOKBACK  = 30     # Search last 30 daily candles for the big candle

# ── Daily Consolidation ──────────────────────────────────── #
S2_RSI_LONG_THRESH  = 70
S2_CONSOL_CANDLES   = 5          # Max 5 tight daily candles (tries 1 to 5)
S2_CONSOL_RANGE_PCT = 0.15       # Max 15% range to count as tight consolidation

# ── Entry Trigger ────────────────────────────────────────── #
S2_BREAKOUT_BUFFER  = 0.001      # 0.1% buffer above box high for entry
S2_LONG_WICK_RATIO  = 2.0        # Wick is "long" if wick > 2x body → buy above wick
                                  # Otherwise → buy above body close

# ── Risk Management ──────────────────────────────────────── #
S2_LEVERAGE         = 10
S2_TRADE_SIZE_PCT   = 0.05       # 5% of balance as margin
S2_TAKE_PROFIT_PCT  = 0.10       # 10% price move = +100% margin at 10x
S2_STOP_LOSS_PCT    = 0.05       # 5% price move = -50% margin at 10x (via box_low SL)