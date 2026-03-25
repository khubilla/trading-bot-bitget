# ============================================================
#  config.py — Copy this to config.py and fill in your keys
# ============================================================

API_KEY        = "YOUR_BITGET_API_KEY"
API_SECRET     = "YOUR_BITGET_SECRET_KEY"
API_PASSPHRASE = "YOUR_BITGET_PASSPHRASE"
DEMO_MODE      = True
BASE_URL       = "https://api.bitget.com"
PRODUCT_TYPE   = "usdt-futures"
MARGIN_COIN    = "USDT"

MIN_VOLUME_USDT    = 5_000_000
QUOTE_ASSET        = "USDT"
SCAN_INTERVAL_SEC  = 60

HTF_INTERVAL   = "1H"
LTF_INTERVAL   = "3m"
DAILY_INTERVAL = "1D"

RSI_PERIOD        = 14
RSI_LONG_THRESH   = 70
RSI_SHORT_THRESH  = 30

CONSOLIDATION_CANDLES   = 8
CONSOLIDATION_RANGE_PCT = 0.003
BREAKOUT_BUFFER_PCT     = 0.001

# ── Strategy 1 Risk ──────────────────────────────────────── #
LEVERAGE         = 30       # ← Change this to your desired leverage
TRADE_SIZE_PCT   = 0.05
TAKE_PROFIT_PCT  = 0.10
STOP_LOSS_PCT    = 0.05

# ── Daily ADX Trend Filter (replaces EMA filter) ─────────── #
# ADX > threshold = trending (allow trades)
# ADX < threshold = sideways (skip)
ADX_TREND_THRESHOLD = 25    # Standard: 25. Higher = stricter trending filter
DAILY_EMA_SLOW      = 20    # EMA used alongside ADX for direction check

MAX_CONCURRENT_TRADES = 1
POLL_INTERVAL_SEC     = 15
SENTIMENT_THRESHOLD   = 0.55
SENTIMENT_SCAN_SEC    = 60

LOG_FILE  = "bot.log"
TRADE_LOG = "trades.csv"
