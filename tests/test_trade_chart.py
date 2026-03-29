import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

def test_get_last_close_returns_exit_price(tmp_path, monkeypatch):
    """get_last_close must return exit_price from paper_trader history."""
    import paper_trader
    fake_state = {
        "balance": 1000.0,
        "positions": {},
        "history": [
            {
                "symbol": "BTCUSDT",
                "pnl": 4.2,
                "pnl_pct": 2.1,
                "reason": "TP",
                "exit": 42680.0,   # already stored, not yet surfaced
            }
        ],
        "total_pnl": 4.2,
        "partial_closes": [],
    }
    monkeypatch.setattr(paper_trader, "_load", lambda: dict(fake_state))
    result = paper_trader.get_last_close("BTCUSDT")
    assert result is not None
    assert result["exit_price"] == 42680.0, f"expected 42680.0, got {result.get('exit_price')}"
