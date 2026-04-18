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

from indicators import calculate_adx, calculate_ema, calculate_macd, calculate_stoch
from tools import find_nearest_resistance

logger = logging.getLogger(__name__)
Signal = Literal["LONG", "SHORT", "HOLD", "PENDING_LONG", "PENDING_SHORT"]


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
