# tests/manual/run_test_s3.py
"""
S3 manual test — Pullback to support (LONG only).

Run standalone:  python tests/manual/run_test_s3.py
Run via pytest:  pytest tests/manual/run_test_s3.py -v -s
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.manual._bc_spy import bc_spy
from tests.manual._bot_factory import make_bot
import config_s3
import config_s1   # STOP_LOSS_PCT cap is trader.py's default, sourced from config_s1
import trader as tr

SYMBOL = "BTCUSDT"
MARK   = 50_000.0
S3_SL  = MARK * 0.96   # structural SL ~4% below — within STOP_LOSS_PCT cap


def _make_sig(sl_override: float = None) -> dict:
    sl = sl_override if sl_override is not None else S3_SL
    return {
        "strategy":              "S3",
        "side":                  "LONG",
        "trigger":               MARK,
        "s3_sl":                 sl,
        "snap_adx":              28.5,
        "snap_entry_trigger":    round(MARK, 8),
        "snap_sl":               round(sl, 8),
        "snap_sentiment":        "BULLISH",
        "snap_sr_clearance_pct": 8.0,
        "priority_rank":         1,
        "priority_score":        28.0,
    }


def test_s3_entry_long():
    print(f"\n{'='*60}")
    print(f"S3 — Entry LONG  (structural SL from pending sig)")
    print(f"  config_s3.S3_LEVERAGE           = {config_s3.S3_LEVERAGE}")
    print(f"  config_s3.S3_TRADE_SIZE_PCT      = {config_s3.S3_TRADE_SIZE_PCT}")
    print(f"  s3_sl={S3_SL:.1f}  floor={MARK*(1-config_s1.STOP_LOSS_PCT):.1f}  → uses max(sl_floor, cap)")
    print(f"  config_s3.S3_TRAILING_TRIGGER_PCT= {config_s3.S3_TRAILING_TRIGGER_PCT}  → trail ≈ {MARK*(1+config_s3.S3_TRAILING_TRIGGER_PCT):.1f}")
    print(f"  config_s3.S3_TRAILING_RANGE_PCT  = {config_s3.S3_TRAILING_RANGE_PCT}")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, hold_side="long"):
        b = make_bot()
        b._fire_s3(SYMBOL, _make_sig(), mark=MARK, balance=10_000.0)


def test_s3_sl_capped_by_pct():
    """sl_floor far below entry → SL capped at fill*(1-STOP_LOSS_PCT)."""
    sl_far = MARK * 0.80   # 20% below — overridden by STOP_LOSS_PCT cap
    print(f"\n{'='*60}")
    print(f"S3 — Entry LONG  (sl_floor far → capped at STOP_LOSS_PCT)")
    print(f"  s3_sl_far={sl_far:.1f}  cap={MARK*(1-config_s1.STOP_LOSS_PCT):.1f}")
    print(f"  cap comes from config_s1.STOP_LOSS_PCT = {config_s1.STOP_LOSS_PCT} (trader.py default)")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, hold_side="long"):
        b = make_bot()
        b._fire_s3(SYMBOL, _make_sig(sl_override=sl_far), mark=MARK, balance=10_000.0)


def test_s3_trailing_refresh():
    print(f"\n{'='*60}")
    print(f"S3 — Trailing refresh  rangeRate={config_s3.S3_TRAILING_RANGE_PCT}")
    print(f"{'='*60}")
    with bc_spy(symbol=SYMBOL, mark_price=MARK, scale_in_qty=0.002, hold_side="long"):
        tr.refresh_plan_exits(SYMBOL, "long", new_trail_trigger=MARK * 1.10)


if __name__ == "__main__":
    test_s3_entry_long()
    test_s3_sl_capped_by_pct()
    test_s3_trailing_refresh()
    print(f"\n{'='*60}")
    print("S3 — all scenarios complete")
    print(f"{'='*60}")
