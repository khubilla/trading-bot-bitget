# S1 Anchored-Box Breakout Entry — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace S1's per-tick sliding 2-bar coil window with a stateful two-phase watcher that anchors the consolidation box when it forms and enters on the first candle that closes beyond it + 0.5%.

**Architecture:** A pure decision function `s1_anchor_decision()` in `strategies/s1.py` holds the arm/fire/disarm logic and is unit-tested in isolation. The `bot.py` scan loop holds a per-symbol in-memory `self.s1_armed` dict, computes the inputs (gates, last-closed candle, RSI), calls the function, and — when `S1_ANCHOR_BOX` is on — uses its result instead of `evaluate_s1`'s sliding breakout signal. `evaluate_s1`'s signature/return are untouched, so IG (`ig_bot.py`) and `backtest.py` keep the old behavior.

**Tech Stack:** Python 3.14, pandas, pytest.

## Global Constraints

- Do **not** change `evaluate_s1` / `check_ltf_long` / `check_ltf_short` signatures, return tuples, or logic.
- Do **not** change `trades.csv` columns, `state.json` / `pair_states` fields, or add `cfg`-path keys.
- Armed-box state is **in-memory only** (never serialized) — consistent with `swing_trail_ref`.
- New behavior is **crypto bots only** (`bot.py` loop, shared by `bybit_bot.py` / `binance_bot.py`). IG and backtest untouched.
- `S1_ANCHOR_BOX = False` must preserve today's sliding behavior bit-for-bit.
- Coil params unchanged: `CONSOLIDATION_CANDLES = 2`, `CONSOLIDATION_RANGE_PCT = 0.04`, `BREAKOUT_BUFFER_PCT = 0.005`.
- Never modify existing tests (per `qa-trading-bot`). Source-only fixes if a test fails.
- Source of truth is `docs/superpowers/specs/2026-06-26-s1-anchored-box-breakout-entry-design.md`.

---

## File Structure

- `config_s1.py`, `config_bybit_s1.py`, `config_binance_s1.py` — add two constants each (Task 1).
- `strategies/s1.py` — add pure `s1_anchor_decision()` (Task 2).
- `tests/test_s1_anchor_box.py` — new unit tests for the function (Task 2).
- `bot.py` — init `self.s1_armed`, wire the watcher, clear on open (Task 3).
- `docs/strategies/S1.md`, `docs/strategies/GENERAL_CONCEPTS.md`, `docs/DEPENDENCIES.md` — doc updates (Task 4).

---

### Task 1: Add config constants to the three crypto configs

**Files:**
- Modify: `config_s1.py`, `config_bybit_s1.py`, `config_binance_s1.py` (after the `BREAKOUT_BUFFER_PCT` line, ~line 34)
- Test: `tests/test_s1_anchor_box.py`

**Interfaces:**
- Produces: `config_s1.S1_ANCHOR_BOX: bool`, `config_s1.S1_BOX_MAX_AGE: int` (and identical in the two exchange copies).

- [ ] **Step 1: Write the failing test**

Create `tests/test_s1_anchor_box.py` with:

```python
import importlib


def test_anchor_box_config_defaults():
    for mod_name in ("config_s1", "config_bybit_s1", "config_binance_s1"):
        mod = importlib.import_module(mod_name)
        assert mod.S1_ANCHOR_BOX is True
        assert mod.S1_BOX_MAX_AGE == 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_s1_anchor_box.py::test_anchor_box_config_defaults -v`
Expected: FAIL with `AttributeError: module 'config_s1' has no attribute 'S1_ANCHOR_BOX'`

- [ ] **Step 3: Add the constants**

In each of `config_s1.py`, `config_bybit_s1.py`, `config_binance_s1.py`, immediately after the `BREAKOUT_BUFFER_PCT = 0.005` line, add:

```python
# ── Anchored-box breakout entry ─────────────────────────────── #
# When True, S1 anchors the consolidation box when it forms and enters on
# the first candle that closes beyond it + BREAKOUT_BUFFER_PCT, instead of
# the legacy per-tick sliding 2-bar window. False = legacy sliding behavior.
S1_ANCHOR_BOX = True
S1_BOX_MAX_AGE = 10   # 3m candles an unbroken armed box survives before expiring
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_s1_anchor_box.py::test_anchor_box_config_defaults -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add config_s1.py config_bybit_s1.py config_binance_s1.py tests/test_s1_anchor_box.py
git commit -m "feat(s1): add S1_ANCHOR_BOX / S1_BOX_MAX_AGE config to crypto configs"
```

---

### Task 2: Pure `s1_anchor_decision()` arm/fire/disarm function

