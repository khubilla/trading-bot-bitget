"""All four bot stacks must import cleanly in isolated subprocesses.

Each test runs in its own Python subprocess so the sys.modules aliasing
installed by binance_bot.py / bybit_bot.py does not contaminate sibling tests.
"""

import subprocess
import sys


def _run(code: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=60,
    )
    return proc.returncode, (proc.stdout + proc.stderr)


def test_bitget_imports():
    code, out = _run("import bot; print('OK')")
    assert code == 0, out


def test_ig_imports():
    code, out = _run("import ig_bot; print('OK')")
    assert code == 0, out


def test_bybit_modules_import():
    code, out = _run("import bybit, bybit_trader, bybit_scanner, bybit_client; print('OK')")
    assert code == 0, out


def test_binance_modules_import():
    code, out = _run("import binance, binance_trader, binance_scanner, binance_client; print('OK')")
    assert code == 0, out


def test_binance_aliases_install_and_bot_loads():
    code, out = _run("""
import sys
import config_binance
sys.modules['config'] = config_binance
for n in (1,2,3,4,5,6,7):
    mod = __import__(f'config_binance_s{n}')
    sys.modules[f'config_s{n}'] = mod
import binance, binance_trader, binance_scanner
sys.modules['bitget']  = binance
sys.modules['trader']  = binance_trader
sys.modules['scanner'] = binance_scanner
import state
state.set_file(config_binance.STATE_FILE)
import bot
assert hasattr(bot, 'MTFBot')
print('OK')
""")
    assert code == 0, out


def test_binance_bot_forbids_co_running_with_bot():
    """binance_bot.py must refuse to load when bot.py is already in sys.modules."""
    code, out = _run("""
import bot  # noqa
try:
    import binance_bot
    raise SystemExit('binance_bot should have raised RuntimeError')
except RuntimeError as e:
    assert 'cannot run in the same Python process' in str(e), str(e)
    print('OK')
""")
    assert code == 0, out
