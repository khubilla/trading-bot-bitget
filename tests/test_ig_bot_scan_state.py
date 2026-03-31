"""
Tests for IGBot scan state — scan_signals + scan_log.

Covers:
  1. _update_scan_state() writes correct entry to _scan_signals
  2. _scan_log gets entry prepended, capped at 20
  3. _save_state() persists scan_signals and scan_log to file
  4. Paper-mode startup restores scan_signals and scan_log from existing state file
"""
import sys
import os
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import ig_bot
import config_ig


def _make_bot(monkeypatch, paper=True):
    """Return an IGBot in paper mode with external calls patched out."""
    monkeypatch.setattr(ig_bot, "_in_trading_window", lambda now: True)
    monkeypatch.setattr(ig_bot, "_is_session_end",    lambda now: False)
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    monkeypatch.setattr(config_ig, "STATE_FILE", tmp.name)
    return ig_bot.IGBot(paper=paper)


# ── 1. _update_scan_state writes correct entry ─────────────────────── #

def test_update_scan_state_writes_signal_fields(monkeypatch):
    """_update_scan_state() stores all expected fields in _scan_signals."""
    bot = _make_bot(monkeypatch)
    bot._update_scan_state(
        instrument="US30",
        signal="PENDING_SHORT",
        reason="Daily EMA bearish ✅ | 1H BOS confirmed ✅ | OB ✅",
        ob_low=44210.0,
        ob_high=44380.0,
        entry_trigger=44380.0,
        sl=44450.0,
        tp=44210.0,
    )
    entry = bot._scan_signals["US30"]
    assert entry["signal"] == "PENDING_SHORT"
    assert entry["ema_ok"] is True
    assert entry["bos_ok"] is True
    assert entry["ob_ok"]  is True
    assert entry["ob_low"]  == 44210.0
    assert entry["ob_high"] == 44380.0
    assert entry["entry_trigger"] == 44380.0
    assert entry["sl"] == 44450.0
    assert entry["tp"] == 44210.0
    assert "updated_at" in entry


def test_update_scan_state_ema_false_when_flat(monkeypatch):
    """ema_ok is False when reason starts with 'Daily EMA flat'."""
    bot = _make_bot(monkeypatch)
    bot._update_scan_state("US30", "HOLD", "Daily EMA flat (EMAs not aligned)",
                            0.0, 0.0, 0.0, 0.0, 0.0)
    assert bot._scan_signals["US30"]["ema_ok"] is False
    assert bot._scan_signals["US30"]["bos_ok"] is False
    assert bot._scan_signals["US30"]["ob_ok"]  is False


def test_update_scan_state_bos_ok_but_no_ob(monkeypatch):
    """bos_ok=True, ob_ok=False when reason shows BOS ✅ but no OB found."""
    bot = _make_bot(monkeypatch)
    bot._update_scan_state("US30", "HOLD", "1H BOS ✅ | No bearish OB found (lookback=10)",
                            0.0, 0.0, 0.0, 0.0, 0.0)
    assert bot._scan_signals["US30"]["ema_ok"] is True
    assert bot._scan_signals["US30"]["bos_ok"] is True
    assert bot._scan_signals["US30"]["ob_ok"]  is False


# ── 2. _scan_log capped at 20, newest first ────────────────────────── #

def test_scan_log_prepends_entry(monkeypatch):
    """Each _update_scan_state() call prepends one entry to _scan_log."""
    bot = _make_bot(monkeypatch)
    bot._update_scan_state("US30", "HOLD", "Daily EMA flat", 0.0, 0.0, 0.0, 0.0, 0.0)
    assert len(bot._scan_log) == 1
    assert bot._scan_log[0]["instrument"] == "US30"
    assert bot._scan_log[0]["message"]    == "Daily EMA flat"
    assert "ts" in bot._scan_log[0]


def test_scan_log_newest_first(monkeypatch):
    """Newest entry is at index 0."""
    bot = _make_bot(monkeypatch)
    bot._update_scan_state("US30", "HOLD", "first",  0.0, 0.0, 0.0, 0.0, 0.0)
    bot._update_scan_state("US30", "HOLD", "second", 0.0, 0.0, 0.0, 0.0, 0.0)
    assert bot._scan_log[0]["message"] == "second"
    assert bot._scan_log[1]["message"] == "first"


def test_scan_log_capped_at_20(monkeypatch):
    """_scan_log never exceeds 20 entries."""
    bot = _make_bot(monkeypatch)
    for i in range(25):
        bot._update_scan_state("US30", "HOLD", f"msg-{i}", 0.0, 0.0, 0.0, 0.0, 0.0)
    assert len(bot._scan_log) == 20
    assert bot._scan_log[0]["message"] == "msg-24"


# ── 3. _save_state persists scan fields ───────────────────────────── #

def test_save_state_persists_scan_fields(monkeypatch):
    """_save_state() writes scan_signals and scan_log to the state file."""
    bot = _make_bot(monkeypatch)
    bot._update_scan_state("US30", "HOLD", "EMA flat", 0.0, 0.0, 0.0, 0.0, 0.0)
    bot._save_state()

    with open(config_ig.STATE_FILE) as f:
        data = json.load(f)

    assert "scan_signals" in data
    assert "US30" in data["scan_signals"]
    assert "scan_log" in data
    assert len(data["scan_log"]) == 1
    assert data["scan_log"][0]["message"] == "EMA flat"


# ── 4. Startup restores scan fields ───────────────────────────────── #

def test_startup_restores_scan_fields(monkeypatch):
    """Paper-mode IGBot restores scan_signals and scan_log from existing state file."""
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    json.dump({
        "position": None,
        "pending_order": None,
        "scan_signals": {"US30": {"signal": "HOLD", "reason": "restored", "ema_ok": False,
                                   "bos_ok": False, "ob_ok": False, "ob_low": None,
                                   "ob_high": None, "entry_trigger": None,
                                   "sl": None, "tp": None, "updated_at": "2026-01-01T00:00:00Z"}},
        "scan_log": [{"ts": "09:30", "instrument": "US30", "message": "restored"}],
    }, tmp)
    tmp.close()

    monkeypatch.setattr(ig_bot, "_in_trading_window", lambda now: True)
    monkeypatch.setattr(ig_bot, "_is_session_end",    lambda now: False)
    monkeypatch.setattr(config_ig, "STATE_FILE", tmp.name)

    bot = ig_bot.IGBot(paper=True)
    assert bot._scan_signals["US30"]["reason"] == "restored"
    assert bot._scan_log[0]["message"] == "restored"


import httpx


def test_ig_state_endpoint_includes_scan_fields(live_server_url):
    """/api/ig/state response includes scan_signals and scan_log keys."""
    r = httpx.get(
        f"{live_server_url}/api/ig/state",
        headers={"Authorization": "Bearer test-token"},
        timeout=5.0,
    )
    assert r.status_code == 200
    data = r.json()
    assert "scan_signals" in data, "scan_signals missing from /api/ig/state response"
    assert "scan_log" in data,     "scan_log missing from /api/ig/state response"
    assert isinstance(data["scan_signals"], dict)
    assert isinstance(data["scan_log"], list)
