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
import state as st
from scanner import get_qualified_pairs_and_sentiment
from strategy import (
    evaluate_s1, evaluate_s2,
    check_htf, check_exit,
    calculate_rsi, detect_consolidation,
    check_daily_trend,
)
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


def _log_trade(action: str, details: dict):
    row = {"timestamp": datetime.now(timezone.utc).isoformat(), "action": action, **details}
    write_header = not os.path.exists(config.TRADE_LOG)
    with open(config.TRADE_LOG, "a", newline="") as f:
        import csv as _csv
        w = _csv.DictWriter(f, fieldnames=row.keys())
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
        logger.info(f"   S1 Risk      : {config.TRADE_SIZE_PCT*100:.0f}% | {config.LEVERAGE}x | "
                    f"SL=box TP={config.TAKE_PROFIT_PCT*100:.0f}%")
        logger.info(f"   S1 ADX thr.  : {config.ADX_TREND_THRESHOLD}")
        logger.info(f"   Dashboard    : python dashboard.py → http://localhost:8080\n")

        # ── Startup position sync ─────────────────────────────────── #
        try:
            existing = tr.get_all_open_positions()
            for sym, pos in existing.items():
                self.active_positions[sym] = {
                    "side": pos["side"], "strategy": "UNKNOWN",
                    "box_high": 0.0, "box_low": 0.0,
                }
                logger.warning(f"⚠️  Resumed: {sym} {pos['side']} qty={pos['qty']}")
                st.add_open_trade({
                    "symbol": sym, "side": pos["side"],
                    "qty": pos["qty"], "entry": pos["entry_price"],
                    "sl": "?", "tp": "?", "margin": 0, "strategy": "UNKNOWN",
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
                    # Advisory box-break warning
                    try:
                        ltf_df = tr.get_candles(sym, config.LTF_INTERVAL, limit=10)
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
                    # Position closed by SL/TP
                    logger.info(f"✅ [{sym}] Closed (SL/TP)")
                    st.close_trade(sym, "CLOSED", 0)
                    st.add_scan_log(f"[{sym}] Trade closed", "INFO")
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
            return

        if direction == "BULLISH":
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
        htf_df   = tr.get_candles(symbol, config.HTF_INTERVAL,   limit=10)
        ltf_df   = tr.get_candles(symbol, config.LTF_INTERVAL,   limit=60)
        daily_df = tr.get_candles(symbol, config.DAILY_INTERVAL, limit=150)

        if htf_df.empty or ltf_df.empty or daily_df.empty:
            return False

        # ── Strategy 1 ───────────────────────────────────────────── #
        s1_sig, s1_rsi, s1_bh, s1_bl, s1_adx = evaluate_s1(
            symbol, htf_df, ltf_df, daily_df, allowed_direction
        )
        htf_bull, htf_bear = check_htf(htf_df)

        from strategy import check_daily_trend as _trend
        trend_ok, adx_val = _trend(daily_df, "LONG" if allowed_direction == "BULLISH" else "SHORT")
        rsi_ser = calculate_rsi(ltf_df["close"].astype(float))
        rsi_val = float(rsi_ser.iloc[-1])
        thresh  = config.RSI_LONG_THRESH if allowed_direction == "BULLISH" else config.RSI_SHORT_THRESH
        d_str   = "LONG" if allowed_direction == "BULLISH" else "SHORT"
        is_coil, bh, bl = detect_consolidation(
            ltf_df, rsi_series=rsi_ser, rsi_threshold=thresh, direction=d_str
        )
        close = float(ltf_df["close"].iloc[-1])
        htf_pass = htf_bull if allowed_direction == "BULLISH" else htf_bear
        rsi_ok   = rsi_val > config.RSI_LONG_THRESH if allowed_direction == "BULLISH" \
                   else rsi_val < config.RSI_SHORT_THRESH

        if   not htf_pass:   s1_reason = "No HTF break"
        elif not trend_ok:   s1_reason = f"ADX={adx_val:.1f} < {config.ADX_TREND_THRESHOLD} (sideways)"
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

        st.update_pair_state(symbol, {
            "rsi": rsi_val, "htf_bull": htf_bull, "htf_bear": htf_bear,
            "signal": s1_sig if s1_sig != "HOLD" else s2_sig,
            "price": close,
            "consolidating": is_coil, "box_high": round(bh,6) if bh else None,
            "box_low": round(bl,6) if bl else None,
            "reason":    s1_reason,
            "s2_reason": s2_reason,
            "rsi_ok": rsi_ok,
            "adx": round(adx_val, 1), "trend_ok": trend_ok,
            "strategy": "S1" if s1_sig != "HOLD" else ("S2" if s2_sig != "HOLD" else "S1"),
            "s2_daily_rsi": s2_rsi,
            "s2_big_candle": s2_rsi > 0 and ("big_candle" in s2_reason or "Big candle" in s2_reason or s2_bh > 0),
            "s2_coiling":    s2_bl > 0 and s2_bh > 0,
        })

        # ── Min balance check ─────────────────────────────────────── #
        min_bal = 5.0 / (config.TRADE_SIZE_PCT * config.LEVERAGE)
        if balance < min_bal:
            st.add_scan_log(f"[{symbol}] Skipped — balance ${balance:.2f} < ${min_bal:.2f}", "WARN")
            return False

        # ── Execute S1 ────────────────────────────────────────────── #
        if s1_sig in ("LONG", "SHORT"):
            st.add_scan_log(
                f"[S1][{symbol}] {'🟢' if s1_sig == 'LONG' else '🔴'} {s1_sig} | "
                f"RSI={rsi_val:.1f} ADX={adx_val:.1f} | SL=box",
                "SIGNAL"
            )
            if s1_sig == "LONG":
                trade = tr.open_long(symbol, box_low=s1_bl, leverage=config.LEVERAGE,
                                     trade_size_pct=config.TRADE_SIZE_PCT,
                                     take_profit_pct=config.TAKE_PROFIT_PCT)
            else:
                trade = tr.open_short(symbol, box_high=s1_bh, leverage=config.LEVERAGE,
                                      trade_size_pct=config.TRADE_SIZE_PCT,
                                      take_profit_pct=config.TAKE_PROFIT_PCT)
            trade["strategy"] = "S1"
            _log_trade(f"S1_{s1_sig}", trade)
            st.add_open_trade(trade)
            self.active_positions[symbol] = {
                "side": s1_sig, "strategy": "S1",
                "box_high": s1_bh, "box_low": s1_bl,
            }
            return True

        # ── Execute S2 ────────────────────────────────────────────── #
        if s2_sig == "LONG":
            from config_s2 import S2_LEVERAGE, S2_TRADE_SIZE_PCT, S2_TAKE_PROFIT_PCT, S2_STOP_LOSS_PCT
            st.add_scan_log(f"[S2][{symbol}] 🟢 LONG | {s2_reason}", "SIGNAL")
            trade = tr.open_long(symbol, box_low=s2_bl, leverage=S2_LEVERAGE,
                                 trade_size_pct=S2_TRADE_SIZE_PCT,
                                 take_profit_pct=S2_TAKE_PROFIT_PCT,
                                 stop_loss_pct=S2_STOP_LOSS_PCT)
            trade["strategy"] = "S2"
            _log_trade("S2_LONG", trade)
            st.add_open_trade(trade)
            self.active_positions[symbol] = {
                "side": "LONG", "strategy": "S2",
                "box_high": s2_bh if s2_bh else 0.0, "box_low": s2_bl,
            }
            return True

        return False


if __name__ == "__main__":
    MTFBot().run()