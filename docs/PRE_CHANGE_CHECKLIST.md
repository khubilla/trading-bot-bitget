# Pre-Change Checklist

⚠️ **MANDATORY:** Run through this before editing any file, changing behavior, or adding features.

## Step 1: Identify What You're Changing

File I'm editing: `_______________`

Check all that apply:
- [ ] Shared file (strategy.py, config_s5.py, paper_trader.py, state.py, scanner.py)
- [ ] Config file (config*.py)
- [ ] Bot file (bot.py, ig_bot.py)
- [ ] Dashboard file (dashboard.py, dashboard.html)
- [ ] Tool file (optimize.py, optimize_ig.py, backtest.py)
- [ ] Data structure (state.json fields, CSV columns, API responses)

## Step 2: Scope Impact

What am I changing?
- [ ] Function signature (params, return type)
- [ ] Field names (state.json, CSV columns)
- [ ] Field types (string→int, adding/removing fields)
- [ ] Import statements
- [ ] Strategy logic (entry/exit conditions)
- [ ] Config parameter names or types

## Step 3: Check Dependencies

For each box checked in Step 1, read the corresponding section in DEPENDENCIES.md:

- **Shared files** → § 2. Shared Files (Cross-Bot)
- **Config files** → § 5. Config Dependencies
- **Data structures** → § 4. Data Contracts
- **Function changes** → § 6. Function Call Graph
- **Strategy logic** → § 7. Strategy Implementations

## Step 4: Verify Both Bots

If you touched ANY shared file, verify both bots:
```bash
python -c "import bot; print('Bitget OK')"
python -c "import ig_bot; print('IG OK')"
```

## Step 5: Verify Data Consumers

If you touched state.json or CSV formats:
- [ ] Check dashboard.py lines 60-75, 310-350 for field references
- [ ] Check dashboard.html lines 1224-2400 for field consumption
- [ ] Check optimize.py lines 82-94 (STRATEGY_COLUMNS) for CSV dependencies
- [ ] Check optimize_ig.py lines 95-103 for CSV dependencies
- [ ] Check backtest.py for field dependencies

## Step 6: Document Changes

After making changes:
- [ ] Update DEPENDENCIES.md if you added new dependencies
- [ ] Update this checklist if you found gaps in coverage
- [ ] Update confusing names section if you renamed things
