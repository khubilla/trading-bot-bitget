"""
IG CFD S5 Bot — multi-instrument (US30, Gold, …).

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
import threading
import time
import uuid
import zoneinfo
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import ig_client as ig
import ig_stream
import config_ig

from strategies.s5 import evaluate_s5
from indicators import calculate_ema
from tools import find_swing_low_target, find_swing_high_target


def _check_disclaimer():
    agreed = Path(__file__).parent / ".disclaimer_agreed"
    if agreed.exists():
        return
    if os.environ.get("PYTEST_CURRENT_TEST") or "--test" in sys.argv:
        return
    print("""
╔══════════════════════════════════════════════════════════════════════╗
║            RISK DISCLAIMER — PLEASE READ BEFORE PROCEEDING          ║
╠══════════════════════════════════════════════════════════════════════╣
║                                                                      ║
║  This software is provided for EDUCATIONAL AND INFORMATIONAL         ║
║  PURPOSES ONLY. Algorithmic trading involves SUBSTANTIAL RISK        ║
║  OF LOSS and is NOT suitable for all investors.                      ║
║                                                                      ║
║  • Past performance does not guarantee future results                ║
║  • You may lose some or all of your invested capital                 ║
║  • The creator accepts NO LIABILITY for any financial losses,        ║
║    damages, or consequences arising from use of this software        ║
║  • This is NOT financial advice                                      ║
║  • Use entirely at your own risk                                     ║
║  • The creator is NOT responsible for modified, redistributed,       ║
║    or tampered versions — only the original source is covered        ║
║                                                                      ║
║  By continuing you confirm that you:                                 ║
║    (1) understand and accept all risks of algorithmic trading        ║
║    (2) release the creator from any liability for losses             ║
║    (3) are solely responsible for all trading decisions              ║
║                                                                      ║
╚══════════════════════════════════════════════════════════════════════╝
""")
    try:
        resp = input('Type "I AGREE" to continue: ').strip()
    except (EOFError, KeyboardInterrupt):
        print("\nDisclaimer not accepted. Exiting.")
        sys.exit(0)
    if resp != "I AGREE":
        print("Disclaimer not accepted. Exiting.")
        sys.exit(0)
    agreed.write_text("agreed\n", encoding="utf-8")
    print("Disclaimer accepted. Starting bot...\n")


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
ET = zoneinfo.ZoneInfo("America/New_York")

# ── CSV fields ───────────────────────────────────────────────── #
_TRADE_FIELDS = [
    "timestamp", "trade_id", "action", "symbol",
    "side", "qty", "entry", "sl", "tp",
    "snap_entry_trigger", "snap_sl", "snap_rr",
    "snap_s5_ob_low", "snap_s5_ob_high", "snap_s5_tp",
    "result", "pnl", "exit_reason",
    "session_date", "mode",
]

_REQUIRED_INSTRUMENT_KEYS = {
    "epic", "display_name", "currency",
    "contract_size", "partial_size", "point_value",
    "session_start", "session_end",
    "daily_limit", "htf_limit", "m15_limit",
    "s5_enabled", "s5_daily_ema_fast", "s5_daily_ema_med", "s5_daily_ema_slow",
    "s5_htf_bos_lookback", "s5_ltf_interval", "s5_ob_lookback",
    "s5_ob_min_impulse", "s5_ob_min_range_pct", "s5_choch_lookback",
    "s5_max_entry_buffer", "s5_sl_buffer_pct", "s5_ob_invalidation_buffer_pct",
    "s5_swing_lookback", "s5_smc_fvg_filter", "s5_smc_fvg_lookback",
    "s5_leverage", "s5_trade_size_pct", "s5_min_rr",
    "s5_trail_range_pct", "s5_use_candle_stops", "s5_min_sr_clearance",
}


def _validate_instruments() -> None:
    for inst in config_ig.INSTRUMENTS:
        missing = _REQUIRED_INSTRUMENT_KEYS - inst.keys()
        if missing:
            raise KeyError(
                f"Instrument config '{inst.get('display_name', '?')}' missing keys: {missing}"
            )


_validate_instruments()


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
    """True if current ET time is within [SESSION_START, SESSION_END) for the first instrument.

    Module-level function kept for test-patching only; _tick_instrument uses the _for variants.
    """
    inst = config_ig.INSTRUMENTS[0]
    start = now.replace(hour=inst["session_start"][0],
                        minute=inst["session_start"][1],
                        second=0, microsecond=0)
    end   = now.replace(hour=inst["session_end"][0],
                        minute=inst["session_end"][1],
                        second=0, microsecond=0)
    return start <= now < end


def _is_session_end(now: datetime) -> bool:
    """True once we hit or pass SESSION_END on a weekday for the first instrument.

    Module-level function kept for test-patching only; _tick_instrument uses the _for variants.
    """
    inst = config_ig.INSTRUMENTS[0]
    return (now.weekday() < 5 and
            now.hour == inst["session_end"][0] and
            now.minute >= inst["session_end"][1])


def _in_trading_window_for(instrument: dict, now: datetime) -> bool:
    """True if current ET time is within [session_start, session_end) for the given instrument."""
    start = now.replace(hour=instrument["session_start"][0],
                        minute=instrument["session_start"][1],
                        second=0, microsecond=0)
    end   = now.replace(hour=instrument["session_end"][0],
                        minute=instrument["session_end"][1],
                        second=0, microsecond=0)
    return start <= now < end


def _is_session_end_for(instrument: dict, now: datetime) -> bool:
    """True once we hit or pass session_end on a weekday for the given instrument."""
    return (now.weekday() < 5 and
            now.hour == instrument["session_end"][0] and
            now.minute >= instrument["session_end"][1])


def _entry_in_window(sig: str, mark: float, trigger: float, instrument: dict) -> bool:
    """Reject stale or over-extended entries — mirrors bot.py S5 gate."""
    if sig == "LONG":
        if mark < trigger:
            logger.info(f"[S5] LONG stale — mark {mark:.1f} < trigger {trigger:.1f}")
            return False
        if mark > trigger * (1 + instrument["s5_max_entry_buffer"]):
            logger.info(f"[S5] LONG entry missed — price {mark:.1f} too far past trigger {trigger:.1f}")
            return False
    else:
        if mark > trigger:
            logger.info(f"[S5] SHORT stale — mark {mark:.1f} > trigger {trigger:.1f}")
            return False
        if mark < trigger * (1 - instrument["s5_max_entry_buffer"]):
            logger.info(f"[S5] SHORT entry missed — price {mark:.1f} too far past trigger {trigger:.1f}")
            return False
    return True


# ── PnL helpers ──────────────────────────────────────────────── #

def _calc_pnl(pos: dict, exit_price: float, instrument: dict) -> float:
    """USD PnL. US30: $1/point per contract."""
    qty = pos.get("current_qty", pos.get("initial_qty", instrument["contract_size"]))
    if pos["side"] == "LONG":
        return (exit_price - pos["entry"]) * qty * instrument["point_value"]
    return (pos["entry"] - exit_price) * qty * instrument["point_value"]


def _calc_partial_pnl(pos: dict, close_price: float, instrument: dict) -> float:
    """PnL for the partial (partial_size contracts)."""
    if pos["side"] == "LONG":
        return (close_price - pos["entry"]) * instrument["partial_size"] * instrument["point_value"]
    return (pos["entry"] - close_price) * instrument["partial_size"] * instrument["point_value"]


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

    def do_partial(self, mark: float, partial_size: float, point_value: float) -> float:
        """Execute paper partial close. Returns partial PnL."""
        pos = self.position
        if pos["side"] == "LONG":
            partial_pnl = (mark - pos["entry"]) * partial_size * point_value
        else:
            partial_pnl = (pos["entry"] - mark) * partial_size * point_value
        pos["partial_done"] = True
        pos["current_qty"]  = pos["initial_qty"] - partial_size
        pos["sl"]           = pos["entry"]   # breakeven
        self.balance       += partial_pnl
        self._save()
        return partial_pnl

    def do_close(self, exit_price: float, instrument: dict) -> float:
        """Execute paper full close. Returns close PnL."""
        pos = self.position
        qty = pos.get("current_qty", pos.get("initial_qty", instrument["contract_size"]))
        if pos["side"] == "LONG":
            pnl = (exit_price - pos["entry"]) * qty * instrument["point_value"]
        else:
            pnl = (pos["entry"] - exit_price) * qty * instrument["point_value"]
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
        self._stop_event = threading.Event()
        # Per-instrument state dicts — keyed by display_name (e.g. "US30")
        self._positions: dict      = {}
        self._pending_orders: dict = {}
        # Structure of each pending_orders value:
        #   {"deal_id": str, "side": str, "ob_low": float, "ob_high": float,
        #    "sl": float, "tp": float, "trigger": float, "size": float, "expires": float}
        self._paper      = _PaperState() if paper else None
        # Candle cache: fetch full history once, then append new candles only.
        # IG limits historical price data to 10,000 points/week — fetching 550
        # points every 45s would exceed that in ~4 minutes.
        # Cache key is (epic, interval) tuple to support multiple instruments.
        self._candle_cache: dict[tuple, pd.DataFrame] = {}
        self._scan_signals: dict = {}   # keyed by display_name; latest evaluate_s5 output per instrument
        self._scan_log: list     = []   # last 20 scan entries, newest first
        self._stream_lock = threading.Lock()

        _first = config_ig.INSTRUMENTS[0]
        mode = "PAPER" if paper else f"LIVE ({config_ig.IG_ACC_TYPE})"
        instruments_str = ", ".join(i["display_name"] for i in config_ig.INSTRUMENTS)
        logger.info(
            f"IGBot starting | mode={mode} | instruments=[{instruments_str}] | "
            f"session={_first['session_start'][0]:02d}:{_first['session_start'][1]:02d}–"
            f"{_first['session_end'][0]:02d}:{_first['session_end'][1]:02d} ET"
        )

        if paper:
            # Restore position from paper state if any
            if self._paper.position is not None:
                self._positions[_first["display_name"]] = self._paper.position
            # Restore pending order and scan state from state file if present
            self._load_state()
        else:
            self._sync_live_position()

    # ── Property shims for backward-compat (single-instrument) ──── #

    @property
    def position(self) -> dict | None:
        return self._positions.get(config_ig.INSTRUMENTS[0]["display_name"])

    @position.setter
    def position(self, value: dict | None) -> None:
        self._positions[config_ig.INSTRUMENTS[0]["display_name"]] = value

    @property
    def pending_order(self) -> dict | None:
        return self._pending_orders.get(config_ig.INSTRUMENTS[0]["display_name"])

    @pending_order.setter
    def pending_order(self, value: dict | None) -> None:
        self._pending_orders[config_ig.INSTRUMENTS[0]["display_name"]] = value

    # ── State persistence ────────────────────────────────────────── #

    def _load_state(self) -> None:
        """Load state from STATE_FILE, migrating old single-instrument format if needed."""
        if not os.path.exists(config_ig.STATE_FILE):
            return
        try:
            with open(config_ig.STATE_FILE) as f:
                raw = json.load(f)
        except Exception:
            return

        _first_name = config_ig.INSTRUMENTS[0]["display_name"]

        # Migrate old single-instrument format
        if "position" in raw and "positions" not in raw:
            raw["positions"] = {_first_name: raw.pop("position")}
            raw["pending_orders"] = {_first_name: raw.pop("pending_order", None)}

        saved_positions = raw.get("positions", {})
        saved_pending   = raw.get("pending_orders", {})

        # For paper mode, paper state owns the position — only restore pending/scan
        if not self.paper:
            self._positions = saved_positions
        else:
            # Merge: paper state is authoritative for position, but restore pending
            pass

        for name, po in saved_pending.items():
            if po is not None:
                self._pending_orders[name] = po

        self._scan_signals = raw.get("scan_signals", {})
        self._scan_log     = raw.get("scan_log", [])

    def _get_candles(self, interval: str, limit: int, epic: str | None = None) -> pd.DataFrame:
        """
        Return candles from in-memory cache.  Only calls IG's price history API
        when a new candle has formed since the last fetch — keeping weekly data
        point usage well under IG's 10,000-point limit.

        Cold start (cache empty): fetches full history (one-time cost).
        Subsequent ticks: fetches 3 candles only when the next candle period
        has opened, then appends & deduplicates.

        The cache key is a (epic, interval) tuple so multiple instruments can
        share the same cache dict without collisions.

        When epic is not provided, falls back to the current instrument being
        processed (self._current_instrument) or INSTRUMENTS[0].
        """
        if epic is None:
            current = getattr(self, "_current_instrument", None)
            epic = current["epic"] if current else config_ig.INSTRUMENTS[0]["epic"]
        cache_key   = (epic, interval)
        interval_ms = {"1D": 86_400_000, "1H": 3_600_000, "15m": 900_000}.get(interval, 60_000)
        now_ms      = int(time.time() * 1000)
        cached      = self._candle_cache.get(cache_key)

        if cached is None or cached.empty:
            df = ig.get_candles(epic, interval, limit)
            if not df.empty:
                self._candle_cache[cache_key] = df
            return df

        last_candle_ts = int(cached["ts"].iloc[-1])
        if now_ms < last_candle_ts + interval_ms:
            return cached   # current candle still open — no new data

        # A new candle period has started — fetch the last 3 to catch any missed
        fresh = ig.get_candles(epic, interval, 3)
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
        self._candle_cache[cache_key] = combined
        return combined

    def _sync_live_position(self) -> None:
        """On startup, restore positions and pending_orders from STATE_FILE."""
        self._load_state()
        # Validate restored live positions against exchange
        for instrument in config_ig.INSTRUMENTS:
            name  = instrument["display_name"]
            saved = self._positions.get(name)
            if saved:
                deal_id = saved.get("deal_id", "")
                live    = ig.get_open_position(deal_id)
                if live:
                    logger.info(f"[{name}] Restored position from state file: {deal_id}")
                else:
                    logger.info(f"[{name}] State file has position but it's no longer open — clearing")
                    self._positions[name] = None
                    self._save_state()
            saved_pending = self._pending_orders.get(name)
            if saved_pending:
                logger.info(f"[{name}] Restored pending order from state file: {saved_pending.get('deal_id')}")

    def _save_state(self) -> None:
        with open(config_ig.STATE_FILE, "w") as f:
            json.dump({
                "positions":      self._positions,
                "pending_orders": self._pending_orders,
                "scan_signals":   self._scan_signals,
                "scan_log":       self._scan_log,
            }, f, indent=2)

    def _clear_state(self, instrument: dict) -> None:
        name = instrument["display_name"]
        self._positions[name] = None
        self._save_state()  # persist cleared position so dashboard heartbeat stays fresh

    def _heartbeat(self) -> None:
        """Touch state file every tick so dashboard knows the bot is alive."""
        any_position = any(v for v in self._positions.values() if v)
        if not os.path.exists(config_ig.STATE_FILE) or not any_position:
            self._save_state()

    def _update_scan_state(
        self,
        instrument: str,
        signal: str,
        reason: str,
        ob_low: float,
        ob_high: float,
        entry_trigger: float,
        sl: float,
        tp: float,
    ) -> None:
        """Update per-instrument signal entry and prepend to scan log (capped at 20)."""
        now_et = datetime.now(ET)

        ema_ok = ("Daily EMA bearish" in reason or "Daily EMA bullish" in reason
                  or "BOS \u2705" in reason or signal in ("PENDING_LONG", "PENDING_SHORT"))
        bos_ok = "BOS \u2705" in reason or signal in ("PENDING_LONG", "PENDING_SHORT")
        ob_ok  = "OB \u2705"  in reason or signal in ("PENDING_LONG", "PENDING_SHORT")

        self._scan_signals[instrument] = {
            "signal":        signal,
            "reason":        reason,
            "ema_ok":        ema_ok,
            "bos_ok":        bos_ok,
            "ob_ok":         ob_ok,
            "ob_low":        ob_low  if ob_low  else None,
            "ob_high":       ob_high if ob_high else None,
            "entry_trigger": entry_trigger if entry_trigger else None,
            "sl":            sl if sl else None,
            "tp":            tp if tp else None,
            "updated_at":    datetime.now(timezone.utc).isoformat(),
        }

        self._scan_log.insert(0, {
            "ts":         now_et.strftime("%H:%M"),
            "instrument": instrument,
            "message":    reason,
        })
        self._scan_log = self._scan_log[:20]

    def _on_stream_event(self, event_type: str, deal_id: str, fill_price: float) -> None:
        """
        Called from the Lightstreamer thread when a fill or position close arrives.
        Acquires _stream_lock to prevent concurrent mutation with _tick().
        """
        with self._stream_lock:
            try:
                if event_type == "WOU_FILL":
                    for inst in config_ig.INSTRUMENTS:
                        name = inst["display_name"]
                        po   = self._pending_orders.get(name)
                        if po and po.get("deal_id") == deal_id:
                            self._current_instrument = inst
                            self._handle_pending_filled(fill_price)
                            self._pending_orders[name] = None
                            self._save_state()
                            logger.info(f"[{name}] [STREAM] WOU fill handled: {deal_id} @ {fill_price}")
                            break
                    else:
                        logger.warning(f"[STREAM] WOU_FILL for unknown deal_id={deal_id}, ignoring")
                elif event_type == "OPU_CLOSE":
                    for inst in config_ig.INSTRUMENTS:
                        name = inst["display_name"]
                        pos  = self._positions.get(name)
                        if pos and pos.get("deal_id") == deal_id:
                            self._current_instrument = inst
                            mark = ig.get_mark_price(inst["epic"])
                            self._handle_position_closed(mark, inst, exit_reason="SL_OR_TP")
                            logger.info(f"[{name}] [STREAM] OPU close handled: {deal_id}")
                            break
                    else:
                        logger.warning(f"[STREAM] OPU_CLOSE for unknown deal_id={deal_id}, ignoring")
            finally:
                self._current_instrument = None

    def run(self) -> None:
        signal.signal(signal.SIGINT,  self.stop)
        signal.signal(signal.SIGTERM, self.stop)

        while self.running:
            try:
                self._tick()
            except Exception as e:
                logger.error(f"Tick error: {e}", exc_info=True)
            self._stop_event.wait(timeout=config_ig.POLL_INTERVAL_SEC)

    def stop(self, *_) -> None:
        logger.info("Shutting down IGBot")
        self.running = False
        self._stop_event.set()

    # ── Main tick ─────────────────────────────────────────────── #

    def _tick(self) -> None:
        if not self.paper:
            if ig_stream.needs_reauth():
                logger.info("Stream token expired — refreshing session")
                ig._refresh_session()
                creds = ig.get_stream_credentials()
                # Filter to only epics with streaming enabled
                all_epics = [i["epic"] for i in config_ig.INSTRUMENTS]
                streamable_epics = [e for e in all_epics if ig.is_streaming_available(e)]
                ig_stream.stop()
                ig_stream.start(
                    epics          = streamable_epics,
                    account_id     = creds["account_id"],
                    cst            = creds["cst"],
                    xst            = creds["xst"],
                    ls_endpoint    = creds["ls_endpoint"],
                    trade_callback = self._on_stream_event,
                )
                return  # skip this tick; resume on next poll
            if not ig_stream.is_connected():
                logger.warning("Stream disconnected — pausing tick")
                return
        with self._stream_lock:
            self._heartbeat()
            now = _now_et()
            for instrument in config_ig.INSTRUMENTS:
                try:
                    self._tick_instrument(instrument, now)
                except Exception:
                    logger.exception("tick error for %s", instrument.get("display_name", "?"))
                finally:
                    self._current_instrument = None

    def _tick_instrument(self, instrument: dict, now: datetime) -> None:
        # Set current instrument so _get_candles and helpers can resolve epic/config
        # without requiring it to be passed as a kwarg (preserves test-patch compatibility).
        self._current_instrument = instrument

        name = instrument["display_name"]
        pos  = self._positions.get(name)
        po   = self._pending_orders.get(name)

        # 1. Session-end force close (handles both open position and pending order)
        if (pos or po) and _is_session_end_for(instrument, now):
            self._session_end_close(instrument)
            return

        # 2. Monitor open position (always, even outside entry window)
        if pos:
            self._monitor_position(instrument)

        # 3. Outside session window or weekend → no new entries
        if not _in_trading_window_for(instrument, now):
            logger.debug(f"[{name}] Outside trading window ({now.strftime('%H:%M')} ET)")
            return
        # Weekend check: Saturday all day, Sunday before 6 PM ET (markets open Sunday 6 PM)
        if now.weekday() == 5:  # Saturday
            logger.info(f"[{name}] Weekend (Saturday) — no new entries")
            return
        if now.weekday() == 6 and now.hour < 18:  # Sunday before 6 PM ET
            logger.info(f"[{name}] Weekend (Sunday before 6 PM ET) — no new entries")
            return

        # 4. Already in a trade
        if pos:
            return

        # 4b. Check pending working order (returns early to avoid new entry evaluation)
        po = self._pending_orders.get(name)
        if po is not None:
            epic = instrument["epic"]
            mark = ig.get_mark_price(epic)
            if mark > 0:
                self._check_pending_order(mark)
            return

        # 5. Fetch candles (cached — only hits API when new candle has formed)
        # _get_candles resolves epic from self._current_instrument when not supplied.
        daily_df = self._get_candles("1D",  instrument["daily_limit"])
        htf_df   = self._get_candles("1H",  instrument["htf_limit"])
        m15_df   = self._get_candles("15m", instrument["m15_limit"])

        if daily_df.empty or htf_df.empty or m15_df.empty:
            logger.warning(f"[{name}] Candle fetch returned empty — skipping tick")
            return

        # 6. Derive allowed_direction from daily EMA waterfall (must match strategy.py logic)
        ema_fast = float(calculate_ema(daily_df["close"].astype(float), instrument["s5_daily_ema_fast"]).iloc[-1])
        ema_med  = float(calculate_ema(daily_df["close"].astype(float), instrument["s5_daily_ema_med"]).iloc[-1])
        ema_slow = float(calculate_ema(daily_df["close"].astype(float), instrument["s5_daily_ema_slow"]).iloc[-1])

        ema_bull = ema_fast > ema_med > ema_slow
        ema_bear = ema_slow > ema_med > ema_fast

        if ema_bull:
            allowed_direction = "BULLISH"
        elif ema_bear:
            allowed_direction = "BEARISH"
        else:
            allowed_direction = "BULLISH" if ema_fast > ema_slow else "BEARISH"

        # 7. Evaluate S5
        epic = instrument["epic"]
        sig, trigger, sl, tp, ob_low, ob_high, reason = evaluate_s5(
            epic, daily_df, htf_df, m15_df, allowed_direction, cfg=instrument
        )
        logger.info(f"[{name}] [S5] {reason}")

        # Update scan state so dashboard always shows latest signal — save regardless of signal
        self._update_scan_state(name, sig, reason, ob_low, ob_high, trigger, sl, tp)
        self._save_state()

        if sig not in ("PENDING_LONG", "PENDING_SHORT"):
            return

        # 8. Get mark price for limit order placement
        mark = ig.get_mark_price(epic)
        if mark <= 0:
            return

        # 9. Guard: reject if mark has already crossed the trigger level
        side = "LONG" if sig == "PENDING_LONG" else "SHORT"
        if not _entry_in_window(side, mark, trigger, instrument):
            logger.info(f"[{name}] [S5] Skipping limit order — mark {mark:.1f} invalid vs trigger {trigger:.1f}")
            return

        # 10. Place limit working order
        sl   = round(sl, 1)
        tp   = round(tp, 1) if tp else round(trigger + abs(trigger - sl) if side == "LONG" else trigger - abs(trigger - sl), 1)
        contract_size = instrument["contract_size"]
        try:
            if side == "LONG":
                deal_id = ig.place_limit_long(epic, trigger, sl, tp, contract_size, currency=instrument["currency"])
            else:
                deal_id = ig.place_limit_short(epic, trigger, sl, tp, contract_size, currency=instrument["currency"])
        except Exception as e:
            logger.error(f"[{name}] [S5] Failed to place limit {side}: {e}")
            return
        self._pending_orders[name] = {
            "deal_id":  deal_id,
            "side":     side,
            "ob_low":   ob_low,
            "ob_high":  ob_high,
            "sl":       sl,
            "tp":       tp,
            "trigger":  trigger,
            "size":     contract_size,
            "expires":  time.time() + 4 * 3600,
        }
        self._save_state()
        logger.info(
            f"[{name}] [S5] {side} limit order placed | trigger={trigger:.1f} | "
            f"SL={sl:.1f} | TP={tp:.1f} | deal_id={deal_id}"
        )

    # ── Open trade ─────────────────────────────────────────────── #

    def _open_trade(self, instrument: dict, sig: str, sl: float, tp: float,
                    ob_low: float, ob_high: float,
                    trigger: float, mark: float) -> None:
        name          = instrument["display_name"]
        contract_size = instrument["contract_size"]
        risk = abs(mark - sl)
        if risk <= 0:
            logger.warning(f"[{name}] [S5] risk=0, skipping")
            return

        tp1 = round(mark + risk if sig == "LONG" else mark - risk, 1)
        # Round SL/TP to nearest whole point (US30 convention)
        sl  = round(sl, 1)
        tp  = round(tp, 1) if tp else tp1

        trade_id   = uuid.uuid4().hex[:8]

        try:
            if self.paper:
                trade = self._paper.open(sig, mark, sl, tp1, tp,
                                         contract_size, trade_id, ob_low, ob_high)
                self._positions[name] = self._paper.position
            else:
                epic = instrument["epic"]
                if sig == "LONG":
                    trade = ig.open_long(epic, sl, tp1, tp, currency=instrument["currency"])
                else:
                    trade = ig.open_short(epic, sl, tp1, tp, currency=instrument["currency"])
                self._positions[name] = {
                    "side":         sig,
                    "deal_id":      trade["deal_id"],
                    "entry":        trade["entry"],
                    "sl":           sl,
                    "tp1":          tp1,
                    "tp":           tp,
                    "initial_qty":  contract_size,
                    "current_qty":  contract_size,
                    "partial_done": False,
                    "trade_id":     trade_id,
                    "opened_at":    _now_et().isoformat(),
                    "ob_low":       ob_low,
                    "ob_high":      ob_high,
                }
                self._save_state()
        except Exception as e:
            logger.error(f"[{name}] [S5] Failed to open {sig}: {e}")
            return

        pos   = self._positions[name]
        entry = pos["entry"] if self.paper else trade["entry"]
        rr    = round(abs(tp - entry) / risk, 2) if risk > 0 else 0

        _log_trade(f"S5_{sig}", {
            "symbol":             name,
            "trade_id":           trade_id,
            "side":               sig,
            "qty":                contract_size,
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
            f"[{name}] [S5] {sig} opened | entry={entry:.1f} | SL={sl:.1f} | "
            f"TP1={tp1:.1f} | TP={tp:.1f} | size={contract_size} | R:R={rr}"
        )

    # ── Pending working order management ──────────────────────── #

    def _check_pending_order(self, mark: float) -> bool:
        """
        Check status of the pending GTC working order.
        Returns True if the pending order was handled (filled, cancelled, or still live).
        Returns False if there is no pending order.

        Resolves instrument from self._current_instrument (set by _tick_instrument).
        Falls back to INSTRUMENTS[0] for backward compatibility.
        """
        instrument = getattr(self, "_current_instrument", None) or config_ig.INSTRUMENTS[0]
        name = instrument["display_name"]
        pending_order = self._pending_orders.get(name)

        if pending_order is None:
            return False

        deal_id = pending_order["deal_id"]
        side    = pending_order["side"]

        try:
            status_info = ig.get_working_order_status(deal_id)
            status = status_info["status"]
        except Exception as e:
            logger.warning(f"[{name}] [S5] _check_pending_order: status check failed ({e}), retrying next tick")
            return True

        if status == "filled":
            fill_price = status_info["fill_price"] or pending_order["trigger"]
            self._handle_pending_filled(fill_price)
            self._pending_orders[name] = None
            self._save_state()
            return True

        elif status == "open":
            cancel_reason = None

            # 1. 15m OB invalidation — price breaks OB outer edge
            if side == "LONG" and mark < pending_order["ob_low"] * (1 - instrument["s5_ob_invalidation_buffer_pct"]):
                cancel_reason = "OB invalidated (price below ob_low)"
            elif side == "SHORT" and mark > pending_order["ob_high"] * (1 + instrument["s5_ob_invalidation_buffer_pct"]):
                cancel_reason = "OB invalidated (price above ob_high)"

            # 2. Expiry
            elif time.time() > pending_order["expires"]:
                cancel_reason = "Limit order expired"

            # 3. HTF direction change — re-evaluate daily EMA + 1H BOS using cached candles
            #    (no extra API calls between candle boundaries)
            else:
                daily_df = self._get_candles("1D", instrument["daily_limit"])
                htf_df   = self._get_candles("1H", instrument["htf_limit"])
                m15_df   = self._get_candles("15m", instrument["m15_limit"])
                if not (daily_df.empty or htf_df.empty or m15_df.empty):
                    ema_fast = float(calculate_ema(daily_df["close"].astype(float), instrument["s5_daily_ema_fast"]).iloc[-1])
                    ema_med  = float(calculate_ema(daily_df["close"].astype(float), instrument["s5_daily_ema_med"]).iloc[-1])
                    ema_slow = float(calculate_ema(daily_df["close"].astype(float), instrument["s5_daily_ema_slow"]).iloc[-1])
                    ema_bull = ema_fast > ema_med > ema_slow
                    ema_bear = ema_slow > ema_med > ema_fast
                    if ema_bull:
                        allowed_direction = "BULLISH"
                    elif ema_bear:
                        allowed_direction = "BEARISH"
                    else:
                        allowed_direction = "BULLISH" if ema_fast > ema_slow else "BEARISH"
                    sig, *_ = evaluate_s5(instrument["epic"], daily_df, htf_df, m15_df, allowed_direction, cfg=instrument)
                    expected = "PENDING_LONG" if side == "LONG" else "PENDING_SHORT"
                    if sig != expected:
                        cancel_reason = f"HTF conditions no longer valid (sig={sig})"

            if cancel_reason:
                cancel_success = False
                try:
                    ig.cancel_working_order(deal_id)
                    cancel_success = True
                except Exception as e:
                    logger.warning(f"[{name}] [S5] cancel_working_order error: {e}")
                if cancel_success:
                    logger.info(f"[{name}] [S5] 🚫 {cancel_reason} — cancelled {deal_id}")
                    self._pending_orders[name] = None
                    self._save_state()
                else:
                    logger.warning(f"[{name}] [S5] cancel unconfirmed — keeping pending for retry")
            return True  # still pending (or just cleared)

        elif status == "deleted":
            logger.info(f"[{name}] [S5] Limit order deleted externally: {deal_id}")
            self._pending_orders[name] = None
            self._save_state()
            return True

        elif status == "unknown":
            # Transient error — leave pending_order as-is, retry next tick
            return True

        return False

    def _handle_pending_filled(self, fill_price: float) -> None:
        """
        Called when the GTC limit order fills.
        Sets self._positions[name] (matching the structure _monitor_position expects)
        and logs the trade to CSV.

        Resolves instrument from self._current_instrument (set by _tick_instrument or
        _check_pending_order before calling this method).
        """
        instrument = getattr(self, "_current_instrument", None) or config_ig.INSTRUMENTS[0]
        name    = instrument["display_name"]
        po      = self._pending_orders.get(name)
        if po is None:
            return
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
            self._positions[name] = self._paper.position
        else:
            self._positions[name] = {
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
            "symbol":             name,
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
            f"[{name}] [S5] {side} limit order FILLED | entry={fill_price:.1f} | "
            f"SL={sl:.1f} | TP1={tp1:.1f} | TP={tp:.1f} | size={size} | R:R={rr}"
        )

    # ── Monitor open position ──────────────────────────────────── #

    def _monitor_position(self, instrument: dict = None) -> None:
        if instrument is None:
            instrument = config_ig.INSTRUMENTS[0]
        name = instrument["display_name"]
        pos  = self._positions.get(name)
        if pos is None:
            return
        epic = instrument["epic"]
        mark = ig.get_mark_price(epic)
        if mark <= 0:
            return

        if self.paper:
            # Check partial
            if self._paper.check_partial(mark):
                self._handle_partial_close(mark, instrument)
                return

            # Check SL/TP
            hit = self._paper.check_sl_tp(mark)
            if hit:
                self._handle_position_closed(mark, instrument, exit_reason=hit)
                return

            # Swing trail after partial
            if pos["partial_done"] and instrument["s5_use_candle_stops"]:
                self._trail_sl_candle(mark, instrument)

        else:
            # Live: sync from exchange
            live = ig.get_open_position(pos["deal_id"])
            if live is None:
                self._handle_position_closed(mark, instrument, exit_reason="SL_OR_TP")
                return

            # Detect bot-driven partial: mark crossed TP1
            if not pos["partial_done"]:
                tp1  = pos["tp1"]
                if (pos["side"] == "LONG"  and mark >= tp1) or \
                   (pos["side"] == "SHORT" and mark <= tp1):
                    self._handle_partial_close(mark, instrument)
                    return

            # Swing trail after partial
            if pos["partial_done"] and instrument["s5_use_candle_stops"]:
                self._trail_sl_candle(mark, instrument)

        upnl = _calc_pnl(pos, mark, instrument)
        logger.info(
            f"[{name}] [S5] {pos['side']} | entry={pos['entry']:.1f} | mark={mark:.1f} | "
            f"uPnL={upnl:+.2f} | qty={pos['current_qty']} | SL={pos['sl']:.1f}"
        )

    def _handle_partial_close(self, mark: float, instrument: dict = None) -> None:
        if instrument is None:
            instrument = config_ig.INSTRUMENTS[0]
        name      = instrument["display_name"]
        pos       = self._positions.get(name)
        if pos is None:
            return
        close_dir = "SELL" if pos["side"] == "LONG" else "BUY"

        if self.paper:
            partial_pnl = self._paper.do_partial(mark, instrument["partial_size"], instrument["point_value"])
            self._positions[name] = self._paper.position
        else:
            ok = ig.partial_close(pos["deal_id"], instrument["partial_size"], close_dir)
            if not ok:
                logger.error(f"[{name}] [S5] partial_close failed — will retry next tick")
                return
            partial_pnl = _calc_partial_pnl(pos, mark, instrument)
            # Move SL to breakeven
            ig.update_sl(pos["deal_id"], pos["entry"])
            pos["sl"]           = pos["entry"]
            pos["partial_done"] = True
            pos["current_qty"]  = pos["initial_qty"] - instrument["partial_size"]
            self._save_state()

        _log_trade("S5_PARTIAL", {
            "symbol":      name,
            "trade_id":    pos["trade_id"],
            "side":        pos["side"],
            "qty":         instrument["partial_size"],
            "entry":       round(pos["entry"], 1),
            "pnl":         round(partial_pnl, 2),
            "result":      "WIN" if partial_pnl >= 0 else "LOSS",
            "exit_reason": "PARTIAL_TP",
        }, paper=self.paper)

        logger.info(
            f"[{name}] [S5] Partial close {instrument['partial_size']} contracts @ {mark:.1f} | "
            f"SL → breakeven {pos['entry']:.1f} | PnL={partial_pnl:+.2f}"
        )

    def _trail_sl_candle(self, mark: float, instrument: dict = None) -> None:
        """Trail SL to previous completed 15m candle low (LONG) or high (SHORT)."""
        if instrument is None:
            instrument = config_ig.INSTRUMENTS[0]
        name = instrument["display_name"]
        pos  = self._positions.get(name)
        if pos is None:
            return
        try:
            epic  = instrument["epic"]
            cs_df = self._get_candles(instrument["s5_ltf_interval"], instrument["s5_swing_lookback"] + 5, epic=epic)
            if cs_df.empty or len(cs_df) < 3:
                return

            if pos["side"] == "LONG":
                raw      = find_swing_low_target(cs_df, mark, lookback=instrument["s5_swing_lookback"])
                new_sl   = round(raw * (1 - instrument["s5_sl_buffer_pct"]), 1) if raw else None
                improves = new_sl is not None and new_sl > pos["sl"]
            else:
                raw      = find_swing_high_target(cs_df, mark, lookback=instrument["s5_swing_lookback"])
                new_sl   = round(raw * (1 + instrument["s5_sl_buffer_pct"]), 1) if raw else None
                improves = new_sl is not None and new_sl < pos["sl"]

            if not improves:
                return

            if self.paper:
                self._paper.update_sl(new_sl)
                self._positions[name]["sl"] = new_sl
            else:
                if ig.update_sl(pos["deal_id"], new_sl):
                    pos["sl"] = new_sl
                    self._save_state()

            logger.info(
                f"[{name}] [S5] Swing trail: SL → {new_sl:.1f} "
                f"(was {pos['sl']:.1f}, "
                f"{'low' if pos['side'] == 'LONG' else 'high'} ±{instrument['s5_sl_buffer_pct']*100:.1f}%)"
            )
        except Exception as e:
            logger.error(f"[{name}] Swing trail error: {e}")

    def _handle_position_closed(self, mark: float, instrument: dict = None,
                                 exit_reason: str = "SL_OR_TP") -> None:
        if instrument is None:
            instrument = config_ig.INSTRUMENTS[0]
        name = instrument["display_name"]
        pos  = self._positions.get(name)
        if pos is None:
            return

        if self.paper:
            # Use SL or TP as exit price for accurate PnL
            if exit_reason == "SL":
                exit_price = pos["sl"]
            elif exit_reason == "TP":
                exit_price = pos["tp"]
            else:
                exit_price = mark
            realized = self._paper.do_close(exit_price, instrument)
        else:
            realized = ig.get_realized_pnl(pos["deal_id"])
            if realized is None:
                realized = _calc_pnl(pos, mark, instrument)

        result = "WIN" if realized >= 0 else "LOSS"

        _log_trade("S5_CLOSE", {
            "symbol":      name,
            "trade_id":    pos["trade_id"],
            "side":        pos["side"],
            "qty":         pos["current_qty"],
            "entry":       round(pos["entry"], 1),
            "pnl":         round(realized, 2),
            "result":      result,
            "exit_reason": exit_reason,
        }, paper=self.paper)

        logger.info(
            f"[{name}] [S5] {result} closed | PnL={realized:+.2f} | reason={exit_reason}"
        )
        self._positions[name] = None
        if not self.paper:
            self._clear_state(instrument)

    # ── Session-end force close ────────────────────────────────── #

    def _session_end_close(self, instrument: dict = None) -> None:
        if instrument is None:
            instrument = getattr(self, "_current_instrument", None) or config_ig.INSTRUMENTS[0]
        name    = instrument["display_name"]
        epic    = instrument["epic"]
        pending = self._pending_orders.get(name)

        # Cancel any pending working order first
        if pending is not None:
            pending_deal_id = pending["deal_id"]
            try:
                ig.cancel_working_order(pending_deal_id)
                logger.info(f"[{name}] [SESSION END] Cancelled pending limit order {pending_deal_id}")
            except Exception as e:
                logger.warning(f"[{name}] [SESSION END] Failed to cancel pending order: {e}")
            # Check if the order filled despite the cancel attempt
            try:
                status_info = ig.get_working_order_status(pending_deal_id)
                if status_info["status"] == "filled":
                    fill_price = status_info["fill_price"] or pending["trigger"]
                    logger.info(
                        f"[{name}] [SESSION END] order filled during cancel, "
                        f"closing position at fill_price={fill_price}"
                    )
                    self._current_instrument = instrument
                    self._handle_pending_filled(fill_price)
                    # Don't clear pending_order here — let the position close block below handle it
                else:
                    self._pending_orders[name] = None
                    self._save_state()
            except Exception as e:
                logger.warning(f"[{name}] [SESSION END] could not verify cancel status: {e}")
                self._pending_orders[name] = None
                self._save_state()

        pos = self._positions.get(name)
        if pos is None:
            return

        mark  = ig.get_mark_price(epic) if not self.paper else 0.0
        close_dir = "SELL" if pos["side"] == "LONG" else "BUY"

        logger.info(
            f"[{name}] [SESSION END] Force-closing {pos['side']} "
            f"qty={pos['current_qty']} @ market (session end)"
        )

        if self.paper:
            # Use current mark from paper_state if available
            if mark <= 0:
                mark = pos["entry"]  # fallback (shouldn't happen)
            realized = self._paper.do_close(mark, instrument)
        else:
            ig.close_position(pos["deal_id"], pos["current_qty"], close_dir)
            realized = ig.get_realized_pnl(pos["deal_id"])
            if realized is None:
                realized = _calc_pnl(pos, mark, instrument)

        result = "WIN" if realized >= 0 else "LOSS"

        _log_trade("S5_CLOSE", {
            "symbol":      name,
            "trade_id":    pos["trade_id"],
            "side":        pos["side"],
            "qty":         pos["current_qty"],
            "entry":       round(pos["entry"], 1),
            "pnl":         round(realized, 2),
            "result":      result,
            "exit_reason": "SESSION_END",
        }, paper=self.paper)

        logger.info(
            f"[{name}] [SESSION END] {result} | PnL={realized:+.2f}"
        )
        self._positions[name] = None
        self._pending_orders[name] = None
        if not self.paper:
            self._clear_state(instrument)


# ── Entry point ──────────────────────────────────────────────── #

if __name__ == "__main__":
    _check_disclaimer()
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

    if not paper_mode:
        # Start Lightstreamer streaming (live mode only)
        try:
            creds = ig.get_stream_credentials()
            # Filter to only epics with streaming enabled
            all_epics = [i["epic"] for i in config_ig.INSTRUMENTS]
            streamable_epics = [e for e in all_epics if ig.is_streaming_available(e)]

            if streamable_epics:
                logger.info(f"Streaming enabled for: {streamable_epics}")
                if len(streamable_epics) < len(all_epics):
                    non_streamable = [e for e in all_epics if e not in streamable_epics]
                    logger.info(f"Using REST polling for: {non_streamable}")
            else:
                logger.warning("No instruments have streaming enabled — using REST polling for all")

            ig_stream.start(
                epics          = streamable_epics,
                account_id     = creds["account_id"],
                cst            = creds["cst"],
                xst            = creds["xst"],
                ls_endpoint    = creds["ls_endpoint"],
                trade_callback = bot._on_stream_event,
            )
            logger.info("Lightstreamer streaming started")
        except Exception as e:
            logger.error(f"Failed to start streaming: {e} — ticks will pause until stream connects")

    bot.run()
