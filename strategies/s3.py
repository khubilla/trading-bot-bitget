"""
Strategy 3 — 15m Swing Pullback (Long-only).

All indicators on 15m chart:
  1. EMA10 > EMA20 > EMA50 > EMA200  (golden alignment)
  2. ADX > S3_ADX_MIN                (strong trend)
  3. Slow Stochastics (5,3) recently oversold (< 30)
     — confirms the pullback has happened
  4. First green candle after the oversold = uptick signal
  5. Current 15m candle closes above that candle's high
  6. MACD (12,26,9) line > signal line  (momentum turning)

SL  = lowest low of oversold period * (1 - SL_BUFFER)
TP  = entry + max(S3_MIN_RR × risk, S3_TAKE_PROFIT_PCT × entry)
"""

import logging
from typing import Literal

import numpy as np
import pandas as pd

from config_s3 import S3_LTF_INTERVAL
from indicators import calculate_adx, calculate_ema, calculate_macd, calculate_stoch
from tools import find_nearest_resistance

logger = logging.getLogger(__name__)
Signal = Literal["LONG", "SHORT", "HOLD", "PENDING_LONG", "PENDING_SHORT"]

# Default candle interval for S3 event snapshots.
SNAPSHOT_INTERVAL = S3_LTF_INTERVAL  # "15m"


