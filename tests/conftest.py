import importlib
import importlib.util
import sys
import pytest


@pytest.fixture(autouse=True)
def _clear_api_key_for_testclient(request, monkeypatch):
    """Clear DASHBOARD_API_KEY for tests that use TestClient directly.

    live_server_url (session-scoped) sets DASHBOARD_API_KEY=test-token and never
    restores it, so every subsequent TestClient test gets 401.  Tests that use
    live_server_url need the env var; tests that don't (TestClient) need it absent.
    """
    if "live_server_url" not in request.fixturenames:
        monkeypatch.delenv("DASHBOARD_API_KEY", raising=False)
    yield


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Reset slowapi in-memory rate limit counters before each test.

    The live_server_url fixture uses a session-scoped server, so rate limit
    state persists across tests.  TestRateLimiting::test_chat_rate_limit_enforced
    exhausts the 10/minute window for 127.0.0.1; without a reset the next test
    (test_chat_within_limit_succeeds) would immediately receive 429.
    """
    import dashboard
    try:
        dashboard.limiter._storage.reset()
    except Exception:
        pass
    yield


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


@pytest.fixture(autouse=True)
def cleanup_ig_trades_csv():
    """Clean up ig_trades.csv before and after each test.

    IG bot tests that instantiate IGBot objects may call _log_trade(), which
    appends rows to ig_trades.csv. Without cleanup, test data pollutes the
    production CSV file.

    Strategy: Back up the file before test, restore after test. If no backup
    exists (clean repo), remove the file after test.
    """
    csv_path = "ig_trades.csv"
    backup_path = "ig_trades.csv.test_backup"
    had_original = os.path.exists(csv_path)

    # Back up existing file if present
    if had_original:
        shutil.copy2(csv_path, backup_path)

    yield

    # Restore original or remove test-generated file
    if had_original:
        shutil.move(backup_path, csv_path)
    elif os.path.exists(csv_path):
        os.remove(csv_path)


import json
import threading
import time
import numpy as np
import pandas as pd
from unittest.mock import patch
import shutil
import os


def _make_mock_candles(n: int = 280) -> pd.DataFrame:
    """Return a minimal OHLCV DataFrame for the candles endpoint."""
    now_ms = int(time.time() * 1000)
    timestamps = [now_ms - i * 180_000 for i in range(n, 0, -1)]
    close = np.linspace(100.0, 110.0, n)
    return pd.DataFrame({
        "timestamp": timestamps,
        "open":   close - 0.5,
        "high":   close + 1.0,
        "low":    close - 1.0,
        "close":  close,
        "volume": np.full(n, 5000.0),
    })


@pytest.fixture(scope="session")
def live_server_url(tmp_path_factory):
    """Start a real uvicorn server on port 8099 with mocked external deps.

    Patches applied for the entire session:
      - dashboard.STATE_FILE     → tmp state.json (no real bot needed)
      - dashboard.IG_STATE_FILE  → tmp ig_state.json
      - trader.get_candles       → returns mock DataFrame
      - claude_analyst.stream_response      → yields one token then stops
      - claude_analyst.build_system_prompt  → returns "mock prompt"
    """
    import uvicorn
    import dashboard

    tmpdir = tmp_path_factory.mktemp("live_server")

    state_data = {
        "status": "RUNNING",
        "started_at": "2026-01-01T00:00:00",
        "last_tick": "2026-01-01T00:00:00",
        "balance": 1000.0,
        "open_trades": [],
        "trade_history": [],
        "scan_log": [],
        "qualified_pairs": [],
        "pair_states": {},
        "position_memory": {},
        "sentiment": {
            "direction": "NEUTRAL",
            "bullish_weight": 0.5,
            "green_count": 0,
            "red_count": 0,
            "total_pairs": 0,
            "green_volume": 0.0,
            "red_volume": 0.0,
            "updated_at": None,
        },
        "stats": {
            "total_trades": 0,
            "win_rate": 0.0,
            "total_pnl": 0.0,
            "avg_pnl": 0.0,
            "best_trade": 0.0,
            "worst_trade": 0.0,
        },
        "strategy_enabled": {
            "S1": True, "S2": True, "S3": True, "S4": True, "S5": True,
        },
    }
    state_file = str(tmpdir / "state.json")
    with open(state_file, "w") as f:
        json.dump(state_data, f)

    ig_state_file = str(tmpdir / "ig_state.json")
    with open(ig_state_file, "w") as f:
        json.dump({"status": "STOPPED", "position": None, "trade_history": [], "stats": {}}, f)

    mock_df = _make_mock_candles()

    active_patches = [
        patch.object(dashboard, "STATE_FILE", state_file),
        patch.object(dashboard, "IG_STATE_FILE", ig_state_file),
        patch("trader.get_candles", return_value=mock_df),
        patch("claude_analyst.build_system_prompt", return_value="mock prompt"),
        patch("claude_analyst.stream_response", side_effect=lambda *a, **kw: iter([{"role": "assistant", "content": "ok"}])),
    ]

    for p in active_patches:
        p.start()

    import os as _os
    _os.environ["DASHBOARD_API_KEY"] = "test-token"

    config = uvicorn.Config(dashboard.app, host="127.0.0.1", port=8099, log_level="error", server_header=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    import httpx as _httpx
    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            _httpx.get("http://127.0.0.1:8099/api/state", timeout=0.5)
            break
        except Exception:
            time.sleep(0.1)
    else:
        server.should_exit = True
        thread.join(timeout=3)
        raise RuntimeError("Test server did not start within 5 seconds on port 8099")

    yield "http://127.0.0.1:8099"

    server.should_exit = True
    thread.join(timeout=5)

    for p in reversed(active_patches):
        p.stop()
