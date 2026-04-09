# tests/test_scanner_liquidity.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import scanner


def _ticker(symbol, bid_pr, ask_pr, bid_sz, ask_sz, vol=10_000_000):
    """Build a minimal ticker dict that passes volume and price filters."""
    last_pr = (float(bid_pr) + float(ask_pr)) / 2
    return {
        "symbol":      symbol,
        "quoteVolume": str(vol),
        "openUtc":     str(last_pr * 0.99),
        "lastPr":      str(last_pr),
        "bidPr":       str(bid_pr),
        "askPr":       str(ask_pr),
        "bidSz":       str(bid_sz),
        "askSz":       str(ask_sz),
    }


def _mock_response(tickers):
    return {"code": "00000", "data": tickers}


# ── Unit tests for _filter_by_liquidity ──────────────────────────────

class TestFilterByLiquidity:

    def test_removes_shallow_pairs(self, monkeypatch):
        monkeypatch.setattr(scanner, "MIN_OB_DEPTH_USDT", 50_000)
        depth_map = {"LIQUIDUSDT": 175_000.0, "ARIAUSDT": 367.0}
        result = scanner._filter_by_liquidity(["LIQUIDUSDT", "ARIAUSDT"], depth_map)
        assert result == ["LIQUIDUSDT"]

    def test_keeps_pair_at_exact_threshold(self, monkeypatch):
        monkeypatch.setattr(scanner, "MIN_OB_DEPTH_USDT", 50_000)
        result = scanner._filter_by_liquidity(["XYZUSDT"], {"XYZUSDT": 50_000.0})
        assert result == ["XYZUSDT"]

    def test_missing_pair_in_depth_map_excluded(self, monkeypatch):
        monkeypatch.setattr(scanner, "MIN_OB_DEPTH_USDT", 50_000)
        result = scanner._filter_by_liquidity(["XYZUSDT"], depth_map={})
        assert result == []

    def test_all_liquid_returns_all(self, monkeypatch):
        monkeypatch.setattr(scanner, "MIN_OB_DEPTH_USDT", 50_000)
        pairs = ["AAUSDT", "BBUSDT"]
        depth_map = {"AAUSDT": 200_000.0, "BBUSDT": 80_000.0}
        result = scanner._filter_by_liquidity(pairs, depth_map)
        assert result == pairs

    def test_empty_input_returns_empty(self, monkeypatch):
        monkeypatch.setattr(scanner, "MIN_OB_DEPTH_USDT", 50_000)
        result = scanner._filter_by_liquidity([], depth_map={})
        assert result == []


# ── Integration tests for get_qualified_pairs_and_sentiment ──────────

class TestLiquidityFilterIntegration:

    def _setup(self, monkeypatch):
        monkeypatch.setattr(scanner, "MIN_VOLUME_USDT", 5_000_000)
        monkeypatch.setattr(scanner, "MAX_PRICE_USDT", 150)
        monkeypatch.setattr(scanner, "SENTIMENT_THRESHOLD", 0.55)
        monkeypatch.setattr(scanner, "MIN_OB_DEPTH_USDT", 50_000)

    def test_enabled_removes_illiquid_pair(self, monkeypatch):
        # LIQUIDUSDT: 20000 × 5.00 + 15000 × 5.01 = $175,150 → passes
        # ARIAUSDT:    200  × 1.04 +   150 × 1.06 = $367     → fails
        self._setup(monkeypatch)
        monkeypatch.setattr(scanner, "LIQUIDITY_CHECK_ENABLED", True)
        tickers = [
            _ticker("LIQUIDUSDT", bid_pr=5.00, ask_pr=5.01, bid_sz=20000, ask_sz=15000),
            _ticker("ARIAUSDT",   bid_pr=1.04, ask_pr=1.06, bid_sz=200,   ask_sz=150),
        ]
        monkeypatch.setattr(scanner.bc, "get_public", lambda *a, **kw: _mock_response(tickers))
        pairs, _ = scanner.get_qualified_pairs_and_sentiment()
        assert "LIQUIDUSDT" in pairs
        assert "ARIAUSDT" not in pairs

    def test_disabled_keeps_illiquid_pair(self, monkeypatch):
        self._setup(monkeypatch)
        monkeypatch.setattr(scanner, "LIQUIDITY_CHECK_ENABLED", False)
        tickers = [
            _ticker("LIQUIDUSDT", bid_pr=5.00, ask_pr=5.01, bid_sz=20000, ask_sz=15000),
            _ticker("ARIAUSDT",   bid_pr=1.04, ask_pr=1.06, bid_sz=200,   ask_sz=150),
        ]
        monkeypatch.setattr(scanner.bc, "get_public", lambda *a, **kw: _mock_response(tickers))
        pairs, _ = scanner.get_qualified_pairs_and_sentiment()
        assert "ARIAUSDT" in pairs
        assert "LIQUIDUSDT" in pairs  # ← add this

    def test_missing_bid_ask_sz_excluded(self, monkeypatch):
        """Pairs where ticker lacks bidSz/askSz get depth=0 and are excluded."""
        self._setup(monkeypatch)
        monkeypatch.setattr(scanner, "LIQUIDITY_CHECK_ENABLED", True)
        ticker_no_size = {
            "symbol":      "NOSIZUSDT",
            "quoteVolume": "10000000",
            "openUtc":     "4.95",
            "lastPr":      "5.0",
            "bidPr":       "4.99",
            "askPr":       "5.01",
            # bidSz and askSz intentionally absent
        }
        monkeypatch.setattr(scanner.bc, "get_public", lambda *a, **kw: _mock_response([ticker_no_size]))
        pairs, _ = scanner.get_qualified_pairs_and_sentiment()
        assert "NOSIZUSDT" not in pairs
