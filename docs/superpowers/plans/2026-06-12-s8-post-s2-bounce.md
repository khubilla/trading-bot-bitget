# S8 Post-S2 Bounce Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add S8 — a LONG-only daily-timeframe bounce strategy that buys a stop-break above a small green candle resting on the tri-confluence of (1) the Darvas coil box top formed before an S2-style breakout, (2) the daily 20MA, and (3) the 61.8% fib retracement of the breakout impulse leg. Exits copy S2.

**Architecture:** One new strategy module `strategies/s8.py` (evaluator, pending watcher, exit computer, paper trail, DNA fields) + three lockstep config files + wiring into the shared `bot.py` loop (used by Bitget, Bybit, Binance), `trader.py`, `paper_trader.py`, `trade_dna.py`, `analytics.py`, `optimize.py`, `dashboard.html`. **No new trades.csv columns** — the CSV contract stays frozen; S8 reuses generically-named existing columns (`snap_daily_rsi`, `snap_entry_trigger`, `snap_sl`, `snap_box_range_pct`, `snap_sentiment`, `box_low`/`box_high`), same precedent as S7 reusing S4's columns.

**Tech Stack:** Python 3, pandas, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-12-s8-post-s2-bounce-design.md`

**Base:** Branch `feat/s8-post-s2-bounce` from `master` HEAD in an isolated worktree (the main tree carries unrelated uncommitted DNA-refetch work — `_DNA_TIMEFRAMES` does NOT exist at HEAD; `_fire_s8` must use HEAD's inline `dna_snapshot("SN", symbol, {"daily": df})` pattern). **Merge note:** when the in-flight DNA work lands, add `"S8": ("daily",)` to its `_DNA_TIMEFRAMES` dict.

---

### Task 0: Worktree + branch + spec/plan commit

**Files:**
- Create: worktree at `.claude/worktrees/s8-post-s2-bounce` (or `git worktree add`)

- [ ] **Step 1:** Create isolated worktree on a new branch from master HEAD (use superpowers:using-git-worktrees):

```bash
git worktree add .claude/worktrees/s8-post-s2-bounce -b feat/s8-post-s2-bounce master
```

- [ ] **Step 2:** Copy the spec and this plan into the worktree (they were authored in the main tree, untracked):

```bash
cp docs/superpowers/specs/2026-06-12-s8-post-s2-bounce-design.md \
   .claude/worktrees/s8-post-s2-bounce/docs/superpowers/specs/
cp docs/superpowers/plans/2026-06-12-s8-post-s2-bounce.md \
   .claude/worktrees/s8-post-s2-bounce/docs/superpowers/plans/
```

- [ ] **Step 3:** Commit them:

```bash
cd .claude/worktrees/s8-post-s2-bounce
git add docs/superpowers/specs/2026-06-12-s8-post-s2-bounce-design.md \
        docs/superpowers/plans/2026-06-12-s8-post-s2-bounce.md
git commit -m "docs(s8): add post-S2 bounce design spec + implementation plan"
```

All subsequent tasks run inside the worktree. Verify the venv works there: `python -c "import pandas"` (use the main tree's `venv/bin/python` via absolute path if needed; pytest runs as `/Users/kevin/code/bitget_mtf_bot/venv/bin/python -m pytest`).

---

### Task 1: Config files (3, lockstep)

**Files:**
- Create: `config_s8.py`, `config_bybit_s8.py`, `config_binance_s8.py`

- [ ] **Step 1: Write `config_s8.py`:**

```python
# ============================================================
#  Strategy 8 Configuration — Post-S2 Bounce (Tri-Confluence)
# ============================================================
# LONG-only daily bounce play in the phase AFTER an S2-style breakout
# that failed to continue upward:
#   1. S2-like structure found in recent history: big momentum candle,
#      tight Darvas coil, RSI>70 breakout above the coil box top
#   2. Price pulled back to a tri-confluence support zone:
#      coil box top + daily 20MA + 61.8% fib of the impulse leg
#   3. A small green daily candle sits on the zone
#   4. Stop-buy above that green candle's high

S8_ENABLED = True

# ── Post-S2 Structure Detection ──────────────────────────── #
S8_BIG_CANDLE_BODY_PCT = 0.20   # Min 20% body to qualify as momentum candle (matches S2)
S8_BIG_CANDLE_LOOKBACK = 30     # Big candle searched within 30 days before breakout day B
S8_RSI_THRESH          = 70     # Daily RSI on breakout day B must exceed this
S8_CONSOL_CANDLES      = 5      # Max coil size before B (tries 1 to 5, matches S2)
S8_CONSOL_RANGE_PCT    = 0.15   # Max 15% effective coil range (matches S2)
S8_DARVAS_WICK_PCT     = 0.05   # Darvas top rule: wick >5% of body top → use body (matches S2)
S8_PHASE_LOOKBACK      = 15     # Breakout day B must be within last 15 completed candles

# ── Tri-Confluence Zone ──────────────────────────────────── #
S8_MIN_EXTENSION   = 0.05   # swing_high must exceed box_top by ≥5% (real impulse leg)
S8_FIB_RETRACE     = 0.618  # fib level of leg (box_low → swing_high) used as support #3
S8_MA_PERIOD       = 20     # daily moving average period (support #2)
S8_MA_TYPE         = "SMA"  # "SMA" | "EMA"
S8_CONFLUENCE_TOL  = 0.02   # box_top/ma/fib must cluster within 2% ((max-min)/max)

# ── Green Bounce Candle ──────────────────────────────────── #
S8_SMALL_BODY_PCT  = 0.05   # green candle body ≤5% of open = "small"
S8_PROXIMITY       = 0.01   # candle low may sit ≤1% above zone top

# ── Entry Trigger ────────────────────────────────────────── #
S8_BREAKOUT_BUFFER  = 0.005  # 0.5% buffer above green candle high
S8_MAX_ENTRY_BUFFER = 0.04   # Skip if price already >4% above trigger (matches S2)

