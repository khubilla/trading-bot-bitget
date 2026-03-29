---
name: qa-trading-bot
description: Run after every code change and before committing — executes pytest, auto-fixes source failures, loops until clean. Never modifies tests.
---

# QA — Trading Bot

**Run after every code change. Before committing.**

## Workflow Position

```
check-trading-bot-dependencies   ← pre-change
        ↓
[make changes]
        ↓
qa-trading-bot                   ← YOU ARE HERE (post-change, pre-commit)
        ↓
git commit + push
```

## Steps

**Step 1: Run pytest**

```bash
pytest tests/ -v --tb=short
```

**Step 2: If all pass**

Report: `✅ QA clean — N tests passing` and stop.

**Step 3: If failures — fix loop**

For each `FAILED` line in the output:
1. Read the failing test to understand what it expects
2. Read the source file(s) it tests
3. Fix the source — **NEVER modify tests** (tests are the source of truth)
4. Re-run pytest

Repeat until `pytest` exits with code 0.

**Step 4: Fix loop guard — escalate after 3 failed attempts**

If the same test fails after 3 consecutive fix attempts on that test:
- STOP immediately
- Report to user:
  - Which test is failing
  - What the last fix attempted was
  - Why it's stuck (contradictory requirements, missing fixture, etc.)
- Do NOT continue trying

## Rules

- Tests are the source of truth — never change a test to make it pass
- Fix production code only
- All tests must pass before committing
- SKIPPED tests are acceptable (runtime files may not exist in all environments)
- FAILED tests block the commit
