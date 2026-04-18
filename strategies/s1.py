"""
Strategy 1 — Multi-Timeframe RSI Breakout.

Entry filters:
  1D:  ADX > 25 (trending, not sideways)
  1H:  current HIGH > previous HIGH (bull) / LOW < prev LOW (bear)
  3m:  RSI > 70 (long) or < 30 (short)
  3m:  Consolidation — AND RSI must have been in zone throughout
  3m:  Candle closes above/below box + buffer

Exit (SL placed as Bitget order at box_low / box_high):
  TP: entry ± TAKE_PROFIT_PCT placed on Bitget
"""

import logging
from typing import Literal

import pandas as pd

from config_s1 import (
    RSI_PERIOD, RSI_LONG_THRESH, RSI_SHORT_THRESH,
    CONSOLIDATION_CANDLES, CONSOLIDATION_RANGE_PCT,
    BREAKOUT_BUFFER_PCT,
)
from indicators import calculate_adx, calculate_ema, calculate_rsi
from tools import check_htf

logger = logging.getLogger(__name__)
Signal   = Literal["LONG", "SHORT", "HOLD", "PENDING_LONG", "PENDING_SHORT"]
ExitFlag = Literal["EXIT", "HOLD"]


# ── Daily Trend Filter (ADX-based) ────────────────────────── #

def check_daily_trend(daily_df: pd.DataFrame, direction: str) -> tuple[bool, float, float]:
    """
    Replaces EMA filter. Uses ADX to confirm trending (not sideways).

    Rules:
      LONG:  ADX > ADX_TREND_THRESHOLD AND last daily close > EMA20 and RSI > DAILY_RSI_LONG_TRESH
      SHORT: ADX > ADX_TREND_THRESHOLD AND last daily close < EMA20 and RSI < DAILY_RSI_SHORT_TRESH

    Returns (passes, adx_value, daily_rsi)
    """
    from config_s1 import ADX_TREND_THRESHOLD, DAILY_EMA_SLOW, DAILY_RSI_LONG_THRESH, DAILY_RSI_SHORT_THRESH

    if len(daily_df) < 30:
        logger.debug("  Daily trend: not enough candles")
        return False, 0.0, 0.0

    closes  = daily_df["close"].astype(float)
    adx_res = calculate_adx(daily_df)
    adx_val = float(adx_res["adx"].iloc[-1])
    rsi_res = calculate_rsi(closes, RSI_PERIOD)
    rsi_val = float(rsi_res.iloc[-1])
    ema20   = float(calculate_ema(closes, DAILY_EMA_SLOW).iloc[-1])
    price   = float(closes.iloc[-1])

    if direction == "LONG":
        passes = adx_val > ADX_TREND_THRESHOLD and price > ema20 and rsi_val > DAILY_RSI_LONG_THRESH
    else:
        passes = adx_val > ADX_TREND_THRESHOLD and price < ema20 and rsi_val < DAILY_RSI_SHORT_THRESH

    logger.debug(
        f"  Daily trend [{direction}]: ADX={adx_val:.1f}: RSI={rsi_val:.1f} "
        f"(need >{ADX_TREND_THRESHOLD}) price={'above' if price > ema20 else 'below'} EMA20 "
        f"→ {'✅' if passes else '❌'}"
    )
    return passes, adx_val, rsi_val


# ── Consolidation (RSI-zone aware) ────────────────────────── #

