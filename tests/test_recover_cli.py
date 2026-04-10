"""
tests/test_recover_cli.py — Tests for recover.py CLI (Bitget-sync redesign).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import csv
import json
import pandas as pd
import pytest
from pathlib import Path
from unittest.mock import patch


# ── Helpers ──────────────────────────────────────────────────────────────── #

def _reload_recover(monkeypatch, state_file, csv_file):
    """Re-import recover with patched file paths."""
    if "recover" in sys.modules:
        del sys.modules["recover"]
    import recover
    recover.STATE_FILE = str(state_file)
    recover.TRADE_LOG  = str(csv_file)
    import state as st
    monkeypatch.setattr(st, "STATE_FILE", str(state_file))
    return recover


def _write_state(path, open_trades):
    data = {
        "open_trades": open_trades,
        "pending_signals": {},
        "position_memory": {},
        "balance": 500.0,
    }
    Path(path).write_text(json.dumps(data))
    return data


def _write_csv(path, rows):
    """Write a minimal trades.csv with given rows."""
    fields = [
        "timestamp", "trade_id", "action", "symbol", "side", "qty",
        "entry", "sl", "tp", "box_low", "box_high", "leverage", "margin",
        "tpsl_set", "strategy", "result", "pnl", "pnl_pct",
        "exit_reason", "exit_price",
    ]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", restval="")
        w.writeheader()
        for r in rows:
            w.writerow(r)


# ── Task 1: Unit tests for pure helpers ──────────────────────────────────── #

class TestGetOpenCsvRow:
    def test_returns_most_recent_open_row(self, tmp_path):
        """Returns the last _LONG/_SHORT row for the symbol."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        csv_file = tmp_path / "trades.csv"
        _write_csv(csv_file, [
            {"action": "S5_SHORT", "symbol": "LINKUSDT", "qty": "14", "trade_id": "aaa"},
        ])
        row = recover._get_open_csv_row(str(csv_file), "LINKUSDT")
        assert row is not None
        assert row["trade_id"] == "aaa"

    def test_returns_none_when_no_matching_row(self, tmp_path):
        """Returns None when symbol not in CSV."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        csv_file = tmp_path / "trades.csv"
        _write_csv(csv_file, [
            {"action": "S5_SHORT", "symbol": "BTCUSDT", "qty": "1", "trade_id": "bbb"},
        ])
        assert recover._get_open_csv_row(str(csv_file), "LINKUSDT") is None

    def test_returns_none_when_csv_missing(self, tmp_path):
        """Returns None when CSV file does not exist."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        assert recover._get_open_csv_row(str(tmp_path / "missing.csv"), "LINKUSDT") is None

    def test_ignores_close_rows(self, tmp_path):
        """Rows with action S5_CLOSE are not returned."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        csv_file = tmp_path / "trades.csv"
        _write_csv(csv_file, [
            {"action": "S5_CLOSE", "symbol": "LINKUSDT", "qty": "14", "trade_id": "ccc"},
        ])
        assert recover._get_open_csv_row(str(csv_file), "LINKUSDT") is None


class TestIsValidSltp:
    def test_valid_floats_return_true(self):
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        assert recover._is_valid_sltp(9.5, 8.0) is True

    def test_string_question_mark_returns_false(self):
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        assert recover._is_valid_sltp("?", 8.0) is False

    def test_none_returns_false(self):
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        assert recover._is_valid_sltp(None, 8.0) is False

    def test_zero_returns_false(self):
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        assert recover._is_valid_sltp(0, 8.0) is False

    def test_empty_string_returns_false(self):
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        assert recover._is_valid_sltp("", 8.0) is False

    def test_string_float_returns_true(self):
        """String floats (as stored in CSV) should be accepted."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        assert recover._is_valid_sltp("9.777", "8.500") is True