# ── Risk Management (exits copy S2) ──────────────────────── #
S8_LEVERAGE         = 10
S8_TRADE_SIZE_PCT   = 0.04   # 4% of portfolio as margin — single full entry, NO scale-in
S8_TAKE_PROFIT_PCT  = 0.10   # partial TP activation: +10% from entry
S8_STOP_LOSS_PCT    = 0.05   # SL cap: 5% from entry (green candle low is primary SL)

S8_TRAILING_TRIGGER_PCT = 0.10   # close 50% at +10% from entry
S8_TRAILING_RANGE_PCT   = 10     # 10% trailing callback on the remaining 50%
S8_USE_SWING_TRAIL      = False  # exchange-side % trail by default (matches S2)
S8_SWING_LOOKBACK       = 30     # daily candles for swing-low search (if swing trail on)
```

- [ ] **Step 2:** Copy verbatim to `config_bybit_s8.py` and `config_binance_s8.py` (only the header comment line may differ: append `— Bybit` / `— Binance`).

- [ ] **Step 3: Verify all three import:**

Run: `for f in config_s8 config_bybit_s8 config_binance_s8; do venv/bin/python -c "import $f; print('$f OK')"; done`
Expected: three OK lines.

- [ ] **Step 4: Commit**

```bash
git add config_s8.py config_bybit_s8.py config_binance_s8.py
git commit -m "feat(s8): add config files for post-S2 bounce strategy (all three crypto bots)"
```

---

### Task 2: `strategies/s8.py` — evaluator (TDD)

**Files:**
- Create: `strategies/s8.py`
- Test: `tests/test_s8_evaluate.py`

- [ ] **Step 1: Write the failing tests** (`tests/test_s8_evaluate.py`):

```python
"""Unit tests for evaluate_s8() — post-S2 bounce at tri-confluence."""
import pandas as pd
import pytest

from strategies.s8 import evaluate_s8


def _mk_df(rows):
    """rows: list of (open, high, low, close). Daily UTC index."""
    idx = pd.date_range("2026-01-01", periods=len(rows), freq="D", tz="UTC")
    return pd.DataFrame(
        {"open":  [r[0] for r in rows], "high": [r[1] for r in rows],
         "low":   [r[2] for r in rows], "close": [r[3] for r in rows]},
        index=idx,
    )


# Fixture geometry constants — keep tests and fixture in sync.
_WARMUP = 50          # flat candles at 100 (RSI/MA warm-up; evaluator needs ≥66 rows)
_B_IDX = _WARMUP + 4  # breakout day index: warmup, big candle, 3 coil candles, B
_BOX_LOW = 128.0      # smallest-window coil (n=1) → box_low = min body of last coil candle


def _post_s2_bounce_rows(green_body=0.02, green_low_on_zone=True,
                         extension=0.30, pullback_steps=9):
    """
    Synthetic post-S2 bounce:
      50 flat warm-up candles around 100 (RSI/MA seed)
      big candle: 100 -> 130 (30% body)
      coil: 3 tight candles 126-130; smallest valid window is n=1 →
            box_top=130 (Darvas wick high), box_low=128 (body bottom)
      breakout day B (idx 54): closes at 136 (> box_top), RSI pumped high
      impulse leg up to swing_high = box_top*(1+extension)
      slow 9-candle pullback toward the fib (drags the 20MA up near the zone)
      last completed candle: small green candle sitting on the zone
      live forming candle on top
    fib618 = swing_high - 0.618*(swing_high - 128).
    """
    rows = [(100, 100.5, 99.5, 100.0)] * _WARMUP
    rows += [(100, 131, 100, 130)]                      # big candle, body 30%
    rows += [(129, 130, 126, 128), (128, 130, 126, 129),
             (129, 130, 126, 128)]                      # coil; n=1 box: 130/128
    rows += [(130, 137, 129, 136)]                      # breakout day B
    swing_high = 130 * (1 + extension)                  # e.g. 169
    rows += [(136, swing_high, 135, swing_high * 0.99)] # impulse peak
    fib = swing_high - 0.618 * (swing_high - _BOX_LOW)
    # pullback: drift down towards fib — enough candles to pull the 20MA near the zone
    top = swing_high * 0.985
    for i in range(pullback_steps):
        px = top - (top - fib * 1.01) * (i + 1) / pullback_steps
        rows += [(px * 1.01, px * 1.02, px * 0.995, px)]
    # green candle on the zone (or floating just above it when green_low_on_zone=False)
    g_low = fib * (1.001 if green_low_on_zone else 1.06)
    g_open = g_low * 1.002
    g_close = g_open * (1 + green_body)
    rows += [(g_open, g_close * 1.003, g_low, g_close)]
    # live forming candle
    rows += [(g_close, g_close * 1.002, g_close * 0.998, g_close * 1.001)]
    return rows


def test_disabled_returns_hold(monkeypatch):
    monkeypatch.setattr("config_s8.S8_ENABLED", False)
    sig, *_, reason = evaluate_s8("BTCUSDT", _mk_df(_post_s2_bounce_rows()))
    assert sig == "HOLD"
    assert "disabled" in reason.lower()


def test_not_enough_candles_returns_hold():
    sig, *_, reason = evaluate_s8("BTCUSDT", _mk_df([(100, 101, 99, 100)] * 10))
    assert sig == "HOLD"
    assert "not enough" in reason.lower()


def test_full_setup_returns_long_with_levels(monkeypatch):
    # widen tolerance: synthetic 20MA isn't engineered to the exact zone
    monkeypatch.setattr("config_s8.S8_CONFLUENCE_TOL", 0.10)
    df = _mk_df(_post_s2_bounce_rows())
    sig, rsi_b, trigger, green_low, zone_low, zone_high, box_top, ma, fib, reason = \
        evaluate_s8("BTCUSDT", df)
    assert sig == "LONG", reason
    assert box_top == pytest.approx(130, rel=0.01)
    g = df.iloc[-2]
    assert green_low == pytest.approx(float(g["low"]))
    assert trigger == pytest.approx(float(g["high"]) * 1.005, rel=1e-6)
    assert zone_low <= green_low
    assert rsi_b > 70


