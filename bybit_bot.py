"""
bybit_bot.py — Bybit USDT-Perp Entry Point

This file is intentionally short. It does NOT clone bot.py's 2700+ lines.

Instead, it installs sys.modules aliases at startup that redirect:
    config       → config_bybit
    config_s1..7 → config_bybit_s1..7
    bitget       → bybit
    trader       → bybit_trader
    scanner      → bybit_scanner

…and then runs bot.MTFBot().run() — the same main loop the Bitget bot uses,
but every exchange-coupled lookup transparently targets Bybit.

⚠️  IMPORTANT — DO NOT import this module from bot.py or ig_bot.py.
    The aliases are process-global; mixing both bots in one Python process
    will corrupt the Bitget bot's exchange references.
    Run as: `python bybit_bot.py`
"""

import sys
import logging

# ── 1. Forbid same-process collisions ──────────────────────────── #

_FORBIDDEN_MODULES = ("bot", "ig_bot")
for _m in _FORBIDDEN_MODULES:
    if _m in sys.modules:
        raise RuntimeError(
            f"bybit_bot.py cannot run in the same Python process as '{_m}'. "
            f"Run as a separate process."
        )

# ── 2. Reject paper mode (not supported for Bybit yet) ─────────── #

if "--paper" in sys.argv:
    print("ERROR: --paper is not supported for Bybit. Use config_bybit.DRY_RUN instead.")
    sys.exit(1)

# ── 3. Install sys.modules aliases BEFORE importing bot.py ─────── #

import config_bybit
sys.modules["config"] = config_bybit

import config_bybit_s1
import config_bybit_s2
import config_bybit_s3
import config_bybit_s4
import config_bybit_s5
import config_bybit_s6
import config_bybit_s7
sys.modules["config_s1"] = config_bybit_s1
sys.modules["config_s2"] = config_bybit_s2
sys.modules["config_s3"] = config_bybit_s3
sys.modules["config_s4"] = config_bybit_s4
sys.modules["config_s5"] = config_bybit_s5
sys.modules["config_s6"] = config_bybit_s6
sys.modules["config_s7"] = config_bybit_s7

import bybit
import bybit_trader
import bybit_scanner
sys.modules["bitget"]  = bybit
sys.modules["trader"]  = bybit_trader
sys.modules["scanner"] = bybit_scanner

# ── 4. Redirect state file BEFORE bot.py touches state ─────────── #

import state
state.set_file(config_bybit.STATE_FILE)

# ── 5. Import bot.py — aliases are now in effect ───────────────── #

import bot

# Sanity print so it's obvious which exchange is wired in.
logger = logging.getLogger(__name__)
logger.info(
    f"[Bybit] Aliases installed: config→config_bybit, bitget→bybit, "
    f"trader→bybit_trader, scanner→bybit_scanner | "
    f"DRY_RUN={config_bybit.DRY_RUN} | state={config_bybit.STATE_FILE}"
)


# ── 6. Main entry ──────────────────────────────────────────────── #

if __name__ == "__main__":
    if not config_bybit.API_KEY or not config_bybit.API_SECRET:
        print("ERROR: Set BYBIT_API_KEY and BYBIT_API_SECRET in environment or .env")
        sys.exit(1)

    bot._check_disclaimer()
    bot.MTFBot().run()
