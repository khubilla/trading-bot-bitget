import importlib

from strategies.s1 import s1_anchor_decision

IV = 180000  # 3m in ms


def test_anchor_box_config_defaults():
    for mod_name in ("config_s1", "config_bybit_s1", "config_binance_s1"):
        mod = importlib.import_module(mod_name)
        assert mod.S1_ANCHOR_BOX is True
        assert mod.S1_BOX_MAX_AGE == 10


def _arm_kwargs(**over):
    base = dict(
        direction="LONG", last_close=100.0, last_ts=IV * 10,
        rsi_val=75.0, rsi_thresh=70.0, gates_ok=True, is_coil=True,
        box_high=101.0, box_low=99.5, buffer_pct=0.005,
        interval_ms=IV, max_age=10,
    )
    base.update(over)
    return base


def test_arm_when_valid_coil_and_inside_box():
    armed, sig = s1_anchor_decision(None, **_arm_kwargs())
    assert sig == "HOLD"
    assert armed is not None
    assert armed["dir"] == "LONG"
    assert armed["box_high"] == 101.0 and armed["box_low"] == 99.5
    assert armed["armed_at_ts"] == IV * 10


def test_no_arm_when_no_coil():
    armed, sig = s1_anchor_decision(None, **_arm_kwargs(is_coil=False))
    assert armed is None and sig == "HOLD"


def test_no_arm_when_already_broken_out():
    # price already above box_high*(1+buffer) → don't arm an extended break
    armed, sig = s1_anchor_decision(None, **_arm_kwargs(last_close=101.6))
    assert armed is None and sig == "HOLD"


def test_no_arm_when_gates_fail():
    armed, sig = s1_anchor_decision(None, **_arm_kwargs(gates_ok=False))
    assert armed is None and sig == "HOLD"


def test_fire_long_on_close_above_anchored_box():
    armed = {"dir": "LONG", "box_high": 101.0, "box_low": 99.5,
             "rsi_thresh": 70.0, "armed_at_ts": IV * 10}
    new, sig = s1_anchor_decision(
        armed, **_arm_kwargs(last_close=101.6, last_ts=IV * 12, is_coil=False))
    assert sig == "LONG"
    assert new is None  # disarmed on fire


def test_no_fire_when_close_inside_buffer():
    armed = {"dir": "LONG", "box_high": 101.0, "box_low": 99.5,
             "rsi_thresh": 70.0, "armed_at_ts": IV * 10}
    new, sig = s1_anchor_decision(
        armed, **_arm_kwargs(last_close=101.3, last_ts=IV * 12, is_coil=False))
    assert sig == "HOLD"
    assert new is armed  # still waiting, box unchanged


def test_disarm_when_rsi_leaves_zone():
    armed = {"dir": "LONG", "box_high": 101.0, "box_low": 99.5,
             "rsi_thresh": 70.0, "armed_at_ts": IV * 10}
    new, sig = s1_anchor_decision(
        armed, **_arm_kwargs(rsi_val=68.0, last_ts=IV * 12, is_coil=False))
    assert new is None and sig == "HOLD"


def test_disarm_on_wrong_way_close():
    armed = {"dir": "LONG", "box_high": 101.0, "box_low": 99.5,
             "rsi_thresh": 70.0, "armed_at_ts": IV * 10}
    new, sig = s1_anchor_decision(
        armed, **_arm_kwargs(last_close=99.0, last_ts=IV * 12, is_coil=False))
    assert new is None and sig == "HOLD"


def test_disarm_on_age_expiry():
    armed = {"dir": "LONG", "box_high": 101.0, "box_low": 99.5,
             "rsi_thresh": 70.0, "armed_at_ts": IV * 10}
    # 11 candles later (> max_age 10), still inside box
    new, sig = s1_anchor_decision(
        armed, **_arm_kwargs(last_close=100.5, last_ts=IV * 21, is_coil=False))
    assert new is None and sig == "HOLD"


def test_disarm_when_gates_flip():
    armed = {"dir": "LONG", "box_high": 101.0, "box_low": 99.5,
             "rsi_thresh": 70.0, "armed_at_ts": IV * 10}
    new, sig = s1_anchor_decision(
        armed, **_arm_kwargs(gates_ok=False, last_ts=IV * 12, is_coil=False))
    assert new is None and sig == "HOLD"


def test_fire_short_on_close_below_anchored_box():
    armed = {"dir": "SHORT", "box_high": 101.0, "box_low": 99.5,
             "rsi_thresh": 30.0, "armed_at_ts": IV * 10}
    new, sig = s1_anchor_decision(
        armed, direction="SHORT", last_close=99.0, last_ts=IV * 12,
        rsi_val=25.0, rsi_thresh=30.0, gates_ok=True, is_coil=False,
        box_high=101.0, box_low=99.5, buffer_pct=0.005, interval_ms=IV, max_age=10)
    assert sig == "SHORT" and new is None