def evaluate_s3(
    symbol: str,
    m15_df: pd.DataFrame,
    d1_df: pd.DataFrame,
) -> tuple[Signal, float, float, float, str]:
    """
    Strategy 3 — 15m Swing Pullback (Long-only). All indicators on 15m.
    Returns (signal, adx, entry_trigger, sl_price, reason).
    """
    from config_s3 import (
        S3_ENABLED,
        S3_EMA_FAST, S3_EMA_MED, S3_EMA_SLOW, S3_EMA_TREND,
        S3_ADX_MIN, S3_ADX_MAX,
        S3_STOCH_K_PERIOD, S3_STOCH_D_SMOOTH, S3_STOCH_OVERSOLD, S3_STOCH_LOOKBACK,
        S3_MACD_FAST, S3_MACD_SLOW, S3_MACD_SIGNAL,
        S3_ENTRY_BUFFER_PCT, S3_SL_BUFFER_PCT, S3_MIN_RR, S3_TRAILING_TRIGGER_PCT,
        S3_MIN_SR_CLEARANCE, S3_DAILY_GAIN_MIN,
    )

    if not S3_ENABLED:
        return "HOLD", 0.0, 0.0, 0.0, "S3 disabled"

    min_15m = max(210, S3_STOCH_K_PERIOD + S3_STOCH_D_SMOOTH + S3_STOCH_LOOKBACK + S3_MACD_SLOW + 10)
    if len(m15_df) < min_15m:
        return "HOLD", 0.0, 0.0, 0.0, f"Not enough 15m candles (need {min_15m})"

    closes_15 = m15_df["close"].astype(float)
    ema10  = float(calculate_ema(closes_15, S3_EMA_FAST).iloc[-1])
    ema20  = float(calculate_ema(closes_15, S3_EMA_MED).iloc[-1])
    ema50  = float(calculate_ema(closes_15, S3_EMA_SLOW).iloc[-1])
    ema200 = float(calculate_ema(closes_15, S3_EMA_TREND).iloc[-1])

    if not (ema10 > ema20 > ema50 > ema200):
        return "HOLD", 0.0, 0.0, 0.0, "15m EMA not aligned (need 10>20>50>200)"

    adx_res = calculate_adx(m15_df)
    adx_val = float(adx_res["adx"].iloc[-1])
    if adx_val < S3_ADX_MIN:
        return "HOLD", adx_val, 0.0, 0.0, (
            f"15m ADX={adx_val:.1f} < {S3_ADX_MIN} (not trending)"
        )
    if adx_val > S3_ADX_MAX:
        return "HOLD", adx_val, 0.0, 0.0, (
            f"15m ADX={adx_val:.1f} > {S3_ADX_MAX} (overextended momentum)"
        )

    current_close = float(m15_df["close"].iloc[-1])
    daily_open    = float(d1_df["open"].iloc[-1])
    daily_gain    = (current_close - daily_open) / daily_open
    if daily_gain < S3_DAILY_GAIN_MIN:
        return "HOLD", adx_val, 0.0, 0.0, (
            f"S3: daily gain {daily_gain * 100:.1f}% < {S3_DAILY_GAIN_MIN * 100:.0f}% "
            f"(need ≥10% above daily open {daily_open:.5f})"
        )

    slow_k, _    = calculate_stoch(m15_df, S3_STOCH_K_PERIOD, S3_STOCH_D_SMOOTH)
    macd_line, sig_line, _ = calculate_macd(closes_15, S3_MACD_FAST, S3_MACD_SLOW, S3_MACD_SIGNAL)
    stoch_now = float(slow_k.iloc[-1])
    macd_ok   = float(macd_line.iloc[-1]) > float(sig_line.iloc[-1])

    lookback_k = slow_k.iloc[-S3_STOCH_LOOKBACK - 1:-1]
    oversold_positions = [i for i, v in enumerate(lookback_k) if not np.isnan(v) and v < S3_STOCH_OVERSOLD]

    if not oversold_positions:
        return "HOLD", adx_val, 0.0, 0.0, (
            f"15m ✅ ADX={adx_val:.1f} EMA aligned | "
            f"Stoch={stoch_now:.1f} — no oversold (<{S3_STOCH_OVERSOLD}) in last {S3_STOCH_LOOKBACK} candles"
        )

    last_os_rel  = oversold_positions[-1]
    first_os_rel = oversold_positions[0]
    abs_last_os  = -(S3_STOCH_LOOKBACK + 1) + last_os_rel
    abs_first_os = -(S3_STOCH_LOOKBACK + 1) + first_os_rel

    os_period_df = m15_df.iloc[abs_first_os : abs_last_os + 1]
    pivot_low    = float(os_period_df["low"].min())
    sl_price     = pivot_low * (1 - S3_SL_BUFFER_PCT)

    after_os_df = m15_df.iloc[abs_last_os + 1 : -1].reset_index(drop=True)

    if after_os_df.empty:
        return "HOLD", adx_val, 0.0, sl_price, (
            f"15m ✅ ADX={adx_val:.1f} | "
            f"Stoch oversold ✅ ({len(oversold_positions)} bars) | "
            f"Waiting for first green uptick candle | MACD={'✅' if macd_ok else '❌'}"
        )

    uptick_candle = m15_df.iloc[-2]
    if float(uptick_candle["close"]) <= float(uptick_candle["open"]):
        return "HOLD", adx_val, 0.0, sl_price, (
            f"15m ✅ ADX={adx_val:.1f} | "
            f"Stoch oversold ✅ | Last candle not green — uptick must be immediately before entry | "
            f"MACD={'✅' if macd_ok else '❌'}"
        )
    last_green = uptick_candle

    entry_trigger = float(last_green["high"]) * (1 + S3_ENTRY_BUFFER_PCT)

    _s3_peak = float(m15_df["high"].iloc[-50:].max())
    _s3_res  = find_nearest_resistance(m15_df, max(entry_trigger, _s3_peak) * 1.01,
                                       lookback=300)
    if _s3_res is not None:
        _res_clearance = (_s3_res - entry_trigger) / entry_trigger
        if _res_clearance < S3_MIN_SR_CLEARANCE:
            return "HOLD", adx_val, 0.0, sl_price, (
                f"S3 setup ✅ | Resistance {_s3_res:.5f} too close to entry trigger "
                f"({_res_clearance * 100:.1f}% < {S3_MIN_SR_CLEARANCE * 100:.0f}% min)"
            )

    if current_close <= entry_trigger:
        return "HOLD", adx_val, entry_trigger, sl_price, (
            f"15m ✅ ADX={adx_val:.1f} | EMA aligned | "
            f"Stoch oversold ✅ | Green uptick ✅ | "
            f"Waiting breakout > {entry_trigger:.5f} (now {current_close:.5f}) | "
            f"MACD={'✅' if macd_ok else '❌'}"
        )

    risk = current_close - sl_price
    if risk <= 0:
        return "HOLD", adx_val, entry_trigger, sl_price, "SL >= entry — invalid setup"

    reward = S3_TRAILING_TRIGGER_PCT * current_close
    rr     = reward / risk
    if rr < S3_MIN_RR:
        return "HOLD", adx_val, entry_trigger, sl_price, (
            f"S3 breakout but R:R={rr:.1f} < {S3_MIN_RR} minimum — skip"
        )

    if not macd_ok:
        return "HOLD", adx_val, entry_trigger, sl_price, (
            f"S3 breakout ✅ R:R={rr:.1f} but MACD bearish — skip"
        )

    logger.info(
        f"[S3][{symbol}] ✅ LONG | 15m EMA aligned | ADX={adx_val:.1f} | "
        f"Stoch oversold | Uptick breakout | SL={sl_price:.5f} | R:R={rr:.1f}"
    )
    return "LONG", adx_val, entry_trigger, sl_price, (
        f"S3 ✅ | ADX={adx_val:.1f} | EMA10>20>50>200 | "
        f"Stoch oversold | MACD ✅ | Uptick breakout | R:R={rr:.1f}"
    )


# ── S3 Exit Placement ─────────────────────────────────────── #

