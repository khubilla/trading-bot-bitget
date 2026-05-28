# ============================================================
#  MTF Breakout Bot — Binance USDT-M Futures — Configuration
# ============================================================

import os
import pathlib as _pl

# Load .env file if present (local development convenience)
_env_file = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_env_file):
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip())

# --- Binance API Credentials ---
# Primary:  BINANCE_API_KEY,        BINANCE_API_SECRET
# Backup:   BINANCE_API_KEY_BACKUP, BINANCE_API_SECRET_BACKUP  (optional)
# binance_client tries primary first; on auth-related failures (-2014/-2015/
# -1022/-2008) it retries once with the backup pair. If backup is unset or
# also fails, the original error propagates.
API_KEY_PRIMARY    = os.environ.get("BINANCE_API_KEY",           "")
API_SECRET_PRIMARY = os.environ.get("BINANCE_API_SECRET",        "")
API_KEY_BACKUP     = os.environ.get("BINANCE_API_KEY_BACKUP",    "")
API_SECRET_BACKUP  = os.environ.get("BINANCE_API_SECRET_BACKUP", "")

# Aliases for back-compat: anything reading API_KEY/API_SECRET sees the primary.
API_KEY    = API_KEY_PRIMARY
API_SECRET = API_SECRET_PRIMARY

# --- Binance API Base ---
# Live:    https://fapi.binance.com
# Testnet: https://testnet.binancefuture.com
BASE_URL    = os.environ.get("BINANCE_BASE_URL", "https://fapi.binance.com")
RECV_WINDOW = "5000"
SETTLE_COIN = "USDT"

# --- Safety Switch ---
# When True, binance_trader.open_long/open_short LOG intended orders and return
# a simulated fill without calling /fapi/v1/order. Flip to False ONLY after
# observing at least one full evaluation cycle in the logs.
DRY_RUN = True

# --- Pair Scanner ---
MIN_VOLUME_USDT         = 5_000_000   # 24h quote volume floor (USDT)
MAX_PRICE_USDT          = 1000        # Exclude pairs above this price
SCAN_INTERVAL_SEC       = 60
LIQUIDITY_CHECK_ENABLED = False
MIN_OB_DEPTH_USDT       = 50_000

# --- Bot Behaviour ---
MAX_CONCURRENT_TRADES = 4
POLL_INTERVAL_SEC     = 15

# --- Initial Balance (for PnL% calculation) ---
INITIAL_BALANCE = 160.0

# --- Market Sentiment Filter ---
SENTIMENT_THRESHOLD = 0.70
SENTIMENT_SCAN_SEC  = 60

# --- Claude Trade Filter ---
CLAUDE_FILTER_ENABLED   = False
CLAUDE_FILTER_MODEL     = "claude-haiku-4-5"
CLAUDE_FILTER_HISTORY_N = 30

# --- Strategy Enable Flags ---
ENABLE_S1 = True
ENABLE_S2 = True
ENABLE_S3 = True
ENABLE_S4 = True
ENABLE_S5 = True
ENABLE_S6 = True
ENABLE_S7 = True

# --- Logging ---
_DATA_DIR = _pl.Path(os.environ.get("DATA_DIR", "."))
LOG_FILE   = str(_DATA_DIR / "binance_bot.log")
TRADE_LOG  = str(_DATA_DIR / "binance_trades.csv")
STATE_FILE = str(_DATA_DIR / "binance_state.json")

# --- Non-Trading Hours --- (PH time, UTC+8) — mirror Bitget defaults
NON_TRADING_HOURS = [
    #(6, 11),
]

# --- Weekend Trading ---
DISABLE_SATURDAY_TRADING = False

# --- Enhanced Trading Windows ---
ENHANCED_TRADING_WINDOWS = [
    #(16, 19, 1.5),
]

REDUCE_TUESDAY_SIZE = False

DEMO_MODE = False
