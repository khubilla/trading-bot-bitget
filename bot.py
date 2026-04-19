"""
bot.py — Main Entry Point

Runs Strategy 1 and Strategy 2 simultaneously.
Only 1 active trade at a time across both strategies.

Strategy 1: MTF RSI Breakout (ADX trend filter, 1H break, 3m RSI+coil+breakout)
Strategy 2: 30-Day Breakout + 3m Consolidation (long candle, squeeze, 3m coil+break)
"""

import time, signal, sys, logging, csv, os, threading, uuid
from datetime import datetime, timezone
from pathlib import Path

import config
import config_s1
import config_s2
import config_s3
import config_s4
import config_s5
import config_s6
import state as st
from scanner import get_qualified_pairs_and_sentiment
from strategies.s1 import evaluate_s1, detect_consolidation, check_daily_trend, check_exit
from strategies.s2 import evaluate_s2
from strategies.s3 import evaluate_s3
from strategies.s4 import evaluate_s4
from strategies.s5 import evaluate_s5
from strategies.s6 import evaluate_s6
from indicators import calculate_rsi
from tools import (
    check_htf,
    find_nearest_resistance, find_nearest_support, find_spike_base,
    find_bullish_ob, find_bearish_ob,
)
from claude_filter import claude_approve
from trade_dna import snapshot as dna_snapshot
import snapshot

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
    # S6 snapshot
    "snap_s6_peak", "snap_s6_drop_pct", "snap_s6_rsi_at_peak",
    # S/R clearance at entry (S2/S3/S4/S5/S6)
    "snap_sr_clearance_pct",
    # Trade DNA trend fingerprint (recorded at entry for future pattern-match filter)
    "snap_trend_daily_ema_slope", "snap_trend_daily_price_vs_ema",
    "snap_trend_daily_rsi_bucket", "snap_trend_daily_adx_state",
    "snap_trend_h1_ema_slope", "snap_trend_h1_price_vs_ema",
    "snap_trend_m15_ema_slope", "snap_trend_m15_price_vs_ema",
    "snap_trend_m15_adx_state",
    "snap_trend_m3_price_vs_ema",
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