def _place_partial_trail_exits(symbol: str, hold_side: str, qty_str: str,
                               sl_trig: float, sl_exec: float,
                               trail_trigger: float, trail_range: float) -> bool:
    """3-leg S3 exits: full SL, 50% partial at trail_trigger, trailing stop on 50%."""
    import time as _t
    import trader
    import bitget as bg

    half_qty   = trader._round_qty(float(qty_str) / 2, symbol)
    rest_qty   = trader._round_qty(float(qty_str) - float(half_qty), symbol)
    range_rate = str(round(trail_range, 4))

    for attempt in range(3):
        try:
            bg.place_pos_sl_only(symbol, hold_side, sl_trig, sl_exec)
            _t.sleep(0.5)
            bg.place_profit_plan(symbol, hold_side, half_qty, trail_trigger)
            _t.sleep(0.5)
            bg.place_moving_plan(symbol, hold_side, rest_qty, trail_trigger, range_rate)
            return True
        except Exception as e:
            logger.warning(f"[{symbol}] S3 exits attempt {attempt+1}/3: {e}")
            if attempt < 2:
                _t.sleep(1.5)
    return False


def compute_and_place_long_exits(symbol: str, qty_str: str, fill: float,
                                 sl_floor: float, box_low: float,
                                 stop_loss_pct: float) -> tuple[bool, float, float]:
    """
    Compute S3 long-side SL/trail levels and place exits.
    Returns (ok, sl_trig, trail_trig).
    """
    import trader
    from config_s3 import S3_TRAILING_TRIGGER_PCT, S3_TRAILING_RANGE_PCT

    trail_trig = float(trader._round_price(fill * (1 + S3_TRAILING_TRIGGER_PCT), symbol))
    raw_sl     = sl_floor if sl_floor > 0 else box_low * 0.999
    sl_cap     = fill * (1 - stop_loss_pct)
    sl_trig    = float(trader._round_price(max(raw_sl, sl_cap), symbol))
    sl_exec    = float(trader._round_price(sl_trig * 0.995, symbol))
    ok = _place_partial_trail_exits(symbol, "long", qty_str, sl_trig, sl_exec, trail_trig, S3_TRAILING_RANGE_PCT)
    return ok, sl_trig, trail_trig


# ── S3 Swing Trail ────────────────────────────────────────── #

def maybe_trail_sl(symbol: str, ap: dict, tr_mod, st_mod) -> None:
    """Structural swing trail for S3 LONG: pull SL up to 15m swing-low after ref high."""
    import config_s3
    from tools import find_swing_high_target, find_swing_low_after_ref

    if not config_s3.S3_USE_SWING_TRAIL:
        return
    if ap.get("side") != "LONG":
        return
    try:
        lb    = config_s3.S3_SWING_LOOKBACK
        cs_df = tr_mod.get_candles(symbol, config_s3.S3_LTF_INTERVAL, limit=lb + 5)
        mark  = tr_mod.get_mark_price(symbol)
        if cs_df.empty or len(cs_df) < 3:
            return
        ref = ap.get("swing_trail_ref")
        if ref is None:
            ap["swing_trail_ref"] = find_swing_high_target(cs_df, mark, lookback=lb)
            return
        if mark >= ref:
            raw = find_swing_low_after_ref(cs_df, mark, ref, lookback=lb)
            if raw:
                swing_sl = raw * (1 - config_s3.S3_SL_BUFFER_PCT)
                if swing_sl > ap.get("sl", 0) and tr_mod.update_position_sl(symbol, swing_sl, hold_side="long"):
                    ap["sl"] = swing_sl
                    st_mod.update_open_trade_sl(symbol, swing_sl)
                    ap["swing_trail_ref"] = find_swing_high_target(cs_df, mark, lookback=lb)
                    logger.info(f"[S3][{symbol}] 📍 Swing trail: SL → {swing_sl:.5f} (15m swing low after ref high {ref:.5f})")
    except Exception as e:
        logger.error(f"S3 swing trail error [{symbol}]: {e}")


# ── S3 DNA Snapshot Fields ────────────────────────────────── #

def dna_fields(candles: dict) -> dict:
    """S3 trade fingerprint: m15 EMA slope, price vs EMA, ADX state."""
    from indicators import calculate_ema, calculate_adx
    from trade_dna import ema_slope, price_vs_ema, adx_state, _is_empty, _closes_from

    out = {}
    m15 = candles.get("m15")
    if _is_empty(m15):
        return out
    closes_m15 = _closes_from(m15)
    ema_m15    = calculate_ema(closes_m15, 20)
    out["snap_trend_m15_ema_slope"]    = ema_slope(closes_m15, 20)
    out["snap_trend_m15_price_vs_ema"] = price_vs_ema(float(closes_m15.iloc[-1]), float(ema_m15.iloc[-1]))
    if hasattr(m15, "columns") and len(m15) >= 20:
        adx_m15 = calculate_adx(m15)["adx"]
        out["snap_trend_m15_adx_state"] = adx_state(adx_m15)
    else:
        out["snap_trend_m15_adx_state"] = ""
    return out


