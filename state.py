"""
state.py — Live State Broadcaster
Bot writes here → dashboard.py reads state.json every 3 seconds.
"""

import json, os, threading, tempfile
from datetime import datetime, timezone
from pathlib import Path

_DATA_DIR  = Path(os.environ.get("DATA_DIR", "."))
STATE_FILE = str(_DATA_DIR / "state.json")
_lock = threading.Lock()

def set_file(path: str):
    """Call once at startup to redirect all state I/O to a different file (e.g. paper mode)."""
    global STATE_FILE
    STATE_FILE = path

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
    "position_memory": {},   # survives reset(): {symbol: {initial_qty, partial_logged}}
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
        dir_ = os.path.dirname(os.path.abspath(STATE_FILE))
        with tempfile.NamedTemporaryFile("w", dir=dir_, delete=False, suffix=".tmp") as tmp:
            json.dump(s, tmp, indent=2, default=str)
            tmp_path = tmp.name
        os.replace(tmp_path, STATE_FILE)


# ── Public API ────────────────────────────────────────────────────── #

def reset():
    """Reset runtime state but preserve trade_history, stats, and position_memory across restarts."""
    s = _read()
    fresh = dict(_default)
    fresh["trade_history"]   = s.get("trade_history", [])
    fresh["stats"]           = s.get("stats", dict(_default["stats"]))
    fresh["position_memory"] = s.get("position_memory", {})
    _write(fresh)

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

def patch_pair_state(symbol: str, patch: dict):
    """Merge patch into an existing pair state entry without overwriting other fields."""
    s = _read()
    existing = s["pair_states"].get(symbol, {})
    s["pair_states"][symbol] = {**existing, **patch, "updated_at": _now()}
    _write(s)

def add_open_trade(trade: dict):
    s = _read()
    if not trade.get("opened_at"):
        trade["opened_at"] = _now()
    trade["unrealised_pnl"] = 0.0
    s["open_trades"].append(trade)
    _write(s)

def update_open_trade_pnl(symbol: str, pnl: float):
    s = _read()
    for t in s["open_trades"]:
        if t["symbol"] == symbol:
            t["unrealised_pnl"] = round(pnl, 4)
            break
    _write(s)

def update_open_trade_margin(symbol: str, new_margin: float):
    s = _read()
    for t in s["open_trades"]:
        if t["symbol"] == symbol:
            t["margin"] = round(new_margin, 4)
            break
    _write(s)

def update_open_trade_leverage(symbol: str, leverage: int):
    s = _read()
    for t in s["open_trades"]:
        if t["symbol"] == symbol:
            t["leverage"] = leverage
            break
    _write(s)

def update_open_trade_mark_price(symbol: str, mark_price: float):
    s = _read()
    for t in s["open_trades"]:
        if t["symbol"] == symbol:
            t["mark_price"] = mark_price
            break
    _write(s)

def update_open_trade_sl(symbol: str, new_sl: float):
    s = _read()
    for t in s["open_trades"]:
        if t["symbol"] == symbol:
            t["sl"] = round(new_sl, 8)
            break
    _write(s)

def set_stats(wins: int, losses: int, total_pnl: float):
    """Overwrite stats (used on startup to restore from CSV)."""
    s = _read()
    s["stats"]["wins"]        = wins
    s["stats"]["losses"]      = losses
    s["stats"]["total_trades"] = wins + losses
    s["stats"]["total_pnl"]   = total_pnl
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
        s["stats"]["total_trades"] += 1
        s["stats"]["total_pnl"] += pnl
    _write(s)

def record_loss(symbol: str):
    """Increment daily loss counter for a symbol. Resets automatically on a new UTC day."""
    s = _read()
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    losses = s.setdefault('daily_losses', {})
    entry = losses.get(symbol, {'date': '', 'count': 0})
    if entry['date'] != today:
        entry = {'date': today, 'count': 0}
    entry['count'] += 1
    losses[symbol] = entry
    _write(s)

def is_pair_paused(symbol: str) -> bool:
    """Returns True if the pair has hit 3 losses today and should be skipped."""
    s = _read()
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    entry = s.get('daily_losses', {}).get(symbol, {'date': '', 'count': 0})
    return entry['date'] == today and entry['count'] >= 3

def get_open_trade(symbol: str) -> dict | None:
    """Return the open trade dict for a symbol, or None if not found."""
    for t in _read()["open_trades"]:
        if t["symbol"] == symbol:
            return t
    return None

def add_scan_log(msg: str, level: str = "INFO"):
    s = _read()
    s["scan_log"].insert(0, {"time": _now(), "level": level, "msg": msg})
    s["scan_log"] = s["scan_log"][:100]
    _write(s)

def get_position_memory(symbol: str) -> dict:
    """Return persisted {initial_qty, partial_logged} for a symbol, or {} if none."""
    return _read().get("position_memory", {}).get(symbol, {})

def update_position_memory(symbol: str, **kwargs):
    """Persist partial-detection state for a symbol so it survives restarts."""
    s = _read()
    s.setdefault("position_memory", {}).setdefault(symbol, {}).update(kwargs)
    _write(s)

def clear_position_memory(symbol: str):
    """Remove position memory for a symbol when the trade fully closes."""
    s = _read()
    s.get("position_memory", {}).pop(symbol, None)
    _write(s)
