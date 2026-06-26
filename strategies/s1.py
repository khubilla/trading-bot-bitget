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
    LTF_INTERVAL,
)

# Default candle interval for S1 event snapshots (open/partial/close/scale_in).
SNAPSHOT_INTERVAL = LTF_INTERVAL  # "3m"
from indicators import calculate_adx, calculate_ema, calculate_rsi
from tools import (
    check_htf,
    find_swing_high_target, find_swing_low_target,
    find_swing_low_after_ref, find_swing_high_after_ref,
)

logger = logging.getLogger(__name__)
Signal   = Literal["LONG", "SHORT", "HOLD", "PENDING_LONG", "PENDING_SHORT"]
ExitFlag = Literal["EXIT", "HOLD"]


# ── Daily Trend Filter (ADX-based) ────────────────────────── #

def check_daily_trend(daily_df: pd.DataFrame, direction: str, cfg: dict | None = None) -> tuple[bool, float, float]:
    """
    Replaces EMA filter. Uses ADX to confirm trending (not sideways).

    Rules:
      LONG:  ADX > ADX_TREND_THRESHOLD AND last daily close > EMA20 and RSI > DAILY_RSI_LONG_TRESH
      SHORT: ADX > ADX_TREND_THRESHOLD AND last daily close < EMA20 and RSI < DAILY_RSI_SHORT_TRESH

    Returns (passes, adx_value, daily_rsi)
    cfg: optional per-instrument CONFIG dict (IG path). When None, reads from config_s1 module (Bitget path).
    """
    if cfg is not None:
        ADX_TREND_THRESHOLD    = cfg["s1_adx_trend_threshold"]
        DAILY_EMA_SLOW         = cfg["s1_daily_ema_slow"]
        DAILY_RSI_LONG_THRESH  = cfg["s1_daily_rsi_long_thresh"]
        DAILY_RSI_SHORT_THRESH = cfg["s1_daily_rsi_short_thresh"]
        RSI_PERIOD_LOCAL       = cfg["s1_rsi_period"]
    else:
        from config_s1 import ADX_TREND_THRESHOLD, DAILY_EMA_SLOW, DAILY_RSI_LONG_THRESH, DAILY_RSI_SHORT_THRESH
        RSI_PERIOD_LOCAL = RSI_PERIOD

    if len(daily_df) < 30:
        logger.debug("  Daily trend: not enough candles")
        return False, 0.0, 0.0

    closes  = daily_df["close"].astype(float)
    adx_res = calculate_adx(daily_df)
    adx_val = float(adx_res["adx"].iloc[-1])
    rsi_res = calculate_rsi(closes, RSI_PERIOD_LOCAL)
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

