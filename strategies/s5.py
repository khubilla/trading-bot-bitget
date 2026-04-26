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

from config_s5 import S5_LTF_INTERVAL
from indicators import calculate_ema
from tools import (
    find_bearish_ob,
    find_bullish_ob,
    find_fvg,
    find_swing_high_target,
    find_swing_low_target,
)

# Default candle interval for S5 event snapshots.
SNAPSHOT_INTERVAL = S5_LTF_INTERVAL  # "15m"

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
    S5 SMC exits (2 TP legs only - SL already attached via preset in market order):
      1. Partial TP (profit_plan, 50%) — at 1:1 R:R level
      2. Hard TP (profit_plan, 50%) — at structural swing target
         Falls back to trailing stop if tp_target == 0

    Note: SL is NOT placed here - it's already attached to the market entry order
    via presetStopLossPrice, so position is protected from the moment it opens.
    """
    import time as _t
    import trader
    import bitget as bg

    half_qty = trader._round_qty(float(qty_str) / 2, symbol)
    rest_qty = trader._round_qty(float(qty_str) - float(half_qty), symbol)

    for attempt in range(3):
        try:
            # SL is already attached via preset - SKIP line that was: bg.place_pos_sl_only(...)
            logger.info(f"[{symbol}] S5 exits: SL already set via preset (trigger={sl_trig:.5f} exec={sl_exec:.5f}), placing TPs only")

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


# ── S5 DNA Snapshot Fields ────────────────────────────────── #

def dna_fields(candles: dict) -> dict:
    """S5 trade fingerprint: daily/H1/m15 EMA slope and price vs EMA."""
    from indicators import calculate_ema
    from trade_dna import ema_slope, price_vs_ema, _is_empty, _closes_from

    out = {}
    daily = candles.get("daily")
    h1    = candles.get("h1")
    m15   = candles.get("m15")
    if not _is_empty(daily):
        closes_d = _closes_from(daily)
        ema_d    = calculate_ema(closes_d, 20)
        out["snap_trend_daily_ema_slope"]    = ema_slope(closes_d, 20)
        out["snap_trend_daily_price_vs_ema"] = price_vs_ema(float(closes_d.iloc[-1]), float(ema_d.iloc[-1]))
    if not _is_empty(h1):
        closes_h = _closes_from(h1)
        ema_h    = calculate_ema(closes_h, 20)
        out["snap_trend_h1_ema_slope"]    = ema_slope(closes_h, 20)
        out["snap_trend_h1_price_vs_ema"] = price_vs_ema(float(closes_h.iloc[-1]), float(ema_h.iloc[-1]))
    if not _is_empty(m15):
        closes_m15 = _closes_from(m15)
        ema_m15    = calculate_ema(closes_m15, 20)
        out["snap_trend_m15_ema_slope"]    = ema_slope(closes_m15, 20)
        out["snap_trend_m15_price_vs_ema"] = price_vs_ema(float(closes_m15.iloc[-1]), float(ema_m15.iloc[-1]))
    return out


# ── S5 OB Invalidation Cooldown ───────────────────────────── #
# After an OB gets invalidated, suppress new PENDING queues for one full 15m candle
# so a stale evaluator result doesn't re-queue the same setup moments later.

_OB_INV_COOLDOWN_SEC = 15 * 60


def _ob_inv_map(bot) -> dict:
    """Lazily-init per-bot OB-invalidation timestamp map."""
    if not hasattr(bot, "_s5_ob_invalidated_at"):
        bot._s5_ob_invalidated_at = {}
    return bot._s5_ob_invalidated_at


def is_ob_cooldown_active(bot, symbol: str) -> bool:
    """True while within the 15m window after the last OB invalidation for `symbol`."""
    import time as _t
    ts = _ob_inv_map(bot).get(symbol, 0)
    return (_t.time() - ts) <= _OB_INV_COOLDOWN_SEC


def mark_ob_invalidated(bot, symbol: str) -> None:
    """Stamp `symbol` as just-invalidated; starts the OB cooldown window."""
    import time as _t
    _ob_inv_map(bot)[symbol] = _t.time()


# ── S5 Pending-Signal Queue ───────────────────────────────── #

def queue_pending(bot, symbol: str, sig: str, trigger: float, sl: float,
                  tp: float, ob_low: float, ob_high: float, m15_df,
                  priority_rank: int = 999, priority_score: float = 0.0,
                  paper_mode: bool | None = None) -> None:
    """Pre-validate an S5 PENDING signal (Claude filter + pre-flight guards) and place limit order."""
    import time as _t
    import state as st
    import config
    import config_s5
    import trader as tr
    from claude_filter import claude_approve

    side = "LONG" if sig == "PENDING_LONG" else "SHORT"
    if config.CLAUDE_FILTER_ENABLED:
        _cd = claude_approve("S5", symbol, {
            "OB zone":            f"{ob_low:.5f}–{ob_high:.5f}",
            "Sentiment":           bot.sentiment.direction if bot.sentiment else "?",
            "Entry trigger":       round(trigger, 5),
            "SL":                  round(sl, 5),
        })
        if not _cd["approved"]:
            logger.info(f"[S5][{symbol}] 🤖 PENDING rejected: {_cd['reason']}")
            st.add_scan_log(f"[S5][{symbol}] 🤖 PENDING rejected: {_cd['reason']}", "WARN")
            return
    rr = round((tp - trigger) / (trigger - sl), 2) if side == "LONG" and tp > trigger > sl > 0 \
         else round((trigger - tp) / (sl - trigger), 2) if 0 < tp < trigger < sl else None
    bot.pending_signals[symbol] = {
        "strategy": "S5", "side": side,
        "trigger": trigger, "sl": sl, "tp": tp,
        "ob_low": ob_low, "ob_high": ob_high,
        "rr": rr, "sr_clearance_pct": None,
        "sentiment": bot.sentiment.direction if bot.sentiment else "?",
        "expires": _t.time() + 4 * 3600,
        "priority_rank": priority_rank,
        "priority_score": priority_score,
        "order_id": None,
        "qty_str": None,
    }
    # Caller (bot.py) passes its module-level PAPER_MODE. Fall back to looking it
    # up on the bot module if not provided (e.g. external callers).
    if paper_mode is None:
        import bot as _bot
        paper_mode = getattr(_bot, "PAPER_MODE", False)
    if not paper_mode:
        try:
            balance  = tr.get_usdt_balance()
            equity   = tr._get_total_equity() or balance
            notional = equity * config_s5.S5_TRADE_SIZE_PCT * config_s5.S5_LEVERAGE
            mark     = tr.get_mark_price(symbol)
            if side == "LONG" and mark < ob_low * (1 - config_s5.S5_OB_INVALIDATION_BUFFER_PCT):
                logger.warning(
                    f"[S5][{symbol}] ⚠️ PENDING LONG skipped: mark {mark:.5f} already < "
                    f"ob_low {ob_low:.5f} — OB invalidated"
                )
                bot.pending_signals.pop(symbol, None)
                return
            elif side == "SHORT" and mark > ob_high * (1 + config_s5.S5_MAX_ENTRY_BUFFER):
                logger.warning(
                    f"[S5][{symbol}] ⚠️ PENDING SHORT skipped: mark {mark:.5f} already > "
                    f"ob_high {ob_high:.5f} — OB invalidated"
                )
                bot.pending_signals.pop(symbol, None)
                return
            if side == "LONG" and mark > trigger:
                logger.warning(
                    f"[S5][{symbol}] ⚠️ PENDING LONG skipped: mark {mark:.5f} already > "
                    f"trigger {trigger:.5f} — price already above OB entry"
                )
                bot.pending_signals.pop(symbol, None)
                return
            elif side == "SHORT" and mark < trigger:
                logger.warning(
                    f"[S5][{symbol}] ⚠️ PENDING SHORT skipped: mark {mark:.5f} already < "
                    f"trigger {trigger:.5f} — price already below OB entry"
                )
                bot.pending_signals.pop(symbol, None)
                return
            max_trigger_distance = 0.10
            if side == "LONG" and trigger > mark * (1 + max_trigger_distance):
                logger.warning(
                    f"[S5][{symbol}] ⚠️ PENDING LONG skipped: trigger {trigger:.5f} is "
                    f">{max_trigger_distance * 100:.0f}% above live mark {mark:.5f} "
                    f"(Bitget exchange limit)"
                )
                bot.pending_signals.pop(symbol, None)
                return
            elif side == "SHORT" and trigger < mark * (1 - max_trigger_distance):
                logger.warning(
                    f"[S5][{symbol}] ⚠️ PENDING SHORT skipped: trigger {trigger:.5f} is "
                    f">{max_trigger_distance * 100:.0f}% below live mark {mark:.5f} "
                    f"(Bitget exchange limit)"
                )
                bot.pending_signals.pop(symbol, None)
                return
            qty_str  = tr._round_qty(notional / mark, symbol)
            if side == "LONG":
                order_id = tr.place_limit_long(symbol, trigger, sl, tp, qty_str)
            else:
                order_id = tr.place_limit_short(symbol, trigger, sl, tp, qty_str)
            bot.pending_signals[symbol]["order_id"] = order_id
            bot.pending_signals[symbol]["qty_str"]  = qty_str
            logger.info(
                f"[S5][{symbol}] 📋 Limit {side} placed @ {trigger:.5f} | "
                f"order_id={order_id} | SL={sl:.5f} | TP={tp:.5f}"
            )
        except Exception as e:
            logger.error(f"[S5][{symbol}] ❌ Failed to place limit order: {e}")
            bot.pending_signals.pop(symbol, None)
            return
    else:
        try:
            mark = tr.get_mark_price(symbol)
            if side == "LONG" and mark < ob_low * (1 - config_s5.S5_OB_INVALIDATION_BUFFER_PCT):
                logger.warning(
                    f"[S5][{symbol}] ⚠️ PENDING LONG skipped (paper): mark {mark:.5f} < ob_low {ob_low:.5f} — OB invalidated"
                )
                bot.pending_signals.pop(symbol, None)
                return
            elif side == "SHORT" and mark > ob_high * (1 + config_s5.S5_MAX_ENTRY_BUFFER):
                logger.warning(
                    f"[S5][{symbol}] ⚠️ PENDING SHORT skipped (paper): mark {mark:.5f} > ob_high {ob_high:.5f} — OB invalidated"
                )
                bot.pending_signals.pop(symbol, None)
                return
            if side == "LONG" and mark > trigger:
                logger.warning(
                    f"[S5][{symbol}] ⚠️ PENDING LONG skipped (paper): mark {mark:.5f} already > trigger {trigger:.5f}"
                )
                bot.pending_signals.pop(symbol, None)
                return
            elif side == "SHORT" and mark < trigger:
                logger.warning(
                    f"[S5][{symbol}] ⚠️ PENDING SHORT skipped (paper): mark {mark:.5f} already < trigger {trigger:.5f}"
                )
                bot.pending_signals.pop(symbol, None)
                return
            max_trigger_distance = 0.10
            if side == "LONG" and trigger > mark * (1 + max_trigger_distance):
                logger.warning(
                    f"[S5][{symbol}] ⚠️ PENDING LONG skipped (paper): trigger {trigger:.5f} > 10% above mark {mark:.5f}"
                )
                bot.pending_signals.pop(symbol, None)
                return
            elif side == "SHORT" and trigger < mark * (1 - max_trigger_distance):
                logger.warning(
                    f"[S5][{symbol}] ⚠️ PENDING SHORT skipped (paper): trigger {trigger:.5f} > 10% below mark {mark:.5f}"
                )
                bot.pending_signals.pop(symbol, None)
                return
        except Exception:
            pass
        bot.pending_signals[symbol]["order_id"] = "PAPER"
    st.save_pending_signals(bot.pending_signals)
    logger.info(
        f"[S5][{symbol}] 🕐 PENDING {side} queued | "
        f"trigger={trigger:.5f} | SL={sl:.5f} | TP={tp:.5f} | R:R={rr}"
    )
    st.add_scan_log(
        f"[S5][{symbol}] 🕐 PENDING {side} | trigger={trigger:.5f} | TP={tp:.5f}", "SIGNAL"
    )


# ── S5 Entry Watcher (pending tick) ───────────────────────── #

def handle_pending_tick(bot, symbol: str, sig: dict, balance: float,
                        paper_mode: bool | None = None) -> str | None:
    """S5 order-fill polling + OB invalidation + expiry. Return 'break' unused (always None)."""
    import time as _t
    import state as st
    import trader as tr
    import config_s5

    # Cancel if scanner no longer sees a PENDING signal (e.g. BOS failed)
    ps = st.get_pair_state(symbol)
    if ps.get("s5_signal", "HOLD") != "PENDING":
        order_id = sig.get("order_id")
        fill_info = None
        try:
            fill_info = tr.get_order_fill(symbol, order_id)
        except Exception as e:
            logger.warning(f"[S5][{symbol}] fill-check error: {e}")
        if fill_info and fill_info["status"] == "filled":
            logger.info(f"[S5][{symbol}] Order already filled despite signal gone — registering trade")
            with bot._trade_lock:
                if symbol not in bot.active_positions and not st.is_pair_paused(symbol):
                    bot._handle_limit_filled(symbol, sig, fill_info["fill_price"], balance)
            bot.pending_signals.pop(symbol, None)
            st.save_pending_signals(bot.pending_signals)
            return None
        try:
            tr.cancel_order(symbol, order_id)
        except Exception as e:
            logger.warning(f"[S5][{symbol}] cancel_order error: {e}")
        logger.info(f"[S5][{symbol}] 🚫 Signal gone — limit cancelled")
        st.add_scan_log(f"[S5][{symbol}] 🚫 Signal gone — limit cancelled", "INFO")
        bot.pending_signals.pop(symbol, None)
        return None

    order_id = sig.get("order_id")
    try:
        mark = tr.get_mark_price(symbol)
    except Exception:
        return None
    side = sig["side"]

    if paper_mode is None:
        import bot as _bot
        paper_mode = getattr(_bot, "PAPER_MODE", False)

    fill_info = None
    if paper_mode and order_id == "PAPER":
        paper_triggered = (
            (side == "LONG"  and mark >= sig["trigger"]) or
            (side == "SHORT" and mark <= sig["trigger"])
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
            return None

    if fill_info["status"] == "filled":
        with bot._trade_lock:
            if symbol in bot.active_positions:
                bot.pending_signals.pop(symbol, None)
                return None
            if st.is_pair_paused(symbol):
                return None
            bot._handle_limit_filled(symbol, sig, fill_info["fill_price"], balance)
        bot.pending_signals.pop(symbol, None)
        st.save_pending_signals(bot.pending_signals)

    # ── OB Invalidation: 15m candle close (not instant mark price) ──── #
    # Check if last COMPLETED 15m candle closed through the OB boundary.
    # This filters out wicks/noise while staying responsive (max 15 min delay).
    else:
        try:
            m15_df = tr.get_candles(symbol, config_s5.S5_LTF_INTERVAL, limit=3)
            if len(m15_df) >= 2:
                last_closed_candle = m15_df.iloc[-2]  # Last completed 15m candle
                last_close = float(last_closed_candle["close"])

                invalidated = False
                if side == "LONG" and last_close < sig["ob_low"] * (1 - config_s5.S5_OB_INVALIDATION_BUFFER_PCT):
                    invalidated = True
                    logger.info(
                        f"[S5][{symbol}] ❌ Limit cancelled — OB invalidated "
                        f"(15m close {last_close:.5f} < ob_low {sig['ob_low']:.5f})"
                    )
                elif side == "SHORT" and last_close > sig["ob_high"] * (1 + config_s5.S5_OB_INVALIDATION_BUFFER_PCT):
                    invalidated = True
                    logger.info(
                        f"[S5][{symbol}] ❌ Limit cancelled — OB invalidated "
                        f"(15m close {last_close:.5f} > ob_high {sig['ob_high']:.5f})"
                    )

                if invalidated:
                    try:
                        tr.cancel_order(symbol, order_id)
                    except Exception as e:
                        logger.warning(f"[S5][{symbol}] cancel_order error: {e}")
                    st.add_scan_log(f"[S5][{symbol}] ❌ OB invalidated — limit cancelled", "INFO")
                    mark_ob_invalidated(bot, symbol)
                    bot.pending_signals.pop(symbol, None)
                    return None
        except Exception as e:
            logger.debug(f"[S5][{symbol}] OB invalidation check error: {e}")

    # ── Expiry Check ──── #
    if _t.time() > sig["expires"]:
        try:
            tr.cancel_order(symbol, order_id)
        except Exception as e:
            logger.warning(f"[S5][{symbol}] cancel_order error: {e}")
        logger.info(f"[S5][{symbol}] ⏰ Limit cancelled — expired")
        st.add_scan_log(f"[S5][{symbol}] ⏰ Limit expired — cancelled", "INFO")
        bot.pending_signals.pop(symbol, None)

    return None


# ── S5 Paper Trail Setup ──────────────────────────────────── #

def compute_paper_trail_long(mark: float, sl_price: float, tp_price_abs: float = 0,
                             take_profit_pct: float = 0.05) -> tuple[bool, float, float, float, bool]:
    """Paper-trader LONG trail setup for S5 (1:1 R:R, breakeven after partial)."""
    from config_s5 import S5_TRAIL_RANGE_PCT
    one_r         = mark - sl_price
    trail_trigger = mark + one_r
    trail_range   = S5_TRAIL_RANGE_PCT
    tp_price      = tp_price_abs if tp_price_abs > mark else trail_trigger
    return True, trail_trigger, trail_range, tp_price, True


def compute_paper_trail_short(mark: float, sl_price: float, tp_price_abs: float = 0,
                              take_profit_pct: float = 0.05) -> tuple[bool, float, float, float, bool]:
    """Paper-trader SHORT trail setup for S5 (1:1 R:R, breakeven after partial)."""
    from config_s5 import S5_TRAIL_RANGE_PCT
    one_r         = sl_price - mark
    trail_trigger = mark - one_r
    trail_range   = S5_TRAIL_RANGE_PCT
    tp_price      = tp_price_abs if 0 < tp_price_abs < mark else trail_trigger
    return True, trail_trigger, trail_range, tp_price, True
