"""Tests for regime.py — market-regime fingerprint fields recorded at entry."""
import csv
from types import SimpleNamespace

import pandas as pd
import pytest

import regime


class TestTimeFields:
    def test_asia_session(self):
        # 00:00 UTC = 08:00 PH → ASIA
        out = regime.time_fields("2026-06-01T00:00:00+00:00")
        assert out["snap_session"] == "ASIA"
        assert out["snap_hour_ph"] == 8
        assert out["snap_dow"] == 0  # 2026-06-01 is a Monday

    def test_london_session(self):
        # 08:00 UTC = 16:00 PH → LONDON
        assert regime.time_fields("2026-06-01T08:00:00+00:00")["snap_session"] == "LONDON"

    def test_ny_session_evening(self):
        # 14:00 UTC = 22:00 PH → NY
        assert regime.time_fields("2026-06-01T14:00:00+00:00")["snap_session"] == "NY"

    def test_ny_session_wraps_past_midnight(self):
        # 20:00 UTC = 04:00 PH (next day) → NY (0-5 wrap)
        assert regime.time_fields("2026-06-01T20:00:00+00:00")["snap_session"] == "NY"

    def test_off_session(self):
        # 22:00 UTC = 06:00 PH → OFF (between NY close 05:00 and Asia open 08:00)
        assert regime.time_fields("2026-06-01T22:00:00+00:00")["snap_session"] == "OFF"

    def test_naive_timestamp_treated_as_utc(self):
        out = regime.time_fields("2026-06-01T00:00:00")
        assert out["snap_hour_ph"] == 8

    def test_bad_timestamp_returns_empty(self):
        assert regime.time_fields("not-a-timestamp") == {}


class TestBtcRegime:
    def test_risk_on(self):
        assert regime.btc_regime(2.0) == "RISK_ON"

    def test_risk_off(self):
        assert regime.btc_regime(-2.0) == "RISK_OFF"

    def test_flat(self):
        assert regime.btc_regime(0.5) == "FLAT"

    def test_none_returns_empty(self):
        assert regime.btc_regime(None) == ""

    def test_boundary_inclusive(self):
        assert regime.btc_regime(1.5) == "RISK_ON"
        assert regime.btc_regime(-1.5) == "RISK_OFF"


def _ohlcv(n: int, base: float = 100.0, vol: float = 1000.0) -> pd.DataFrame:
    rows = []
    for i in range(n):
        c = base + i * 0.5
        rows.append({"high": c + 1, "low": c - 1, "close": c, "open": c, "volume": vol})
    return pd.DataFrame(rows)


class TestVolatilityFields:
    def test_full_fields_present(self):
        out = regime.volatility_fields(_ohlcv(60))
        assert "snap_atr_pct" in out
        assert "snap_atr_pctile" in out
        assert "snap_vol_vs_avg" in out
        assert out["snap_atr_pct"] > 0

    def test_vol_vs_avg_detects_spike(self):
        df = _ohlcv(40)
        df.loc[df.index[-1], "volume"] = 5000.0  # last bar 5x the steady 1000
        out = regime.volatility_fields(df)
        assert out["snap_vol_vs_avg"] > 3

    def test_none_returns_empty(self):
        assert regime.volatility_fields(None) == {}

    def test_short_df_returns_empty(self):
        assert regime.volatility_fields(_ohlcv(5)) == {}

    def test_missing_volume_column_still_returns_atr(self):
        df = _ohlcv(40).drop(columns=["volume"])
        out = regime.volatility_fields(df)
        assert "snap_atr_pct" in out
        assert "snap_vol_vs_avg" not in out


class TestSidecarLog:
    """bot.MTFBot._log_regime writes a sidecar row keyed by trade_id, leaving trades.csv alone."""

    def _make_bot(self, monkeypatch, tmp_path, btc_change=2.0):
        import bot, config
        monkeypatch.setattr(config, "TRADE_LOG", str(tmp_path / "trades.csv"))
        # Stub the trader global so no funding API call is attempted.
        monkeypatch.setattr(bot, "tr", SimpleNamespace())
        b = object.__new__(bot.MTFBot)  # bypass heavy __init__
        b.sentiment = SimpleNamespace(btc_change=btc_change)
        return bot, b

    def test_writes_sidecar_row(self, tmp_path, monkeypatch):
        bot, b = self._make_bot(monkeypatch, tmp_path)
        trade = {"trade_id": "abc12345", "symbol": "BTCUSDT", "strategy": "S1", "side": "long"}
        b._log_regime("BTCUSDT", _ohlcv(40), trade)

        sidecar = tmp_path / "trades_regime.csv"
        assert sidecar.exists()
        assert not (tmp_path / "trades.csv").exists()  # trades.csv untouched
        rows = list(csv.DictReader(open(sidecar)))
        assert len(rows) == 1
        r = rows[0]
        assert r["trade_id"] == "abc12345"
        assert r["strategy"] == "S1"
        assert r["snap_btc_regime"] == "RISK_ON"
        assert r["snap_session"] in {"ASIA", "LONDON", "NY", "OFF"}
        assert r["snap_atr_pct"] != ""

    def test_sidecar_path_derivation(self, tmp_path, monkeypatch):
        bot, _ = self._make_bot(monkeypatch, tmp_path)
        assert bot._regime_log_path() == str(tmp_path / "trades_regime.csv")

    def test_appends_without_rewriting_header(self, tmp_path, monkeypatch):
        bot, b = self._make_bot(monkeypatch, tmp_path)
        for tid in ("aaaa1111", "bbbb2222"):
            b._log_regime("BTCUSDT", None, {"trade_id": tid, "symbol": "BTCUSDT",
                                            "strategy": "S5", "side": "short"})
        rows = list(csv.DictReader(open(tmp_path / "trades_regime.csv")))
        assert [r["trade_id"] for r in rows] == ["aaaa1111", "bbbb2222"]
