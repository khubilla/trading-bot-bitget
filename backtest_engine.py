"""
backtest_engine.py — Unified backtesting harness for S1–S6

Monkey-patches trader/state/scanner/snapshot/claude_filter/startup_recovery
with in-memory mocks, then runs MTFBot._tick() against historical parquet data.
"""

import sys
import uuid
import time as _time_mod
import logging
import argparse
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# BacktestState — in-memory replacement for state.py
# ═══════════════════════════════════════════════════════════════════

class BacktestState:
    """Drop-in mock for state.py — all writes go to in-memory dicts."""

    def __init__(self):
        self._open_trades: dict[str, dict] = {}
        self._position_memory: dict[str, dict] = {}
        self._pair_states: dict[str, dict] = {}
        self._stats = {"wins": 0, "losses": 0, "total_pnl": 0.0}
        self._loss_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._pending_signals: dict = {}
        self.qualified_pairs: list = []
        self.sentiment = None
        self.balance: float = 0.0
        self.closed_trades: list[dict] = []

    # ── Trade management ──────────────────────────────────────────── #

    def add_open_trade(self, trade: dict) -> None:
        self._open_trades[trade["symbol"]] = dict(trade)

    def get_open_trade(self, symbol: str) -> dict | None:
        return self._open_trades.get(symbol)

    def get_open_trades(self) -> list[dict]:
        return list(self._open_trades.values())

    def close_trade(self, symbol: str, pnl: float, result: str,
                    exit_price: float, exit_reason: str) -> None:
        ot = self._open_trades.pop(symbol, None)
        if ot:
            ot.update({"pnl": pnl, "result": result,
                        "exit_price": exit_price, "exit_reason": exit_reason})
            self.closed_trades.append(ot)

    def update_open_trade_margin(self, symbol: str, margin: float) -> None:
        if symbol in self._open_trades:
            self._open_trades[symbol]["margin"] = margin

    def update_open_trade_pnl(self, symbol: str, pnl: float) -> None:
        if symbol in self._open_trades:
            self._open_trades[symbol]["unrealised_pnl"] = pnl

    def update_open_trade_mark_price(self, symbol: str, price: float) -> None:
        if symbol in self._open_trades:
            self._open_trades[symbol]["mark_price"] = price

    def update_open_trade_sl(self, symbol: str, sl: float) -> None:
        if symbol in self._open_trades:
            self._open_trades[symbol]["sl"] = sl

    def update_open_trade_leverage(self, symbol: str, lev: int) -> None:
        if symbol in self._open_trades:
            self._open_trades[symbol]["leverage"] = lev

    def patch_pair_state(self, symbol: str, **kwargs) -> None:
        self._pair_states.setdefault(symbol, {}).update(kwargs)

    def update_pair_state(self, symbol: str, data: dict) -> None:
        self._pair_states.setdefault(symbol, {}).update(data)

    def get_pair_state(self, symbol: str) -> dict:
        return self._pair_states.get(symbol, {})

    # ── Stats / pause ─────────────────────────────────────────────── #

    def set_stats(self, wins: int, losses: int, total_pnl: float,
                  pnl_pct: float = 0.0) -> None:
        self._stats = {"wins": wins, "losses": losses, "total_pnl": total_pnl}

    def record_loss(self, symbol: str, day_str: str) -> None:
        self._loss_counts[symbol][day_str] += 1

    def is_pair_paused(self, symbol: str, day_str: str | None = None) -> bool:
        if day_str is None:
            day_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self._loss_counts[symbol][day_str] >= 3

    # ── Position memory ───────────────────────────────────────────── #

    def update_position_memory(self, symbol: str, **kwargs) -> None:
        self._position_memory.setdefault(symbol, {}).update(kwargs)

    def get_position_memory(self, symbol: str) -> dict:
        return self._position_memory.get(symbol, {})

    def clear_position_memory(self, symbol: str) -> None:
        self._position_memory.pop(symbol, None)

    # ── Pending signals ───────────────────────────────────────────── #

    def load_pending_signals(self) -> dict:
        return {}

    def save_pending_signals(self, signals: dict) -> None:
        self._pending_signals = signals

    # ── Bot infra (all no-ops or trivial) ────────────────────────── #

    def reset(self) -> None:
        pass

    def set_status(self, status: str) -> None:
        pass

    def set_file(self, path: str) -> None:
        pass

    def add_scan_log(self, msg: str, level: str = "INFO") -> None:
        pass

    def update_balance(self, balance: float) -> None:
        self.balance = balance

    def update_sentiment(self, sentiment) -> None:
        self.sentiment = sentiment

    def update_qualified_pairs(self, pairs: list) -> None:
        self.qualified_pairs = pairs

    def _read(self) -> dict:
        return {"open_trades": list(self._open_trades.values())}

    def _write(self, data: dict) -> None:
        pass


# ═══════════════════════════════════════════════════════════════════
# MockTrader — replacement for trader.py
# ═══════════════════════════════════════════════════════════════════

