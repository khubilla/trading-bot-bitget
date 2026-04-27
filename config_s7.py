# ============================================================
#  Strategy 7 Configuration — Post-Pump 1H Darvas Breakdown Short
# ============================================================
# Same daily setup as S4 (post-pump RSI exhaustion).
# Entry trigger differs: instead of "below previous day's low",
# wait for a stair-step 1H Darvas box (top + low) formed within
# the current UTC day, then fire on a confirmed 1H close below
# the low box.
# Sentiment gate: BEARISH only (gated in bot.py scan loop).

S7_ENABLED = True

# ── Big Candle Detection (mirrors S4) ───────────────────────── #
S7_BIG_CANDLE_BODY_PCT  = 0.20   # ≥ 20% body to qualify as a momentum candle
S7_BIG_CANDLE_LOOKBACK  = 30     # search last 30 daily candles

# ── RSI Gates (mirrors S4) ──────────────────────────────────── #
S7_RSI_PEAK_THRESH      = 75    # peak ≥ 75 within last RSI_PEAK_LOOKBACK days
S7_RSI_PEAK_LOOKBACK    = 10
S7_RSI_STILL_HOT_THRESH = 70    # prev-day RSI must remain ≥ 70 (no fade yet)
S7_RSI_DIV_MIN_DROP     = 5     # informational divergence threshold

# ── 1H Darvas Box Detection (NEW) ───────────────────────────── #
S7_BOX_CONFIRM_COUNT    = 2     # candles required to "hold" above/below the
                                # establishing candle before the box locks
                                # → 1 establishing + 2 confirming = 3 candles per box
                                # → minimum total ≈ 6 candles since UTC midnight

# ── Entry Trigger ───────────────────────────────────────────── #
S7_ENTRY_BUFFER     = 0.005     # entry trigger = box_low × (1 − 0.5%)
S7_MAX_ENTRY_BUFFER = 0.04      # skip if mark already > 4% past trigger
                                # (SL is leverage-capped, not spike-anchored)

# ── Risk Management (mirrors S4) ────────────────────────────── #
S7_LEVERAGE         = 10
S7_TRADE_SIZE_PCT   = 0.04      # 4% portfolio margin (50% initial → +50% scale-in)

S7_TRAILING_TRIGGER_PCT = 0.10  # 50% partial close at −10% (price)
S7_TRAILING_RANGE_PCT   = 10    # 10% callback on remaining 50%
S7_USE_SWING_TRAIL      = True
S7_SWING_LOOKBACK       = 30    # daily candles for swing-trail anchor

# ── S/R Clearance ───────────────────────────────────────────── #
S7_MIN_SR_CLEARANCE = 0.15      # skip SHORT if support floor < 15% below entry
