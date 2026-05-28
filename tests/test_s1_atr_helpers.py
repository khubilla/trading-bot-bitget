"""Tests for compute_s1_sl_atr and compute_s1_tp_atr (IG path)."""
import pytest
from strategies.s1 import compute_s1_sl_atr, compute_s1_tp_atr


def _cfg(sl_mult=1.5, tp_mult=3.0, sl_buf=0.001):
    return {"s1_sl_atr_mult": sl_mult, "s1_tp_atr_mult": tp_mult, "s1_sl_buffer_pct": sl_buf}


def test_long_sl_uses_atr_cap_when_box_low_too_far():
    """LONG: when box_low is well below the ATR-derived floor, SL = entry - atr_mult*ATR (tighter)."""
    entry, box_low, atr = 100.0, 80.0, 2.0
    sl = compute_s1_sl_atr("LONG", entry, box_high=0.0, box_low=box_low, atr_value=atr, cfg=_cfg())
    # ATR cap = 100 - 1.5*2 = 97.0; box floor = 80 * 0.999 = 79.92 → max(97.0, 79.92) = 97.0
    assert sl == pytest.approx(97.0)


def test_long_sl_uses_structural_floor_when_box_close():
    """LONG: when box_low is tight, the structural floor (box-buffer) is used."""
    entry, box_low, atr = 100.0, 99.0, 5.0   # ATR cap = 92.5; box floor = 99 * 0.999 = 98.901
    sl = compute_s1_sl_atr("LONG", entry, box_high=0.0, box_low=box_low, atr_value=atr, cfg=_cfg())
    assert sl == pytest.approx(98.901)


def test_short_sl_uses_atr_cap_when_box_high_too_far():
    """SHORT: ATR-derived ceiling is used when box_high is far above."""
    entry, box_high, atr = 100.0, 120.0, 2.0
    sl = compute_s1_sl_atr("SHORT", entry, box_high=box_high, box_low=0.0, atr_value=atr, cfg=_cfg())
    # ATR ceil = 100 + 1.5*2 = 103.0; box ceil = 120 * 1.001 = 120.12 → min(103.0, 120.12) = 103.0
    assert sl == pytest.approx(103.0)


def test_short_sl_uses_structural_ceiling_when_box_close():
    entry, box_high, atr = 100.0, 101.0, 5.0  # ATR ceil = 107.5; box ceil = 101 * 1.001 = 101.101
    sl = compute_s1_sl_atr("SHORT", entry, box_high=box_high, box_low=0.0, atr_value=atr, cfg=_cfg())
    assert sl == pytest.approx(101.101)


def test_long_tp_at_entry_plus_atr_mult():
    tp = compute_s1_tp_atr("LONG", entry=100.0, atr_value=2.0, cfg=_cfg())
    assert tp == pytest.approx(106.0)   # 100 + 3.0*2


def test_short_tp_at_entry_minus_atr_mult():
    tp = compute_s1_tp_atr("SHORT", entry=100.0, atr_value=2.0, cfg=_cfg())
    assert tp == pytest.approx(94.0)    # 100 - 3.0*2
