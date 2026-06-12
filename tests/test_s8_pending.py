"""S8 queue_pending + handle_pending_tick behaviour."""
import time

import pytest

import strategies.s8 as s8


class _Sentiment:
    direction = "BULLISH"


class _Bot:
    def __init__(self):
        self.pending_signals = {}
        self.active_positions = {}
        self.sentiment = _Sentiment()
        self._trade_lock = __import__("threading").Lock()
        self.fired = []

    def _fire_s8(self, symbol, sig, mark, balance):
        self.fired.append((symbol, mark))


def _candidate():
    return {
        "symbol": "ABCUSDT", "s8_trigger": 105.0, "s8_green_low": 99.0,
        "s8_zone_low": 98.0, "s8_zone_high": 100.0,
        "s8_box_top": 99.5, "s8_ma20": 98.5, "s8_fib618": 99.0,
        "s8_rsi": 75.0, "s8_reason": "test", "priority_rank": 1,
        "priority_score": 10.0,
    }


@pytest.fixture
def bot(monkeypatch):
    b = _Bot()
    monkeypatch.setattr("state.save_pending_signals", lambda *a, **k: None)
    monkeypatch.setattr("state.add_scan_log", lambda *a, **k: None)
    return b


def test_queue_pending_payload(bot):
    s8.queue_pending(bot, _candidate())
    sig = bot.pending_signals["ABCUSDT"]
    assert sig["strategy"] == "S8"
    assert sig["side"] == "LONG"
    assert sig["trigger"] == 105.0
    assert sig["s8_green_low"] == 99.0
    assert sig["s8_zone_low"] == 98.0
    assert sig["expires"] > time.time()


def _pending_sig():
    return {
        "strategy": "S8", "side": "LONG", "trigger": 105.0,
        "s8_trigger": 105.0, "s8_green_low": 99.0,
        "s8_zone_low": 98.0, "s8_zone_high": 100.0,
        "snap_daily_rsi": 75.0, "snap_sentiment": "BULLISH",
        "expires": time.time() + 86400,
    }


def test_pending_fires_in_window(bot, monkeypatch):
    bot.pending_signals["ABCUSDT"] = _pending_sig()
    monkeypatch.setattr("state.get_pair_state", lambda s: {"s8_signal": "LONG"})
    monkeypatch.setattr("state.is_pair_paused", lambda s: False)
    monkeypatch.setattr("trader.get_mark_price", lambda s: 105.5)
    import config
    monkeypatch.setattr(config, "MAX_CONCURRENT_TRADES", 5, raising=False)
    s8.handle_pending_tick(bot, "ABCUSDT", bot.pending_signals["ABCUSDT"], 1000.0)
    assert bot.fired == [("ABCUSDT", 105.5)]
    assert "ABCUSDT" not in bot.pending_signals


def test_pending_invalidates_below_zone(bot, monkeypatch):
    bot.pending_signals["ABCUSDT"] = _pending_sig()
    monkeypatch.setattr("state.get_pair_state", lambda s: {"s8_signal": "LONG"})
    monkeypatch.setattr("trader.get_mark_price", lambda s: 97.0)
    s8.handle_pending_tick(bot, "ABCUSDT", bot.pending_signals["ABCUSDT"], 1000.0)
    assert bot.fired == []
    assert "ABCUSDT" not in bot.pending_signals


def test_pending_cancelled_when_signal_gone(bot, monkeypatch):
    bot.pending_signals["ABCUSDT"] = _pending_sig()
    monkeypatch.setattr("state.get_pair_state", lambda s: {"s8_signal": "HOLD"})
    s8.handle_pending_tick(bot, "ABCUSDT", bot.pending_signals["ABCUSDT"], 1000.0)
    assert bot.fired == []
    assert "ABCUSDT" not in bot.pending_signals


def test_pending_waits_below_trigger(bot, monkeypatch):
    bot.pending_signals["ABCUSDT"] = _pending_sig()
    monkeypatch.setattr("state.get_pair_state", lambda s: {"s8_signal": "LONG"})
    monkeypatch.setattr("trader.get_mark_price", lambda s: 102.0)
    s8.handle_pending_tick(bot, "ABCUSDT", bot.pending_signals["ABCUSDT"], 1000.0)
    assert bot.fired == []
    assert "ABCUSDT" in bot.pending_signals


def test_compute_paper_trail_long():
    use_trail, trig, rng, tp, be = s8.compute_paper_trail_long(100.0, 95.0)
    assert use_trail is True
    assert trig == pytest.approx(110.0)
