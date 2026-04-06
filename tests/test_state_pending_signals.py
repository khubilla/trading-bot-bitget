import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import state as st


@pytest.fixture(autouse=True)
def tmp_state(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "STATE_FILE", str(tmp_path / "state.json"))
    st.reset()


def test_save_and_load_pending_signals():
    signals = {
        "BTCUSDT": {"strategy": "S2", "side": "LONG", "trigger": 50000.0, "s2_bl": 48000.0},
    }
    st.save_pending_signals(signals)
    loaded = st.load_pending_signals()
    assert loaded == signals


def test_load_pending_signals_returns_empty_when_missing():
    loaded = st.load_pending_signals()
    assert loaded == {}


def test_pending_signals_preserved_across_reset():
    signals = {"ETHUSDT": {"strategy": "S3", "side": "LONG", "trigger": 2000.0}}
    st.save_pending_signals(signals)
    st.reset()
    loaded = st.load_pending_signals()
    assert loaded == signals


def test_save_pending_signals_overwrites():
    st.save_pending_signals({"AAVEUSDT": {"strategy": "S4", "trigger": 100.0}})
    st.save_pending_signals({"DOTUSDT": {"strategy": "S2", "trigger": 10.0}})
    loaded = st.load_pending_signals()
    assert "AAVEUSDT" not in loaded
    assert "DOTUSDT" in loaded