class TestPatchSltp:
    """_patch_sltp updates state.json SL/TP without writing a CSV row."""

    def _exchange_pos(self, side="SHORT", entry=9.311):
        return {
            "side": side, "entry_price": entry,
            "qty": 14.0, "margin": 13.05, "leverage": 10,
        }

    def test_patches_state_json_sl_tp(self, tmp_path, monkeypatch):
        """_patch_sltp writes new sl/tp into state.json for the symbol."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        import state as st
        import trader as tr
        import startup_recovery

        state_file = tmp_path / "state.json"
        _write_state(state_file, [{
            "symbol": "LINKUSDT", "side": "SHORT", "qty": 14.0,
            "entry": 9.311, "sl": "?", "tp": "?", "strategy": "S3",
            "trade_id": "abc123", "opened_at": "2026-04-08T09:00:00+00:00",
            "margin": 13.05, "leverage": 10, "tpsl_set": False,
        }])
        monkeypatch.setattr(st, "STATE_FILE", str(state_file))

        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())
        monkeypatch.setattr(tr, "get_candles",
                            lambda sym, i, limit=100: pd.DataFrame())

        result = recover._patch_sltp(
            "LINKUSDT",
            {"strategy": "S3", "opened_at": "2026-04-08T09:00:00+00:00"},
            self._exchange_pos(),
            str(state_file),
            str(tmp_path / "trades.csv"),
            dry_run=False,
        )

        assert result["action"] == "PATCH_SLTP"
        assert float(result["sl"]) > 0
        assert float(result["tp"]) > 0

        # state.json updated
        data = json.loads(state_file.read_text())
        t = data["open_trades"][0]
        assert float(t["sl"]) > 0
        assert float(t["tp"]) > 0

    def test_dry_run_does_not_write_state(self, tmp_path, monkeypatch):
        """--dry-run: state.json is not modified by _patch_sltp."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        import state as st
        import trader as tr
        import startup_recovery

        state_file = tmp_path / "state.json"
        _write_state(state_file, [{
            "symbol": "LINKUSDT", "side": "SHORT", "qty": 14.0,
            "entry": 9.311, "sl": "?", "tp": "?", "strategy": "S3",
            "trade_id": "abc123", "opened_at": "2026-04-08T09:00:00+00:00",
            "margin": 13.05, "leverage": 10, "tpsl_set": False,
        }])
        monkeypatch.setattr(st, "STATE_FILE", str(state_file))
        before = state_file.read_text()

        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())
        monkeypatch.setattr(tr, "get_candles",
                            lambda sym, i, limit=100: pd.DataFrame())

        recover._patch_sltp(
            "LINKUSDT",
            {"strategy": "S3", "opened_at": "2026-04-08T09:00:00+00:00"},
            self._exchange_pos(),
            str(state_file),
            str(tmp_path / "trades.csv"),
            dry_run=True,
        )

        assert state_file.read_text() == before

    def test_uses_s5_ob_recovery_for_s5_strategy(self, tmp_path, monkeypatch):
        """For S5 strategy, attempt_s5_recovery is called when candles are available."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        import state as st
        import trader as tr
        import startup_recovery

        state_file = tmp_path / "state.json"
        _write_state(state_file, [{
            "symbol": "LINKUSDT", "side": "SHORT", "qty": 14.0,
            "entry": 9.311, "sl": "?", "tp": "?", "strategy": "S5",
            "trade_id": "abc123", "opened_at": "2026-04-08T09:00:00+00:00",
            "margin": 13.05, "leverage": 10, "tpsl_set": False,
        }])
        monkeypatch.setattr(st, "STATE_FILE", str(state_file))

        # Return non-empty DataFrames so the guard passes and attempt_s5_recovery is called
        n = 5
        fake_df = pd.DataFrame({
            "ts": list(range(n)), "open": [9.0]*n, "high": [9.1]*n,
            "low": [8.9]*n, "close": [9.05]*n, "vol": [100.0]*n,
        })
        s5_called = []
        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: fake_df)
        monkeypatch.setattr(recover, "fetch_candles_at",
                            lambda *a, **kw: fake_df)
        monkeypatch.setattr(tr, "get_candles",
                            lambda sym, i, limit=100: fake_df)
        monkeypatch.setattr(
            recover, "attempt_s5_recovery",
            lambda *a, **kw: s5_called.append(True) or None,
        )

        recover._patch_sltp(
            "LINKUSDT",
            {"strategy": "S5", "opened_at": "2026-04-08T09:00:00+00:00"},
            self._exchange_pos(),
            str(state_file),
            str(tmp_path / "trades.csv"),
            dry_run=False,
        )

        assert s5_called, "attempt_s5_recovery must be called for S5 strategy"

    def test_does_not_write_csv(self, tmp_path, monkeypatch):
        """_patch_sltp never creates or appends to trades.csv."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        import state as st
        import trader as tr
        import startup_recovery

        state_file = tmp_path / "state.json"
        csv_file   = tmp_path / "trades.csv"
        _write_state(state_file, [{
            "symbol": "LINKUSDT", "side": "SHORT", "qty": 14.0,
            "entry": 9.311, "sl": "?", "tp": "?", "strategy": "S1",
            "trade_id": "abc123", "opened_at": "2026-04-08T09:00:00+00:00",
            "margin": 13.05, "leverage": 10, "tpsl_set": False,
        }])
        monkeypatch.setattr(st, "STATE_FILE", str(state_file))

        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())
        monkeypatch.setattr(tr, "get_candles",
                            lambda sym, i, limit=100: pd.DataFrame())

        recover._patch_sltp(
            "LINKUSDT",
            {"strategy": "S1", "opened_at": "2026-04-08T09:00:00+00:00"},
            self._exchange_pos(),
            str(state_file),
            str(csv_file),
            dry_run=False,
        )

        assert not csv_file.exists(), "_patch_sltp must not write trades.csv"


