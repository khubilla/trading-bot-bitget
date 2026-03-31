"""
IG CFD S5 Bot — Wall Street Cash (US30) only.

Runs S5 (SMC Order Block Pullback) strategy via IG.com REST API.
Completely independent from bot.py / trader.py / bitget_client.py.

Usage:
  python ig_bot.py           # live mode (uses IG_ACC_TYPE from env)
  python ig_bot.py --paper   # paper simulation, no real orders
"""
import csv
import json
import logging
import os
import signal
import sys
import time
import uuid
import zoneinfo
from datetime import datetime, timezone

import pandas as pd
import ig_client as ig
import config_ig

# Apply US30-specific S5 params — must happen before strategy.evaluate_s5() is imported
# or called, since evaluate_s5() does `from config_s5 import ...` at call time.
import config_s5 as _cs5_orig
import config_ig_s5 as _cs5_ig
_base_attrs = {a for a in dir(_cs5_orig) if not a.startswith('_')}
for _attr in [a for a in dir(_cs5_ig) if not a.startswith('_')]:
    if _attr not in _base_attrs:
        raise AttributeError(
            f"config_ig_s5.{_attr} has no matching attribute in config_s5 — "
            f"check for a typo in config_ig_s5.py"
        )
    setattr(_cs5_orig, _attr, getattr(_cs5_ig, _attr))
del _cs5_orig, _cs5_ig, _attr, _base_attrs

from config_ig_s5 import (
    S5_DAILY_EMA_FAST, S5_DAILY_EMA_SLOW,
    S5_USE_CANDLE_STOPS, S5_SL_BUFFER_PCT, S5_SWING_LOOKBACK, S5_MAX_ENTRY_BUFFER,
    S5_LTF_INTERVAL, S5_OB_INVALIDATION_BUFFER_PCT,
)
from strategy import evaluate_s5, find_swing_low_target, find_swing_high_target, calculate_ema

