"""
bot.py — Main Entry Point

Runs Strategy 1 and Strategy 2 simultaneously.
Only 1 active trade at a time across both strategies.

Strategy 1: MTF RSI Breakout (ADX trend filter, 1H break, 3m RSI+coil+breakout)
Strategy 2: 30-Day Breakout + 3m Consolidation (long candle, squeeze, 3m coil+break)
"""

import time, signal, sys, logging, csv, os, threading, uuid
from datetime import datetime, timezone

import config
import config_s1
import config_s2
import config_s3
import config_s4
import config_s5
import state as st
from scanner import get_qualified_pairs_and_sentiment
from strategy import (
    evaluate_s1, evaluate_s2, evaluate_s3, evaluate_s4, evaluate_s5,
    check_htf, check_exit,
    calculate_rsi, detect_consolidation,
    check_daily_trend,
    find_nearest_resistance, find_nearest_support, find_spike_base,
    find_bullish_ob, find_bearish_ob,
    find_swing_high_target, find_swing_low_target,
)
from claude_filter import claude_approve
import snapshot

PAPER_MODE = "--paper" in sys.argv
if PAPER_MODE:
    import paper_trader as tr
    st.set_file(str(config._DATA_DIR / "state_paper.json"))
    config.TRADE_LOG = config.TRADE_LOG.replace("trades.csv", "trades_paper.csv")
    print("📝 PAPER TRADING MODE — no real orders will be placed")
else:
    import trader as tr

# ── Logging ──────────────────────────────────────────────────────── #

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
    ]
)
logger = logging.getLogger(__name__)


_TRADE_FIELDS = [
    "timestamp", "trade_id", "action", "symbol", "side", "qty", "entry", "sl", "tp",
    "box_low", "box_high", "leverage", "margin", "tpsl_set", "strategy",
    # S1 snapshot
    "snap_rsi", "snap_adx", "snap_htf", "snap_coil", "snap_box_range_pct", "snap_sentiment",
    # S2 snapshot
    "snap_daily_rsi",
    # S3 snapshot
    "snap_entry_trigger", "snap_sl", "snap_rr",
    # S4 snapshot
    "snap_rsi_peak", "snap_spike_body_pct", "snap_rsi_div", "snap_rsi_div_str",
    # S5 snapshot
    "snap_s5_ob_low", "snap_s5_ob_high", "snap_s5_tp",
    # S/R clearance at entry (S2/S3/S4/S5)
    "snap_sr_clearance_pct",
    # Close fields
    "result", "pnl", "pnl_pct", "exit_reason", "exit_price",
]

def _log_trade(action: str, details: dict):
    row = {"timestamp": datetime.now(timezone.utc).isoformat(), "action": action, **details}
    write_header = not os.path.exists(config.TRADE_LOG)
    with open(config.TRADE_LOG, "a", newline="") as f:
        import csv as _csv
        w = _csv.DictWriter(f, fieldnames=_TRADE_FIELDS, extrasaction="ignore", restval="")
        if write_header:
            w.writeheader()
        w.writerow(row)


def _get_unclosed_csv_positions(csv_path: str) -> dict[str, dict]:
    """
    Scan the CSV and return {symbol: enriched_open_row} for every position
    that has an open (_LONG/_SHORT) row but no matching _CLOSE row.
    Enriched dict includes partial_logged and partial_pnl from any _PARTIAL row.
    """
    if not os.path.exists(csv_path):
        return {}
    try:
        with open(csv_path, newline="") as f:
            rows = list(csv.DictReader(f))
        open_by_tid:    dict[str, dict]  = {}
        closed_tids:    set[str]         = set()
        partial_by_tid: dict[str, float] = {}
        for r in rows:
            action = r.get("action", "")
            tid    = r.get("trade_id", "")
            if not tid:
                continue
            if any(action.endswith(sfx) for sfx in ("_LONG", "_SHORT")):
                open_by_tid[tid] = r
            elif "_CLOSE" in action:
                closed_tids.add(tid)
            elif "_PARTIAL" in action:
                try:
                    partial_by_tid[tid] = float(r.get("pnl") or 0)
                except (ValueError, TypeError):
                    partial_by_tid[tid] = 0.0
        result: dict[str, dict] = {}
        for tid, r in open_by_tid.items():
            if tid in closed_tids:
                continue
            sym = r.get("symbol", "")
            if sym:
                result[sym] = {
                    **r,
                    "partial_logged": tid in partial_by_tid,
                    "partial_pnl":    partial_by_tid.get(tid, 0.0),
                }
        return result
    except Exception:
        return {}


def _get_open_csv_row(csv_path: str, symbol: str) -> dict | None:
    """Return the most recent open (LONG/SHORT) CSV row for a symbol, or None."""
    if not os.path.exists(csv_path):
        return None
    try:
        with open(csv_path, newline="") as f:
            rows = list(csv.DictReader(f))
        for r in reversed(rows):
            action = r.get("action", "")
            if (r.get("symbol") == symbol
                    and any(action.endswith(sfx) for sfx in ("_LONG", "_SHORT"))
                    and r.get("qty")):
                return r
    except Exception:
        pass
    return None


def _df_to_candles(df) -> list[dict]:
    """Convert OHLCV DataFrame to snapshot candle list."""
    return [
        {"t": int(r["ts"]), "o": float(r["open"]), "h": float(r["high"]),
         "l": float(r["low"]),  "c": float(r["close"]), "v": float(r["vol"])}
        for _, r in df.iterrows()
    ]


_STRATEGY_CANDLE_INTERVAL = {
    "S1": config_s1.LTF_INTERVAL,    # "3m"
    "S2": "1D",
    "S3": config_s3.S3_LTF_INTERVAL, # "15m"
    "S4": "1D",
    "S5": config_s5.S5_LTF_INTERVAL, # "15m"
}


def _rebuild_stats_from_csv(csv_path: str):
    """Rebuild win/loss stats in state.py from the CSV so they survive bot restarts."""
    if not os.path.exists(csv_path):
        return
    wins = losses = 0
    total_pnl = 0.0
    try:
        with open(csv_path, newline="") as f:
            for r in csv.DictReader(f):
                action = r.get("action", "")
                is_close   = "_CLOSE"   in action
                is_partial = "_PARTIAL" in action
                if not is_close and not is_partial:
                    continue
                # Win/loss count only for final closes, not partial TPs
                if is_close:
                    result = r.get("result", "")
                    if result == "WIN":
                        wins += 1
                    elif result == "LOSS":
                        losses += 1
                try:
                    total_pnl += float(r.get("pnl") or 0)
                except (ValueError, TypeError):
                    pass
    except Exception as e:
        logger.warning(f"Stats rebuild from CSV failed: {e}")
        return
    if wins + losses > 0:
        st.set_stats(wins, losses, round(total_pnl, 4))
        logger.info(f"📊 Stats loaded from CSV: {wins}W / {losses}L | Total PnL={total_pnl:+.2f}")


# ── Bot ───────────────────────────────────────────────────────────── #