def check_ltf_long(ltf_df: pd.DataFrame, cfg: dict | None = None) -> tuple[bool, float, float, float]:
    if cfg is not None:
        RSI_PERIOD_LOCAL        = cfg["s1_rsi_period"]
        RSI_LONG_THRESH_LOCAL   = cfg["s1_rsi_long_thresh"]
        CONSOLIDATION_CANDLES_LOCAL   = cfg["s1_consolidation_candles"]
        CONSOLIDATION_RANGE_PCT_LOCAL = cfg["s1_consolidation_range_pct"]
        BREAKOUT_BUFFER_PCT_LOCAL     = cfg["s1_breakout_buffer_pct"]
    else:
        RSI_PERIOD_LOCAL        = RSI_PERIOD
        RSI_LONG_THRESH_LOCAL   = RSI_LONG_THRESH
        CONSOLIDATION_CANDLES_LOCAL   = CONSOLIDATION_CANDLES
        CONSOLIDATION_RANGE_PCT_LOCAL = CONSOLIDATION_RANGE_PCT
        BREAKOUT_BUFFER_PCT_LOCAL     = BREAKOUT_BUFFER_PCT

    if len(ltf_df) < RSI_PERIOD_LOCAL + CONSOLIDATION_CANDLES_LOCAL + 3:
        return False, 50.0, 0.0, 0.0

    closes  = ltf_df["close"].astype(float)
    rsi_ser = calculate_rsi(closes, RSI_PERIOD_LOCAL)
    rsi_val = float(rsi_ser.iloc[-1])

    if rsi_val <= RSI_LONG_THRESH_LOCAL:
        return False, rsi_val, 0.0, 0.0

    # Check consolidation on candles BEFORE the last closed candle.
    # This prevents the breakout candle from polluting the consolidation window.
    # With CONSOLIDATION_CANDLES=2: window = ltf_df.iloc[-4:-2] (positions -4, -3)
    consolidation_window = ltf_df.iloc[-(CONSOLIDATION_CANDLES_LOCAL + 2):-2]

    if len(consolidation_window) < CONSOLIDATION_CANDLES_LOCAL:
        return False, rsi_val, 0.0, 0.0

    box_high = float(consolidation_window["high"].max())
    box_low  = float(consolidation_window["low"].min())
    mid      = (box_high + box_low) / 2

    if mid == 0:
        return False, rsi_val, 0.0, 0.0

    range_pct = (box_high - box_low) / mid
    if range_pct > CONSOLIDATION_RANGE_PCT_LOCAL:
        logger.debug(f"  Consolidation ❌ range={range_pct*100:.3f}% > {CONSOLIDATION_RANGE_PCT_LOCAL*100}%")
        return False, rsi_val, box_high, box_low

    # Check RSI was in zone throughout the consolidation window
    window_rsi = rsi_ser.iloc[-(CONSOLIDATION_CANDLES_LOCAL + 2):-2]
    if not (window_rsi > RSI_LONG_THRESH_LOCAL).all():
        logger.debug(f"  Consolidation ❌ RSI not > {RSI_LONG_THRESH_LOCAL} throughout (min={window_rsi.min():.1f})")
        return False, rsi_val, box_high, box_low

    logger.debug(f"  Consolidation ✓ range={range_pct*100:.3f}% H={box_high} L={box_low}")

    # Check if last CLOSED candle broke out above the box
    last_closed = float(ltf_df["close"].iloc[-2])
    if last_closed > box_high * (1 + BREAKOUT_BUFFER_PCT_LOCAL):
        return True, rsi_val, box_high, box_low

    return False, rsi_val, box_high, box_low


def check_ltf_short(ltf_df: pd.DataFrame, cfg: dict | None = None) -> tuple[bool, float, float, float]:
    if cfg is not None:
        RSI_PERIOD_LOCAL        = cfg["s1_rsi_period"]
        RSI_SHORT_THRESH_LOCAL  = cfg["s1_rsi_short_thresh"]
        CONSOLIDATION_CANDLES_LOCAL   = cfg["s1_consolidation_candles"]
        CONSOLIDATION_RANGE_PCT_LOCAL = cfg["s1_consolidation_range_pct"]
        BREAKOUT_BUFFER_PCT_LOCAL     = cfg["s1_breakout_buffer_pct"]
    else:
        RSI_PERIOD_LOCAL        = RSI_PERIOD
        RSI_SHORT_THRESH_LOCAL  = RSI_SHORT_THRESH
        CONSOLIDATION_CANDLES_LOCAL   = CONSOLIDATION_CANDLES
        CONSOLIDATION_RANGE_PCT_LOCAL = CONSOLIDATION_RANGE_PCT
        BREAKOUT_BUFFER_PCT_LOCAL     = BREAKOUT_BUFFER_PCT

    if len(ltf_df) < RSI_PERIOD_LOCAL + CONSOLIDATION_CANDLES_LOCAL + 3:
        return False, 50.0, 0.0, 0.0

    closes  = ltf_df["close"].astype(float)
    rsi_ser = calculate_rsi(closes, RSI_PERIOD_LOCAL)
    rsi_val = float(rsi_ser.iloc[-1])

    if rsi_val >= RSI_SHORT_THRESH_LOCAL:
        return False, rsi_val, 0.0, 0.0

    # Check consolidation on candles BEFORE the last closed candle.
    # This prevents the breakout candle from polluting the consolidation window.
    # With CONSOLIDATION_CANDLES=2: window = ltf_df.iloc[-4:-2] (positions -4, -3)
    consolidation_window = ltf_df.iloc[-(CONSOLIDATION_CANDLES_LOCAL + 2):-2]

    if len(consolidation_window) < CONSOLIDATION_CANDLES_LOCAL:
        return False, rsi_val, 0.0, 0.0

    box_high = float(consolidation_window["high"].max())
    box_low  = float(consolidation_window["low"].min())
    mid      = (box_high + box_low) / 2

    if mid == 0:
        return False, rsi_val, 0.0, 0.0

    range_pct = (box_high - box_low) / mid
    if range_pct > CONSOLIDATION_RANGE_PCT_LOCAL:
        logger.debug(f"  Consolidation ❌ range={range_pct*100:.3f}% > {CONSOLIDATION_RANGE_PCT_LOCAL*100}%")
        return False, rsi_val, box_high, box_low

    # Check RSI was in zone throughout the consolidation window
    window_rsi = rsi_ser.iloc[-(CONSOLIDATION_CANDLES_LOCAL + 2):-2]
    if not (window_rsi < RSI_SHORT_THRESH_LOCAL).all():
        logger.debug(f"  Consolidation ❌ RSI not < {RSI_SHORT_THRESH_LOCAL} throughout (max={window_rsi.max():.1f})")
        return False, rsi_val, box_high, box_low

    logger.debug(f"  Consolidation ✓ range={range_pct*100:.3f}% H={box_high} L={box_low}")

    # Check if last CLOSED candle broke out below the box
    last_closed = float(ltf_df["close"].iloc[-2])
    if last_closed < box_low * (1 - BREAKOUT_BUFFER_PCT_LOCAL):
        return True, rsi_val, box_high, box_low

    return False, rsi_val, box_high, box_low