class MockTrader:
    """
    Fake trader.py. Implements the full public interface used by bot.py.
    Feeds candle data from parquet dicts, simulates positions in memory.

    parquet: dict[symbol] = {
        "3m":  pd.DataFrame,
        "15m": pd.DataFrame,
        "1h":  pd.DataFrame,
        "1d":  pd.DataFrame,
    }
    """

    def __init__(self, universe: list[str], parquet: dict[str, dict],
                 balance: float = 1000.0):
        self.universe    = universe
        self.parquet     = parquet   # sym → {"3m": df, "15m": df, "1h": df, "1d": df}
        self._balance    = balance
        self.sim_time: int = 0       # current simulation epoch ms

        self._positions: dict[str, dict]  = {}
        self._pending_orders: dict[str, dict] = {}   # order_id → order
        self._closed_trades: list[dict]   = []
        self._partial_events: list[dict]  = []        # drained by bot._tick() (PAPER path, kept for symmetry)

    # ── Time-sliced candle access ─────────────────────────────────── #

    def _slice(self, symbol: str, tf: str, limit: int) -> pd.DataFrame:
        """Return last `limit` candles for symbol/timeframe up to sim_time."""
        df = self.parquet.get(symbol, {}).get(tf)
        if df is None or df.empty:
            return pd.DataFrame()
        sliced = df[df["ts"] <= self.sim_time]
        return sliced.tail(limit).reset_index(drop=True)

    def get_candles(self, symbol: str, interval: str, limit: int = 100) -> pd.DataFrame:
        tf_map = {
            "3m": "3m", "15m": "15m",
            "1H": "1h", "1h": "1h",
            "1D": "1d", "1d": "1d", "1Dutc": "1d",
        }
        tf = tf_map.get(interval, interval.lower())
        return self._slice(symbol, tf, limit)

    def get_daily_candles_utc(self, symbol: str, limit: int = 100) -> pd.DataFrame:
        return self._slice(symbol, "1d", limit)

    def get_mark_price(self, symbol: str) -> float:
        df = self._slice(symbol, "3m", 1)
        if df.empty:
            return 0.0
        return float(df.iloc[-1]["close"])

    # ── Account ───────────────────────────────────────────────────── #

    def get_usdt_balance(self) -> float:
        return self._balance

    def _get_total_equity(self) -> float:
        return self._balance

    def _update_balance(self, pnl: float) -> None:
        self._balance += pnl

    # ── Rounding helpers (passthrough — no symbol precision needed in sim) #

    def _round_price(self, price: float, symbol: str) -> float:
        return round(price, 6)

    def _round_qty(self, qty: float, symbol: str) -> float:
        return round(qty, 4)

    # ── Positions ─────────────────────────────────────────────────── #

    def get_all_open_positions(self) -> dict[str, dict]:
        """Return exchange-style position dict for each open position."""
        result = {}
        for sym, pos in self._positions.items():
            result[sym] = {
                "side":           pos["side"],
                "entry_price":    pos["entry"],
                "qty":            pos["qty"],
                "unrealised_pnl": self._unrealised_pnl(sym, pos),
                "mark_price":     self.get_mark_price(sym),
                "margin":         pos["margin"],
                "leverage":       pos["leverage"],
            }
        return result

    def _unrealised_pnl(self, sym: str, pos: dict) -> float:
        mark = self.get_mark_price(sym)
        if mark == 0:
            return 0.0
        direction = 1 if pos["side"] == "LONG" else -1
        return direction * (mark - pos["entry"]) / pos["entry"] * pos["margin"] * pos["leverage"]

    def _calc_qty(self, symbol: str, trade_size_pct: float, leverage: int) -> tuple[float, float]:
        """Returns (qty, margin)."""
        mark   = self.get_mark_price(symbol)
        margin = self._balance * trade_size_pct
        qty    = self._round_qty((margin * leverage) / mark, symbol) if mark > 0 else 0.0
        return qty, margin

    def _open_position(self, symbol: str, side: str,
                       sl: float, tp_trig: float, trail_pct: float,
                       leverage: int, trade_size_pct: float,
                       size_multiplier: float = 1.0,
                       scale_in: bool = False) -> dict:
        """Core position-open logic. size_multiplier=0.5 for scale-in initial."""
        mark   = self.get_mark_price(symbol)
        qty, margin = self._calc_qty(symbol, trade_size_pct * size_multiplier, leverage)
        trade_id = uuid.uuid4().hex[:8]

        self._positions[symbol] = {
            "_symbol":        symbol,
            "side":           side,
            "entry":          mark,
            "qty":            qty,
            "initial_qty":    qty,
            "sl":             sl,
            "tp_trig":        tp_trig,
            "trail_pct":      trail_pct,
            "trail_active":   False,
            "trail_peak":     0.0,
            "trail_sl":       0.0,
            "partial_done":   False,
            "scale_in_after": self.sim_time + 3_600_000 if scale_in else 0,
            "scale_in_done":  not scale_in,
            "margin":         margin,
            "leverage":       leverage,
            "strategy":       "",
            "trade_id":       trade_id,
            "open_ts":        self.sim_time,
        }
        return {
            "symbol": symbol, "side": side, "qty": str(qty),
            "entry":  mark,   "sl":   sl,   "tp":   tp_trig,
            "box_low": 0.0, "leverage": leverage,
            "margin": margin, "tpsl_set": True,
        }

    def open_long(self, symbol: str, box_low: float = 0, sl_floor: float = 0,
                  leverage: int = 10, trade_size_pct: float = 0.04,
                  take_profit_pct: float = 0.10, stop_loss_pct: float = 0.05,
                  use_s1_exits: bool = False, use_s2_exits: bool = False,
                  use_s3_exits: bool = False, use_s5_exits: bool = False,
                  tp_price_abs: float = 0) -> dict:
        mark = self.get_mark_price(symbol)
        # Determine SL
        if sl_floor > 0:
            sl = sl_floor
        elif box_low > 0:
            sl = max(box_low * 0.999, mark * (1 - stop_loss_pct))
        else:
            sl = mark * (1 - stop_loss_pct)

        # Determine TP trigger and trail pct — use getattr fallbacks so this
        # works even when backtest.py has installed lightweight config stubs
        def _cfg(mod_name, attr, default):
            m = sys.modules.get(mod_name)
            return getattr(m, attr, default) if m is not None else default

        if use_s5_exits:
            one_r    = mark - sl
            tp_trig  = mark + one_r
            trail_pct = _cfg("config_s5", "S5_TRAIL_RANGE_PCT", 10.0)
        elif use_s1_exits:
            tp_trig  = mark * (1 + _cfg("config_s1", "TAKE_PROFIT_PCT", 0.10))
            trail_pct = _cfg("config_s1", "S1_TRAIL_RANGE_PCT", 10.0)
        elif use_s2_exits:
            tp_trig  = mark * (1 + _cfg("config_s2", "S2_TRAILING_TRIGGER_PCT", 0.10))
            trail_pct = _cfg("config_s2", "S2_TRAILING_RANGE_PCT", 10.0)
        elif use_s3_exits:
            tp_trig  = mark * (1 + _cfg("config_s3", "S3_TRAILING_TRIGGER_PCT", 0.10))
            trail_pct = _cfg("config_s3", "S3_TRAILING_RANGE_PCT", 10.0)
        else:
            tp_trig  = tp_price_abs if tp_price_abs > mark else mark * (1 + take_profit_pct)
            trail_pct = 10.0

        # S2 starts at 50% size with scale-in queued
        needs_scale_in = use_s2_exits
        size_mult = 0.5 if needs_scale_in else 1.0
        return self._open_position(symbol, "LONG", sl, tp_trig, trail_pct,
                                   leverage, trade_size_pct, size_mult, needs_scale_in)

    def open_short(self, symbol: str, box_high: float = 0, sl_floor: float = 0,
                   leverage: int = 10, trade_size_pct: float = 0.04,
                   take_profit_pct: float = 0.10,
                   use_s1_exits: bool = False, use_s4_exits: bool = False,
                   use_s5_exits: bool = False, use_s6_exits: bool = False,
                   tp_price_abs: float = 0) -> dict:
        mark = self.get_mark_price(symbol)
        # SL
        if sl_floor > 0:
            sl = sl_floor
        elif box_high > 0:
            sl = box_high * 1.001
        else:
            sl = mark * (1 + 0.05)

        # TP trigger and trail pct — use getattr fallbacks for config stubs
        def _cfg(mod_name, attr, default):
            m = sys.modules.get(mod_name)
            return getattr(m, attr, default) if m is not None else default

        if use_s5_exits:
            one_r    = sl - mark
            tp_trig  = mark - one_r
            trail_pct = _cfg("config_s5", "S5_TRAIL_RANGE_PCT", 10.0)
        elif use_s1_exits:
            tp_trig  = mark * (1 - _cfg("config_s1", "TAKE_PROFIT_PCT", 0.10))
            trail_pct = _cfg("config_s1", "S1_TRAIL_RANGE_PCT", 10.0)
        elif use_s4_exits:
            tp_trig  = mark * (1 - _cfg("config_s4", "S4_TRAILING_TRIGGER_PCT", 0.10))
            trail_pct = _cfg("config_s4", "S4_TRAILING_RANGE_PCT", 10.0)
        elif use_s6_exits:
            tp_trig  = mark * (1 - _cfg("config_s6", "S6_TRAILING_TRIGGER_PCT", 0.10))
            trail_pct = _cfg("config_s6", "S6_TRAIL_RANGE_PCT", 10.0)
        else:
            tp_trig  = tp_price_abs if 0 < tp_price_abs < mark else mark * (1 - take_profit_pct)
            trail_pct = 10.0

        needs_scale_in = use_s4_exits or use_s6_exits
        size_mult = 0.5 if needs_scale_in else 1.0
        return self._open_position(symbol, "SHORT", sl, tp_trig, trail_pct,
                                   leverage, trade_size_pct, size_mult, needs_scale_in)

    def scale_in_long(self, symbol: str, additional_trade_size_pct: float,
                      leverage: int) -> None:
        if symbol not in self._positions:
            return
        pos    = self._positions[symbol]
        mark   = self.get_mark_price(symbol)
        extra_qty, extra_margin = self._calc_qty(symbol, additional_trade_size_pct, leverage)
        old_qty = pos["qty"]
        new_qty = old_qty + extra_qty
        avg_entry = (pos["entry"] * old_qty + mark * extra_qty) / new_qty
        pos["qty"]           = new_qty
        pos["initial_qty"]   = new_qty
        pos["entry"]         = avg_entry
        pos["margin"]       += extra_margin
        pos["scale_in_done"] = True
        pos["tp_trig"]       = avg_entry * (pos["tp_trig"] / pos["entry"])

    def scale_in_short(self, symbol: str, additional_trade_size_pct: float,
                       leverage: int) -> None:
        if symbol not in self._positions:
            return
        pos    = self._positions[symbol]
        mark   = self.get_mark_price(symbol)
        extra_qty, extra_margin = self._calc_qty(symbol, additional_trade_size_pct, leverage)
        old_qty = pos["qty"]
        new_qty = old_qty + extra_qty
        avg_entry = (pos["entry"] * old_qty + mark * extra_qty) / new_qty
        pos["qty"]           = new_qty
        pos["initial_qty"]   = new_qty
        pos["entry"]         = avg_entry
        pos["margin"]       += extra_margin
        pos["scale_in_done"] = True
        pos["tp_trig"]       = avg_entry * (pos["tp_trig"] / pos["entry"])

    # ── Exit order management ─────────────────────────────────────── #

    def refresh_plan_exits(self, symbol: str, hold_side: str,
                           new_trail_trigger: float = 0) -> bool:
        if symbol in self._positions and new_trail_trigger > 0:
            self._positions[symbol]["tp_trig"] = new_trail_trigger
        return True

    def update_position_sl(self, symbol: str, new_sl: float,
                           hold_side: str = "long") -> bool:
        if symbol in self._positions:
            pos = self._positions[symbol]
            if pos["side"] == "LONG" and new_sl > pos["sl"]:
                pos["sl"] = new_sl
            elif pos["side"] == "SHORT" and new_sl < pos["sl"]:
                pos["sl"] = new_sl
            return True
        return False

    def cancel_all_orders(self, symbol: str) -> None:
        pass

    # ── Limit orders (S5) ─────────────────────────────────────────── #

    def place_limit_long(self, symbol: str, limit_price: float,
                         sl_price: float, tp_price: float,
                         qty_str: str) -> str:
        order_id = uuid.uuid4().hex[:8]
        self._pending_orders[order_id] = {
            "symbol": symbol, "side": "LONG",
            "limit_price": limit_price, "sl": sl_price,
            "tp": tp_price, "qty_str": qty_str,
            "placed_ts": self.sim_time,
        }
        return order_id

    def place_limit_short(self, symbol: str, limit_price: float,
                          sl_price: float, tp_price: float,
                          qty_str: str) -> str:
        order_id = uuid.uuid4().hex[:8]
        self._pending_orders[order_id] = {
            "symbol": symbol, "side": "SHORT",
            "limit_price": limit_price, "sl": sl_price,
            "tp": tp_price, "qty_str": qty_str,
            "placed_ts": self.sim_time,
        }
        return order_id

    def cancel_order(self, symbol: str, order_id: str) -> None:
        self._pending_orders.pop(order_id, None)

    def get_order_fill(self, symbol: str, order_id: str) -> dict:
        """
        Check if current bar triggered the plan order.
        LONG trigger: fires when mark rises to or above limit_price (from below).
        SHORT trigger: fires when mark falls to or below limit_price (from above).
        """
        order = self._pending_orders.get(order_id)
        if not order:
            return {"status": "cancelled", "fill_price": 0.0}
        mark = self.get_mark_price(symbol)
        lp   = order["limit_price"]
        if order["side"] == "LONG" and mark >= lp:
            return {"status": "filled", "fill_price": lp}
        if order["side"] == "SHORT" and mark <= lp:
            return {"status": "filled", "fill_price": lp}
        return {"status": "live", "fill_price": 0.0}

    # ── S5 exits (called by _handle_limit_filled in non-paper path) ── #

    def _place_s5_exits(self, symbol: str, hold_side: str, qty_str: str,
                        sl_trig: float, sl_exec: float,
                        part_trig: float, tp_targ: float,
                        trail_range_pct: float) -> bool:
        """Store S5 exit params into the position after limit fill."""
        if symbol in self._positions:
            pos = self._positions[symbol]
            pos["sl"]        = sl_trig
            pos["tp_trig"]   = part_trig
            pos["trail_pct"] = trail_range_pct
        return True

    # ── History (used by bot.py close detection in non-paper path) ── #

    def get_history_position(self, symbol: str,
                              open_time_iso: str | None = None,
                              entry_price: float | None = None,
                              retries: int = 1,
                              retry_delay: float = 0) -> dict | None:
        """Return last closed trade's PnL for symbol."""
        for t in reversed(self._closed_trades):
            if t.get("symbol") == symbol:
                return {"pnl": t.get("total_pnl", 0.0),
                        "exit_price": t.get("exit_price"),
                        "close_time": t.get("exit_date")}
        return None

    def get_realized_pnl(self, symbol: str, retries: int = 1,
                         retry_delay: float = 0) -> float | None:
        for t in reversed(self._closed_trades):
            if t.get("symbol") == symbol:
                return t.get("total_pnl", 0.0)
        return None

    def is_partial_closed(self, symbol: str) -> bool:
        return False

    def set_leverage(self, symbol: str, leverage: int) -> None:
        pass

    def drain_partial_closes(self) -> list[dict]:
        """PAPER_MODE path — not called in non-paper backtest mode."""
        return []

    def get_last_close(self, symbol: str) -> dict | None:
        """PAPER_MODE path — not called in non-paper backtest mode."""
        return None

    def tag_strategy(self, symbol: str, strategy: str) -> None:
        """PAPER_MODE path — not called in non-paper backtest mode."""
        pass

    # ── Exit simulation (called per 3m bar, before bot._tick()) ──── #

    def process_bar(self, symbol: str, bar: dict) -> dict | None:
        """
        Simulate exits for symbol against the given OHLCV bar.
        Returns a closed-trade dict if the position closed, else None.
        Also fires partial TP (modifies position in-place, returns None).
        """
        pos = self._positions.get(symbol)
        if pos is None:
            return None

        h    = float(bar["high"])
        l    = float(bar["low"])
        side = pos["side"]

        def _close(exit_price: float, result: str, reason: str) -> dict:
            pnl_dir     = 1 if side == "LONG" else -1
            close_pct   = (exit_price - pos["entry"]) / pos["entry"] * pnl_dir
            close_pnl   = round(close_pct * pos["margin"] * 0.5 * pos["leverage"], 4)
            partial_pnl = pos.get("_partial_pnl", 0.0)
            total_pnl   = round(close_pnl + partial_pnl, 4)
            self._update_balance(total_pnl)
            trade = {
                "symbol":         symbol,
                "strategy":       pos["strategy"],
                "side":           side,
                "entry_price":    pos["entry"],
                "exit_price":     exit_price,
                "sl":             pos["sl"],
                "tp_trig":        pos["tp_trig"],
                "result":         result,
                "exit_reason":    reason,
                "partial_pnl":    partial_pnl,
                "close_pnl":      close_pnl,
                "total_pnl":      total_pnl,
                "margin_pnl_pct": round(total_pnl / pos["margin"] * 100, 2) if pos["margin"] else 0,
                "scale_in":       pos["scale_in_done"] and not pos.get("_no_scale"),
                "candles_held":   (bar["ts"] - pos["open_ts"]) // (3 * 60_000),
                "entry_date":     datetime.fromtimestamp(pos["open_ts"] / 1000, tz=timezone.utc).isoformat(),
                "exit_date":      datetime.fromtimestamp(bar["ts"] / 1000, tz=timezone.utc).isoformat(),
                "margin":         pos["margin"],
                "leverage":       pos["leverage"],
                "trade_id":       pos["trade_id"],
            }
            self._closed_trades.append(trade)
            del self._positions[symbol]
            return trade

        if side == "LONG":
            # SL and TP same bar → SL wins (conservative)
            sl_hit = l <= pos["sl"]
            tp_hit = h >= pos["tp_trig"] and not pos["partial_done"]

            if sl_hit:
                return _close(pos["sl"], "LOSS", "SL")

            if tp_hit:
                # Partial TP: close 50%, activate trail
                partial_exit = pos["tp_trig"]
                pct          = (partial_exit - pos["entry"]) / pos["entry"]
                partial_pnl  = round(pct * pos["margin"] * 0.5 * pos["leverage"], 4)
                self._update_balance(partial_pnl)
                pos["_partial_pnl"] = partial_pnl
                pos["partial_done"] = True
                pos["trail_active"] = True
                pos["trail_peak"]   = partial_exit
                pos["trail_sl"]     = partial_exit * (1 - pos["trail_pct"] / 100)
                pos["qty"]          = round(pos["qty"] * 0.5, 6)
                pos["margin"]       = pos["margin"] * 0.5
                return None  # partial — position still open

            if pos["trail_active"]:
                pos["trail_peak"] = max(pos["trail_peak"], h)
                pos["trail_sl"]   = pos["trail_peak"] * (1 - pos["trail_pct"] / 100)
                if l <= pos["trail_sl"]:
                    return _close(pos["trail_sl"], "WIN", "TRAIL")

        else:  # SHORT
            sl_hit = h >= pos["sl"]
            tp_hit = l <= pos["tp_trig"] and not pos["partial_done"]

            if sl_hit:
                return _close(pos["sl"], "LOSS", "SL")

            if tp_hit:
                partial_exit = pos["tp_trig"]
                pct          = (pos["entry"] - partial_exit) / pos["entry"]
                partial_pnl  = round(pct * pos["margin"] * 0.5 * pos["leverage"], 4)
                self._update_balance(partial_pnl)
                pos["_partial_pnl"] = partial_pnl
                pos["partial_done"] = True
                pos["trail_active"] = True
                pos["trail_peak"]   = partial_exit
                pos["trail_sl"]     = partial_exit * (1 + pos["trail_pct"] / 100)
                pos["qty"]          = round(pos["qty"] * 0.5, 6)
                pos["margin"]       = pos["margin"] * 0.5
                return None

            if pos["trail_active"]:
                pos["trail_peak"] = min(pos["trail_peak"], l)
                pos["trail_sl"]   = pos["trail_peak"] * (1 + pos["trail_pct"] / 100)
                if h >= pos["trail_sl"]:
                    return _close(pos["trail_sl"], "WIN", "TRAIL")

        return None


