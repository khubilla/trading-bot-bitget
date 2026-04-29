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
MIN_VOLUME_USDT   = 1_000_000   # 24h quote volume filter (5 million USDT)
MAX_PRICE_USDT    = 0         # Exclude pairs priced above this (set high to include BTC/ETH if desired)
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
CLAUDE_FILTER_ENABLED   = False                        # set True to enable approval gate
CLAUDE_FILTER_MODEL     = "claude-haiku-4-5"  # cheapest Claude model
CLAUDE_FILTER_HISTORY_N = 30                           # last N trades to send as context

# --- Logging ---
# DATA_DIR is set to the shared Render disk mount path (/data) in production,
# and defaults to the current directory locally.
import pathlib as _pl
_DATA_DIR = _pl.Path(os.environ.get("DATA_DIR", "."))
LOG_FILE  = str(_DATA_DIR / "bot.log")
TRADE_LOG = str(_DATA_DIR / "trades.csv")

# --- Non-Trading Hours ---
# Avoid trading during these hours (PH time, UTC+8)
# Format: List of (start_hour, end_hour) tuples. Ranges can cross midnight.
# Set to empty list [] to disable this filter.
# CRITICAL: Based on trade analysis, 8-11 AM PH causes 45% of catastrophic losses
NON_TRADING_HOURS = [
    (8, 11),   # 8:00 AM - 11:00 AM PH (00:00-03:00 UTC) - WORST SESSION: 45% of catastrophic losses
    (22, 1),   # 10:00 PM - 1:00 AM PH (14:00-17:00 UTC) - Your original restriction
]

# --- Weekend Trading ---
# Disable trading on Saturdays (low liquidity, 25% win rate, -242% net P&L)
DISABLE_SATURDAY_TRADING = True

# --- Enhanced Trading Windows ---
# Increase position size during high-performance sessions (PH time, UTC+8)
# Format: List of (start_hour, end_hour, multiplier) tuples
# Based on trade analysis: European Main (16:00-19:00 PH) has 59.1% win rate, +593% P&L
ENHANCED_TRADING_WINDOWS = [
    (16, 19, 2.0),  # 4:00 PM - 7:00 PM PH (08:00-11:00 UTC) - European Main Session - 2x position size
]

# Optional: Reduce position size during risky days
# Tuesday reduction REMOVED - analysis shows root causes (Asian Late session,
# symbol re-entry) are already addressed by blackout windows. 2/4 Tuesdays were
# profitable (+185%, +103%). Net P&L was only -4.4% across 18 trades.
REDUCE_TUESDAY_SIZE = False
# TUESDAY_SIZE_MULTIPLIER = 0.5  # Disabled - not needed with blackout windows