# ── Logging ──────────────────────────────────────────────────── #
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    handlers=[
        logging.FileHandler(config_ig.LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────── #
EPIC          = config_ig.EPIC
CONTRACT_SIZE = config_ig.CONTRACT_SIZE
PARTIAL_SIZE  = config_ig.PARTIAL_SIZE
POINT_VALUE   = config_ig.POINT_VALUE
ET            = zoneinfo.ZoneInfo("America/New_York")

# ── CSV fields ───────────────────────────────────────────────── #
_TRADE_FIELDS = [
    "timestamp", "trade_id", "action",
    "side", "qty", "entry", "sl", "tp",
    "snap_entry_trigger", "snap_sl", "snap_rr",
    "snap_s5_ob_low", "snap_s5_ob_high", "snap_s5_tp",
    "result", "pnl", "exit_reason",
    "session_date", "mode",
]


def _log_trade(action: str, details: dict, paper: bool = False) -> None:
    row = {
        "timestamp":    datetime.now(timezone.utc).isoformat(),
        "action":       action,
        "mode":         "PAPER" if paper else "LIVE",
        "session_date": _now_et().strftime("%Y-%m-%d"),
        **details,
    }
    write_header = not os.path.exists(config_ig.TRADE_LOG)
    with open(config_ig.TRADE_LOG, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_TRADE_FIELDS,
                           extrasaction="ignore", restval="")
        if write_header:
            w.writeheader()
        w.writerow(row)


# ── Trading session helpers ──────────────────────────────────── #

def _now_et() -> datetime:
    return datetime.now(ET)


def _in_trading_window(now: datetime) -> bool:
    """True if current ET time is within [SESSION_START, SESSION_END)."""
    start = now.replace(hour=config_ig.SESSION_START[0],
                        minute=config_ig.SESSION_START[1],
                        second=0, microsecond=0)
    end   = now.replace(hour=config_ig.SESSION_END[0],
                        minute=config_ig.SESSION_END[1],
                        second=0, microsecond=0)
    return start <= now < end


def _is_session_end(now: datetime) -> bool:
    """True once we hit or pass SESSION_END on a weekday."""
    return (now.weekday() < 5 and
            now.hour == config_ig.SESSION_END[0] and
            now.minute >= config_ig.SESSION_END[1])


def _entry_in_window(sig: str, mark: float, trigger: float) -> bool:
    """Reject stale or over-extended entries — mirrors bot.py S5 gate."""
    if sig == "LONG":
        if mark < trigger:
            logger.info(f"[S5] LONG stale — mark {mark:.1f} < trigger {trigger:.1f}")
            return False
        if mark > trigger * (1 + S5_MAX_ENTRY_BUFFER):
            logger.info(f"[S5] LONG entry missed — price {mark:.1f} too far past trigger {trigger:.1f}")
            return False
    else:
        if mark > trigger:
            logger.info(f"[S5] SHORT stale — mark {mark:.1f} > trigger {trigger:.1f}")
            return False
        if mark < trigger * (1 - S5_MAX_ENTRY_BUFFER):
            logger.info(f"[S5] SHORT entry missed — price {mark:.1f} too far past trigger {trigger:.1f}")
            return False
    return True


# ── PnL helpers ──────────────────────────────────────────────── #

def _calc_pnl(pos: dict, exit_price: float) -> float:
    """USD PnL. US30: $1/point per contract."""
    qty = pos.get("current_qty", pos.get("initial_qty", CONTRACT_SIZE))
    if pos["side"] == "LONG":
        return (exit_price - pos["entry"]) * qty * POINT_VALUE
    return (pos["entry"] - exit_price) * qty * POINT_VALUE


def _calc_partial_pnl(pos: dict, close_price: float) -> float:
    """PnL for the partial (PARTIAL_SIZE contracts)."""
    if pos["side"] == "LONG":
        return (close_price - pos["entry"]) * PARTIAL_SIZE * POINT_VALUE
    return (pos["entry"] - close_price) * PARTIAL_SIZE * POINT_VALUE


# ── Paper state ──────────────────────────────────────────────── #

class _PaperState:
    """
    In-memory paper trading simulation.
    No exchange calls for orders — just simulates SL/TP/partial by comparing
    mark price to stored levels each tick.
    Persisted to STATE_FILE for restart survival.
    """

    def __init__(self):
        self.balance  = 10_000.0
        self.position: dict | None = None
        self._load()

    def _load(self) -> None:
        if os.path.exists(config_ig.STATE_FILE):
            try:
                with open(config_ig.STATE_FILE) as f:
                    data = json.load(f)
                self.balance  = float(data.get("balance", self.balance))
                self.position = data.get("position")
            except Exception:
                pass

    def _save(self) -> None:
        with open(config_ig.STATE_FILE, "w") as f:
            json.dump({"balance": self.balance, "position": self.position}, f, indent=2)

    def open(self, side: str, entry: float, sl: float,
             tp1: float, tp: float, qty: float,
             trade_id: str, ob_low: float, ob_high: float) -> dict:
        self.position = {
            "side":         side,
            "deal_id":      f"PAPER-{trade_id}",
            "entry":        entry,
            "sl":           sl,
            "tp1":          tp1,
            "tp":           tp,
            "initial_qty":  qty,
            "current_qty":  qty,
            "partial_done": False,
            "trade_id":     trade_id,
            "opened_at":    _now_et().isoformat(),
            "ob_low":       ob_low,
            "ob_high":      ob_high,
        }
        self._save()
        return self.position

    def check_sl_tp(self, mark: float) -> str | None:
        """Returns 'SL' or 'TP' if hit, else None."""
        if not self.position:
            return None
        pos  = self.position
        side = pos["side"]
        sl   = pos["sl"]
        tp   = pos["tp"]
        if side == "LONG":
            if mark <= sl:
                return "SL"
            if mark >= tp:
                return "TP"
        else:
            if mark >= sl:
                return "SL"
            if mark <= tp:
                return "TP"
        return None

    def check_partial(self, mark: float) -> bool:
        """Returns True if TP1 is hit and partial not yet done."""
        if not self.position or self.position["partial_done"]:
            return False
        pos  = self.position
        tp1  = pos["tp1"]
        if pos["side"] == "LONG"  and mark >= tp1:
            return True
        if pos["side"] == "SHORT" and mark <= tp1:
            return True
        return False

    def do_partial(self, mark: float) -> float:
        """Execute paper partial close. Returns partial PnL."""
        pos = self.position
        partial_pnl = _calc_partial_pnl(pos, mark)
        pos["partial_done"] = True
        pos["current_qty"]  = pos["initial_qty"] - PARTIAL_SIZE
        pos["sl"]           = pos["entry"]   # breakeven
        self.balance       += partial_pnl
        self._save()
        return partial_pnl

    def do_close(self, exit_price: float) -> float:
        """Execute paper full close. Returns close PnL."""
        pos = self.position
        pnl = _calc_pnl(pos, exit_price)
        self.balance  += pnl
        self.position  = None
        self._save()
        return pnl

    def update_sl(self, new_sl: float) -> None:
        if self.position:
            self.position["sl"] = new_sl
            self._save()


# ── Main bot ─────────────────────────────────────────────────── #

class IGBot:

    def __init__(self, paper: bool = False):
        self.paper       = paper
        self.running     = True
        self.position: dict | None = None   # live position state dict
        self.pending_order: dict | None = None
        # Structure: {"deal_id": str, "side": str, "ob_low": float, "ob_high": float,
        #              "sl": float, "tp": float, "trigger": float, "size": float,
        #              "expires": float}
        self._paper      = _PaperState() if paper else None
        # Candle cache: fetch full history once, then append new candles only.
        # IG limits historical price data to 10,000 points/week — fetching 550
        # points every 45s would exceed that in ~4 minutes.
        self._candle_cache: dict[str, pd.DataFrame] = {}

        mode = "PAPER" if paper else f"LIVE ({config_ig.IG_ACC_TYPE})"
        logger.info(
            f"IGBot starting | mode={mode} | epic={EPIC} | "
            f"session={config_ig.SESSION_START[0]:02d}:{config_ig.SESSION_START[1]:02d}–"
            f"{config_ig.SESSION_END[0]:02d}:{config_ig.SESSION_END[1]:02d} ET | "
            f"size={CONTRACT_SIZE}"
        )

        if paper:
            # Restore position from paper state if any
            self.position = self._paper.position
            # Restore pending order from state file if present
            if os.path.exists(config_ig.STATE_FILE):
                try:
                    with open(config_ig.STATE_FILE) as _f:
                        _data = json.load(_f)
                    self.pending_order = _data.get("pending_order")
                except Exception:
                    pass
        else:
            self._sync_live_position()

    def _get_candles(self, interval: str, limit: int) -> pd.DataFrame:
        """
        Return candles from in-memory cache.  Only calls IG's price history API
        when a new candle has formed since the last fetch — keeping weekly data
        point usage well under IG's 10,000-point limit.

        Cold start (cache empty): fetches full history (one-time cost).
        Subsequent ticks: fetches 3 candles only when the next candle period
        has opened, then appends & deduplicates.
        """
        interval_ms = {"1D": 86_400_000, "1H": 3_600_000, "15m": 900_000}.get(interval, 60_000)
        now_ms      = int(time.time() * 1000)
        cached      = self._candle_cache.get(interval)

        if cached is None or cached.empty:
            df = ig.get_candles(EPIC, interval, limit)
            if not df.empty:
                self._candle_cache[interval] = df
            return df

        last_candle_ts = int(cached["ts"].iloc[-1])
        if now_ms < last_candle_ts + interval_ms:
            return cached   # current candle still open — no new data

        # A new candle period has started — fetch the last 3 to catch any missed
        fresh = ig.get_candles(EPIC, interval, 3)
        if fresh.empty:
            return cached
        combined = (
            pd.concat([cached, fresh])
            .drop_duplicates("ts")
            .sort_values("ts")
            .reset_index(drop=True)
            .tail(limit)
            .reset_index(drop=True)
        )
        self._candle_cache[interval] = combined
        return combined

    def _sync_live_position(self) -> None:
        """On startup, restore position and pending_order from STATE_FILE."""
        if not os.path.exists(config_ig.STATE_FILE):
            return
        try:
            with open(config_ig.STATE_FILE) as f:
                data = json.load(f)
            saved = data.get("position")
            if saved:
                deal_id = saved.get("deal_id", "")
                live    = ig.get_open_position(deal_id)
                if live:
                    self.position = saved
                    logger.info(f"Restored position from state file: {deal_id}")
                else:
                    logger.info("State file has position but it's no longer open — clearing")
                    self._clear_state()
            saved_pending = data.get("pending_order")
            if saved_pending:
                self.pending_order = saved_pending
                logger.info(f"Restored pending order from state file: {saved_pending.get('deal_id')}")
        except Exception as e:
            logger.warning(f"Could not restore state: {e}")

    def _save_state(self) -> None:
        with open(config_ig.STATE_FILE, "w") as f:
            json.dump({"position": self.position, "pending_order": self.pending_order}, f, indent=2)

    def _clear_state(self) -> None:
        self.position = None
        self._save_state()  # write {"position": null} so dashboard heartbeat stays fresh

    def _heartbeat(self) -> None:
        """Touch state file every tick so dashboard knows the bot is alive."""
        if not os.path.exists(config_ig.STATE_FILE) or not self.position:
            self._save_state()

    def run(self) -> None:
        signal.signal(signal.SIGINT,  self.stop)
        signal.signal(signal.SIGTERM, self.stop)

        while self.running:
            try:
                self._tick()
            except Exception as e:
                logger.error(f"Tick error: {e}", exc_info=True)
            time.sleep(config_ig.POLL_INTERVAL_SEC)

    def stop(self, *_) -> None:
        logger.info("Shutting down IGBot")
        self.running = False

    # ── Main tick ─────────────────────────────────────────────── #

    def _tick(self) -> None:
        self._heartbeat()
        now = _now_et()

        # 1. Session-end force close (handles both open position and pending order)
        if (self.position or self.pending_order) and _is_session_end(now):
            self._session_end_close()
            return

        # 2. Monitor open position (always, even outside entry window)
        if self.position:
            self._monitor_position()

        # 3. Outside session window or weekend → no new entries
        if not _in_trading_window(now):
            logger.debug(f"Outside trading window ({now.strftime('%H:%M')} ET)")
            return
        if now.weekday() >= 5:
            logger.info("Weekend — no new entries")
            return

        # 4. Already in a trade
        if self.position:
            return

        # 4b. Check pending working order (returns early to avoid new entry evaluation)
        if self.pending_order is not None:
            mark = ig.get_mark_price(EPIC)
            if mark > 0:
                self._check_pending_order(mark)
            return

        # 5. Fetch candles (cached — only hits API when new candle has formed)
        daily_df = self._get_candles("1D",  config_ig.DAILY_LIMIT)
        htf_df   = self._get_candles("1H",  config_ig.HTF_LIMIT)
        m15_df   = self._get_candles("15m", config_ig.M15_LIMIT)

        if daily_df.empty or htf_df.empty or m15_df.empty:
            logger.warning("Candle fetch returned empty — skipping tick")
            return

        # 6. Derive allowed_direction from daily EMA
        #    (go_short = allowed_direction == "BEARISH" in strategy.py line 1089
        #     so NEUTRAL only enables LONG — we must set explicitly)
        ema_fast = float(calculate_ema(daily_df["close"].astype(float), S5_DAILY_EMA_FAST).iloc[-1])
        ema_slow = float(calculate_ema(daily_df["close"].astype(float), S5_DAILY_EMA_SLOW).iloc[-1])
        allowed_direction = "BULLISH" if ema_fast > ema_slow else "BEARISH"

        # 7. Evaluate S5
        sig, trigger, sl, tp, ob_low, ob_high, reason = evaluate_s5(
            EPIC, daily_df, htf_df, m15_df, allowed_direction,
        )
        logger.info(f"[S5] {reason}")

        if sig not in ("PENDING_LONG", "PENDING_SHORT"):
            return

        # 8. Get mark price for limit order placement
        mark = ig.get_mark_price(EPIC)
        if mark <= 0:
            return

        # 9. Place limit working order
        side = "LONG" if sig == "PENDING_LONG" else "SHORT"
        sl   = round(sl, 1)
        tp   = round(tp, 1) if tp else round(trigger + abs(trigger - sl) if side == "LONG" else trigger - abs(trigger - sl), 1)
        try:
            if side == "LONG":
                deal_id = ig.place_limit_long(EPIC, trigger, sl, tp, CONTRACT_SIZE)
            else:
                deal_id = ig.place_limit_short(EPIC, trigger, sl, tp, CONTRACT_SIZE)
        except Exception as e:
            logger.error(f"[S5] Failed to place limit {side}: {e}")
            return
        self.pending_order = {
            "deal_id":  deal_id,
            "side":     side,
            "ob_low":   ob_low,
            "ob_high":  ob_high,
            "sl":       sl,
            "tp":       tp,
            "trigger":  trigger,
            "size":     CONTRACT_SIZE,
            "expires":  time.time() + 4 * 3600,
        }
        self._save_state()
        logger.info(
            f"[S5] {side} limit order placed | trigger={trigger:.1f} | "
            f"SL={sl:.1f} | TP={tp:.1f} | deal_id={deal_id}"
        )

    # ── Open trade ─────────────────────────────────────────────── #

    def _open_trade(self, sig: str, sl: float, tp: float,
                    ob_low: float, ob_high: float,
                    trigger: float, mark: float) -> None:
        risk = abs(mark - sl)
        if risk <= 0:
            logger.warning("[S5] risk=0, skipping")
            return

        tp1 = round(mark + risk if sig == "LONG" else mark - risk, 1)
        # Round SL/TP to nearest whole point (US30 convention)
        sl  = round(sl, 1)
        tp  = round(tp, 1) if tp else tp1

        trade_id   = uuid.uuid4().hex[:8]
        close_dir  = "SELL" if sig == "LONG" else "BUY"

        try:
            if self.paper:
                trade = self._paper.open(sig, mark, sl, tp1, tp,
                                         CONTRACT_SIZE, trade_id, ob_low, ob_high)
                self.position = self._paper.position
            else:
                if sig == "LONG":
                    trade = ig.open_long(EPIC, sl, tp1, tp)
                else:
                    trade = ig.open_short(EPIC, sl, tp1, tp)
                self.position = {
                    "side":         sig,
                    "deal_id":      trade["deal_id"],
                    "entry":        trade["entry"],
                    "sl":           sl,
                    "tp1":          tp1,
                    "tp":           tp,
                    "initial_qty":  CONTRACT_SIZE,
                    "current_qty":  CONTRACT_SIZE,
                    "partial_done": False,
                    "trade_id":     trade_id,
                    "opened_at":    _now_et().isoformat(),
                    "ob_low":       ob_low,
                    "ob_high":      ob_high,
                }
                self._save_state()
        except Exception as e:
            logger.error(f"[S5] Failed to open {sig}: {e}")
            return

        entry = self.position["entry"] if self.paper else trade["entry"]
        rr    = round(abs(tp - entry) / risk, 2) if risk > 0 else 0

        _log_trade(f"S5_{sig}", {
            "trade_id":           trade_id,
            "side":               sig,
            "qty":                CONTRACT_SIZE,
            "entry":              round(entry, 1),
            "sl":                 sl,
            "tp":                 tp,
            "snap_entry_trigger": round(trigger, 1),
            "snap_sl":            sl,
            "snap_rr":            rr,
            "snap_s5_ob_low":     round(ob_low, 1),
            "snap_s5_ob_high":    round(ob_high, 1),
            "snap_s5_tp":         tp,
        }, paper=self.paper)

        logger.info(
            f"[S5] {sig} opened | entry={entry:.1f} | SL={sl:.1f} | "
            f"TP1={tp1:.1f} | TP={tp:.1f} | size={CONTRACT_SIZE} | R:R={rr}"
        )

    # ── Pending working order management ──────────────────────── #

    def _check_pending_order(self, mark: float) -> bool:
        """
        Check status of the pending GTC working order.
        Returns True if the pending order was handled (filled, cancelled, or still live).
        Returns False if there is no pending order.
        """
        if self.pending_order is None:
            return False

        deal_id = self.pending_order["deal_id"]
        side    = self.pending_order["side"]

        try:
            status_info = ig.get_working_order_status(deal_id)
            status = status_info["status"]
        except Exception as e:
            logger.warning(f"[S5] _check_pending_order: status check failed ({e}), retrying next tick")
            return True

        if status == "filled":
            fill_price = status_info["fill_price"] or self.pending_order["trigger"]
            self._handle_pending_filled(fill_price)
            self.pending_order = None
            self._save_state()
            return True

        elif status == "open":
            # Check OB invalidation
            if side == "LONG" and mark < self.pending_order["ob_low"] * (1 - S5_OB_INVALIDATION_BUFFER_PCT):
                try:
                    ig.cancel_working_order(deal_id)
                except Exception as e:
                    logger.warning(f"[S5] cancel_working_order error: {e}")
                logger.info(f"[S5] OB invalidated — cancelled limit order {deal_id}")
                self.pending_order = None
                self._save_state()
            elif side == "SHORT" and mark > self.pending_order["ob_high"] * (1 + S5_OB_INVALIDATION_BUFFER_PCT):
                try:
                    ig.cancel_working_order(deal_id)
                except Exception as e:
                    logger.warning(f"[S5] cancel_working_order error: {e}")
                logger.info(f"[S5] OB invalidated — cancelled limit order {deal_id}")
                self.pending_order = None
                self._save_state()
            elif time.time() > self.pending_order["expires"]:
                try:
                    ig.cancel_working_order(deal_id)
                except Exception as e:
                    logger.warning(f"[S5] cancel_working_order error: {e}")
                logger.info(f"[S5] Limit order expired — cancelled {deal_id}")
                self.pending_order = None
                self._save_state()
            return True  # still pending (or just cleared)

        elif status == "deleted":
            logger.info(f"[S5] Limit order deleted externally: {deal_id}")
            self.pending_order = None
            self._save_state()
            return True

        elif status == "unknown":
            # Transient error — leave pending_order as-is, retry next tick
            return True

        return False

    def _handle_pending_filled(self, fill_price: float) -> None:
        """
        Called when the GTC limit order fills.
        Sets self.position (matching the structure _monitor_position expects)
        and logs the trade to CSV.
        """
        po       = self.pending_order
        side     = po["side"]
        sl       = po["sl"]
        tp       = po["tp"]
        trigger  = po["trigger"]
        ob_low   = po["ob_low"]
        ob_high  = po["ob_high"]
        size     = po["size"]

        risk = abs(fill_price - sl)
        tp1  = round(fill_price + risk if side == "LONG" else fill_price - risk, 1)

        trade_id = uuid.uuid4().hex[:8]

        if self.paper:
            trade = self._paper.open(side, fill_price, sl, tp1, tp,
                                     size, trade_id, ob_low, ob_high)
            self.position = self._paper.position
        else:
            self.position = {
                "side":         side,
                "deal_id":      po["deal_id"],
                "entry":        fill_price,
                "sl":           sl,
                "tp1":          tp1,
                "tp":           tp,
                "initial_qty":  size,
                "current_qty":  size,
                "partial_done": False,
                "trade_id":     trade_id,
                "opened_at":    _now_et().isoformat(),
                "ob_low":       ob_low,
                "ob_high":      ob_high,
            }
            self._save_state()

        rr = round(abs(tp - fill_price) / risk, 2) if risk > 0 else 0

        _log_trade(f"S5_{side}", {
            "trade_id":           trade_id,
            "side":               side,
            "qty":                size,
            "entry":              round(fill_price, 1),
            "sl":                 sl,
            "tp":                 tp,
            "snap_entry_trigger": round(trigger, 1),
            "snap_sl":            sl,
            "snap_rr":            rr,
            "snap_s5_ob_low":     round(ob_low, 1),
            "snap_s5_ob_high":    round(ob_high, 1),
            "snap_s5_tp":         tp,
        }, paper=self.paper)

        logger.info(
            f"[S5] {side} limit order FILLED | entry={fill_price:.1f} | "
            f"SL={sl:.1f} | TP1={tp1:.1f} | TP={tp:.1f} | size={size} | R:R={rr}"
        )

    # ── Monitor open position ──────────────────────────────────── #

    def _monitor_position(self) -> None:
        pos  = self.position
        mark = ig.get_mark_price(EPIC)
        if mark <= 0:
            return

        if self.paper:
            # Check partial
            if self._paper.check_partial(mark):
                self._handle_partial_close(mark)
                return

            # Check SL/TP
            hit = self._paper.check_sl_tp(mark)
            if hit:
                self._handle_position_closed(mark, exit_reason=hit)
                return

            # Swing trail after partial
            if pos["partial_done"] and S5_USE_CANDLE_STOPS:
                self._trail_sl_candle(mark)

        else:
            # Live: sync from exchange
            live = ig.get_open_position(pos["deal_id"])
            if live is None:
                self._handle_position_closed(mark, exit_reason="SL_OR_TP")
                return

            # Detect bot-driven partial: mark crossed TP1
            if not pos["partial_done"]:
                tp1  = pos["tp1"]
                if (pos["side"] == "LONG"  and mark >= tp1) or \
                   (pos["side"] == "SHORT" and mark <= tp1):
                    self._handle_partial_close(mark)
                    return

            # Swing trail after partial
            if pos["partial_done"] and S5_USE_CANDLE_STOPS:
                self._trail_sl_candle(mark)

        upnl = _calc_pnl(pos, mark)
        logger.info(
            f"[S5] {pos['side']} | entry={pos['entry']:.1f} | mark={mark:.1f} | "
            f"uPnL={upnl:+.2f} | qty={pos['current_qty']} | SL={pos['sl']:.1f}"
        )

    def _handle_partial_close(self, mark: float) -> None:
        pos       = self.position
        close_dir = "SELL" if pos["side"] == "LONG" else "BUY"

        if self.paper:
            partial_pnl = self._paper.do_partial(mark)
            self.position = self._paper.position
        else:
            ok = ig.partial_close(pos["deal_id"], PARTIAL_SIZE, close_dir)
            if not ok:
                logger.error("[S5] partial_close failed — will retry next tick")
                return
            partial_pnl = _calc_partial_pnl(pos, mark)
            # Move SL to breakeven
            ig.update_sl(pos["deal_id"], pos["entry"])
            pos["sl"]           = pos["entry"]
            pos["partial_done"] = True
            pos["current_qty"]  = pos["initial_qty"] - PARTIAL_SIZE
            self._save_state()

        _log_trade("S5_PARTIAL", {
            "trade_id":    pos["trade_id"],
            "side":        pos["side"],
            "qty":         PARTIAL_SIZE,
            "entry":       round(pos["entry"], 1),
            "pnl":         round(partial_pnl, 2),
            "result":      "WIN" if partial_pnl >= 0 else "LOSS",
            "exit_reason": "PARTIAL_TP",
        }, paper=self.paper)

        logger.info(
            f"[S5] Partial close {PARTIAL_SIZE} contracts @ {mark:.1f} | "
            f"SL → breakeven {pos['entry']:.1f} | PnL={partial_pnl:+.2f}"
        )

    def _trail_sl_candle(self, mark: float) -> None:
        """Trail SL to previous completed 15m candle low (LONG) or high (SHORT)."""
        pos = self.position
        try:
            cs_df = ig.get_candles(EPIC, S5_LTF_INTERVAL, limit=S5_SWING_LOOKBACK + 5)
            if cs_df.empty or len(cs_df) < 3:
                return

            if pos["side"] == "LONG":
                raw      = find_swing_low_target(cs_df, mark, lookback=S5_SWING_LOOKBACK)
                new_sl   = round(raw * (1 - S5_SL_BUFFER_PCT), 1) if raw else None
                improves = new_sl is not None and new_sl > pos["sl"]
            else:
                raw      = find_swing_high_target(cs_df, mark, lookback=S5_SWING_LOOKBACK)
                new_sl   = round(raw * (1 + S5_SL_BUFFER_PCT), 1) if raw else None
                improves = new_sl is not None and new_sl < pos["sl"]

            if not improves:
                return

            if self.paper:
                self._paper.update_sl(new_sl)
                self.position["sl"] = new_sl
            else:
                if ig.update_sl(pos["deal_id"], new_sl):
                    pos["sl"] = new_sl
                    self._save_state()

            logger.info(
                f"[S5] Swing trail: SL → {new_sl:.1f} "
                f"(was {pos['sl']:.1f}, "
                f"{'low' if pos['side'] == 'LONG' else 'high'} ±{S5_SL_BUFFER_PCT*100:.1f}%)"
            )
        except Exception as e:
            logger.error(f"Swing trail error: {e}")

    def _handle_position_closed(self, mark: float, exit_reason: str = "SL_OR_TP") -> None:
        pos = self.position

        if self.paper:
            # Use SL or TP as exit price for accurate PnL
            if exit_reason == "SL":
                exit_price = pos["sl"]
            elif exit_reason == "TP":
                exit_price = pos["tp"]
            else:
                exit_price = mark
            realized = self._paper.do_close(exit_price)
        else:
            realized = ig.get_realized_pnl(pos["deal_id"])
            if realized is None:
                realized = _calc_pnl(pos, mark)

        result = "WIN" if realized >= 0 else "LOSS"

        _log_trade("S5_CLOSE", {
            "trade_id":    pos["trade_id"],
            "side":        pos["side"],
            "qty":         pos["current_qty"],
            "entry":       round(pos["entry"], 1),
            "pnl":         round(realized, 2),
            "result":      result,
            "exit_reason": exit_reason,
        }, paper=self.paper)

        logger.info(
            f"[S5] {result} closed | PnL={realized:+.2f} | reason={exit_reason}"
        )
        self.position = None
        if not self.paper:
            self._clear_state()

    # ── Session-end force close ────────────────────────────────── #

    def _session_end_close(self) -> None:
        # Cancel any pending working order first
        if self.pending_order is not None:
            pending_deal_id = self.pending_order["deal_id"]
            try:
                ig.cancel_working_order(pending_deal_id)
                logger.info(f"[SESSION END] Cancelled pending limit order {pending_deal_id}")
            except Exception as e:
                logger.warning(f"[SESSION END] Failed to cancel pending order: {e}")
            # Check if the order filled despite the cancel attempt
            try:
                status_info = ig.get_working_order_status(pending_deal_id)
                if status_info["status"] == "filled":
                    fill_price = status_info["fill_price"] or self.pending_order["trigger"]
                    logger.info(
                        f"[SESSION END] order filled during cancel, "
                        f"closing position at fill_price={fill_price}"
                    )
                    self._handle_pending_filled(fill_price)
                    # Don't clear pending_order here — let the position close block below handle it
                else:
                    self.pending_order = None
                    self._save_state()
            except Exception as e:
                logger.warning(f"[SESSION END] could not verify cancel status: {e}")
                self.pending_order = None
                self._save_state()

        if self.position is None:
            return

        pos   = self.position
        mark  = ig.get_mark_price(EPIC) if not self.paper else 0.0
        close_dir = "SELL" if pos["side"] == "LONG" else "BUY"

        logger.info(
            f"[SESSION END] Force-closing {pos['side']} "
            f"qty={pos['current_qty']} @ market (12:30 ET)"
        )

        if self.paper:
            # Use current mark from paper_state if available
            if mark <= 0:
                mark = pos["entry"]  # fallback (shouldn't happen)
            realized = self._paper.do_close(mark)
        else:
            ig.close_position(pos["deal_id"], pos["current_qty"], close_dir)
            realized = ig.get_realized_pnl(pos["deal_id"])
            if realized is None:
                realized = _calc_pnl(pos, mark)

        result = "WIN" if realized >= 0 else "LOSS"

        _log_trade("S5_CLOSE", {
            "trade_id":    pos["trade_id"],
            "side":        pos["side"],
            "qty":         pos["current_qty"],
            "entry":       round(pos["entry"], 1),
            "pnl":         round(realized, 2),
            "result":      result,
            "exit_reason": "SESSION_END",
        }, paper=self.paper)

        logger.info(
            f"[SESSION END] {result} | PnL={realized:+.2f}"
        )
        self.position = None
        self.pending_order = None
        if not self.paper:
            self._clear_state()


# ── Entry point ──────────────────────────────────────────────── #

if __name__ == "__main__":
    paper_mode = "--paper" in sys.argv
    config_ig.PAPER_MODE = paper_mode

    if not paper_mode:
        # Validate credentials are set
        missing = [k for k in ("IG_API_KEY", "IG_USERNAME", "IG_PASSWORD")
                   if not getattr(config_ig, k)]
        if missing:
            print(f"ERROR: Missing credentials: {', '.join(missing)}")
            print("Set them via environment variables or config_ig.py")
            sys.exit(1)

        # Establish IG session before starting the loop
        try:
            ig._get_session()
        except Exception as e:
            print(f"ERROR: Could not connect to IG: {e}")
            sys.exit(1)

    bot = IGBot(paper=paper_mode)
    bot.run()