# ── S3 Pending-Signal Queue ───────────────────────────────── #

def queue_pending(bot, c: dict) -> None:
    """Queue an S3 LONG pullback on bot.pending_signals for the entry watcher."""
    import time as _t
    import state as st
    import config_s3

    symbol = c["symbol"]
    s3_trigger = c["s3_trigger"]
    s3_sl      = c["s3_sl"]
    rr = round(config_s3.S3_TRAILING_TRIGGER_PCT * s3_trigger / (s3_trigger - s3_sl), 2) \
         if s3_trigger and s3_sl and s3_trigger > s3_sl else None
    bot.pending_signals[symbol] = {
        "strategy":           "S3",
        "side":               "LONG",
        "trigger":            s3_trigger,
        "s3_sl":              s3_sl,
        "priority_rank":      c.get("priority_rank", 999),
        "priority_score":     c.get("priority_score", 0.0),
        "snap_adx":           round(c["s3_adx"], 1) if c.get("s3_adx") else None,
        "snap_entry_trigger": round(s3_trigger, 8),
        "snap_sl":            round(s3_sl, 8),
        "snap_rr":            rr,
        "snap_sentiment":     bot.sentiment.direction if bot.sentiment else "?",
        "snap_sr_clearance_pct": c.get("s3_sr_resistance_pct"),
        "expires":            _t.time() + 86400,
    }
    st.save_pending_signals(bot.pending_signals)
    logger.info(
        f"[S3][{symbol}] 🕐 PENDING LONG queued | "
        f"trigger={s3_trigger:.5f} | SL={s3_sl:.5f}"
    )
    st.add_scan_log(
        f"[S3][{symbol}] 🕐 PENDING LONG | trigger={s3_trigger:.5f}", "SIGNAL"
    )


# ── S3 Entry Watcher (pending tick) ───────────────────────── #

def handle_pending_tick(bot, symbol: str, sig: dict, balance: float,
                        paper_mode: bool | None = None) -> str | None:
    """S3 pullback trigger + invalidation check. Return 'break' to stop outer loop."""
    import state as st
    import trader as tr
    import config, config_s3

    ps = st.get_pair_state(symbol)
    if ps.get("s3_signal", "HOLD") not in ("LONG",):
        logger.info(f"[S3][{symbol}] 🚫 Signal gone — cancelling pending")
        st.add_scan_log(f"[S3][{symbol}] 🚫 Pending cancelled (signal gone)", "INFO")
        bot.pending_signals.pop(symbol, None)
        st.save_pending_signals(bot.pending_signals)
        return None
    try:
        mark = tr.get_mark_price(symbol)
    except Exception:
        return None
    s3_sl = sig["s3_sl"]
    if mark < s3_sl:
        logger.info(f"[S3][{symbol}] ❌ Invalidated — mark {mark:.5f} < SL {s3_sl:.5f}")
        st.add_scan_log(f"[S3][{symbol}] ❌ Pending cancelled (price below SL)", "INFO")
        bot.pending_signals.pop(symbol, None)
        st.save_pending_signals(bot.pending_signals)
        return None
    s3_trigger = sig["trigger"]
    in_window = s3_trigger <= mark <= s3_trigger * (1 + config_s3.S3_MAX_ENTRY_BUFFER)
    if in_window:
        with bot._trade_lock:
            if symbol in bot.active_positions:
                bot.pending_signals.pop(symbol, None)
                st.save_pending_signals(bot.pending_signals)
                return None
            if len(bot.active_positions) >= config.MAX_CONCURRENT_TRADES:
                return "break"
            if st.is_pair_paused(symbol):
                return None
            bot._fire_s3(symbol, sig, mark, balance)
        bot.pending_signals.pop(symbol, None)
        st.save_pending_signals(bot.pending_signals)
    return None


# ── S3 Paper Trail Setup ──────────────────────────────────── #

def compute_paper_trail_long(mark: float, sl_price: float, tp_price_abs: float = 0,
                             take_profit_pct: float = 0.05) -> tuple[bool, float, float, float, bool]:
    """Paper-trader LONG trail setup for S3. Returns (use_trailing, trail_trigger, trail_range, tp_price, breakeven_after_partial)."""
    from config_s3 import S3_TRAILING_TRIGGER_PCT, S3_TRAILING_RANGE_PCT
    trail_trigger = mark * (1 + S3_TRAILING_TRIGGER_PCT)
    trail_range   = S3_TRAILING_RANGE_PCT
    return True, trail_trigger, trail_range, trail_trigger, False
