"""
state.py — Live State Broadcaster
Bot writes here → dashboard.py reads state.json every 3 seconds.
"""

import json, os, threading
from datetime import datetime, timezone

STATE_FILE = "state.json"
_lock = threading.Lock()

_default: dict = {
    "status":          "STOPPED",
    "started_at":      None,
    "last_tick":       None,
    "balance":         0.0,
    "open_trades":     [],
    "trade_history":   [],
    "scan_log":        [],
    "qualified_pairs": [],
    "pair_states":     {},
    "sentiment": {
        "direction":      "NEUTRAL",
        "bullish_weight": 0.5,
        "green_count":    0,
        "red_count":      0,
        "total_pairs":    0,
        "green_volume":   0.0,
        "red_volume":     0.0,
        "updated_at":     None,
    },
    "stats": {
        "total_trades": 0,
        "wins":         0,
        "losses":       0,
        "total_pnl":    0.0,
    }
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _read() -> dict:
    if not os.path.exists(STATE_FILE):
        return dict(_default)
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return dict(_default)

def _write(s: dict):
    with _lock:
        with open(STATE_FILE, "w") as f:
            json.dump(s, f, indent=2, default=str)


# ── Public API ────────────────────────────────────────────────────── #

def reset():                    _write(dict(_default))

def set_status(status: str):
    s = _read()
    s["status"] = status
    if status == "RUNNING" and not s.get("started_at"):
        s["started_at"] = _now()
    _write(s)

def update_balance(bal: float):
    s = _read(); s["balance"] = round(bal, 4); s["last_tick"] = _now(); _write(s)

def update_qualified_pairs(pairs: list[str]):
    s = _read()
    s["qualified_pairs"] = pairs
    # Remove ghost cards for pairs no longer being scanned
    current = set(pairs)
    s["pair_states"] = {
        sym: data
        for sym, data in s["pair_states"].items()
        if sym in current
    }
    _write(s)

def update_sentiment(sent) -> None:
    s = _read()
    s["sentiment"] = {
        "direction":      sent.direction,
        "bullish_weight": sent.bullish_weight,
        "green_count":    sent.green_count,
        "red_count":      sent.red_count,
        "total_pairs":    sent.total_pairs,
        "green_volume":   sent.green_volume,
        "red_volume":     sent.red_volume,
        "updated_at":     _now(),
    }
    _write(s)

def update_pair_state(symbol: str, data: dict):
    s = _read()
    s["pair_states"][symbol] = {**data, "updated_at": _now()}
    _write(s)

def add_open_trade(trade: dict):
    s = _read()
    trade["opened_at"] = _now()
    trade["unrealised_pnl"] = 0.0
    s["open_trades"].append(trade)
    s["stats"]["total_trades"] += 1
    _write(s)

def update_open_trade_pnl(symbol: str, pnl: float):
    s = _read()
    for t in s["open_trades"]:
        if t["symbol"] == symbol:
            t["unrealised_pnl"] = round(pnl, 4)
            break
    _write(s)

def close_trade(symbol: str, result: str, pnl: float):
    s = _read()
    closed = [t for t in s["open_trades"] if t["symbol"] == symbol]
    s["open_trades"] = [t for t in s["open_trades"] if t["symbol"] != symbol]
    for t in closed:
        t["closed_at"] = _now(); t["result"] = result; t["pnl"] = round(pnl, 4)
        s["trade_history"].insert(0, t)
    s["trade_history"] = s["trade_history"][:50]
    if result in ("WIN", "LOSS"):
        key = "wins" if result == "WIN" else "losses"
        s["stats"][key] += 1
        s["stats"]["total_pnl"] += pnl
    _write(s)

def add_scan_log(msg: str, level: str = "INFO"):
    s = _read()
    s["scan_log"].insert(0, {"time": _now(), "level": level, "msg": msg})
    s["scan_log"] = s["scan_log"][:100]
    _write(s)
