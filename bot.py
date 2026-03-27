"""
bot.py — Main Entry Point

Runs Strategy 1 and Strategy 2 simultaneously.
Only 1 active trade at a time across both strategies.

Strategy 1: MTF RSI Breakout (ADX trend filter, 1H break, 3m RSI+coil+breakout)
Strategy 2: 30-Day Breakout + 3m Consolidation (long candle, squeeze, 3m coil+break)
"""

import time, signal, sys, logging, csv, os
from datetime import datetime, timezone

import config
import config_s1
import config_s2
import config_s3
import config_s4
import state as st
from scanner import get_qualified_pairs_and_sentiment
from strategy import (
    evaluate_s1, evaluate_s2, evaluate_s3, evaluate_s4,
    check_htf, check_exit,
    calculate_rsi, detect_consolidation,
    check_daily_trend,
)
PAPER_MODE = "--paper" in sys.argv
if PAPER_MODE:
    import paper_trader as tr
    st.set_file("state_paper.json")
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
    "timestamp", "action", "symbol", "side", "qty", "entry", "sl", "tp",
    "box_low", "box_high", "leverage", "margin", "tpsl_set", "strategy",
    # S1 snapshot
    "snap_rsi", "snap_adx", "snap_htf", "snap_coil", "snap_box_range_pct", "snap_sentiment",
    # S2 snapshot
    "snap_daily_rsi",
    # S3 snapshot
    "snap_entry_trigger", "snap_sl", "snap_rr",
    # S4 snapshot
    "snap_rsi_peak", "snap_spike_body_pct", "snap_rsi_div", "snap_rsi_div_str",
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

        st.reset()
        st.set_status("RUNNING")
        st.add_scan_log("Bot initialised (S1 + S2)", "INFO")

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
                strategy = pos.get("strategy", "UNKNOWN")
                self.active_positions[sym] = {
                    "side": pos["side"], "strategy": strategy,
                    "box_high": 0.0, "box_low": 0.0,
                }
                logger.warning(f"⚠️  Resumed: {sym} {pos['side']} qty={pos['qty']} [{strategy}]")
                st.add_open_trade({
                    "symbol":   sym,
                    "side":     pos["side"],
                    "qty":      pos["qty"],
                    "entry":    pos["entry_price"],
                    "sl":       pos.get("sl", "?"),
                    "tp":       pos.get("tp", "?"),
                    "margin":   pos.get("margin", 0),
                    "leverage": pos.get("leverage", 0),
                    "strategy": strategy,
                })
            if existing:
                st.add_scan_log(f"Resumed {len(existing)} position(s)", "WARN")
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

            # Sync pnl + detect closed positions
            for sym in list(self.active_positions.keys()):
                if sym in exchange_positions:
                    pos = exchange_positions[sym]
                    st.update_open_trade_pnl(sym, pos["unrealised_pnl"])
                    ap = self.active_positions[sym]
                    logger.info(
                        f"📊 [{ap['strategy']}][{sym}] {pos['side']} | "
                        f"Entry={pos['entry_price']:.5f} | "
                        f"uPnL={pos['unrealised_pnl']:+.4f} USDT | "
                        f"Box={ap['box_low']:.5f}–{ap['box_high']:.5f}"
                    )
                    # Scale-in check (S2/S4 only)
                    if ap.get("scale_in_pending") and time.time() >= ap["scale_in_after"]:
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
                            else:
                                logger.info(f"[{ap['strategy']}][{sym}] ⏸️ Scale-in skipped — price {mark_now:.5f} outside entry window")
                            ap["scale_in_pending"] = False
                        except Exception as e:
                            logger.error(f"Scale-in error [{sym}]: {e}")
                            ap["scale_in_pending"] = False

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
                    last_pnl = 0.0
                    for t in st._read()["open_trades"]:
                        if t["symbol"] == sym:
                            last_pnl = float(t.get("unrealised_pnl") or 0)
                            break
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
                    _log_trade(f"{ap['strategy']}_CLOSE", {
                        "symbol": sym, "side": ap["side"],
                        "pnl": round(last_pnl, 4), "result": result,
                    })
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

        # ── 6. Scan pairs for new entries ─────────────────────────── #
        for symbol in self.qualified_pairs:
            if not self.running:
                break
            # Re-check limit inside loop (a trade may have opened this cycle)
            if len(self.active_positions) >= config.MAX_CONCURRENT_TRADES:
                break
            # Skip symbols already in a trade
            if symbol in self.active_positions:
                continue
            # Skip pairs paused due to 3 losses today
            if st.is_pair_paused(symbol):
                continue
            try:
                if self._evaluate_pair(symbol, direction, balance):
                    pass  # keep scanning for more if limit allows
            except RuntimeError as e:
                if "429" in str(e):
                    logger.warning("Rate limited — backing off 5s")
                    time.sleep(5)
                else:
                    logger.error(f"[{symbol}] Error: {e}", exc_info=True)
            except Exception as e:
                logger.error(f"[{symbol}] Error: {e}", exc_info=True)
            time.sleep(0.4)

    def _evaluate_pair(self, symbol: str, allowed_direction: str, balance: float) -> bool:
        htf_df   = tr.get_candles(symbol, config_s1.HTF_INTERVAL,   limit=10)
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

        # ── Strategy 3 ───────────────────────────────────────────── #
        s3_sig, s3_adx, s3_trigger, s3_sl, s3_reason = "HOLD", 0.0, 0.0, 0.0, ""
        if config_s3.S3_ENABLED and self.sentiment.direction != "BEARISH":
            m15_df = tr.get_candles(symbol, config_s3.S3_LTF_INTERVAL, limit=300)
            if not m15_df.empty:
                s3_sig, s3_adx, s3_trigger, s3_sl, s3_reason = evaluate_s3(
                    symbol, m15_df
                )
                logger.info(f"[S3][{symbol}] {s3_reason}")

        # ── Strategy 4 ───────────────────────────────────────────── #
        s4_sig, s4_rsi, s4_trigger, s4_sl, s4_body_pct, s4_rsi_peak, s4_div, s4_div_str, s4_reason = "HOLD", 50.0, 0.0, 0.0, 0.0, 0.0, False, "", ""
        if config_s4.S4_ENABLED and self.sentiment.direction != "BULLISH":
            s4_sig, s4_rsi, s4_trigger, s4_sl, s4_body_pct, s4_rsi_peak, s4_div, s4_div_str, s4_reason = evaluate_s4(symbol, daily_df)
            logger.info(f"[S4][{symbol}] {s4_reason}")

        st.update_pair_state(symbol, {
            "rsi": rsi_val, "htf_bull": htf_bull, "htf_bear": htf_bear,
            "signal": s1_sig if s1_sig != "HOLD" else (s2_sig if s2_sig != "HOLD" else (s3_sig if s3_sig != "HOLD" else s4_sig)),
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
            "s4_reason": s4_reason,
            "s4_signal": s4_sig,
            "rsi_ok": rsi_ok,
            "adx": round(adx_val, 1), "trend_ok": trend_ok,
            "strategy": "S1" if s1_sig != "HOLD" else ("S2" if s2_sig != "HOLD" else ("S3" if s3_sig != "HOLD" else ("S4" if s4_sig != "HOLD" else "S1"))),
            "s2_daily_rsi": s2_rsi,
            "s2_big_candle": s2_rsi > 0 and ("big_candle" in s2_reason or "Big candle" in s2_reason or s2_bh > 0),
            "s2_coiling":    s2_bl > 0 and s2_bh > 0,
        })

        # ── Min balance check ─────────────────────────────────────── #
        min_bal = 5.0 / (config_s1.TRADE_SIZE_PCT * config_s1.LEVERAGE)
        if balance < min_bal:
            st.add_scan_log(f"[{symbol}] Skipped — balance ${balance:.2f} < ${min_bal:.2f}", "WARN")
            return False

        # ── Execute S1 ────────────────────────────────────────────── #
        if s1_sig in ("LONG", "SHORT") and allowed_direction != "NEUTRAL":
            st.add_scan_log(
                f"[S1][{symbol}] {'🟢' if s1_sig == 'LONG' else '🔴'} {s1_sig} | "
                f"RSI={rsi_val:.1f} ADX={adx_val:.1f} | SL=box",
                "SIGNAL"
            )
            # SL = lower of (-50% P/L) or (last swing candle × buffer)
            lev = config_s1.LEVERAGE
            mark_now = float(ltf_df["close"].iloc[-1])
            pnl50_long  = mark_now * (1 - 0.50 / lev)   # -50% P/L at leverage
            pnl50_short = mark_now * (1 + 0.50 / lev)   # -50% P/L for short
            last_red_low = next(
                (float(r["low"])  for _, r in ltf_df.iloc[::-1].iterrows() if float(r["close"]) < float(r["open"])),
                None
            )
            last_grn_high = next(
                (float(r["high"]) for _, r in ltf_df.iloc[::-1].iterrows() if float(r["close"]) > float(r["open"])),
                None
            )
            sl_long  = min(pnl50_long,  last_red_low  * 0.998 if last_red_low  else pnl50_long)
            sl_short = max(pnl50_short, last_grn_high * 1.002 if last_grn_high else pnl50_short)

            if s1_sig == "LONG":
                trade = tr.open_long(symbol, sl_floor=sl_long, leverage=config_s1.LEVERAGE,
                                     trade_size_pct=config_s1.TRADE_SIZE_PCT,
                                     take_profit_pct=config_s1.TAKE_PROFIT_PCT)
            else:
                trade = tr.open_short(symbol, sl_floor=sl_short, leverage=config_s1.LEVERAGE,
                                      trade_size_pct=config_s1.TRADE_SIZE_PCT,
                                      take_profit_pct=config_s1.TAKE_PROFIT_PCT)
            trade["strategy"] = "S1"
            trade["snap_rsi"]         = round(rsi_val, 1)
            trade["snap_adx"]         = round(adx_val, 1)
            trade["snap_htf"]         = "BULL" if htf_bull else "BEAR" if htf_bear else "NONE"
            trade["snap_coil"]        = is_coil
            trade["snap_box_range_pct"] = round((s1_bh - s1_bl) / s1_bl * 100, 3) if s1_bh and s1_bl else None
            trade["snap_sentiment"]   = self.sentiment.direction
            _log_trade(f"S1_{s1_sig}", trade)
            st.add_open_trade(trade)
            if PAPER_MODE: tr.tag_strategy(symbol, "S1")
            self.active_positions[symbol] = {
                "side": s1_sig, "strategy": "S1",
                "box_high": s1_bh, "box_low": s1_bl,
            }
            return True

        # ── Execute S2 ────────────────────────────────────────────── #
        if s2_sig == "LONG":
            mark_now = tr.get_mark_price(symbol)
            if mark_now > s2_bh * (1 + config_s2.S2_MAX_ENTRY_BUFFER):
                logger.info(f"[S2][{symbol}] ⏸️ LONG setup valid but entry missed — price {mark_now:.5f} already >{config_s2.S2_MAX_ENTRY_BUFFER*100:.0f}% above trigger {s2_bh:.5f}")
                return False
            st.add_scan_log(f"[S2][{symbol}] 🟢 LONG | {s2_reason}", "SIGNAL")
            trade = tr.open_long(symbol, box_low=s2_bl, leverage=config_s2.S2_LEVERAGE,
                                 trade_size_pct=config_s2.S2_TRADE_SIZE_PCT * 0.5,
                                 take_profit_pct=config_s2.S2_TAKE_PROFIT_PCT,
                                 stop_loss_pct=config_s2.S2_STOP_LOSS_PCT,
                                 use_s2_exits=True)
            trade["strategy"] = "S2"
            trade["snap_daily_rsi"]   = round(s2_rsi, 1)
            trade["snap_box_range_pct"] = round((s2_bh - s2_bl) / s2_bl * 100, 3) if s2_bh and s2_bl else None
            trade["snap_sentiment"]   = self.sentiment.direction
            _log_trade("S2_LONG", trade)
            st.add_open_trade(trade)
            if PAPER_MODE: tr.tag_strategy(symbol, "S2")
            self.active_positions[symbol] = {
                "side": "LONG", "strategy": "S2",
                "box_high": s2_bh if s2_bh else 0.0, "box_low": s2_bl,
                "scale_in_pending": True,
                "scale_in_after": time.time() + 3600,
                "scale_in_trade_size_pct": config_s2.S2_TRADE_SIZE_PCT,
            }
            return True

        # ── Execute S3 ────────────────────────────────────────────── #
        if s3_sig == "LONG":
            mark_now = tr.get_mark_price(symbol)
            if mark_now > s3_trigger * (1 + config_s3.S3_MAX_ENTRY_BUFFER):
                logger.info(f"[S3][{symbol}] ⏸️ LONG setup valid but entry missed — price {mark_now:.5f} already >{config_s3.S3_MAX_ENTRY_BUFFER*100:.0f}% above trigger {s3_trigger:.5f}")
                return False
            st.add_scan_log(f"[S3][{symbol}] 🟢 LONG | {s3_reason}", "SIGNAL")
            trade = tr.open_long(
                symbol,
                sl_floor        = s3_sl,
                leverage        = config_s3.S3_LEVERAGE,
                trade_size_pct  = config_s3.S3_TRADE_SIZE_PCT,
                use_s2_exits    = True,
            )
            trade["strategy"] = "S3"
            trade["snap_adx"]           = round(s3_adx, 1) if s3_adx else None
            trade["snap_entry_trigger"] = round(s3_trigger, 8) if s3_trigger else None
            trade["snap_sl"]            = round(s3_sl, 8) if s3_sl else None
            trade["snap_rr"]            = round(
                config_s3.S3_TRAILING_TRIGGER_PCT * s3_trigger / (s3_trigger - s3_sl), 2
            ) if s3_trigger and s3_sl and s3_trigger > s3_sl else None
            trade["snap_sentiment"]     = self.sentiment.direction
            _log_trade("S3_LONG", trade)
            st.add_open_trade(trade)
            if PAPER_MODE: tr.tag_strategy(symbol, "S3")
            self.active_positions[symbol] = {
                "side": "LONG", "strategy": "S3",
                "box_high": s3_trigger, "box_low": s3_sl,
            }
            return True

        # ── Execute S4 ────────────────────────────────────────────── #
        if s4_sig == "SHORT" and s4_trigger > 0:
            mark_now        = tr.get_mark_price(symbol)
            prev_low_approx = s4_trigger / (1 - config_s4.S4_ENTRY_BUFFER)
            too_far         = mark_now < prev_low_approx * (1 - config_s4.S4_MAX_ENTRY_BUFFER)
            if too_far:
                logger.info(
                    f"[S4][{symbol}] ⏸️ SHORT setup valid but entry missed — "
                    f"price {mark_now:.5f} already >{config_s4.S4_MAX_ENTRY_BUFFER*100:.0f}% "
                    f"below prev_low {prev_low_approx:.5f} (window: {s4_trigger:.5f}–{prev_low_approx*(1-config_s4.S4_MAX_ENTRY_BUFFER):.5f})"
                )
            if mark_now <= s4_trigger and not too_far:
                st.add_scan_log(
                    f"[S4][{symbol}] 🔴 SHORT | spike={s4_body_pct*100:.0f}% RSI={s4_rsi:.1f} | "
                    f"entry≤{s4_trigger:.5f} triggered @ {mark_now:.5f}",
                    "SIGNAL"
                )
                trade = tr.open_short(
                    symbol,
                    sl_floor       = s4_sl,
                    leverage       = config_s4.S4_LEVERAGE,
                    trade_size_pct = config_s4.S4_TRADE_SIZE_PCT * 0.5,
                    use_s4_exits   = True,
                )
                trade["strategy"]            = "S4"
                trade["snap_rsi"]            = round(s4_rsi, 1)
                trade["snap_rsi_peak"]       = round(s4_rsi_peak, 1)
                trade["snap_spike_body_pct"] = round(s4_body_pct * 100, 1)
                trade["snap_rsi_div"]        = s4_div
                trade["snap_rsi_div_str"]    = s4_div_str
                trade["snap_sl"]             = round(s4_sl, 8) if s4_sl else None
                trade["snap_sentiment"]      = self.sentiment.direction
                _log_trade("S4_SHORT", trade)
                st.add_open_trade(trade)
                if PAPER_MODE: tr.tag_strategy(symbol, "S4")
                self.active_positions[symbol] = {
                    "side": "SHORT", "strategy": "S4",
                    "box_high": s4_sl, "box_low": s4_trigger,
                    "scale_in_pending": True,
                    "scale_in_after": time.time() + 3600,
                    "scale_in_trade_size_pct": config_s4.S4_TRADE_SIZE_PCT,
                    "s4_prev_low": prev_low_approx,
                }
                return True

        return False


if __name__ == "__main__":
    MTFBot().run()