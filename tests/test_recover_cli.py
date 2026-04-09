"""
tests/test_recover_cli.py — Tests for recover.py CLI.
Manual recovery when bot is already running.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import csv
import json
import pandas as pd
import pytest
from unittest.mock import patch
from io import StringIO


class TestRecoverCli:
    def _make_state(self, tmp_path, symbols=None):
        """Write a minimal state.json with UNKNOWN open trades."""
        symbols = symbols or ["LINKUSDT", "UNIUSDT"]
        trades = [
            {
                "symbol": sym, "side": "SHORT",
                "qty": 14.0, "entry": 9.311,
                "sl": "?", "tp": "?",
                "strategy": "UNKNOWN", "trade_id": "",
                "opened_at": "2026-04-08T09:05:59+00:00",
                "margin": 13.05, "leverage": 10,
                "unrealised_pnl": 1.5, "mark_price": 9.0,
                "tpsl_set": False,
            }
            for sym in symbols
        ]
        state = {
            "open_trades": trades,
            "pending_signals": {},
            "position_memory": {},
            "balance": 500.0,
        }
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(state))
        return state_file

    def _run_cli(self, args: list, state_file, csv_file):
        """Import and run recover.main() with given args."""
        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        recover.STATE_FILE = str(state_file)
        recover.TRADE_LOG  = str(csv_file)
        recover.main(args)

    def test_dry_run_writes_nothing(self, tmp_path, monkeypatch):
        """--dry-run flag: no files are written, no state changes."""
        state_file = self._make_state(tmp_path)
        csv_file   = tmp_path / "trades.csv"

        import startup_recovery
        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())

        import trader as tr
        monkeypatch.setattr(tr, "get_candles",
                            lambda sym, i, limit=100: pd.DataFrame())

        state_before = state_file.read_text()

        self._run_cli(["--dry-run"], state_file, csv_file)

        # State unchanged
        assert state_file.read_text() == state_before
        # CSV not created
        assert not csv_file.exists()

    def test_symbols_filter(self, tmp_path, monkeypatch):
        """--symbols LINKUSDT only processes LINKUSDT, not UNIUSDT."""
        state_file = self._make_state(tmp_path, ["LINKUSDT", "UNIUSDT"])
        csv_file   = tmp_path / "trades.csv"

        import startup_recovery
        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())

        import trader as tr
        monkeypatch.setattr(tr, "get_candles",
                            lambda sym, i, limit=100: pd.DataFrame())

        import snapshot
        monkeypatch.setattr(snapshot, "save_snapshot", lambda **kw: None)

        processed = []

        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        recover.STATE_FILE = str(state_file)
        recover.TRADE_LOG  = str(csv_file)
        monkeypatch.setattr(recover, "_log_trade_to_csv",
                            lambda csv_path, action, details, dry_run=False:
                            processed.append(details.get("symbol")) if not dry_run else None)

        recover.main(["--symbols", "LINKUSDT"])

        assert "LINKUSDT" in processed
        assert "UNIUSDT" not in processed

    def test_summary_table_printed(self, tmp_path, monkeypatch, capsys):
        """Summary table is printed to stdout after recovery."""
        state_file = self._make_state(tmp_path, ["LINKUSDT"])
        csv_file   = tmp_path / "trades.csv"

        import startup_recovery
        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())

        import trader as tr
        monkeypatch.setattr(tr, "get_candles",
                            lambda sym, i, limit=100: pd.DataFrame())

        import snapshot
        monkeypatch.setattr(snapshot, "save_snapshot", lambda **kw: None)

        if "recover" in sys.modules:
            del sys.modules["recover"]
        import recover
        recover.STATE_FILE = str(state_file)
        recover.TRADE_LOG  = str(csv_file)

        recover.main([])

        captured = capsys.readouterr()
        assert "LINKUSDT" in captured.out
        assert "sl" in captured.out.lower() or "SL" in captured.out