# ── Anchored-Box Breakout Decision ────────────────────────── #

def s1_anchor_decision(
    armed: dict | None, *, direction: str, last_close: float, last_ts: int,
    rsi_val: float, rsi_thresh: float, gates_ok: bool, is_coil: bool,
    box_high: float, box_low: float, buffer_pct: float,
    interval_ms: int, max_age: int,
) -> tuple[dict | None, str]:
    """Anchored-box breakout state machine for S1 (pure / stateless).

    Returns (new_armed_state, signal). new_armed_state is the box dict to keep
    (or None to clear). signal is "LONG"/"SHORT" on a fired breakout, else "HOLD".
    """
    in_zone = rsi_val > rsi_thresh if direction == "LONG" else rsi_val < rsi_thresh

    if armed is not None:
        # Disarm conditions (no trade)
        if not gates_ok or not in_zone:
            return None, "HOLD"
        if int((last_ts - armed["armed_at_ts"]) // interval_ms) > max_age:
            return None, "HOLD"
        if direction == "LONG" and last_close < armed["box_low"]:
            return None, "HOLD"
        if direction == "SHORT" and last_close > armed["box_high"]:
            return None, "HOLD"
        # Fire on close-confirmed breakout of the anchored box
        if direction == "LONG" and last_close > armed["box_high"] * (1 + buffer_pct):
            return None, "LONG"
        if direction == "SHORT" and last_close < armed["box_low"] * (1 - buffer_pct):
            return None, "SHORT"
        return armed, "HOLD"

    # Arm: valid coil, gates pass, RSI in zone, price not already broken out
    if not (gates_ok and in_zone and is_coil):
        return None, "HOLD"
    if direction == "LONG" and last_close > box_high * (1 + buffer_pct):
        return None, "HOLD"
    if direction == "SHORT" and last_close < box_low * (1 - buffer_pct):
        return None, "HOLD"
    return ({"dir": direction, "box_high": box_high, "box_low": box_low,
             "rsi_thresh": rsi_thresh, "armed_at_ts": last_ts}, "HOLD")


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
    cfg: dict | None = None,
) -> tuple[Signal, float, float, float, float, float]:
    """
    Returns (signal, rsi, box_high, box_low, adx, daily_rsi)
    allowed_direction: "BULLISH" | "BEARISH"
    cfg: optional per-instrument CONFIG dict (IG path). When None, reads from config_s1 module (Bitget path).
    """
    if cfg is not None:
        S1_ENABLED = cfg["s1_enabled"]
    else:
        from config_s1 import S1_ENABLED
    if not S1_ENABLED:
        return "HOLD", 50.0, 0.0, 0.0, 0.0, 0.0

    # IG path: compute daily ATR + S/R clearance setup for downstream exit math + gating
    atr_val = 0.0
    sr_clearance_long  = float("inf")
    sr_clearance_short = float("inf")
    if cfg is not None:
        from indicators import calculate_atr
        from tools import nearest_daily_sr_clearance
        atr_period = cfg.get("s1_atr_period", 14)
        atr_series = calculate_atr(daily_df, period=atr_period)
        atr_val = float(atr_series.iloc[-1]) if len(atr_series) else 0.0
        cfg["_last_atr"] = atr_val
        if atr_val <= 0:
            return "HOLD", 50.0, 0.0, 0.0, 0.0, 0.0
        sr_mult = cfg.get("s1_sr_clearance_atr_mult", 0.0)
        if sr_mult > 0:
            sr_clearance_long  = nearest_daily_sr_clearance(daily_df, direction="LONG")
            sr_clearance_short = nearest_daily_sr_clearance(daily_df, direction="SHORT")
            cfg["_last_sr_clearance_long_atr"]  = (sr_clearance_long / atr_val) if atr_val else float("inf")
            cfg["_last_sr_clearance_short_atr"] = (sr_clearance_short / atr_val) if atr_val else float("inf")

    bull_htf, bear_htf = check_htf(htf_df)

    if bull_htf and allowed_direction == "BULLISH":
        trend_ok, adx, daily_rsi = check_daily_trend(daily_df, "LONG", cfg=cfg)
        if not trend_ok:
            return "HOLD", 50.0, 0.0, 0.0, adx, daily_rsi
        valid, rsi, bh, bl = check_ltf_long(ltf_df, cfg=cfg)
        if valid:
            if cfg is not None and cfg.get("s1_sr_clearance_atr_mult", 0) > 0:
                if sr_clearance_long < cfg["s1_sr_clearance_atr_mult"] * atr_val:
                    return "HOLD", rsi, bh, bl, adx, daily_rsi
            logger.info(f"[S1][{symbol}] ✅ LONG | RSI={rsi:.1f} ADX={adx:.1f} Daily RSI={daily_rsi:.1f}")
            return "LONG", rsi, bh, bl, adx, daily_rsi
        return "HOLD", rsi, bh, bl, adx, daily_rsi

    if bear_htf and allowed_direction == "BEARISH":
        trend_ok, adx, daily_rsi = check_daily_trend(daily_df, "SHORT", cfg=cfg)
        if not trend_ok:
            return "HOLD", 50.0, 0.0, 0.0, adx, daily_rsi
        valid, rsi, bh, bl = check_ltf_short(ltf_df, cfg=cfg)
        if valid:
            if cfg is not None and cfg.get("s1_sr_clearance_atr_mult", 0) > 0:
                if sr_clearance_short < cfg["s1_sr_clearance_atr_mult"] * atr_val:
                    return "HOLD", rsi, bh, bl, adx, daily_rsi
            logger.info(f"[S1][{symbol}] ✅ SHORT | RSI={rsi:.1f} ADX={adx:.1f} Daily RSI={daily_rsi:.1f}")
            return "SHORT", rsi, bh, bl, adx, daily_rsi
        return "HOLD", rsi, bh, bl, adx, daily_rsi

    return "HOLD", 50.0, 0.0, 0.0, 0.0, 0.0