def _snapshot_interval(strategy: str) -> str:
    """Return the candle interval this strategy uses for event snapshots.

    Each strategy module owns its own `SNAPSHOT_INTERVAL`; missing modules
    fall back to "15m" so unknown strategies never break the snapshot path.
    """
    from importlib import import_module
    try:
        return import_module(f"strategies.{strategy.lower()}").SNAPSHOT_INTERVAL
    except (ImportError, AttributeError):
        return "15m"


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
        pnl_pct = round((total_pnl / config.INITIAL_BALANCE) * 100, 2) if config.INITIAL_BALANCE > 0 else 0.0
        st.set_stats(wins, losses, round(total_pnl, 4), pnl_pct)
        logger.info(f"📊 Stats loaded from CSV: {wins}W / {losses}L | Total PnL={total_pnl:+.2f} ({pnl_pct:+.1f}%)")


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
        # Entry watcher — pending signals waiting for price trigger (all strategies)
        self.pending_signals: dict[str, dict] = st.load_pending_signals()
        self._trade_lock = threading.Lock()
        # Priority evaluation — candidates collected each scan cycle (all strategies)
        self.candidates: list = []

        st.reset()
        st.set_status("RUNNING")
        # Rebuild win/loss stats from CSV so header survives restarts
        _rebuild_stats_from_csv(config.TRADE_LOG)
        st.add_scan_log("Bot initialised (S1 + S2 + S3 + S4 + S5 + S6)", "INFO")

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
                    "trade_id": _csv.get("trade_id", "") if _csv else "",
                }
                try:
                    _resumed_ap["sl"] = float(_sl)
                except (TypeError, ValueError):
                    pass
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
                    "trade_id":  _csv.get("trade_id", "") if _csv else "",
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
                    if ap.get("strategy") not in ("S1", "S2", "S3", "S4", "S5", "S6"):
                        continue
                    if ap.get("partial_logged"):
                        continue
                    # If CSV already has a partial row, sync the flag and skip
                    csv_data = unclosed.get(sym, {})
                    if csv_data.get("partial_logged"):
                        ap["partial_logged"] = True
                        ap["partial_pnl"] = csv_data.get("partial_pnl", 0.0)
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
                        try:
                            _si = _snapshot_interval(ap["strategy"])
                            _sdf = tr.get_candles(sym, _si, limit=100)
                            if not _sdf.empty:
                                snapshot.save_snapshot(
                                    trade_id=trade_id, event="partial",
                                    symbol=sym, interval=_si,
                                    candles=_df_to_candles(_sdf),
                                    event_price=round(exit_p, 8),
                                )
                        except Exception as e:
                            logger.warning(f"[{ap['strategy']}][{sym}] startup partial snapshot failed: {e}")
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
                    try:
                        _si = _snapshot_interval(strategy)
                        _sdf = tr.get_candles(sym, _si, limit=100)
                        if not _sdf.empty:
                            snapshot.save_snapshot(
                                trade_id=trade_id, event="close",
                                symbol=sym, interval=_si,
                                candles=_df_to_candles(_sdf),
                                event_price=round(exit_p, 8) if exit_p else 0.0,
                            )
                    except Exception as e:
                        logger.warning(f"[{strategy}][{sym}] startup close snapshot failed: {e}")
                    st.clear_position_memory(sym)
                    logger.warning(
                        f"[{strategy}][{sym}] ⚠️  Startup reconcile: close detected | "
                        f"PnL≈{close_pnl:+.4f} | exit≈{exit_p}"
                    )
                if unclosed:
                    # Re-sync stats to include any newly logged closes
                    _rebuild_stats_from_csv(config.TRADE_LOG)

            # Always rebuild stats from CSV as source of truth (catches any
            # manual CSV corrections or PnL accounting fixes across restarts)
            _rebuild_stats_from_csv(config.TRADE_LOG)

            # ── Startup recovery: positions that filled while bot was stopped ── #
            if not PAPER_MODE:
                try:
                    self._startup_recovery(existing)
                except Exception as _e:
                    logger.warning(f"Startup recovery failed: {_e}")

        except Exception as e:
            logger.error(f"Startup sync error: {e}")

    def _startup_recovery(self, existing: dict) -> None:
        """
        Detect and recover positions that filled while the bot was stopped.

        Pass 1: positions on exchange with no CSV open row.
        Pass 2: pending signals whose limit orders filled AND closed while down.

        Called once from __init__ after Pass B. PAPER_MODE: skip (no limit orders).
        """
        from startup_recovery import fetch_candles_at, estimate_sl_tp, attempt_s5_recovery

        try:
            balance = tr.get_usdt_balance()
        except Exception as _e:
            logger.warning(f"Startup recovery: could not fetch balance: {_e}")
            balance = 0.0

        recovered = 0

        # ── Pass 1: open positions with no CSV record ──────────────── #
        for sym, pos in existing.items():
            try:
                if _get_open_csv_row(config.TRADE_LOG, sym) is not None:
                    continue  # CSV exists — handled by Pass A/B

                sig = self.pending_signals.get(sym)

                if sig and sig.get("order_id") and sig["order_id"] != "PAPER":
                    # Happy path — original signal data available
                    try:
                        fill_info = tr.get_order_fill(sym, sig["order_id"])
                    except Exception as _e:
                        logger.warning(
                            f"[S5][{sym}] ⚠️ Startup recovery: get_order_fill failed: {_e}"
                        )
                        continue

                    if fill_info["status"] == "live":
                        continue  # still pending — not a crash case

                    if fill_info["status"] == "filled":
                        fill_price = fill_info["fill_price"]
                        logger.warning(
                            f"[S5][{sym}] ⚠️ Startup recovery [happy]: limit filled while "
                            f"bot was down @ {fill_price:.5f} — running _handle_limit_filled"
                        )
                        st.add_scan_log(
                            f"[S5][{sym}] ⚠️ Recovery [happy]: filled @ {fill_price:.5f}", "WARN"
                        )
                        self._handle_limit_filled(sym, sig, fill_price, balance)
                        self.pending_signals.pop(sym, None)
                        st.save_pending_signals(self.pending_signals)
                        recovered += 1

                else:
                    # Sad path — no pending signal; estimate SL/TP from entry
                    _ot       = st.get_open_trade(sym)
                    entry     = float(pos.get("entry_price") or pos.get("entry", 0))
                    side      = pos.get("side", "SHORT")
                    margin    = float(pos.get("margin", 0))
                    leverage  = int(float(pos.get("leverage") or 10))
                    qty       = float(pos.get("qty", 0))
                    opened_at = (
                        (_ot or {}).get("opened_at")
                        or datetime.now(timezone.utc).isoformat()
                    )
                    trade_id  = uuid.uuid4().hex[:8]

                    # Parse opened_at → end_ms for Bitget historical candle fetch
                    try:
                        end_ms = int(
                            datetime.fromisoformat(opened_at).timestamp() * 1000
                        ) + 60_000
                    except Exception:
                        end_ms = int(time.time() * 1000)

                    # Fetch historical candles at fill time
                    m15_df   = fetch_candles_at(sym, "15m", limit=100, end_ms=end_ms)
                    htf_df   = fetch_candles_at(sym, "1H",  limit=50,  end_ms=end_ms)
                    daily_df = tr.get_candles(sym, "1D", limit=60)

                    # Try to recover OB/SL/TP from historical candles
                    result = None
                    if not m15_df.empty and not htf_df.empty and not daily_df.empty:
                        result = attempt_s5_recovery(sym, m15_df, htf_df, daily_df, side)

                    if result:
                        sl, tp, ob_low, ob_high = result
                    else:
                        sl, tp, ob_low, ob_high = estimate_sl_tp(entry, side)

                    # Patch active_positions
                    if sym in self.active_positions:
                        self.active_positions[sym].update({
                            "strategy": "UNKNOWN",
                            "sl":       sl,
                            "box_high": ob_high,
                            "box_low":  ob_low,
                            "trade_id": trade_id,
                        })

                    # Patch state.json open_trades entry
                    _s = st._read()
                    for _t in _s["open_trades"]:
                        if _t["symbol"] == sym:
                            _t.update({
                                "trade_id": trade_id,
                                "sl":       round(sl, 8),
                                "tp":       round(tp, 8),
                                "box_high": round(ob_high, 8),
                                "box_low":  round(ob_low,  8),
                                "tpsl_set": False,
                            })
                            break
                    st._write(_s)

                    # Append open row to trades.csv
                    _log_trade(f"UNKNOWN_{side}", {
                        "trade_id":        trade_id,
                        "symbol":          sym,
                        "side":            side,
                        "qty":             qty,
                        "entry":           entry,
                        "sl":              round(sl,      8),
                        "tp":              round(tp,      8),
                        "box_low":         round(ob_low,  8),
                        "box_high":        round(ob_high, 8),
                        "leverage":        leverage,
                        "margin":          round(margin,  8),
                        "tpsl_set":        False,
                        "strategy":        "UNKNOWN",
                        "snap_s5_ob_low":  round(ob_low,  8),
                        "snap_s5_ob_high": round(ob_high, 8),
                        "snap_s5_tp":      round(tp,      8),
                    })

                    # Save open snapshot (only if candles available)
                    if not m15_df.empty:
                        snapshot.save_snapshot(
                            trade_id=trade_id,
                            event="open",
                            symbol=sym,
                            interval="15m",
                            candles=_df_to_candles(m15_df),
                            event_price=entry,
                            captured_at=opened_at,
                        )

                    logger.warning(
                        f"[UNKNOWN][{sym}] ⚠️ Startup recovery [sad]: no signal data | "
                        f"SL≈{sl:.5f} | tpsl_set=False — manual TPSL needed"
                    )
                    st.add_scan_log(
                        f"[UNKNOWN][{sym}] ⚠️ Recovery [sad]: tpsl_set=False — manual TPSL needed",
                        "WARN",
                    )
                    recovered += 1

            except Exception as _e:
                logger.warning(f"[{sym}] Startup recovery error: {_e}")

        # ── Pass 2: pending signals whose limit filled AND position closed while down ── #
        for sym, sig in list(self.pending_signals.items()):
            try:
                if sym in self.active_positions:
                    continue  # already handled in Pass 1 or normal startup

                order_id = sig.get("order_id")
                if not order_id or order_id == "PAPER":
                    continue  # non-S5 or paper-mode signal — skip

                try:
                    fill_info = tr.get_order_fill(sym, order_id)
                except Exception as _e:
                    logger.warning(f"[S5][{sym}] ⚠️ Pass 2 get_order_fill failed: {_e}")
                    continue

                if fill_info["status"] != "filled":
                    continue

                fill_price = fill_info["fill_price"]
                fill_time  = datetime.now(timezone.utc).isoformat()
                trade_id   = uuid.uuid4().hex[:8]
                side       = sig.get("side", "SHORT")

                # Log open row
                _log_trade(f"S5_{side}", {
                    "trade_id":           trade_id,
                    "symbol":             sym,
                    "side":               side,
                    "entry":              fill_price,
                    "sl":                 round(sig.get("sl", 0), 8),
                    "tp":                 round(sig.get("tp", 0), 8),
                    "box_low":            round(sig.get("ob_low",  0), 8),
                    "box_high":           round(sig.get("ob_high", 0), 8),
                    "leverage":           config_s5.S5_LEVERAGE,
                    "tpsl_set":           False,
                    "strategy":           "S5",
                    "snap_entry_trigger": round(sig.get("trigger", fill_price), 8),
                    "snap_sl":            round(sig.get("sl", 0), 8),
                    "snap_rr":            sig.get("rr"),
                    "snap_sentiment":     sig.get("sentiment", "?"),
                    "snap_s5_ob_low":     round(sig.get("ob_low",  0), 8),
                    "snap_s5_ob_high":    round(sig.get("ob_high", 0), 8),
                    "snap_s5_tp":         round(sig.get("tp", 0), 8),
                })

                # Fetch and log close info
                hist = None
                try:
                    hist = tr.get_history_position(
                        sym,
                        open_time_iso=fill_time,
                        entry_price=fill_price,
                    )
                except Exception as _e:
                    logger.warning(f"[S5][{sym}] Pass 2: get_history_position failed: {_e}")

                if hist:
                    close_pnl  = round(hist["pnl"], 4)
                    exit_price = hist.get("exit_price")
                    _log_trade("S5_CLOSE", {
                        "trade_id":    trade_id,
                        "symbol":      sym,
                        "side":        side,
                        "pnl":         close_pnl,
                        "result":      "WIN" if close_pnl >= 0 else "LOSS",
                        "exit_reason": "RECONCILED",
                        "exit_price":  round(exit_price, 8) if exit_price else None,
                    })
                    logger.warning(
                        f"[S5][{sym}] ⚠️ Pass 2: opened+closed while down | "
                        f"fill={fill_price:.5f} | PnL={close_pnl:+.4f}"
                    )
                else:
                    logger.warning(
                        f"[S5][{sym}] ⚠️ Pass 2: opened while down, close history unavailable"
                    )

                self.pending_signals.pop(sym, None)
                st.save_pending_signals(self.pending_signals)
                recovered += 1

            except Exception as _e:
                logger.warning(f"[S5][{sym}] Pass 2 recovery error: {_e}")

        if recovered:
            _rebuild_stats_from_csv(config.TRADE_LOG)
            st.add_scan_log(
                f"⚠️ Startup recovery: {recovered} position(s) recovered", "WARN"
            )
            logger.warning(f"Startup recovery: {recovered} position(s) recovered")

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

        if config.NON_TRADING_HOURS_FROM is not None and config.NON_TRADING_HOURS_TO is not None:
            now_time = datetime.now()
        
            # Monday=0, Sunday=6. Weekends are 5 and 6.
            is_weekend = now_time.weekday() >= 5
            is_monday_early = now_time.weekday() == 0 and now_time.hour < config.NON_TRADING_HOURS_TO
            
            # Range crosses midnight: 
            # True if hour is 22 (10PM), 23 (11PM), or 0 (12AM)
            is_restricted_time = now_time.hour >= config.NON_TRADING_HOURS_FROM or now_time.hour < config.NON_TRADING_HOURS_TO

            if is_restricted_time and not is_weekend and not is_monday_early:
                st.add_scan_log("Non-trading hours — skipping scan", "INFO")
                return

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
                    # Cache partial pnl so close logging can subtract it (avoids double-count)
                    ap_ref = self.active_positions.get(pc["symbol"])
                    if ap_ref is not None:
                        ap_ref["partial_pnl"] = ap_ref.get("partial_pnl", 0.0) + pc["pnl"]
                    # Update to REMAINING margin (exchange_positions has the updated value from paper_trader)
                    remaining_margin = exchange_positions.get(pc["symbol"], {}).get("margin", 0)
                    if remaining_margin:
                        st.update_open_trade_margin(pc["symbol"], remaining_margin)
                    logger.info(f"[{pc['strategy']}][{pc['symbol']}] 📊 Partial logged: PnL={pc['pnl']:+.4f} ({pc['pnl_pct']:+.1f}%)")
                    try:
                        _pc_tid = self.active_positions.get(pc["symbol"], {}).get("trade_id", "")
                        _pc_int = _snapshot_interval(pc["strategy"])
                        _pc_df  = tr.get_candles(pc["symbol"], _pc_int, limit=100)
                        if not _pc_df.empty:
                            snapshot.save_snapshot(
                                trade_id=_pc_tid, event="partial",
                                symbol=pc["symbol"], interval=_pc_int,
                                candles=_df_to_candles(_pc_df),
                                event_price=round(pc.get("exit", 0.0), 8),
                            )
                    except Exception as e:
                        logger.warning(f"[{pc['strategy']}][{pc['symbol']}] partial snapshot failed: {e}")

            # Sync pnl + detect closed positions
            for sym in list(self.active_positions.keys()):
                if sym in exchange_positions:
                    pos = exchange_positions[sym]
                    st.update_open_trade_pnl(sym, pos["unrealised_pnl"])
                    if pos.get("mark_price"):
                        st.update_open_trade_mark_price(sym, pos["mark_price"])
                    # Sync margin from live exchange so dashboard total value stays accurate
                    _ot_live = st.get_open_trade(sym)
                    if _ot_live and pos.get("margin"):
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

                    # Track initial qty and detect live partial close (S1-S6)
                    if not PAPER_MODE and ap.get("strategy") in ("S1", "S2", "S3", "S4", "S5", "S6"):
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
                                interval = _snapshot_interval(ap["strategy"])
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

                    # Strategy-owned structural swing trail — delegated to strategies/sN.py
                    _strat = ap.get("strategy")
                    if _strat == "S5":
                        from strategies.s5 import maybe_trail_sl as _trail_s5
                        _trail_s5(sym, ap, tr, st)
                    elif _strat == "S1":
                        from strategies.s1 import maybe_trail_sl as _trail_s1
                        _trail_s1(sym, ap, tr, st)
                    elif _strat == "S3":
                        from strategies.s3 import maybe_trail_sl as _trail_s3
                        _trail_s3(sym, ap, tr, st)
                    elif _strat in ("S2", "S4"):
                        _partial_done = (
                            tr.is_partial_closed(sym) if PAPER_MODE
                            else ap.get("partial_logged", False)
                        )
                        if _strat == "S2":
                            from strategies.s2 import maybe_trail_sl as _trail_s2
                            _trail_s2(sym, ap, tr, st, _partial_done)
                        else:
                            from strategies.s4 import maybe_trail_sl as _trail_s4
                            _trail_s4(sym, ap, tr, st, _partial_done)

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
                    _exit_price = None
                    if PAPER_MODE:
                        _lc = tr.get_last_close(sym)
                        if _lc:
                            # CSV close row = remaining-portion pnl only (partial already logged separately)
                            last_pnl    = _lc["pnl"] - ap.get("partial_pnl", 0.0)
                            pnl_pct     = _lc["pnl_pct"]
                            exit_reason = _lc["reason"]
                    else:
                        # Live: use Bitget history-position for accurate PnL + actual fill price.
                        # achievedProfits = total for the full position (partial + final combined).
                        # Subtract already-logged partial so last_pnl = remaining portion only,
                        # matching what the startup reconcile path does.
                        _ot_entry = float(_ot.get("entry") or 0) if _ot else None
                        _ot_opened = _ot.get("opened_at") if _ot else None
                        _hist = tr.get_history_position(sym,
                                                        open_time_iso=_ot_opened,
                                                        entry_price=_ot_entry)
                        if _hist is not None:
                            last_pnl   = _hist["pnl"] - ap.get("partial_pnl", 0.0)
                            _exit_price = _hist.get("exit_price")  # actual closeAvgPrice from Bitget
                        else:
                            # API not yet settled — write placeholder row and retry next tick
                            _row_ts = datetime.now(timezone.utc).isoformat()
                            _log_trade(f"{ap['strategy']}_CLOSE", {
                                "trade_id":    ap.get("trade_id", ""),
                                "symbol":      sym, "side": ap["side"],
                                "pnl":         0, "result": "",
                                "exit_reason": "PENDING_RECONCILE",
                            })
                            logger.warning(f"[{ap['strategy']}][{sym}] PnL pending — placeholder written, will reconcile next tick")
                            st.close_trade(sym, "LOSS", 0)  # temporary; overwritten by reconcile
                            st.clear_position_memory(sym)
                            del self.active_positions[sym]
                            continue

                    # Total PnL for dashboard = close portion + any already-logged partial
                    total_pnl = last_pnl + ap.get("partial_pnl", 0.0)
                    result = "WIN" if total_pnl >= 0 else "LOSS"
                    logger.info(f"{'✅' if result == 'WIN' else '❌'} [{sym}] Closed ({result}) PnL={total_pnl:+.4f}")
                    st.close_trade(sym, result, total_pnl)
                    if result == "LOSS":
                        st.record_loss(sym)
                        st.record_strategy_loss(ap["strategy"], sym)
                        if st.is_pair_paused(sym):
                            logger.info(f"⛔ [{sym}] 3 losses today — paused until tomorrow (UTC)")
                            st.add_scan_log(f"[{sym}] ⛔ Paused for today — 3 losses reached", "WARN")
                    st.add_scan_log(
                        f"[{ap['strategy']}][{sym}] Closed {result} | PnL={total_pnl:+.4f} USDT", "INFO"
                    )
                    if PAPER_MODE and _lc:
                        try:
                            _exit_price = _lc.get("exit_price")
                        except Exception:
                            _exit_price = None
                    elif _exit_price is None:
                        # Fallback: mark price if history API returned nothing
                        try:
                            _exit_price = tr.get_mark_price(sym)
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
                        interval = _snapshot_interval(ap["strategy"])
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

        # ── 3b. Orphan reconciliation ─────────────────────────────── #
        # Catches positions in state.json open_trades that are NOT in
        # self.active_positions (e.g. injected by a recovery script while
        # the bot was running).  Runs every tick — cheap because it only
        # calls get_all_open_positions() when orphans are found.
        _orphans = [
            ot for ot in st.get_open_trades()
            if ot["symbol"] not in self.active_positions
        ]
        if _orphans:
            try:
                _orph_xpos = tr.get_all_open_positions()
            except Exception as _e:
                logger.warning(f"Orphan reconcile: positions fetch failed: {_e}")
                _orph_xpos = {}
            for _ot in _orphans:
                _sym      = _ot["symbol"]
                _strategy = _ot.get("strategy", "UNKNOWN")
                if _sym in _orph_xpos:
                    # Still open on exchange — re-register so future ticks monitor it
                    self.active_positions[_sym] = {
                        "side":     _ot.get("side", "LONG"),
                        "strategy": _strategy,
                        "box_high": float(_ot.get("tp") or _ot.get("sl") or 0),
                        "box_low":  float(_ot.get("sl") or 0),
                        "trade_id": _ot.get("trade_id", ""),
                    }
                    logger.warning(f"[{_strategy}][{_sym}] ⚠️ Orphan re-registered into active_positions")
                    st.add_scan_log(f"[{_strategy}][{_sym}] ⚠️ Orphan re-registered", "WARN")
                else:
                    # Gone from exchange — closed without bot knowing; reconcile now
                    _hist = None
                    try:
                        _hist = tr.get_history_position(
                            _sym,
                            open_time_iso=_ot.get("opened_at"),
                            entry_price=float(_ot.get("entry") or 0),
                        )
                    except Exception as _e:
                        logger.warning(f"[{_strategy}][{_sym}] orphan history error: {_e}")
                    _pnl        = _hist["pnl"] if _hist else 0.0
                    _exit_price = _hist.get("exit_price") if _hist else None
                    _result     = "WIN" if _pnl >= 0 else "LOSS"
                    _entry      = float(_ot.get("entry") or 0)
                    _lev        = float(_ot.get("leverage") or 1)
                    _pnl_pct    = None
                    if _exit_price and _entry and _lev:
                        _chg     = (_exit_price - _entry) / _entry if _ot.get("side") == "LONG" else (_entry - _exit_price) / _entry
                        _pnl_pct = round(_chg * _lev * 100, 2)
                    st.close_trade(_sym, _result, _pnl)
                    if _result == "LOSS":
                        st.record_loss(_sym)
                        st.record_strategy_loss(_strategy, _sym)
                    _log_trade(f"{_strategy}_CLOSE", {
                        "trade_id":    _ot.get("trade_id", ""),
                        "symbol":      _sym,
                        "side":        _ot.get("side", ""),
                        "pnl":         round(_pnl, 4),
                        "result":      _result,
                        "pnl_pct":     _pnl_pct,
                        "exit_reason": "ORPHAN_RECONCILE",
                        "exit_price":  _exit_price,
                    })
                    try:
                        _interval = _snapshot_interval(_strategy)
                        _snap_df  = tr.get_candles(_sym, _interval, limit=100)
                        if not _snap_df.empty:
                            snapshot.save_snapshot(
                                trade_id=_ot.get("trade_id", ""), event="close",
                                symbol=_sym, interval=_interval,
                                candles=_df_to_candles(_snap_df),
                                event_price=round(_exit_price, 8) if _exit_price else 0.0,
                            )
                    except Exception as _e:
                        logger.warning(f"[{_strategy}][{_sym}] orphan close snapshot failed: {_e}")
                    st.clear_position_memory(_sym)
                    logger.warning(f"[{_strategy}][{_sym}] ⚠️ Orphan reconciled: {_result} PnL={_pnl:+.4f}")
                    st.add_scan_log(f"[{_strategy}][{_sym}] ⚠️ Orphan reconciled: {_result} PnL={_pnl:+.4f}", "WARN")

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
        s1_sig, s1_rsi, s1_bh, s1_bl, s1_adx, s1_daily_rsi = "HOLD", 50.0, 0.0, 0.0, 0.0, 50.0
        if config_s1.S1_ENABLED:
            s1_sig, s1_rsi, s1_bh, s1_bl, s1_adx, s1_daily_rsi = evaluate_s1(
                symbol, htf_df, ltf_df, daily_df, allowed_direction
            )
        htf_bull, htf_bear = check_htf(htf_df)

        from strategies.s1 import check_daily_trend as _trend
        trend_ok, adx_val, daily_rsi = _trend(daily_df, "LONG" if allowed_direction == "BULLISH" else "SHORT")
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
        elif not trend_ok:
            closes_d  = daily_df["close"].astype(float)
            from indicators import calculate_ema as _ema
            ema20_d   = float(_ema(closes_d, config_s1.DAILY_EMA_SLOW).iloc[-1])
            price_d   = float(closes_d.iloc[-1])
            if adx_val <= config_s1.ADX_TREND_THRESHOLD:
                s1_reason = f"ADX={adx_val:.1f} < {config_s1.ADX_TREND_THRESHOLD} (sideways)"
            else:
                side_str  = "above" if allowed_direction == "BULLISH" else "below"
                actual    = "below" if price_d < ema20_d else "above"
                s1_reason = f"ADX={adx_val:.1f} ✓ but price {actual} EMA20 (need {side_str})"
            if allowed_direction == "BULLISH" and daily_rsi <= config_s1.DAILY_RSI_LONG_THRESH:
                s1_reason += f" | daily RSI {daily_rsi:.1f} ≤ {config_s1.DAILY_RSI_LONG_THRESH}"
            elif allowed_direction == "BEARISH" and daily_rsi >= config_s1.DAILY_RSI_SHORT_THRESH:
                s1_reason += f" | daily RSI {daily_rsi:.1f} ≥ {config_s1.DAILY_RSI_SHORT_THRESH}"
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
            (config_s3.S3_ENABLED and self.sentiment.direction == "BULLISH") or
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
        if config_s3.S3_ENABLED and self.sentiment.direction == "BULLISH" and m15_df is not None:
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
                s5_sr_pct = None  # S5 R:R to structural swing target handles clearance internally
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
        if config_s4.S4_ENABLED and self.sentiment.direction == "BEARISH":
            s4_sig, s4_rsi, s4_trigger, s4_sl, s4_body_pct, s4_rsi_peak, s4_div, s4_div_str, s4_reason = evaluate_s4(symbol, daily_df, htf_df)
            logger.info(f"[S4][{symbol}] {s4_reason}")
        # 1H low filter display values
        s4_1h_low = None
        s4_1h_low_ok = None
        _lb = config_s4.S4_LOW_LOOKBACK
        if htf_df is not None and len(htf_df) >= _lb + 1:
            s4_1h_low = round(float(htf_df["low"].iloc[-(_lb + 1):-1].min()), 8)
            if s4_trigger > 0:
                s4_1h_low_ok = s4_trigger <= s4_1h_low

        # ── Strategy 6 ───────────────────────────────────────────── #
        s6_sig, s6_peak_level, s6_sl, s6_drop_pct, s6_rsi_at_peak, s6_reason = "HOLD", 0.0, 0.0, 0.0, 0.0, ""
        if config_s6.S6_ENABLED:
            s6_sig, s6_peak_level, s6_sl, s6_drop_pct, s6_rsi_at_peak, s6_reason = evaluate_s6(
                symbol, daily_df, allowed_direction
            )
            logger.info(f"[S6][{symbol}] {s6_reason}")

        # ── S/R Clearance (for dashboard display + entry guard) ──── #
        _sr_res        = find_nearest_resistance(daily_df, close)
        _sr_sup        = find_nearest_support(daily_df, close)
        _s4_base       = find_spike_base(daily_df, price_ceiling=close)
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
            "signal": s1_sig if s1_sig != "HOLD" else (s2_sig if s2_sig != "HOLD" else (s3_sig if s3_sig != "HOLD" else (s4_sig if s4_sig != "HOLD" else ("PENDING" if s5_sig.startswith("PENDING") else (s6_sig if s6_sig != "HOLD" else s5_sig))))),
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
            "s4_1h_low": s4_1h_low,
            "s4_1h_low_ok": s4_1h_low_ok,
            "s6_signal":      s6_sig,
            "s6_reason":      s6_reason,
            "s6_peak_level":  round(s6_peak_level, 8) if s6_peak_level else None,
            "s6_sl":          round(s6_sl,          8) if s6_sl          else None,
            "s6_fakeout_seen": False,
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
            "strategy": "S1" if s1_sig != "HOLD" else ("S2" if s2_sig != "HOLD" else ("S3" if s3_sig != "HOLD" else ("S4" if s4_sig != "HOLD" else ("S6" if s6_sig not in ("HOLD", "") else ("S5" if s5_sig not in ("HOLD", "") else "S1"))))),
            "s2_daily_rsi": s2_rsi,
            "s2_big_candle": s2_rsi > 0 and ("big_candle" in s2_reason or "Big candle" in s2_reason or s2_bh > 0),
            "s2_coiling":    s2_bl > 0 and s2_bh > 0 and s2_sig == "HOLD",
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
            sl_long_est  = max(close * (1 - config_s1.STOP_LOSS_PCT),
                               s1_bl * (1 - config_s1.S1_SL_BUFFER_PCT))
            sl_short_est = min(close * (1 + config_s1.STOP_LOSS_PCT),
                               s1_bh * (1 + config_s1.S1_SL_BUFFER_PCT))
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
                "ltf_df": ltf_df, "daily_df": daily_df, "allowed_direction": allowed_direction,
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

        # ── Collect S6 candidate ──────────────────────────────────── #
        if s6_sig == "PENDING_SHORT" and s6_peak_level > 0:
            self.candidates.append({
                "strategy": "S6", "symbol": symbol, "sig": "PENDING_SHORT",
                "rr": None, "sr_pct": None,
                "s6_peak_level": s6_peak_level, "s6_sl": s6_sl,
                "s6_drop_pct": s6_drop_pct, "s6_rsi_at_peak": s6_rsi_at_peak,
                "s6_reason": s6_reason, "daily_df": daily_df,
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
        }

        for candidate in ranked:
            sym      = candidate["symbol"]
            sig      = candidate["sig"]
            strategy = candidate["strategy"]

            if sig in ("PENDING_LONG", "PENDING_SHORT"):
                if strategy == "S6":
                    if sym not in self.pending_signals and not st.is_pair_paused(sym) \
                            and not st.is_strategy_on_cooldown(strategy, sym):
                        self._queue_s6_pending(candidate)
                elif strategy == "S5":
                    from strategies import s5 as _s5_mod
                    if sym not in self.pending_signals and not st.is_pair_paused(sym) \
                            and not st.is_strategy_on_cooldown(strategy, sym) \
                            and not _s5_mod.is_ob_cooldown_active(self, sym):
                        # S5 PENDING — queue with rank so entry watcher respects ordering
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
            if st.is_strategy_on_cooldown(strategy, sym):
                logger.info(f"[{strategy}][{sym}] 🕐 Cooldown active — skipping entry (4h after LOSS)")
                continue

            if strategy == "S2":
                if sym not in self.pending_signals:
                    min_bal = 5.0 / (config_s2.S2_TRADE_SIZE_PCT * config_s2.S2_LEVERAGE)
                    if balance >= min_bal:
                        self._queue_s2_pending(candidate)
            elif strategy == "S3":
                if sym not in self.pending_signals:
                    min_bal = 5.0 / (config_s3.S3_TRADE_SIZE_PCT * config_s3.S3_LEVERAGE)
                    if balance >= min_bal:
                        self._queue_s3_pending(candidate)
            elif strategy == "S4":
                if sym not in self.pending_signals:
                    min_bal = 5.0 / (config_s4.S4_TRADE_SIZE_PCT * config_s4.S4_LEVERAGE)
                    if balance >= min_bal:
                        self._queue_s4_pending(candidate)
            elif strategy == "S5":
                with self._trade_lock:
                    if sym in self.active_positions:
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
        """Execute scale-in for S2/S4/S6 and save candle snapshot."""
        try:
            from importlib import import_module
            strat = ap["strategy"]
            mod   = import_module(f"strategies.{strat.lower()}")
            specs = mod.scale_in_specs()

            # Sentiment gate: only scale in when market direction matches the trade.
            _sentiment = getattr(self, "sentiment", None)
            if _sentiment is not None and _sentiment.direction != specs["direction"]:
                logger.info(
                    f"[{strat}][{sym}] ⏸️ Scale-in skipped — market is "
                    f"{_sentiment.direction} (need {specs['direction']})"
                )
                return  # keep scale_in_pending=True; retry next tick

            mark_now  = tr.get_mark_price(sym)
            in_window = mod.is_scale_in_window(ap, mark_now)
            remaining = ap["scale_in_trade_size_pct"] * 0.5
            if in_window:
                if specs["hold_side"] == "long":
                    tr.scale_in_long(sym, remaining, specs["leverage"])
                else:
                    tr.scale_in_short(sym, remaining, specs["leverage"])
                logger.info(f"[{strat}][{sym}] ✅ Scale-in +{remaining*100:.0f}% @ {mark_now:.5f}")
                st.add_scan_log(f"[{strat}][{sym}] Scale-in executed @ {mark_now:.5f}", "INFO")
                _log_trade(f"{strat}_SCALE_IN", {
                    "trade_id": ap.get("trade_id", ""),
                    "symbol": sym, "side": ap["side"],
                    "entry": round(mark_now, 8),
                })
                if PAPER_MODE:
                    updated_pos = tr.get_all_open_positions().get(sym, {})
                    if updated_pos.get("margin"):
                        st.update_open_trade_margin(sym, updated_pos["margin"])
                    # Update initial_qty so partial detection uses new total
                    new_total_qty = float(updated_pos.get("qty", 0))
                    if new_total_qty > 0:
                        ap["initial_qty"] = new_total_qty
                        st.update_position_memory(sym, initial_qty=new_total_qty)
                else:
                    # Refresh plan exits (profit_plan + moving_plan) to reflect new total qty.
                    # SL (place-pos-tpsl) is position-level and auto-scales on Bitget.
                    try:
                        import time as _si_t
                        # Poll until Bitget's position API reflects the scale-in fill.
                        # A fixed sleep is unreliable — the REST endpoint can lag several
                        # seconds behind the actual fill. We wait up to 12 seconds.
                        _pre_qty = float(ap.get("qty", 0))
                        _deadline = _si_t.time() + 12
                        _scale_pos = {}
                        while _si_t.time() < _deadline:
                            _scale_pos = tr.get_all_open_positions().get(sym, {})
                            if float(_scale_pos.get("qty", 0)) > _pre_qty:
                                break
                            _si_t.sleep(1.5)
                        else:
                            logger.warning(
                                f"[{strat}][{sym}] ⚠️ Position qty did not increase "
                                f"after scale-in within 12s (pre={_pre_qty})"
                            )
                        hold_side = specs["hold_side"]
                        # Recompute trail trigger and SL from new average entry after scale-in
                        new_trig = 0.0
                        # Update initial_qty and margin to reflect scaled-in position
                        _new_qty = float(_scale_pos.get("qty", 0))
                        if _new_qty > 0:
                            ap["initial_qty"] = _new_qty
                            st.update_position_memory(sym, initial_qty=_new_qty)
                        if _scale_pos.get("margin"):
                            st.update_open_trade_margin(sym, float(_scale_pos["margin"]))
                        new_avg = _scale_pos.get("entry_price", 0)
                        if new_avg > 0:
                            new_sl, new_trig = mod.recompute_scale_in_sl_trigger(ap, new_avg)
                            if tr.update_position_sl(sym, new_sl, hold_side=hold_side):
                                ap["sl"] = new_sl
                                st.update_open_trade_sl(sym, new_sl)
                        if not tr.refresh_plan_exits(sym, hold_side, new_trig):
                            logger.warning(f"[{strat}][{sym}] ⚠️ Scale-in exits refresh failed — verify plan orders manually")
                            st.add_scan_log(f"[{strat}][{sym}] ⚠️ Scale-in exits refresh failed", "WARN")
                    except Exception as _ref_e:
                        logger.warning(f"[{strat}][{sym}] ⚠️ Scale-in exits refresh error: {_ref_e}")
                # Save scale-in snapshot
                try:
                    interval = _snapshot_interval(strat)
                    _snap_df = tr.get_candles(sym, interval, limit=100)
                    if not _snap_df.empty:
                        snapshot.save_snapshot(
                            trade_id=ap.get("trade_id", ""), event="scale_in",
                            symbol=sym, interval=interval,
                            candles=_df_to_candles(_snap_df),
                            event_price=round(mark_now, 8),
                        )
                except Exception as e:
                    logger.warning(f"[{strat}][{sym}] scale_in snapshot failed: {e}")
            else:
                logger.info(f"[{strat}][{sym}] ⏸️ Scale-in waiting — price {mark_now:.5f} outside entry window")
                return  # keep scale_in_pending=True; retry next tick
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
        if c["sr_pct"] is not None and c["sr_pct"] < config_s1.S1_MIN_SR_CLEARANCE * 100:
            st.add_scan_log(
                f"[S1][{symbol}] ⛔ Skipped — S/R clearance {c['sr_pct']:.1f}% < {config_s1.S1_MIN_SR_CLEARANCE*100:.0f}%",
                "WARN"
            )
            return False
        sl_long  = max(mark_now * (1 - config_s1.STOP_LOSS_PCT),
                       c["s1_bl"] * (1 - config_s1.S1_SL_BUFFER_PCT))
        sl_short = min(mark_now * (1 + config_s1.STOP_LOSS_PCT),
                       c["s1_bh"] * (1 + config_s1.S1_SL_BUFFER_PCT))
        st.add_scan_log(
            f"[S1][{symbol}] {'🟢' if s1_sig == 'LONG' else '🔴'} {s1_sig} | "
            f"RSI={c['rsi_val']:.1f} ADX={c['adx_val']:.1f} | rank=#{c['priority_rank']}",
            "SIGNAL"
        )
        if s1_sig == "LONG":
            trade = tr.open_long(symbol, sl_floor=sl_long, leverage=lev,
                                 trade_size_pct=config_s1.TRADE_SIZE_PCT,
                                 strategy="S1")
        else:
            trade = tr.open_short(symbol, sl_floor=sl_short, leverage=lev,
                                  trade_size_pct=config_s1.TRADE_SIZE_PCT,
                                  strategy="S1")
        trade["strategy"] = "S1"
        trade["snap_rsi"]           = round(c["rsi_val"], 1)
        trade["snap_adx"]           = round(c["adx_val"], 1)
        trade["snap_htf"]           = "BULL" if c["htf_bull"] else "BEAR" if c["htf_bear"] else "NONE"
        trade["snap_coil"]          = c["is_coil"]
        trade["snap_box_range_pct"] = round((c["s1_bh"] - c["s1_bl"]) / c["s1_bl"] * 100, 3) if c["s1_bh"] and c["s1_bl"] else None
        trade["snap_sentiment"]     = self.sentiment.direction
        _daily = c.get("daily_df")
        if _daily is not None and not _daily.empty:
            _d_rsi = calculate_rsi(_daily["close"].astype(float))
            trade["snap_daily_rsi"] = round(float(_d_rsi.iloc[-1]), 1)
        trade["trade_id"] = uuid.uuid4().hex[:8]
        trade.update(dna_snapshot("S1", symbol, {
            "daily": c.get("daily_df"),
            # h1 not in S1 candidate dict — snap_trend_h1_* will record as ""
            "m3":    c.get("ltf_df"),
        }))
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
            "sl": trade.get("sl", 0.0),
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
            if config.CLAUDE_FILTER_ENABLED:
                _cd = claude_approve("S5", symbol, {
                    "OB zone": f"{s5_ob_low:.5f}–{s5_ob_high:.5f}",
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
                strategy       = "S5",
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
            trade.update(dna_snapshot("S5", symbol, {
                "m15": m15_df,
            }))
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
            if config.CLAUDE_FILTER_ENABLED:
                _cd = claude_approve("S5", symbol, {
                    "OB zone": f"{s5_ob_low:.5f}–{s5_ob_high:.5f}",
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
                strategy       = "S5",
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
            trade.update(dna_snapshot("S5", symbol, {
                "m15": m15_df,
            }))
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
        """Delegate to strategies.s5.queue_pending (late import to respect test patches)."""
        from strategies.s5 import queue_pending
        queue_pending(self, symbol, sig, trigger, sl, tp, ob_low, ob_high, m15_df,
                      priority_rank, priority_score, paper_mode=PAPER_MODE)

    def _queue_s2_pending(self, c: dict) -> None:
        """Delegate to strategies.s2.queue_pending."""
        from strategies.s2 import queue_pending
        queue_pending(self, c)

    def _queue_s3_pending(self, c: dict) -> None:
        """Delegate to strategies.s3.queue_pending."""
        from strategies.s3 import queue_pending
        queue_pending(self, c)

    def _queue_s4_pending(self, c: dict) -> None:
        """Delegate to strategies.s4.queue_pending."""
        from strategies.s4 import queue_pending
        queue_pending(self, c)

    def _queue_s6_pending(self, candidate: dict) -> None:
        """Delegate to strategies.s6.queue_pending."""
        from strategies.s6 import queue_pending
        queue_pending(self, candidate)

    def _fire_s2(self, symbol: str, sig: dict, mark: float, balance: float) -> None:
        """Open S2 LONG at fire time. Runs S/R check against pair_states."""
        ps = st.get_pair_state(symbol)
        sr_resistance = ps.get("s2_sr_resistance_price")
        if sr_resistance is not None:
            clearance = (sr_resistance - mark) / mark
            if clearance < config_s2.S2_MIN_SR_CLEARANCE:
                logger.info(
                    f"[S2][{symbol}] ⏸️ Fire skipped — resistance {sr_resistance:.5f} "
                    f"only {clearance*100:.1f}% away"
                )
                st.add_scan_log(
                    f"[S2][{symbol}] ⛔ Fire: resistance too close ({clearance*100:.1f}%)", "WARN"
                )
                self.pending_signals.pop(symbol, None)
                st.save_pending_signals(self.pending_signals)
                return
        if config.CLAUDE_FILTER_ENABLED:
            _sr_str = f"{round((sr_resistance - mark) / mark * 100, 1)}%" if sr_resistance else "none found"
            _cd = claude_approve("S2", symbol, {
                "RSI": sig.get("snap_daily_rsi", "?"),
                "S/R clearance": _sr_str,
                "Sentiment": sig.get("snap_sentiment", "?"),
                "Entry": round(mark, 5), "SL": round(sig["s2_bl"], 5),
            })
            if not _cd["approved"]:
                logger.info(f"[S2][{symbol}] 🤖 Claude rejected: {_cd['reason']}")
                st.add_scan_log(f"[S2][{symbol}] 🤖 Rejected: {_cd['reason']}", "WARN")
                self.pending_signals.pop(symbol, None)
                st.save_pending_signals(self.pending_signals)
                return
        st.add_scan_log(f"[S2][{symbol}] 🟢 LONG fired @ {mark:.5f}", "SIGNAL")
        trade = tr.open_long(
            symbol, box_low=sig["s2_bl"], leverage=config_s2.S2_LEVERAGE,
            trade_size_pct=config_s2.S2_TRADE_SIZE_PCT * 0.5,
            take_profit_pct=config_s2.S2_TAKE_PROFIT_PCT,
            stop_loss_pct=config_s2.S2_STOP_LOSS_PCT,
            strategy       = "S2",
        )
        trade["strategy"]              = "S2"
        trade["snap_daily_rsi"]        = sig.get("snap_daily_rsi")
        trade["snap_box_range_pct"]    = sig.get("snap_box_range_pct")
        trade["snap_sentiment"]        = sig.get("snap_sentiment")
        trade["snap_sr_clearance_pct"] = round((sr_resistance - mark) / mark * 100, 1) \
                                         if sr_resistance else None
        trade["trade_id"] = uuid.uuid4().hex[:8]
        trade.update(dna_snapshot("S2", symbol, {
            "daily": sig.get("daily_df"),
        }))
        _log_trade("S2_LONG", trade)
        st.add_open_trade(trade)
        try:
            snapshot.save_snapshot(
                trade_id=trade["trade_id"], event="open",
                symbol=symbol, interval="1D", candles=[],
                event_price=float(trade.get("entry", 0)),
            )
        except Exception as e:
            logger.warning(f"[S2][{symbol}] snapshot save failed: {e}")
        if PAPER_MODE: tr.tag_strategy(symbol, "S2")
        self.active_positions[symbol] = {
            "side": "LONG", "strategy": "S2",
            "box_high": sig["s2_bh"], "box_low": sig["s2_bl"],
            "scale_in_pending": True, "scale_in_after": time.time() + 3600,
            "scale_in_trade_size_pct": config_s2.S2_TRADE_SIZE_PCT,
            "trade_id": trade["trade_id"],
        }

    def _fire_s3(self, symbol: str, sig: dict, mark: float, balance: float) -> None:
        """Open S3 LONG at fire time. Runs S/R check against pair_states."""
        ps = st.get_pair_state(symbol)
        sr_resistance = ps.get("s3_sr_resistance_price")
        if sr_resistance is not None:
            clearance = (sr_resistance - mark) / mark
            if clearance < config_s3.S3_MIN_SR_CLEARANCE:
                logger.info(
                    f"[S3][{symbol}] ⏸️ Fire skipped — resistance {sr_resistance:.5f} "
                    f"only {clearance*100:.1f}% away"
                )
                st.add_scan_log(
                    f"[S3][{symbol}] ⛔ Fire: resistance too close ({clearance*100:.1f}%)", "WARN"
                )
                self.pending_signals.pop(symbol, None)
                st.save_pending_signals(self.pending_signals)
                return
        if config.CLAUDE_FILTER_ENABLED:
            _sr_str = f"{round((sr_resistance - mark) / mark * 100, 1)}%" if sr_resistance else "none found"
            _cd = claude_approve("S3", symbol, {
                "ADX": sig.get("snap_adx", "?"),
                "S/R clearance (15m)": _sr_str,
                "Sentiment": sig.get("snap_sentiment", "?"),
                "Entry": round(mark, 5), "SL": round(sig["s3_sl"], 5),
            })
            if not _cd["approved"]:
                logger.info(f"[S3][{symbol}] 🤖 Claude rejected: {_cd['reason']}")
                st.add_scan_log(f"[S3][{symbol}] 🤖 Rejected: {_cd['reason']}", "WARN")
                self.pending_signals.pop(symbol, None)
                st.save_pending_signals(self.pending_signals)
                return
        st.add_scan_log(f"[S3][{symbol}] 🟢 LONG fired @ {mark:.5f}", "SIGNAL")
        trade = tr.open_long(
            symbol, sl_floor=sig["s3_sl"], leverage=config_s3.S3_LEVERAGE,
            trade_size_pct=config_s3.S3_TRADE_SIZE_PCT, strategy       = "S3",
        )
        trade["strategy"]              = "S3"
        trade["snap_adx"]              = sig.get("snap_adx")
        trade["snap_entry_trigger"]    = sig.get("snap_entry_trigger")
        trade["snap_sl"]               = sig.get("snap_sl")
        trade["snap_rr"]               = sig.get("snap_rr")
        trade["snap_sentiment"]        = sig.get("snap_sentiment")
        trade["snap_sr_clearance_pct"] = sig.get("snap_sr_clearance_pct")
        trade["trade_id"] = uuid.uuid4().hex[:8]
        trade.update(dna_snapshot("S3", symbol, {
            "m15": sig.get("m15_df"),
        }))
        _log_trade("S3_LONG", trade)
        st.add_open_trade(trade)
        try:
            snapshot.save_snapshot(
                trade_id=trade["trade_id"], event="open",
                symbol=symbol, interval=config_s3.S3_LTF_INTERVAL, candles=[],
                event_price=float(trade.get("entry", 0)),
            )
        except Exception as e:
            logger.warning(f"[S3][{symbol}] snapshot save failed: {e}")
        if PAPER_MODE: tr.tag_strategy(symbol, "S3")
        self.active_positions[symbol] = {
            "side": "LONG", "strategy": "S3",
            "box_high": sig["trigger"], "box_low": sig["s3_sl"],
            "trade_id": trade["trade_id"],
        }

    def _fire_s4(self, symbol: str, sig: dict, mark: float, balance: float) -> None:
        """Open S4 SHORT at fire time. Runs S/R check against pair_states."""
        ps = st.get_pair_state(symbol)
        sr_support_pct = ps.get("s4_sr_support_pct")
        if sr_support_pct is not None and sr_support_pct < config_s4.S4_MIN_SR_CLEARANCE * 100:
            logger.info(
                f"[S4][{symbol}] ⏸️ Fire skipped — support clearance {sr_support_pct:.1f}% too small"
            )
            st.add_scan_log(
                f"[S4][{symbol}] ⛔ Fire: support too close ({sr_support_pct:.1f}%)", "WARN"
            )
            self.pending_signals.pop(symbol, None)
            st.save_pending_signals(self.pending_signals)
            return
        if config.CLAUDE_FILTER_ENABLED:
            _sr_str = f"{sr_support_pct:.1f}%" if sr_support_pct else "none found"
            _cd = claude_approve("S4", symbol, {
                "RSI peak": sig.get("snap_rsi_peak", "?"),
                "RSI divergence": str(sig.get("snap_rsi_div", "?")),
                "S/R clearance (spike base)": _sr_str,
                "Sentiment": sig.get("snap_sentiment", "?"),
                "Entry": round(mark, 5), "SL": round(sig["s4_sl"], 5),
            })
            if not _cd["approved"]:
                logger.info(f"[S4][{symbol}] 🤖 Claude rejected: {_cd['reason']}")
                st.add_scan_log(f"[S4][{symbol}] 🤖 Rejected: {_cd['reason']}", "WARN")
                self.pending_signals.pop(symbol, None)
                st.save_pending_signals(self.pending_signals)
                return
        s4_sl_actual = mark * (1 + 0.50 / config_s4.S4_LEVERAGE)
        st.add_scan_log(
            f"[S4][{symbol}] 🔴 SHORT fired @ {mark:.5f} | entry≤{sig['trigger']:.5f}", "SIGNAL"
        )
        trade = tr.open_short(
            symbol, sl_floor=s4_sl_actual, leverage=config_s4.S4_LEVERAGE,
            trade_size_pct=config_s4.S4_TRADE_SIZE_PCT * 0.5, strategy       = "S4",
        )
        trade["strategy"]              = "S4"
        trade["snap_rsi"]              = sig.get("snap_rsi")
        trade["snap_rsi_peak"]         = sig.get("snap_rsi_peak")
        trade["snap_spike_body_pct"]   = sig.get("snap_spike_body_pct")
        trade["snap_rsi_div"]          = sig.get("snap_rsi_div")
        trade["snap_rsi_div_str"]      = sig.get("snap_rsi_div_str")
        trade["snap_sl"]               = round(s4_sl_actual, 8)
        trade["snap_sentiment"]        = sig.get("snap_sentiment")
        trade["snap_sr_clearance_pct"] = sr_support_pct
        trade["trade_id"] = uuid.uuid4().hex[:8]
        trade.update(dna_snapshot("S4", symbol, {
            "daily": sig.get("daily_df"),
            # h1 not carried in sig dict — snap_trend_h1_* will record as ""
        }))
        _log_trade("S4_SHORT", trade)
        st.add_open_trade(trade)
        try:
            snapshot.save_snapshot(
                trade_id=trade["trade_id"], event="open",
                symbol=symbol, interval="1D", candles=[],
                event_price=float(trade.get("entry", 0)),
            )
        except Exception as e:
            logger.warning(f"[S4][{symbol}] snapshot save failed: {e}")
        if PAPER_MODE: tr.tag_strategy(symbol, "S4")
        self.active_positions[symbol] = {
            "side": "SHORT", "strategy": "S4",
            "box_high": sig["s4_sl"], "box_low": sig["trigger"],
            "scale_in_pending": True, "scale_in_after": time.time() + 3600,
            "scale_in_trade_size_pct": config_s4.S4_TRADE_SIZE_PCT,
            "s4_prev_low": sig["prev_low"],
            "trade_id": trade["trade_id"],
        }

    def _fire_s6(self, symbol: str, sig: dict, mark: float, balance: float) -> None:
        """Open S6 SHORT after two-phase fakeout confirmed. Initial entry at 50% size; scale-in queued 1h later."""
        sl_price = mark * (1 + config_s6.S6_SL_PCT / config_s6.S6_LEVERAGE)
        st.add_scan_log(
            f"[S6][{symbol}] 🔴 SHORT | peak={sig['peak_level']:.5f} | "
            f"fakeout confirmed → entry @ {mark:.5f}", "SIGNAL"
        )
        trade = tr.open_short(
            symbol, sl_floor=sl_price, leverage=config_s6.S6_LEVERAGE,
            trade_size_pct=config_s6.S6_TRADE_SIZE_PCT * 0.5, strategy       = "S6",
        )
        trade["strategy"]              = "S6"
        trade["snap_s6_peak"]          = sig.get("snap_s6_peak")
        trade["snap_s6_drop_pct"]      = sig.get("snap_s6_drop_pct")
        trade["snap_s6_rsi_at_peak"]   = sig.get("snap_s6_rsi_at_peak")
        trade["snap_sentiment"]        = sig.get("snap_sentiment")
        trade["snap_sr_clearance_pct"] = None
        trade["trade_id"] = uuid.uuid4().hex[:8]
        trade.update(dna_snapshot("S6", symbol, {
            "daily": sig.get("daily_df"),
        }))
        _log_trade("S6_SHORT", trade)
        st.add_open_trade(trade)
        try:
            snapshot.save_snapshot(
                trade_id=trade["trade_id"], event="open",
                symbol=symbol, interval="1D", candles=[],
                event_price=float(trade.get("entry", 0)),
            )
        except Exception as e:
            logger.warning(f"[S6][{symbol}] snapshot save failed: {e}")
        if PAPER_MODE: tr.tag_strategy(symbol, "S6")
        self.active_positions[symbol] = {
            "side": "SHORT", "strategy": "S6",
            "box_high": sl_price, "box_low": sig["peak_level"],
            "scale_in_pending": True, "scale_in_after": time.time() + 3600,
            "scale_in_trade_size_pct": config_s6.S6_TRADE_SIZE_PCT,
            "trade_id": trade["trade_id"],
        }

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
                    positions = tr.get_all_open_positions()   # internally calls _check_exit for each position
                    for sym, pos in positions.items():
                        if pos.get("mark_price"):
                            st.update_open_trade_mark_price(sym, pos["mark_price"])
                except Exception as e:
                    logger.debug(f"Paper position poll error: {e}")

            # Live mode: refresh mark price for active trades at 4s resolution
            elif not PAPER_MODE and self.active_positions:
                for sym in list(self.active_positions.keys()):
                    try:
                        mark = tr.get_mark_price(sym)
                        st.update_open_trade_mark_price(sym, mark)
                    except Exception:
                        pass

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

                    if strategy in ("S2", "S3", "S4", "S5", "S6"):
                        # Delegate to the strategy module's handle_pending_tick.
                        # Returns "break" to stop the outer for-loop (MAX_CONCURRENT_TRADES hit).
                        from importlib import import_module
                        try:
                            mod = import_module(f"strategies.{strategy.lower()}")
                            result = mod.handle_pending_tick(self, symbol, sig, balance,
                                                             paper_mode=PAPER_MODE)
                            if result == "break":
                                break
                        except Exception as e:
                            logger.error(f"[{strategy}][{symbol}] pending tick error: {e}")
                    else:
                        # ── Unknown strategy: expire stale signals ──── #
                        if time.time() > sig.get("expires", 0):
                            logger.info(f"[{strategy}][{symbol}] ⏰ Pending signal expired — removing")
                            st.add_scan_log(f"[{strategy}][{symbol}] ⏰ Pending expired", "INFO")
                            self.pending_signals.pop(symbol, None)
                            st.save_pending_signals(self.pending_signals)

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
                strategy       = "S5",
                tp_price_abs   = sig["tp"],
            )
        else:
            trade = tr.open_short(
                symbol,
                sl_floor       = sig["sl"],
                leverage       = config_s5.S5_LEVERAGE,
                trade_size_pct = config_s5.S5_TRADE_SIZE_PCT,
                strategy       = "S5",
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
        trade.update(dna_snapshot("S5", symbol, {}))  # no DFs available in watcher
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
                    strategy       = "S5",
                    tp_price_abs   = tp_price,
                )
            else:
                trade = tr.open_short(
                    symbol,
                    sl_floor       = sl_price,
                    leverage       = config_s5.S5_LEVERAGE,
                    trade_size_pct = config_s5.S5_TRADE_SIZE_PCT,
                    strategy       = "S5",
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
            trade.update(dna_snapshot("S5", symbol, {}))  # no DFs available in watcher
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
        from strategies.s5 import place_exits_from_signal
        _ok, sl_trig, part_trig, tp_targ = place_exits_from_signal(
            symbol, side, qty_str, fill_price, sl_price, tp_price,
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
        trade.update(dna_snapshot("S5", symbol, {}))  # no DFs available in watcher
        _log_trade(f"S5_{side}", trade)
        st.add_open_trade(trade)
        try:
            _snap_df = tr.get_candles(symbol, config_s5.S5_LTF_INTERVAL, limit=100)
            if not _snap_df.empty:
                snapshot.save_snapshot(
                    trade_id=trade_id, event="open",
                    symbol=symbol, interval=config_s5.S5_LTF_INTERVAL,
                    candles=_df_to_candles(_snap_df),
                    event_price=fill_price,
                )
        except Exception as e:
            logger.warning(f"[S5][{symbol}] snapshot save failed: {e}")
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
    _check_disclaimer()
    MTFBot().run()