# ═══════════════════════════════════════════════════════════════════
# MockScanner — replacement for scanner.py
# ═══════════════════════════════════════════════════════════════════

class _Sentiment:
    """Mimics the Sentiment namedtuple returned by the real scanner."""
    def __init__(self, direction: str, green_count: int, red_count: int,
                 total_pairs: int, bullish_weight: float):
        self.direction      = direction
        self.green_count    = green_count
        self.red_count      = red_count
        self.total_pairs    = total_pairs
        self.bullish_weight = bullish_weight


class MockScanner:
    """
    Fake scanner.py. Returns fixed universe + synthetic sentiment.
    Sentiment is derived from how many symbols have 3m close > 1D open at sim_time.
    """

    def __init__(self, universe: list[str], parquet: dict[str, dict]):
        self.universe  = universe
        self.parquet   = parquet
        self.sim_time: int = 0

    def get_qualified_pairs_and_sentiment(self):
        DAY_MS = 86_400_000
        day_open_ts = self.sim_time - (self.sim_time % DAY_MS)
        green = 0
        for sym in self.universe:
            p     = self.parquet.get(sym, {})
            df_3m = p.get("3m")
            df_1d = p.get("1d")
            if df_3m is None or df_1d is None:
                continue
            cur_3m = df_3m[df_3m["ts"] <= self.sim_time]
            cur_1d = df_1d[df_1d["ts"] <= day_open_ts]
            if cur_3m.empty or cur_1d.empty:
                continue
            close_3m   = float(cur_3m.iloc[-1]["close"])
            daily_open = float(cur_1d.iloc[-1]["open"])
            if daily_open > 0 and close_3m > daily_open:
                green += 1

        n     = len(self.universe)
        ratio = green / n if n > 0 else 0.5
        if ratio > 0.60:
            direction = "BULLISH"
        elif ratio < 0.40:
            direction = "BEARISH"
        else:
            direction = "NEUTRAL"

        return self.universe, _Sentiment(
            direction      = direction,
            green_count    = green,
            red_count      = n - green,
            total_pairs    = n,
            bullish_weight = ratio,
        )


