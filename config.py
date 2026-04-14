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
MAX_PRICE_USDT    = 150         # Exclude pairs priced above this (set high to include BTC/ETH if desired)
SCAN_INTERVAL_SEC = 60           # Re-scan all pairs every 60 seconds

# --- Liquidity Filter ---
LIQUIDITY_CHECK_ENABLED = False
MIN_OB_DEPTH_USDT       = 50_000   # bidSz×bidPr + askSz×askPr must meet this

# --- Bot Behaviour ---
MAX_CONCURRENT_TRADES = 10   # Max simultaneous open positions across all strategies
POLL_INTERVAL_SEC     = 15  # Seconds between each evaluation cycle

# --- Initial Balance (for PnL% calculation) ---
INITIAL_BALANCE = 160.0  # Starting balance in USDT (update this to your actual starting amount)

# --- Market Sentiment Filter ---
# Volume-weighted: only LONG when majority green, only SHORT when majority red
SENTIMENT_THRESHOLD = 0.70   # >70% green volume → BULLISH; <30% → BEARISH
SENTIMENT_SCAN_SEC  = 60     # Re-calculate alongside pair scan

# --- Claude Trade Filter ---
CLAUDE_FILTER_ENABLED   = True                        # set True to enable approval gate
CLAUDE_FILTER_MODEL     = "claude-3-haiku-20240307"  # cheapest Claude model
CLAUDE_FILTER_HISTORY_N = 30                           # last N trades to send as context

# --- Logging ---
# DATA_DIR is set to the shared Render disk mount path (/data) in production,
# and defaults to the current directory locally.
import pathlib as _pl
_DATA_DIR = _pl.Path(os.environ.get("DATA_DIR", "."))
LOG_FILE  = str(_DATA_DIR / "bot.log")
TRADE_LOG = str(_DATA_DIR / "trades.csv")

# --- Non-Trading Hours ---
# Avoid trading during these hours (PH time)
# Set to None or empty list to disable this filter.
NON_TRADING_HOURS_FROM = 22 # 22:00 PH time (14:00 UTC)
NON_TRADING_HOURS_TO = 1 # 01:00 PH time (17:00 UTC)