def detect_consolidation(
    ltf_df: pd.DataFrame,
    rsi_series: pd.Series | None = None,
    rsi_threshold: float | None = None,
    direction: str = "LONG",
) -> tuple[bool, float, float]:
    """
    Returns (is_consolidating, box_high, box_low).
    Only valid if price range is tight AND RSI was in the zone throughout.
    """
    window = ltf_df.iloc[-(CONSOLIDATION_CANDLES + 1):-1]
    if len(window) < CONSOLIDATION_CANDLES:
        return False, 0.0, 0.0

    box_high = float(window["high"].max())
    box_low  = float(window["low"].min())
    mid      = (box_high + box_low) / 2
    if mid == 0:
        return False, 0.0, 0.0

    range_pct = (box_high - box_low) / mid
    if range_pct > CONSOLIDATION_RANGE_PCT:
        return False, box_high, box_low

    if rsi_series is not None and rsi_threshold is not None:
        window_rsi = rsi_series.iloc[-(CONSOLIDATION_CANDLES + 1):-1]
        if direction == "LONG" and not (window_rsi > rsi_threshold).all():
            logger.debug(f"  Consolidation ❌ RSI not >  {rsi_threshold} throughout (min={window_rsi.min():.1f})")
            return False, box_high, box_low
        if direction == "SHORT" and not (window_rsi < rsi_threshold).all():
            logger.debug(f"  Consolidation ❌ RSI not < {rsi_threshold} throughout (max={window_rsi.max():.1f})")
            return False, box_high, box_low

    logger.debug(f"  Consolidation ✓ range={range_pct*100:.3f}% H={box_high} L={box_low}")
    return True, box_high, box_low


# ── LTF Entry ─────────────────────────────────────────────── #

def check_ltf_long(ltf_df: pd.DataFrame) -> tuple[bool, float, float, float]:
    if len(ltf_df) < RSI_PERIOD + CONSOLIDATION_CANDLES + 2:
        return False, 50.0, 0.0, 0.0

    closes  = ltf_df["close"].astype(float)
    rsi_ser = calculate_rsi(closes, RSI_PERIOD)
    rsi_val = float(rsi_ser.iloc[-1])

    if rsi_val <= RSI_LONG_THRESH:
        return False, rsi_val, 0.0, 0.0

    is_coil, box_high, box_low = detect_consolidation(
        ltf_df, rsi_series=rsi_ser, rsi_threshold=RSI_LONG_THRESH, direction="LONG"
    )
    if not is_coil:
        return False, rsi_val, 0.0, 0.0

    close = float(ltf_df["close"].iloc[-1])
    if close > box_high * (1 + BREAKOUT_BUFFER_PCT):
        return True, rsi_val, box_high, box_low
    return False, rsi_val, box_high, box_low


def check_ltf_short(ltf_df: pd.DataFrame) -> tuple[bool, float, float, float]:
    if len(ltf_df) < RSI_PERIOD + CONSOLIDATION_CANDLES + 2:
        return False, 50.0, 0.0, 0.0

    closes  = ltf_df["close"].astype(float)
    rsi_ser = calculate_rsi(closes, RSI_PERIOD)
    rsi_val = float(rsi_ser.iloc[-1])

    if rsi_val >= RSI_SHORT_THRESH:
        return False, rsi_val, 0.0, 0.0

    is_coil, box_high, box_low = detect_consolidation(
        ltf_df, rsi_series=rsi_ser, rsi_threshold=RSI_SHORT_THRESH, direction="SHORT"
    )
    if not is_coil:
        return False, rsi_val, 0.0, 0.0

    close = float(ltf_df["close"].iloc[-1])
    if close < box_low * (1 - BREAKOUT_BUFFER_PCT):
        return True, rsi_val, box_high, box_low
    return False, rsi_val, box_high, box_low


# ── Dynamic Exit Check ────────────────────────────────────── #

def check_exit(
    ltf_df: pd.DataFrame,
    side: str,
    box_high: float,
    box_low: float,
) -> tuple[ExitFlag, str]:
    """
    Advisory check — warns when last closed 3m candle broke box.
    Primary exit is handled by Bitget SL/TP orders.
    Uses iloc[-2] = last FULLY CLOSED candle.
    """
    if len(ltf_df) < 3:
        return "HOLD", ""

    last_closed = float(ltf_df["close"].iloc[-2])

    if side == "LONG" and box_low > 0 and last_closed < box_low:
        return "EXIT", f"Last closed 3m ({last_closed:.6f}) < box_low ({box_low:.6f})"
    if side == "SHORT" and box_high > 0 and last_closed > box_high:
        return "EXIT", f"Last closed 3m ({last_closed:.6f}) > box_high ({box_high:.6f})"

    return "HOLD", ""