# ═══════════════════════════════════════════════════════════════════
# Lightweight module stubs (no-ops)
# ═══════════════════════════════════════════════════════════════════

class _MockSnapshot:
    def save_snapshot(self, *a, **kw): pass


class _MockClaudeFilter:
    def claude_approve(self, *a, **kw): return True


class _MockStartupRecovery:
    def fetch_candles_at(self, *a, **kw):
        return pd.DataFrame()

    def estimate_sl_tp(self, entry, side):
        sl = entry * 0.95 if side == "LONG" else entry * 1.05
        tp = entry * 1.10 if side == "LONG" else entry * 0.90
        return sl, tp, 0.0, 0.0

    def attempt_s5_recovery(self, *a, **kw):
        return None


# ═══════════════════════════════════════════════════════════════════
# BacktestEngine — time loop and module patching
# ═══════════════════════════════════════════════════════════════════

class BacktestEngine:
    """
    Orchestrates the backtest:
    1. Patches sys.modules with mocks before importing bot
    2. Builds a unified 3m timeline
    3. Per bar: runs exit simulation then bot._tick()
    """

    def __init__(self, universe: list[str], parquet: dict[str, dict],
                 balance: float = 1000.0, days: int = 365,
                 enabled_strategies: set | None = None):
        self.universe = universe
        self.parquet  = parquet
        self.balance  = balance
        self.days     = days
        self.enabled  = enabled_strategies or {"S1", "S2", "S3", "S4", "S5", "S6"}
        self._trades: list[dict] = []

    def _patch_modules(self, mock_trader: MockTrader,
                       bs: BacktestState,
                       mock_scanner: MockScanner) -> None:
        """Install mocks into sys.modules before bot.py is imported.

        Originals (if any) are saved into self._module_snapshot so that
        _unpatch_modules() can restore them after the backtest run.
        """
        # backtest.py replaces sys.modules["config"] and "config_sN" with
        # lightweight stubs that lack many attributes.  Restore the real
        # modules from their source files so scanner/bot/strategy get the
        # full attribute sets they need.
        import importlib.util as _ilu
        _root = Path(__file__).parent
        for _mod_name in ["config", "config_s1", "config_s2", "config_s3",
                          "config_s4", "config_s5", "config_s6"]:
            try:
                _existing = sys.modules.get(_mod_name)
                # If missing or a types.ModuleType stub (no __file__), reload from disk
                if _existing is None or getattr(_existing, "__file__", None) is None:
                    _spec = _ilu.spec_from_file_location(
                        _mod_name, _root / f"{_mod_name}.py")
                    _fresh = _ilu.module_from_spec(_spec)
                    _spec.loader.exec_module(_fresh)
                    sys.modules[_mod_name] = _fresh
            except Exception:
                pass

        self._module_snapshot = {}
        _MISSING = object()
        for _name in ("trader", "state", "snapshot", "claude_filter", "startup_recovery"):
            self._module_snapshot[_name] = sys.modules.get(_name, _MISSING)

        sys.modules["trader"]           = mock_trader          # type: ignore[assignment]
        sys.modules["state"]            = bs                   # type: ignore[assignment]
        sys.modules["snapshot"]         = _MockSnapshot()      # type: ignore[assignment]
        sys.modules["claude_filter"]    = _MockClaudeFilter()  # type: ignore[assignment]
        sys.modules["startup_recovery"] = _MockStartupRecovery()  # type: ignore[assignment]

        # Patch scanner module's function directly
        self._scanner_snapshot = None
        try:
            import scanner as _scanner_mod
            self._scanner_snapshot = (_scanner_mod, _scanner_mod.get_qualified_pairs_and_sentiment)
            _scanner_mod.get_qualified_pairs_and_sentiment = \
                mock_scanner.get_qualified_pairs_and_sentiment
        except ImportError:
            pass

    def _unpatch_modules(self) -> None:
        """Restore sys.modules entries saved by _patch_modules."""
        _MISSING = object()
        snapshot = getattr(self, "_module_snapshot", None) or {}
        for _name, _orig in snapshot.items():
            if _orig is _MISSING or _orig is None:
                sys.modules.pop(_name, None)
            else:
                sys.modules[_name] = _orig
        self._module_snapshot = {}

        scanner_snap = getattr(self, "_scanner_snapshot", None)
        if scanner_snap is not None:
            _scanner_mod, _orig_fn = scanner_snap
            _scanner_mod.get_qualified_pairs_and_sentiment = _orig_fn
            self._scanner_snapshot = None

    def _build_timeline(self) -> list[int]:
        """Sorted unique 3m timestamps across all symbols."""
        all_ts: set[int] = set()
        for sym in self.universe:
            df = self.parquet.get(sym, {}).get("3m")
            if df is not None and not df.empty:
                all_ts.update(df["ts"].tolist())
        return sorted(all_ts)

    def run(self) -> list[dict]:
        mock_trader  = MockTrader(self.universe, self.parquet, self.balance)
        bs           = BacktestState()
        mock_scanner = MockScanner(self.universe, self.parquet)

        self._patch_modules(mock_trader, bs, mock_scanner)
        try:
            return self._run_inner(mock_trader, bs, mock_scanner)
        finally:
            self._unpatch_modules()
            sys.modules.pop("bot", None)
            sys.modules.pop("scanner", None)

    def _run_inner(self, mock_trader: MockTrader, bs: BacktestState,
                   mock_scanner: MockScanner) -> list[dict]:
        # Import bot AFTER patching; remove cached modules for test isolation
        sys.modules.pop("bot", None)
        sys.modules.pop("scanner", None)
        import bot as _bot_mod

        # Suppress interactive disclaimer if present
        if hasattr(_bot_mod, "_check_disclaimer"):
            _bot_mod._check_disclaimer = lambda: None

        bot_instance = _bot_mod.MTFBot()

        # Override enabled strategy flags in config_sN modules
        for s_num in range(1, 7):
            mod_name = f"config_s{s_num}"
            try:
                mod  = __import__(mod_name)
                attr = f"S{s_num}_ENABLED"
                if hasattr(mod, attr):
                    setattr(mod, attr, f"S{s_num}" in self.enabled)
            except ImportError:
                pass

        timeline = self._build_timeline()
        total    = len(timeline)
        print(f"\n📊 Backtest: {len(self.universe)} symbols | {total} 3m bars")

        for idx, ts in enumerate(timeline):
            mock_trader.sim_time  = ts
            mock_scanner.sim_time = ts

            # ── Exit simulation: check each open position ──────── #
            for sym in list(mock_trader._positions.keys()):
                df_3m = self.parquet.get(sym, {}).get("3m")
                if df_3m is None:
                    continue
                bar_rows = df_3m[df_3m["ts"] == ts]
                if bar_rows.empty:
                    continue
                bar    = bar_rows.iloc[0].to_dict()
                closed = mock_trader.process_bar(sym, bar)
                if closed:
                    if closed["result"] == "LOSS":
                        day_str = datetime.fromtimestamp(
                            ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                        bs.record_loss(sym, day_str)
                    self._trades.append(closed)

            # ── Check pending limit orders (S5) ───────────────── #
            for order_id, order in list(mock_trader._pending_orders.items()):
                sym  = order["symbol"]
                fill = mock_trader.get_order_fill(sym, order_id)
                if fill["status"] == "filled":
                    bal = mock_trader.get_usdt_balance()
                    try:
                        bot_instance._handle_limit_filled(sym, {
                            **order,
                            "side":              order["side"],
                            "trigger":           order["limit_price"],
                            "sl":                order["sl"],
                            "tp":                order["tp"],
                            "qty_str":           order["qty_str"],
                            "ob_low":            0.0,
                            "ob_high":           0.0,
                            "rr":                0.0,
                            "sentiment":         mock_scanner.get_qualified_pairs_and_sentiment()[1].direction,
                            "sr_clearance_pct":  None,
                        }, fill["fill_price"], bal)
                    except Exception as e:
                        logger.debug(f"[{sym}] _handle_limit_filled error: {e}")
                    mock_trader._pending_orders.pop(order_id, None)

            # ── Force scan every tick ──────────────────────────── #
            bot_instance.last_scan_time = 0

            # ── Pair pause gate ────────────────────────────────── #
            day_str = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            bot_instance.qualified_pairs = [
                s for s in self.universe
                if not bs.is_pair_paused(s, day_str)
            ]

            # ── Run bot tick ───────────────────────────────────── #
            try:
                bot_instance._tick()
            except Exception as e:
                logger.debug(f"[{ts}] tick error: {e}")

            # ── Tag newly opened positions with strategy from state ─ #
            for sym, pos in mock_trader._positions.items():
                if not pos.get("strategy"):
                    ot = bs.get_open_trade(sym)
                    if ot:
                        pos["strategy"] = ot.get("strategy", "")

            if idx % 5000 == 0 and idx > 0:
                pct = idx / total * 100
                print(f"  {pct:.0f}% | bar {idx}/{total} | "
                      f"open={len(mock_trader._positions)} trades={len(self._trades)}")

        # Close any still-open positions at last bar close price (timeout)
        for sym, pos in list(mock_trader._positions.items()):
            mark = mock_trader.get_mark_price(sym)
            if mark == 0:
                continue
            direction   = 1 if pos["side"] == "LONG" else -1
            pct_chg     = (mark - pos["entry"]) / pos["entry"] * direction
            total_pnl   = round(pct_chg * pos["margin"] * pos["leverage"], 4)
            partial_pnl = pos.get("_partial_pnl", 0.0)
            t = {
                "symbol":         sym,
                "strategy":       pos["strategy"],
                "side":           pos["side"],
                "entry_price":    pos["entry"],
                "exit_price":     mark,
                "sl":             pos["sl"],
                "tp_trig":        pos["tp_trig"],
                "result":         "WIN" if total_pnl >= 0 else "LOSS",
                "exit_reason":    "TIMEOUT",
                "partial_pnl":    partial_pnl,
                "close_pnl":      round(total_pnl - partial_pnl, 4),
                "total_pnl":      total_pnl,
                "margin_pnl_pct": round(total_pnl / pos["margin"] * 100, 2) if pos["margin"] else 0,
                "scale_in":       pos["scale_in_done"],
                "candles_held":   (timeline[-1] - pos["open_ts"]) // (3 * 60_000) if timeline else 0,
                "entry_date":     datetime.fromtimestamp(pos["open_ts"] / 1000, tz=timezone.utc).isoformat(),
                "exit_date":      datetime.fromtimestamp(timeline[-1] / 1000, tz=timezone.utc).isoformat() if timeline else "",
                "margin":         pos["margin"],
                "leverage":       pos["leverage"],
                "trade_id":       pos["trade_id"],
            }
            self._trades.append(t)

        self.balance = mock_trader._balance   # expose for report
        print(f"\n✅ Done | {len(self._trades)} trades | "
              f"final balance: {mock_trader._balance:.2f} USDT")
        return self._trades


# ═══════════════════════════════════════════════════════════════════
# HTML report
# ═══════════════════════════════════════════════════════════════════

def _build_report(trades: list[dict], run_time: str, balance_start: float,
                  balance_end: float) -> str:
    """Build a simple HTML report from closed trade list."""
    if not trades:
        return "<html><body><h1>No trades</h1></body></html>"

    df = pd.DataFrame(trades)

    def stats(tlist):
        if not tlist:
            return dict(count=0, wins=0, losses=0, win_rate=0,
                        total_pnl=0, avg_win=0, avg_loss=0, best=0, worst=0)
        t = pd.DataFrame(tlist)
        w = t[t["result"] == "WIN"]
        l = t[t["result"] == "LOSS"]
        return dict(
            count    = len(t),
            wins     = len(w),
            losses   = len(l),
            win_rate = round(len(w) / len(t) * 100, 1),
            total_pnl = round(t["total_pnl"].sum(), 2),
            avg_win   = round(w["total_pnl"].mean(), 2) if len(w) else 0,
            avg_loss  = round(l["total_pnl"].mean(), 2) if len(l) else 0,
            best      = round(t["total_pnl"].max(), 2),
            worst     = round(t["total_pnl"].min(), 2),
        )

    rows_html = ""
    for _, row in df.sort_values("entry_date").iterrows():
        colour = "#2ecc71" if row["result"] == "WIN" else "#e74c3c"
        rows_html += (
            f"<tr style='color:{colour}'>"
            f"<td>{row['strategy']}</td><td>{row['symbol']}</td>"
            f"<td>{row['side']}</td><td>{row['entry_date'][:10]}</td>"
            f"<td>{row['exit_date'][:10]}</td>"
            f"<td>{row['entry_price']:.4f}</td><td>{row['exit_price']:.4f}</td>"
            f"<td>{row['result']}</td><td>{row['exit_reason']}</td>"
            f"<td>{row['total_pnl']:+.2f}</td>"
            f"<td>{row['margin_pnl_pct']:+.1f}%</td>"
            f"</tr>\n"
        )

    by_strategy = {}
    for s in ["S1", "S2", "S3", "S4", "S5", "S6"]:
        by_strategy[s] = stats([t for t in trades if t["strategy"] == s])
    overall = stats(trades)

    summary = "".join(
        f"<tr><td>{s}</td><td>{v['count']}</td><td>{v['wins']}</td>"
        f"<td>{v['losses']}</td><td>{v['win_rate']}%</td>"
        f"<td>{v['total_pnl']:+.2f}</td>"
        f"<td>{v['avg_win']:+.2f}</td><td>{v['avg_loss']:+.2f}</td></tr>\n"
        for s, v in by_strategy.items() if v["count"] > 0
    )

    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<title>Backtest Engine Report</title>
<style>body{{font-family:monospace;background:#111;color:#eee;padding:20px}}
table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #333;padding:4px 8px;text-align:left}}
th{{background:#222}}h1,h2{{color:#f39c12}}</style></head><body>
<h1>Backtest Engine Report</h1>
<p>Run: {run_time} | Balance: {balance_start:.0f} → {balance_end:.2f} USDT
({(balance_end - balance_start) / balance_start * 100:+.1f}%)</p>
<h2>Summary by Strategy</h2>
<table><tr><th>Strategy</th><th>Trades</th><th>Wins</th><th>Losses</th>
<th>WR%</th><th>Total PnL</th><th>Avg Win</th><th>Avg Loss</th></tr>
{summary}
<tr style='font-weight:bold'><td>TOTAL</td><td>{overall['count']}</td>
<td>{overall['wins']}</td><td>{overall['losses']}</td>
<td>{overall['win_rate']}%</td><td>{overall['total_pnl']:+.2f}</td>
<td>{overall['avg_win']:+.2f}</td><td>{overall['avg_loss']:+.2f}</td></tr>
</table>
<h2>All Trades</h2>
<table><tr><th>Strat</th><th>Symbol</th><th>Side</th><th>Entry</th><th>Exit</th>
<th>Entry $</th><th>Exit $</th><th>Result</th><th>Reason</th>
<th>PnL (USDT)</th><th>Margin PnL%</th></tr>
{rows_html}</table></body></html>"""


# ═══════════════════════════════════════════════════════════════════
# CLI entry point
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Unified S1–S6 backtest engine using real bot.py tick loop"
    )
    parser.add_argument("--days",     type=int,   default=365,
                        help="Lookback window in days (default 365)")
    parser.add_argument("--balance",  type=float, default=1000.0,
                        help="Starting USDT balance (default 1000)")
    parser.add_argument("--symbols",  nargs="*",
                        help="Override symbol universe (default: all data/daily/*.parquet)")
    parser.add_argument("--no-fetch", action="store_true",
                        help="Skip parquet cache updates, use existing data only")
    parser.add_argument("--fetch-only", action="store_true",
                        help="Download/update all parquet caches then exit (no backtest run)")
    parser.add_argument("--output",   default="backtest_engine_report.html",
                        help="Output HTML report filename")
    for s in range(1, 7):
        parser.add_argument(f"--s{s}-only", action="store_true",
                            help=f"Run S{s} strategy only")
    args = parser.parse_args()

    # Determine enabled strategies
    only_flags = [f"s{i}_only" for i in range(1, 7) if getattr(args, f"s{i}_only", False)]
    if only_flags:
        enabled = {f"S{f[1]}" for f in only_flags}
    else:
        enabled = {"S1", "S2", "S3", "S4", "S5", "S6"}

    # Build symbol universe
    if args.symbols:
        universe = args.symbols
    else:
        universe = sorted(p.stem for p in Path("data/daily").glob("*.parquet"))

    if not universe:
        print("❌ No symbols found in data/daily/. Run backtest.py first to populate cache.")
        return

    print(f"📦 Universe: {len(universe)} symbols | {args.days} days | "
          f"balance={args.balance} | strategies={enabled}")

    # Load / update parquet caches
    import backtest as bt
    parquet: dict[str, dict] = {}
    print("\n⬇️  Loading candle data...")
    for sym in universe:
        p: dict = {}
        try:
            if args.no_fetch:
                for tf, cache_dir in [
                    ("1d",  bt._DAILY_CACHE),
                    ("15m", bt._CACHE_15M),
                    ("1h",  bt._CACHE_1H),
                    ("3m",  bt._CACHE_3M),
                ]:
                    path = cache_dir / f"{sym}.parquet"
                    if path.exists():
                        p[tf] = pd.read_parquet(path)
            else:
                p["1d"]  = bt.load_daily(sym, days=args.days)
                p["15m"] = bt.load_15m(sym,   days=args.days)
                p["1h"]  = bt.load_1h(sym,    days=args.days)
                p["3m"]  = bt.load_3m(sym,    days=args.days)
        except Exception as e:
            print(f"  ⚠️  {sym}: data load error: {e}")
            continue

        df_3m = p.get("3m")
        if df_3m is None or df_3m.empty:
            print(f"  ⚠️  {sym}: no 3m data — skipping")
            continue
        parquet[sym] = p

    valid_universe = [s for s in universe if s in parquet]
    print(f"✅ Loaded {len(valid_universe)}/{len(universe)} symbols with 3m data\n")

    if args.fetch_only:
        print("📦 --fetch-only: cache update complete, skipping backtest.")
        return

    # Run engine
    engine = BacktestEngine(
        universe           = valid_universe,
        parquet            = parquet,
        balance            = args.balance,
        days               = args.days,
        enabled_strategies = enabled,
    )
    run_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    trades   = engine.run()

    # Write report
    report = _build_report(
        trades,
        run_time      = run_time,
        balance_start = args.balance,
        balance_end   = engine.balance,
    )
    Path(args.output).write_text(report, encoding="utf-8")
    print(f"\n📄 Report → {args.output}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    main()
