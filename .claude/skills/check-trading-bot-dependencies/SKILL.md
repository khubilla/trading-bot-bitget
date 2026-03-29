---
name: check-trading-bot-dependencies
description: Use before planning or coding any change to the trading bot — reads PRE_CHANGE_CHECKLIST.md and DEPENDENCIES.md to identify what will break. Runs BEFORE superpowers brainstorming, writing-plans, or subagent-driven-development.
---

# Check Trading Bot Dependencies

**This skill runs FIRST — before brainstorming, before planning, before any code.**

## Workflow Position

```
check-trading-bot-dependencies   ← YOU ARE HERE (always first)
        ↓
brainstorming (if design needed) ← superpowers, informed by findings above
        ↓
writing-plans                    ← superpowers
        ↓
subagent-driven-development      ← superpowers
```

The dependency check findings feed directly into brainstorming context. If a brainstorm or plan would touch shared files, broken contracts, or cross-bot dependencies, those constraints must be in scope from the start — not discovered mid-implementation.

## When to Use

- Any time the user asks to change, add, fix, or refactor anything in this codebase
- Before invoking brainstorming or writing-plans
- Even for "small" changes — cross-bot breakage is usually invisible until runtime

## Steps

**Step 1: Read the checklist**

Read `./docs/PRE_CHANGE_CHECKLIST.md` in full.

**Step 2: Classify the change**

Using Steps 1–2 of the checklist, identify:
- Which file category is being changed (shared file, bot-specific, config, data contract, dashboard, optimizer)
- Which change type applies (function signature, return value, config param, state field, CSV column, strategy enable/disable)

**Step 3: Look up relevant sections**

Using Step 3 of the checklist, read the relevant sections from `./docs/DEPENDENCIES.md`:
- **Shared file change** → Section 2 (Shared Files)
- **State/JSON change** → Section 4 (Data Contracts)
- **Config change** → Section 5 (Config Dependencies)
- **Strategy change** → Section 7 (Strategy Implementations)
- **Confusing name or file** → Section 10 (Confusing Names & Pitfalls)

**Step 4: Report findings before proceeding**

Output this report, then proceed to brainstorming or coding:

```
## Dependency Check

**Change type:** [what's being changed]
**Affected sections:** [which DEPENDENCIES.md sections are relevant]

**Who else uses this:**
- [file:line — what breaks if you change it]
- [file:line — what breaks if you change it]

**Data contracts affected:**
- [any state.json fields, CSV columns, or return values that consumers depend on]

**Both bots affected?** [Yes/No — explain which]

**Constraints for brainstorm/plan:**
- [anything the design or implementation must not break]

**Safe to proceed?** [Yes / Yes with caution: X / No: must fix Y first]
```

The "Constraints for brainstorm/plan" section is carried forward as hard requirements into any superpowers brainstorming or plan that follows.

## Red Flags — STOP if you see these

- Changing `evaluate_s5()` signature → breaks both `bot.py` AND `ig_bot.py`
- Changing state.json fields → breaks dashboard.py and dashboard.html
- Changing CSV columns → breaks optimize.py or optimize_ig.py
- Editing `config_s5.py` → affects Bitget immediately, IG silently ignores unless `config_ig_s5.py` is also updated
- Editing `strategy.py` → shared by both bots AND backtest.py
- Confusing `paper_state.json` with `state_paper.json` → completely different purposes (see Section 10)

## The Rule

```
NO BRAINSTORM. NO PLAN. NO CODE. UNTIL DEPENDENCY CHECK IS DONE.
```