def test_no_breakout_in_lookback_returns_hold(monkeypatch):
    monkeypatch.setattr("config_s8.S8_CONFLUENCE_TOL", 0.10)
    monkeypatch.setattr("config_s8.S8_PHASE_LOOKBACK", 3)  # breakout is older than 3 candles
    sig, *_, reason = evaluate_s8("BTCUSDT", _mk_df(_post_s2_bounce_rows()))
    assert sig == "HOLD"
    assert "structure" in reason.lower()


def test_continuation_candle_is_not_breakout_day(monkeypatch):
    """The impulse-peak candle closes above the breakout day, but its 'coil'
    (the breakout candle) closes ABOVE the big candle body top — the S2
    containment rule must reject it, anchoring B at the true coil breakout."""
    monkeypatch.setattr("config_s8.S8_CONFLUENCE_TOL", 0.10)
    df = _mk_df(_post_s2_bounce_rows())
    sig, _, _, _, _, _, box_top, _, _, reason = evaluate_s8("BTCUSDT", df)
    assert sig == "LONG", reason
    assert box_top == pytest.approx(130, rel=0.01)  # NOT the breakout candle's high (137)


def test_red_candle_returns_hold(monkeypatch):
    monkeypatch.setattr("config_s8.S8_CONFLUENCE_TOL", 0.10)
    rows = _post_s2_bounce_rows()
    o, h, l, c = rows[-2]
    rows[-2] = (c, h, l, o)        # invert: red candle
    sig, *_, reason = evaluate_s8("BTCUSDT", _mk_df(rows))
    assert sig == "HOLD"
    assert "green" in reason.lower() or "red" in reason.lower()


def test_big_green_candle_returns_hold(monkeypatch):
    monkeypatch.setattr("config_s8.S8_CONFLUENCE_TOL", 0.10)
    sig, *_, reason = evaluate_s8(
        "BTCUSDT", _mk_df(_post_s2_bounce_rows(green_body=0.12)))
    assert sig == "HOLD"
    assert "body" in reason.lower()


def test_candle_floating_above_zone_returns_hold(monkeypatch):
    monkeypatch.setattr("config_s8.S8_CONFLUENCE_TOL", 0.10)
    sig, *_, reason = evaluate_s8(
        "BTCUSDT", _mk_df(_post_s2_bounce_rows(green_low_on_zone=False)))
    assert sig == "HOLD"
    assert "zone" in reason.lower()


def test_confluence_spread_too_wide_returns_hold(monkeypatch):
    monkeypatch.setattr("config_s8.S8_CONFLUENCE_TOL", 0.001)  # impossible tolerance
    sig, *_, reason = evaluate_s8("BTCUSDT", _mk_df(_post_s2_bounce_rows()))
    assert sig == "HOLD"
    assert "confluence" in reason.lower()


def test_leg_too_small_returns_hold(monkeypatch):
    monkeypatch.setattr("config_s8.S8_CONFLUENCE_TOL", 0.10)
    monkeypatch.setattr("config_s8.S8_MIN_EXTENSION", 0.50)  # demand 50% leg
    sig, *_, reason = evaluate_s8(
        "BTCUSDT", _mk_df(_post_s2_bounce_rows(extension=0.30)))
    assert sig == "HOLD"
    assert "leg" in reason.lower() or "extension" in reason.lower()


def test_fib_arithmetic_exact(monkeypatch):
    monkeypatch.setattr("config_s8.S8_CONFLUENCE_TOL", 0.10)
    df = _mk_df(_post_s2_bounce_rows())
    sig, _, _, _, _, _, box_top, _, fib, reason = evaluate_s8("BTCUSDT", df)
    assert sig == "LONG", reason
    swing_high = float(df["high"].iloc[_B_IDX:-1].max())   # high from breakout day on
    assert fib == pytest.approx(swing_high - 0.618 * (swing_high - _BOX_LOW), rel=1e-9)
```

- [ ] **Step 2: Run tests to verify they fail:**

Run: `venv/bin/python -m pytest tests/test_s8_evaluate.py -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'strategies.s8'`

- [ ] **Step 3: Write `strategies/s8.py` (evaluator portion):**

```python
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
```

- [ ] **Step 4: Run tests:**

Run: `venv/bin/python -m pytest tests/test_s8_evaluate.py -v`
Expected: ALL PASS. If `test_full_setup_returns_long_with_levels` fails on the synthetic geometry, debug with prints of `box_top/ma/fib/zone` — adjust the test fixture (not the strategy thresholds) so the intended setup is actually present.

- [ ] **Step 5: Commit**

```bash
git add strategies/s8.py tests/test_s8_evaluate.py
git commit -m "feat(s8): add post-S2 bounce evaluator (tri-confluence + green candle gate)"
```

---

### Task 3: Pending watcher + paper trail (TDD)

**Files:**
- Modify: `strategies/s8.py` (append)
- Test: `tests/test_s8_pending.py`

- [ ] **Step 1: Write failing tests** (`tests/test_s8_pending.py`):

```python
"""S8 queue_pending + handle_pending_tick behaviour."""
import time
import types
import pytest

import strategies.s8 as s8


class _Sentiment:
    direction = "BULLISH"


class _Bot:
    def __init__(self):
        self.pending_signals = {}
        self.active_positions = {}
        self.sentiment = _Sentiment()
        self._trade_lock = __import__("threading").Lock()
        self.fired = []

    def _fire_s8(self, symbol, sig, mark, balance):
        self.fired.append((symbol, mark))


