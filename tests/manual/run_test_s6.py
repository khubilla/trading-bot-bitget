# tests/manual/run_test_s6.py
"""
S6 manual test — V-formation liquidity sweep (SHORT only).

Run standalone:  python tests/manual/run_test_s6.py
Run via pytest:  pytest tests/manual/run_test_s6.py -v -s
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.manual._bc_spy import bc_spy
from tests.manual._bot_factory import make_bot
import config_s6
import trader as tr

SYMBOL     = "BTCUSDT"
MARK       = 50_000.0
PEAK_LEVEL = MARK * 1.50    # peak well above current price (fakeout resolved)
S6_SL      = MARK * (1 + config_s6.S6_SL_PCT / config_s6.S6_LEVERAGE)


def _make_sig() -> dict:
    return {
        "strategy":              "S6",
        "side":                  "SHORT",
        "peak_level":            PEAK_LEVEL,
        "sl":                    S6_SL,
        "drop_pct":              0.35,
        "rsi_at_peak":           78.0,
        "fakeout_seen":          True,
        "snap_s6_peak":          round(PEAK_LEVEL, 8),
        "snap_s6_drop_pct":      35.0,
        "snap_s6_rsi_at_peak":   78.0,
        "snap_sentiment":        "BEARISH",
        "priority_rank":         1,
        "priority_score":        18.0,
    }


def test_s6_entry_short():
    print(f"\n{'='*60}")
    print(f"S6 — Entry SHORT  (V-formation fakeout confirmed)")
    print(f"  config_s6.S6_LEVERAGE            = {config_s6.S6_LEVERAGE}")
    print(f"  config_s6.S6_TRADE_SIZE_PCT       = {config_s6.S6_TRADE_SIZE_PCT} (50% initial = {config_s6.S6_TRADE_SIZE_PCT*0.5*100:.0f}%)")
    print(f"  config_s6.S6_SL_PCT              = {config_s6.S6_SL_PCT}  → SL = {S6_SL:.2f}")
    print(f"  config_s6.S6_TRAILING_TRIGGER_PCT= {config_s6.S6_TRAILING_TRIGGER_PCT}  → trail ≈ {MARK*(1-config_s6.S6_TRAILING_TRIGGER_PCT):.1f}")
    print(f"  config_s6.S6_TRAIL_RANGE_PCT     = {config_s6.S6_TRAIL_RANGE_PCT}")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, hold_side="short"):
        b = make_bot()
        b._fire_s6(SYMBOL, _make_sig(), mark=MARK, balance=10_000.0)


def test_s6_scale_in_short():
    print(f"\n{'='*60}")
    print(f"S6 — Scale-in SHORT  (+{config_s6.S6_TRADE_SIZE_PCT*0.5*100:.0f}% of equity)")
    print(f"  in-window: mark < peak_level={PEAK_LEVEL:.1f}")
    print(f"{'='*60}")
    ap = {
        "side":                    "SHORT",
        "strategy":                "S6",
        "box_high":                S6_SL,
        "box_low":                 PEAK_LEVEL,
        "scale_in_pending":        True,
        "scale_in_trade_size_pct": config_s6.S6_TRADE_SIZE_PCT,
        "qty":                     0.0,
        "trade_id":                "test-trade-s6-001",
    }
    # mark must be < peak_level for in_window check
    with bc_spy(symbol=SYMBOL, mark_price=MARK, init_qty=0.002, scale_in_qty=0.004, hold_side="short"):
        b = make_bot()
        b.active_positions[SYMBOL] = ap
        b._do_scale_in(SYMBOL, ap)


def test_s6_trailing_refresh():
    print(f"\n{'='*60}")
    print(f"S6 — Trailing refresh  rangeRate={config_s6.S6_TRAIL_RANGE_PCT}")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, scale_in_qty=0.004, hold_side="short"):
        tr.refresh_plan_exits(SYMBOL, "short", new_trail_trigger=MARK * 0.90)


if __name__ == "__main__":
    test_s6_entry_short()
    test_s6_scale_in_short()
    test_s6_trailing_refresh()
    print(f"\n{'='*60}")
    print("S6 — all scenarios complete")
    print(f"{'='*60}")
