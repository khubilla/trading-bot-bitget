"""
IG.com CFD bot configuration — registry and shared settings.
Per-instrument params live in config_ig_<name>.py.

Usage:
  export IG_API_KEY=...
  export IG_USERNAME=...
  export IG_PASSWORD=...
  export IG_ACC_TYPE=DEMO   # or LIVE
  python ig_bot.py --paper  # paper mode, no real orders
  python ig_bot.py          # live
"""
import os
from pathlib import Path

from config_ig_us30 import CONFIG as _US30
from config_ig_gold  import CONFIG as _GOLD

INSTRUMENTS = [_US30, _GOLD]

# Load .env from project root
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# ── IG Credentials ─────────────────────────────────────────── #
IG_API_KEY    = os.environ.get("IG_API_KEY",    "")
IG_USERNAME   = os.environ.get("IG_USERNAME",   "")
IG_PASSWORD   = os.environ.get("IG_PASSWORD",   "")
IG_ACC_TYPE   = os.environ.get("IG_ACC_TYPE",   "DEMO").upper()
IG_ACCOUNT_ID = os.environ.get("IG_ACCOUNT_ID", "")

IG_DEMO_URL = "https://demo-api.ig.com/gateway/deal"
IG_LIVE_URL = "https://api.ig.com/gateway/deal"

# ── Shared settings ─────────────────────────────────────────── #
SESSION_START     = (0, 0)
SESSION_END       = (23, 59)
POLL_INTERVAL_SEC = 45
PAPER_MODE        = False

# ── File paths ──────────────────────────────────────────────── #
LOG_FILE   = "ig_bot.log"
TRADE_LOG  = "ig_trades.csv"
STATE_FILE = "ig_state.json"
