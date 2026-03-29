# Codebase Dependency Map

**Last updated:** 2026-03-29
**Update frequency:** After every PR that changes interfaces, data contracts, or cross-file dependencies

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Shared Files (Cross-Bot)](#2-shared-files-cross-bot)
3. [Bot-Specific Files](#3-bot-specific-files)
4. [Data Contracts](#4-data-contracts)
5. [Config Dependencies](#5-config-dependencies)
6. [Function Call Graph](#6-function-call-graph)
7. [Strategy Implementations](#7-strategy-implementations)
8. [External Tool Dependencies](#8-external-tool-dependencies)
9. [Dashboard Integration](#9-dashboard-integration)
10. [Confusing Names & Common Pitfalls](#10-confusing-names--common-pitfalls)
11. [Maintenance Guide](#11-maintenance-guide)

---

## 1. Architecture Overview

### Two Independent Bots

```
┌─────────────────────────────────────────────────────────────┐
│                     BITGET BOT (bot.py)                     │
│  Crypto USDT-margined futures · S1-S5 strategies           │
│  Output: state.json, trades.csv (or _paper variants)       │
└─────────────────────────────────────────────────────────────┘
                              ↓
                    ┌─────────────────┐
                    │  strategy.py    │ ← SHARED
                    │  config_s5.py   │ ← SHARED (patched by IG)
                    │  paper_trader.py│ ← SHARED
                    └─────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                      IG BOT (ig_bot.py)                     │
│  US30/Dow CFD · S5 only · 09:30-12:30 ET session          │
│  Output: ig_state.json, ig_trades.csv                      │
└─────────────────────────────────────────────────────────────┘
```

### Shared Code Contract

**Files shared between bots:**
- `strategy.py` — all evaluate_s1 through evaluate_s5 functions
- `config_s5.py` — S5 parameters (Bitget direct, IG patched with config_ig_s5)
- `paper_trader.py` — simulation engine (used by Bitget bot only in paper mode)

**Separation rules:**
- Changes to shared files require testing BOTH bots
- Bitget and IG must work independently (no cross-bot state)
- config_s5 changes affect Bitget immediately; IG overrides via config_ig_s5

### Data Flow

```
bot.py → state.py → state_paper.json → dashboard.py → dashboard.html
       → _log_trade  → trades_paper.csv  → optimize.py

ig_bot.py → _log_trade → ig_trades.csv → optimize_ig.py
          → _save_state → ig_state.json (position persistence, no state.py module)

paper_trader.py → paper_state.json (internal simulation state)
```

---

## 2. Shared Files (Cross-Bot)

[To be populated in Task 4]

---

## 3. Bot-Specific Files

[To be populated in Task 7]

---

## 4. Data Contracts

[To be populated in Task 5]

---

## 5. Config Dependencies

[To be populated in Task 8]

---

## 6. Function Call Graph

[To be populated in Task 6]

---

## 7. Strategy Implementations

[To be populated in Task 9]

---

## 8. External Tool Dependencies

[To be populated in Task 10]

---

## 9. Dashboard Integration

[To be populated in Task 11]

---

## 10. Confusing Names & Common Pitfalls

[To be populated in Task 12]

---

## 11. Maintenance Guide

[To be populated in Task 13]
