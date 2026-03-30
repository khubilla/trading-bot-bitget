# tests/test_ui.py
"""
UI tests — API shape (via FastAPI TestClient) + HTML presence (text search).
No browser required. No API keys needed. No running server needed.
"""
import os

import pytest

# ── Helpers ───────────────────────────────────────────────────────────────── #

DASHBOARD_HTML = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dashboard.html")


def _html() -> str:
    with open(DASHBOARD_HTML, encoding="utf-8") as f:
        return f.read()


# ── API shape tests ───────────────────────────────────────────────────────── #

class TestApiState:
    """Tests for the 3 simple /api/state contracts that need no CSV fixture."""

    @pytest.fixture(autouse=True)
    def client(self, tmp_path, monkeypatch):
        """TestClient using the real FastAPI app, redirected to a tmp state file."""
        from starlette.testclient import TestClient
        import dashboard

        monkeypatch.setattr(dashboard, "STATE_FILE", str(tmp_path / "state_paper.json"))
        self.client = TestClient(dashboard.app, raise_server_exceptions=False)

    def test_returns_200(self):
        """/api/state always returns HTTP 200 (never 404/500)."""
        resp = self.client.get("/api/state")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

    def test_response_is_json(self):
        """/api/state always returns valid JSON."""
        resp = self.client.get("/api/state")
        data = resp.json()
        assert isinstance(data, dict), f"Expected dict, got {type(data)}"

    def test_envelope_has_status_key(self):
        """/api/state response always has a 'status' key (STOPPED, RUNNING, or ERROR)."""
        resp = self.client.get("/api/state")
        data = resp.json()
        assert "status" in data, f"Missing 'status' key in response: {list(data.keys())}"


def test_trade_history_is_list_when_present(tmp_path, monkeypatch):
    """If trade_history is returned, it must be a list (never a dict or None)."""
    import json
    import dashboard
    from starlette.testclient import TestClient

    state_file = tmp_path / "state_paper.json"
    state_file.write_text(json.dumps({
        "status": "RUNNING", "started_at": "", "last_tick": "",
        "balance": 1000.0, "open_trades": {}, "trade_history": [],
        "scan_log": [], "qualified_pairs": [], "pair_states": {}, "sentiment": "NEUTRAL",
    }))
    monkeypatch.setattr(dashboard, "STATE_FILE", str(state_file))

    resp = TestClient(dashboard.app, raise_server_exceptions=False).get("/api/state")
    data = resp.json()
    assert isinstance(data.get("trade_history"), list), (
        f"trade_history must be a list, got {type(data.get('trade_history'))}"
    )


def test_trade_history_entry_has_chart_fields(tmp_path, monkeypatch):
    """
    When trade_history entries are present, each must have the chart replay fields:
    entry, sl, tp, exit_price, open_at, interval, events.
    These are populated by _load_csv_history (Task 3).
    """
    import csv, io, json
    import dashboard
    from starlette.testclient import TestClient

    CHART_FIELDS = {"entry", "sl", "tp", "exit_price", "open_at", "interval", "events"}

    fields = [
        "timestamp", "trade_id", "action", "symbol", "side", "qty", "entry", "sl", "tp",
        "box_low", "box_high", "leverage", "margin", "tpsl_set", "strategy",
        "snap_rsi", "snap_adx", "snap_htf", "snap_coil", "snap_box_range_pct", "snap_sentiment",
        "snap_daily_rsi", "snap_entry_trigger", "snap_sl", "snap_rr",
        "snap_rsi_peak", "snap_spike_body_pct", "snap_rsi_div", "snap_rsi_div_str",
        "snap_s5_ob_low", "snap_s5_ob_high", "snap_s5_tp", "snap_sr_clearance_pct",
        "result", "pnl", "pnl_pct", "exit_reason", "exit_price",
    ]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, restval="", extrasaction="ignore")
    w.writeheader()
    w.writerow({
        "timestamp": "2026-03-29T10:00:00+00:00",
        "trade_id": "t1", "action": "S5_LONG",
        "symbol": "BTCUSDT", "side": "LONG",
        "entry": "42000", "sl": "41500", "tp": "43000",
    })
    w.writerow({
        "timestamp": "2026-03-29T12:00:00+00:00",
        "trade_id": "t1", "action": "S5_CLOSE",
        "symbol": "BTCUSDT", "side": "LONG",
        "pnl": "4.0", "result": "WIN", "pnl_pct": "2.0",
        "exit_reason": "TP", "exit_price": "43000",
    })

    (tmp_path / "trades_paper.csv").write_text(buf.getvalue())
    (tmp_path / "state_paper.json").write_text(json.dumps({
        "status": "RUNNING", "started_at": "", "last_tick": "",
        "balance": 1000.0, "open_trades": {}, "trade_history": [],
        "scan_log": [], "qualified_pairs": [], "pair_states": {}, "sentiment": "NEUTRAL",
    }))
    monkeypatch.setattr(dashboard, "STATE_FILE", str(tmp_path / "state_paper.json"))

    resp = TestClient(dashboard.app, raise_server_exceptions=False).get("/api/state")
    data = resp.json()

    hist = data.get("trade_history", [])
    assert len(hist) >= 1, "Expected at least 1 trade history entry"

    missing = CHART_FIELDS - set(hist[0].keys())
    assert not missing, (
        f"trade_history entry missing chart fields: {sorted(missing)}. "
        "Check _load_csv_history in dashboard.py."
    )


