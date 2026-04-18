"""
Strategy 5 — SMC Order Block Pullback with ChoCH Entry.

Multi-timeframe Smart Money Concept strategy.
  1D:  EMA10 > EMA20 > EMA50 (bullish) / reverse (bearish)
  1H:  Break of Structure — close above prior swing high (LONG)
       or below prior swing low (SHORT)
  15m: Find Order Block (last opposing candle before impulse)
  15m: Pullback touches OB zone
  15m: Change of Character (ChoCH) — candle closes back through
       OB boundary → entry trigger confirmed

LONG:  OB = last red candle before bullish impulse
       Entry  = ob_high (red candle open) + buffer
       SL     = ob_low  (red candle low)  − buffer

SHORT: OB = last green candle before bearish impulse
       Entry  = ob_low  (green candle open) − buffer
       SL     = ob_high (green candle high) + buffer
"""

import logging
from typing import Literal

import pandas as pd

from indicators import calculate_ema
from tools import (
    find_bearish_ob,
    find_bullish_ob,
    find_fvg,
    find_swing_high_target,
    find_swing_low_target,
)

logger = logging.getLogger(__name__)
Signal = Literal["LONG", "SHORT", "HOLD", "PENDING_LONG", "PENDING_SHORT"]


def evaluate_s5(
    symbol: str,
    daily_df: pd.DataFrame,
    htf_df: pd.DataFrame,
    m15_df: pd.DataFrame,
    allowed_direction: str,
    cfg=None,
) -> tuple[Signal, float, float, float, float, float, str]:
    """
    Strategy 5 — SMC Order Block Pullback.
    Returns (signal, entry_trigger, sl_price, tp_price, ob_low, ob_high, reason).

    When `cfg` is not None (IG path): reads all S5 params from the instrument
    CONFIG dict.  When `cfg` is None (Bitget/backtest path): performs LATE
    import of config_s5 module at call time.
    """
    if cfg is not None:
        S5_ENABLED                    = cfg["s5_enabled"]
        S5_DAILY_EMA_FAST             = cfg["s5_daily_ema_fast"]
        S5_DAILY_EMA_MED              = cfg["s5_daily_ema_med"]
        S5_DAILY_EMA_SLOW             = cfg["s5_daily_ema_slow"]
        S5_HTF_BOS_LOOKBACK           = cfg["s5_htf_bos_lookback"]
        S5_OB_LOOKBACK                = cfg["s5_ob_lookback"]
        S5_OB_MIN_IMPULSE             = cfg["s5_ob_min_impulse"]
        S5_OB_MIN_RANGE_PCT           = cfg["s5_ob_min_range_pct"]
        S5_CHOCH_LOOKBACK             = cfg["s5_choch_lookback"]
        S5_MAX_ENTRY_BUFFER           = cfg["s5_max_entry_buffer"]
        S5_SL_BUFFER_PCT              = cfg["s5_sl_buffer_pct"]
        S5_MIN_RR                     = cfg["s5_min_rr"]
        S5_SWING_LOOKBACK             = cfg["s5_swing_lookback"]
        S5_SMC_FVG_FILTER             = cfg["s5_smc_fvg_filter"]
        S5_SMC_FVG_LOOKBACK           = cfg["s5_smc_fvg_lookback"]
    else:
        from config_s5 import (          # noqa: PLC0415
            S5_ENABLED,
            S5_DAILY_EMA_FAST, S5_DAILY_EMA_MED, S5_DAILY_EMA_SLOW,
            S5_HTF_BOS_LOOKBACK,
            S5_OB_LOOKBACK, S5_OB_MIN_IMPULSE, S5_CHOCH_LOOKBACK,
            S5_MAX_ENTRY_BUFFER, S5_SL_BUFFER_PCT,
            S5_MIN_RR, S5_SWING_LOOKBACK,
            S5_OB_MIN_RANGE_PCT, S5_SMC_FVG_FILTER, S5_SMC_FVG_LOOKBACK,
        )

    if not S5_ENABLED:
        return "HOLD", 0.0, 0.0, 0.0, 0.0, 0.0, "S5 disabled"

    go_long  = allowed_direction == "BULLISH"
    go_short = allowed_direction == "BEARISH"

    rsi_period = 14
    if len(daily_df) < rsi_period + 50 or len(htf_df) < S5_HTF_BOS_LOOKBACK + 2:
        return "HOLD", 0.0, 0.0, 0.0, 0.0, 0.0, "Not enough candles"
    if m15_df is None or len(m15_df) < S5_OB_LOOKBACK + S5_CHOCH_LOOKBACK + 10:
        return "HOLD", 0.0, 0.0, 0.0, 0.0, 0.0, "Not enough 15m candles"

    # ── 1. Daily EMA bias ─────────────────────────────────── #
    daily_closes = daily_df["close"].astype(float)
    ema_fast = float(calculate_ema(daily_closes, S5_DAILY_EMA_FAST).iloc[-1])
    ema_med  = float(calculate_ema(daily_closes, S5_DAILY_EMA_MED).iloc[-1])
    ema_slow = float(calculate_ema(daily_closes, S5_DAILY_EMA_SLOW).iloc[-1])

    ema_bull = ema_fast > ema_med > ema_slow
    ema_bear = ema_slow > ema_med > ema_fast

    if go_long and not ema_bull:
        return "HOLD", 0.0, 0.0, 0.0, 0.0, 0.0, (
            f"Daily EMA not bullish (EMA{S5_DAILY_EMA_FAST}<EMA{S5_DAILY_EMA_SLOW})"
        )
    if go_short and not ema_bear:
        return "HOLD", 0.0, 0.0, 0.0, 0.0, 0.0, (
            f"Daily EMA not bearish (EMA{S5_DAILY_EMA_FAST}>EMA{S5_DAILY_EMA_SLOW})"
        )

    # ── 2. 1H Break of Structure ───────────────────────────── #
    htf_win = htf_df.iloc[-(S5_HTF_BOS_LOOKBACK + 2):-1].reset_index(drop=True)
    n_htf   = len(htf_win)
    bos_high = None
    bos_low  = None

    for k in range(n_htf - 2, 0, -1):
        if bos_high is None:
            h = float(htf_win.iloc[k]["high"])
            if (h > float(htf_win.iloc[k - 1]["high"]) and
                    h > float(htf_win.iloc[k + 1]["high"])):
                post_closes = htf_win.iloc[k + 1:]["close"].astype(float)
                if any(c > h for c in post_closes):
                    bos_high = h
        if bos_low is None:
            lo = float(htf_win.iloc[k]["low"])
            if (lo < float(htf_win.iloc[k - 1]["low"]) and
                    lo < float(htf_win.iloc[k + 1]["low"])):
                post_closes = htf_win.iloc[k + 1:]["close"].astype(float)
                if any(c < lo for c in post_closes):
                    bos_low = lo
        if bos_high is not None and bos_low is not None:
            break

    if go_long and bos_high is None:
        return "HOLD", 0.0, 0.0, 0.0, 0.0, 0.0, (
            f"Daily EMA bullish ✅ | 1H BOS not confirmed "
            f"(no swing high with subsequent close-above in last {S5_HTF_BOS_LOOKBACK} bars)"
        )
    if go_short and bos_low is None:
        return "HOLD", 0.0, 0.0, 0.0, 0.0, 0.0, (
            f"Daily EMA bearish ✅ | 1H BOS not confirmed "
            f"(no swing low with subsequent close-below in last {S5_HTF_BOS_LOOKBACK} bars)"
        )

    # ── 3 + 4 + 5. OB → Pullback touch → ChoCH ───────────── #
    if go_long:
        ob = find_bullish_ob(m15_df, lookback=S5_OB_LOOKBACK, min_impulse_pct=S5_OB_MIN_IMPULSE)
        if ob is None:
            return "HOLD", 0.0, 0.0, 0.0, 0.0, 0.0, (
                f"Daily EMA ✅ | 1H BOS ✅ | No 15m Bullish OB found (lookback={S5_OB_LOOKBACK})"
            )
        ob_low, ob_high = ob

        ob_range = (ob_high - ob_low) / ob_low if ob_low > 0 else 0
        if ob_range < S5_OB_MIN_RANGE_PCT:
            return "HOLD", 0.0, 0.0, 0.0, ob_low, ob_high, (
                f"Daily EMA ✅ | 1H BOS ✅ | Bullish OB too narrow "
                f"({ob_range*100:.2f}% < {S5_OB_MIN_RANGE_PCT*100:.1f}%)"
            )

        if S5_SMC_FVG_FILTER:
            fvg = find_fvg(m15_df, direction="BULL", lookback=S5_SMC_FVG_LOOKBACK)
            if fvg is None or fvg[0] < ob_low:
                return "HOLD", 0.0, 0.0, 0.0, ob_low, ob_high, (
                    f"Daily EMA ✅ | 1H BOS ✅ | Bullish OB ✅ | No BULL FVG above OB — skipping"
                )

        recent = m15_df.iloc[-S5_CHOCH_LOOKBACK:]
        ob_touched = any(float(r["low"]) <= ob_high * 1.002 for _, r in recent.iterrows())
        if not ob_touched:
            return "HOLD", 0.0, 0.0, 0.0, ob_low, ob_high, (
                f"Daily EMA ✅ | 1H BOS ✅ | Bullish OB {ob_low:.5f}–{ob_high:.5f} | "
                f"Waiting pullback touch"
            )

        entry_trigger = ob_high
        sl_price      = ob_low * (1 - S5_SL_BUFFER_PCT)
        current_close = float(m15_df["close"].iloc[-1])

        if current_close > ob_high * (1 + S5_MAX_ENTRY_BUFFER):
            return "HOLD", entry_trigger, sl_price, 0.0, ob_low, ob_high, (
                f"Daily EMA ✅ | 1H BOS ✅ | Bullish OB ✅ | OB touched ✅ | "
                f"Stale — price {current_close:.5f} already >{S5_MAX_ENTRY_BUFFER*100:.2g}% above ob_high {ob_high:.5f}"
            )

        tp_price = find_swing_high_target(m15_df, entry_trigger, lookback=S5_SWING_LOOKBACK)
        if tp_price is None:
            return "HOLD", entry_trigger, sl_price, 0.0, ob_low, ob_high, (
                f"S5 OB ✅ but no swing high found above {entry_trigger:.5f} — skip"
            )

        risk   = entry_trigger - sl_price
        reward = tp_price - entry_trigger
        rr     = reward / risk if risk > 0 else 0
        if rr < S5_MIN_RR:
            return "HOLD", entry_trigger, sl_price, tp_price, ob_low, ob_high, (
                f"S5 OB ✅ but R:R={rr:.1f} < {S5_MIN_RR} (TP={tp_price:.5f}) — skip"
            )

        logger.info(
            f"[S5][{symbol}] 🕐 PENDING_LONG | Bullish OB {ob_low:.5f}–{ob_high:.5f} | "
            f"limit@{entry_trigger:.5f} | TP={tp_price:.5f} | R:R={rr:.1f}"
        )
        return "PENDING_LONG", entry_trigger, sl_price, tp_price, ob_low, ob_high, (
            f"S5 OB {ob_low:.5f}–{ob_high:.5f} | Limit@{entry_trigger:.5f} | TP={tp_price:.5f} R:R={rr:.1f}"
        )

    elif go_short:
        ob = find_bearish_ob(m15_df, lookback=S5_OB_LOOKBACK, min_impulse_pct=S5_OB_MIN_IMPULSE)
        if ob is None:
            return "HOLD", 0.0, 0.0, 0.0, 0.0, 0.0, (
                f"Daily EMA ✅ | 1H BOS ✅ | No 15m Bearish OB found (lookback={S5_OB_LOOKBACK})"
            )
        ob_low, ob_high = ob

        ob_range = (ob_high - ob_low) / ob_low if ob_low > 0 else 0
        if ob_range < S5_OB_MIN_RANGE_PCT:
            return "HOLD", 0.0, 0.0, 0.0, ob_low, ob_high, (
                f"Daily EMA ✅ | 1H BOS ✅ | Bearish OB too narrow "
                f"({ob_range*100:.2f}% < {S5_OB_MIN_RANGE_PCT*100:.1f}%)"
            )

        if S5_SMC_FVG_FILTER:
            fvg = find_fvg(m15_df, direction="BEAR", lookback=S5_SMC_FVG_LOOKBACK)
            if fvg is None or fvg[1] > ob_high:
                return "HOLD", 0.0, 0.0, 0.0, ob_low, ob_high, (
                    f"Daily EMA ✅ | 1H BOS ✅ | Bearish OB ✅ | No BEAR FVG below OB — skipping"
                )

        recent = m15_df.iloc[-S5_CHOCH_LOOKBACK:]
        ob_touched = any(float(r["high"]) >= ob_low * 0.998 for _, r in recent.iterrows())
        if not ob_touched:
            return "HOLD", 0.0, 0.0, 0.0, ob_low, ob_high, (
                f"Daily EMA ✅ | 1H BOS ✅ | Bearish OB {ob_low:.5f}–{ob_high:.5f} | "
                f"Waiting pullback touch"
            )

        entry_trigger = ob_low
        sl_price      = ob_high * (1 + S5_SL_BUFFER_PCT)
        current_close = float(m15_df["close"].iloc[-1])

        if current_close < ob_low * (1 - S5_MAX_ENTRY_BUFFER):
            return "HOLD", entry_trigger, sl_price, 0.0, ob_low, ob_high, (
                f"Daily EMA ✅ | 1H BOS ✅ | Bearish OB ✅ | OB touched ✅ | "
                f"Stale — price {current_close:.5f} already >{S5_MAX_ENTRY_BUFFER*100:.2g}% below ob_low {ob_low:.5f}"
            )

        tp_price = find_swing_low_target(m15_df, entry_trigger, lookback=S5_SWING_LOOKBACK)
        if tp_price is None:
            return "HOLD", entry_trigger, sl_price, 0.0, ob_low, ob_high, (
                f"S5 OB ✅ but no swing low found below {entry_trigger:.5f} — skip"
            )

        risk   = sl_price - entry_trigger
        reward = entry_trigger - tp_price
        rr     = reward / risk if risk > 0 else 0
        if rr < S5_MIN_RR:
            return "HOLD", entry_trigger, sl_price, tp_price, ob_low, ob_high, (
                f"S5 OB ✅ but R:R={rr:.1f} < {S5_MIN_RR} (TP={tp_price:.5f}) — skip"
            )

        logger.info(
            f"[S5][{symbol}] 🕐 PENDING_SHORT | Bearish OB {ob_low:.5f}–{ob_high:.5f} | "
            f"limit@{entry_trigger:.5f} | TP={tp_price:.5f} | R:R={rr:.1f}"
        )
        return "PENDING_SHORT", entry_trigger, sl_price, tp_price, ob_low, ob_high, (
            f"S5 OB {ob_low:.5f}–{ob_high:.5f} | Limit@{entry_trigger:.5f} | TP={tp_price:.5f} R:R={rr:.1f}"
        )

    return "HOLD", 0.0, 0.0, 0.0, 0.0, 0.0, "Direction not BULLISH or BEARISH — S5 skipped"


