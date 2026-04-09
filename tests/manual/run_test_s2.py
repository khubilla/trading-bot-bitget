# tests/manual/run_test_s2.py
"""
S2 manual test — Daily momentum coil breakout (LONG only).

Run standalone:  python tests/manual/run_test_s2.py
Run via pytest:  pytest tests/manual/run_test_s2.py -v -s
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.manual._bc_spy import bc_spy
from tests.manual._bot_factory import make_bot
import config_s2
import trader as tr

SYMBOL = "BTCUSDT"
MARK   = 50_000.0
BOX_H  = MARK          # trigger == box_high
BOX_L  = MARK * 0.93   # 7% consolidation range


def _make_sig() -> dict:
    return {
        "strategy":           "S2",
        "side":               "LONG",
        "trigger":            BOX_H,
        "s2_bh":              BOX_H,
        "s2_bl":              BOX_L,
        "snap_daily_rsi":     72.5,
        "snap_box_range_pct": round((BOX_H - BOX_L) / BOX_L * 100, 3),
        "snap_sentiment":     "BULLISH",
        "priority_rank":      1,
        "priority_score":     35.0,
    }


def test_s2_entry_long():
    print(f"\n{'='*60}")
    print(f"S2 — Entry LONG  (50% initial size, scale-in queued)")
    print(f"  config_s2.S2_LEVERAGE           = {config_s2.S2_LEVERAGE}")
    print(f"  config_s2.S2_TRADE_SIZE_PCT      = {config_s2.S2_TRADE_SIZE_PCT} (50% initial = {config_s2.S2_TRADE_SIZE_PCT*0.5*100:.0f}%)")
    print(f"  config_s2.S2_STOP_LOSS_PCT       = {config_s2.S2_STOP_LOSS_PCT}  → SL ≈ {MARK*(1-config_s2.S2_STOP_LOSS_PCT):.1f}")
    print(f"  config_s2.S2_TRAILING_TRIGGER_PCT= {config_s2.S2_TRAILING_TRIGGER_PCT}  → trail ≈ {MARK*(1+config_s2.S2_TRAILING_TRIGGER_PCT):.1f}")
    print(f"  config_s2.S2_TRAILING_RANGE_PCT  = {config_s2.S2_TRAILING_RANGE_PCT}")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, hold_side="long"):
        b = make_bot()
        b._fire_s2(SYMBOL, _make_sig(), mark=MARK, balance=10_000.0)


def test_s2_scale_in_long():
    print(f"\n{'='*60}")
    print(f"S2 — Scale-in LONG  (+{config_s2.S2_TRADE_SIZE_PCT*0.5*100:.0f}% of equity)")
    print(f"  in-window: {BOX_H:.1f} ≤ mark ≤ {BOX_H*(1+config_s2.S2_MAX_ENTRY_BUFFER):.1f}")
    print(f"{'='*60}")
    mark_in_window = BOX_H * 1.01   # 1% above box_high — inside S2_MAX_ENTRY_BUFFER
    ap = {
        "side":                    "LONG",
        "strategy":                "S2",
        "box_high":                BOX_H,
        "box_low":                 BOX_L,
        "scale_in_pending":        True,
        "scale_in_trade_size_pct": config_s2.S2_TRADE_SIZE_PCT,
        "qty":                     0.0,
        "trade_id":                "test-trade-s2-001",
    }
    with bc_spy(symbol=SYMBOL, mark_price=mark_in_window, init_qty=0.002, scale_in_qty=0.004, hold_side="long"):
        b = make_bot()
        b.active_positions[SYMBOL] = ap
        b._do_scale_in(SYMBOL, ap)


def test_s2_trailing_refresh():
    print(f"\n{'='*60}")
    print(f"S2 — Trailing refresh  (cancel existing plans, re-place with new qty)")
    print(f"  config_s2.S2_TRAILING_RANGE_PCT = {config_s2.S2_TRAILING_RANGE_PCT}")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, scale_in_qty=0.004, hold_side="long"):
        tr.refresh_plan_exits(SYMBOL, "long", new_trail_trigger=MARK * 1.10)


if __name__ == "__main__":
    test_s2_entry_long()
    test_s2_scale_in_long()
    test_s2_trailing_refresh()
    print(f"\n{'='*60}")
    print("S2 — all scenarios complete")
    print(f"{'='*60}")
