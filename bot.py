"""
bot.py — Main Trading Bot Entry Point (Bitget USDT Futures)

Risk rules:
  ✅ 5% portfolio margin per trade
  ✅ 10x leverage (isolated)
  ✅ TP = +100% margin (10% price move) — placed as Bitget order
  ✅ Dynamic SL — bot monitors and closes when:
       LONG:  3m candle closes below box_low  OR  RSI < 70
       SHORT: 3m candle closes above box_high OR  RSI > 30
  ✅ 1 active trade MAX
  ✅ LONG only when BULLISH sentiment
  ✅ SHORT only when BEARISH sentiment
  ✅ Daily EMA filter: price > EMA10 > EMA20 (LONG) or price < EMA10 < EMA20 (SHORT)

Run:
    python bot.py

Dashboard (separate terminal):
    python dashboard.py  →  http://localhost:8080
"""

import time, signal, sys, logging, csv, os
from datetime import datetime, timezone

import config
import state as st
from scanner import get_qualified_pairs_and_sentiment
from strategy import evaluate_pair, check_htf, check_exit, calculate_rsi, detect_consolidation
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
        self.active_symbol   = None      # symbol we're in (or None)
        self.active_side     = None      # "LONG" or "SHORT"
        self.active_box_high = 0.0       # consolidation box at entry
        self.active_box_low  = 0.0
        self.last_scan_time  = 0
        self.qualified_pairs : list[str] = []
        self.sentiment       = None

        st.reset()
        st.set_status("RUNNING")
        st.add_scan_log("Bitget MTF Bot initialised", "INFO")

        logger.info("🤖 Bitget USDT-Futures MTF Bot")
        logger.info(f"   Mode          : {'DEMO (Paper Trading)' if config.DEMO_MODE else '⚡ LIVE'}")
        logger.info(f"   Risk/trade    : {config.TRADE_SIZE_PCT*100:.0f}% margin | "
                    f"{config.LEVERAGE}x | TP={config.TAKE_PROFIT_PCT*100:.0f}%")
        logger.info(f"   Dynamic SL    : LONG exits when close < box_low OR RSI < {config.RSI_LONG_THRESH}")
        logger.info(f"                   SHORT exits when close > box_high OR RSI > {config.RSI_SHORT_THRESH}")
        logger.info(f"   Daily EMA     : price > EMA{config.DAILY_EMA_FAST} > EMA{config.DAILY_EMA_SLOW} (LONG)")
        logger.info(f"   Sentiment thr.: {config.SENTIMENT_THRESHOLD*100:.0f}% vol-weighted")
        logger.info("   Dashboard     : python dashboard.py → http://localhost:8080\n")

        # ── Sync any existing open position on startup ─────────────── #
        try:
            existing = tr.get_all_open_positions()
            if existing:
                sym = list(existing.keys())[0]
                pos = existing[sym]
                self.active_symbol = sym
                self.active_side   = pos["side"]
                logger.warning(
                    f"⚠️  Found existing open position: "
                    f"{sym} {pos['side']} qty={pos['qty']} entry={pos['entry_price']}"
                )
                st.add_open_trade({
                    "symbol": sym, "side": pos["side"],
                    "qty": pos["qty"], "entry": pos["entry_price"],
                    "sl": "dynamic", "tp": None, "margin": 0,
                })
                st.add_scan_log(f"Resumed existing position: {sym} {pos['side']}", "WARN")
        except Exception as e:
            logger.error(f"Startup position sync error: {e}")

    def stop(self, *_):
        logger.info("🛑 Stopping bot...")
        st.set_status("STOPPED")
        st.add_scan_log("Bot stopped", "WARN")
        self.running = False
        sys.exit(0)

    def run(self):
        signal.signal(signal.SIGINT,  self.stop)
        signal.signal(signal.SIGTERM, self.stop)
        logger.info("▶️  Bot running...\n")

        while self.running:
            try:
                self._tick()
            except Exception as e:
                logger.error(f"Tick error: {e}", exc_info=True)
                st.add_scan_log(f"Tick error: {e}", "ERROR")
            time.sleep(config.POLL_INTERVAL_SEC)

    def _tick(self):
        now = time.time()

        # ── 1. Rescan pairs + sentiment ──────────────────────────── #
        if now - self.last_scan_time >= config.SCAN_INTERVAL_SEC:
            self.qualified_pairs, self.sentiment = get_qualified_pairs_and_sentiment()
            st.update_qualified_pairs(self.qualified_pairs)
            st.update_sentiment(self.sentiment)
            st.add_scan_log(
                f"Market: {self.sentiment.direction} "
                f"({self.sentiment.bullish_weight*100:.1f}% green by vol) | "
                f"🟢{self.sentiment.green_count}  🔴{self.sentiment.red_count} "
                f"of {self.sentiment.total_pairs} pairs",
                "INFO"
            )
            self.last_scan_time = now

        if not self.qualified_pairs or self.sentiment is None:
            return

        # ── 2. Sync balance ──────────────────────────────────────── #
        try:
            balance = tr.get_usdt_balance()
            st.update_balance(balance)
        except Exception as e:
            logger.error(f"Balance error: {e}")
            return

        # ── 3. Monitor active trade (dynamic SL) ─────────────────── #
        if self.active_symbol:
            try:
                positions = tr.get_all_open_positions()
            except Exception as e:
                logger.error(f"Positions error: {e}")
                return

            if self.active_symbol in positions:
                pos = positions[self.active_symbol]
                st.update_open_trade_pnl(self.active_symbol, pos["unrealised_pnl"])
                logger.info(
                    f"📊 [{self.active_symbol}] {pos['side']} | "
                    f"Entry={pos['entry_price']:.5f} | "
                    f"uPnL={pos['unrealised_pnl']:+.4f} USDT | "
                    f"Balance=${balance:.2f} | "
                    f"Box={self.active_box_low:.5f}–{self.active_box_high:.5f}"
                )

                # ── Dynamic SL check ─────────────────────────────── #
                try:
                    ltf_df = tr.get_candles(self.active_symbol, config.LTF_INTERVAL, limit=60)
                    if not ltf_df.empty:
                        exit_flag, exit_reason = check_exit(
                            ltf_df,
                            self.active_side,
                            self.active_box_high,
                            self.active_box_low,
                        )
                        if exit_flag == "EXIT":
                            logger.info(f"🔴 [{self.active_symbol}] Dynamic SL triggered: {exit_reason}")
                            st.add_scan_log(f"[{self.active_symbol}] Dynamic SL: {exit_reason}", "SIGNAL")
                            closed = tr.close_position(self.active_symbol, self.active_side)
                            if closed:
                                pnl = pos["unrealised_pnl"]
                                result = "WIN" if pnl > 0 else "LOSS"
                                st.close_trade(self.active_symbol, result, pnl)
                                _log_trade("DYNAMIC_SL_CLOSE", {
                                    "symbol": self.active_symbol,
                                    "side":   self.active_side,
                                    "pnl":    pnl,
                                    "reason": exit_reason,
                                })
                                self.active_symbol   = None
                                self.active_side     = None
                                self.active_box_high = 0.0
                                self.active_box_low  = 0.0
                except Exception as e:
                    logger.error(f"[{self.active_symbol}] Dynamic SL check error: {e}")

                return  # still in trade (or just closed)

            else:
                # Position closed by TP
                logger.info(f"✅ [{self.active_symbol}] Position closed by TP")
                st.close_trade(self.active_symbol, "WIN", 0)
                st.add_scan_log(f"[{self.active_symbol}] Trade closed (TP hit)", "INFO")
                self.active_symbol   = None
                self.active_side     = None
                self.active_box_high = 0.0
                self.active_box_low  = 0.0

        # ── 4. Sentiment gate ─────────────────────────────────────── #
        direction = self.sentiment.direction
        if direction == "NEUTRAL":
            logger.info(
                f"⏸️  Sentiment NEUTRAL ({self.sentiment.bullish_weight*100:.1f}%) — "
                "waiting for clearer market direction"
            )
            st.add_scan_log(
                f"Sentiment NEUTRAL ({self.sentiment.bullish_weight*100:.1f}% green) — no trades",
                "WARN"
            )
            return

        allowed = "LONG" if direction == "BULLISH" else "SHORT"
        logger.info(
            f"🌍 {direction} ({self.sentiment.bullish_weight*100:.1f}% green) "
            f"— scanning for {allowed} setups"
        )

        # ── 5. Scan pairs for entry ───────────────────────────────── #
        for symbol in self.qualified_pairs:
            if not self.running:
                break
            try:
                if self._evaluate_pair(symbol, direction, balance):
                    break
            except RuntimeError as e:
                if "429" in str(e):
                    logger.warning(f"Rate limited — backing off 5s")
                    time.sleep(5)
                else:
                    logger.error(f"[{symbol}] Error: {e}", exc_info=True)
            except Exception as e:
                logger.error(f"[{symbol}] Error: {e}", exc_info=True)
            time.sleep(0.4)

    def _evaluate_pair(self, symbol: str, allowed_direction: str, balance: float) -> bool:
        htf_df   = tr.get_candles(symbol, config.HTF_INTERVAL,   limit=10)
        ltf_df   = tr.get_candles(symbol, config.LTF_INTERVAL,   limit=60)
        daily_df = tr.get_candles(symbol, config.DAILY_INTERVAL, limit=30)

        if htf_df.empty or ltf_df.empty or daily_df.empty:
            return False

        signal, rsi, box_high, box_low = evaluate_pair(
            symbol, htf_df, ltf_df, daily_df, allowed_direction
        )
        htf_bull, htf_bear = check_htf(htf_df)

        # Determine reason for logging
        from strategy import check_daily_ema, calculate_rsi as _rsi, detect_consolidation as _coil
        ema_ok, price, ema10, ema20 = check_daily_ema(daily_df, "LONG" if allowed_direction == "BULLISH" else "SHORT")
        rsi_val = float(_rsi(ltf_df["close"].astype(float)).iloc[-1])
        is_coil, bh, bl = _coil(ltf_df)
        close = float(ltf_df["close"].iloc[-1])

        htf_pass = htf_bull if allowed_direction == "BULLISH" else htf_bear
        rsi_ok   = rsi_val > config.RSI_LONG_THRESH if allowed_direction == "BULLISH" else rsi_val < config.RSI_SHORT_THRESH

        if   not htf_pass: reason = "No HTF break"
        elif not ema_ok:   reason = f"Daily EMA ❌ price={price:.4f} ema10={ema10:.4f} ema20={ema20:.4f}"
        elif not rsi_ok:   reason = f"RSI {rsi_val:.1f} {'< 70' if allowed_direction == 'BULLISH' else '> 30'}"
        elif not is_coil:  reason = "No consolidation"
        elif signal == "HOLD": reason = "Waiting breakout"
        else:              reason = f"{'LONG' if signal == 'LONG' else 'SHORT'} ✅"

        logger.info(
            f"[{symbol}] RSI={rsi_val:.1f} | "
            f"HTF={'▲' if htf_bull else '▼' if htf_bear else '—'} | "
            f"EMA={'✓' if ema_ok else '✗'} | "
            f"Coil={'✓' if is_coil else '✗'} | "
            f"Signal={signal} | {reason}"
        )

        st.update_pair_state(symbol, {
            "rsi":           rsi_val,
            "htf_bull":      htf_bull,
            "htf_bear":      htf_bear,
            "signal":        signal,
            "price":         float(tr.get_mark_price(symbol)) if signal != "HOLD" else close,
            "consolidating": is_coil,
            "box_high":      round(bh, 6) if bh else None,
            "box_low":       round(bl, 6) if bl else None,
            "reason":        reason,
            "rsi_ok":        rsi_ok,
            "ema_ok":        ema_ok,
        })

        if signal == "HOLD":
            return False

        if balance < (5.0 / (config.TRADE_SIZE_PCT * config.LEVERAGE)):
            st.add_scan_log(f"[{symbol}] Skipped — balance ${balance:.2f} too low", "WARN")
            return False

        if signal == "LONG":
            st.add_scan_log(
                f"[{symbol}] 🟢 LONG | RSI={rsi_val:.1f} | "
                f"Sentiment BULLISH ({self.sentiment.bullish_weight*100:.1f}%) | "
                f"Daily EMA ✅",
                "SIGNAL"
            )
            trade = tr.open_long(symbol)
            _log_trade("OPEN_LONG", trade)
            st.add_open_trade(trade)
            self.active_symbol   = symbol
            self.active_side     = "LONG"
            self.active_box_high = box_high
            self.active_box_low  = box_low
            return True

        if signal == "SHORT":
            st.add_scan_log(
                f"[{symbol}] 🔴 SHORT | RSI={rsi_val:.1f} | "
                f"Sentiment BEARISH ({self.sentiment.bullish_weight*100:.1f}%) | "
                f"Daily EMA ✅",
                "SIGNAL"
            )
            trade = tr.open_short(symbol)
            _log_trade("OPEN_SHORT", trade)
            st.add_open_trade(trade)
            self.active_symbol   = symbol
            self.active_side     = "SHORT"
            self.active_box_high = box_high
            self.active_box_low  = box_low
            return True

        return False


if __name__ == "__main__":
    MTFBot().run()