# ── S5 Exit Placement ─────────────────────────────────────── #

def _place_exits(symbol: str, hold_side: str, qty_str: str,
                 sl_trig: float, sl_exec: float,
                 partial_trig: float, tp_target: float,
                 trail_range_pct: float) -> bool:
    """
    S5 SMC exits (3 legs):
      1. SL (loss_plan) — full position at OB outer edge
      2. Partial TP (profit_plan, 50%) — at 1:1 R:R level
      3. Hard TP (profit_plan, 50%) — at structural swing target
         Falls back to trailing stop if tp_target == 0
    """
    import time as _t
    import trader
    import bitget as bg

    half_qty = trader._round_qty(float(qty_str) / 2, symbol)
    rest_qty = trader._round_qty(float(qty_str) - float(half_qty), symbol)

    for attempt in range(3):
        try:
            bg.place_pos_sl_only(symbol, hold_side, sl_trig, sl_exec)
            _t.sleep(0.5)
            bg.place_profit_plan(symbol, hold_side, half_qty, partial_trig)
            _t.sleep(0.5)
            if tp_target > 0:
                bg.place_profit_plan(symbol, hold_side, rest_qty, tp_target)
            else:
                bg.place_moving_plan(symbol, hold_side, rest_qty, partial_trig,
                                     str(round(trail_range_pct / 100, 4)))
            return True
        except Exception as e:
            logger.warning(f"[{symbol}] S5 exits attempt {attempt+1}/3: {e}")
            if attempt < 2:
                _t.sleep(1.5)
    return False