class MTFBot:
    def __init__(self):
        self.running         = True
        # Dict of { symbol: { side, strategy, box_high, box_low } }
        # Supports MAX_CONCURRENT_TRADES > 1
        self.active_positions: dict[str, dict] = {}
        self.last_scan_time  = 0
        self.qualified_pairs : list[str] = []
        self.sentiment       = None
        # Entry watcher — pending signals waiting for price trigger
        self.pending_signals: dict[str, dict] = {}
        self._trade_lock = threading.Lock()
        # Priority evaluation — candidates collected each scan cycle (all strategies)
        self.candidates: list = []

        st.reset()
        st.set_status("RUNNING")
        # Rebuild win/loss stats from CSV so header survives restarts
        _rebuild_stats_from_csv(config.TRADE_LOG)
        st.add_scan_log("Bot initialised (S1 + S2 + S3 + S4 + S5)", "INFO")

        logger.info("🤖 Bitget USDT-Futures MTF Bot — Strategy 1 + 2")
        logger.info(f"   Mode         : {'DEMO' if config.DEMO_MODE else '⚡ LIVE'}")
        logger.info(f"   S1 Risk      : {config_s1.TRADE_SIZE_PCT*100:.0f}% | {config_s1.LEVERAGE}x | "
                    f"SL=box TP={config_s1.TAKE_PROFIT_PCT*100:.0f}%")
        logger.info(f"   S1 ADX thr.  : {config_s1.ADX_TREND_THRESHOLD}")
        logger.info(f"   Dashboard    : python dashboard.py → http://localhost:8080")
        if PAPER_MODE:
            logger.info(f"   ⚠️  PAPER MODE : orders simulated | balance in paper_state.json\n")
        else:
            logger.info(f"")

        # ── Startup position sync ─────────────────────────────────── #
        try:
            existing = tr.get_all_open_positions()
            for sym, pos in existing.items():
                # Backfill strategy, SL, TP, and open time from CSV — the exchange
                # all-position API doesn't return these fields.
                _csv     = _get_open_csv_row(config.TRADE_LOG, sym)
                strategy = (_csv.get("action", "").split("_")[0]
                            if _csv else pos.get("strategy", "UNKNOWN")) or "UNKNOWN"
                _sl      = (_csv.get("sl")  or pos.get("sl",  "?")) if _csv else pos.get("sl",  "?")
                _tp      = (_csv.get("tp")  or pos.get("tp",  "?")) if _csv else pos.get("tp",  "?")
                _opened  = (_csv.get("timestamp") or pos.get("opened_at")) if _csv else pos.get("opened_at")

                _pmem = st.get_position_memory(sym)
                _resumed_ap: dict = {
                    "side": pos["side"], "strategy": strategy,
                    "box_high": 0.0, "box_low": 0.0,
                }
                if _pmem.get("initial_qty"):
                    _resumed_ap["initial_qty"] = _pmem["initial_qty"]
                if _pmem.get("partial_logged"):
                    _resumed_ap["partial_logged"] = _pmem["partial_logged"]
                self.active_positions[sym] = _resumed_ap
                logger.warning(f"⚠️  Resumed: {sym} {pos['side']} qty={pos['qty']} [{strategy}]"
                               + (f" | initial_qty={_pmem['initial_qty']}" if _pmem.get("initial_qty") else ""))
                st.add_open_trade({
                    "symbol":    sym,
                    "side":      pos["side"],
                    "qty":       pos["qty"],
                    "entry":     pos["entry_price"],
                    "sl":        _sl,
                    "tp":        _tp,
                    "margin":    pos.get("margin", 0),
                    "leverage":  pos.get("leverage", 0),
                    "strategy":  strategy,
                    "opened_at": _opened,
                })
            if existing:
                st.add_scan_log(f"Resumed {len(existing)} position(s)", "WARN")

            # ── Startup partial + close reconciliation ────────────── #
            # Read CSV once; used by both passes below.
            if not PAPER_MODE:
                unclosed = _get_unclosed_csv_positions(config.TRADE_LOG)

            # Pass A — partial TPs that fired while bot was disconnected.
            # Guards (in priority order):
            #   1. position_memory already has partial_logged=True
            #   2. CSV already has a _PARTIAL row for this trade_id
            #      (covers manually-added rows that never went through bot code)
            if not PAPER_MODE:
                for sym, ap in list(self.active_positions.items()):
                    if ap.get("strategy") not in ("S2", "S3", "S4", "S5"):
                        continue
                    if ap.get("partial_logged"):
                        continue
                    # If CSV already has a partial row, sync the flag and skip
                    csv_data = unclosed.get(sym, {})
                    if csv_data.get("partial_logged"):
                        ap["partial_logged"] = True
                        st.update_position_memory(sym, partial_logged=True)
                        continue
                    pos         = existing.get(sym, {})
                    current_qty = float(pos.get("qty", 0))
                    initial_qty = ap.get("initial_qty")
                    csv_open    = csv_data if csv_data else None
                    if not initial_qty:
                        if not csv_open:
                            csv_open = _get_open_csv_row(config.TRADE_LOG, sym)
                        initial_qty = float(csv_open["qty"]) if csv_open and csv_open.get("qty") else None
                        if initial_qty:
                            ap["initial_qty"] = initial_qty
                            st.update_position_memory(sym, initial_qty=initial_qty)
                    if not initial_qty or current_qty <= 0:
                        continue
                    if current_qty < initial_qty * 0.75:
                        entry_p  = float(pos.get("entry_price", 0))
                        side     = ap["side"]
                        lev      = float(pos.get("leverage") or 10)
                        trade_id = (csv_open or {}).get("trade_id", ap.get("trade_id", ""))
                        tp_str   = (csv_open or {}).get("tp") or pos.get("tp")
                        try:
                            exit_p = float(tp_str) if tp_str and tp_str not in ("?", "") else tr.get_mark_price(sym)
                        except Exception:
                            exit_p = entry_p
                        price_chg   = (exit_p - entry_p) / entry_p if side == "LONG" else (entry_p - exit_p) / entry_p
                        _ot         = st.get_open_trade(sym)
                        half_margin = float(_ot.get("margin", 0)) * 0.5 if _ot else 0.0
                        partial_pnl = round(price_chg * half_margin * lev, 4) if half_margin else 0.0
                        partial_pct = round(price_chg * lev * 100, 2)
                        ap["partial_logged"] = True
                        ap["partial_pnl"]    = round(partial_pnl, 4)
                        st.update_position_memory(sym, partial_logged=True)
                        st.update_open_trade_margin(sym, half_margin)
                        _log_trade(f"{ap['strategy']}_PARTIAL", {
                            "trade_id": trade_id, "symbol": sym, "side": side,
                            "pnl": partial_pnl, "exit_price": round(exit_p, 8),
                            "result": "WIN" if partial_pnl >= 0 else "LOSS",
                            "pnl_pct": partial_pct, "exit_reason": "PARTIAL_TP",
                        })
                        logger.warning(
                            f"[{ap['strategy']}][{sym}] ⚠️  Startup reconcile: partial detected | "
                            f"qty {initial_qty}→{current_qty} | PnL≈{partial_pnl:+.4f} ({partial_pct:+.1f}%)"
                        )

            # Pass B — full SL/TP closes that fired while bot was disconnected.
            if not PAPER_MODE:
                for sym, csv_row in unclosed.items():
                    if sym in existing:
                        continue  # position still open — handled above
                    strategy  = csv_row.get("action", "").split("_")[0]
                    trade_id  = csv_row.get("trade_id", "")
                    side      = csv_row.get("side", "")
                    hist      = tr.get_history_position(
                        sym,
                        open_time_iso=csv_row.get("timestamp"),
                        entry_price=float(csv_row["entry"]) if csv_row.get("entry") else None,
                    )
                    if hist is None:
                        logger.warning(f"[{strategy}][{sym}] ⚠️  Closed while disconnected but history-position unavailable")
                        continue
                    total_pnl = hist["pnl"]
                    exit_p    = hist.get("exit_price")
                    # Subtract already-logged partial PnL to avoid double-counting
                    close_pnl = round(total_pnl - csv_row["partial_pnl"], 4)
                    result    = "WIN" if close_pnl >= 0 else "LOSS"
                    _log_trade(f"{strategy}_CLOSE", {
                        "trade_id":    trade_id,
                        "symbol":      sym,
                        "side":        side,
                        "pnl":         close_pnl,
                        "result":      result,
                        "exit_reason": "RECONCILED",
                        "exit_price":  round(exit_p, 8) if exit_p else None,
                    })
                    st.clear_position_memory(sym)
                    logger.warning(
                        f"[{strategy}][{sym}] ⚠️  Startup reconcile: close detected | "
                        f"PnL≈{close_pnl:+.4f} | exit≈{exit_p}"
                    )
                if unclosed:
                    # Re-sync stats to include any newly logged closes
                    _rebuild_stats_from_csv(config.TRADE_LOG)

        except Exception as e:
            logger.error(f"Startup sync error: {e}")

    def stop(self, *_):
        logger.info("🛑 Stopping bot...")
        st.set_status("STOPPED")
        self.running = False
        sys.exit(0)

    def run(self):
        signal.signal(signal.SIGINT,  self.stop)
        signal.signal(signal.SIGTERM, self.stop)
        logger.info("▶️  Running...\n")
        t = threading.Thread(target=self._entry_watcher_loop, daemon=True, name="entry-watcher")
        t.start()
        logger.info("🔍 Entry watcher started (polling every 4s)")

        while self.running:
            try:
                self._tick()
            except Exception as e:
                logger.error(f"Tick error: {e}", exc_info=True)
                st.add_scan_log(f"Tick error: {e}", "ERROR")
            time.sleep(config.POLL_INTERVAL_SEC)

    def _tick(self):
        now = time.time()

        # ── 1. Rescan ────────────────────────────────────────────── #
        if now - self.last_scan_time >= config.SCAN_INTERVAL_SEC:
            self.qualified_pairs, self.sentiment = get_qualified_pairs_and_sentiment()
            st.update_qualified_pairs(self.qualified_pairs)
            st.update_sentiment(self.sentiment)
            st.add_scan_log(
                f"Market: {self.sentiment.direction} "
                f"({self.sentiment.bullish_weight*100:.1f}% green) | "
                f"🟢{self.sentiment.green_count} 🔴{self.sentiment.red_count} "
                f"of {self.sentiment.total_pairs} pairs",
                "INFO"
            )
            self.last_scan_time = now

        if not self.qualified_pairs or self.sentiment is None:
            return

        # ── 2. Balance ───────────────────────────────────────────── #
        try:
            balance = tr.get_usdt_balance()
            st.update_balance(balance)
        except Exception as e:
            logger.error(f"Balance error: {e}")
            return

        # ── 3. Monitor active trades ──────────────────────────────  #
        if self.active_positions:
            try:
                exchange_positions = tr.get_all_open_positions()
            except Exception as e:
                logger.error(f"Positions error: {e}")
                return

            # Log paper partial closes (50% TP) to CSV
            if PAPER_MODE:
                for pc in tr.drain_partial_closes():
                    _log_trade(f"{pc['strategy']}_PARTIAL", {
                        "trade_id": self.active_positions.get(pc["symbol"], {}).get("trade_id", ""),
                        "symbol": pc["symbol"], "side": pc["side"],
                        "pnl": pc["pnl"], "result": "WIN" if pc["pnl"] >= 0 else "LOSS",
                        "pnl_pct": pc["pnl_pct"], "exit_reason": "PARTIAL_TP",
                        "exit_price": pc.get("exit"),
                    })
                    # Update to REMAINING margin (exchange_positions has the updated value from paper_trader)
                    remaining_margin = exchange_positions.get(pc["symbol"], {}).get("margin", 0)
                    if remaining_margin:
                        st.update_open_trade_margin(pc["symbol"], remaining_margin)
                    logger.info(f"[{pc['strategy']}][{pc['symbol']}] 📊 Partial logged: PnL={pc['pnl']:+.4f} ({pc['pnl_pct']:+.1f}%)")

            # Sync pnl + detect closed positions
            for sym in list(self.active_positions.keys()):
                if sym in exchange_positions:
                    pos = exchange_positions[sym]
                    st.update_open_trade_pnl(sym, pos["unrealised_pnl"])
                    # Backfill margin/leverage for positions resumed at startup
                    _ot_live = st.get_open_trade(sym)
                    if _ot_live and not _ot_live.get("margin") and pos.get("margin"):
                        st.update_open_trade_margin(sym, pos["margin"])
                    if _ot_live and not _ot_live.get("leverage") and pos.get("leverage"):
                        st.update_open_trade_leverage(sym, pos["leverage"])
                    if PAPER_MODE and pos.get("sl"):
                        st.update_open_trade_sl(sym, pos["sl"])
                    ap = self.active_positions[sym]
                    logger.info(
                        f"📊 [{ap['strategy']}][{sym}] {pos['side']} | "
                        f"Entry={pos['entry_price']:.5f} | "
                        f"uPnL={pos['unrealised_pnl']:+.4f} USDT | "
                        f"Box={ap['box_low']:.5f}–{ap['box_high']:.5f}"
                    )

                    # Track initial qty and detect live partial close (S2/S4)
                    if not PAPER_MODE and ap.get("strategy") in ("S2", "S3", "S4", "S5"):
                        if "initial_qty" not in ap:
                            ap["initial_qty"] = float(pos["qty"])
                            st.update_position_memory(sym, initial_qty=float(pos["qty"]))
                        elif (not ap.get("partial_logged") and
                              float(pos["qty"]) < ap["initial_qty"] * 0.75):
                            # Qty dropped below 75% of original → partial TP fired
                            entry_p = pos["entry_price"]
                            partial_qty = ap["initial_qty"] - float(pos["qty"])
                            mark_now = tr.get_mark_price(sym)
                            side = ap["side"]
                            price_chg = (mark_now - entry_p) / entry_p if side == "LONG" else (entry_p - mark_now) / entry_p
                            # Margin for the closed half (approximate from stored trade margin)
                            _ot = st.get_open_trade(sym)
                            half_margin = float(_ot.get("margin", 0)) * 0.5 if _ot else 0.0
                            partial_pnl = price_chg * half_margin * ap.get("leverage", 10) if half_margin else 0.0
                            partial_pct = round(price_chg * ap.get("leverage", 10) * 100, 2)
                            ap["partial_logged"] = True
                            ap["partial_pnl"]    = round(partial_pnl, 4)
                            st.update_position_memory(sym, partial_logged=True)
                            _log_trade(f"{ap['strategy']}_PARTIAL", {
                                "trade_id": ap.get("trade_id", ""),
                                "symbol": sym, "side": side,
                                "pnl": round(partial_pnl, 4),
                                "exit_price": round(mark_now, 8),
                                "result": "WIN" if partial_pnl >= 0 else "LOSS",
                                "pnl_pct": partial_pct, "exit_reason": "PARTIAL_TP",
                            })
                            try:
                                interval = _STRATEGY_CANDLE_INTERVAL.get(ap["strategy"], "15m")
                                _snap_df = tr.get_candles(sym, interval, limit=100)
                                if not _snap_df.empty:
                                    snapshot.save_snapshot(
                                        trade_id=ap.get("trade_id", ""), event="partial",
                                        symbol=sym, interval=interval,
                                        candles=_df_to_candles(_snap_df),
                                        event_price=round(mark_now, 8),
                                    )
                            except Exception as e:
                                logger.warning(f"[{ap['strategy']}][{sym}] partial snapshot failed: {e}")
                            st.update_open_trade_margin(sym, half_margin)
                            logger.info(f"[{ap['strategy']}][{sym}] 📊 Live partial logged: PnL≈{partial_pnl:+.4f} ({partial_pct:+.1f}%)")
                    # Scale-in check (S2/S4 only)
                    if ap.get("scale_in_pending") and time.time() >= ap["scale_in_after"]:
                        self._do_scale_in(sym, ap)

                    # S5 Structural Swing Trail — trail SL to nearest swing high/low (SMC style)
                    if config_s5.S5_USE_CANDLE_STOPS and ap.get("strategy") == "S5":
                        try:
                            cs_df   = tr.get_candles(sym, config_s5.S5_LTF_INTERVAL, limit=config_s5.S5_SWING_LOOKBACK + 5)
                            mark_s5 = tr.get_mark_price(sym)
                            if not cs_df.empty and len(cs_df) >= 3:
                                if ap["side"] == "LONG":
                                    raw = find_swing_low_target(cs_df, mark_s5, lookback=config_s5.S5_SWING_LOOKBACK)
                                    swing_sl = raw * (1 - config_s5.S5_SL_BUFFER_PCT) if raw else None
                                    hold_s   = "long"
                                else:
                                    raw = find_swing_high_target(cs_df, mark_s5, lookback=config_s5.S5_SWING_LOOKBACK)
                                    swing_sl = raw * (1 + config_s5.S5_SL_BUFFER_PCT) if raw else None
                                    hold_s   = "short"
                                if swing_sl is not None and tr.update_position_sl(sym, swing_sl, hold_side=hold_s):
                                    ap["sl"] = swing_sl
                                    st.update_open_trade_sl(sym, swing_sl)
                                    logger.info(
                                        f"[S5][{sym}] 📍 Swing trail: SL → {swing_sl:.5f} "
                                        f"(swing {'low' if ap['side'] == 'LONG' else 'high'} ±{config_s5.S5_SL_BUFFER_PCT*100:.1f}% buffer)"
                                    )
                        except Exception as e:
                            logger.error(f"Swing trail error [{sym}]: {e}")

                    # S3 Structural Swing Trail — trail SL to nearest 15m swing low from entry
                    if config_s3.S3_USE_SWING_TRAIL and ap.get("strategy") == "S3":
                        try:
                            cs_df   = tr.get_candles(sym, config_s3.S3_LTF_INTERVAL, limit=config_s3.S3_SWING_LOOKBACK + 5)
                            mark_s3 = tr.get_mark_price(sym)
                            if not cs_df.empty and len(cs_df) >= 3:
                                raw = find_swing_low_target(cs_df, mark_s3, lookback=config_s3.S3_SWING_LOOKBACK)
                                swing_sl = raw * (1 - config_s3.S3_SL_BUFFER_PCT) if raw else None
                                if swing_sl is not None and tr.update_position_sl(sym, swing_sl, hold_side="long"):
                                    ap["sl"] = swing_sl
                                    st.update_open_trade_sl(sym, swing_sl)
                                    logger.info(f"[S3][{sym}] 📍 Swing trail: SL → {swing_sl:.5f} (nearest 15m swing low)")
                        except Exception as e:
                            logger.error(f"[S3] Swing trail error [{sym}]: {e}")

                    # S2 Structural Swing Trail — after partial, trail SL to nearest daily swing low
                    if config_s2.S2_USE_SWING_TRAIL and ap.get("strategy") == "S2":
                        partial_done = (
                            tr.is_partial_closed(sym) if PAPER_MODE
                            else ap.get("partial_logged", False)
                        )
                        if partial_done:
                            try:
                                cs_df   = tr.get_candles(sym, "1D", limit=config_s2.S2_SWING_LOOKBACK + 5)
                                mark_s2 = tr.get_mark_price(sym)
                                if not cs_df.empty and len(cs_df) >= 3:
                                    raw = find_swing_low_target(cs_df, mark_s2, lookback=config_s2.S2_SWING_LOOKBACK)
                                    swing_sl = raw * (1 - config_s2.S2_STOP_LOSS_PCT) if raw else None
                                    if swing_sl is not None and tr.update_position_sl(sym, swing_sl, hold_side="long"):
                                        ap["sl"] = swing_sl
                                        st.update_open_trade_sl(sym, swing_sl)
                                        logger.info(f"[S2][{sym}] 📍 Swing trail: SL → {swing_sl:.5f} (nearest daily swing low)")
                            except Exception as e:
                                logger.error(f"[S2] Swing trail error [{sym}]: {e}")

                    # S4 Structural Swing Trail — after partial, trail SL to nearest daily swing high
                    if config_s4.S4_USE_SWING_TRAIL and ap.get("strategy") == "S4":
                        partial_done = (
                            tr.is_partial_closed(sym) if PAPER_MODE
                            else ap.get("partial_logged", False)
                        )
                        if partial_done:
                            try:
                                cs_df   = tr.get_candles(sym, "1D", limit=config_s4.S4_SWING_LOOKBACK + 5)
                                mark_s4 = tr.get_mark_price(sym)
                                if not cs_df.empty and len(cs_df) >= 3:
                                    raw = find_swing_high_target(cs_df, mark_s4, lookback=config_s4.S4_SWING_LOOKBACK)
                                    swing_sl = raw * (1 + config_s4.S4_ENTRY_BUFFER) if raw else None
                                    if swing_sl is not None and tr.update_position_sl(sym, swing_sl, hold_side="short"):
                                        ap["sl"] = swing_sl
                                        st.update_open_trade_sl(sym, swing_sl)
                                        logger.info(f"[S4][{sym}] 📍 Swing trail: SL → {swing_sl:.5f} (nearest daily swing high)")
                            except Exception as e:
                                logger.error(f"[S4] Swing trail error [{sym}]: {e}")

                    # Advisory box-break warning
                    try:
                        ltf_df = tr.get_candles(sym, config_s1.LTF_INTERVAL, limit=10)
                        if not ltf_df.empty:
                            flag, reason = check_exit(
                                ltf_df, ap["side"], ap["box_high"], ap["box_low"]
                            )
                            if flag == "EXIT":
                                logger.info(f"⚠️  [{sym}] Box broken — SL should trigger: {reason}")
                                st.add_scan_log(f"[{sym}] Box broken: {reason}", "WARN")
                    except Exception as e:
                        logger.error(f"Exit check error [{sym}]: {e}")
                else:
                    # Position closed by SL/TP — grab last known PnL from state
                    ap       = self.active_positions[sym]
                    _ot      = st.get_open_trade(sym)
                    last_pnl = float(_ot.get("unrealised_pnl") or 0) if _ot else 0.0
                    pnl_pct     = None
                    exit_reason = ""
                    _lc         = None
                    if PAPER_MODE:
                        _lc = tr.get_last_close(sym)
                        if _lc:
                            last_pnl    = _lc["pnl"]
                            pnl_pct     = _lc["pnl_pct"]
                            exit_reason = _lc["reason"]
                    else:
                        # Live: use Bitget realized P/L if available (combines partial + final)
                        realized = tr.get_realized_pnl(sym)
                        if realized is not None:
                            last_pnl = realized
                        # Add partial pnl stored during monitoring if realized API not available
                        elif ap.get("partial_pnl"):
                            last_pnl += ap["partial_pnl"]
                    result = "WIN" if last_pnl >= 0 else "LOSS"
                    logger.info(f"{'✅' if result == 'WIN' else '❌'} [{sym}] Closed ({result}) PnL={last_pnl:+.4f}")
                    st.close_trade(sym, result, last_pnl)
                    if result == "LOSS":
                        st.record_loss(sym)
                        if st.is_pair_paused(sym):
                            logger.info(f"⛔ [{sym}] 3 losses today — paused until tomorrow (UTC)")
                            st.add_scan_log(f"[{sym}] ⛔ Paused for today — 3 losses reached", "WARN")
                    st.add_scan_log(
                        f"[{ap['strategy']}][{sym}] Closed {result} | PnL={last_pnl:+.4f} USDT", "INFO"
                    )
                    try:
                        _exit_price = _lc.get("exit_price") if (PAPER_MODE and _lc) else tr.get_mark_price(sym)
                    except Exception:
                        _exit_price = None
                    if not PAPER_MODE and pnl_pct is None and _exit_price and _ot:
                        _entry = float(_ot.get("entry") or 0)
                        _lev   = float(_ot.get("leverage") or 1)
                        if _entry:
                            _chg = (_exit_price - _entry) / _entry if ap["side"] == "LONG" else (_entry - _exit_price) / _entry
                            pnl_pct = round(_chg * _lev * 100, 2)
                    _log_trade(f"{ap['strategy']}_CLOSE", {
                        "trade_id": ap.get("trade_id", ""),
                        "symbol": sym, "side": ap["side"],
                        "pnl": round(last_pnl, 4), "result": result,
                        "pnl_pct": pnl_pct, "exit_reason": exit_reason,
                        "exit_price": _exit_price,
                    })
                    try:
                        interval = _STRATEGY_CANDLE_INTERVAL.get(ap["strategy"], "15m")
                        _snap_df = tr.get_candles(sym, interval, limit=100)
                        if not _snap_df.empty:
                            snapshot.save_snapshot(
                                trade_id=ap.get("trade_id", ""), event="close",
                                symbol=sym, interval=interval,
                                candles=_df_to_candles(_snap_df),
                                event_price=round(_exit_price, 8) if _exit_price else 0.0,
                            )
                    except Exception as e:
                        logger.warning(f"[{ap['strategy']}][{sym}] close snapshot failed: {e}")
                    st.clear_position_memory(sym)
                    del self.active_positions[sym]

        # ── 4. Check if we can open more trades ───────────────────── #
        open_count = len(self.active_positions)
        if open_count >= config.MAX_CONCURRENT_TRADES:
            logger.info(
                f"⏸️  Max trades reached ({open_count}/{config.MAX_CONCURRENT_TRADES}) — waiting"
            )
            return

        # ── 5. Sentiment gate ─────────────────────────────────────── #
        direction = self.sentiment.direction
        if direction == "NEUTRAL":
            logger.info(f"⏸️  NEUTRAL — S1 paused, S2 scanning...")
            allowed = "BULLISH"  # S2 is LONG-only, still scan on neutral
        elif direction == "BULLISH":
            allowed = "BULLISH"
        elif direction == "BEARISH":
            allowed = "BEARISH"
        else:
            allowed = "NEUTRAL" 
        logger.info(
            f"🌍 {direction} ({self.sentiment.bullish_weight*100:.1f}%) — "
            f"scanning {allowed} | {open_count}/{config.MAX_CONCURRENT_TRADES} trades open"
        )

        # ── 6. Scan pairs — collect candidates, no execution yet ──── #
        self.candidates = []   # reset candidate list each cycle
        for symbol in self.qualified_pairs:
            if not self.running:
                break
            # Skip symbols already in a trade
            if symbol in self.active_positions:
                continue
            # Skip pairs paused due to 3 losses today
            if st.is_pair_paused(symbol):
                continue
            try:
                self._evaluate_pair(symbol, direction, balance)
            except RuntimeError as e:
                if "429" in str(e):
                    logger.warning("Rate limited — backing off 5s")
                    time.sleep(5)
                else:
                    logger.error(f"[{symbol}] Error: {e}", exc_info=True)
            except Exception as e:
                logger.error(f"[{symbol}] Error: {e}", exc_info=True)
            time.sleep(0.4)

        # ── 7. Execute best candidates (priority-ranked across all strategies) ─ #
        self._execute_best_candidate(direction, balance)

    def _evaluate_pair(self, symbol: str, allowed_direction: str, balance: float) -> bool:
        htf_df   = tr.get_candles(symbol, config_s1.HTF_INTERVAL,   limit=15)
        ltf_df   = tr.get_candles(symbol, config_s1.LTF_INTERVAL,   limit=60)
        daily_df = tr.get_candles(symbol, config_s1.DAILY_INTERVAL, limit=250)

        if htf_df.empty or ltf_df.empty or daily_df.empty:
            return False

        # ── Strategy 1 ───────────────────────────────────────────── #
        s1_sig, s1_rsi, s1_bh, s1_bl, s1_adx = "HOLD", 50.0, 0.0, 0.0, 0.0
        if config_s1.S1_ENABLED:
            s1_sig, s1_rsi, s1_bh, s1_bl, s1_adx = evaluate_s1(
                symbol, htf_df, ltf_df, daily_df, allowed_direction
            )
        htf_bull, htf_bear = check_htf(htf_df)

        from strategy import check_daily_trend as _trend
        trend_ok, adx_val = _trend(daily_df, "LONG" if allowed_direction == "BULLISH" else "SHORT")
        rsi_ser = calculate_rsi(ltf_df["close"].astype(float))
        rsi_val = float(rsi_ser.iloc[-1])
        thresh  = config_s1.RSI_LONG_THRESH if allowed_direction == "BULLISH" else config_s1.RSI_SHORT_THRESH
        d_str   = "LONG" if allowed_direction == "BULLISH" else "SHORT"
        is_coil, bh, bl = detect_consolidation(
            ltf_df, rsi_series=rsi_ser, rsi_threshold=thresh, direction=d_str
        )
        close = float(ltf_df["close"].iloc[-1])
        htf_pass = htf_bull if allowed_direction == "BULLISH" else htf_bear
        rsi_ok   = rsi_val > config_s1.RSI_LONG_THRESH if allowed_direction == "BULLISH" \
                   else rsi_val < config_s1.RSI_SHORT_THRESH

        if   not htf_pass:   s1_reason = "No HTF break"
        elif not trend_ok:   s1_reason = f"ADX={adx_val:.1f} < {config_s1.ADX_TREND_THRESHOLD} (sideways)"
        elif not rsi_ok:     s1_reason = f"RSI {rsi_val:.1f} not in zone"
        elif not is_coil:    s1_reason = "No RSI-zone consolidation"
        elif s1_sig == "HOLD": s1_reason = "Waiting breakout"
        else:                s1_reason = f"{s1_sig} ✅"

        logger.info(
            f"[S1][{symbol}] RSI={rsi_val:.1f} | ADX={adx_val:.1f} | "
            f"HTF={'▲' if htf_bull else '▼' if htf_bear else '—'} | "
            f"Coil={'✓' if is_coil else '✗'} | {s1_reason}"
        )

        # ── Strategy 2 (evaluate BEFORE update_pair_state) ───────── #
        s2_sig, s2_rsi, s2_bh, s2_bl, s2_reason = "HOLD", 50.0, 0.0, 0.0, ""
        if self.sentiment.direction != "BEARISH":
            s2_sig, s2_rsi, s2_bh, s2_bl, s2_reason = evaluate_s2(symbol, daily_df)
            logger.info(f"[S2][{symbol}] daily_RSI={s2_rsi:.1f} | {s2_reason}")

        # ── Fetch 15m candles (shared by S3 + S5) ────────────────── #
        m15_df = None
        need_m15 = (
            (config_s3.S3_ENABLED and self.sentiment.direction != "BEARISH") or
            config_s5.S5_ENABLED
        )
        if need_m15:
            m15_df = tr.get_candles(symbol, config_s3.S3_LTF_INTERVAL, limit=300)
            if m15_df.empty:
                m15_df = None

        # ── Strategy 3 ───────────────────────────────────────────── #
        s3_sig, s3_adx, s3_trigger, s3_sl, s3_reason = "HOLD", 0.0, 0.0, 0.0, ""
        s3_sr_resistance_pct   = None
        s3_sr_resistance_price = None
        _d_open = float(daily_df["open"].iloc[-1]) if daily_df is not None and not daily_df.empty else None
        s3_daily_gain_pct = round((close - _d_open) / _d_open * 100, 1) if _d_open else None
        if config_s3.S3_ENABLED and self.sentiment.direction != "BEARISH" and m15_df is not None:
            s3_sig, s3_adx, s3_trigger, s3_sl, s3_reason = evaluate_s3(symbol, m15_df, daily_df)
            logger.info(f"[S3][{symbol}] {s3_reason}")
            # Skip the pre-pullback peak (highest high in last 50 15m candles) —
            # same logic as _execute_s3 resistance check.
            _s3_peak = float(m15_df["high"].iloc[-50:].max())
            _s3_res  = find_nearest_resistance(m15_df, _s3_peak * 1.01, lookback=300)
            s3_sr_resistance_pct   = round((_s3_res - close) / close * 100, 1) if _s3_res else None
            s3_sr_resistance_price = round(_s3_res, 8) if _s3_res else None

        # ── Strategy 5 ───────────────────────────────────────────── #
        s5_sig, s5_trigger, s5_sl, s5_tp, s5_ob_low, s5_ob_high, s5_reason = "HOLD", 0.0, 0.0, 0.0, 0.0, 0.0, ""
        s5_sr_pct = None
        if config_s5.S5_ENABLED and m15_df is not None:
            s5_sig, s5_trigger, s5_sl, s5_tp, s5_ob_low, s5_ob_high, s5_reason = evaluate_s5(
                symbol, daily_df, htf_df, m15_df, allowed_direction
            )
            logger.info(f"[S5][{symbol}] {s5_reason}")
            if s5_sig in ("LONG", "SHORT", "PENDING_LONG", "PENDING_SHORT") and s5_trigger > 0:
                # Compute R:R from returned values
                if s5_sig in ("LONG", "PENDING_LONG") and s5_tp > s5_trigger > s5_sl > 0:
                    _s5_rr = round((s5_tp - s5_trigger) / (s5_trigger - s5_sl), 2)
                elif s5_sig in ("SHORT", "PENDING_SHORT") and 0 < s5_tp < s5_trigger < s5_sl:
                    _s5_rr = round((s5_trigger - s5_tp) / (s5_sl - s5_trigger), 2)
                else:
                    _s5_rr = None
                # Compute S/R clearance from entry trigger (uniform reference for ranking)
                if s5_sig in ("LONG", "PENDING_LONG"):
                    _s5_nr = find_nearest_resistance(m15_df, s5_trigger, lookback=300)
                    s5_sr_pct = round((_s5_nr - s5_trigger) / s5_trigger * 100, 1) if _s5_nr else None
                else:
                    _s5_ns = find_nearest_support(m15_df, s5_trigger, lookback=300)
                    s5_sr_pct = round((s5_trigger - _s5_ns) / s5_trigger * 100, 1) if _s5_ns else None
                # Collect for priority ranking — execution deferred to _execute_best_candidate()
                self.candidates.append({
                    "strategy": "S5", "symbol": symbol, "sig": s5_sig,
                    "trigger": s5_trigger, "sl": s5_sl, "tp": s5_tp,
                    "ob_low": s5_ob_low, "ob_high": s5_ob_high,
                    "reason": s5_reason, "rr": _s5_rr, "sr_pct": s5_sr_pct,
                    "m15_df": m15_df,
                })

        # ── Strategy 4 ───────────────────────────────────────────── #
        s4_sig, s4_rsi, s4_trigger, s4_sl, s4_body_pct, s4_rsi_peak, s4_div, s4_div_str, s4_reason = "HOLD", 50.0, 0.0, 0.0, 0.0, 0.0, False, "", ""
        if config_s4.S4_ENABLED and self.sentiment.direction != "BULLISH":
            s4_sig, s4_rsi, s4_trigger, s4_sl, s4_body_pct, s4_rsi_peak, s4_div, s4_div_str, s4_reason = evaluate_s4(symbol, daily_df)
            logger.info(f"[S4][{symbol}] {s4_reason}")

        # ── S/R Clearance (for dashboard display + entry guard) ──── #
        _sr_res        = find_nearest_resistance(daily_df, close)
        _sr_sup        = find_nearest_support(daily_df, close)
        _s4_base       = find_spike_base(daily_df)
        sr_res_pct     = round((_sr_res - close) / close * 100, 1) if _sr_res else None
        sr_sup_pct     = round((close - _sr_sup) / close * 100, 1) if _sr_sup else None
        s4_sr_sup_pct  = round((close - _s4_base) / close * 100, 1) if _s4_base else None
        # S2-specific resistance: skip the spike peak (same logic as _execute_s2).
        # Apply whenever S2 setup is detected (big candle + coiling), not just on signal,
        # so the chart R line is correct during the coiling/watching phase too.
        if s2_bh > 0:
            _spike_peak  = float(daily_df["high"].iloc[-config_s2.S2_BIG_CANDLE_LOOKBACK:].max())
            _s2_res      = find_nearest_resistance(daily_df, _spike_peak * 1.01)
            s2_sr_resistance_pct   = round((_s2_res - close) / close * 100, 1) if _s2_res else None
            s2_sr_resistance_price = round(_s2_res, 8) if _s2_res else None
        else:
            s2_sr_resistance_pct   = sr_res_pct
            s2_sr_resistance_price = None

        st.update_pair_state(symbol, {
            "rsi": rsi_val, "htf_bull": htf_bull, "htf_bear": htf_bear,
            "signal": s1_sig if s1_sig != "HOLD" else (s2_sig if s2_sig != "HOLD" else (s3_sig if s3_sig != "HOLD" else (s4_sig if s4_sig != "HOLD" else ("PENDING" if s5_sig.startswith("PENDING") else s5_sig)))),
            "s1_signal": s1_sig,
            "s2_signal": s2_sig,
            "price": close,
            "consolidating": is_coil, "box_high": round(bh,6) if bh else None,
            "box_low": round(bl,6) if bl else None,
            "reason":    s1_reason,
            "s2_reason": s2_reason,
            "s3_reason": s3_reason,
            "s3_signal": s3_sig,
            "s3_adx": round(s3_adx, 1) if s3_adx else None,
            "s3_daily_gain_pct": s3_daily_gain_pct,
            "s4_reason": s4_reason,
            "s4_signal": s4_sig,
            "s5_reason": s5_reason,
            "s5_signal": s5_sig if s5_sig in ("LONG", "SHORT", "HOLD") else "PENDING",
            "s5_ob_low":  round(s5_ob_low,  8) if s5_ob_low  else None,
            "s5_ob_high": round(s5_ob_high, 8) if s5_ob_high else None,
            "s5_entry_trigger": round(s5_trigger, 8) if s5_trigger else None,
            "s5_sl":      round(s5_sl,      8) if s5_sl       else None,
            "s5_tp":      round(s5_tp,      8) if s5_tp       else None,
            "s5_sr_pct":  s5_sr_pct,
            "rsi_ok": rsi_ok,
            "adx": round(adx_val, 1), "trend_ok": trend_ok,
            "strategy": "S1" if s1_sig != "HOLD" else ("S2" if s2_sig != "HOLD" else ("S3" if s3_sig != "HOLD" else ("S4" if s4_sig != "HOLD" else ("S5" if s5_sig not in ("HOLD", "") else "S1")))),
            "s2_daily_rsi": s2_rsi,
            "s2_big_candle": s2_rsi > 0 and ("big_candle" in s2_reason or "Big candle" in s2_reason or s2_bh > 0),
            "s2_coiling":    s2_bl > 0 and s2_bh > 0,
            "s2_box_low":    round(s2_bl, 8) if s2_bl > 0 else None,
            "s2_box_high":   round(s2_bh, 8) if s2_bh > 0 else None,
            "sr_resistance_pct":    sr_res_pct,
            "s2_sr_resistance_pct":   s2_sr_resistance_pct,
            "s2_sr_resistance_price": s2_sr_resistance_price,
            "s3_sr_resistance_pct":   s3_sr_resistance_pct,
            "s3_sr_resistance_price": s3_sr_resistance_price if s3_trigger > 0 else None,
            "sr_support_pct":       sr_sup_pct,
            "s4_sr_support_pct":    s4_sr_sup_pct,
            # Reset S5 priority rank each cycle — patched in by _execute_best_s5_candidate
            "s5_priority_rank":  None,
            "s5_priority_score": None,
        })

        # ── Min balance check ─────────────────────────────────────── #
        min_bal = 5.0 / (config_s1.TRADE_SIZE_PCT * config_s1.LEVERAGE)
        if balance < min_bal:
            st.add_scan_log(f"[{symbol}] Skipped — balance ${balance:.2f} < ${min_bal:.2f}", "WARN")
            return False

        # ── Collect S1 candidate ──────────────────────────────────── #
        if s1_sig in ("LONG", "SHORT") and allowed_direction != "NEUTRAL":
            lev = config_s1.LEVERAGE
            last_red_low  = next((float(r["low"])  for _, r in ltf_df.iloc[::-1].iterrows() if float(r["close"]) < float(r["open"])), None)
            last_grn_high = next((float(r["high"]) for _, r in ltf_df.iloc[::-1].iterrows() if float(r["close"]) > float(r["open"])), None)
            pnl50_long  = close * (1 - 0.50 / lev)
            pnl50_short = close * (1 + 0.50 / lev)
            sl_long_est  = max(pnl50_long,  last_red_low  * 0.998 if last_red_low  else pnl50_long)
            sl_short_est = min(pnl50_short, last_grn_high * 1.002 if last_grn_high else pnl50_short)
            if s1_sig == "LONG" and close > sl_long_est:
                s1_rr = round(config_s1.TAKE_PROFIT_PCT / ((close - sl_long_est) / close), 2)
            elif s1_sig == "SHORT" and sl_short_est > close:
                s1_rr = round(config_s1.TAKE_PROFIT_PCT / ((sl_short_est - close) / close), 2)
            else:
                s1_rr = None
            self.candidates.append({
                "strategy": "S1", "symbol": symbol, "sig": s1_sig,
                "rr": s1_rr,
                "sr_pct": sr_res_pct if s1_sig == "LONG" else sr_sup_pct,
                "s1_bh": s1_bh, "s1_bl": s1_bl,
                "rsi_val": rsi_val, "adx_val": adx_val,
                "htf_bull": htf_bull, "htf_bear": htf_bear, "is_coil": is_coil,
                "ltf_df": ltf_df, "allowed_direction": allowed_direction,
            })

        # ── Collect S2 candidate ──────────────────────────────────── #
        if s2_sig == "LONG":
            s2_rr = round(config_s2.S2_TAKE_PROFIT_PCT / config_s2.S2_STOP_LOSS_PCT, 2)
            self.candidates.append({
                "strategy": "S2", "symbol": symbol, "sig": "LONG",
                "rr": s2_rr, "sr_pct": s2_sr_resistance_pct,
                "s2_bh": s2_bh, "s2_bl": s2_bl,
                "s2_rsi": s2_rsi, "s2_reason": s2_reason, "daily_df": daily_df,
            })

        # ── Collect S3 candidate ──────────────────────────────────── #
        if s3_sig == "LONG":
            s3_rr = round(config_s3.S3_TRAILING_TRIGGER_PCT * s3_trigger / (s3_trigger - s3_sl), 2) \
                    if s3_trigger and s3_sl and s3_trigger > s3_sl else None
            self.candidates.append({
                "strategy": "S3", "symbol": symbol, "sig": "LONG",
                "rr": s3_rr, "sr_pct": s3_sr_resistance_pct,
                "s3_trigger": s3_trigger, "s3_sl": s3_sl,
                "s3_adx": s3_adx, "s3_reason": s3_reason,
                "s3_sr_resistance_pct": s3_sr_resistance_pct, "m15_df": m15_df,
            })

        # ── Collect S4 candidate ──────────────────────────────────── #
        if s4_sig == "SHORT" and s4_trigger > 0:
            self.candidates.append({
                "strategy": "S4", "symbol": symbol, "sig": "SHORT",
                "rr": None, "sr_pct": s4_sr_sup_pct,
                "s4_trigger": s4_trigger, "s4_sl": s4_sl,
                "s4_rsi": s4_rsi, "s4_rsi_peak": s4_rsi_peak,
                "s4_body_pct": s4_body_pct, "s4_div": s4_div, "s4_div_str": s4_div_str,
                "s4_reason": s4_reason, "daily_df": daily_df,
            })

        # All strategies deferred — executed by _execute_best_candidate()
        return False

    # ── Priority Evaluation (all strategies) ─────────────────────── #

    def _execute_best_candidate(self, direction: str, balance: float) -> None:
        """Rank all candidates (S1–S5, LONG/SHORT/PENDING) by R:R + S/R and execute/queue in order."""
        if not self.candidates:
            return

        def _score(c: dict) -> float:
            # Primary: R:R (weight 10×); Secondary: S/R clearance %
            return (c["rr"] or 0) * 10 + (c["sr_pct"] or 0)

        ranked = sorted(self.candidates, key=_score, reverse=True)

        # Assign rank/score to every candidate — shared reference for entry watcher
        for i, c in enumerate(ranked):
            c["priority_rank"]  = i + 1
            c["priority_score"] = round(_score(c), 1)

        # Push rank badges to dashboard for S5 immediate candidates
        for c in ranked:
            if c["strategy"] == "S5" and c["sig"] in ("LONG", "SHORT"):
                st.patch_pair_state(c["symbol"], {
                    "s5_priority_rank":  c["priority_rank"],
                    "s5_priority_score": c["priority_score"],
                })

        if len(ranked) > 1:
            logger.info(f"[RANK] {len(ranked)} candidates — ranked by R:R + S/R clearance:")
            for c in ranked:
                logger.info(
                    f"  #{c['priority_rank']} [{c['strategy']}][{c['symbol']}]: "
                    f"R:R={c['rr']} SR={c['sr_pct']}% sig={c['sig']} score={c['priority_score']}"
                )

        _dispatchers = {
            "S1": self._execute_s1,
            "S2": self._execute_s2,
            "S3": self._execute_s3,
            "S4": self._execute_s4,
        }

        for candidate in ranked:
            sym      = candidate["symbol"]
            sig      = candidate["sig"]
            strategy = candidate["strategy"]

            if sig in ("PENDING_LONG", "PENDING_SHORT"):
                # S5 PENDING only — queue with rank so entry watcher respects ordering
                if sym not in self.pending_signals and not st.is_pair_paused(sym):
                    self._queue_s5_pending(
                        sym, sig, candidate["trigger"], candidate["sl"], candidate["tp"],
                        candidate["ob_low"], candidate["ob_high"], candidate["m15_df"],
                        priority_rank=candidate["priority_rank"],
                        priority_score=candidate["priority_score"],
                    )
                continue

            # Immediate LONG/SHORT — stop if slots full
            if len(self.active_positions) >= config.MAX_CONCURRENT_TRADES:
                break
            if sym in self.active_positions or st.is_pair_paused(sym):
                continue

            if strategy == "S5":
                min_bal = 5.0 / (config_s5.S5_TRADE_SIZE_PCT * config_s5.S5_LEVERAGE)
                if balance < min_bal:
                    continue
                self._execute_s5(
                    sym, sig, candidate["trigger"], candidate["sl"], candidate["tp"],
                    candidate["ob_low"], candidate["ob_high"], candidate["reason"],
                    candidate["m15_df"], balance,
                )
            elif strategy in _dispatchers:
                min_bal = 5.0 / (config_s1.TRADE_SIZE_PCT * config_s1.LEVERAGE)
                if balance < min_bal:
                    continue
                _dispatchers[strategy](candidate, balance)

    # ── Scale-in executor ────────────────────────────────────────── #

    def _do_scale_in(self, sym: str, ap: dict) -> None:
        """Execute scale-in for S2/S4 and save candle snapshot."""
        try:
            mark_now  = tr.get_mark_price(sym)
            in_window = False
            if ap["strategy"] == "S2":
                in_window = ap["box_high"] <= mark_now <= ap["box_high"] * (1 + config_s2.S2_MAX_ENTRY_BUFFER)
            elif ap["strategy"] == "S4":
                pl = ap["s4_prev_low"]
                in_window = pl * (1 - config_s4.S4_MAX_ENTRY_BUFFER) <= mark_now <= pl * (1 - config_s4.S4_ENTRY_BUFFER)
            remaining = ap["scale_in_trade_size_pct"] * 0.5
            if in_window:
                if ap["strategy"] == "S2":
                    tr.scale_in_long(sym, remaining, config_s2.S2_LEVERAGE)
                else:
                    tr.scale_in_short(sym, remaining, config_s4.S4_LEVERAGE)
                logger.info(f"[{ap['strategy']}][{sym}] ✅ Scale-in +{remaining*100:.0f}% @ {mark_now:.5f}")
                st.add_scan_log(f"[{ap['strategy']}][{sym}] Scale-in executed @ {mark_now:.5f}", "INFO")
                _log_trade(f"{ap['strategy']}_SCALE_IN", {
                    "trade_id": ap.get("trade_id", ""),
                    "symbol": sym, "side": ap["side"],
                    "entry": round(mark_now, 8),
                })
                if PAPER_MODE:
                    updated_pos = tr.get_all_open_positions().get(sym, {})
                    if updated_pos.get("margin"):
                        st.update_open_trade_margin(sym, updated_pos["margin"])
                # Save scale-in snapshot
                try:
                    interval = _STRATEGY_CANDLE_INTERVAL.get(ap["strategy"], "15m")
                    _snap_df = tr.get_candles(sym, interval, limit=100)
                    if not _snap_df.empty:
                        snapshot.save_snapshot(
                            trade_id=ap.get("trade_id", ""), event="scale_in",
                            symbol=sym, interval=interval,
                            candles=_df_to_candles(_snap_df),
                            event_price=round(mark_now, 8),
                        )
                except Exception as e:
                    logger.warning(f"[{ap['strategy']}][{sym}] scale_in snapshot failed: {e}")
            else:
                logger.info(f"[{ap['strategy']}][{sym}] ⏸️ Scale-in skipped — price {mark_now:.5f} outside entry window")
            ap["scale_in_pending"] = False
        except Exception as e:
            logger.error(f"Scale-in error [{sym}]: {e}")
            ap["scale_in_pending"] = False

    # ── Per-strategy executors ────────────────────────────────────── #

    def _execute_s1(self, c: dict, balance: float) -> bool:
        symbol = c["symbol"]
        if symbol in self.active_positions:
            return False
        s1_sig = c["sig"]
        ltf_df = c["ltf_df"]
        lev    = config_s1.LEVERAGE
        mark_now = tr.get_mark_price(symbol)
        pnl50_long  = mark_now * (1 - 0.50 / lev)
        pnl50_short = mark_now * (1 + 0.50 / lev)
        last_red_low  = next((float(r["low"])  for _, r in ltf_df.iloc[::-1].iterrows() if float(r["close"]) < float(r["open"])), None)
        last_grn_high = next((float(r["high"]) for _, r in ltf_df.iloc[::-1].iterrows() if float(r["close"]) > float(r["open"])), None)
        sl_long  = max(pnl50_long,  last_red_low  * 0.998 if last_red_low  else pnl50_long)
        sl_short = min(pnl50_short, last_grn_high * 1.002 if last_grn_high else pnl50_short)
        st.add_scan_log(
            f"[S1][{symbol}] {'🟢' if s1_sig == 'LONG' else '🔴'} {s1_sig} | "
            f"RSI={c['rsi_val']:.1f} ADX={c['adx_val']:.1f} | rank=#{c['priority_rank']}",
            "SIGNAL"
        )
        if s1_sig == "LONG":
            trade = tr.open_long(symbol, sl_floor=sl_long, leverage=lev,
                                 trade_size_pct=config_s1.TRADE_SIZE_PCT,
                                 take_profit_pct=config_s1.TAKE_PROFIT_PCT)
        else:
            trade = tr.open_short(symbol, sl_floor=sl_short, leverage=lev,
                                  trade_size_pct=config_s1.TRADE_SIZE_PCT,
                                  take_profit_pct=config_s1.TAKE_PROFIT_PCT)
        trade["strategy"] = "S1"
        trade["snap_rsi"]           = round(c["rsi_val"], 1)
        trade["snap_adx"]           = round(c["adx_val"], 1)
        trade["snap_htf"]           = "BULL" if c["htf_bull"] else "BEAR" if c["htf_bear"] else "NONE"
        trade["snap_coil"]          = c["is_coil"]
        trade["snap_box_range_pct"] = round((c["s1_bh"] - c["s1_bl"]) / c["s1_bl"] * 100, 3) if c["s1_bh"] and c["s1_bl"] else None
        trade["snap_sentiment"]     = self.sentiment.direction
        trade["trade_id"] = uuid.uuid4().hex[:8]
        _log_trade(f"S1_{s1_sig}", trade)
        st.add_open_trade(trade)
        try:
            snapshot.save_snapshot(
                trade_id=trade["trade_id"], event="open",
                symbol=symbol, interval=config_s1.LTF_INTERVAL,
                candles=_df_to_candles(ltf_df),
                event_price=float(trade.get("entry", 0)),
            )
        except Exception as e:
            logger.warning(f"[S1][{symbol}] snapshot save failed: {e}")
        if PAPER_MODE: tr.tag_strategy(symbol, "S1")
        self.active_positions[symbol] = {
            "side": s1_sig, "strategy": "S1",
            "box_high": c["s1_bh"], "box_low": c["s1_bl"],
            "trade_id": trade["trade_id"],
        }
        return True

    def _execute_s2(self, c: dict, balance: float) -> bool:
        symbol = c["symbol"]
        if symbol in self.active_positions:
            return False
        mark_now = tr.get_mark_price(symbol)
        s2_bh = c["s2_bh"]
        if mark_now > s2_bh * (1 + config_s2.S2_MAX_ENTRY_BUFFER):
            logger.info(f"[S2][{symbol}] ⏸️ LONG entry missed — price {mark_now:.5f} >{config_s2.S2_MAX_ENTRY_BUFFER*100:.0f}% above trigger {s2_bh:.5f}")
            return False
        # S2 setup: big spike candle → coil (consolidation under spike) → breakout.
        # s2_bh is the coil top, NOT the spike high. The spike peak sits above the coil
        # and is picked up as resistance, but S2 is designed to trade through it.
        # Find the spike peak (highest high in the big-candle lookback window) and
        # search for pre-existing resistance only above that level.
        _daily = c["daily_df"]
        _spike_peak = float(_daily["high"].iloc[-config_s2.S2_BIG_CANDLE_LOOKBACK:].max())
        nearest_res = find_nearest_resistance(_daily, _spike_peak * 1.01)
        if nearest_res is not None:
            clearance = (nearest_res - mark_now) / mark_now
            if clearance < config_s2.S2_MIN_SR_CLEARANCE:
                logger.info(f"[S2][{symbol}] ⏸️ LONG skipped — resistance {nearest_res:.5f} only {clearance*100:.1f}% away")
                st.add_scan_log(f"[S2][{symbol}] ⛔ Resistance {nearest_res:.5f} too close ({clearance*100:.1f}%)", "WARN")
                return False
        if config.CLAUDE_FILTER_ENABLED:
            _sr_str = f"{round((nearest_res - mark_now) / mark_now * 100, 1)}%" if nearest_res else "none found"
            _cd = claude_approve("S2", symbol, {
                "RSI": round(c["s2_rsi"], 1), "S/R clearance": _sr_str,
                "Sentiment": self.sentiment.direction,
                "Entry": round(mark_now, 5), "SL": round(c["s2_bl"], 5),
            })
            if not _cd["approved"]:
                logger.info(f"[S2][{symbol}] 🤖 Claude rejected: {_cd['reason']}")
                st.add_scan_log(f"[S2][{symbol}] 🤖 Rejected: {_cd['reason']}", "WARN")
                return False
        st.add_scan_log(f"[S2][{symbol}] 🟢 LONG | {c['s2_reason']} | rank=#{c['priority_rank']}", "SIGNAL")
        trade = tr.open_long(symbol, box_low=c["s2_bl"], leverage=config_s2.S2_LEVERAGE,
                             trade_size_pct=config_s2.S2_TRADE_SIZE_PCT * 0.5,
                             take_profit_pct=config_s2.S2_TAKE_PROFIT_PCT,
                             stop_loss_pct=config_s2.S2_STOP_LOSS_PCT,
                             use_s2_exits=True)
        trade["strategy"] = "S2"
        trade["snap_daily_rsi"]        = round(c["s2_rsi"], 1)
        trade["snap_box_range_pct"]    = round((s2_bh - c["s2_bl"]) / c["s2_bl"] * 100, 3) if s2_bh and c["s2_bl"] else None
        trade["snap_sentiment"]        = self.sentiment.direction
        trade["snap_sr_clearance_pct"] = round((nearest_res - mark_now) / mark_now * 100, 1) if nearest_res else None
        trade["trade_id"] = uuid.uuid4().hex[:8]
        _log_trade("S2_LONG", trade)
        st.add_open_trade(trade)
        try:
            snapshot.save_snapshot(
                trade_id=trade["trade_id"], event="open",
                symbol=symbol, interval="1D",
                candles=_df_to_candles(c["daily_df"]),
                event_price=float(trade.get("entry", 0)),
            )
        except Exception as e:
            logger.warning(f"[S2][{symbol}] snapshot save failed: {e}")
        if PAPER_MODE: tr.tag_strategy(symbol, "S2")
        self.active_positions[symbol] = {
            "side": "LONG", "strategy": "S2",
            "box_high": s2_bh if s2_bh else 0.0, "box_low": c["s2_bl"],
            "scale_in_pending": True, "scale_in_after": time.time() + 3600,
            "scale_in_trade_size_pct": config_s2.S2_TRADE_SIZE_PCT,
            "trade_id": trade["trade_id"],
        }
        return True

    def _execute_s3(self, c: dict, balance: float) -> bool:
        symbol = c["symbol"]
        if symbol in self.active_positions:
            return False
        mark_now = tr.get_mark_price(symbol)
        s3_trigger = c["s3_trigger"]
        if mark_now > s3_trigger * (1 + config_s3.S3_MAX_ENTRY_BUFFER):
            logger.info(f"[S3][{symbol}] ⏸️ LONG entry missed — price {mark_now:.5f} >{config_s3.S3_MAX_ENTRY_BUFFER*100:.0f}% above trigger {s3_trigger:.5f}")
            return False
        # S3 is a pullback strategy: the swing high that caused the Stochastic to go
        # oversold is the pre-pullback peak — it's part of the setup, not a resistance
        # to avoid. Find that recent peak and search for resistance above it.
        _m15 = c["m15_df"]
        if _m15 is not None:
            _recent_peak = float(_m15["high"].iloc[-50:].max())  # highest high in last ~12h
            _res_floor   = _recent_peak * 1.01
        else:
            _res_floor = c["s3_trigger"]
        nearest_res = find_nearest_resistance(_m15, _res_floor, lookback=300) if _m15 is not None else None
        if nearest_res is not None:
            clearance = (nearest_res - mark_now) / mark_now
            if clearance < config_s3.S3_MIN_SR_CLEARANCE:
                logger.info(f"[S3][{symbol}] ⏸️ LONG skipped — 15m resistance {nearest_res:.5f} only {clearance*100:.1f}% away")
                st.add_scan_log(f"[S3][{symbol}] ⛔ 15m resistance {nearest_res:.5f} too close ({clearance*100:.1f}%)", "WARN")
                return False
        if config.CLAUDE_FILTER_ENABLED:
            _sr_str = f"{c['s3_sr_resistance_pct']}%" if c["s3_sr_resistance_pct"] else "none found"
            _cd = claude_approve("S3", symbol, {
                "ADX": round(c["s3_adx"], 1) if c["s3_adx"] else "?",
                "S/R clearance (15m)": _sr_str, "Sentiment": self.sentiment.direction,
                "Entry": round(mark_now, 5), "SL": round(c["s3_sl"], 5),
            })
            if not _cd["approved"]:
                logger.info(f"[S3][{symbol}] 🤖 Claude rejected: {_cd['reason']}")
                st.add_scan_log(f"[S3][{symbol}] 🤖 Rejected: {_cd['reason']}", "WARN")
                return False
        st.add_scan_log(f"[S3][{symbol}] 🟢 LONG | {c['s3_reason']} | rank=#{c['priority_rank']}", "SIGNAL")
        trade = tr.open_long(symbol, sl_floor=c["s3_sl"], leverage=config_s3.S3_LEVERAGE,
                             trade_size_pct=config_s3.S3_TRADE_SIZE_PCT, use_s2_exits=True)
        trade["strategy"] = "S3"
        trade["snap_adx"]              = round(c["s3_adx"], 1) if c["s3_adx"] else None
        trade["snap_entry_trigger"]    = round(s3_trigger, 8)
        trade["snap_sl"]               = round(c["s3_sl"], 8)
        trade["snap_rr"]               = round(config_s3.S3_TRAILING_TRIGGER_PCT * s3_trigger / (s3_trigger - c["s3_sl"]), 2) \
                                         if s3_trigger and c["s3_sl"] and s3_trigger > c["s3_sl"] else None
        trade["snap_sentiment"]        = self.sentiment.direction
        trade["snap_sr_clearance_pct"] = c["s3_sr_resistance_pct"]
        trade["trade_id"] = uuid.uuid4().hex[:8]
        _log_trade("S3_LONG", trade)
        st.add_open_trade(trade)
        try:
            snapshot.save_snapshot(
                trade_id=trade["trade_id"], event="open",
                symbol=symbol, interval=config_s3.S3_LTF_INTERVAL,
                candles=_df_to_candles(c["m15_df"]),
                event_price=float(trade.get("entry", 0)),
            )
        except Exception as e:
            logger.warning(f"[S3][{symbol}] snapshot save failed: {e}")
        if PAPER_MODE: tr.tag_strategy(symbol, "S3")
        self.active_positions[symbol] = {
            "side": "LONG", "strategy": "S3",
            "box_high": s3_trigger, "box_low": c["s3_sl"],
            "trade_id": trade["trade_id"],
        }
        return True

    def _execute_s4(self, c: dict, balance: float) -> bool:
        symbol = c["symbol"]
        if symbol in self.active_positions:
            return False
        mark_now        = tr.get_mark_price(symbol)
        s4_trigger      = c["s4_trigger"]
        prev_low_approx = s4_trigger / (1 - config_s4.S4_ENTRY_BUFFER)
        too_far         = mark_now < prev_low_approx * (1 - config_s4.S4_MAX_ENTRY_BUFFER)
        if too_far:
            logger.info(
                f"[S4][{symbol}] ⏸️ SHORT entry missed — "
                f"price {mark_now:.5f} >{config_s4.S4_MAX_ENTRY_BUFFER*100:.0f}% below prev_low {prev_low_approx:.5f}"
            )
            return False
        if mark_now > s4_trigger:
            return False  # price not yet in entry window
        spike_base = find_spike_base(c["daily_df"])
        if spike_base is not None:
            clearance = (mark_now - spike_base) / mark_now
            if clearance < config_s4.S4_MIN_SR_CLEARANCE:
                logger.info(f"[S4][{symbol}] ⏸️ SHORT skipped — pre-pump base {spike_base:.5f} only {clearance*100:.1f}% away")
                st.add_scan_log(f"[S4][{symbol}] ⛔ Pre-pump base {spike_base:.5f} too close ({clearance*100:.1f}%)", "WARN")
                return False
        if config.CLAUDE_FILTER_ENABLED:
            _sr_str = f"{round((mark_now - spike_base) / mark_now * 100, 1)}%" if spike_base else "none found"
            _cd = claude_approve("S4", symbol, {
                "RSI peak": round(c["s4_rsi_peak"], 1), "RSI divergence": str(c["s4_div"]),
                "S/R clearance (spike base)": _sr_str, "Sentiment": self.sentiment.direction,
                "Entry": round(s4_trigger, 5), "SL": round(c["s4_sl"], 5),
            })
            if not _cd["approved"]:
                logger.info(f"[S4][{symbol}] 🤖 Claude rejected: {_cd['reason']}")
                st.add_scan_log(f"[S4][{symbol}] 🤖 Rejected: {_cd['reason']}", "WARN")
                return False
        st.add_scan_log(
            f"[S4][{symbol}] 🔴 SHORT | spike={c['s4_body_pct']*100:.0f}% RSI={c['s4_rsi']:.1f} | "
            f"entry≤{s4_trigger:.5f} @ {mark_now:.5f} | rank=#{c['priority_rank']}",
            "SIGNAL"
        )
        s4_sl_actual = mark_now * (1 + 0.50 / config_s4.S4_LEVERAGE)
        trade = tr.open_short(symbol, sl_floor=s4_sl_actual, leverage=config_s4.S4_LEVERAGE,
                              trade_size_pct=config_s4.S4_TRADE_SIZE_PCT * 0.5, use_s4_exits=True)
        trade["strategy"]              = "S4"
        trade["snap_rsi"]              = round(c["s4_rsi"], 1)
        trade["snap_rsi_peak"]         = round(c["s4_rsi_peak"], 1)
        trade["snap_spike_body_pct"]   = round(c["s4_body_pct"] * 100, 1)
        trade["snap_rsi_div"]          = c["s4_div"]
        trade["snap_rsi_div_str"]      = c["s4_div_str"]
        trade["snap_sl"]               = round(s4_sl_actual, 8)
        trade["snap_sentiment"]        = self.sentiment.direction
        trade["snap_sr_clearance_pct"] = round((mark_now - spike_base) / mark_now * 100, 1) if spike_base else None
        trade["trade_id"] = uuid.uuid4().hex[:8]
        _log_trade("S4_SHORT", trade)
        st.add_open_trade(trade)
        try:
            snapshot.save_snapshot(
                trade_id=trade["trade_id"], event="open",
                symbol=symbol, interval="1D",
                candles=_df_to_candles(c["daily_df"]),
                event_price=float(trade.get("entry", 0)),
            )
        except Exception as e:
            logger.warning(f"[S4][{symbol}] snapshot save failed: {e}")
        if PAPER_MODE: tr.tag_strategy(symbol, "S4")
        self.active_positions[symbol] = {
            "side": "SHORT", "strategy": "S4",
            "box_high": c["s4_sl"], "box_low": s4_trigger,
            "scale_in_pending": True, "scale_in_after": time.time() + 3600,
            "scale_in_trade_size_pct": config_s4.S4_TRADE_SIZE_PCT,
            "s4_prev_low": prev_low_approx,
            "trade_id": trade["trade_id"],
        }
        return True

    def _execute_s5(self, symbol: str, s5_sig: str, s5_trigger: float, s5_sl: float,
                    s5_tp: float, s5_ob_low: float, s5_ob_high: float,
                    s5_reason: str, m15_df, balance: float) -> bool:
        """Open an S5 LONG or SHORT trade. Returns True if the trade was opened."""
        if symbol in self.active_positions:
            return False
        mark_now = tr.get_mark_price(symbol)

        if s5_sig == "LONG":
            if mark_now < s5_trigger:
                logger.info(f"[S5][{symbol}] ⏸️ LONG signal stale — price {mark_now:.5f} fell back below entry {s5_trigger:.5f}")
                return False
            if mark_now > s5_trigger * (1 + config_s5.S5_MAX_ENTRY_BUFFER):
                logger.info(f"[S5][{symbol}] ⏸️ LONG entry missed — price already >{config_s5.S5_MAX_ENTRY_BUFFER*100:.0f}% above trigger {s5_trigger:.5f}")
                return False
            nearest_res = find_nearest_resistance(m15_df, mark_now, lookback=300) if m15_df is not None else None
            if nearest_res is not None:
                clearance = (nearest_res - mark_now) / mark_now
                if clearance < config_s5.S5_MIN_SR_CLEARANCE:
                    logger.info(
                        f"[S5][{symbol}] ⏸️ LONG skipped — 15m resistance {nearest_res:.5f} "
                        f"only {clearance*100:.1f}% away (min {config_s5.S5_MIN_SR_CLEARANCE*100:.0f}%)"
                    )
                    st.add_scan_log(f"[S5][{symbol}] ⛔ 15m resistance {nearest_res:.5f} too close ({clearance*100:.1f}%)", "WARN")
                    return False
            if config.CLAUDE_FILTER_ENABLED:
                _sr_str = f"{round((nearest_res - mark_now) / mark_now * 100, 1)}%" if nearest_res else "none found"
                _cd = claude_approve("S5", symbol, {
                    "OB zone": f"{s5_ob_low:.5f}–{s5_ob_high:.5f}",
                    "S/R clearance (15m)": _sr_str,
                    "Sentiment": self.sentiment.direction,
                    "Entry": round(mark_now, 5),
                    "SL": round(s5_sl, 5),
                })
                if not _cd["approved"]:
                    logger.info(f"[S5][{symbol}] 🤖 Claude rejected: {_cd['reason']}")
                    st.add_scan_log(f"[S5][{symbol}] 🤖 Rejected: {_cd['reason']}", "WARN")
                    return False
            st.add_scan_log(f"[S5][{symbol}] 🟢 LONG | {s5_reason}", "SIGNAL")
            trade = tr.open_long(
                symbol,
                sl_floor       = s5_sl,
                leverage       = config_s5.S5_LEVERAGE,
                trade_size_pct = config_s5.S5_TRADE_SIZE_PCT,
                use_s5_exits   = True,
                tp_price_abs   = s5_tp,
            )
            trade["strategy"]            = "S5"
            trade["snap_entry_trigger"]  = round(s5_trigger, 8)
            trade["snap_sl"]             = round(s5_sl, 8)
            trade["snap_rr"]             = round(
                (s5_tp - s5_trigger) / (s5_trigger - s5_sl), 2
            ) if s5_tp > s5_trigger > s5_sl > 0 else None
            trade["snap_sentiment"]      = self.sentiment.direction
            trade["snap_sr_clearance_pct"] = round((nearest_res - mark_now) / mark_now * 100, 1) if nearest_res else None
            trade["snap_s5_ob_low"]      = round(s5_ob_low,  8) if s5_ob_low  else None
            trade["snap_s5_ob_high"]     = round(s5_ob_high, 8) if s5_ob_high else None
            trade["snap_s5_tp"]          = round(s5_tp, 8) if s5_tp else None
            trade["trade_id"] = uuid.uuid4().hex[:8]
            _log_trade("S5_LONG", trade)
            st.add_open_trade(trade)
            try:
                if m15_df is not None:
                    snapshot.save_snapshot(
                        trade_id=trade["trade_id"], event="open",
                        symbol=symbol, interval=config_s5.S5_LTF_INTERVAL,
                        candles=_df_to_candles(m15_df),
                        event_price=float(trade.get("entry", 0)),
                    )
            except Exception as e:
                logger.warning(f"[S5][{symbol}] snapshot save failed: {e}")
            if PAPER_MODE: tr.tag_strategy(symbol, "S5")
            self.active_positions[symbol] = {
                "side": "LONG", "strategy": "S5",
                "box_high": s5_trigger, "box_low": s5_sl,
                "trade_id": trade["trade_id"],
            }
            return True

        else:  # SHORT
            if mark_now > s5_trigger:
                logger.info(f"[S5][{symbol}] ⏸️ SHORT signal stale — price {mark_now:.5f} bounced above entry {s5_trigger:.5f}")
                return False
            if mark_now < s5_trigger * (1 - config_s5.S5_MAX_ENTRY_BUFFER):
                logger.info(f"[S5][{symbol}] ⏸️ SHORT entry missed — price already >{config_s5.S5_MAX_ENTRY_BUFFER*100:.0f}% below trigger {s5_trigger:.5f}")
                return False
            nearest_sup = find_nearest_support(m15_df, mark_now, lookback=300) if m15_df is not None else None
            if nearest_sup is not None:
                clearance = (mark_now - nearest_sup) / mark_now
                if clearance < config_s5.S5_MIN_SR_CLEARANCE:
                    logger.info(
                        f"[S5][{symbol}] ⏸️ SHORT skipped — 15m support {nearest_sup:.5f} "
                        f"only {clearance*100:.1f}% away (min {config_s5.S5_MIN_SR_CLEARANCE*100:.0f}%)"
                    )
                    st.add_scan_log(f"[S5][{symbol}] ⛔ 15m support {nearest_sup:.5f} too close ({clearance*100:.1f}%)", "WARN")
                    return False
            if config.CLAUDE_FILTER_ENABLED:
                _sr_str = f"{round((mark_now - nearest_sup) / mark_now * 100, 1)}%" if nearest_sup else "none found"
                _cd = claude_approve("S5", symbol, {
                    "OB zone": f"{s5_ob_low:.5f}–{s5_ob_high:.5f}",
                    "S/R clearance (15m)": _sr_str,
                    "Sentiment": self.sentiment.direction,
                    "Entry": round(mark_now, 5),
                    "SL": round(s5_sl, 5),
                })
                if not _cd["approved"]:
                    logger.info(f"[S5][{symbol}] 🤖 Claude rejected: {_cd['reason']}")
                    st.add_scan_log(f"[S5][{symbol}] 🤖 Rejected: {_cd['reason']}", "WARN")
                    return False
            st.add_scan_log(
                f"[S5][{symbol}] 🔴 SHORT | OB {s5_ob_low:.5f}–{s5_ob_high:.5f} | "
                f"entry≤{s5_trigger:.5f} triggered @ {mark_now:.5f}",
                "SIGNAL"
            )
            trade = tr.open_short(
                symbol,
                sl_floor       = s5_sl,
                leverage       = config_s5.S5_LEVERAGE,
                trade_size_pct = config_s5.S5_TRADE_SIZE_PCT,
                use_s5_exits   = True,
                tp_price_abs   = s5_tp,
            )
            trade["strategy"]            = "S5"
            trade["snap_entry_trigger"]  = round(s5_trigger, 8)
            trade["snap_sl"]             = round(s5_sl, 8)
            trade["snap_rr"]             = round(
                (s5_trigger - s5_tp) / (s5_sl - s5_trigger), 2
            ) if 0 < s5_tp < s5_trigger < s5_sl else None
            trade["snap_sentiment"]      = self.sentiment.direction
            trade["snap_sr_clearance_pct"] = round((mark_now - nearest_sup) / mark_now * 100, 1) if nearest_sup else None
            trade["snap_s5_ob_low"]      = round(s5_ob_low,  8) if s5_ob_low  else None
            trade["snap_s5_ob_high"]     = round(s5_ob_high, 8) if s5_ob_high else None
            trade["snap_s5_tp"]          = round(s5_tp, 8) if s5_tp else None
            trade["trade_id"] = uuid.uuid4().hex[:8]
            _log_trade("S5_SHORT", trade)
            st.add_open_trade(trade)
            try:
                if m15_df is not None:
                    snapshot.save_snapshot(
                        trade_id=trade["trade_id"], event="open",
                        symbol=symbol, interval=config_s5.S5_LTF_INTERVAL,
                        candles=_df_to_candles(m15_df),
                        event_price=float(trade.get("entry", 0)),
                    )
            except Exception as e:
                logger.warning(f"[S5][{symbol}] snapshot save failed: {e}")
            if PAPER_MODE: tr.tag_strategy(symbol, "S5")
            self.active_positions[symbol] = {
                "side": "SHORT", "strategy": "S5",
                "box_high": s5_sl, "box_low": s5_trigger,
                "trade_id": trade["trade_id"],
            }
            return True

    # ── Entry Watcher ─────────────────────────────────────────────── #

    def _queue_s5_pending(self, symbol: str, sig: str, trigger: float, sl: float,
                          tp: float, ob_low: float, ob_high: float, m15_df,
                          priority_rank: int = 999, priority_score: float = 0.0) -> None:
        """Pre-validate an S5 PENDING signal (S/R + Claude) and add to pending_signals."""
        side = "LONG" if sig == "PENDING_LONG" else "SHORT"
        # S/R clearance check using already-fetched m15_df
        nearest = None
        clearance = None
        if side == "LONG":
            nearest = find_nearest_resistance(m15_df, trigger, lookback=300)
            if nearest is not None:
                clearance = (nearest - trigger) / trigger
                if clearance < config_s5.S5_MIN_SR_CLEARANCE:
                    logger.info(
                        f"[S5][{symbol}] ⛔ PENDING: resistance {nearest:.5f} "
                        f"only {clearance*100:.1f}% away — not queuing"
                    )
                    return
        else:
            nearest = find_nearest_support(m15_df, trigger, lookback=300)
            if nearest is not None:
                clearance = (trigger - nearest) / trigger
                if clearance < config_s5.S5_MIN_SR_CLEARANCE:
                    logger.info(
                        f"[S5][{symbol}] ⛔ PENDING: support {nearest:.5f} "
                        f"only {clearance*100:.1f}% away — not queuing"
                    )
                    return
        sr_pct = round(clearance * 100, 1) if clearance is not None else None
        # Claude filter
        if config.CLAUDE_FILTER_ENABLED:
            sr_str = f"{sr_pct}%" if sr_pct is not None else "none found"
            _cd = claude_approve("S5", symbol, {
                "OB zone":            f"{ob_low:.5f}–{ob_high:.5f}",
                "S/R clearance (15m)": sr_str,
                "Sentiment":           self.sentiment.direction if self.sentiment else "?",
                "Entry trigger":       round(trigger, 5),
                "SL":                  round(sl, 5),
            })
            if not _cd["approved"]:
                logger.info(f"[S5][{symbol}] 🤖 PENDING rejected: {_cd['reason']}")
                st.add_scan_log(f"[S5][{symbol}] 🤖 PENDING rejected: {_cd['reason']}", "WARN")
                return
        rr = round((tp - trigger) / (trigger - sl), 2) if side == "LONG" and tp > trigger > sl > 0 \
             else round((trigger - tp) / (sl - trigger), 2) if 0 < tp < trigger < sl else None
        self.pending_signals[symbol] = {
            "strategy": "S5", "side": side,
            "trigger": trigger, "sl": sl, "tp": tp,
            "ob_low": ob_low, "ob_high": ob_high,
            "rr": rr, "sr_clearance_pct": sr_pct,
            "sentiment": self.sentiment.direction if self.sentiment else "?",
            "expires": time.time() + 4 * 3600,
            "priority_rank": priority_rank,
            "priority_score": priority_score,
            "order_id": None,   # filled in below
            "qty_str": None,    # filled in below (needed by _handle_limit_filled)
        }
        # Place the GTC limit order immediately — SL preset so position is protected on fill
        if not PAPER_MODE:
            try:
                balance  = tr.get_usdt_balance()
                equity   = tr._get_total_equity() or balance
                notional = equity * config_s5.S5_TRADE_SIZE_PCT * config_s5.S5_LEVERAGE
                mark     = tr.get_mark_price(symbol)
                # NOTE: qty snapped at queue time, not fill time — acceptable for S5 (positions sized conservatively)
                qty_str  = tr._round_qty(notional / mark, symbol)
                if side == "LONG":
                    order_id = tr.place_limit_long(symbol, trigger, sl, tp, qty_str)
                else:
                    order_id = tr.place_limit_short(symbol, trigger, sl, tp, qty_str)
                self.pending_signals[symbol]["order_id"] = order_id
                self.pending_signals[symbol]["qty_str"]  = qty_str
                logger.info(
                    f"[S5][{symbol}] 📋 Limit {side} placed @ {trigger:.5f} | "
                    f"order_id={order_id} | SL={sl:.5f} | TP={tp:.5f}"
                )
            except Exception as e:
                logger.error(f"[S5][{symbol}] ❌ Failed to place limit order: {e}")
                self.pending_signals.pop(symbol, None)
                return
        else:
            self.pending_signals[symbol]["order_id"] = "PAPER"
        logger.info(
            f"[S5][{symbol}] 🕐 PENDING {side} queued | "
            f"trigger={trigger:.5f} | SL={sl:.5f} | TP={tp:.5f} | R:R={rr}"
        )
        st.add_scan_log(
            f"[S5][{symbol}] 🕐 PENDING {side} | trigger={trigger:.5f} | TP={tp:.5f}", "SIGNAL"
        )

    def _entry_watcher_loop(self) -> None:
        """Background thread — polls every 4s for:
        1. S5 pending signals: poll order fill status + OB invalidation + expiry
        2. Non-S5 pending signals (S3, etc.): price-trigger check (legacy path)
        3. (Paper only) Active position SL/TP simulation
        """
        while self.running:
            # Paper position monitor — run _check_exit at 4s resolution
            if PAPER_MODE and self.active_positions:
                try:
                    tr.get_all_open_positions()   # internally calls _check_exit for each position
                except Exception as e:
                    logger.debug(f"Paper position poll error: {e}")

            if self.pending_signals:
                try:
                    balance = tr.get_usdt_balance()
                except Exception:
                    time.sleep(4)
                    continue
                # Process pending signals in priority rank order (best setup first)
                ordered = sorted(
                    self.pending_signals.items(),
                    key=lambda kv: kv[1].get("priority_rank", 999),
                )
                for symbol, sig in ordered:
                    if not sig:
                        continue
                    # Already in a trade — clear the pending signal
                    if symbol in self.active_positions:
                        self.pending_signals.pop(symbol, None)
                        continue

                    strategy = sig.get("strategy")

                    if strategy == "S5":
                        # ── S5: order-fill polling path ──────────────── #
                        order_id = sig.get("order_id")
                        try:
                            mark = tr.get_mark_price(symbol)
                        except Exception:
                            continue
                        side = sig["side"]

                        # Determine fill info (real vs paper)
                        fill_info = None
                        if PAPER_MODE and order_id == "PAPER":
                            # Simulate fill by price comparison
                            # LONG limit BUY fills when price DROPS to trigger (mark <= trigger)
                            # SHORT limit SELL fills when price RISES to trigger (mark >= trigger)
                            paper_triggered = (
                                (side == "LONG"  and mark <= sig["trigger"]) or
                                (side == "SHORT" and mark >= sig["trigger"])
                            )
                            if paper_triggered:
                                fill_info = {"status": "filled", "fill_price": sig["trigger"]}
                            else:
                                fill_info = {"status": "live", "fill_price": 0.0}
                        else:
                            try:
                                fill_info = tr.get_order_fill(symbol, order_id)
                            except Exception as e:
                                logger.warning(f"[S5][{symbol}] get_order_fill error: {e}")
                                continue

                        if fill_info["status"] == "filled":
                            with self._trade_lock:
                                if symbol in self.active_positions:
                                    self.pending_signals.pop(symbol, None)
                                    continue
                                if len(self.active_positions) >= config.MAX_CONCURRENT_TRADES:
                                    break
                                if st.is_pair_paused(symbol):
                                    continue
                                self._handle_limit_filled(symbol, sig, fill_info["fill_price"], balance)
                            self.pending_signals.pop(symbol, None)

                        elif (side == "LONG"  and
                              mark < sig["ob_low"] * (1 - config_s5.S5_OB_INVALIDATION_BUFFER_PCT)):
                            try:
                                tr.cancel_order(symbol, order_id)
                            except Exception as e:
                                logger.warning(f"[S5][{symbol}] cancel_order error: {e}")
                            logger.info(
                                f"[S5][{symbol}] ❌ Limit cancelled — OB invalidated (mark={mark:.5f})"
                            )
                            st.add_scan_log(f"[S5][{symbol}] ❌ OB invalidated — limit cancelled", "INFO")
                            self.pending_signals.pop(symbol, None)

                        elif (side == "SHORT" and
                              mark > sig["ob_high"] * (1 + config_s5.S5_OB_INVALIDATION_BUFFER_PCT)):
                            try:
                                tr.cancel_order(symbol, order_id)
                            except Exception as e:
                                logger.warning(f"[S5][{symbol}] cancel_order error: {e}")
                            logger.info(
                                f"[S5][{symbol}] ❌ Limit cancelled — OB invalidated (mark={mark:.5f})"
                            )
                            st.add_scan_log(f"[S5][{symbol}] ❌ OB invalidated — limit cancelled", "INFO")
                            self.pending_signals.pop(symbol, None)

                        elif time.time() > sig["expires"]:
                            try:
                                tr.cancel_order(symbol, order_id)
                            except Exception as e:
                                logger.warning(f"[S5][{symbol}] cancel_order error: {e}")
                            logger.info(f"[S5][{symbol}] ⏰ Limit cancelled — expired")
                            st.add_scan_log(f"[S5][{symbol}] ⏰ Limit expired — cancelled", "INFO")
                            self.pending_signals.pop(symbol, None)

                    else:
                        # ── Non-S5 (S3, etc.): legacy price-trigger path ── #
                        # Expire stale signals
                        if time.time() > sig["expires"]:
                            logger.info(f"[{strategy}][{symbol}] ⏰ Pending signal expired — removing")
                            st.add_scan_log(f"[{strategy}][{symbol}] ⏰ Pending expired", "INFO")
                            self.pending_signals.pop(symbol, None)
                            continue
                        try:
                            mark = tr.get_mark_price(symbol)
                        except Exception:
                            continue
                        side = sig["side"]
                        trigger = sig["trigger"]
                        triggered = (side == "LONG"  and mark >= trigger) or \
                                    (side == "SHORT" and mark <= trigger)
                        if triggered:
                            with self._trade_lock:
                                if symbol in self.active_positions:
                                    self.pending_signals.pop(symbol, None)
                                    continue
                                if len(self.active_positions) >= config.MAX_CONCURRENT_TRADES:
                                    break
                                if st.is_pair_paused(symbol):
                                    continue
                                self._fire_pending(symbol, sig, mark, balance)
                            self.pending_signals.pop(symbol, None)

            time.sleep(4)

    def _fire_pending(self, symbol: str, sig: dict, mark_now: float, balance: float) -> None:
        """Open a trade for a triggered pending signal. Called under _trade_lock."""
        side = sig["side"]
        logger.info(
            f"[S5][{symbol}] 🎯 Entry watcher triggered {side} @ {mark_now:.5f} "
            f"(trigger={sig['trigger']:.5f})"
        )
        if side == "LONG":
            trade = tr.open_long(
                symbol,
                sl_floor       = sig["sl"],
                leverage       = config_s5.S5_LEVERAGE,
                trade_size_pct = config_s5.S5_TRADE_SIZE_PCT,
                use_s5_exits   = True,
                tp_price_abs   = sig["tp"],
            )
        else:
            trade = tr.open_short(
                symbol,
                sl_floor       = sig["sl"],
                leverage       = config_s5.S5_LEVERAGE,
                trade_size_pct = config_s5.S5_TRADE_SIZE_PCT,
                use_s5_exits   = True,
                tp_price_abs   = sig["tp"],
            )
        trade["strategy"]              = "S5"
        trade["snap_entry_trigger"]    = round(sig["trigger"], 8)
        trade["snap_sl"]               = round(sig["sl"], 8)
        trade["snap_rr"]               = sig.get("rr")
        trade["snap_sentiment"]        = sig.get("sentiment", "?")
        trade["snap_sr_clearance_pct"] = sig.get("sr_clearance_pct")
        trade["snap_s5_ob_low"]        = round(sig["ob_low"],  8) if sig.get("ob_low")  else None
        trade["snap_s5_ob_high"]       = round(sig["ob_high"], 8) if sig.get("ob_high") else None
        trade["snap_s5_tp"]            = round(sig["tp"], 8) if sig.get("tp") else None
        trade["trade_id"] = uuid.uuid4().hex[:8]
        _log_trade(f"S5_{side}", trade)
        st.add_open_trade(trade)
        if PAPER_MODE:
            tr.tag_strategy(symbol, "S5")
        self.active_positions[symbol] = {
            "side": side, "strategy": "S5",
            "box_high": sig["trigger"] if side == "LONG" else sig["sl"],
            "box_low":  sig["sl"]      if side == "LONG" else sig["trigger"],
            "trade_id": trade["trade_id"],
        }
        st.add_scan_log(
            f"[S5][{symbol}] 🎯 Entry watcher: {side} @ {mark_now:.5f} | "
            f"SL={sig['sl']:.5f} | TP={sig['tp']:.5f}", "SIGNAL"
        )

    def _handle_limit_filled(self, symbol: str, sig: dict, fill_price: float, balance: float) -> None:
        """Called when a GTC limit order fills. Sets up exits and logs the trade."""
        side     = sig["side"]
        sl_price = sig["sl"]
        tp_price = sig["tp"]
        qty_str  = sig.get("qty_str") or "0"

        if PAPER_MODE:
            # Paper mode: delegate to open_long/open_short which handle exits internally.
            # _round_price and _place_s5_exits are not available in paper_trader.
            if side == "LONG":
                trade = tr.open_long(
                    symbol,
                    sl_floor       = sl_price,
                    leverage       = config_s5.S5_LEVERAGE,
                    trade_size_pct = config_s5.S5_TRADE_SIZE_PCT,
                    use_s5_exits   = True,
                    tp_price_abs   = tp_price,
                )
            else:
                trade = tr.open_short(
                    symbol,
                    sl_floor       = sl_price,
                    leverage       = config_s5.S5_LEVERAGE,
                    trade_size_pct = config_s5.S5_TRADE_SIZE_PCT,
                    use_s5_exits   = True,
                    tp_price_abs   = tp_price,
                )
            sl_trig  = trade.get("sl", sl_price)
            tp_targ  = trade.get("tp", tp_price)
            trade_id = uuid.uuid4().hex[:8]
            trade.update({
                "symbol":   symbol,
                "side":     side,
                "qty":      qty_str,
                "entry":    fill_price,
                "sl":       sl_trig,
                "tp":       tp_targ,
                "leverage": config_s5.S5_LEVERAGE,
                "margin":   round(balance * config_s5.S5_TRADE_SIZE_PCT, 4),
                "tpsl_set": True,
                "strategy": "S5",
                "snap_entry_trigger":    round(sig["trigger"], 8),
                "snap_sl":               round(sig["sl"], 8),
                "snap_rr":               sig.get("rr"),
                "snap_sentiment":        sig.get("sentiment", "?"),
                "snap_sr_clearance_pct": sig.get("sr_clearance_pct"),
                "snap_s5_ob_low":        round(sig["ob_low"],  8) if sig.get("ob_low")  else None,
                "snap_s5_ob_high":       round(sig["ob_high"], 8) if sig.get("ob_high") else None,
                "snap_s5_tp":            round(sig["tp"], 8)      if sig.get("tp")      else None,
                "trade_id": trade_id,
            })
            _log_trade(f"S5_{side}", trade)
            st.add_open_trade(trade)
            tr.tag_strategy(symbol, "S5")
            self.active_positions[symbol] = {
                "side": side, "strategy": "S5",
                "box_high": sig["trigger"] if side == "LONG" else sig["sl"],
                "box_low":  sig["sl"]      if side == "LONG" else sig["trigger"],
                "trade_id": trade_id,
            }
            logger.info(
                f"[S5][{symbol}] ✅ [PAPER] Limit filled {side} @ {fill_price:.5f} | "
                f"SL={sl_trig} | TP={tp_targ}"
            )
            st.add_scan_log(
                f"[S5][{symbol}] ✅ [PAPER] Limit filled {side} @ {fill_price:.5f} | "
                f"SL={sl_trig} | TP={tp_price:.5f}", "SIGNAL"
            )
            return

        # ── Real trader path ─────────────────────────────────────────── #
        # Compute SL execution price and 1:1 partial TP trigger (mirrors open_long/short with use_s5_exits)
        if side == "LONG":
            sl_trig   = float(tr._round_price(sl_price, symbol))
            sl_exec   = float(tr._round_price(sl_trig * 0.995, symbol))
            one_r     = fill_price - sl_trig
            part_trig = float(tr._round_price(fill_price + one_r, symbol))
            tp_targ   = float(tr._round_price(tp_price, symbol)) if tp_price > fill_price else 0.0
        else:
            sl_trig   = float(tr._round_price(sl_price, symbol))
            sl_exec   = float(tr._round_price(sl_trig * 1.005, symbol))
            one_r     = sl_trig - fill_price
            part_trig = float(tr._round_price(fill_price - one_r, symbol))
            tp_targ   = float(tr._round_price(tp_price, symbol)) if 0 < tp_price < fill_price else 0.0

        tr._place_s5_exits(
            symbol,
            side.lower(),
            qty_str,
            sl_trig, sl_exec,
            part_trig, tp_targ,
            config_s5.S5_TRAIL_RANGE_PCT,
        )

        trade_id = uuid.uuid4().hex[:8]
        trade = {
            "symbol":   symbol,
            "side":     side,
            "qty":      qty_str,
            "entry":    fill_price,
            "sl":       sl_trig,
            "tp":       tp_targ if tp_targ > 0 else part_trig,
            "leverage": config_s5.S5_LEVERAGE,
            "margin":   round(balance * config_s5.S5_TRADE_SIZE_PCT, 4),
            "tpsl_set": True,
            "strategy": "S5",
            "snap_entry_trigger":    round(sig["trigger"], 8),
            "snap_sl":               round(sig["sl"], 8),
            "snap_rr":               sig.get("rr"),
            "snap_sentiment":        sig.get("sentiment", "?"),
            "snap_sr_clearance_pct": sig.get("sr_clearance_pct"),
            "snap_s5_ob_low":        round(sig["ob_low"],  8) if sig.get("ob_low")  else None,
            "snap_s5_ob_high":       round(sig["ob_high"], 8) if sig.get("ob_high") else None,
            "snap_s5_tp":            round(sig["tp"], 8)      if sig.get("tp")      else None,
            "trade_id": trade_id,
        }
        _log_trade(f"S5_{side}", trade)
        st.add_open_trade(trade)
        self.active_positions[symbol] = {
            "side": side, "strategy": "S5",
            "box_high": sig["trigger"] if side == "LONG" else sig["sl"],
            "box_low":  sig["sl"]      if side == "LONG" else sig["trigger"],
            "trade_id": trade_id,
        }
        logger.info(
            f"[S5][{symbol}] ✅ Limit filled {side} @ {fill_price:.5f} | "
            f"SL={sl_trig} | TP={'trail' if tp_targ == 0 else tp_targ}"
        )
        st.add_scan_log(
            f"[S5][{symbol}] ✅ Limit filled {side} @ {fill_price:.5f} | "
            f"SL={sl_trig} | TP={tp_price:.5f}", "SIGNAL"
        )


if __name__ == "__main__":
    MTFBot().run()