def _candidate():
    return {
        "symbol": "ABCUSDT", "s8_trigger": 105.0, "s8_green_low": 99.0,
        "s8_zone_low": 98.0, "s8_zone_high": 100.0,
        "s8_box_top": 99.5, "s8_ma20": 98.5, "s8_fib618": 99.0,
        "s8_rsi": 75.0, "s8_reason": "test", "priority_rank": 1,
        "priority_score": 10.0,
    }


@pytest.fixture
def bot(monkeypatch):
    b = _Bot()
    monkeypatch.setattr("state.save_pending_signals", lambda *a, **k: None)
    monkeypatch.setattr("state.add_scan_log", lambda *a, **k: None)
    return b


def test_queue_pending_payload(bot):
    s8.queue_pending(bot, _candidate())
    sig = bot.pending_signals["ABCUSDT"]
    assert sig["strategy"] == "S8"
    assert sig["side"] == "LONG"
    assert sig["trigger"] == 105.0
    assert sig["s8_green_low"] == 99.0
    assert sig["s8_zone_low"] == 98.0
    assert sig["expires"] > time.time()


def _pending_sig():
    return {
        "strategy": "S8", "side": "LONG", "trigger": 105.0,
        "s8_trigger": 105.0, "s8_green_low": 99.0,
        "s8_zone_low": 98.0, "s8_zone_high": 100.0,
        "snap_daily_rsi": 75.0, "snap_sentiment": "BULLISH",
        "expires": time.time() + 86400,
    }


def test_pending_fires_in_window(bot, monkeypatch):
    bot.pending_signals["ABCUSDT"] = _pending_sig()
    monkeypatch.setattr("state.get_pair_state", lambda s: {"s8_signal": "LONG"})
    monkeypatch.setattr("state.is_pair_paused", lambda s: False)
    monkeypatch.setattr("trader.get_mark_price", lambda s: 105.5)
    import config
    monkeypatch.setattr(config, "MAX_CONCURRENT_TRADES", 5, raising=False)
    s8.handle_pending_tick(bot, "ABCUSDT", bot.pending_signals["ABCUSDT"], 1000.0)
    assert bot.fired == [("ABCUSDT", 105.5)]
    assert "ABCUSDT" not in bot.pending_signals


def test_pending_invalidates_below_zone(bot, monkeypatch):
    bot.pending_signals["ABCUSDT"] = _pending_sig()
    monkeypatch.setattr("state.get_pair_state", lambda s: {"s8_signal": "LONG"})
    monkeypatch.setattr("trader.get_mark_price", lambda s: 97.0)
    s8.handle_pending_tick(bot, "ABCUSDT", bot.pending_signals["ABCUSDT"], 1000.0)
    assert bot.fired == []
    assert "ABCUSDT" not in bot.pending_signals


def test_pending_cancelled_when_signal_gone(bot, monkeypatch):
    bot.pending_signals["ABCUSDT"] = _pending_sig()
    monkeypatch.setattr("state.get_pair_state", lambda s: {"s8_signal": "HOLD"})
    s8.handle_pending_tick(bot, "ABCUSDT", bot.pending_signals["ABCUSDT"], 1000.0)
    assert bot.fired == []
    assert "ABCUSDT" not in bot.pending_signals


def test_pending_waits_below_trigger(bot, monkeypatch):
    bot.pending_signals["ABCUSDT"] = _pending_sig()
    monkeypatch.setattr("state.get_pair_state", lambda s: {"s8_signal": "LONG"})
    monkeypatch.setattr("trader.get_mark_price", lambda s: 102.0)
    s8.handle_pending_tick(bot, "ABCUSDT", bot.pending_signals["ABCUSDT"], 1000.0)
    assert bot.fired == []
    assert "ABCUSDT" in bot.pending_signals


def test_compute_paper_trail_long():
    use_trail, trig, rng, tp, be = s8.compute_paper_trail_long(100.0, 95.0)
    assert use_trail is True
    assert trig == pytest.approx(110.0)
```

(If the existing `tests/conftest.py` at HEAD already stubs `bitget_client`/network access for strategy tests, follow its conventions; check `tests/test_s7_evaluate.py` imports run clean first.)

- [ ] **Step 2: Run to verify failure:**

Run: `venv/bin/python -m pytest tests/test_s8_pending.py -v`
Expected: FAIL with `AttributeError: module 'strategies.s8' has no attribute 'queue_pending'`

- [ ] **Step 3: Append to `strategies/s8.py`:**

```python
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


# ── S8 Paper Trail Setup ──────────────────────────────────── #

def compute_paper_trail_long(mark: float, sl_price: float, tp_price_abs: float = 0,
                             take_profit_pct: float = 0.05) -> tuple[bool, float, float, float, bool]:
    """Paper-trader LONG trail setup for S8 (same shape as S2's).
    Returns (use_trailing, trail_trigger, trail_range, tp_price, breakeven_after_partial)."""
    from config_s8 import S8_TRAILING_TRIGGER_PCT, S8_TRAILING_RANGE_PCT
    trail_trigger = mark * (1 + S8_TRAILING_TRIGGER_PCT)
    trail_range   = S8_TRAILING_RANGE_PCT
    return True, trail_trigger, trail_range, trail_trigger, False
```

- [ ] **Step 4: Run tests:**

Run: `venv/bin/python -m pytest tests/test_s8_pending.py tests/test_s8_evaluate.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add strategies/s8.py tests/test_s8_pending.py
git commit -m "feat(s8): pending watcher (trigger window + zone invalidation) and paper trail"
```

---

### Task 4: Exit computation + swing trail + DNA fields (TDD)

**Files:**
- Modify: `strategies/s8.py` (append)
- Test: `tests/test_s8_exits.py`

- [ ] **Step 1: Write failing tests** (`tests/test_s8_exits.py`):

```python
"""S8 exit computation: SL = max(green_low*0.999-floor, fill*(1-5%)), S2-style 2-leg TP."""
import pytest

import strategies.s8 as s8