**Files:**
- Modify: `strategies/s1.py` (add function after `check_ltf_short`, ~line 248)
- Test: `tests/test_s1_anchor_box.py`

**Interfaces:**
- Produces:
```python
def s1_anchor_decision(
    armed: dict | None, *, direction: str, last_close: float, last_ts: int,
    rsi_val: float, rsi_thresh: float, gates_ok: bool, is_coil: bool,
    box_high: float, box_low: float, buffer_pct: float,
    interval_ms: int, max_age: int,
) -> tuple[dict | None, str]:
    # returns (new_armed_state_or_None, signal) where signal in {"LONG","SHORT","HOLD"}
```
  The armed dict shape is `{"dir","box_high","box_low","rsi_thresh","armed_at_ts"}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_s1_anchor_box.py`:

```python
from strategies.s1 import s1_anchor_decision

IV = 180000  # 3m in ms

def _arm_kwargs(**over):
    base = dict(
        direction="LONG", last_close=100.0, last_ts=IV * 10,
        rsi_val=75.0, rsi_thresh=70.0, gates_ok=True, is_coil=True,
        box_high=101.0, box_low=99.5, buffer_pct=0.005,
        interval_ms=IV, max_age=10,
    )
    base.update(over)
    return base


def test_arm_when_valid_coil_and_inside_box():
    armed, sig = s1_anchor_decision(None, **_arm_kwargs())
    assert sig == "HOLD"
    assert armed is not None
    assert armed["dir"] == "LONG"
    assert armed["box_high"] == 101.0 and armed["box_low"] == 99.5
    assert armed["armed_at_ts"] == IV * 10


def test_no_arm_when_no_coil():
    armed, sig = s1_anchor_decision(None, **_arm_kwargs(is_coil=False))
    assert armed is None and sig == "HOLD"


def test_no_arm_when_already_broken_out():
    # price already above box_high*(1+buffer) → don't arm an extended break
    armed, sig = s1_anchor_decision(None, **_arm_kwargs(last_close=101.6))
    assert armed is None and sig == "HOLD"


def test_no_arm_when_gates_fail():
    armed, sig = s1_anchor_decision(None, **_arm_kwargs(gates_ok=False))
    assert armed is None and sig == "HOLD"


def test_fire_long_on_close_above_anchored_box():
    armed = {"dir": "LONG", "box_high": 101.0, "box_low": 99.5,
             "rsi_thresh": 70.0, "armed_at_ts": IV * 10}
    new, sig = s1_anchor_decision(
        armed, **_arm_kwargs(last_close=101.6, last_ts=IV * 12, is_coil=False))
    assert sig == "LONG"
    assert new is None  # disarmed on fire


def test_no_fire_when_close_inside_buffer():
    armed = {"dir": "LONG", "box_high": 101.0, "box_low": 99.5,
             "rsi_thresh": 70.0, "armed_at_ts": IV * 10}
    new, sig = s1_anchor_decision(
        armed, **_arm_kwargs(last_close=101.3, last_ts=IV * 12, is_coil=False))
    assert sig == "HOLD"
    assert new is armed  # still waiting, box unchanged


def test_disarm_when_rsi_leaves_zone():
    armed = {"dir": "LONG", "box_high": 101.0, "box_low": 99.5,
             "rsi_thresh": 70.0, "armed_at_ts": IV * 10}
    new, sig = s1_anchor_decision(
        armed, **_arm_kwargs(rsi_val=68.0, last_ts=IV * 12, is_coil=False))
    assert new is None and sig == "HOLD"


def test_disarm_on_wrong_way_close():
    armed = {"dir": "LONG", "box_high": 101.0, "box_low": 99.5,
             "rsi_thresh": 70.0, "armed_at_ts": IV * 10}
    new, sig = s1_anchor_decision(
        armed, **_arm_kwargs(last_close=99.0, last_ts=IV * 12, is_coil=False))
    assert new is None and sig == "HOLD"


def test_disarm_on_age_expiry():
    armed = {"dir": "LONG", "box_high": 101.0, "box_low": 99.5,
             "rsi_thresh": 70.0, "armed_at_ts": IV * 10}
    # 11 candles later (> max_age 10), still inside box
    new, sig = s1_anchor_decision(
        armed, **_arm_kwargs(last_close=100.5, last_ts=IV * 21, is_coil=False))
    assert new is None and sig == "HOLD"


def test_disarm_when_gates_flip():
    armed = {"dir": "LONG", "box_high": 101.0, "box_low": 99.5,
             "rsi_thresh": 70.0, "armed_at_ts": IV * 10}
    new, sig = s1_anchor_decision(
        armed, **_arm_kwargs(gates_ok=False, last_ts=IV * 12, is_coil=False))
    assert new is None and sig == "HOLD"


def test_fire_short_on_close_below_anchored_box():
    armed = {"dir": "SHORT", "box_high": 101.0, "box_low": 99.5,
             "rsi_thresh": 30.0, "armed_at_ts": IV * 10}
    new, sig = s1_anchor_decision(
        armed, direction="SHORT", last_close=99.0, last_ts=IV * 12,
        rsi_val=25.0, rsi_thresh=30.0, gates_ok=True, is_coil=False,
        box_high=101.0, box_low=99.5, buffer_pct=0.005, interval_ms=IV, max_age=10)
    assert sig == "SHORT" and new is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_s1_anchor_box.py -v`
