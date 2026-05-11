"""
Quick grid search for US100 S5 parameter tuning.

Modifies config_ig_us100.py in-place, runs backtest_ig.py, captures results.

Usage:
    python tune_us100.py --quick      # test key params only
    python tune_us100.py --full       # comprehensive grid search
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# ── Parameter grids ──────────────────────────────────────────────── #

QUICK_GRID = {
    "s5_swing_lookback":     [10, 15, 20, 25],
    "s5_ob_lookback":        [30, 40, 50],
    "s5_smc_fvg_filter":     [False, True],
    "s5_min_rr":             [0.8, 1.0, 1.2],
}

FULL_GRID = {
    "s5_htf_bos_lookback":   [8, 10, 12],
    "s5_ob_lookback":        [30, 40, 50],
    "s5_ob_min_impulse":     [0.004, 0.005, 0.006],
    "s5_ob_min_range_pct":   [0.0005, 0.001, 0.0015],
    "s5_swing_lookback":     [10, 15, 20, 25],
    "s5_min_rr":             [0.8, 1.0, 1.2],
    "s5_smc_fvg_filter":     [False, True],
    "s5_smc_fvg_lookback":   [10, 15, 20],
}

# ── Config manipulation ──────────────────────────────────────────────── #

CONFIG_FILE = Path("config_ig_us100.py")
BACKUP_FILE = CONFIG_FILE.with_suffix(".py.bak")


def backup_config():
    """Save current config before grid search."""
    import shutil
    shutil.copy(CONFIG_FILE, BACKUP_FILE)
    print(f"💾 Config backed up to: {BACKUP_FILE}")


def restore_config():
    """Restore original config."""
    import shutil
    shutil.copy(BACKUP_FILE, CONFIG_FILE)
    print(f"✅ Config restored from backup")


def update_param(param_name: str, value):
    """Update a single parameter in config_ig_us100.py."""
    text = CONFIG_FILE.read_text()

    # Match line like: "s5_swing_lookback": 20,
    pattern = rf'("{param_name}":\s*)([^,\n]+)'

    if isinstance(value, bool):
        new_val = "True" if value else "False"
    elif isinstance(value, (int, float)):
        new_val = str(value)
    else:
        new_val = f'"{value}"'

    new_text = re.sub(pattern, rf'\g<1>{new_val}', text)

    if new_text == text:
        print(f"⚠️  Failed to update {param_name} (pattern not found)")
        return False

    CONFIG_FILE.write_text(new_text)
    return True


# ── Backtest runner ──────────────────────────────────────────────────── #

def run_backtest() -> dict | None:
    """
    Run backtest_ig.py --instrument US100 --no-fetch and parse output.
    Returns dict with fills, wins, losses, wr, total_pnl or None on error.
    """
    try:
        result = subprocess.run(
            ["python", "backtest_ig.py", "--instrument", "US100", "--no-fetch"],
            capture_output=True,
            text=True,
            timeout=60,
        )

        # Parse output lines like:
        #   Filled:     5  (33.3%)
        #   Win rate:   40.0%
        #   Total PnL:  -96.1 pts

        output = result.stdout + result.stderr

        fills_match = re.search(r"Filled:\s+(\d+)", output)
        wr_match = re.search(r"Win rate:\s+([\d.]+)%", output)
        pnl_match = re.search(r"Total PnL:\s+([-\d.]+)\s+pts", output)

        if not all([fills_match, wr_match, pnl_match]):
            print(f"⚠️  Failed to parse backtest output")
            return None

        fills = int(fills_match.group(1))
        wr = float(wr_match.group(1)) / 100
        total_pnl = float(pnl_match.group(1))

        wins = round(fills * wr)
        losses = fills - wins

        return {
            "fills": fills,
            "wins": wins,
            "losses": losses,
            "wr": wr,
            "total_pnl": total_pnl,
            "score": wr * total_pnl if fills >= 5 else 0,  # require min 5 fills
        }

    except subprocess.TimeoutExpired:
        print("⚠️  Backtest timeout")
        return None
    except Exception as e:
        print(f"⚠️  Backtest error: {e}")
        return None


# ── Grid search ──────────────────────────────────────────────────────── #

def generate_combos(grid: dict) -> list[dict]:
    """Generate all parameter combinations from grid."""
    import itertools
    param_names = list(grid.keys())
    param_values = [grid[k] for k in param_names]
    combos = list(itertools.product(*param_values))
    return [dict(zip(param_names, combo)) for combo in combos]


def run_grid_search(grid: dict) -> list[dict]:
    """Run backtest for each parameter combination."""
    combos = generate_combos(grid)
    results = []

    print(f"\n🔬 Grid search: {len(combos)} combinations")
    print(f"   Parameters: {', '.join(grid.keys())}\n")

    for i, combo in enumerate(combos, 1):
        print(f"[{i}/{len(combos)}] Testing: {combo}")

        # Update config
        for param, value in combo.items():
            if not update_param(param, value):
                print("  → Skipping (config update failed)\n")
                continue

        # Run backtest
        result = run_backtest()
        if result is None:
            print("  → Skipping (backtest failed)\n")
            continue

        result["params"] = combo
        results.append(result)

        print(f"  → {result['fills']} fills | {result['wins']}W {result['losses']}L | "
              f"WR={result['wr']*100:.1f}% | PnL={result['total_pnl']:+.1f} pts | "
              f"score={result['score']:.1f}\n")

    results.sort(key=lambda r: r["score"], reverse=True)
    return results


# ── Results display ──────────────────────────────────────────────────── #

def print_top_results(results: list[dict], top_n: int = 10):
    """Print top N results."""
    if not results:
        print("\n❌ No valid results")
        return

    print(f"\n{'='*80}")
    print(f"🏆 TOP {min(top_n, len(results))} RESULTS")
    print(f"{'='*80}\n")

    for i, r in enumerate(results[:top_n], 1):
        print(f"#{i} | Score: {r['score']:.1f} | "
              f"{r['fills']} fills | {r['wins']}W {r['losses']}L | "
              f"WR={r['wr']*100:.1f}% | Total PnL={r['total_pnl']:+.1f} pts")
        print(f"    Params: {r['params']}\n")


# ── Main ──────────────────────────────────────────────────────────────── #

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true",
                        help="Run quick grid (4 key params)")
    parser.add_argument("--full", action="store_true",
                        help="Run full grid (all params)")
    parser.add_argument("--top", type=int, default=10,
                        help="Show top N results")
    args = parser.parse_args()

    if not args.quick and not args.full:
        print("❌ Specify --quick or --full")
        sys.exit(1)

    grid = QUICK_GRID if args.quick else FULL_GRID

    # Backup config
    backup_config()

    try:
        # Run grid search
        results = run_grid_search(grid)

        if not results:
            print("\n❌ No valid results")
            sys.exit(1)

        # Display results
        print_top_results(results, args.top)

        # Save to file
        output_file = Path("us100_tune_results.json")
        with open(output_file, "w") as f:
            json.dump({
                "grid_search": results[:args.top],
                "grid_type": "quick" if args.quick else "full",
            }, f, indent=2)
        print(f"💾 Results saved to: {output_file}\n")

        # Print best params for easy copy-paste
        best = results[0]
        print("📋 BEST PARAMS (copy to config_ig_us100.py):")
        print("-" * 50)
        for param, value in best["params"].items():
            if isinstance(value, bool):
                val_str = "True" if value else "False"
            else:
                val_str = str(value)
            print(f'    "{param}": {val_str},')
        print()

    finally:
        # Restore original config
        restore_config()


if __name__ == "__main__":
    main()