@pytest.fixture
def patched(monkeypatch):
    import trader
    calls = {}
    monkeypatch.setattr(trader, "_round_price", lambda p, s: f"{p:.5f}")
    placed = []
    import strategies.s2 as s2
    def fake_place(symbol, hold_side, qty_str, sl_trig, sl_exec, trail_trigger, trail_range):
        placed.append({"symbol": symbol, "hold_side": hold_side, "qty": qty_str,
                       "sl_trig": sl_trig, "trail_trigger": trail_trigger,
                       "trail_range": trail_range})
        return True
    monkeypatch.setattr(s2, "_place_partial_trail_exits", fake_place)
    calls["placed"] = placed
    return calls


def test_sl_uses_green_low_floor_when_within_cap(patched):
    # fill=100, sl_floor (green_low*0.999 precomputed) = 97 → above 95 cap → SL=97
    ok, sl, trail = s8.compute_and_place_long_exits("ABCUSDT", "10", 100.0, 97.0, 0.05)
    assert ok is True
    assert sl == pytest.approx(97.0)
    assert trail == pytest.approx(110.0)
    assert patched["placed"][0]["hold_side"] == "long"


def test_sl_capped_at_5pct_when_green_low_too_deep(patched):
    # sl_floor = 80 → below the 95 cap → SL=95
    ok, sl, trail = s8.compute_and_place_long_exits("ABCUSDT", "10", 100.0, 80.0, 0.05)
    assert sl == pytest.approx(95.0)


def test_trail_range_passed_through(patched):
    s8.compute_and_place_long_exits("ABCUSDT", "10", 100.0, 97.0, 0.05)
    import config_s8
    assert patched["placed"][0]["trail_range"] == config_s8.S8_TRAILING_RANGE_PCT


def test_dna_fields_daily_only():
    import pandas as pd
    idx = pd.date_range("2026-01-01", periods=60, freq="D", tz="UTC")
    closes = pd.Series([100 + i for i in range(60)], index=idx)
    df = pd.DataFrame({"open": closes, "high": closes * 1.01,
                       "low": closes * 0.99, "close": closes}, index=idx)
    out = s8.dna_fields({"daily": df})
    assert "snap_trend_daily_ema_slope" in out
    assert "snap_trend_daily_price_vs_ema" in out
    assert "snap_trend_daily_rsi_bucket" in out
    assert s8.dna_fields({}) == {}
```

- [ ] **Step 2: Run to verify failure:**

Run: `venv/bin/python -m pytest tests/test_s8_exits.py -v`
Expected: FAIL with `AttributeError: ... no attribute 'compute_and_place_long_exits'`

- [ ] **Step 3: Append to `strategies/s8.py`:**

```python
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
```

- [ ] **Step 4: Run tests:**

Run: `venv/bin/python -m pytest tests/test_s8_exits.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add strategies/s8.py tests/test_s8_exits.py
git commit -m "feat(s8): S2-style exits (green-low SL floor + 5% cap), swing trail, DNA fields"
```

---

### Task 5: `trader.py` + `paper_trader.py` dispatch

**Files:**
- Modify: `trader.py` (`open_long` — kwargs at ~line 360, strategy map at ~378, exit branch at ~424)
- Modify: `paper_trader.py` (~line 157 strategy tuple)
- Test: extend `tests/test_s8_exits.py`

- [ ] **Step 1: Add failing test** to `tests/test_s8_exits.py`:

```python
def test_trader_open_long_dispatches_s8(monkeypatch):
    """strategy="S8" routes to strategies.s8.compute_and_place_long_exits."""
    import trader
    monkeypatch.setattr(trader, "get_usdt_balance", lambda: 1000.0)
    monkeypatch.setattr(trader, "_get_total_equity", lambda: 1000.0)
    monkeypatch.setattr(trader, "get_mark_price", lambda s: 100.0)
    monkeypatch.setattr(trader, "_round_qty", lambda q, s: str(round(q, 3)))
    monkeypatch.setattr(trader, "_round_price", lambda p, s: f"{p:.5f}")
    monkeypatch.setattr(trader, "set_leverage", lambda s, l: None)
    monkeypatch.setattr(trader, "get_all_open_positions",
                        lambda: {"ABCUSDT": {"entry_price": 100.0}})
    monkeypatch.setattr(trader.bc, "post", lambda *a, **k: {})
    monkeypatch.setattr("time.sleep", lambda s: None)
    called = {}
    import strategies.s8 as s8mod
    monkeypatch.setattr(s8mod, "compute_and_place_long_exits",
                        lambda sym, qty, fill, slf, pct: called.update(
                            dict(sym=sym, fill=fill, slf=slf, pct=pct)) or (True, 97.0, 110.0))
    res = trader.open_long("ABCUSDT", sl_floor=97.0, leverage=10,
                           trade_size_pct=0.04, stop_loss_pct=0.05, strategy="S8")
    assert called["slf"] == 97.0
    assert called["pct"] == 0.05
    assert res["sl"] == 97.0
```

Run: `venv/bin/python -m pytest tests/test_s8_exits.py::test_trader_open_long_dispatches_s8 -v`
Expected: FAIL (S8 falls into the generic `else` branch, `called` stays empty → KeyError)

If the monkeypatching of `trader.bc.post` doesn't match how existing tests stub the HTTP layer, mirror `tests/test_trader_exits.py`'s fixtures instead — do not invent a new stubbing style.

- [ ] **Step 2: Edit `trader.py` `open_long`** — three edits:

(a) kwargs block — after `use_s7_exits: bool     = False,` add:

```python
    use_s8_exits: bool     = False,
```

(b) strategy map — after `elif strategy == "S7": use_s7_exits = True` add:

```python
    elif strategy == "S8": use_s8_exits = True
```

(c) exit dispatch — after the `elif use_s7_exits:` block add:

```python
    elif use_s8_exits:
        from strategies.s8 import compute_and_place_long_exits as _s8_long_exits
        ok, sl_trig, tp_trig = _s8_long_exits(symbol, qty, fill, sl_floor, stop_loss_pct)