# ── Strategy 1 Master Evaluator ───────────────────────────── #

def evaluate_s1(
    symbol: str,
    htf_df: pd.DataFrame,
    ltf_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    allowed_direction: str,
) -> tuple[Signal, float, float, float, float, float]:
    """
    Returns (signal, rsi, box_high, box_low, adx, daily_rsi)
    allowed_direction: "BULLISH" | "BEARISH"
    """
    from config_s1 import S1_ENABLED
    if not S1_ENABLED:
        return "HOLD", 50.0, 0.0, 0.0, 0.0, 0.0

    bull_htf, bear_htf = check_htf(htf_df)

    if bull_htf and allowed_direction == "BULLISH":
        trend_ok, adx, daily_rsi = check_daily_trend(daily_df, "LONG")
        if not trend_ok:
            return "HOLD", 50.0, 0.0, 0.0, adx, daily_rsi
        valid, rsi, bh, bl = check_ltf_long(ltf_df)
        if valid:
            logger.info(f"[S1][{symbol}] ✅ LONG | RSI={rsi:.1f} ADX={adx:.1f} Daily RSI={daily_rsi:.1f}")
            return "LONG", rsi, bh, bl, adx, daily_rsi
        return "HOLD", rsi, bh, bl, adx, daily_rsi

    if bear_htf and allowed_direction == "BEARISH":
        trend_ok, adx, daily_rsi = check_daily_trend(daily_df, "SHORT")
        if not trend_ok:
            return "HOLD", 50.0, 0.0, 0.0, adx, daily_rsi
        valid, rsi, bh, bl = check_ltf_short(ltf_df)
        if valid:
            logger.info(f"[S1][{symbol}] ✅ SHORT | RSI={rsi:.1f} ADX={adx:.1f} Daily RSI={daily_rsi:.1f}")
            return "SHORT", rsi, bh, bl, adx, daily_rsi
        return "HOLD", rsi, bh, bl, adx, daily_rsi

    return "HOLD", 50.0, 0.0, 0.0, 0.0, 0.0


# ── S1 Exit Placement ─────────────────────────────────────── #

def _place_exits(symbol: str, hold_side: str, qty_str: str,
                 sl_trig: float, sl_exec: float,
                 trail_trigger: float, trail_range: float) -> bool:
    """
    S1 exit orders (3 legs): SL on full qty, partial TP (50% at trail_trigger),
    trailing stop on the remaining 50%.
    """
    import time as _t
    import trader  # late import (avoids circular dep; picks up test patches of trader._sym_info)
    import bitget as bg

    half_qty = trader._round_qty(float(qty_str) / 2, symbol)
    rest_qty = trader._round_qty(float(qty_str) - float(half_qty), symbol)
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
            logger.warning(f"[{symbol}] S1 exits attempt {attempt+1}/3: {e}")
            if attempt < 2:
                _t.sleep(1.5)
    return False


def compute_and_place_long_exits(symbol: str, qty_str: str, fill: float, sl_floor: float) -> tuple[bool, float, float]:
    """Compute S1 long-side SL/trail levels and place exits. Returns (ok, sl_trig, trail_trig)."""
    import trader
    from config_s1 import S1_TRAIL_RANGE_PCT, TAKE_PROFIT_PCT

    trail_trig = float(trader._round_price(fill * (1 + TAKE_PROFIT_PCT), symbol))
    sl_trig    = float(trader._round_price(sl_floor, symbol))
    sl_exec    = float(trader._round_price(sl_trig * 0.995, symbol))
    ok = _place_exits(symbol, "long", qty_str, sl_trig, sl_exec, trail_trig, S1_TRAIL_RANGE_PCT)
    return ok, sl_trig, trail_trig


