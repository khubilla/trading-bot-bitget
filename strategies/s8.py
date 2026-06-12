"""
Strategy 8 — Post-S2 Bounce: breakout-retest at tri-confluence support.

Phase: after an S2-style daily breakout that failed to continue upward.
Price retraces to a zone where three supports cluster:
  1. The Darvas coil box top that formed BEFORE the S2 breakout
  2. The daily 20MA
  3. The 61.8% fib retracement of the impulse leg (coil box low → post-breakout swing high)
A small green daily candle resting on the zone arms a stop-buy above its high.

LONG only. Daily candles only. Exits copy S2 (preset SL, 50% partial TP at +10%,
10% trailing callback on the remainder). Single full-size entry — NO scale-in.
"""

import logging
from typing import Literal

import pandas as pd

from indicators import calculate_rsi
from tools import body_pct, upper_wick

logger = logging.getLogger(__name__)
Signal = Literal["LONG", "HOLD"]

# Default candle interval for S8 event snapshots.
SNAPSHOT_INTERVAL = "1D"

_HOLD = ("HOLD", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def _eff_top(row, wick_pct: float) -> float:
    """Darvas effective top: body top when the upper wick is rejected, else wick high."""
    bt = max(float(row["close"]), float(row["open"]))
    return bt if upper_wick(row) > wick_pct * bt else float(row["high"])


def _coil_before(daily_df: pd.DataFrame, b: int, big_top: float, cfg) -> tuple[float, float] | None:
    """
    Tight Darvas coil (1..S8_CONSOL_CANDLES candles) ending right before index b.
    Same effective-top/body-bottom math as S2, including S2's containment rule:
    every coil candle must close at/below the big momentum candle's body top —
    this is what stops impulse-continuation candles from registering as B.
    Returns (box_top, box_low) or None.
    """
    for n in range(1, cfg.S8_CONSOL_CANDLES + 1):
        if b - n < 0:
            return None
        window = daily_df.iloc[b - n:b]
        if not all(float(r["close"]) <= big_top for _, r in window.iterrows()):
            continue
        eff_tops = window.apply(lambda r: _eff_top(r, cfg.S8_DARVAS_WICK_PCT), axis=1)
        eff_h = float(eff_tops.max())
        eff_l = float(window.apply(
            lambda r: min(float(r["close"]), float(r["open"])), axis=1).min())
        if eff_h <= 0:
            continue
        if (eff_h - eff_l) / eff_h > cfg.S8_CONSOL_RANGE_PCT:
            continue
        return eff_h, eff_l
    return None


def _find_structure(daily_df: pd.DataFrame, rsi_ser: pd.Series, cfg) -> dict | None:
    """
    Most recent S2-like breakout day B within the last S8_PHASE_LOOKBACK completed
    candles: big momentum candle within S8_BIG_CANDLE_LOOKBACK days before B,
    tight coil right before B (all coil closes ≤ big candle body top),
    RSI > S8_RSI_THRESH on B, and B's close above the coil box top.
    iloc[-1] is the live forming candle and is never B.
    """
    last_completed = len(daily_df) - 2
    earliest = max(cfg.S8_CONSOL_CANDLES + 1,
                   last_completed - cfg.S8_PHASE_LOOKBACK + 1)
    for b in range(last_completed, earliest - 1, -1):
        if float(rsi_ser.iloc[b]) <= cfg.S8_RSI_THRESH:
            continue
        lb_start = max(0, b - cfg.S8_BIG_CANDLE_LOOKBACK)
        big_top = 0.0
        for _, row in daily_df.iloc[lb_start:b].iterrows():
            if body_pct(row) >= cfg.S8_BIG_CANDLE_BODY_PCT:
                big_top = max(big_top, float(row["close"]), float(row["open"]))
        if big_top <= 0:
            continue
        coil = _coil_before(daily_df, b, big_top, cfg)
        if coil is None:
            continue
        box_top, box_low = coil
        if float(daily_df["close"].iloc[b]) <= box_top:
            continue
        return {"b": b, "box_top": box_top, "box_low": box_low,
                "rsi_b": float(rsi_ser.iloc[b])}
    return None


def evaluate_s8(
    symbol: str,
    daily_df: pd.DataFrame,
) -> tuple[Signal, float, float, float, float, float, float, float, float, str]:
    """
    Strategy 8 — purely on daily candles. LONG only.
    Returns (signal, rsi_at_breakout, entry_trigger, green_low,
             zone_low, zone_high, box_top, ma, fib, reason)
    """
    import config_s8 as cfg

    if not cfg.S8_ENABLED:
        return (*_HOLD, "S8 disabled")

    rsi_period = 14
    min_candles = max(
        rsi_period + cfg.S8_BIG_CANDLE_LOOKBACK + cfg.S8_PHASE_LOOKBACK
        + cfg.S8_CONSOL_CANDLES + 2,
        cfg.S8_MA_PERIOD + 2,
    )
    if daily_df is None or len(daily_df) < min_candles:
        return (*_HOLD, "Not enough daily candles")

    closes = daily_df["close"].astype(float)
    rsi_ser = calculate_rsi(closes, rsi_period)

    s = _find_structure(daily_df, rsi_ser, cfg)
    if s is None:
        return (*_HOLD,
                f"No post-S2 structure (big candle + coil + RSI>{cfg.S8_RSI_THRESH} "
                f"breakout) in last {cfg.S8_PHASE_LOOKBACK}d")

    b, box_top, box_low, rsi_b = s["b"], s["box_top"], s["box_low"], s["rsi_b"]

    # Impulse leg: breakout day B through the last completed candle
    swing_high = float(daily_df["high"].iloc[b:-1].max())
    if swing_high <= box_top * (1 + cfg.S8_MIN_EXTENSION):
        return (*_HOLD,
                f"Breakout leg too small — swing high {swing_high:.5f} "
                f"< box_top +{cfg.S8_MIN_EXTENSION*100:.0f}%")

    fib = swing_high - cfg.S8_FIB_RETRACE * (swing_high - box_low)

    closes_completed = closes.iloc[:-1]
    if cfg.S8_MA_TYPE.upper() == "EMA":
        from indicators import calculate_ema
        ma = float(calculate_ema(closes_completed, cfg.S8_MA_PERIOD).iloc[-1])
    else:
        ma = float(closes_completed.rolling(cfg.S8_MA_PERIOD).mean().iloc[-1])

    levels = sorted([box_top, ma, fib])
    zone_low, zone_high = levels[0], levels[2]
    width = (zone_high - zone_low) / zone_high
    if width > cfg.S8_CONFLUENCE_TOL:
        return ("HOLD", rsi_b, 0.0, 0.0, 0.0, 0.0, box_top, ma, fib,
                f"No tri-confluence — box_top={box_top:.5f} ma={ma:.5f} "
                f"fib={fib:.5f} spread {width*100:.1f}% > {cfg.S8_CONFLUENCE_TOL*100:.0f}%")

    green = daily_df.iloc[-2]   # last COMPLETED daily candle
    g_open, g_close = float(green["open"]), float(green["close"])
    g_low, g_high = float(green["low"]), float(green["high"])
    if g_close <= g_open:
        return ("HOLD", rsi_b, 0.0, 0.0, zone_low, zone_high, box_top, ma, fib,
                "Confluence ✅ — last completed candle is red, waiting green bounce")
    gb = body_pct(green)
    if gb > cfg.S8_SMALL_BODY_PCT:
        return ("HOLD", rsi_b, 0.0, 0.0, zone_low, zone_high, box_top, ma, fib,
                f"Confluence ✅ — green candle body {gb*100:.1f}% "
                f"> {cfg.S8_SMALL_BODY_PCT*100:.0f}% (not small)")
    if not (zone_low <= g_low <= zone_high * (1 + cfg.S8_PROXIMITY)):
        return ("HOLD", rsi_b, 0.0, 0.0, zone_low, zone_high, box_top, ma, fib,
                f"Confluence ✅ — green candle low {g_low:.5f} not sitting on "
                f"zone [{zone_low:.5f}, {zone_high:.5f}]")

    trigger = g_high * (1 + cfg.S8_BREAKOUT_BUFFER)
    logger.info(
        f"[S8][{symbol}] ✅ LONG | box_top={box_top:.5f} ma={ma:.5f} fib={fib:.5f} "
        f"zone=[{zone_low:.5f},{zone_high:.5f}] | green low={g_low:.5f} "
        f"body={gb*100:.1f}% | trigger={trigger:.5f}"
    )
    return ("LONG", rsi_b, trigger, g_low, zone_low, zone_high, box_top, ma, fib,
            f"S8 ✅ tri-confluence bounce | zone {zone_low:.5f}–{zone_high:.5f} "
            f"(width {width*100:.1f}%) | green body {gb*100:.1f}% | "
            f"buy > {trigger:.5f}")


# ── S8 Pending-Signal Queue ───────────────────────────────── #

def queue_pending(bot, c: dict) -> None:
    """Queue an S8 LONG bounce on bot.pending_signals for the entry watcher."""
    import time as _t
    import state as st

    symbol = c["symbol"]
    zone_high = c.get("s8_zone_high") or 0.0
    zone_low  = c.get("s8_zone_low") or 0.0
    bot.pending_signals[symbol] = {
        "strategy":           "S8",
        "side":               "LONG",
        "trigger":            c["s8_trigger"],
        "s8_trigger":         c["s8_trigger"],
        "s8_green_low":       c["s8_green_low"],
        "s8_zone_low":        zone_low,
        "s8_zone_high":       zone_high,
        "s8_box_top":         c.get("s8_box_top"),
        "s8_ma20":            c.get("s8_ma20"),
        "s8_fib618":          c.get("s8_fib618"),
        "priority_rank":      c.get("priority_rank", 999),
        "priority_score":     c.get("priority_score", 0.0),
        "snap_daily_rsi":     round(c["s8_rsi"], 1) if c.get("s8_rsi") else None,
        "snap_box_range_pct": round((zone_high - zone_low) / zone_high * 100, 3)
                              if zone_high else None,
        "snap_sentiment":     bot.sentiment.direction if bot.sentiment else "?",
        "expires":            _t.time() + 86400,
    }
    st.save_pending_signals(bot.pending_signals)
    logger.info(
        f"[S8][{symbol}] 🕐 PENDING LONG queued | "
        f"trigger={c['s8_trigger']:.5f} | green_low={c['s8_green_low']:.5f}"
    )
    st.add_scan_log(
        f"[S8][{symbol}] 🕐 PENDING LONG | trigger={c['s8_trigger']:.5f}", "SIGNAL"
    )


# ── S8 Entry Watcher (pending tick) ───────────────────────── #

def handle_pending_tick(bot, symbol: str, sig: dict, balance: float,
                        paper_mode: bool | None = None) -> str | None:
    """S8 bounce trigger + invalidation check. Return 'break' to stop outer loop."""
    import state as st
    import trader as tr
    import config, config_s8

    ps = st.get_pair_state(symbol)
    if ps.get("s8_signal", "HOLD") not in ("LONG",):
        logger.info(f"[S8][{symbol}] 🚫 Signal gone — cancelling pending")
        st.add_scan_log(f"[S8][{symbol}] 🚫 Pending cancelled (signal gone)", "INFO")
        bot.pending_signals.pop(symbol, None)
        st.save_pending_signals(bot.pending_signals)
        return None
    try:
        mark = tr.get_mark_price(symbol)
    except Exception:
        return None
    trigger  = sig["s8_trigger"]
    zone_low = sig["s8_zone_low"]
    if mark < zone_low:
        logger.info(f"[S8][{symbol}] ❌ Invalidated — mark {mark:.5f} < zone_low {zone_low:.5f}")
        st.add_scan_log(f"[S8][{symbol}] ❌ Pending cancelled (price below zone)", "INFO")
        bot.pending_signals.pop(symbol, None)
        st.save_pending_signals(bot.pending_signals)
        return None
    in_window = trigger <= mark <= trigger * (1 + config_s8.S8_MAX_ENTRY_BUFFER)
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
            bot._fire_s8(symbol, sig, mark, balance)
        bot.pending_signals.pop(symbol, None)
        st.save_pending_signals(bot.pending_signals)
    return None


# ── S8 Exit Placement (exits copy S2) ─────────────────────── #

def compute_and_place_long_exits(symbol: str, qty_str: str, fill: float,
                                 sl_floor: float, stop_loss_pct: float) -> tuple[bool, float, float]:
    """
    Compute S8 long-side SL/trail levels and place the S2-style 2-leg TP exits.
    sl_floor is the structural SL already floored by the caller
    (green candle low × 0.999); the 5% cap from fill still applies on top.
    SL itself is attached as a preset on the entry order — the value returned
    here is the recorded/recomputed level.
    Returns (ok, sl_trig, trail_trig).
    """
    import trader
    from config_s8 import S8_TRAILING_TRIGGER_PCT, S8_TRAILING_RANGE_PCT
    import strategies.s2 as _s2   # module ref so test patches of the primitive apply

    trail_trig = float(trader._round_price(fill * (1 + S8_TRAILING_TRIGGER_PCT), symbol))
    sl_trig    = float(trader._round_price(max(sl_floor, fill * (1 - stop_loss_pct)), symbol))
    sl_exec    = float(trader._round_price(sl_trig * 0.995, symbol))
    ok = _s2._place_partial_trail_exits(symbol, "long", qty_str, sl_trig, sl_exec,
                                        trail_trig, S8_TRAILING_RANGE_PCT)
    return ok, sl_trig, trail_trig


# ── S8 Swing Trail (same reference-gated cycle as S2) ─────── #

def maybe_trail_sl(symbol: str, ap: dict, tr_mod, st_mod, partial_done: bool) -> None:
    """
    Structural swing trail for S8 LONG: only active after the partial has fired.
    Pulls SL up to the 1D swing-low after price exceeds the prior swing-high.
    """
    import config_s8
    from tools import find_swing_high_target, find_swing_low_after_ref

    if not getattr(config_s8, "S8_USE_SWING_TRAIL", False):
        return
    if ap.get("side") != "LONG" or not partial_done:
        return
    try:
        lb    = config_s8.S8_SWING_LOOKBACK
        cs_df = tr_mod.get_candles(symbol, "1D", limit=lb + 5)
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
                swing_sl = raw * (1 - config_s8.S8_STOP_LOSS_PCT)
                if swing_sl > ap.get("sl", 0) and tr_mod.update_position_sl(symbol, swing_sl, hold_side="long"):
                    ap["sl"] = swing_sl
                    st_mod.update_open_trade_sl(symbol, swing_sl)
                    ap["swing_trail_ref"] = find_swing_high_target(cs_df, mark, lookback=lb)
                    logger.info(f"[S8][{symbol}] 📍 Swing trail: SL → {swing_sl:.5f}")
    except Exception as e:
        logger.error(f"S8 swing trail error [{symbol}]: {e}")


# ── S8 DNA Snapshot Fields ────────────────────────────────── #

def dna_fields(candles: dict) -> dict:
    """S8 trade fingerprint: daily EMA slope, price vs EMA, RSI bucket (same as S2)."""
    from indicators import calculate_ema, calculate_rsi
    from trade_dna import ema_slope, price_vs_ema, rsi_bucket, _is_empty, _closes_from

    out = {}
    daily = candles.get("daily")
    if _is_empty(daily):
        return out
    closes_d = _closes_from(daily)
    ema_d    = calculate_ema(closes_d, 20)
    rsi_d    = calculate_rsi(closes_d)
    out["snap_trend_daily_ema_slope"]    = ema_slope(closes_d, 20)
    out["snap_trend_daily_price_vs_ema"] = price_vs_ema(float(closes_d.iloc[-1]), float(ema_d.iloc[-1]))
    out["snap_trend_daily_rsi_bucket"]   = rsi_bucket(float(rsi_d.iloc[-1]))
    return out


# ── S8 Paper Trail Setup ──────────────────────────────────── #

def compute_paper_trail_long(mark: float, sl_price: float, tp_price_abs: float = 0,
                             take_profit_pct: float = 0.05) -> tuple[bool, float, float, float, bool]:
    """Paper-trader LONG trail setup for S8 (same shape as S2's).
    Returns (use_trailing, trail_trigger, trail_range, tp_price, breakeven_after_partial)."""
    from config_s8 import S8_TRAILING_TRIGGER_PCT, S8_TRAILING_RANGE_PCT
    trail_trigger = mark * (1 + S8_TRAILING_TRIGGER_PCT)
    trail_range   = S8_TRAILING_RANGE_PCT
    return True, trail_trigger, trail_range, trail_trigger, False
