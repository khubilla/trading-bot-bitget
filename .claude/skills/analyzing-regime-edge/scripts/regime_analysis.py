#!/usr/bin/env python3
"""
regime_analysis.py — Win/loss edge analysis across DNA + regime snapshot fields.

Joins entry-time context (snap_* fields on the OPEN row, plus the regime sidecar
keyed by trade_id) to trade OUTCOMES (result/pnl on the CLOSE row), then reports
win-rate and net-PnL splits across each context dimension.

Usage:
    python scripts/regime_analysis.py                       # bitget: trades.csv + trades_regime.csv
    python scripts/regime_analysis.py --ledger bybit_trades.csv --regime bybit_trades_regime.csv
    python scripts/regime_analysis.py --since 2026-06-15    # only entries on/after a date
    python scripts/regime_analysis.py --min 5               # min sample size per bucket (default 4)

Run from the repo root (where the CSVs live).

WHY THIS SCRIPT EXISTS (lessons baked in — do not re-learn them by hand):
  1. Pandas suffix collision: CLOSE rows carry blank snap_* columns. A naive
     closes.merge(opens) keeps the BLANK close column and silently returns all
     blanks. Fix: DROP snap_* (and side/base) from closes BEFORE the merge and
     take them only from the opens frame. This script does that.
  2. Test pollution: TESTS{N}USDT rows (from tests/test_bot_scale_in_exits.py)
     used to leak into trades.csv. They have no result/pnl so never affected
     win/loss, but this script drops them defensively anyway.
  3. Some rows have unescaped commas in snap_rsi_div_str -> use engine='python',
     on_bad_lines='skip' and report how many were skipped.
"""
import argparse
import re
import sys
import pandas as pd

DIMENSIONS = [
    "side", "base", "snap_sentiment",
    "snap_trend_daily_ema_slope", "snap_trend_daily_price_vs_ema",
    "snap_trend_daily_rsi_bucket",
    "snap_trend_h1_ema_slope", "snap_trend_h1_price_vs_ema",
    "snap_session", "snap_dow", "snap_btc_regime", "snap_atr_pctile",
]


def load(path, label):
    bad = []
    df = pd.read_csv(path, engine="python", on_bad_lines=lambda x: bad.append(x))
    if bad:
        print(f"  [{label}] skipped {len(bad)} malformed line(s) "
              f"(likely unescaped comma in snap_rsi_div_str)")
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default="trades.csv")
    ap.add_argument("--regime", default="trades_regime.csv")
    ap.add_argument("--since", default=None, help="ISO date; keep entries on/after")
    ap.add_argument("--min", type=int, default=4, help="min rows per bucket to report")
    args = ap.parse_args()

    print(f"Loading {args.ledger} + {args.regime} ...")
    t = load(args.ledger, "ledger")
    try:
        reg = load(args.regime, "regime")
    except FileNotFoundError:
        print(f"  regime sidecar {args.regime} not found — regime dims will be blank")
        reg = pd.DataFrame(columns=["trade_id"])

    # Drop test pollution defensively.
    t = t[~t["symbol"].astype(str).str.contains(r"^TESTS?\d", na=False)].copy()

    t["ts"] = pd.to_datetime(t["timestamp"], utc=True, errors="coerce")
    t["base"] = t["action"].str.extract(r"(S\d)")
    snapcols = [c for c in t.columns if c.startswith("snap_")]

    # CLOSE rows carry the outcome; drop their blank snap_* so the join repopulates.
    closes = t[t["result"].astype(str).isin(["WIN", "LOSS"])].drop(
        columns=snapcols + ["side", "base"], errors="ignore").copy()
    # OPEN rows carry entry context.
    opens = (t[t["action"].str.contains("LONG|SHORT", na=False)]
             .drop_duplicates("trade_id")
             .set_index("trade_id")[snapcols + ["side", "base"]])

    m = closes.merge(opens, on="trade_id", how="left")
    if len(reg):
        m = m.merge(reg.drop_duplicates("trade_id").set_index("trade_id"),
                    on="trade_id", how="left", suffixes=("", "_reg"))

    if args.since:
        m = m[m["ts"] >= pd.Timestamp(args.since, tz="UTC")]

    m["win"] = (m["result"] == "WIN").astype(int)
    m["pnl"] = pd.to_numeric(m["pnl"], errors="coerce")

    if not len(m):
        print("No closed trades in range."); return

    print(f"\n{'='*60}\nCLOSED TRADES: {len(m)} | "
          f"win% {m['win'].mean()*100:.1f} | net pnl {m['pnl'].sum():.2f}")
    print(f"date range: {m['ts'].min()} -> {m['ts'].max()}")
    print(f"entry context matched: {m['snap_sentiment'].notna().sum() if 'snap_sentiment' in m else 0}/{len(m)}"
          f" | regime matched: {m['snap_session'].notna().sum() if 'snap_session' in m else 0}/{len(m)}")

    # ---- CAVEAT CHECKS (always run; these bit us before) ----
    print(f"\n{'-'*60}\nDATA-QUALITY CAVEATS")
    if "snap_btc_regime" in m:
        bvals = m["snap_btc_regime"].dropna().unique()
        if len(bvals) <= 1:
            print(f"  ⚠ snap_btc_regime has no variance ({list(bvals)}) — "
                  f"cannot draw conclusions from it yet.")
    for h1 in ["snap_trend_h1_ema_slope", "snap_trend_h1_price_vs_ema"]:
        if h1 in m and m[h1].notna().sum() == 0:
            print(f"  ⚠ {h1} is 0% populated — H1 trend logging gap (dna_fields wiring).")
    if "snap_session" in m:
        cov = m["snap_session"].notna().mean()
        if cov < 0.5:
            print(f"  ⚠ regime sidecar covers only {cov*100:.0f}% of closes "
                  f"(logging started recently) — cross-period comparisons are confounded; "
                  f"trust WITHIN-window contrasts (use --since to isolate).")

    # ---- SPLITS ----
    for col in DIMENSIONS:
        if col not in m:
            continue
        d = m.copy()
        d[col] = d[col].fillna("(blank)")
        g = d.groupby(col).agg(n=("win", "size"), winpct=("win", "mean"),
                               pnl=("pnl", "sum"))
        g = g[g["n"] >= args.min]
        if len(g):
            g["winpct"] = (g["winpct"] * 100).round(0)
            print(f"\n--- {col} ---")
            print(g.sort_values("winpct").to_string())


if __name__ == "__main__":
    main()