def compute_and_place_short_exits(symbol: str, qty_str: str, fill: float, sl_trig: float, sl_exec: float) -> tuple[bool, float, float]:
    """Compute S1 short-side trail level and place exits. Returns (ok, sl_trig, trail_trig)."""
    import trader
    from config_s1 import S1_TRAIL_RANGE_PCT, TAKE_PROFIT_PCT

    trail_trig = float(trader._round_price(fill * (1 - TAKE_PROFIT_PCT), symbol))
    ok = _place_exits(symbol, "short", qty_str, sl_trig, sl_exec, trail_trig, S1_TRAIL_RANGE_PCT)
    return ok, sl_trig, trail_trig


# ── S1 Swing Trail ────────────────────────────────────────── #

def maybe_trail_sl(symbol: str, ap: dict, tr_mod, st_mod) -> None:
    """
    Structural swing trail for S1: LONG pulls SL up to latest 3m swing-low
    after price reaches the prior swing-high; SHORT mirrors for swing-high.
    """
    from config_s1 import (
        S1_USE_SWING_TRAIL, S1_SWING_LOOKBACK, S1_SL_BUFFER_PCT, LTF_INTERVAL,
    )
    from tools import (
        find_swing_high_target, find_swing_low_target,
        find_swing_low_after_ref, find_swing_high_after_ref,
    )

    if not S1_USE_SWING_TRAIL:
        return
    try:
        cs_df = tr_mod.get_candles(symbol, LTF_INTERVAL, limit=S1_SWING_LOOKBACK + 5)
        mark  = tr_mod.get_mark_price(symbol)
        if cs_df.empty or len(cs_df) < 3:
            return
        if ap["side"] == "LONG":
            ref = ap.get("swing_trail_ref")
            if ref is None:
                ap["swing_trail_ref"] = find_swing_high_target(cs_df, mark, lookback=S1_SWING_LOOKBACK)
            elif mark >= ref:
                raw = find_swing_low_after_ref(cs_df, mark, ref, lookback=S1_SWING_LOOKBACK)
                if raw:
                    swing_sl = raw * (1 - S1_SL_BUFFER_PCT)
                    if swing_sl > ap.get("sl", 0) and tr_mod.update_position_sl(symbol, swing_sl, hold_side="long"):
                        ap["sl"] = swing_sl
                        st_mod.update_open_trade_sl(symbol, swing_sl)
                        ap["swing_trail_ref"] = find_swing_high_target(cs_df, mark, lookback=S1_SWING_LOOKBACK)
                        logger.info(f"[S1][{symbol}] 📍 Swing trail: SL → {swing_sl:.5f} (3m swing low after ref high {ref:.5f})")
        else:
            ref = ap.get("swing_trail_ref")
            if ref is None:
                ap["swing_trail_ref"] = find_swing_low_target(cs_df, mark, lookback=S1_SWING_LOOKBACK)
            elif mark <= ref:
                raw = find_swing_high_after_ref(cs_df, mark, ref, lookback=S1_SWING_LOOKBACK)
                if raw:
                    swing_sl = raw * (1 + S1_SL_BUFFER_PCT)
                    if swing_sl < ap.get("sl", float("inf")) and tr_mod.update_position_sl(symbol, swing_sl, hold_side="short"):
                        ap["sl"] = swing_sl
                        st_mod.update_open_trade_sl(symbol, swing_sl)
                        ap["swing_trail_ref"] = find_swing_low_target(cs_df, mark, lookback=S1_SWING_LOOKBACK)
                        logger.info(f"[S1][{symbol}] 📍 Swing trail: SL → {swing_sl:.5f} (3m swing high after ref low {ref:.5f})")
    except Exception as e:
        logger.error(f"S1 swing trail error [{symbol}]: {e}")