```

Do NOT touch `open_short` — S8 is LONG only.

- [ ] **Step 3: Edit `paper_trader.py`** line ~157: change

```python
    if strategy in ("S2", "S3", "S5", "S7"):
```

to

```python
    if strategy in ("S2", "S3", "S5", "S7", "S8"):
```

(LONG path only; leave the SHORT tuple at ~227 alone.)

- [ ] **Step 4: Run tests:**

Run: `venv/bin/python -m pytest tests/test_s8_exits.py tests/test_trader_exits.py -v`
Expected: ALL PASS (including pre-existing trader tests — no regression)

- [ ] **Step 5: Commit**

```bash
git add trader.py paper_trader.py tests/test_s8_exits.py
git commit -m "feat(s8): dispatch S8 exits in trader.open_long and paper trail in paper_trader"
```

---

### Task 6: `bot.py` wiring

**Files:**
- Modify: `bot.py` (all anchors below are greppable strings at HEAD)

- [ ] **Step 1: Imports.** After `import config_s7` add `import config_s8`. After `from strategies.s7 import evaluate_s7` add `from strategies.s8 import evaluate_s8`.

- [ ] **Step 2: Strategy enumeration tuples.** Three greps, add `"S8"`/`S8` to each:
- `st.add_scan_log("Bot initialised (S1 + S2 + S3 + S4 + S5 + S6 + S7)"` → `... + S7 + S8)`
- `ap.get("strategy") not in ("S1", "S2", "S3", "S4", "S5", "S6", "S7")` → append `"S8"`
- `ap.get("strategy") in ("S1", "S2", "S3", "S4", "S5", "S6", "S7")` → append `"S8"`
- Pending-tick dispatch: `if strategy in ("S2", "S3", "S4", "S5", "S6", "S7"):` → append `"S8"` (the `importlib.import_module(f"strategies.{strategy.lower()}")` delegation then works unchanged).

- [ ] **Step 3: Swing-trail dispatch.** Find `elif _strat in ("S2", "S4", "S7"):` (the per-tick `maybe_trail_sl` dispatch). Append `"S8"` to the tuple and add a branch below the `elif _strat == "S7":` one:

```python
                        elif _strat == "S8":
                            from strategies.s8 import maybe_trail_sl as _trail_s8
                            _trail_s8(sym, ap, tr, st, _partial_done)