def test_trade_history_snap_fields_forwarded(tmp_path, monkeypatch):
    """
    Snap fields from the OPEN row must appear in trade_history entries so
    claude_analyst.build_system_prompt() receives actual indicator values.
    """
    import csv, io, json
    import dashboard
    from starlette.testclient import TestClient

    fields = [
        "timestamp", "trade_id", "action", "symbol", "side", "qty", "entry", "sl", "tp",
        "box_low", "box_high", "leverage", "margin", "tpsl_set", "strategy",
        "snap_rsi", "snap_adx", "snap_htf", "snap_coil", "snap_box_range_pct", "snap_sentiment",
        "snap_daily_rsi", "snap_entry_trigger", "snap_sl", "snap_rr",
        "snap_rsi_peak", "snap_spike_body_pct", "snap_rsi_div", "snap_rsi_div_str",
        "snap_s5_ob_low", "snap_s5_ob_high", "snap_s5_tp", "snap_sr_clearance_pct",
        "result", "pnl", "pnl_pct", "exit_reason", "exit_price",
    ]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, restval="", extrasaction="ignore")
    w.writeheader()
    w.writerow({
        "timestamp": "2026-03-30T08:00:00+00:00",
        "trade_id": "snap1", "action": "S3_LONG",
        "symbol": "ONTUSDT", "side": "LONG",
        "entry": "0.07458", "sl": "0.07132", "tp": "0.08204",
        "snap_adx": "62.5", "snap_daily_rsi": "73.8", "snap_rr": "2.95",
        "snap_sentiment": "BULLISH",
    })
    w.writerow({
        "timestamp": "2026-03-30T09:00:00+00:00",
        "trade_id": "snap1", "action": "S3_CLOSE",
        "symbol": "ONTUSDT", "side": "LONG",
        "pnl": "-2.84", "result": "LOSS", "pnl_pct": "-43.44",
        "exit_reason": "SL", "exit_price": "0.07134",
    })

    (tmp_path / "trades_paper.csv").write_text(buf.getvalue())
    (tmp_path / "state_paper.json").write_text(json.dumps({
        "status": "RUNNING", "started_at": "", "last_tick": "",
        "balance": 1000.0, "open_trades": {}, "trade_history": [],
        "scan_log": [], "qualified_pairs": [], "pair_states": {}, "sentiment": "NEUTRAL",
    }))
    monkeypatch.setattr(dashboard, "STATE_FILE", str(tmp_path / "state_paper.json"))

    resp = TestClient(dashboard.app, raise_server_exceptions=False).get("/api/state")
    data = resp.json()

    hist = data.get("trade_history", [])
    assert len(hist) >= 1, "Expected at least 1 trade history entry"

    entry = hist[0]
    assert entry.get("snap_adx") == "62.5", f"snap_adx not forwarded, got {entry.get('snap_adx')!r}"
    assert entry.get("snap_daily_rsi") == "73.8", f"snap_daily_rsi not forwarded"
    assert entry.get("snap_rr") == "2.95", f"snap_rr not forwarded"
    assert entry.get("snap_sentiment") == "BULLISH", f"snap_sentiment not forwarded"


