import importlib
import importlib.util
import sys
import pytest


def _load_fresh_config_s5():
    """Load config_s5 from source, bypassing sys.modules, to get the original values."""
    spec = importlib.util.find_spec("config_s5")
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    return {k: v for k, v in vars(fresh).items() if not k.startswith('_')}


# Capture original values once at import time (before any test module is imported
# at the module level by pytest collection).  We use importlib to load a fresh
# copy straight from the source file so that any prior ig_bot patch doesn't
# taint our snapshot.
_ORIGINAL_CONFIG_S5 = _load_fresh_config_s5()


@pytest.fixture(autouse=True)
def restore_config_s5():
    """Restore config_s5 module state after each test.

    ig_bot.py patches config_s5 at import time with IG-specific values.
    Without this fixture, importing ig_bot in one test pollutes config_s5
    for all subsequent tests that call evaluate_s5().
    """
    import config_s5
    yield
    # Restore to original source values after each test
    for k, v in _ORIGINAL_CONFIG_S5.items():
        setattr(config_s5, k, v)