def compute_and_place_long_exits(symbol: str, qty_str: str, fill: float,
                                 sl_floor: float, tp_price_abs: float) -> tuple[bool, float, float]:
    """
    Compute S5 long-side levels and place exits.
    Returns (ok, sl_trig, tp_display) where tp_display = tp_target or 1R partial.
    """
    import trader
    from config_s5 import S5_TRAIL_RANGE_PCT

    sl_trig   = float(trader._round_price(sl_floor, symbol))
    sl_exec   = float(trader._round_price(sl_trig * 0.995, symbol))
    one_r     = fill - sl_trig
    part_trig = float(trader._round_price(fill + one_r, symbol))
    tp_targ   = float(trader._round_price(tp_price_abs, symbol)) if tp_price_abs > fill else 0.0
    ok = _place_exits(symbol, "long", qty_str, sl_trig, sl_exec, part_trig, tp_targ, S5_TRAIL_RANGE_PCT)
    tp_display = tp_targ if tp_targ > 0 else part_trig
    return ok, sl_trig, tp_display


def compute_and_place_short_exits(symbol: str, qty_str: str, fill: float,
                                  sl_trig: float, sl_exec: float,
                                  tp_price_abs: float) -> tuple[bool, float, float]:
    """
    Compute S5 short-side levels and place exits.
    Returns (ok, sl_trig, tp_display).
    """
    import trader
    from config_s5 import S5_TRAIL_RANGE_PCT

    one_r     = sl_trig - fill
    part_trig = float(trader._round_price(fill - one_r, symbol))
    tp_targ   = float(trader._round_price(tp_price_abs, symbol)) if 0 < tp_price_abs < fill else 0.0
    ok = _place_exits(symbol, "short", qty_str, sl_trig, sl_exec, part_trig, tp_targ, S5_TRAIL_RANGE_PCT)
    tp_display = tp_targ if tp_targ > 0 else part_trig
    return ok, sl_trig, tp_display


