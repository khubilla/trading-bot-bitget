---
name: verify-strategy-revision
description: Mandatory skill before any change to strategy logic, entry conditions, exit rules, or parameters. Reads the strategy's .md file as the source of truth, checks for breaking changes, and flags terminology or concept drift.
---

# Verify Strategy Revision

**This skill runs BEFORE any code change to a strategy.**

Use this skill whenever the task involves:
- Changing entry conditions for any strategy (S1–S6)
- Changing exit logic (SL, TP, trailing, partial TP)
- Adding, removing, or renaming parameters in any `config_s*.py`
- Modifying the evaluate function in `strategy.py` for any strategy
- Changing how state.json or trades.csv is updated for a strategy
- Changing scale-in logic for S2 or S4
- Changing the S5 pending/limit order or entry watcher logic
- Changing the S6 two-phase watcher logic

---

## Step 1: Read the Strategy's .md file

Read the relevant strategy file from `docs/strategies/`:

| Strategy | File |
|----------|------|
| S1 | `docs/strategies/S1.md` |
| S2 | `docs/strategies/S2.md` |
| S3 | `docs/strategies/S3.md` |
| S4 | `docs/strategies/S4.md` |
| S5 | `docs/strategies/S5.md` |
| S6 | `docs/strategies/S6.md` |
| General concepts | `docs/strategies/GENERAL_CONCEPTS.md` |

**Read every section of the relevant strategy file. Do not skim.**

---

## Step 2: Read the General Concepts file

Read `docs/strategies/GENERAL_CONCEPTS.md` fully.

Check whether the proposed change:
- Contradicts any rule in §1 (Darvas Box), §2 (Body vs Wick), §3 (Swing High/Low), §4 (S/R), §5 (Trailing Stop)
- Changes the canonical signal values (§13) — never use "BUY", "SELL", etc.
- Affects any shared concept (partial TP %, trailing logic, sentiment gate, pair pause, scale-in)

---

## Step 3: Check for Breaking Changes

Ask the following questions about the proposed change. Flag any "YES" as a **critical risk**:

### Entry Logic
- [ ] Does this change when a signal fires? (affects pair scanner display immediately)
- [ ] Does this change what `evaluate_s*()` returns? (affects callers in `bot.py`)
- [ ] Does this change the return tuple structure or ordering? (BREAKING — all callers unpack by position)
- [ ] Does this add or remove a filter that could cause trades to fire that previously didn't, or vice versa?

### Exit Logic
- [ ] Does this change how the SL is calculated at entry?
- [ ] Does this change the partial TP trigger level?
- [ ] Does this change which exit order type is placed (profit_plan vs moving_plan)?
- [ ] Does this change the trailing callback %?

### Parameters
- [ ] Is a parameter being renamed in `config_s*.py`? → Update all references in `strategy.py` and `bot.py`
- [ ] Is a new parameter added? → Confirm it is imported where used and has a sensible default
- [ ] Is `S5_*` being added to `config_s5.py`? → **Also add to `config_ig_us30.py`, `config_ig_gold.py`, and the `cfg is not None` block in `evaluate_s5()`** (see DEPENDENCIES.md §10.3)

### State / CSV
- [ ] Does this change which `pair_states` fields are written? → Check `dashboard.html` and `dashboard.py`
- [ ] Does this add or remove a CSV column? → Check `_TRADE_FIELDS` in `bot.py` line 102
- [ ] Does this rename an existing state field? → Dashboard will crash with undefined errors

### S5 Specific
- [ ] Does this change the `entry_trigger` assignment? (`ob_high` for LONG, `ob_low` for SHORT — must stay stable)
- [ ] Does this change the `PENDING_LONG` / `PENDING_SHORT` signal semantics? → Entry watcher polling depends on these exact values
- [ ] Does this change the OB invalidation condition? → Affects when pending signals are cancelled

### S6 Specific
- [ ] Does this change `peak_level`? → `s6_fakeout_seen` logic in `_process_s6_watchers()` depends on it
- [ ] Does this change what triggers Phase 1 (fakeout seen) or Phase 2 (entry)?

---

## Step 4: Verify Terminology

The strategy .md files define the authoritative terms. Enforce these throughout the change:

| Term | Meaning | Never confuse with |
|------|---------|-------------------|
| **Coil** | Tight consolidation zone after an impulse | "range", "box" in different context |
| **Consolidation** | Synonym for coil | "ranging" (ranging = broader, no breakout setup) |
| **Darvas Box** | Specific coil detection method with body/wick rule | Generic "box" |
| **Order Block (OB)** | Last opposing candle before an impulse (S5 specific) | S/R level, pivot |
| **Break of Structure (BOS)** | Close above prior swing high / below prior swing low | "new high", "breakout" |
| **ChoCH** | Change of Character (removed from S5 in 2026-03-31) | Do NOT use this term for the S5 entry — it now uses a limit order at ob_high/ob_low |
| **Swing High/Low** | Candle with high/low strictly greater/less than both neighbours | "pivot high/low" (similar but defined differently in some contexts) |
| **Peak Level (S6)** | High of the swing-high candle that forms the V-formation top | Not the same as general "resistance" |
| **Fakeout (S6)** | Price sweeping above `peak_level` in Phase 1 | "breakout" (true breakout would not come back down) |
| **Impulse** | A run of 2+ consecutive same-direction candles moving price ≥1% | Single large candle |
| **S/R Clearance** | % distance from entry to nearest resistance (LONG) or support (SHORT) | R:R |
| **PENDING_LONG/SHORT** | S5 and S6 signal states awaiting price confirmation | "LONG"/"SHORT" (which mean immediate market entry) |

---

## Step 5: Update the Strategy .md File

After implementing the change, **update the strategy's .md file** to reflect the new behavior:

- If entry conditions changed → update §2 (When to Enter)
- If exit logic changed → update §3 (When to Exit)
- If `pair_states` fields changed → update §4 (state.json Effects)
- If CSV columns changed → update §5 (trades.csv Columns)
- If parameters changed → update §1 (Parameters table)

**The .md file is the source of truth. Code and documentation must stay in sync.**

Also update `docs/strategies/GENERAL_CONCEPTS.md` if the change affects any shared rule (trailing stop %, partial TP %, sentiment gate, etc.).

---

## Step 6: Output Your Findings

Before writing any code, report:

```
## Strategy Revision Check

**Strategy:** S[N] — [Name]
**Change summary:** [one-line description]

**Strategy .md reviewed:** ✅ / ❌
**General concepts .md reviewed:** ✅ / ❌

**Conflicts with strategy spec:**
- [list any contradictions or NONE]

**Breaking changes detected:**
- [list breaking changes or NONE]

**Terminology check:**
- [list any misused terms or NONE]

**Parameters affected:**
- [list all config params added/changed/removed]

**State/CSV fields affected:**
- [list all state.json or CSV changes]

**Cross-bot impact (IG bot):**
- [Yes/No — explain if S5 params changed]

**Strategy .md update required:** Yes / No
**GENERAL_CONCEPTS.md update required:** Yes / No

**Safe to proceed?** Yes / Yes with caution: [X] / No: must resolve [Y]
```

---

## The Rule

```
NO CODE CHANGE TO STRATEGY LOGIC UNTIL:
1. Strategy .md file is read and understood
2. GENERAL_CONCEPTS.md is read
3. Breaking change check is complete
4. Report is output

NO COMMIT UNTIL:
The relevant strategy .md file reflects the final implementation.
```
