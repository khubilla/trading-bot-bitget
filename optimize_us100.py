"""
Grid search optimizer for US100 (NASDAQ) S5 parameters.

Usage:
    python optimize_us100.py              # run grid search on all parameters
    python optimize_us100.py --quick      # smaller grid for fast iteration
    python optimize_us100.py --param swing_lookback  # tune single param
"""

import argparse
import itertools
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from backtest_ig import run_backtest_for_instrument
from config_ig import INSTRUMENTS

# Find US100 config
US100_CFG = None
for inst in INSTRUMENTS:
    if inst["display_name"] == "US100":
        US100_CFG = inst
        break

if US100_CFG is None:
    print("❌ US100 not found in INSTRUMENTS")
    sys.exit(1)

# ── Parameter grids ──────────────────────────────────────────────── #

FULL_GRID = {
    "s5_htf_bos_lookback":   [8, 10, 12],
    "s5_ob_lookback":        [30, 40, 50],
    "s5_ob_min_impulse":     [0.004, 0.005, 0.006],
    "s5_ob_min_range_pct":   [0.0005, 0.001, 0.0015],
    "s5_swing_lookback":     [15, 20, 25],
    "s5_min_rr":             [0.8, 1.0, 1.2],
    "s5_smc_fvg_filter":     [False, True],
    "s5_smc_fvg_lookback":   [10, 15, 20],
}

QUICK_GRID = {
    "s5_ob_lookback":        [30, 40],
    "s5_swing_lookback":     [15, 20],
    "s5_min_rr":             [0.8, 1.0],
    "s5_smc_fvg_filter":     [False, True],
}

# ── Grid search ──────────────────────────────────────────────────── #

def run_grid_search(param_grid: dict, baseline_cfg: dict) -> list[dict]:
    """
    Run backtest for every combination in param_grid.
    Returns list of result dicts sorted by score (WR * total_pnl).
    """
    results = []
    param_names = list(param_grid.keys())
    param_values = [param_grid[k] for k in param_names]
    combos = list(itertools.product(*param_values))

    print(f"\n🔬 Grid search: {len(combos)} combinations")
    print(f"   Parameters: {', '.join(param_names)}\n")

    for i, combo in enumerate(combos, 1):
        cfg = baseline_cfg.copy()
        for param_name, param_val in zip(param_names, combo):
            cfg[param_name] = param_val

        print(f"[{i}/{len(combos)}] Testing: {dict(zip(param_names, combo))}")

        try:
            trades_df = run_backtest_for_instrument(cfg, no_fetch=True)

            if trades_df.empty:
                print("  → 0 fills, skipping\n")
                continue

            wins = (trades_df["result"] == "WIN").sum()
            losses = (trades_df["result"] == "LOSS").sum()
            total = wins + losses
            wr = wins / total if total > 0 else 0
            total_pnl = trades_df["pnl"].sum()
            avg_pnl = trades_df["pnl"].mean()
            score = wr * total_pnl if total >= 5 else 0  # require min 5 trades

            result = {
                "params": dict(zip(param_names, combo)),
                "fills": len(trades_df),
                "wins": wins,
                "losses": losses,
                "wr": wr,
                "total_pnl": total_pnl,
                "avg_pnl": avg_pnl,
                "score": score,
            }
            results.append(result)

            print(f"  → {len(trades_df)} fills | {wins}W {losses}L | "
                  f"WR={wr*100:.1f}% | PnL={total_pnl:+.1f} pts | score={score:.1f}\n")

        except Exception as e:
            print(f"  → ERROR: {e}\n")
            continue

    results.sort(key=lambda r: r["score"], reverse=True)
    return results


def print_top_results(results: list[dict], top_n: int = 10):
    """Print top N results from grid search."""
    if not results:
        print("\n❌ No valid results")
        return

    print(f"\n{'='*80}")
    print(f"🏆 TOP {min(top_n, len(results))} RESULTS (sorted by score = WR × total_pnl)")
    print(f"{'='*80}\n")

    for i, r in enumerate(results[:top_n], 1):
        print(f"#{i} | Score: {r['score']:.1f} | "
              f"{r['fills']} fills | {r['wins']}W {r['losses']}L | "
              f"WR={r['wr']*100:.1f}% | Total PnL={r['total_pnl']:+.1f} pts")
        print(f"    Params: {r['params']}\n")