def place_exits_from_signal(symbol: str, side: str, qty_str: str, fill: float,
                            sl_price: float, tp_price: float) -> tuple[bool, float, float, float]:
    """
    Place S5 exits when opening a position from a filled plan order (signal watcher).
    Returns (ok, sl_trig, part_trig, tp_targ).
    """
    import trader
    from config_s5 import S5_TRAIL_RANGE_PCT

    if side == "LONG":
        sl_trig   = float(trader._round_price(sl_price, symbol))
        sl_exec   = float(trader._round_price(sl_trig * 0.995, symbol))
        one_r     = fill - sl_trig
        part_trig = float(trader._round_price(fill + one_r, symbol))
        tp_targ   = float(trader._round_price(tp_price, symbol)) if tp_price > fill else 0.0
        hold_side = "long"
    else:
        sl_trig   = float(trader._round_price(sl_price, symbol))
        sl_exec   = float(trader._round_price(sl_trig * 1.005, symbol))
        one_r     = sl_trig - fill
        part_trig = float(trader._round_price(fill - one_r, symbol))
        tp_targ   = float(trader._round_price(tp_price, symbol)) if 0 < tp_price < fill else 0.0
        hold_side = "short"
    ok = _place_exits(symbol, hold_side, qty_str, sl_trig, sl_exec, part_trig, tp_targ, S5_TRAIL_RANGE_PCT)
    return ok, sl_trig, part_trig, tp_targ


