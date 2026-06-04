"""
Regression: rest_qty = round_qty(total - half) must not drop a tick.

Float error in the floor (`math.floor(qty/step)`) made `total - half` land on
e.g. 4.999999999999999 and floor to 4, so a small-tick position split as
half + rest left one tick uncovered by partial-TP/trailing (AMDUSDT: total 0.09
→ 0.04 + 0.04, losing 0.01). The invariant is: half + rest == total exactly.

Covers all four exchange rounders (bitget/trader = Bitget bot; bybit; binance).
backtest_engine._round_qty uses round() and is unaffected.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Small-tick symbol shaped like AMDUSDT (0.01 step, 2 dp).
_AMD_BITGET = {"price_place": 2, "volume_place": 2, "size_mult": 0.01, "min_trade_num": 0.01}
_AMD_STEP   = {"volume_place": 2, "qty_step": 0.01, "min_trade_num": 0.01, "min_notional": 0.0}

# Totals chosen so total/2 and total-half exercise float-boundary remainders.
_TOTALS = [0.03, 0.05, 0.07, 0.09, 0.11, 0.13, 0.15, 0.17, 0.19, 0.21, 0.23, 0.25]


def _assert_split_complete(round_qty_fn):
    for total in _TOTALS:
        half = float(round_qty_fn(total / 2))
        rest = float(round_qty_fn(total - half))
        assert abs((half + rest) - total) < 1e-9, (
            f"total={total}: half={half} + rest={rest} = {half + rest} — lost a tick"
        )


def test_trader_round_qty_split_complete(monkeypatch):
    import trader
    monkeypatch.setattr(trader, "_sym_info", lambda s: _AMD_BITGET)
    _assert_split_complete(lambda q: trader._round_qty(q, "AMDUSDT"))


def test_bitget_round_qty_split_complete(monkeypatch):
    import bitget
    monkeypatch.setattr(bitget, "sym_info", lambda s: _AMD_BITGET)
    _assert_split_complete(lambda q: bitget.round_qty(q, "AMDUSDT"))


def test_bybit_round_qty_split_complete(monkeypatch):
    import bybit
    monkeypatch.setattr(bybit, "sym_info", lambda s: _AMD_STEP)
    _assert_split_complete(lambda q: bybit.round_qty(q, "AMDUSDT"))


def test_binance_round_qty_split_complete(monkeypatch):
    import binance
    monkeypatch.setattr(binance, "sym_info", lambda s: _AMD_STEP)
    _assert_split_complete(lambda q: binance.round_qty(q, "AMDUSDT"))
