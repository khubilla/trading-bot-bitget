# ============================================================
#  config.py — Copy this to config.py and fill in your keys
# ============================================================
# This file contains credentials and shared bot settings only.
# Strategy parameters live in config_s1.py and config_s2.py.

API_KEY        = "YOUR_BITGET_API_KEY"
API_SECRET     = "YOUR_BITGET_SECRET_KEY"
API_PASSPHRASE = "YOUR_BITGET_PASSPHRASE"
DEMO_MODE      = True
BASE_URL       = "https://api.bitget.com"
PRODUCT_TYPE   = "usdt-futures"
MARGIN_COIN    = "USDT"

MIN_VOLUME_USDT   = 5_000_000
SCAN_INTERVAL_SEC = 60

MAX_CONCURRENT_TRADES = 2
POLL_INTERVAL_SEC     = 15
SENTIMENT_THRESHOLD   = 0.55
SENTIMENT_SCAN_SEC    = 60

LOG_FILE  = "bot.log"
TRADE_LOG = "trades.csv"