# ── S5 Swing Trail ────────────────────────────────────────── #

def maybe_trail_sl(symbol: str, ap: dict, tr_mod, st_mod) -> None:
    """Structural swing trail for S5 (SMC): LONG pulls SL up to swing-low, SHORT mirror."""
    import config_s5
    from tools import (
        find_swing_high_target, find_swing_low_target,
        find_swing_low_after_ref, find_swing_high_after_ref,
    )

    if not config_s5.S5_USE_SWING_TRAIL:
        return
    try:
        lb    = config_s5.S5_SWING_LOOKBACK
        cs_df = tr_mod.get_candles(symbol, config_s5.S5_LTF_INTERVAL, limit=lb + 5)
        mark  = tr_mod.get_mark_price(symbol)
        if cs_df.empty or len(cs_df) < 3:
            return
        if ap["side"] == "LONG":
            ref = ap.get("swing_trail_ref")
            if ref is None:
                ap["swing_trail_ref"] = find_swing_high_target(cs_df, mark, lookback=lb)
            elif mark >= ref:
                raw = find_swing_low_after_ref(cs_df, mark, ref, lookback=lb)
                if raw:
                    swing_sl = raw * (1 - config_s5.S5_SL_BUFFER_PCT)
                    if swing_sl > ap.get("sl", 0) and tr_mod.update_position_sl(symbol, swing_sl, hold_side="long"):
                        ap["sl"] = swing_sl
                        st_mod.update_open_trade_sl(symbol, swing_sl)
                        ap["swing_trail_ref"] = find_swing_high_target(cs_df, mark, lookback=lb)
                        logger.info(f"[S5][{symbol}] 📍 Swing trail: SL → {swing_sl:.5f} (swing low after ref high {ref:.5f})")
        else:
            ref = ap.get("swing_trail_ref")
            if ref is None:
                ap["swing_trail_ref"] = find_swing_low_target(cs_df, mark, lookback=lb)
            elif mark <= ref:
                raw = find_swing_high_after_ref(cs_df, mark, ref, lookback=lb)
                if raw:
                    swing_sl = raw * (1 + config_s5.S5_SL_BUFFER_PCT)
                    if swing_sl < ap.get("sl", float("inf")) and tr_mod.update_position_sl(symbol, swing_sl, hold_side="short"):
                        ap["sl"] = swing_sl
                        st_mod.update_open_trade_sl(symbol, swing_sl)
                        ap["swing_trail_ref"] = find_swing_low_target(cs_df, mark, lookback=lb)
                        logger.info(f"[S5][{symbol}] 📍 Swing trail: SL → {swing_sl:.5f} (swing high after ref low {ref:.5f})")
    except Exception as e:
        logger.error(f"S5 swing trail error [{symbol}]: {e}")