def test_trade_history_includes_box_levels(tmp_path, monkeypatch):
    """box_low and box_high from the OPEN CSV row must appear in trade_history entries."""
    import csv, io, json
    import dashboard
    from starlette.testclient import TestClient

    fields = [
        "timestamp", "trade_id", "action", "symbol", "side", "qty", "entry", "sl", "tp",
        "box_low", "box_high", "leverage", "margin", "tpsl_set", "strategy",
        "snap_rsi", "snap_adx", "snap_htf", "snap_coil", "snap_box_range_pct", "snap_sentiment",
        "snap_daily_rsi", "snap_entry_trigger", "snap_sl", "snap_rr",
        "snap_rsi_peak", "snap_spike_body_pct", "snap_rsi_div", "snap_rsi_div_str",
        "snap_s5_ob_low", "snap_s5_ob_high", "snap_s5_tp", "snap_sr_clearance_pct",
        "result", "pnl", "pnl_pct", "exit_reason", "exit_price",
    ]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, restval="", extrasaction="ignore")
    w.writeheader()
    w.writerow({
        "timestamp": "2026-03-30T08:00:00+00:00",
        "trade_id": "box1", "action": "S2_LONG",
        "symbol": "ARIAUSDT", "side": "LONG",
        "entry": "0.35713", "sl": "0.3392", "tp": "0.39284",
        "box_low": "0.3200", "box_high": "0.3550",
    })
    w.writerow({
        "timestamp": "2026-03-30T09:00:00+00:00",
        "trade_id": "box1", "action": "S2_CLOSE",
        "symbol": "ARIAUSDT", "pnl": "3.0", "result": "WIN",
        "pnl_pct": "12.0", "exit_reason": "TP", "exit_price": "0.39284",
    })

    (tmp_path / "trades_paper.csv").write_text(buf.getvalue())
    (tmp_path / "state_paper.json").write_text(json.dumps({
        "status": "RUNNING", "started_at": "", "last_tick": "",
        "balance": 1000.0, "open_trades": {}, "trade_history": [],
        "scan_log": [], "qualified_pairs": [], "pair_states": {}, "sentiment": "NEUTRAL",
    }))
    monkeypatch.setattr(dashboard, "STATE_FILE", str(tmp_path / "state_paper.json"))

    resp = TestClient(dashboard.app, raise_server_exceptions=False).get("/api/state")
    hist = resp.json().get("trade_history", [])
    assert len(hist) >= 1
    entry = hist[0]
    assert entry.get("box_low") == 0.32, f"box_low not in trade record, got {entry.get('box_low')!r}"
    assert entry.get("box_high") == 0.355, f"box_high not in trade record, got {entry.get('box_high')!r}"


# ── HTML presence tests ───────────────────────────────────────────────────── #

