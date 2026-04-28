# tests/manual/run_test_s7.py
"""
S7 manual test — Post-Pump 1H Darvas Breakdown Short.

Run standalone:  python tests/manual/run_test_s7.py
Run via pytest:  pytest tests/manual/run_test_s7.py -v -s
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.manual._bc_spy import bc_spy
from tests.manual._bot_factory import make_bot
import config_s7
import trader as tr

SYMBOL   = "BTCUSDT"
MARK     = 50_000.0
BOX_LOW  = MARK / (1 - config_s7.S7_ENTRY_BUFFER)   # trigger = box_low*(1-ENTRY_BUFFER) ≈ MARK
BOX_TOP  = BOX_LOW * 1.03                            # 3% above box low
S7_SL    = MARK * (1 + 0.50 / config_s7.S7_LEVERAGE) # computed inside _fire_s7


def _make_sig() -> dict:
    return {
        "strategy":            "S7",
        "side":                "SHORT",
        "trigger":             MARK,
        "s7_sl":               S7_SL,
        "box_low":             BOX_LOW,
        "box_top":             BOX_TOP,
        "snap_rsi":            45.0,
        "snap_rsi_peak":       85.0,
        "snap_spike_body_pct": 65.0,
        "snap_rsi_div":        True,
        "snap_rsi_div_str":    "RSI divergence",
        "snap_box_top":        BOX_TOP,
        "snap_box_low_initial": BOX_LOW,
        "snap_sentiment":      "BEARISH",
        "priority_rank":       1,
        "priority_score":      22.0,
    }


def test_s7_entry_short():
    print(f"\n{'='*60}")
    print(f"S7 — Entry SHORT  (1H Darvas Breakdown)")
    print(f"  config_s7.S7_LEVERAGE            = {config_s7.S7_LEVERAGE}")
    print(f"  config_s7.S7_TRADE_SIZE_PCT       = {config_s7.S7_TRADE_SIZE_PCT} (50% initial = {config_s7.S7_TRADE_SIZE_PCT*0.5*100:.0f}%)")
    print(f"  box_low = {BOX_LOW:.2f}  box_top = {BOX_TOP:.2f}")
    print(f"  trigger = box_low*(1 - {config_s7.S7_ENTRY_BUFFER}) = {MARK:.2f}")
    print(f"  SL = mark*(1 + 0.50/{config_s7.S7_LEVERAGE}) = {S7_SL:.2f}")
    print(f"  config_s7.S7_TRAILING_TRIGGER_PCT = {config_s7.S7_TRAILING_TRIGGER_PCT}  → trail ≈ {MARK*(1-config_s7.S7_TRAILING_TRIGGER_PCT):.1f}")
    print(f"  config_s7.S7_TRAILING_RANGE_PCT   = {config_s7.S7_TRAILING_RANGE_PCT}")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, hold_side="short"):
        b = make_bot()
        b._fire_s7(SYMBOL, _make_sig(), mark=MARK, balance=10_000.0)


def test_s7_scale_in_short():
    print(f"\n{'='*60}")
    print(f"S7 — Scale-in SHORT  (+{config_s7.S7_TRADE_SIZE_PCT*0.5*100:.0f}% of equity)")
    print(f"  in-window: {BOX_LOW*(1-config_s7.S7_MAX_ENTRY_BUFFER):.1f} ≤ mark ≤ {BOX_LOW*(1-config_s7.S7_ENTRY_BUFFER):.1f}")
    print(f"{'='*60}")
    mark_in_window = BOX_LOW * (1 - config_s7.S7_ENTRY_BUFFER * 1.5)
    ap = {
        "side":                    "SHORT",
        "strategy":                "S7",
        "box_high":                S7_SL,
        "box_low":                 MARK,
        "scale_in_pending":        True,
        "scale_in_trade_size_pct": config_s7.S7_TRADE_SIZE_PCT,
        "s7_box_low":              BOX_LOW,
        "qty":                     0.0,
        "trade_id":                "test-trade-s7-001",
    }
    with bc_spy(symbol=SYMBOL, mark_price=mark_in_window, init_qty=0.002, scale_in_qty=0.004, hold_side="short"):
        b = make_bot()
        b.active_positions[SYMBOL] = ap
        b._do_scale_in(SYMBOL, ap)


def test_s7_trailing_refresh():
    print(f"\n{'='*60}")
    print(f"S7 — Trailing refresh  rangeRate={config_s7.S7_TRAILING_RANGE_PCT}")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, scale_in_qty=0.004, hold_side="short"):
        tr.refresh_plan_exits(SYMBOL, "short", new_trail_trigger=MARK * 0.90)


if __name__ == "__main__":
    test_s7_entry_short()
    test_s7_scale_in_short()
    test_s7_trailing_refresh()
    print(f"\n{'='*60}")
    print("S7 — all scenarios complete")
    print(f"{'='*60}")