def print_baseline_comparison(baseline: dict, best: dict):
    """Compare baseline vs best result."""
    print(f"\n{'='*80}")
    print("📊 BASELINE vs BEST")
    print(f"{'='*80}\n")

    print("BASELINE:")
    print(f"  {baseline['fills']} fills | {baseline['wins']}W {baseline['losses']}L | "
          f"WR={baseline['wr']*100:.1f}% | Total PnL={baseline['total_pnl']:+.1f} pts")

    print("\nBEST:")
    print(f"  {best['fills']} fills | {best['wins']}W {best['losses']}L | "
          f"WR={best['wr']*100:.1f}% | Total PnL={best['total_pnl']:+.1f} pts")

    print(f"\nIMPROVEMENT:")
    wr_delta = (best['wr'] - baseline['wr']) * 100
    pnl_delta = best['total_pnl'] - baseline['total_pnl']
    print(f"  WR: {wr_delta:+.1f} percentage points")
    print(f"  Total PnL: {pnl_delta:+.1f} pts")
    print(f"\nChanged params:")
    for k, v in best['params'].items():
        if US100_CFG[k] != v:
            print(f"  {k}: {US100_CFG[k]} → {v}")


# ── Main ──────────────────────────────────────────────────────────── #

def main():
    parser = argparse.ArgumentParser(description="US100 S5 parameter optimizer")
    parser.add_argument("--quick", action="store_true",
                        help="Run smaller grid (faster)")
    parser.add_argument("--param", type=str,
                        help="Tune single parameter only (e.g. swing_lookback)")
    parser.add_argument("--top", type=int, default=10,
                        help="Show top N results (default 10)")
    args = parser.parse_args()

    # Select grid
    if args.param:
        if f"s5_{args.param}" not in FULL_GRID:
            print(f"❌ Unknown param: {args.param}")
            print(f"Available: {', '.join(k.replace('s5_', '') for k in FULL_GRID.keys())}")
            sys.exit(1)
        grid = {f"s5_{args.param}": FULL_GRID[f"s5_{args.param}"]}
    elif args.quick:
        grid = QUICK_GRID
    else:
        grid = FULL_GRID

    # Run baseline
    print("\n📊 Running baseline (current config_ig_us100.py params)...")
    try:
        baseline_trades = run_backtest_for_instrument(US100_CFG, no_fetch=True)
        baseline_wins = (baseline_trades["result"] == "WIN").sum()
        baseline_losses = (baseline_trades["result"] == "LOSS").sum()
        baseline_total = baseline_wins + baseline_losses
        baseline = {
            "params": {},
            "fills": len(baseline_trades),
            "wins": baseline_wins,
            "losses": baseline_losses,
            "wr": baseline_wins / baseline_total if baseline_total > 0 else 0,
            "total_pnl": baseline_trades["pnl"].sum(),
            "avg_pnl": baseline_trades["pnl"].mean(),
            "score": 0,  # not scored
        }
        print(f"✅ Baseline: {baseline['fills']} fills | "
              f"{baseline['wins']}W {baseline['losses']}L | "
              f"WR={baseline['wr']*100:.1f}% | "
              f"Total PnL={baseline['total_pnl']:+.1f} pts\n")
    except Exception as e:
        print(f"❌ Baseline failed: {e}")
        sys.exit(1)

    # Run grid search
    results = run_grid_search(grid, US100_CFG)

    if not results:
        print("\n❌ No valid results from grid search")
        sys.exit(1)

    # Print results
    print_top_results(results, args.top)
    print_baseline_comparison(baseline, results[0])

    # Save results
    output_file = Path("us100_optimization_results.json")
    import json
    with open(output_file, "w") as f:
        json.dump({
            "baseline": baseline,
            "grid_search": results[:args.top],
            "timestamp": pd.Timestamp.now().isoformat(),
        }, f, indent=2, default=str)
    print(f"\n💾 Results saved to: {output_file}")


if __name__ == "__main__":
    main()