# ── S1 Exit Placement ─────────────────────────────────────── #

def _place_exits(symbol: str, hold_side: str, qty_str: str,
                 sl_trig: float, sl_exec: float,
                 trail_trigger: float, trail_range: float) -> bool:
    """
    S1 exit orders (2 TP legs only - SL already attached via preset in market order):
    Partial TP (50% at trail_trigger), trailing stop on the remaining 50%.

    Note: SL is NOT placed here - it's already attached to the market entry order
    via presetStopLossPrice, so position is protected from the moment it opens.
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


# ── S1 DNA Snapshot Fields ────────────────────────────────── #

def dna_fields(candles: dict) -> dict:
    """S1 trade fingerprint: daily EMA slope / price vs EMA / ADX state, H1 EMA, 3m EMA."""
    from indicators import calculate_ema, calculate_adx
    from trade_dna import ema_slope, price_vs_ema, adx_state, _is_empty, _closes_from

    out = {}
    daily = candles.get("daily")
    h1    = candles.get("h1")
    m3    = candles.get("m3")

    if not _is_empty(daily):
        closes_d = _closes_from(daily)
        ema_d    = calculate_ema(closes_d, 20)
        out["snap_trend_daily_ema_slope"]    = ema_slope(closes_d, 20)
        out["snap_trend_daily_price_vs_ema"] = price_vs_ema(float(closes_d.iloc[-1]), float(ema_d.iloc[-1]))
        if hasattr(daily, "columns") and len(daily) >= 20:
            adx_d = calculate_adx(daily)["adx"]
            out["snap_trend_daily_adx_state"] = adx_state(adx_d)
        else:
            out["snap_trend_daily_adx_state"] = ""

    if not _is_empty(h1):
        closes_h = _closes_from(h1)
        ema_h    = calculate_ema(closes_h, 20)
        out["snap_trend_h1_ema_slope"]    = ema_slope(closes_h, 20)
        out["snap_trend_h1_price_vs_ema"] = price_vs_ema(float(closes_h.iloc[-1]), float(ema_h.iloc[-1]))

    if not _is_empty(m3):
        closes_m3 = _closes_from(m3)
        ema_m3    = calculate_ema(closes_m3, 20)
        out["snap_trend_m3_price_vs_ema"] = price_vs_ema(float(closes_m3.iloc[-1]), float(ema_m3.iloc[-1]))

    return out


# ── ATR-based exit math (IG path) ───────────────────────── #

def compute_s1_sl_atr(direction: str, entry: float, box_high: float, box_low: float,
                     atr_value: float, cfg: dict) -> float:
    """
    Structural SL with ATR cap (IG path).

    LONG:  SL = max(entry − atr_mult·ATR, box_low  · (1 − buffer))   # tighter of the two
    SHORT: SL = min(entry + atr_mult·ATR, box_high · (1 + buffer))   # tighter of the two

    cfg supplies s1_sl_atr_mult and s1_sl_buffer_pct.
    """
    sl_buffer = cfg["s1_sl_buffer_pct"]
    if direction == "LONG":
        atr_floor        = entry - cfg["s1_sl_atr_mult"] * atr_value
        structural_floor = box_low * (1 - sl_buffer)
        return max(atr_floor, structural_floor)
    atr_ceil        = entry + cfg["s1_sl_atr_mult"] * atr_value
    structural_ceil = box_high * (1 + sl_buffer)
    return min(atr_ceil, structural_ceil)


def compute_s1_tp_atr(direction: str, entry: float, atr_value: float, cfg: dict) -> float:
    """TP1 (50% partial) trigger at entry ± tp_atr_mult × ATR (IG path)."""
    delta = cfg["s1_tp_atr_mult"] * atr_value
    return entry + delta if direction == "LONG" else entry - delta


def maybe_trail_sl_ig(instrument: dict, pos: dict, ig_mod, candles_df,
                      mark_price: float) -> None:
    """
    IG-aware structural swing trail for S1.

    Mirrors maybe_trail_sl's logic but reads CONFIG from `instrument`, calls
    ig_mod.update_sl, and mutates pos["sl"] / pos["swing_trail_ref"] directly.
    Caller is responsible for persisting state.

    LONG:  once mark >= swing_trail_ref, find swing low formed after ref candle,
           step SL up to swing_low * (1 - buffer); reset ref to next swing high.
    SHORT: mirror.

    No-op when instrument["s1_use_swing_trail"] is False or candles_df is short.
    """
    if not instrument.get("s1_use_swing_trail", False):
        return
    if candles_df is None or len(candles_df) < 3:
        return

    lookback = instrument["s1_swing_lookback"]
    buffer   = instrument["s1_sl_buffer_pct"]
    side     = pos["side"]

    try:
        if side == "LONG":
            ref = pos.get("swing_trail_ref")
            if ref is None:
                pos["swing_trail_ref"] = find_swing_high_target(candles_df, mark_price, lookback=lookback)
                return
            if mark_price >= ref:
                raw = find_swing_low_after_ref(candles_df, mark_price, ref, lookback=lookback)
                if raw:
                    swing_sl = raw * (1 - buffer)
                    if swing_sl > pos.get("sl", 0) and ig_mod.update_sl(pos["deal_id"], swing_sl):
                        pos["sl"] = swing_sl
                        pos["swing_trail_ref"] = find_swing_high_target(candles_df, mark_price, lookback=lookback)
                        logger.info(f"[S1][IG] Swing trail: SL -> {swing_sl:.5f}")
        else:  # SHORT
            ref = pos.get("swing_trail_ref")
            if ref is None:
                pos["swing_trail_ref"] = find_swing_low_target(candles_df, mark_price, lookback=lookback)
                return
            if mark_price <= ref:
                raw = find_swing_high_after_ref(candles_df, mark_price, ref, lookback=lookback)
                if raw:
                    swing_sl = raw * (1 + buffer)
                    if swing_sl < pos.get("sl", float("inf")) and ig_mod.update_sl(pos["deal_id"], swing_sl):
                        pos["sl"] = swing_sl
                        pos["swing_trail_ref"] = find_swing_low_target(candles_df, mark_price, lookback=lookback)
                        logger.info(f"[S1][IG] Swing trail: SL -> {swing_sl:.5f}")
    except Exception as e:
        logger.error(f"S1 IG swing trail error: {e}")
