"""
bot.py — Main Trading Bot Entry Point (Bitget USDT Futures)

Risk rules enforced here:
  ✅ 5% portfolio margin per trade
  ✅ 10x leverage (isolated margin)
  ✅ SL = −50% of margin  (5% price move × 10x)
  ✅ TP = +100% of margin (10% price move × 10x)
  ✅ 1 active trade MAX — no new trades until position closes
  ✅ LONG only when market sentiment is BULLISH (vol-weighted ≥55% green)
  ✅ SHORT only when market sentiment is BEARISH (vol-weighted ≥55% red)

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
from strategy import evaluate_pair, check_htf
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
        self.active_symbol   = None       # symbol we're currently in (or None)
        self.last_scan_time  = 0
        self.qualified_pairs : list[str] = []
        self.sentiment       = None

        st.reset()
        st.set_status("RUNNING")
        st.add_scan_log("Bitget MTF Bot initialised", "INFO")

        logger.info("🤖 Bitget USDT-Futures MTF Bot")
        logger.info(f"   Mode          : {'DEMO (Paper Trading)' if config.DEMO_MODE else '⚡ LIVE'}")
        logger.info(f"   Risk/trade    : {config.TRADE_SIZE_PCT*100:.0f}% margin | "
                    f"{config.LEVERAGE}x | SL={config.STOP_LOSS_PCT*100:.0f}% | TP={config.TAKE_PROFIT_PCT*100:.0f}%")
        logger.info(f"   Sentiment thr.: {config.SENTIMENT_THRESHOLD*100:.0f}% vol-weighted")
        logger.info("   Dashboard     : python dashboard.py → http://localhost:8080\n")

        # ── Sync any existing open position on startup ─────────────── #
        try:
            existing = tr.get_all_open_positions()
            if existing:
                sym = list(existing.keys())[0]
                pos = existing[sym]
                self.active_symbol = sym
                logger.warning(
                    f"⚠️  Found existing open position: "
                    f"{sym} {pos['side']} qty={pos['qty']} entry={pos['entry_price']}"
                )
                st.add_open_trade({
                    "symbol": sym, "side": pos["side"],
                    "qty":    pos["qty"], "entry": pos["entry_price"],
                    "sl": None, "tp": None, "margin": 0,
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

        # ── 3. Check active trade ────────────────────────────────── #
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
                    f"Entry={pos['entry_price']:.4f} | "
                    f"uPnL={pos['unrealised_pnl']:+.4f} USDT | "
                    f"Balance=${balance:.2f}"
                )
                return   # ← STRICT: do nothing while trade is open
            else:
                logger.info(f"✅ [{self.active_symbol}] Position closed by SL/TP")
                st.close_trade(self.active_symbol, "CLOSED", 0)
                st.add_scan_log(f"[{self.active_symbol}] Trade closed (SL/TP hit)", "INFO")
                self.active_symbol = None

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

        # ── 5. Scan pairs for setup ───────────────────────────────── #
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
            time.sleep(0.4)   # increased from 0.15 → 0.4s to avoid 429

    def _evaluate_pair(self, symbol: str, allowed_direction: str, balance: float) -> bool:
        htf_df = tr.get_candles(symbol, config.HTF_INTERVAL, limit=10)
        ltf_df = tr.get_candles(symbol, config.LTF_INTERVAL, limit=60)

        if htf_df.empty or ltf_df.empty:
            return False

        signal, rsi        = evaluate_pair(symbol, htf_df, ltf_df)
        htf_bull, htf_bear = check_htf(htf_df)
        price              = float(ltf_df["close"].iloc[-1])

        # Detailed per-pair check for dashboard
        from strategy import detect_consolidation, calculate_rsi as _rsi
        rsi_val               = float(_rsi(ltf_df["close"].astype(float)).iloc[-1])
        is_coil, box_h, box_l = detect_consolidation(ltf_df)
        breakout_long         = price > box_h * (1 + config.BREAKOUT_BUFFER_PCT)  if box_h else False
        breakout_short        = price < box_l * (1 - config.BREAKOUT_BUFFER_PCT)  if box_l else False

        # Which checks pass/fail
        rsi_long_ok  = rsi_val > config.RSI_LONG_THRESH
        rsi_short_ok = rsi_val < config.RSI_SHORT_THRESH

        # Determine blocking reason for display
        if htf_bull:
            if   not rsi_long_ok:  reason = f"RSI {rsi_val:.1f} < 70"
            elif not is_coil:      reason = "No consolidation"
            elif not breakout_long:reason = "Waiting breakout"
            else:                  reason = "LONG ✅"
        elif htf_bear:
            if   not rsi_short_ok:  reason = f"RSI {rsi_val:.1f} > 30"
            elif not is_coil:       reason = "No consolidation"
            elif not breakout_short:reason = "Waiting breakout"
            else:                   reason = "SHORT ✅"
        else:
            reason = "No HTF break"

        logger.info(
            f"[{symbol}] RSI={rsi_val:.1f} | "
            f"HTF={'▲' if htf_bull else '▼' if htf_bear else '—'} | "
            f"Coil={'✓' if is_coil else '✗'} | "
            f"Signal={signal} | {reason}"
        )

        st.update_pair_state(symbol, {
            "rsi":            rsi_val,
            "htf_bull":       htf_bull,
            "htf_bear":       htf_bear,
            "signal":         signal,
            "price":          price,
            "consolidating":  is_coil,
            "box_high":       round(box_h, 6) if box_h else None,
            "box_low":        round(box_l, 6) if box_l else None,
            "reason":         reason,
            "rsi_ok":         rsi_long_ok if htf_bull else (rsi_short_ok if htf_bear else False),
        })

        if signal == "HOLD":
            return False

        # Sentiment gate
        if signal == "LONG"  and allowed_direction != "BULLISH":
            return False
        if signal == "SHORT" and allowed_direction != "BEARISH":
            return False

        min_required = 5.0 / (config.TRADE_SIZE_PCT * config.LEVERAGE)
        if balance < min_required:
            st.add_scan_log(f"[{symbol}] Skipped — balance ${balance:.2f} too low (need ${min_required:.2f})", "WARN")
            return False

        if signal == "LONG":
            st.add_scan_log(
                f"[{symbol}] 🟢 LONG | RSI={rsi:.1f} | "
                f"Sentiment BULLISH ({self.sentiment.bullish_weight*100:.1f}%)",
                "SIGNAL"
            )
            trade = tr.open_long(symbol)
            _log_trade("OPEN_LONG", trade)
            st.add_open_trade(trade)
            self.active_symbol = symbol
            return True

        if signal == "SHORT":
            st.add_scan_log(
                f"[{symbol}] 🔴 SHORT | RSI={rsi:.1f} | "
                f"Sentiment BEARISH ({self.sentiment.bullish_weight*100:.1f}%)",
                "SIGNAL"
            )
            trade = tr.open_short(symbol)
            _log_trade("OPEN_SHORT", trade)
            st.add_open_trade(trade)
            self.active_symbol = symbol
            return True

        return False


if __name__ == "__main__":
    MTFBot().run()
