"""
tests/test_startup_recovery.py — Tests for startup_recovery helpers and Bot._startup_recovery().
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pandas as pd
import pytest
from unittest.mock import patch

import startup_recovery as sr


class TestFetchCandlesAt:
    def test_returns_dataframe_with_correct_columns(self):
        """fetch_candles_at returns a sorted DataFrame when API returns rows."""
        fake_rows = [
            ["1744000800000", "9.3", "9.4", "9.2", "9.35", "100", "930"],
            ["1744000200000", "9.1", "9.2", "9.0", "9.15", "80",  "731"],
        ]
        fake_resp = {"data": fake_rows}

        with patch("startup_recovery.bc.get_public", return_value=fake_resp) as mock_get:
            df = sr.fetch_candles_at("LINKUSDT", "15m", limit=50, end_ms=1744001000000)

        assert not df.empty
        assert list(df.columns[:6]) == ["ts", "open", "high", "low", "close", "vol"]
        # sorted by ts ascending
        assert df.iloc[0]["ts"] < df.iloc[1]["ts"]
        # endTime param was passed
        call_params = mock_get.call_args[1]["params"]
        assert call_params["endTime"] == "1744001000000"
        assert call_params["limit"] == "50"

    def test_returns_empty_on_empty_response(self):
        """fetch_candles_at returns empty DataFrame when API returns no data."""
        with patch("startup_recovery.bc.get_public", return_value={"data": []}):
            df = sr.fetch_candles_at("LINKUSDT", "15m", limit=50, end_ms=1744001000000)
        assert df.empty

    def test_returns_empty_on_api_exception(self):
        """fetch_candles_at returns empty DataFrame (not exception) on API error."""
        with patch("startup_recovery.bc.get_public", side_effect=RuntimeError("API down")):
            df = sr.fetch_candles_at("LINKUSDT", "15m", limit=50, end_ms=1744001000000)
        assert df.empty


class TestEstimateSlTp:
    def test_short_sl_above_entry(self):
        """SHORT: SL is 5% above entry."""
        sl, tp, ob_low, ob_high = sr.estimate_sl_tp(10.0, "SHORT")
        assert sl == pytest.approx(10.5, rel=1e-6)

    def test_short_tp_below_entry(self):
        """SHORT: TP is 10% below entry."""
        sl, tp, ob_low, ob_high = sr.estimate_sl_tp(10.0, "SHORT")
        assert tp == pytest.approx(9.0, rel=1e-6)

    def test_short_ob_band(self):
        """SHORT: ob_high == entry, ob_low == entry * 0.99."""
        sl, tp, ob_low, ob_high = sr.estimate_sl_tp(10.0, "SHORT")
        assert ob_high == pytest.approx(10.0, rel=1e-6)
        assert ob_low  == pytest.approx(9.9,  rel=1e-6)

    def test_long_sl_below_entry(self):
        """LONG: SL is 5% below entry."""
        sl, tp, ob_low, ob_high = sr.estimate_sl_tp(10.0, "LONG")
        assert sl == pytest.approx(9.5, rel=1e-6)

    def test_long_tp_above_entry(self):
        """LONG: TP is 10% above entry."""
        sl, tp, ob_low, ob_high = sr.estimate_sl_tp(10.0, "LONG")
        assert tp == pytest.approx(11.0, rel=1e-6)

    def test_long_ob_band(self):
        """LONG: ob_low == entry, ob_high == entry * 1.01."""
        sl, tp, ob_low, ob_high = sr.estimate_sl_tp(10.0, "LONG")
        assert ob_low  == pytest.approx(10.0,  rel=1e-6)
        assert ob_high == pytest.approx(10.1,  rel=1e-6)


class TestAttemptS5Recovery:
    """Tests for attempt_s5_recovery — mocks evaluate_s5 to avoid exchange calls."""

    def _make_df(self):
        import numpy as np
        import time
        n = 60
        now_ms = int(time.time() * 1000)
        ts = [now_ms - i * 900_000 for i in range(n, 0, -1)]
        c = np.linspace(9.0, 10.0, n)
        return pd.DataFrame({
            "ts": ts, "open": c - 0.05, "high": c + 0.1,
            "low": c - 0.1, "close": c, "vol": [1000.0] * n,
        })

    def test_returns_values_when_evaluate_s5_finds_signal(self):
        """Returns (sl, tp, ob_low, ob_high) when evaluate_s5 returns PENDING_SHORT."""
        df = self._make_df()
        mock_result = ("PENDING_SHORT", 9.5, 9.9, 8.5, 9.4, 9.5, "OB found")

        with patch("startup_recovery.evaluate_s5", return_value=mock_result):
            result = sr.attempt_s5_recovery("LINKUSDT", df, df, df, "SHORT")

        assert result is not None
        sl, tp, ob_low, ob_high = result
        assert sl      == pytest.approx(9.9, rel=1e-6)
        assert tp      == pytest.approx(8.5, rel=1e-6)
        assert ob_low  == pytest.approx(9.4, rel=1e-6)
        assert ob_high == pytest.approx(9.5, rel=1e-6)

    def test_returns_none_when_evaluate_s5_returns_hold(self):
        """Returns None when evaluate_s5 returns HOLD (no usable signal)."""
        df = self._make_df()
        mock_result = ("HOLD", 0.0, 0.0, 0.0, 0.0, 0.0, "Not enough candles")

        with patch("startup_recovery.evaluate_s5", return_value=mock_result):
            result = sr.attempt_s5_recovery("LINKUSDT", df, df, df, "SHORT")

        assert result is None

    def test_returns_none_when_evaluate_s5_raises(self):
        """Returns None (not exception) when evaluate_s5 raises."""
        df = self._make_df()

        with patch("startup_recovery.evaluate_s5", side_effect=RuntimeError("crash")):
            result = sr.attempt_s5_recovery("LINKUSDT", df, df, df, "SHORT")

        assert result is None

    def test_long_side_accepted(self):
        """Returns values for LONG side when evaluate_s5 returns PENDING_LONG."""
        df = self._make_df()
        mock_result = ("PENDING_LONG", 9.5, 9.1, 10.5, 9.4, 9.6, "OB found")

        with patch("startup_recovery.evaluate_s5", return_value=mock_result):
            result = sr.attempt_s5_recovery("LINKUSDT", df, df, df, "LONG")

        assert result is not None


import threading
import bot
import state as st


def _make_bot(monkeypatch) -> bot.MTFBot:
    """Return a minimal MTFBot bypassing __init__."""
    b = object.__new__(bot.MTFBot)
    b.pending_signals = {}
    b.active_positions = {}
    b._trade_lock = threading.Lock()
    b.running = True
    b.sentiment = type("S", (), {"direction": "BEARISH"})()
    monkeypatch.setattr(bot.st, "add_scan_log", lambda *a, **kw: None)
    monkeypatch.setattr(bot.st, "save_pending_signals", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "PAPER_MODE", False)
    return b


def _make_sig(order_id="ORD001", side="SHORT"):
    import time
    return {
        "strategy": "S5", "side": side,
        "trigger":  9.311, "sl": 9.777, "tp": 8.566,
        "ob_low":   9.22,  "ob_high": 9.311,
        "qty_str":  "14.0", "rr": 2.5,
        "sentiment": "BEARISH",
        "expires":  time.time() + 7200,
        "order_id": order_id,
    }


SYM = "LINKUSDT"


class TestStartupRecoveryHappyPath:
    def test_calls_handle_limit_filled_when_order_filled(self, monkeypatch):
        """Happy path: pending signal + filled order → _handle_limit_filled called."""
        b = _make_bot(monkeypatch)
        sig = _make_sig()
        b.pending_signals[SYM] = sig

        # No CSV row for this symbol
        monkeypatch.setattr(bot, "_get_open_csv_row", lambda path, sym: None)
        monkeypatch.setattr(
            bot.tr, "get_order_fill",
            lambda sym, oid: {"status": "filled", "fill_price": 9.31},
        )
        monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)

        handled = []
        monkeypatch.setattr(
            b, "_handle_limit_filled",
            lambda sym, s, fp, bal: handled.append((sym, fp, bal)),
        )

        existing = {SYM: {"side": "SHORT", "entry_price": 9.311, "qty": 14.0,
                          "margin": 13.05, "leverage": 10,
                          "unrealised_pnl": 1.5, "mark_price": 9.0}}
        b._startup_recovery(existing)

        assert len(handled) == 1
        assert handled[0] == (SYM, 9.31, 1000.0)
        assert SYM not in b.pending_signals

    def test_skips_when_order_still_live(self, monkeypatch):
        """Happy path: order still live → _handle_limit_filled NOT called, signal kept."""
        b = _make_bot(monkeypatch)
        b.pending_signals[SYM] = _make_sig()

        monkeypatch.setattr(bot, "_get_open_csv_row", lambda path, sym: None)
        monkeypatch.setattr(
            bot.tr, "get_order_fill",
            lambda sym, oid: {"status": "live", "fill_price": 0.0},
        )
        monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)

        handled = []
        monkeypatch.setattr(
            b, "_handle_limit_filled",
            lambda sym, s, fp, bal: handled.append(sym),
        )

        existing = {SYM: {"side": "SHORT", "entry_price": 9.311, "qty": 14.0,
                          "margin": 13.05, "leverage": 10,
                          "unrealised_pnl": 1.5, "mark_price": 9.0}}
        b._startup_recovery(existing)

        assert len(handled) == 0
        assert SYM in b.pending_signals

    def test_skips_symbol_with_existing_csv_row(self, monkeypatch):
        """If CSV already has an open row, symbol is skipped entirely."""
        b = _make_bot(monkeypatch)
        b.pending_signals[SYM] = _make_sig()

        # CSV row exists → skip
        monkeypatch.setattr(bot, "_get_open_csv_row", lambda path, sym: {"qty": "14"})
        monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)

        handled = []
        monkeypatch.setattr(
            b, "_handle_limit_filled",
            lambda sym, s, fp, bal: handled.append(sym),
        )

        existing = {SYM: {"side": "SHORT", "entry_price": 9.311, "qty": 14.0,
                          "margin": 13.05, "leverage": 10,
                          "unrealised_pnl": 1.5, "mark_price": 9.0}}
        b._startup_recovery(existing)

        assert len(handled) == 0


class TestStartupRecoverySadPath:
    def _existing(self, entry=9.311, side="SHORT"):
        return {SYM: {
            "side": side, "entry_price": entry,
            "qty": 14.0, "margin": 13.05, "leverage": 10,
            "unrealised_pnl": 1.5, "mark_price": 9.0,
        }}

    def test_sad_path_patches_state_and_logs_csv(self, monkeypatch, tmp_path):
        """No pending signal → state patched with tpsl_set=False; CSV row appended."""
        import csv, json

        b = _make_bot(monkeypatch)
        # No pending signal for SYM

        monkeypatch.setattr(bot, "_get_open_csv_row", lambda path, sym: None)
        monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)
        monkeypatch.setattr(bot.tr, "get_candles", lambda sym, i, limit=100: pd.DataFrame())

        import startup_recovery
        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())

        # Fake state.json with the UNKNOWN position
        state_data = {
            "open_trades": [{
                "symbol": SYM, "side": "SHORT", "qty": 14.0,
                "entry": 9.311, "sl": "?", "tp": "?",
                "strategy": "UNKNOWN", "trade_id": "",
                "opened_at": "2026-04-08T09:05:59+00:00",
                "margin": 13.05, "leverage": 10,
                "unrealised_pnl": 1.5, "mark_price": 9.0,
            }],
            "pending_signals": {}, "position_memory": {},
        }
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(state_data))
        monkeypatch.setattr(bot.st, "_read",
                            lambda: json.loads(state_file.read_text()))
        monkeypatch.setattr(bot.st, "_write",
                            lambda s: state_file.write_text(json.dumps(s)))

        logged = []
        monkeypatch.setattr(bot, "_log_trade",
                            lambda action, details: logged.append((action, details)))
        monkeypatch.setattr(bot, "_rebuild_stats_from_csv", lambda *a: None)
        monkeypatch.setattr(bot.snapshot, "save_snapshot", lambda **kw: None)
        monkeypatch.setattr(bot.st, "get_open_trade", lambda sym: state_data["open_trades"][0])

        b.active_positions[SYM] = {
            "side": "SHORT", "strategy": "UNKNOWN", "sl": "?",
            "box_high": 0, "box_low": 0, "trade_id": "",
            "opened_at": "2026-04-08T09:05:59+00:00",
        }

        b._startup_recovery(self._existing())

        # CSV row was appended
        assert len(logged) == 1
        action, details = logged[0]
        assert action == "UNKNOWN_SHORT"
        assert details["tpsl_set"] is False
        assert float(details["sl"]) > 9.311  # SL above entry for SHORT

        # trade_id was generated
        assert details["trade_id"] != ""

    def test_sad_path_skips_snapshot_on_empty_candles(self, monkeypatch, tmp_path):
        """Empty candle response → snapshot.save_snapshot NOT called; no crash."""
        import json
        b = _make_bot(monkeypatch)

        monkeypatch.setattr(bot, "_get_open_csv_row", lambda path, sym: None)
        monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)
        monkeypatch.setattr(bot.tr, "get_candles", lambda sym, i, limit=100: pd.DataFrame())

        import startup_recovery
        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())

        state_data = {
            "open_trades": [{
                "symbol": SYM, "side": "SHORT", "qty": 14.0,
                "entry": 9.311, "sl": "?", "tp": "?",
                "strategy": "UNKNOWN", "trade_id": "",
                "opened_at": "2026-04-08T09:05:59+00:00",
                "margin": 13.05, "leverage": 10,
                "unrealised_pnl": 1.5, "mark_price": 9.0,
            }],
            "pending_signals": {}, "position_memory": {},
        }
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(state_data))
        monkeypatch.setattr(bot.st, "_read",
                            lambda: json.loads(state_file.read_text()))
        monkeypatch.setattr(bot.st, "_write",
                            lambda s: state_file.write_text(json.dumps(s)))
        monkeypatch.setattr(bot.st, "get_open_trade", lambda sym: state_data["open_trades"][0])
        monkeypatch.setattr(bot, "_log_trade", lambda *a, **kw: None)
        monkeypatch.setattr(bot, "_rebuild_stats_from_csv", lambda *a: None)

        snap_calls = []
        monkeypatch.setattr(bot.snapshot, "save_snapshot",
                            lambda **kw: snap_calls.append(kw))

        b.active_positions[SYM] = {
            "side": "SHORT", "strategy": "UNKNOWN",
            "sl": "?", "box_high": 0, "box_low": 0, "trade_id": "",
            "opened_at": "2026-04-08T09:05:59+00:00",
        }

        b._startup_recovery(self._existing())  # should not raise

        assert snap_calls == [], "snapshot.save_snapshot must NOT be called with empty candles"

    def test_error_in_sad_path_does_not_crash(self, monkeypatch):
        """Exception during sad path recovery is caught; _startup_recovery returns normally."""
        b = _make_bot(monkeypatch)
        monkeypatch.setattr(bot, "_get_open_csv_row", lambda path, sym: None)
        monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)
        # st.get_open_trade raises — simulates unexpected state
        monkeypatch.setattr(bot.st, "get_open_trade",
                            lambda sym: (_ for _ in ()).throw(RuntimeError("boom")))

        import startup_recovery
        monkeypatch.setattr(startup_recovery, "fetch_candles_at",
                            lambda *a, **kw: pd.DataFrame())
        monkeypatch.setattr(bot.tr, "get_candles", lambda sym, i, limit=100: pd.DataFrame())
        monkeypatch.setattr(bot, "_rebuild_stats_from_csv", lambda *a: None)

        b.active_positions[SYM] = {
            "side": "SHORT", "strategy": "UNKNOWN",
            "sl": "?", "box_high": 0, "box_low": 0, "trade_id": "",
        }

        # Should complete without raising
        b._startup_recovery({SYM: {"side": "SHORT", "entry_price": 9.311,
                                    "qty": 14.0, "margin": 13.05, "leverage": 10,
                                    "unrealised_pnl": 1.5, "mark_price": 9.0}})


class TestStartupRecoveryPass2:
    def test_logs_open_and_close_for_filled_and_closed_signal(self, monkeypatch):
        """Pass 2: signal filled + position not in active_positions → open+close CSV rows."""
        b = _make_bot(monkeypatch)
        sig = _make_sig(order_id="ORD123", side="SHORT")
        b.pending_signals[SYM] = sig
        # SYM is NOT in active_positions (position already closed)

        monkeypatch.setattr(bot, "_get_open_csv_row", lambda path, sym: None)
        monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)
        monkeypatch.setattr(
            bot.tr, "get_order_fill",
            lambda sym, oid: {"status": "filled", "fill_price": 9.31},
        )
        monkeypatch.setattr(
            bot.tr, "get_history_position",
            lambda sym, **kw: {"pnl": 2.5, "exit_price": 8.5, "close_time": "2026-04-08T12:00:00"},
        )
        monkeypatch.setattr(bot, "_rebuild_stats_from_csv", lambda *a: None)

        logged = []
        monkeypatch.setattr(bot, "_log_trade",
                            lambda action, details: logged.append((action, details)))

        # existing has NO SYM (it's already closed on exchange)
        b._startup_recovery({})

        actions = [a for a, _ in logged]
        assert "S5_SHORT" in actions, "open row must be logged"
        assert "S5_CLOSE" in actions, "close row must be logged"
        assert SYM not in b.pending_signals

    def test_skips_non_s5_signals_in_pass2(self, monkeypatch):
        """Pass 2 skips signals without order_id (non-S5) — no get_order_fill called."""
        b = _make_bot(monkeypatch)
        b.pending_signals[SYM] = {
            "strategy": "S3", "side": "LONG", "trigger": 9.0,
            "sl": 8.5, "expires": 9999999999,
            # No order_id
        }

        monkeypatch.setattr(bot, "_get_open_csv_row", lambda path, sym: None)
        monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)
        monkeypatch.setattr(bot, "_rebuild_stats_from_csv", lambda *a: None)

        fill_calls = []
        monkeypatch.setattr(
            bot.tr, "get_order_fill",
            lambda sym, oid: fill_calls.append(oid) or {"status": "live", "fill_price": 0.0},
        )

        b._startup_recovery({})

        assert fill_calls == [], "get_order_fill must NOT be called for non-S5 signals"
        assert SYM in b.pending_signals  # signal untouched

    def test_skips_paper_order_id_in_pass2(self, monkeypatch):
        """Pass 2 skips signals with order_id='PAPER'."""
        b = _make_bot(monkeypatch)
        b.pending_signals[SYM] = _make_sig(order_id="PAPER")

        monkeypatch.setattr(bot, "_get_open_csv_row", lambda path, sym: None)
        monkeypatch.setattr(bot.tr, "get_usdt_balance", lambda: 1000.0)
        monkeypatch.setattr(bot, "_rebuild_stats_from_csv", lambda *a: None)

        fill_calls = []
        monkeypatch.setattr(
            bot.tr, "get_order_fill",
            lambda sym, oid: fill_calls.append(oid) or {"status": "live", "fill_price": 0.0},
        )

        b._startup_recovery({})

        assert fill_calls == []


class TestStartupRecoveryIntegration:
    def test_exception_in_startup_recovery_does_not_crash(self, monkeypatch):
        """Exception inside _startup_recovery is caught; caller completes normally."""
        b = _make_bot(monkeypatch)
        monkeypatch.setattr(bot.tr, "get_usdt_balance",
                            lambda: (_ for _ in ()).throw(RuntimeError("balance API down")))

        # Should not raise
        try:
            b._startup_recovery({"AAVEUSDT": {}})
        except Exception as exc:
            pytest.fail(f"_startup_recovery raised unexpectedly: {exc}")
