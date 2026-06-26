---
name: analyzing-regime-edge
description: Use when the user wants to analyze whether DNA/regime snapshot fields (snap_session, snap_atr_pctile, snap_btc_regime, snap_trend_*, side, strategy) explain wins vs losses across the trade ledger — i.e. aggregate edge analysis, NOT a single-trade audit. Triggers on "analyze the regime data", "which conditions lose money", "re-run the regime analysis", "is the bot losing in certain regimes".
---

# Analyzing Regime / DNA Edge

## Overview

This is **aggregate edge analysis**: join entry-time context to trade outcomes and
find which conditions win vs lose. It is the companion to `analyzing-trade-execution`
(which audits ONE trade against code). Use that skill instead if the user names a
specific trade_id or asks "did this trade execute correctly".

The snapshot fields come from two recorders:
- `trade_dna.py` → trend fingerprint (`snap_trend_*`, `snap_rsi`, `snap_sentiment`, …) on the OPEN row of `trades.csv`.
- `regime.py` → context sidecar (`snap_session`, `snap_hour_ph`, `snap_dow`, `snap_atr_pct(ile)`, `snap_vol_vs_avg`, `snap_btc_change`, `snap_btc_regime`, `snap_funding_rate`) in `trades_regime.csv`, keyed by `trade_id`.

## How to run

From the repo root:

```bash
python .claude/skills/analyzing-regime-edge/scripts/regime_analysis.py
```

Useful flags:
- `--since 2026-06-15` — isolate a window (use this once the regime sidecar covers
  more than one BTC regime / multiple weeks, to avoid confounding).
- `--ledger bybit_trades.csv --regime bybit_trades_regime.csv` — analyze the Bybit book.
- `--min 5` — raise the minimum bucket size before a split is reported.

The script prints overall win%/PnL, coverage stats, **automatic data-quality caveats**,
then win%/PnL splits for each dimension (side, strategy, sentiment, daily/H1 trend,
session, day-of-week, BTC regime, ATR percentile).

## The Iron Law (inherited from analyzing-trade-execution)

```
NO CAUSAL CONCLUSION WITHOUT CODE VERIFICATION
```

A split is a *correlation*. Before telling the user "X causes losses", open the
relevant `strategies/sN.py` and confirm what the field means at entry. Example from
the first run: S1 shorts firing at `snap_rsi` ≈ 17 looked like "shorting oversold",
but `evaluate_s1` showed S1 is a momentum BREAKOUT strategy — RSI<30 shorts are
by design, and the loss signal was that those breakouts were mean-reverting at
`snap_atr_pctile` ≈ 98 (climactic moves). Same field, very different story.

## Mandatory caveats to report (the script flags these; you must relay them)

1. **`snap_btc_regime` variance** — if every tagged row is `FLAT` (no
   RISK_ON/RISK_OFF), the field has zero contrast. Report "no conclusion yet",
   never "FLAT loses".
2. **Coverage / confounding** — the regime sidecar started logging recently. If it
   covers <50% of closes, the regime-tagged trades ≈ one recent window. Cross-period
   comparisons are confounded; trust WITHIN-window contrasts (`--since`) only.
3. **H1 trend gap** — `snap_trend_h1_*` has historically been 0% populated despite
   `dna_fields()` computing it. If still 0%, flag it as a logging bug, don't use it.
4. **Test pollution** — `TESTS{N}USDT` rows are test artifacts (see below). The
   script drops them; if you ever hand-roll the analysis, exclude `^TESTS?\d`.
5. **Join collision** — CLOSE rows carry blank `snap_*` columns. Drop them from the
   closes frame BEFORE merging entry context, or the merge silently returns all
   blanks. (The script does this; a hand-rolled `closes.merge(opens)` will lie.)

## Known data hygiene

- Production ledgers: `trades.csv` (Bitget) + `trades_regime.csv`; `bybit_trades.csv`
  + `bybit_trades_regime.csv`; `ig_trades.csv`.
- `tests/conftest.py` has autouse `cleanup_trades_csv` + `cleanup_ig_trades_csv`
  fixtures that back up/restore the ledgers around every test, so pytest no longer
  pollutes them. If `TESTS*` rows reappear, that isolation has regressed — check
  `tests/test_bot_scale_in_exits.py::test_scale_in_all_strategies_affected`.

## Output → action

Translate splits into testable filter hypotheses, then quantify them before
recommending a code change (e.g. "skip entries when snap_atr_pctile > 90: would
have changed June PnL by N"). Hand any actual strategy/param change to
`verify-strategy-revision` and `check-trading-bot-dependencies` first.
