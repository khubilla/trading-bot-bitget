# ============================================================
#  S5 Strategy Configuration — IG CFD / US30 (Wall Street Cash)
# ============================================================
# This file replaces config_s5.py for the IG bot.
# All percentage-based thresholds are tuned for US30's intraday
# characteristics (3h morning session, ~30-50pt average 15m range,
# overnight gaps, no 24/7 market continuity).
#
# Crypto defaults (config_s5.py) are left untouched — bot.py/Bitget
# is completely unaffected by this file.

# ── Daily bias ───────────────────────────────────────────── #
S5_ENABLED        = True
S5_DAILY_EMA_FAST = 10     # same as crypto
S5_DAILY_EMA_MED  = 20     # same
S5_DAILY_EMA_SLOW = 50     # same

# ── 1H Structure (Break of Structure) ────────────────────── #
# Crypto default: 10 (10h spans overnight + prior session).
# US30: 5h covers pre-market open + current morning without
# pulling BOS pivots from a prior closed session.
S5_HTF_BOS_LOOKBACK = 5

# ── 15m Order Block Detection ────────────────────────────── #
S5_LTF_INTERVAL    = "15m"

# Crypto default: 50 (12.5h — reaches into overnight gap).
# US30: 20 = 5h, session-aware.
S5_OB_LOOKBACK     = 20

# Crypto default: 0.01 (1% = ~180pts on US30 — rarely happens in 15m).
# US30: 0.5% = ~90pts, realistic for a strong morning 15m impulse.
S5_OB_MIN_IMPULSE  = 0.005

# Crypto default: 0.005 (0.5% = ~90pts per candle — above average US30 15m range).
# US30: 0.2% = ~36pts, within typical morning 15m candle range.
S5_OB_MIN_RANGE_PCT = 0.002

# Crypto default: 20 (5h). US30: 10 = 2.5h, tighter for 3h session.
S5_CHOCH_LOOKBACK  = 10

# ── Entry / SL ───────────────────────────────────────────── #
# S5_ENTRY_BUFFER_PCT removed — entry is at ob_high exactly (limit order)

# Crypto default: 0.04 (4% = ~720pts — meaningless staleness guard on US30).
# US30: 1% = ~180pts, meaningful threshold for a stale entry.
# Note: used by ig_bot.py's _entry_in_window(), NOT by evaluate_s5().
S5_MAX_ENTRY_BUFFER = 0.01

# Crypto default: 0.003 (0.3% = ~54pts). 0.2% = ~36pts is tighter but
# sufficient for a 30-50pt average candle range.
S5_SL_BUFFER_PCT   = 0.002
S5_OB_INVALIDATION_BUFFER_PCT = 0.001

# ── Structural TP (swing target) ─────────────────────────── #
# Crypto default: 50 (12.5h — TP targets land in overnight/Asian hours).
# US30: 20 = 5h, keeps swing targets within the current session.
S5_SWING_LOOKBACK  = 20

# ── SMC FVG confluence ───────────────────────────────────── #
S5_SMC_FVG_FILTER   = False   # disabled (same as crypto)
S5_SMC_FVG_LOOKBACK = 10      # was 20; tighter for 3h session

# ── Risk management ──────────────────────────────────────── #
# ig_bot.py uses fixed CONTRACT_SIZE from config_ig.py — leverage
# and trade_size_pct are not applied. Kept for completeness.
S5_LEVERAGE       = 1      # N/A for IG CFD
S5_TRADE_SIZE_PCT = 0.05   # unused by ig_bot.py

S5_MIN_RR          = 2.0   # minimum 2:1 R:R (same as crypto)
S5_TRAIL_RANGE_PCT = 5     # 5% fallback trailing callback (same)
S5_USE_CANDLE_STOPS = True  # trail SL to prev 15m candle after partial TP

# ── Dead param (never imported by evaluate_s5) ───────────── #
S5_MIN_SR_CLEARANCE = 0.10  # orphaned in strategy.py — kept for completeness