class TestFullRecovery:
    """_full_recovery: new trade_id, CSV open row, state patch, snapshot."""

    def _exchange_pos(self, side="SHORT", entry=9.311):
        return {
            "side": side, "entry_price": entry,
            "qty": 14.0, "margin": 13.05, "leverage": 10,
        }

    def test_writes_csv_row_and_patches_state(self, tmp_path, monkeypatch):
        """FULL_RECOVERY writes a CSV open row and patches state.json."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        import state as st
        import trader as tr
        import startup_recovery
        import snapshot

        state_file = tmp_path / "state.json"
        csv_file   = tmp_path / "trades.csv"
        _write_state(state_file, [])
        monkeypatch.setattr(st, "STATE_FILE", str(state_file))

        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())
        monkeypatch.setattr(recover, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())
        monkeypatch.setattr(tr, "get_candles",
                            lambda sym, i, limit=100: pd.DataFrame())
        monkeypatch.setattr(snapshot, "save_snapshot", lambda **kw: None)

        result = recover._full_recovery(
            "LINKUSDT", self._exchange_pos(),
            str(state_file), str(csv_file),
            dry_run=False,
        )

        assert result["action"] == "FULL"
        assert len(result["trade_id"]) == 8
        assert float(result["sl"]) > 0
        assert float(result["tp"]) > 0

        # CSV row written
        assert csv_file.exists()
        with open(csv_file, newline="") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1
        assert rows[0]["symbol"] == "LINKUSDT"
        assert rows[0]["trade_id"] == result["trade_id"]

        # state.json patched
        data = json.loads(state_file.read_text())
        trades = data["open_trades"]
        assert any(t["symbol"] == "LINKUSDT" for t in trades)

    def test_dry_run_writes_nothing(self, tmp_path, monkeypatch):
        """dry_run=True: no files written."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        import state as st
        import trader as tr
        import startup_recovery

        state_file = tmp_path / "state.json"
        csv_file   = tmp_path / "trades.csv"
        _write_state(state_file, [])
        monkeypatch.setattr(st, "STATE_FILE", str(state_file))
        state_before = state_file.read_text()

        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())
        monkeypatch.setattr(recover, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())
        monkeypatch.setattr(tr, "get_candles",
                            lambda sym, i, limit=100: pd.DataFrame())

        recover._full_recovery(
            "LINKUSDT", self._exchange_pos(),
            str(state_file), str(csv_file),
            dry_run=True,
        )

        assert state_file.read_text() == state_before
        assert not csv_file.exists()

    def test_snapshot_saved_when_candles_available(self, tmp_path, monkeypatch):
        """snapshot.save_snapshot is called when m15 candles are non-empty."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        import state as st
        import trader as tr
        import startup_recovery
        import snapshot

        state_file = tmp_path / "state.json"
        csv_file   = tmp_path / "trades.csv"
        _write_state(state_file, [])
        monkeypatch.setattr(st, "STATE_FILE", str(state_file))

        n = 10
        fake_df = pd.DataFrame({
            "ts": list(range(n)), "open": [9.0]*n, "high": [9.1]*n,
            "low": [8.9]*n, "close": [9.05]*n, "vol": [100.0]*n,
        })

        monkeypatch.setattr(
            startup_recovery, "fetch_candles_at",
            lambda sym, interval, **kw: fake_df,
        )
        monkeypatch.setattr(
            recover, "fetch_candles_at",
            lambda sym, interval, **kw: fake_df,
        )
        monkeypatch.setattr(tr, "get_candles",
                            lambda sym, i, limit=100: fake_df)

        snap_calls = []
        monkeypatch.setattr(snapshot, "save_snapshot",
                            lambda **kw: snap_calls.append(kw))

        recover._full_recovery(
            "LINKUSDT", self._exchange_pos(),
            str(state_file), str(csv_file),
            dry_run=False,
        )

        assert len(snap_calls) == 1
        assert snap_calls[0]["event"] == "open"
        assert snap_calls[0]["symbol"] == "LINKUSDT"

    def test_snapshot_skipped_when_no_candles(self, tmp_path, monkeypatch):
        """snapshot.save_snapshot is NOT called when m15 candles are empty."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        import state as st
        import trader as tr
        import startup_recovery
        import snapshot

        state_file = tmp_path / "state.json"
        csv_file   = tmp_path / "trades.csv"
        _write_state(state_file, [])
        monkeypatch.setattr(st, "STATE_FILE", str(state_file))

        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())
        monkeypatch.setattr(recover, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())
        monkeypatch.setattr(tr, "get_candles",
                            lambda sym, i, limit=100: pd.DataFrame())

        snap_calls = []
        monkeypatch.setattr(snapshot, "save_snapshot",
                            lambda **kw: snap_calls.append(kw))

        recover._full_recovery(
            "LINKUSDT", self._exchange_pos(),
            str(state_file), str(csv_file),
            dry_run=False,
        )

        assert snap_calls == []