class TestDashboardHtml:
    def test_file_exists(self):
        """dashboard.html must exist."""
        assert os.path.exists(DASHBOARD_HTML), f"dashboard.html not found at {DASHBOARD_HTML}"

    def test_openTradeChart_defined(self):
        """openTradeChart() function must be defined in dashboard.html."""
        assert "function openTradeChart(" in _html(), \
            "openTradeChart() is missing from dashboard.html"

    def test_isoToUnix_defined(self):
        """isoToUnix() helper must be defined in dashboard.html."""
        assert "function isoToUnix(" in _html(), \
            "isoToUnix() is missing from dashboard.html"

    def test_applyClosedTradeOverlay_defined(self):
        """_applyClosedTradeOverlay() must be defined in dashboard.html."""
        assert "function _applyClosedTradeOverlay(" in _html(), \
            "_applyClosedTradeOverlay() is missing from dashboard.html"

    def test_drawTradeStems_defined(self):
        """_drawTradeStems() must be defined in dashboard.html."""
        assert "function _drawTradeStems(" in _html(), \
            "_drawTradeStems() is missing from dashboard.html"

    def test_drawTradeShading_defined(self):
        """_drawTradeShading() must be defined in dashboard.html."""
        assert "function _drawTradeShading(" in _html(), \
            "_drawTradeShading() is missing from dashboard.html"

    def test_trade_history_element_exists(self):
        """#trade-history DOM element must be declared in dashboard.html."""
        assert 'id="trade-history"' in _html(), \
            '#trade-history element missing from dashboard.html'

    def test_chartOverlay_element_exists(self):
        """#chartOverlay DOM element must be declared in dashboard.html."""
        assert 'id="chartOverlay"' in _html(), \
            '#chartOverlay element missing from dashboard.html'

    def test_hist_trades_store_assigned(self):
        """window._histTrades must be assigned in renderHistory (enables onclick index lookup)."""
        assert "window._histTrades" in _html(), \
            "window._histTrades assignment missing — clickable history rows will be broken"

    def test_overlay_closed_branch(self):
        """loadChart must branch on ov.closed to dispatch _applyClosedTradeOverlay."""
        assert "_applyClosedTradeOverlay" in _html(), \
            "_applyClosedTradeOverlay call missing from loadChart overlay block"

    def test_closechart_clears_markers(self):
        """closeChart must call setMarkers([]) to clean up on close."""
        assert "setMarkers([])" in _html(), \
            "setMarkers([]) missing from closeChart — markers will persist after modal close"

    def test_closechart_removes_overlay_elements(self):
        """closeChart must remove .trade-stems-svg and .trade-shade elements."""
        html = _html()
        assert "trade-stems-svg" in html, \
            ".trade-stems-svg cleanup missing from closeChart"
        assert "trade-shade" in html, \
            ".trade-shade cleanup missing from closeChart"


# ── Chat endpoint tests ───────────────────────────────────────────────────── #

import dashboard as _dashboard
from starlette.testclient import TestClient
client = TestClient(_dashboard.app, raise_server_exceptions=False)


def test_chat_endpoint_exists():
    """POST /api/chat must exist and return SSE content-type."""
    import claude_analyst as _ca
    original = _ca.stream_response
    _ca.stream_response = lambda system, messages: iter(["Hello", " world"])
    try:
        resp = client.post("/api/chat", json={
            "trade": {
                "symbol": "ONTUSDT", "side": "LONG", "strategy": "S3",
                "entry": "0.07458", "sl": "0.07132", "tp": "0.08204",
                "exit_price": "0.07134", "result": "LOSS",
                "pnl": "-2.8391", "pnl_pct": "-43.44", "exit_reason": "SL",
            },
            "messages": [{"role": "user", "content": "Was this entry valid?"}],
        })
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        body = resp.text
        assert 'data: "Hello"' in body
        assert "data: [DONE]" in body
    finally:
        _ca.stream_response = original


