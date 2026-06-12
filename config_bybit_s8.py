# ============================================================
#  Strategy 8 Configuration — Post-S2 Bounce (Tri-Confluence) — Bybit
# ============================================================
# LONG-only daily bounce play in the phase AFTER an S2-style breakout
# that failed to continue upward:
#   1. S2-like structure found in recent history: big momentum candle,
#      tight Darvas coil, RSI>70 breakout above the coil box top
#   2. Price pulled back to a tri-confluence support zone:
#      coil box top + daily 20MA + 61.8% fib of the impulse leg
#   3. A small green daily candle sits on the zone
#   4. Stop-buy above that green candle's high

S8_ENABLED = True

# ── Post-S2 Structure Detection ──────────────────────────── #
S8_BIG_CANDLE_BODY_PCT = 0.20   # Min 20% body to qualify as momentum candle (matches S2)
S8_BIG_CANDLE_LOOKBACK = 30     # Big candle searched within 30 days before breakout day B
S8_RSI_THRESH          = 70     # Daily RSI on breakout day B must exceed this
S8_CONSOL_CANDLES      = 5      # Max coil size before B (tries 1 to 5, matches S2)
S8_CONSOL_RANGE_PCT    = 0.15   # Max 15% effective coil range (matches S2)
S8_DARVAS_WICK_PCT     = 0.05   # Darvas top rule: wick >5% of body top → use body (matches S2)
S8_PHASE_LOOKBACK      = 15     # Breakout day B must be within last 15 completed candles

# ── Tri-Confluence Zone ──────────────────────────────────── #
S8_MIN_EXTENSION   = 0.05   # swing_high must exceed box_top by ≥5% (real impulse leg)
S8_FIB_RETRACE     = 0.618  # fib level of leg (box_low → swing_high) used as support #3
S8_MA_PERIOD       = 20     # daily moving average period (support #2)
S8_MA_TYPE         = "SMA"  # "SMA" | "EMA"
S8_CONFLUENCE_TOL  = 0.02   # box_top/ma/fib must cluster within 2% ((max-min)/max)

# ── Green Bounce Candle ──────────────────────────────────── #
S8_SMALL_BODY_PCT  = 0.05   # green candle body ≤5% of open = "small"
S8_PROXIMITY       = 0.01   # candle low may sit ≤1% above zone top

# ── Entry Trigger ────────────────────────────────────────── #
S8_BREAKOUT_BUFFER  = 0.005  # 0.5% buffer above green candle high
S8_MAX_ENTRY_BUFFER = 0.04   # Skip if price already >4% above trigger (matches S2)

# ── Risk Management (exits copy S2) ──────────────────────── #
S8_LEVERAGE         = 10
S8_TRADE_SIZE_PCT   = 0.04   # 4% of portfolio as margin — single full entry, NO scale-in
S8_TAKE_PROFIT_PCT  = 0.10   # partial TP activation: +10% from entry
S8_STOP_LOSS_PCT    = 0.05   # SL cap: 5% from entry (green candle low is primary SL)

S8_TRAILING_TRIGGER_PCT = 0.10   # close 50% at +10% from entry
S8_TRAILING_RANGE_PCT   = 10     # 10% trailing callback on the remaining 50%
S8_USE_SWING_TRAIL      = False  # exchange-side % trail by default (matches S2)
S8_SWING_LOOKBACK       = 30     # daily candles for swing-low search (if swing trail on)