import snapshot as snapshot_mod


class TestMainTwoPasses:
    """Integration tests for the rewritten main()."""

    def _bitget_pos(self, sym, side="SHORT", entry=9.311):
        return {
            "side": side, "entry_price": entry,
            "qty": 14.0, "margin": 13.05, "leverage": 10,
        }

    def test_skips_position_with_valid_sltp(self, tmp_path, monkeypatch, capsys):
        """Pass 1: SKIP when CSV row exists and SL/TP are valid."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        import state as st
        import trader as tr

        state_file = tmp_path / "state.json"
        csv_file   = tmp_path / "trades.csv"
        _write_state(state_file, [{
            "symbol": "LINKUSDT", "side": "SHORT", "qty": 14.0,
            "entry": 9.311, "sl": 9.777, "tp": 8.500, "strategy": "S5",
            "trade_id": "abc123", "opened_at": "2026-04-08T09:00:00+00:00",
            "margin": 13.05, "leverage": 10, "tpsl_set": True,
        }])
        _write_csv(csv_file, [{
            "action": "S5_SHORT", "symbol": "LINKUSDT", "qty": "14",
            "trade_id": "abc123", "sl": "9.777", "tp": "8.500",
        }])
        monkeypatch.setattr(st, "STATE_FILE", str(state_file))
        monkeypatch.setattr(tr, "get_all_open_positions",
                            lambda: {"LINKUSDT": self._bitget_pos("LINKUSDT")})

        recover.STATE_FILE = str(state_file)
        recover.TRADE_LOG  = str(csv_file)
        recover.main([])

        out = capsys.readouterr().out
        assert "SKIP" in out
        assert "LINKUSDT" in out

    def test_patch_sltp_for_known_strategy_missing_sltp(self, tmp_path, monkeypatch, capsys):
        """Pass 1: PATCH_SLTP when CSV exists but SL/TP are bad."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        import state as st
        import trader as tr
        import startup_recovery

        state_file = tmp_path / "state.json"
        csv_file   = tmp_path / "trades.csv"
        _write_state(state_file, [{
            "symbol": "LINKUSDT", "side": "SHORT", "qty": 14.0,
            "entry": 9.311, "sl": "?", "tp": "?", "strategy": "S3",
            "trade_id": "abc123", "opened_at": "2026-04-08T09:00:00+00:00",
            "margin": 13.05, "leverage": 10, "tpsl_set": False,
        }])
        _write_csv(csv_file, [{
            "action": "S3_SHORT", "symbol": "LINKUSDT", "qty": "14",
            "trade_id": "abc123", "sl": "?", "tp": "?",
        }])
        monkeypatch.setattr(st, "STATE_FILE", str(state_file))
        monkeypatch.setattr(tr, "get_all_open_positions",
                            lambda: {"LINKUSDT": self._bitget_pos("LINKUSDT")})
        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())
        monkeypatch.setattr(tr, "get_candles",
                            lambda sym, i, limit=100: pd.DataFrame())

        recover.STATE_FILE = str(state_file)
        recover.TRADE_LOG  = str(csv_file)
        recover.main([])

        out = capsys.readouterr().out
        assert "PATCH_SLTP" in out
        assert "LINKUSDT" in out

    def test_full_recovery_for_position_with_no_csv(self, tmp_path, monkeypatch, capsys):
        """Pass 1: FULL_RECOVERY when no CSV open row exists."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        import state as st
        import trader as tr
        import startup_recovery

        state_file = tmp_path / "state.json"
        csv_file   = tmp_path / "trades.csv"
        _write_state(state_file, [])
        monkeypatch.setattr(st, "STATE_FILE", str(state_file))
        monkeypatch.setattr(tr, "get_all_open_positions",
                            lambda: {"LINKUSDT": self._bitget_pos("LINKUSDT")})
        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())
        monkeypatch.setattr(tr, "get_candles",
                            lambda sym, i, limit=100: pd.DataFrame())
        monkeypatch.setattr(snapshot_mod, "save_snapshot", lambda **kw: None)

        recover.STATE_FILE = str(state_file)
        recover.TRADE_LOG  = str(csv_file)
        recover.main([])

        out = capsys.readouterr().out
        assert "FULL" in out
        assert "LINKUSDT" in out

    def test_pass2_warns_for_state_position_not_on_bitget(self, tmp_path, monkeypatch, capsys):
        """Pass 2: WARNING printed for position in state.json but not on Bitget."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        import state as st
        import trader as tr

        state_file = tmp_path / "state.json"
        csv_file   = tmp_path / "trades.csv"
        _write_state(state_file, [{
            "symbol": "XRPUSDT", "side": "SHORT", "qty": 100.0,
            "entry": 0.5, "sl": 0.525, "tp": 0.45, "strategy": "S3",
            "trade_id": "xyz789", "opened_at": "2026-04-08T09:00:00+00:00",
            "margin": 5.0, "leverage": 10, "tpsl_set": True,
        }])
        monkeypatch.setattr(st, "STATE_FILE", str(state_file))
        monkeypatch.setattr(tr, "get_all_open_positions", lambda: {})

        recover.STATE_FILE = str(state_file)
        recover.TRADE_LOG  = str(csv_file)
        recover.main([])

        out = capsys.readouterr().out
        assert "WARNING" in out or "⚠" in out
        assert "XRPUSDT" in out

    def test_symbols_filter_limits_pass1(self, tmp_path, monkeypatch, capsys):
        """--symbols LINKUSDT: only LINKUSDT processed in Pass 1, ETHUSDT skipped."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        import state as st
        import trader as tr
        import startup_recovery

        state_file = tmp_path / "state.json"
        csv_file   = tmp_path / "trades.csv"
        _write_state(state_file, [])
        monkeypatch.setattr(st, "STATE_FILE", str(state_file))
        monkeypatch.setattr(tr, "get_all_open_positions", lambda: {
            "LINKUSDT": self._bitget_pos("LINKUSDT"),
            "ETHUSDT":  self._bitget_pos("ETHUSDT", entry=2000.0),
        })
        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())
        monkeypatch.setattr(tr, "get_candles",
                            lambda sym, i, limit=100: pd.DataFrame())
        monkeypatch.setattr(snapshot_mod, "save_snapshot", lambda **kw: None)

        recover.STATE_FILE = str(state_file)
        recover.TRADE_LOG  = str(csv_file)
        recover.main(["--symbols", "LINKUSDT"])

        out = capsys.readouterr().out
        assert "LINKUSDT" in out
        assert "ETHUSDT" not in out

    def test_dry_run_prints_prefix_and_writes_nothing(self, tmp_path, monkeypatch, capsys):
        """--dry-run: output contains [DRY RUN] and no files are written."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        import state as st
        import trader as tr
        import startup_recovery

        state_file = tmp_path / "state.json"
        csv_file   = tmp_path / "trades.csv"
        _write_state(state_file, [])
        state_before = state_file.read_text()
        monkeypatch.setattr(st, "STATE_FILE", str(state_file))
        monkeypatch.setattr(tr, "get_all_open_positions",
                            lambda: {"LINKUSDT": self._bitget_pos("LINKUSDT")})
        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())
        monkeypatch.setattr(tr, "get_candles",
                            lambda sym, i, limit=100: pd.DataFrame())

        recover.STATE_FILE = str(state_file)
        recover.TRADE_LOG  = str(csv_file)
        recover.main(["--dry-run"])

        out = capsys.readouterr().out
        assert "[DRY RUN]" in out
        assert state_file.read_text() == state_before
        assert not csv_file.exists()

    def test_no_positions_prints_nothing_to_recover(self, tmp_path, monkeypatch, capsys):
        """When Bitget returns no positions and state is empty, prints nothing-to-recover."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        import state as st
        import trader as tr

        state_file = tmp_path / "state.json"
        csv_file   = tmp_path / "trades.csv"
        _write_state(state_file, [])
        monkeypatch.setattr(st, "STATE_FILE", str(state_file))
        monkeypatch.setattr(tr, "get_all_open_positions", lambda: {})

        recover.STATE_FILE = str(state_file)
        recover.TRADE_LOG  = str(csv_file)
        recover.main([])

        out = capsys.readouterr().out
        assert "nothing" in out.lower() or "0 position" in out.lower()