def test_chat_endpoint_missing_api_key(monkeypatch):
    """Missing ANTHROPIC_API_KEY returns 200 SSE with error message."""
    import claude_analyst as _ca
    def _raise(system, messages):
        raise Exception("No API key")
        yield
    original = _ca.stream_response
    _ca.stream_response = _raise
    try:
        resp = client.post("/api/chat", json={
            "trade": {"symbol": "X", "strategy": "S3"},
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 200
        assert "error" in resp.text.lower() or "⚠" in resp.text
    finally:
        _ca.stream_response = original


# ── Entry chart endpoint tests ─────────────────────────────────────────────── #

class TestApiEntryChart:
    """Tests for GET /api/entry-chart."""

    # 35 mock candle rows (oldest first)
    # Timestamp base: 1774856700000 = 2026-03-30 07:45 UTC, step 900_000 ms (15m)
    MOCK_CANDLES = [
        [str(1774856700000 + i * 900_000), "3.477", "3.534", "3.477", "3.534", "1000.0", "0"]
        for i in range(35)
    ]

    @pytest.fixture(autouse=True)
    def mock_bc(self, monkeypatch):
        import bitget_client as bc
        def _mock(path, params=None):
            if "/candles" in path:
                return {"data": self.MOCK_CANDLES}
            return {"data": []}
        monkeypatch.setattr(bc, "get_public", _mock)

    def test_returns_200(self):
        """/api/entry-chart returns HTTP 200."""
        resp = client.get("/api/entry-chart", params={
            "symbol": "UNIUSDT",
            "open_at": "2026-03-30T08:00:00+00:00",
            "strategy": "S3",
            "entry": "3.54",
            "sl": "3.462",
            "snap_sl": "3.452",
            "tp": "3.894",
            "snap_entry_trigger": "3.537",
        })
        assert resp.status_code == 200

    def test_response_shape(self):
        """Response has candles list, entry_ts int, highlights dict."""
        resp = client.get("/api/entry-chart", params={
            "symbol": "UNIUSDT",
            "open_at": "2026-03-30T08:00:00+00:00",
            "strategy": "S3",
            "entry": "3.54",
            "sl": "3.462",
            "snap_sl": "3.452",
            "tp": "3.894",
            "snap_entry_trigger": "3.537",
        })
        data = resp.json()
        assert "candles" in data, f"missing 'candles': {list(data.keys())}"
        assert "entry_ts" in data, f"missing 'entry_ts': {list(data.keys())}"
        assert "highlights" in data, f"missing 'highlights': {list(data.keys())}"
        assert isinstance(data["candles"], list)
        assert isinstance(data["highlights"], dict)
        assert len(data["candles"]) <= 25, f"expected ≤25 candles, got {len(data['candles'])}"

    def test_candle_ohlcv_fields(self):
        """Each candle has t, o, h, l, c, v fields."""
        resp = client.get("/api/entry-chart", params={
            "symbol": "UNIUSDT",
            "open_at": "2026-03-30T08:00:00+00:00",
            "strategy": "S3",
        })
        data = resp.json()
        assert data["candles"], "candles list is empty"
        c = data["candles"][0]
        for field in ("t", "o", "h", "l", "c", "v"):
            assert field in c, f"candle missing field '{field}': {list(c.keys())}"

    def test_s5_uses_ob_params(self):
        """S5 highlights come directly from snap_s5_ob_low/ob_high params."""
        resp = client.get("/api/entry-chart", params={
            "symbol": "WLDUSDT",
            "open_at": "2026-03-30T08:00:00+00:00",
            "strategy": "S5",
            "entry": "0.2849",
            "sl": "0.2817",
            "tp": "0.3134",
            "snap_s5_ob_low":  "0.2723",
            "snap_s5_ob_high": "0.2741",
        })
        data = resp.json()
        h = data.get("highlights", {})
        assert h.get("ob_low")  == 0.2723, f"ob_low wrong: {h.get('ob_low')}"
        assert h.get("ob_high") == 0.2741, f"ob_high wrong: {h.get('ob_high')}"

    def test_s1_uses_box_params(self):
        """S1 highlights come directly from box_low/box_high params."""
        resp = client.get("/api/entry-chart", params={
            "symbol": "BTCUSDT",
            "open_at": "2026-03-30T08:00:00+00:00",
            "strategy": "S1",
            "entry": "85000",
            "box_low": "84000",
            "box_high": "85500",
        })
        data = resp.json()
        h = data.get("highlights", {})
        assert h.get("box_low")  == 84000.0
        assert h.get("box_high") == 85500.0

    def test_missing_symbol_returns_error(self):
        """/api/entry-chart with no candle data returns JSON with error key."""
        import bitget_client as bc
        # Override to return empty for this one test
        original = bc.get_public
        bc.get_public = lambda path, params=None: {"data": []}
        try:
            resp = client.get("/api/entry-chart", params={
                "symbol": "FAKECOIN",
                "open_at": "2026-03-30T08:00:00+00:00",
                "strategy": "S3",
            })
            assert resp.status_code == 200
            assert "error" in resp.json()
        finally:
            bc.get_public = original
