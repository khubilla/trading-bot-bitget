# tests/manual/run_test_s1.py
"""
S1 manual test — RSI/ADX momentum strategy.

Run standalone:  python tests/manual/run_test_s1.py
Run via pytest:  pytest tests/manual/run_test_s1.py -v -s
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import pandas as pd
from tests.manual._bc_spy import bc_spy
from tests.manual._bot_factory import make_bot
import config_s1

SYMBOL = "BTCUSDT"
MARK   = 50_000.0


def _make_candidate(sig: str, box_low_near: bool = True) -> dict:
    """Build an S1 candidate dict. box_low_near controls which SL path fires."""
    bh = MARK * 1.02
    # Near: box_low within STOP_LOSS_PCT → SL uses box_low * (1 - S1_SL_BUFFER_PCT)
    # Far:  box_low far below entry → SL uses mark * (1 - STOP_LOSS_PCT)
    bl = MARK * 0.995 if box_low_near else MARK * 0.90
    return {
        "symbol":        SYMBOL,
        "sig":           sig,
        "ltf_df":        pd.DataFrame(),
        "sr_pct":        config_s1.S1_MIN_SR_CLEARANCE * 100 + 1.0,  # pass gate
        "s1_bh":         bh,
        "s1_bl":         bl,
        "rsi_val":       72.0,
        "adx_val":       28.0,
        "htf_bull":      sig == "LONG",
        "htf_bear":      sig == "SHORT",
        "is_coil":       False,
        "priority_rank": 1,
    }


def test_s1_entry_long():
    print(f"\n{'='*60}")
    print(f"S1 — Entry LONG  (box_low near, SL from box_low)")
    print(f"  config_s1.LEVERAGE          = {config_s1.LEVERAGE}")
    print(f"  config_s1.TRADE_SIZE_PCT    = {config_s1.TRADE_SIZE_PCT}")
    print(f"  config_s1.S1_SL_BUFFER_PCT  = {config_s1.S1_SL_BUFFER_PCT}")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, hold_side="long"):
        b = make_bot()
        b._execute_s1(_make_candidate("LONG", box_low_near=True), balance=10_000.0)


def test_s1_entry_short():
    print(f"\n{'='*60}")
    print(f"S1 — Entry SHORT  (box_high near, SL from box_high)")
    print(f"  config_s1.LEVERAGE          = {config_s1.LEVERAGE}")
    print(f"  config_s1.TRADE_SIZE_PCT    = {config_s1.TRADE_SIZE_PCT}")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, hold_side="short"):
        b = make_bot()
        b._execute_s1(_make_candidate("SHORT", box_low_near=True), balance=10_000.0)


def test_s1_sl_box_low_far():
    print(f"\n{'='*60}")
    print(f"S1 — Entry LONG  (box_low far → SL floored at STOP_LOSS_PCT)")
    print(f"  config_s1.STOP_LOSS_PCT = {config_s1.STOP_LOSS_PCT}")
    print(f"  Expected SL ≈ {MARK * (1 - config_s1.STOP_LOSS_PCT):.1f}")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, hold_side="long"):
        b = make_bot()
        b._execute_s1(_make_candidate("LONG", box_low_near=False), balance=10_000.0)


def test_s1_trailing_long():
    """Calls _place_s1_exits directly to show moving_plan payload."""
    import trader as tr
    print(f"\n{'='*60}")
    print(f"S1 — Trailing stop (moving_plan)")
    print(f"  config_s1.S1_TRAIL_RANGE_PCT = {config_s1.S1_TRAIL_RANGE_PCT}")
    print(f"  config_s1.TAKE_PROFIT_PCT    = {config_s1.TAKE_PROFIT_PCT}")
    trail_trig = round(MARK * (1 + config_s1.TAKE_PROFIT_PCT), 1)
    print(f"  trail trigger ≈ {trail_trig}")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, hold_side="long"):
        tr._place_s1_exits(
            SYMBOL, "long", "0.002",
            round(MARK * 0.97, 1),   # sl_trig
            round(MARK * 0.965, 1),  # sl_exec
            trail_trig,
            config_s1.S1_TRAIL_RANGE_PCT,
        )


if __name__ == "__main__":
    test_s1_entry_long()
    test_s1_entry_short()
    test_s1_sl_box_low_far()
    test_s1_trailing_long()
    print(f"\n{'='*60}")
    print("S1 — all scenarios complete")
    print(f"{'='*60}")
