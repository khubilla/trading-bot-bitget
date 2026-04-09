# tests/manual/run_test_s4.py
"""
S4 manual test — RSI divergence spike-reversal (SHORT only).

Run standalone:  python tests/manual/run_test_s4.py
Run via pytest:  pytest tests/manual/run_test_s4.py -v -s
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.manual._bc_spy import bc_spy
from tests.manual._bot_factory import make_bot
import config_s4
import trader as tr

SYMBOL   = "BTCUSDT"
MARK     = 50_000.0
PREV_LOW = MARK / (1 - config_s4.S4_ENTRY_BUFFER)   # entry = prev_low*(1-ENTRY_BUFFER) ≈ MARK
S4_SL    = MARK * (1 + 0.50 / config_s4.S4_LEVERAGE) # computed inside _fire_s4


def _make_sig() -> dict:
    return {
        "strategy":          "S4",
        "side":              "SHORT",
        "trigger":           MARK,
        "s4_sl":             S4_SL,
        "prev_low":          PREV_LOW,
        "snap_rsi":          45.0,
        "snap_rsi_peak":     85.0,
        "snap_spike_body_pct": 65.0,
        "snap_rsi_div":      True,
        "snap_rsi_div_str":  "RSI divergence",
        "snap_sentiment":    "BEARISH",
        "priority_rank":     1,
        "priority_score":    22.0,
    }


def test_s4_entry_short():
    print(f"\n{'='*60}")
    print(f"S4 — Entry SHORT  (RSI divergence reversal)")
    print(f"  config_s4.S4_LEVERAGE            = {config_s4.S4_LEVERAGE}")
    print(f"  config_s4.S4_TRADE_SIZE_PCT       = {config_s4.S4_TRADE_SIZE_PCT} (50% initial = {config_s4.S4_TRADE_SIZE_PCT*0.5*100:.0f}%)")
    print(f"  SL = mark*(1 + 0.50/{config_s4.S4_LEVERAGE}) = {S4_SL:.2f}")
    print(f"  config_s4.S4_TRAILING_TRIGGER_PCT = {config_s4.S4_TRAILING_TRIGGER_PCT}  → trail ≈ {MARK*(1-config_s4.S4_TRAILING_TRIGGER_PCT):.1f}")
    print(f"  config_s4.S4_TRAILING_RANGE_PCT   = {config_s4.S4_TRAILING_RANGE_PCT}")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, hold_side="short"):
        b = make_bot()
        b._fire_s4(SYMBOL, _make_sig(), mark=MARK, balance=10_000.0)


def test_s4_scale_in_short():
    print(f"\n{'='*60}")
    print(f"S4 — Scale-in SHORT  (+{config_s4.S4_TRADE_SIZE_PCT*0.5*100:.0f}% of equity)")
    print(f"  in-window: {PREV_LOW*(1-config_s4.S4_MAX_ENTRY_BUFFER):.1f} ≤ mark ≤ {PREV_LOW*(1-config_s4.S4_ENTRY_BUFFER):.1f}")
    print(f"{'='*60}")
    mark_in_window = PREV_LOW * (1 - config_s4.S4_ENTRY_BUFFER * 1.5)
    ap = {
        "side":                    "SHORT",
        "strategy":                "S4",
        "box_high":                S4_SL,
        "box_low":                 MARK,
        "scale_in_pending":        True,
        "scale_in_trade_size_pct": config_s4.S4_TRADE_SIZE_PCT,
        "s4_prev_low":             PREV_LOW,
        "qty":                     0.0,
        "trade_id":                "test-trade-s4-001",
    }
    with bc_spy(symbol=SYMBOL, mark_price=mark_in_window, init_qty=0.002, scale_in_qty=0.004, hold_side="short"):
        b = make_bot()
        b.active_positions[SYMBOL] = ap
        b._do_scale_in(SYMBOL, ap)


def test_s4_trailing_refresh():
    print(f"\n{'='*60}")
    print(f"S4 — Trailing refresh  rangeRate={config_s4.S4_TRAILING_RANGE_PCT}")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, scale_in_qty=0.004, hold_side="short"):
        tr.refresh_plan_exits(SYMBOL, "short", new_trail_trigger=MARK * 0.90)


if __name__ == "__main__":
    test_s4_entry_short()
    test_s4_scale_in_short()
    test_s4_trailing_refresh()
    print(f"\n{'='*60}")
    print("S4 — all scenarios complete")
    print(f"{'='*60}")