Expected: the new tests FAIL with `ImportError: cannot import name 's1_anchor_decision'`

- [ ] **Step 3: Implement the function**

In `strategies/s1.py`, after `check_ltf_short` (~line 248), add:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_s1_anchor_box.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add strategies/s1.py tests/test_s1_anchor_box.py
git commit -m "feat(s1): add pure s1_anchor_decision arm/fire/disarm state machine"
```

---

### Task 3: Wire the watcher into the bot loop

**Files:**
- Modify: `bot.py` — `Bot.__init__` (~line 377), import (line 27), S1 block (after ~line 1424, before line 1640), clear-on-open (after line 2096)
- Verify: `python -c "import bot"` + full suite

**Interfaces:**
- Consumes: `s1_anchor_decision` (Task 2), `config_s1.S1_ANCHOR_BOX`, `config_s1.S1_BOX_MAX_AGE` (Task 1).
- Produces: `Bot.s1_armed: dict[str, dict]` (in-memory).

- [ ] **Step 1: Add the import**

In `bot.py` line 27, extend the existing import:

```python
from strategies.s1 import evaluate_s1, detect_consolidation, check_daily_trend, check_exit, s1_anchor_decision
```

- [ ] **Step 2: Initialize the in-memory state**

In `Bot.__init__` (near `self.pending_signals = st.load_pending_signals()`, ~line 386), add:

```python
        self.s1_armed: dict[str, dict] = {}   # in-memory anchored S1 boxes (not persisted)
```

- [ ] **Step 3: Override s1_sig with the anchored watcher**

In `bot.py`, immediately after the S1 logging block ends (after line 1430, before the S2 block at line 1432), insert:

```python
        # ── S1 anchored-box breakout watcher (crypto only) ───────── #
        if config_s1.S1_ANCHOR_BOX and allowed_direction != "NEUTRAL":
            _dir    = "LONG" if allowed_direction == "BULLISH" else "SHORT"
            _thresh = config_s1.RSI_LONG_THRESH if _dir == "LONG" else config_s1.RSI_SHORT_THRESH
            _iv     = config_s1.LTF_INTERVAL
            _iv_ms  = int(_iv[:-1]) * (60000 if _iv.endswith("m") else 3600000)
            _prev   = self.s1_armed.get(symbol)
            _new_armed, s1_sig = s1_anchor_decision(
                _prev,
                direction=_dir,
                last_close=float(ltf_df["close"].iloc[-2]),
                last_ts=int(ltf_df["ts"].iloc[-2]),
                rsi_val=rsi_val,
                rsi_thresh=_thresh,
                gates_ok=(htf_pass and trend_ok),
                is_coil=is_coil,
                box_high=bh,
                box_low=bl,
                buffer_pct=config_s1.BREAKOUT_BUFFER_PCT,
                interval_ms=_iv_ms,
                max_age=config_s1.S1_BOX_MAX_AGE,
            )
            if _new_armed is None:
                self.s1_armed.pop(symbol, None)
            else:
                self.s1_armed[symbol] = _new_armed
            # On fire, use the anchored box levels for the structural SL.
            if s1_sig in ("LONG", "SHORT") and _prev:
                s1_bh, s1_bl = _prev["box_high"], _prev["box_low"]
```

Note: `htf_pass`, `trend_ok`, `is_coil`, `bh`, `bl`, `rsi_val`, `s1_bh`, `s1_bl` are all already defined above this point (lines 1393-1424). This block reassigns `s1_sig` (and, on fire, `s1_bh`/`s1_bl`) so the existing candidate-collection at line 1641 consumes the anchored decision unchanged.

- [ ] **Step 4: Clear armed state when an S1 trade opens**

In `bot.py`, immediately after the S1 `open_long`/`open_short` block (after line 2096, after the `else:` `open_short` call returns `trade`), add:

```python
        self.s1_armed.pop(symbol, None)