```

**Verify while there:** the scale-in machinery keys off `ap.get("scale_in_pending")`, which `_fire_s8` never sets — S8 must NOT appear in any scale-in-specific tuple. If the `("S2", "S4", "S7")` tuple you found gates BOTH scale-in and swing trail, read the surrounding block carefully: only the swing-trail dispatch gets S8.

- [ ] **Step 4: Scan-loop evaluation.** After the S7 evaluation block (anchor: `s7_sr_res_pct = round(` ... end of that if/elif), insert:

```python
        # ── Strategy 8 (post-S2 bounce at tri-confluence) ────────── #
        (s8_sig, s8_rsi, s8_trigger, s8_green_low, s8_zone_low, s8_zone_high,
         s8_box_top, s8_ma20, s8_fib618, s8_reason) = (
            "HOLD", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, ""
        )
        if config_s8.S8_ENABLED and self.sentiment.direction == "BULLISH":
            (s8_sig, s8_rsi, s8_trigger, s8_green_low, s8_zone_low, s8_zone_high,
             s8_box_top, s8_ma20, s8_fib618, s8_reason) = evaluate_s8(symbol, daily_df)
            logger.info(f"[S8][{symbol}] {s8_reason}")
```

- [ ] **Step 5: Pair-state fields.** In the `update_pair_state` dict (anchor: `"s7_sr_resistance_pct": s7_sr_res_pct,`), add after the s7 block:

```python
            "s8_signal":     s8_sig,
            "s8_reason":     s8_reason,
            "s8_trigger":    s8_trigger if s8_trigger > 0 else None,
            "s8_green_low":  s8_green_low if s8_green_low > 0 else None,
            "s8_zone_low":   s8_zone_low if s8_zone_low > 0 else None,
            "s8_zone_high":  s8_zone_high if s8_zone_high > 0 else None,
            "s8_box_top":    s8_box_top if s8_box_top > 0 else None,
            "s8_ma20":       s8_ma20 if s8_ma20 > 0 else None,
            "s8_fib618":     s8_fib618 if s8_fib618 > 0 else None,
            "s8_daily_rsi":  round(s8_rsi, 1) if s8_rsi else None,
```

Also extend the aggregate `"signal"` and `"strategy"` ternary chains: insert `s8_sig` / `"S8"` right after the `s7_sig` / `"S7"` position in each chain (keep the existing fallback order otherwise).

- [ ] **Step 6: Candidate collection.** After the `# ── Collect S7 candidate ──` block, add:

```python
        # ── Collect S8 candidate ──────────────────────────────────── #
        if s8_sig == "LONG" and s8_trigger > 0:
            s8_rr = round(config_s8.S8_TAKE_PROFIT_PCT / config_s8.S8_STOP_LOSS_PCT, 2)
            self.candidates.append({
                "strategy": "S8", "symbol": symbol, "sig": "LONG",
                "rr": s8_rr, "sr_pct": None,
                "s8_trigger": s8_trigger, "s8_green_low": s8_green_low,
                "s8_zone_low": s8_zone_low, "s8_zone_high": s8_zone_high,
                "s8_box_top": s8_box_top, "s8_ma20": s8_ma20, "s8_fib618": s8_fib618,
                "s8_rsi": s8_rsi, "s8_reason": s8_reason, "daily_df": daily_df,
            })
```

- [ ] **Step 7: `_execute_best_candidate` queue branch.** After the `elif strategy == "S7":` branch add:

```python
            elif strategy == "S8":
                if sym not in self.pending_signals:
                    min_bal = 5.0 / (config_s8.S8_TRADE_SIZE_PCT * config_s8.S8_LEVERAGE)
                    if balance >= min_bal:
                        self._queue_s8_pending(candidate)
```

- [ ] **Step 8: Delegates + fire.** Next to `_queue_s7_pending` add:

```python
    def _queue_s8_pending(self, c: dict) -> None:
        """Delegate to strategies.s8.queue_pending."""
        from strategies.s8 import queue_pending
        queue_pending(self, c)
```

After `_fire_s7` add (mirrors `_fire_s2`/`_fire_s3` at HEAD — note HEAD's inline `dna_snapshot(..., {"daily": df})` pattern, NOT `_dna_candles` which only exists in uncommitted WIP):

```python
    def _fire_s8(self, symbol: str, sig: dict, mark: float, balance: float) -> None:
        """Open S8 LONG at fire time. Single full entry, no scale-in."""
        sl_floor = max(sig["s8_green_low"] * 0.999,
                       mark * (1 - config_s8.S8_STOP_LOSS_PCT))
        if config.CLAUDE_FILTER_ENABLED:
            _cd = claude_approve("S8", symbol, {
                "RSI@breakout": sig.get("snap_daily_rsi", "?"),
                "Confluence width": f"{sig.get('snap_box_range_pct', '?')}%",
                "Sentiment": sig.get("snap_sentiment", "?"),
                "Entry": round(mark, 5), "SL": round(sl_floor, 5),
            })
            if not _cd["approved"]:
                logger.info(f"[S8][{symbol}] 🤖 Claude rejected: {_cd['reason']}")
                st.add_scan_log(f"[S8][{symbol}] 🤖 Rejected: {_cd['reason']}", "WARN")
                self.pending_signals.pop(symbol, None)
                st.save_pending_signals(self.pending_signals)
                return
        size_multiplier = get_position_size_multiplier()
        adjusted_size = config_s8.S8_TRADE_SIZE_PCT * size_multiplier
        size_note = f" ({size_multiplier}x)" if size_multiplier != 1.0 else ""
        st.add_scan_log(f"[S8][{symbol}] 🟢 LONG fired @ {mark:.5f}{size_note}", "SIGNAL")
        trade = tr.open_long(
            symbol, box_low=sig.get("s8_zone_low", 0), sl_floor=sl_floor,
            leverage=config_s8.S8_LEVERAGE,
            trade_size_pct=adjusted_size,
            take_profit_pct=config_s8.S8_TAKE_PROFIT_PCT,
            stop_loss_pct=config_s8.S8_STOP_LOSS_PCT,
            strategy       = "S8",
        )
        trade["strategy"]           = "S8"
        trade["box_high"]           = sig.get("s8_zone_high")
        trade["snap_daily_rsi"]     = sig.get("snap_daily_rsi")
        trade["snap_box_range_pct"] = sig.get("snap_box_range_pct")
        trade["snap_entry_trigger"] = sig.get("s8_trigger")
        trade["snap_sl"]            = round(sl_floor, 8)
        trade["snap_sentiment"]     = sig.get("snap_sentiment")
        trade["trade_id"] = uuid.uuid4().hex[:8]
        _daily_df = None
        try:
            _daily_df = tr.get_candles(symbol, "1D", limit=100)
        except Exception:
            pass
        trade.update(dna_snapshot("S8", symbol, {"daily": _daily_df}))
        self._log_regime(symbol, _daily_df, trade)
        _log_trade("S8_LONG", trade)
        st.add_open_trade(trade)
        try:
            _candles = _df_to_candles(_daily_df) \
                if _daily_df is not None and not _daily_df.empty else []
            snapshot.save_snapshot(
                trade_id=trade["trade_id"], event="open",
                symbol=symbol, interval="1D", candles=_candles,
                event_price=float(trade.get("entry", 0)),
            )
        except Exception as e:
            logger.warning(f"[S8][{symbol}] snapshot save failed: {e}")
        if PAPER_MODE: tr.tag_strategy(symbol, "S8")
        self.active_positions[symbol] = {
            "side": "LONG", "strategy": "S8",
            "box_high": sig.get("s8_zone_high"), "box_low": sig.get("s8_zone_low"),
            "trade_id": trade["trade_id"],
        }
```

Check `self._log_regime(...)`'s signature at HEAD before writing the call (it may take the df or interval differently) and match it exactly as `_fire_s2` does.

- [ ] **Step 9: Verify imports + existing watcher tests:**

Run: `venv/bin/python -c "import bot; print('Bitget OK')" && venv/bin/python -m pytest tests/test_bot_entry_watcher_all.py tests/test_state_pending_signals.py -v`
Expected: `Bitget OK`, ALL PASS

- [ ] **Step 10: Commit**

```bash
git add bot.py
git commit -m "feat(s8): wire post-S2 bounce into shared bot loop (scan, queue, fire, trail)"
```

---

### Task 7: trade_dna + analytics + optimize + dashboard

**Files:**
- Modify: `trade_dna.py` (`_get_handler`), `analytics.py` (lines 16–27), `optimize.py` (`STRATEGY_COLUMNS`), `dashboard.html` (anchors below)
- Test: extend `tests/test_s8_exits.py` (dna dispatch) — dashboard/analytics covered by `tests/test_analytics.py` conventions

- [ ] **Step 1: `trade_dna.py`** — in `_get_handler`, after the S7 branch add:

```python
    elif strategy == "S8":
        from strategies.s8 import dna_fields
```

- [ ] **Step 2: `analytics.py`:**

```python
STRATEGIES = ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8")
```

and in `STRATEGY_SNAP_FIELDS` add (note: S7 has no entry there either — but S8's reused columns are generic, so add it):

```python
    "S8": ("snap_daily_rsi", "snap_entry_trigger", "snap_sl", "snap_box_range_pct"),
```

- [ ] **Step 3: `optimize.py`** — in `STRATEGY_COLUMNS` add:

```python
    "S8": ["result", "pnl_pct", "exit_reason",
           "snap_daily_rsi", "snap_entry_trigger", "snap_sl",
           "snap_box_range_pct", "snap_sentiment"],
```

- [ ] **Step 4: `dashboard.html`** — mirror every S7 occurrence (grep `s7`/`S7`):
- `<button class="strat-tab" id="tab-s7" ...>` → add `<button class="strat-tab" id="tab-s8" onclick="switchTab('s8')">S8 — Bounce</button>`
- `<div class="stab" data-s="S7">S7</div>` → add S8 sibling
- `<div class="strategy-panel" data-s="S7"></div>` → add S8 sibling
- both `['S1','S2','S3','S4','S5','S6','S7']` JS arrays → append `'S8'`
- `STRATEGY_SNAP_COLS`-style map (anchor `S7: ['snap_rsi_peak',...]`) → add `S8: ['snap_daily_rsi','snap_entry_trigger','snap_sl','snap_box_range_pct'],`
- `_ENTRY_CHART_INTERVAL` → add `S8:'1D'`
- `tabMap` → add `S8: 's8'`
- the `if (tab === 's7') {...}` scanner-detail block → add an `s8` block rendering `ps.s8_reason`, `ps.s8_signal`, zone bounds, trigger (copy the s7 block, swap fields)
- `s7CardHTML` → add `s8CardHTML(sym, ps)` (copy s7CardHTML, render `s8_signal`, `s8_reason`, `s8_zone_low`/`s8_zone_high`, `s8_box_top`, `s8_ma20`, `s8_fib618`, `s8_trigger`) and the `activeTab === 's7'` render branch → add `s8` branch
- `chartActiveTab === 's7'` interval selector + `strat === 'S7'` mapping → add s8 with `'1D'`

- [ ] **Step 5: Run analytics + dna tests:**

Run: `venv/bin/python -m pytest tests/test_analytics.py tests/test_analytics_endpoint.py tests/test_trade_dna.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add trade_dna.py analytics.py optimize.py dashboard.html
git commit -m "feat(s8): analytics/dashboard/optimizer/DNA integration"
```

---

### Task 8: Documentation

**Files:**
- Create: `docs/strategies/S8.md`
- Modify: `docs/DEPENDENCIES.md`, spec (CSV-reuse note)

- [ ] **Step 1: Write `docs/strategies/S8.md`** following the exact section structure of `docs/strategies/S2.md` (Parameters table from `config_s8.py`; When to Enter = spec §2–§3; When to Exit = spec §4; state.json Effects = the `s8_*` field list from Task 6 Step 5; trades.csv Columns = the reused-column mapping: `snap_daily_rsi`=RSI at breakout day B, `snap_entry_trigger`=stop-buy trigger, `snap_sl`=initial SL, `snap_box_range_pct`=confluence zone width %, `box_low`/`box_high`=zone bounds; Active Trade Behavior = PnL sync, partial-TP detection, optional swing trail, close detection — NO scale-in; Pair Scanner Display = s8 dashboard fields; Trade History on Exit = same pause rule as S2).

- [ ] **Step 2: Update `docs/DEPENDENCIES.md`:**
- §2.1: add `strategies/s8.py` to the per-strategy file list and the cross-bot shared list; note the s2 exit-primitive import exception now covers s7→s4 AND s8→s2.
- §4.1: add the `s8_*` pair-state fields block (copy format of the S7 fields block).
- §4.2: note S8 reuses existing snap columns (no CSV header change), list the mapping.
- §7.1 table: add row `| S8 | Bitget/Bybit/Binance | strategies/s8.py | evaluate_s8 | compute_and_place_long_exits | — | _place_partial_trail_exits (from s2) | maybe_trail_sl (LONG) |`.
- §9.1: update "Adding a new strategy" example if it references S8 as hypothetical.
- §4.5 regime sidecar scope note: S8 daily (extend the "S2/S4/S6/S7 daily" list).

- [ ] **Step 3: Amend the spec** (`docs/superpowers/specs/2026-06-12-s8-post-s2-bounce-design.md`) §7: replace the new-column list with the reused-column mapping + rationale ("trades.csv contract frozen; DictWriter fieldnames must match the live CSV header — new columns would require migrate_trades_csv.py").

- [ ] **Step 4: Commit**

```bash
git add docs/strategies/S8.md docs/DEPENDENCIES.md docs/superpowers/specs/2026-06-12-s8-post-s2-bounce-design.md
git commit -m "docs(s8): strategy doc + dependency map updates"
```

---

### Task 9: Full verification

- [ ] **Step 1: Full test suite:**

Run: `venv/bin/python -m pytest tests/ -x -q`
Expected: ALL PASS (use the qa-trading-bot skill loop: fix source, never tests, re-run until clean)

- [ ] **Step 2: All three crypto bot import checks** (per PRE_CHANGE_CHECKLIST Step 4):

```bash
venv/bin/python -c "import bot; print('Bitget OK')"
venv/bin/python -c "import bybit_bot" 2>&1 | head -3   # alias bootstrap; OK if it only complains about missing API keys
venv/bin/python -c "import binance_bot" 2>&1 | head -3
venv/bin/python -c "import ig_bot" 2>&1 | head -3       # must be untouched by S8
```

Expected: Bitget OK; Bybit/Binance/IG fail only on credentials (if at all), never on `ModuleNotFoundError: config_bybit_s8` or any S8 symbol.

- [ ] **Step 3: Evaluator smoke on live-shaped data** (offline, from a CSV/parquet of real candles if available; otherwise skip — unit tests cover the logic).

- [ ] **Step 4: Commit any fixes, then push branch + open PR:**

```bash
git push -u origin feat/s8-post-s2-bounce
gh pr create --title "feat(s8): post-S2 bounce strategy (tri-confluence retest)" --body "..."
```

PR body: summary of spec, link to `docs/strategies/S8.md`, note "no trades.csv header change", note the `_DNA_TIMEFRAMES` merge-note for the in-flight DNA branch.
