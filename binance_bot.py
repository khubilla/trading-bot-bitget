"""
binance_bot.py — Binance USDT-M Futures Entry Point

This file is intentionally short. It does NOT clone bot.py's 2700+ lines.

Instead, it installs sys.modules aliases at startup that redirect:
    config       → config_binance
    config_s1..8 → config_binance_s1..8
    bitget       → binance
    trader       → binance_trader
    scanner      → binance_scanner

…and then runs bot.MTFBot().run() — the same main loop the Bitget bot uses,
but every exchange-coupled lookup transparently targets Binance.

⚠️  IMPORTANT — DO NOT import this module from bot.py, ig_bot.py, or bybit_bot.py.
    The aliases are process-global; mixing bots in one Python process will
    corrupt the sibling bots' exchange references. Run as: `python binance_bot.py`
"""

import sys
import logging

# ── 1. Forbid same-process collisions ──────────────────────────── #

_FORBIDDEN_MODULES = ("bot", "ig_bot", "bybit_bot")
for _m in _FORBIDDEN_MODULES:
    if _m in sys.modules:
        raise RuntimeError(
            f"binance_bot.py cannot run in the same Python process as '{_m}'. "
            f"Run as a separate process."
        )

# ── 2. Reject paper mode (not supported for Binance yet) ───────── #

if "--paper" in sys.argv:
    print("ERROR: --paper is not supported for Binance. Use config_binance.DRY_RUN instead.")
    sys.exit(1)

# ── 3. Install sys.modules aliases BEFORE importing bot.py ─────── #

import config_binance
sys.modules["config"] = config_binance

import config_binance_s1
import config_binance_s2
import config_binance_s3
import config_binance_s4
import config_binance_s5
import config_binance_s6
import config_binance_s7
import config_binance_s8
sys.modules["config_s1"] = config_binance_s1
sys.modules["config_s2"] = config_binance_s2
sys.modules["config_s3"] = config_binance_s3
sys.modules["config_s4"] = config_binance_s4
sys.modules["config_s5"] = config_binance_s5
sys.modules["config_s6"] = config_binance_s6
sys.modules["config_s7"] = config_binance_s7
sys.modules["config_s8"] = config_binance_s8

import binance
import binance_trader
import binance_scanner
sys.modules["bitget"]  = binance
sys.modules["trader"]  = binance_trader
sys.modules["scanner"] = binance_scanner

# ── 4. Redirect state file BEFORE bot.py touches state ─────────── #

import state
state.set_file(config_binance.STATE_FILE)

# ── 5. Import bot.py — aliases are now in effect ───────────────── #

import bot

# Sanity print so it's obvious which exchange is wired in.
logger = logging.getLogger(__name__)
logger.info(
    f"[Binance] Aliases installed: config→config_binance, bitget→binance, "
    f"trader→binance_trader, scanner→binance_scanner | "
    f"DRY_RUN={config_binance.DRY_RUN} | state={config_binance.STATE_FILE}"
)


# ── 6. Main entry ──────────────────────────────────────────────── #

if __name__ == "__main__":
    if not config_binance.API_KEY or not config_binance.API_SECRET:
        print("ERROR: Set BINANCE_API_KEY and BINANCE_API_SECRET in environment or .env")
        sys.exit(1)

    # Force one-way position mode at startup (idempotent).
    binance.ensure_one_way_mode()

    bot._check_disclaimer()
    bot.MTFBot().run()