```

(Place it where `c["symbol"]`/`symbol` is in scope for the S1 open path; use the same `symbol` variable used by `open_long` above.)

- [ ] **Step 5: Verify both bots import and the suite is green**

Run:
```bash
python -c "import bot; print('bot OK')"
python -c "import ig_bot; print('ig_bot OK')"
python -m pytest tests/ -q
```
Expected: both import lines print OK; full suite passes (no regressions in existing S1 tests).

- [ ] **Step 6: Commit**

```bash
git add bot.py
git commit -m "feat(s1): wire anchored-box watcher into bot loop behind S1_ANCHOR_BOX"
```

---

### Task 4: Update strategy docs

**Files:**
- Modify: `docs/strategies/S1.md` (§1 table, §2, §6)
- Modify: `docs/strategies/GENERAL_CONCEPTS.md` (§1)
- Modify: `docs/DEPENDENCIES.md` (§5.4 note)

- [ ] **Step 1: Update `S1.md` §1 parameters table**

Add two rows to the table in §1 (after the `S1_SWING_LOOKBACK` row):

```markdown
| `S1_ANCHOR_BOX` | `True` | Anchor the coil box when it forms; enter on first close beyond it (vs legacy per-tick sliding window) |
| `S1_BOX_MAX_AGE` | `10` | 3m candles an unbroken armed box survives before it expires |
```

- [ ] **Step 2: Rewrite `S1.md` §2 Step 5 (Breakout) as the anchored two-phase entry**

Replace the "**Step 5 — 3m Breakout**" block with:

```markdown
**Step 5 — 3m Breakout (anchored, when `S1_ANCHOR_BOX = True`)**

Entry is a two-phase in-memory watcher (`s1_anchor_decision` in `strategies/s1.py`):
- **Arm:** when Steps 1–4 pass and price has not yet broken out, the coil box (`box_high`/`box_low`) is anchored in `self.s1_armed[symbol]` and stops moving.
- **Fire:** on the first 3m candle that *closes* beyond the anchored edge ± `BREAKOUT_BUFFER_PCT` (above `box_high × 1.005` LONG / below `box_low × 0.995` SHORT), the trade opens; the anchored box levels set the structural SL.
- **Disarm (no trade):** RSI leaves the zone, price closes the wrong way out of the box, a macro gate (HTF/daily trend) flips, or the box exceeds `S1_BOX_MAX_AGE` candles.

When `S1_ANCHOR_BOX = False`, the legacy per-tick sliding breakout applies: `current 3m close > box_high × (1 + 0.5%)` (LONG) on the last closed candle.

The watcher is **crypto-bot only** (`bot.py` loop). IG (`ig_bot.py`) and `backtest.py` always use the legacy sliding path. Armed state is **in-memory only** — a restart re-arms from the next valid coil.
```

- [ ] **Step 3: Add note to `GENERAL_CONCEPTS.md` §1**

After the bullet "The box is **not** formed by candles that have already broken out…", add:

```markdown
- **S1 anchoring (3m):** when `S1_ANCHOR_BOX = True`, S1 locks the coil box once it forms and watches that fixed box for a close-confirmed breakout, rather than recomputing a sliding 2-candle box every tick. This prevents entries that chase a re-anchored window several candles into a move.
```

- [ ] **Step 4: Add note to `DEPENDENCIES.md` §5.4**

After the "Index vs FX defaults" paragraph in §5.4, add:

```markdown
**S1 anchored-box entry (crypto only):** `config_s1.py` / `config_bybit_s1.py` / `config_binance_s1.py` carry `S1_ANCHOR_BOX` (default `True`) and `S1_BOX_MAX_AGE` (default `10`). These gate an in-memory watcher in the `bot.py` loop (`Bot.s1_armed`) that overrides `evaluate_s1`'s sliding breakout signal. Not read in the `cfg` path — IG configs and `_validate_instruments` are unaffected. State is not serialized.
```

- [ ] **Step 5: Commit**

```bash
git add docs/strategies/S1.md docs/strategies/GENERAL_CONCEPTS.md docs/DEPENDENCIES.md
git commit -m "docs(s1): document anchored-box breakout entry + config + dependency note"
```

---

### Task 5: Final QA gate

- [ ] **Step 1: Run the QA skill**

Invoke `qa-trading-bot` (runs pytest, auto-fixes source-side failures, never edits tests). Confirm a clean run.

- [ ] **Step 2: Sanity-check the toggle path**

Run:
```bash
python -c "import config_s1; print('anchor:', config_s1.S1_ANCHOR_BOX, 'maxage:', config_s1.S1_BOX_MAX_AGE)"
python -m pytest tests/test_s1_anchor_box.py -v
```
Expected: prints `anchor: True maxage: 10`; all anchor-box tests pass.

- [ ] **Step 3: Final commit (if QA made source fixes)**

```bash
git add -A
git commit -m "test(s1): qa pass for anchored-box breakout entry"
```
