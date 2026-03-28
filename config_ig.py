"""
IG.com CFD bot configuration.
All credentials are read from environment variables.
Only the active account's keys are needed.

Usage:
  export IG_API_KEY=...
  export IG_USERNAME=...
  export IG_PASSWORD=...
  export IG_ACC_TYPE=DEMO   # or LIVE
  python ig_bot.py --paper  # paper mode, no real orders
  python ig_bot.py          # live (demo or live depending on IG_ACC_TYPE)
"""
import os
from pathlib import Path

# Load .env from project root (no python-dotenv needed)
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# ── IG Credentials ──────────────────────────────────────────── #
IG_API_KEY    = os.environ.get("IG_API_KEY",    "")
IG_USERNAME   = os.environ.get("IG_USERNAME",   "")
IG_PASSWORD   = os.environ.get("IG_PASSWORD",   "")
IG_ACC_TYPE   = os.environ.get("IG_ACC_TYPE",   "DEMO").upper()  # "DEMO" | "LIVE"
IG_ACCOUNT_ID = os.environ.get("IG_ACCOUNT_ID", "")             # optional; auto-selected if ""

IG_DEMO_URL = "https://demo-api.ig.com/gateway/deal"
IG_LIVE_URL = "https://api.ig.com/gateway/deal"

# ── Instrument ──────────────────────────────────────────────── #
EPIC         = "IX.D.DOW.IFD.IP"   # Wall Street Cash (US30 / Dow Jones)
CURRENCY     = "USD"

# ── Contract sizing ─────────────────────────────────────────── #
# Min contract on Wall Street Cash is 0.02.
# Open 0.04 → partial close 0.02 at 1:1 R:R → trail remaining 0.02.
CONTRACT_SIZE = 1   # opening size (contracts)
PARTIAL_SIZE  = 0.5   # close at TP1 (50%)
POINT_VALUE   = 1.0    # USD per point per contract (Wall Street Cash = $1/pt)

# ── Trading Session (US Eastern Time) ───────────────────────── #
# Only enter new trades within this window.
# Any open position at SESSION_END is force-closed at market.
SESSION_START = (9, 30)    # 09:30 ET
SESSION_END   = (12, 30)   # 12:30 ET

# ── Bot Behaviour ───────────────────────────────────────────── #
POLL_INTERVAL_SEC = 45     # 45s; candles are cached so price history stays under 10k pts/week
PAPER_MODE        = False  # override with --paper flag

# ── File paths ──────────────────────────────────────────────── #
LOG_FILE   = "ig_bot.log"
TRADE_LOG  = "ig_trades.csv"
STATE_FILE = "ig_state.json"

# ── Candle fetch limits ─────────────────────────────────────── #
DAILY_LIMIT = 200   # 1D candles  (strategy needs RSI_PERIOD + 50 ≈ 64+)
HTF_LIMIT   = 50    # 1H candles  (S5_HTF_BOS_LOOKBACK + 2 = 12)
M15_LIMIT   = 300   # 15m candles (OB lookback + ChoCH lookback + buffer)
