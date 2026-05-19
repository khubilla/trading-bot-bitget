# ============================================================
#  MTF Breakout Bot — Bybit USDT Perpetual Futures — Configuration
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

# --- Bybit API Credentials ---
# Primary:  BYBIT_API_KEY,        BYBIT_API_SECRET
# Backup:   BYBIT_API_KEY_BACKUP, BYBIT_API_SECRET_BACKUP  (optional)
# bybit_client tries primary first; on auth-related failures (retCodes
# 10003/10004/10005/10010) it retries once with the backup pair. If backup
# is unset or also fails, the original error propagates.
API_KEY_PRIMARY    = os.environ.get("BYBIT_API_KEY",           "")
API_SECRET_PRIMARY = os.environ.get("BYBIT_API_SECRET",        "")
API_KEY_BACKUP     = os.environ.get("BYBIT_API_KEY_BACKUP",    "")
API_SECRET_BACKUP  = os.environ.get("BYBIT_API_SECRET_BACKUP", "")

# Aliases for back-compat: anything reading API_KEY/API_SECRET sees the primary.
API_KEY    = API_KEY_PRIMARY
API_SECRET = API_SECRET_PRIMARY

# --- Bybit API Base ---
# Live:    https://api.bybit.com
# Testnet: https://api-testnet.bybit.com
BASE_URL    = os.environ.get("BYBIT_BASE_URL", "https://api.bybit.com")
RECV_WINDOW = "5000"
CATEGORY    = "linear"      # USDT perpetual futures
SETTLE_COIN = "USDT"

# --- Safety Switch ---
# When True, bybit_trader.open_long/open_short LOG intended orders and return
# a simulated fill without calling /v5/order/create. Flip to False ONLY after
# observing at least one full evaluation cycle in the logs.
DRY_RUN = False

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
# Required attrs (bot.py reads CLAUDE_FILTER_ENABLED on every entry-watcher tick).
# Set to False to disable the LLM approval gate on Bybit trades.
CLAUDE_FILTER_ENABLED   = False
CLAUDE_FILTER_MODEL     = "claude-haiku-4-5"
CLAUDE_FILTER_HISTORY_N = 30

# --- Strategy Enable Flags ---
# Mirror Bitget bot.py behaviour: all strategies on, gated per-pair by their own configs.
ENABLE_S1 = True
ENABLE_S2 = True
ENABLE_S3 = True
ENABLE_S4 = True
ENABLE_S5 = True
ENABLE_S6 = True
ENABLE_S7 = True

# --- Logging ---
import pathlib as _pl
_DATA_DIR = _pl.Path(os.environ.get("DATA_DIR", "."))
LOG_FILE  = str(_DATA_DIR / "bybit_bot.log")
TRADE_LOG = str(_DATA_DIR / "bybit_trades.csv")
STATE_FILE = str(_DATA_DIR / "bybit_state.json")

# --- Non-Trading Hours --- (PH time, UTC+8) — mirror Bitget defaults
NON_TRADING_HOURS = [
    #(6, 11),
]

# --- Weekend Trading ---
DISABLE_SATURDAY_TRADING = False

# --- Enhanced Trading Windows ---
ENHANCED_TRADING_WINDOWS = [
    (16, 19, 1.5),  # 4pm-7pm PH time (UTC+8) — 1.5x position size
]

REDUCE_TUESDAY_SIZE = False

DEMO_MODE = False

# --- Shadow Tracker (sentiment-blocked virtual trades) ---
# See shadow_tracker.py and config.py for full description. Bybit aliasing
# (bybit_bot.py: sys.modules["config"] = config_bybit) means bot.py reads
# these values when running under bybit_bot.py.
SHADOW_TRACKING_ENABLED = True
SHADOW_STATE_FILE       = str(_DATA_DIR / "bybit_shadow_state.json")
SHADOW_TRADES_CSV       = str(_DATA_DIR / "bybit_shadow_trades.csv")
SHADOW_SCALE_INS_CSV    = str(_DATA_DIR / "bybit_shadow_scale_ins.csv")
