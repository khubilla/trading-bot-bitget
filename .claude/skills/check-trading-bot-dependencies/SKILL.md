---
name: check-trading-bot-dependencies
description: Use before planning or coding any change to the trading bot — reads PRE_CHANGE_CHECKLIST.md and DEPENDENCIES.md to identify what will break
---

# Check Trading Bot Dependencies

**Run this before every plan and before every code change.**

## When to Use

- Before writing or proposing a plan for any change to this codebase
- Before touching any file that could affect the other bot or the dashboard
- When the user asks to add a feature, fix a bug, or refactor anything in the trading bot

## Steps

**Step 1: Read the checklist**

Read `/Users/kevin/Downloads/bitget_mtf_bot/docs/PRE_CHANGE_CHECKLIST.md` in full.

**Step 2: Classify the change**

Using Steps 1–2 of the checklist, identify:
- Which file category is being changed (shared file, bot-specific, config, data contract, dashboard, optimizer)
- Which change type applies (function signature, return value, config param, state field, CSV column, strategy enable/disable)

**Step 3: Look up relevant sections**

Using Step 3 of the checklist, read the relevant sections from `/Users/kevin/Downloads/bitget_mtf_bot/docs/DEPENDENCIES.md`:
- **Shared file change** → Section 2 (Shared Files)
- **State/JSON change** → Section 4 (Data Contracts)
- **Config change** → Section 5 (Config Dependencies)
- **Strategy change** → Section 7 (Strategy Implementations)
- **Confusing name or file** → Section 10 (Confusing Names & Pitfalls)

**Step 4: Report findings before proceeding**

Before writing any plan or code, output:

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

**Safe to proceed?** [Yes / Yes with caution: X / No: must fix Y first]
```

Only proceed to planning or coding after this report is written.

## Red Flags — STOP if you see these

- Changing `evaluate_s5()` signature → breaks both `bot.py` AND `ig_bot.py`
- Changing state.json fields → breaks dashboard.py and dashboard.html
- Changing CSV columns → breaks optimize.py or optimize_ig.py
- Editing `config_s5.py` → affects Bitget immediately, IG silently ignores unless `config_ig_s5.py` is also updated
- Editing `strategy.py` → shared by both bots AND backtest.py
- Confusing `paper_state.json` with `state_paper.json` → completely different purposes (see Section 10)

## The Rule

```
NO PLAN. NO CODE. UNTIL DEPENDENCY CHECK IS DONE.
```
