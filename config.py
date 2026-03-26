# ============================================================
#  MTF Breakout Bot — Bitget USDT Futures — Configuration
# ============================================================

import os

# Load .env file if present (local development convenience)
_env_file = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_env_file):
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip())

# --- Bitget API Credentials ---
# Set via environment variables on Render, or in a local .env file.
API_KEY        = os.environ.get("BITGET_API_KEY",        "")
API_SECRET     = os.environ.get("BITGET_API_SECRET",     "")
API_PASSPHRASE = os.environ.get("BITGET_API_PASSPHRASE", "")

# --- Demo / Live Toggle ---
# Bitget uses the same API endpoint for both.
# DEMO mode = use your Bitget Paper Trading (Demo) account credentials.
# LIVE mode = use your real Bitget Futures account credentials.
# Set this to True while testing — switch to False only when ready for real money.
DEMO_MODE = False

# --- Bitget API Base ---
BASE_URL     = "https://api.bitget.com"
PRODUCT_TYPE = "usdt-futures"    # USDT-margined perpetual futures
MARGIN_COIN  = "USDT"

# --- Pair Scanner ---
MIN_VOLUME_USDT   = 5_000_000   # 24h quote volume filter (5 million USDT)
SCAN_INTERVAL_SEC = 60           # Re-scan all pairs every 60 seconds

# --- Bot Behaviour ---
MAX_CONCURRENT_TRADES = 2   # STRICT: only 1 active trade at a time
POLL_INTERVAL_SEC     = 15  # Seconds between each evaluation cycle

# --- Market Sentiment Filter ---
# Volume-weighted: only LONG when majority green, only SHORT when majority red
SENTIMENT_THRESHOLD = 0.55   # >55% green volume → BULLISH; <45% → BEARISH
SENTIMENT_SCAN_SEC  = 60     # Re-calculate alongside pair scan

# --- Logging ---
# DATA_DIR is set to the shared Render disk mount path (/data) in production,
# and defaults to the current directory locally.
import pathlib as _pl
_DATA_DIR = _pl.Path(os.environ.get("DATA_DIR", "."))
LOG_FILE  = str(_DATA_DIR / "bot.log")
TRADE_LOG = str(_DATA_DIR / "trades.csv")
