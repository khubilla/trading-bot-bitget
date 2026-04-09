# tests/manual/run_test_s5.py
"""
S5 manual test — SMC order block entry.

Run standalone:  python tests/manual/run_test_s5.py
Run via pytest:  pytest tests/manual/run_test_s5.py -v -s
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.manual._bc_spy import bc_spy
from tests.manual._bot_factory import make_bot
import config_s5
import trader as tr

SYMBOL    = "BTCUSDT"
MARK      = 50_000.0
S5_SL     = MARK * 0.96     # OB-based structural SL (4% below entry)
S5_TP     = MARK * 1.08     # hard TP (2:1 R:R — SL=4%, TP=8%)
S5_OB_LOW = MARK * 0.97
S5_OB_HI  = MARK * 0.98


def _make_sig(side: str) -> dict:
    sl = S5_SL if side == "LONG" else MARK * 1.04
    tp = S5_TP if side == "LONG" else MARK * 0.92
    return {
        "strategy":          "S5",
        "side":              side,
        "trigger":           MARK,
        "sl":                sl,
        "tp":                tp,
        "ob_low":            S5_OB_LOW,
        "ob_high":           S5_OB_HI,
        "sentiment":         "BULLISH" if side == "LONG" else "BEARISH",
        "rr":                2.0,
        "sr_clearance_pct":  12.0,
        "priority_rank":     1,
    }


def test_s5_entry_long():
    print(f"\n{'='*60}")
    print(f"S5 — Entry LONG  (OB zone, 1:1 R:R partial TP + trailing)")
    print(f"  config_s5.S5_LEVERAGE      = {config_s5.S5_LEVERAGE}")
    print(f"  config_s5.S5_TRADE_SIZE_PCT= {config_s5.S5_TRADE_SIZE_PCT}")
    print(f"  SL={S5_SL:.1f}  hard_TP={S5_TP:.1f}")
    print(f"  partial TP at 1:1 R:R ≈ {MARK + (MARK - S5_SL):.1f}")
    print(f"  config_s5.S5_TRAIL_RANGE_PCT= {config_s5.S5_TRAIL_RANGE_PCT}")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, hold_side="long"):
        b = make_bot()
        b._fire_pending(SYMBOL, _make_sig("LONG"), mark_now=MARK, balance=10_000.0)


def test_s5_entry_short():
    print(f"\n{'='*60}")
    print(f"S5 — Entry SHORT  (OB zone, 1:1 R:R partial TP + trailing)")
    print(f"  SL={MARK*1.04:.1f}  hard_TP={MARK*0.92:.1f}")
    print(f"  partial TP at 1:1 R:R ≈ {MARK - (MARK*1.04 - MARK):.1f}")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, hold_side="short"):
        b = make_bot()
        b._fire_pending(SYMBOL, _make_sig("SHORT"), mark_now=MARK, balance=10_000.0)


def test_s5_trailing_refresh():
    print(f"\n{'='*60}")
    print(f"S5 — Partial TP refresh  rangeRate={config_s5.S5_TRAIL_RANGE_PCT}")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, scale_in_qty=0.002, hold_side="long"):
        tr.refresh_plan_exits(SYMBOL, "long", new_trail_trigger=MARK * 1.04)


if __name__ == "__main__":
    test_s5_entry_long()
    test_s5_entry_short()
    test_s5_trailing_refresh()
    print(f"\n{'='*60}")
    print("S5 — all scenarios complete")
    print(f"{'='*60}")
