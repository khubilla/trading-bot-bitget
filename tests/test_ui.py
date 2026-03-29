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
