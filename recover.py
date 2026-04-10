#!/usr/bin/env python3
"""
recover.py — Manual recovery CLI for positions that filled while the bot was stopped.

Usage:
    python recover.py [--dry-run] [--symbols SYM1 SYM2 ...]

Options:
    --dry-run    Print what would change without writing anything.
    --symbols    Limit recovery to specific symbols (default: all UNKNOWN in state.json).

Mirrors Bot._startup_recovery() sad path for use when the bot is already running.
The bot will pick up the patched state.json and trades.csv on its next tick.
"""
import argparse
import csv
import json
import sys
import uuid
import logging
from datetime import datetime, timezone
from pathlib import Path

import config
import snapshot
import state as st
import trader as tr
from startup_recovery import fetch_candles_at, estimate_sl_tp, attempt_s5_recovery

logger = logging.getLogger(__name__)

# Defaults — can be overridden in tests
STATE_FILE = "state.json"
TRADE_LOG  = config.TRADE_LOG

_TRADE_FIELDS = [
    "timestamp", "trade_id", "action", "symbol", "side", "qty", "entry", "sl", "tp",
    "box_low", "box_high", "leverage", "margin", "tpsl_set", "strategy",
    "snap_rsi", "snap_adx", "snap_htf", "snap_coil", "snap_box_range_pct", "snap_sentiment",
    "snap_daily_rsi",
    "snap_entry_trigger", "snap_sl", "snap_rr",
    "snap_rsi_peak", "snap_spike_body_pct", "snap_rsi_div", "snap_rsi_div_str",
    "snap_s5_ob_low", "snap_s5_ob_high", "snap_s5_tp",
    "snap_s6_peak", "snap_s6_drop_pct", "snap_s6_rsi_at_peak",
    "snap_sr_clearance_pct",
    "result", "pnl", "pnl_pct", "exit_reason", "exit_price",
]


def _get_open_csv_row(csv_path: str, symbol: str) -> dict | None:
    """Return the most recent open (_LONG/_SHORT) CSV row for a symbol, or None."""
    if not Path(csv_path).exists():
        return None
    try:
        with open(csv_path, newline="") as f:
            rows = list(csv.DictReader(f))
        for r in reversed(rows):
            action = r.get("action", "")
            if (r.get("symbol") == symbol
                    and any(action.endswith(sfx) for sfx in ("_LONG", "_SHORT"))
                    and r.get("qty")):
                return r
    except Exception:
        pass
    return None


def _is_valid_sltp(sl, tp) -> bool:
    """Return True iff both sl and tp parse as float > 0."""
    try:
        return float(sl) > 0 and float(tp) > 0
    except (TypeError, ValueError):
        return False


def _patch_sltp(sym: str, state_entry: dict, exchange_pos: dict,
                state_file: str, csv_path: str,
                dry_run: bool = False) -> dict:
    """
    PATCH_SLTP case: CSV open row exists but SL/TP are bad.
    Derives new SL/TP via S5 OB recovery (S5) or estimate_sl_tp (all others).
    Patches state.json only — no CSV write.
    Returns summary dict: {symbol, action, strategy, sl, tp}.
    """
    strategy  = state_entry.get("strategy", "UNKNOWN")
    side      = exchange_pos.get("side", "SHORT")
    entry     = float(exchange_pos.get("entry_price", 0))
    opened_at = state_entry.get("opened_at") or datetime.now(timezone.utc).isoformat()

    sl = tp = ob_low = ob_high = None

    if strategy == "S5":
        try:
            end_ms = int(datetime.fromisoformat(opened_at).timestamp() * 1000) + 60_000
        except Exception:
            import time
            end_ms = int(time.time() * 1000)

        m15_df   = fetch_candles_at(sym, "15m", limit=100, end_ms=end_ms)
        htf_df   = fetch_candles_at(sym, "1H",  limit=50,  end_ms=end_ms)
        daily_df = tr.get_candles(sym, "1D", limit=60)

        if not m15_df.empty and not htf_df.empty and not daily_df.empty:
            result = attempt_s5_recovery(sym, m15_df, htf_df, daily_df, side)
            if result:
                sl, tp, ob_low, ob_high = result

    if sl is None:
        sl, tp, ob_low, ob_high = estimate_sl_tp(entry, side)

    if not dry_run:
        s = st._read()
        for t in s.get("open_trades", []):
            if t["symbol"] == sym:
                t.update({
                    "sl":       round(sl,      8),
                    "tp":       round(tp,      8),
                    "box_high": round(ob_high, 8),
                    "box_low":  round(ob_low,  8),
                    "tpsl_set": False,
                })
                break
        st._write(s)

    return {"symbol": sym, "action": "PATCH_SLTP", "strategy": strategy,
            "sl": round(sl, 8), "tp": round(tp, 8)}


def _log_trade_to_csv(csv_path: str, action: str, details: dict,
                      dry_run: bool = False) -> None:
    """Append a trade row to trades.csv. No-op in dry-run mode."""
    if dry_run:
        return
    row = {"timestamp": datetime.now(timezone.utc).isoformat(), "action": action, **details}
    write_header = not Path(csv_path).exists()
    with open(csv_path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_TRADE_FIELDS, extrasaction="ignore", restval="")
        if write_header:
            w.writeheader()
        w.writerow(row)


def _patch_state(state_file: str, sym: str, trade_id: str,
                 sl: float, tp: float, ob_low: float, ob_high: float,
                 dry_run: bool = False) -> None:
    """Update the open_trades entry for sym in state.json."""
    if dry_run:
        return
    data = json.loads(Path(state_file).read_text())
    for t in data.get("open_trades", []):
        if t["symbol"] == sym:
            t.update({
                "trade_id": trade_id,
                "sl":       round(sl,      8),
                "tp":       round(tp,      8),
                "box_high": round(ob_high, 8),
                "box_low":  round(ob_low,  8),
                "tpsl_set": False,
            })
            break
    Path(state_file).write_text(json.dumps(data, indent=2))


def _df_to_candles(df) -> list[dict]:
    return [
        {"t": int(r.ts), "o": float(r.open), "h": float(r.high),
         "l": float(r.low),  "c": float(r.close), "v": float(r.vol)}
        for r in df.itertuples()
    ]


def recover_position(sym: str, trade_entry: dict,
                     state_file: str, csv_path: str,
                     dry_run: bool = False) -> dict:
    """
    Run sad-path recovery for a single UNKNOWN position.
    Returns summary dict: {symbol, trade_id, entry, sl, tp, snapshot}.
    """
    entry     = float(trade_entry.get("entry", 0))
    side      = trade_entry.get("side", "SHORT")
    margin    = float(trade_entry.get("margin", 0))
    leverage  = int(float(trade_entry.get("leverage") or 10))
    qty       = float(trade_entry.get("qty", 0))
    opened_at = trade_entry.get("opened_at") or datetime.now(timezone.utc).isoformat()
    trade_id  = uuid.uuid4().hex[:8]

    try:
        end_ms = int(datetime.fromisoformat(opened_at).timestamp() * 1000) + 60_000
    except Exception:
        import time
        end_ms = int(time.time() * 1000)

    m15_df   = fetch_candles_at(sym, "15m", limit=100, end_ms=end_ms)
    htf_df   = fetch_candles_at(sym, "1H",  limit=50,  end_ms=end_ms)
    daily_df = tr.get_candles(sym, "1D", limit=60)

    result = None
    if not m15_df.empty and not htf_df.empty and not daily_df.empty:
        result = attempt_s5_recovery(sym, m15_df, htf_df, daily_df, side)

    sl, tp, ob_low, ob_high = result if result else estimate_sl_tp(entry, side)

    # Patch state.json
    _patch_state(state_file, sym, trade_id, sl, tp, ob_low, ob_high, dry_run=dry_run)

    # Append CSV open row
    _log_trade_to_csv(csv_path, f"UNKNOWN_{side}", {
        "trade_id":        trade_id,
        "symbol":          sym,
        "side":            side,
        "qty":             qty,
        "entry":           entry,
        "sl":              round(sl,      8),
        "tp":              round(tp,      8),
        "box_low":         round(ob_low,  8),
        "box_high":        round(ob_high, 8),
        "leverage":        leverage,
        "margin":          round(margin,  8),
        "tpsl_set":        False,
        "strategy":        "UNKNOWN",
        "snap_s5_ob_low":  round(ob_low,  8),
        "snap_s5_ob_high": round(ob_high, 8),
        "snap_s5_tp":      round(tp,      8),
    }, dry_run=dry_run)

    # Save snapshot
    snap_saved = False
    if not dry_run and not m15_df.empty:
        snapshot.save_snapshot(
            trade_id=trade_id,
            event="open",
            symbol=sym,
            interval="15m",
            candles=_df_to_candles(m15_df),
            event_price=entry,
            captured_at=opened_at,
        )
        snap_saved = True

    return {
        "symbol":   sym,
        "trade_id": trade_id,
        "entry":    entry,
        "sl":       sl,
        "tp":       tp,
        "snapshot": snap_saved,
    }


def main(args=None):
    parser = argparse.ArgumentParser(
        description="Manual recovery for positions that filled while the bot was stopped."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would change without writing to disk.")
    parser.add_argument("--symbols", nargs="+", metavar="SYM",
                        help="Limit recovery to specific symbols.")
    parsed = parser.parse_args(args)

    data   = json.loads(Path(STATE_FILE).read_text())
    trades = data.get("open_trades", [])

    # Find UNKNOWN positions (no CSV open row, trade_id blank or sl="?")
    targets = [
        t for t in trades
        if (t.get("strategy") == "UNKNOWN" or t.get("sl") in ("?", "", None))
        and (not parsed.symbols or t["symbol"] in parsed.symbols)
    ]

    if not targets:
        print("No UNKNOWN positions found — nothing to recover.")
        return

    mode = "[DRY RUN] " if parsed.dry_run else ""
    print(f"{mode}Recovering {len(targets)} position(s)...\n")

    results = []
    for t in targets:
        sym = t["symbol"]
        print(f"  {sym}...", end=" ", flush=True)
        try:
            r = recover_position(sym, t, STATE_FILE, TRADE_LOG, dry_run=parsed.dry_run)
            results.append(r)
            print("done")
        except Exception as e:
            print(f"ERROR: {e}")
            logger.warning(f"[{sym}] recover_position failed: {e}")

    # Summary table
    print(f"\n{'Symbol':14s}  {'trade_id':>9s}  {'Entry':>10s}  {'SL':>10s}  {'TP':>10s}  {'Snap':>4s}")
    print("-" * 65)
    for r in results:
        snap = "yes" if r["snapshot"] else ("skip" if not parsed.dry_run else "n/a")
        print(
            f"{r['symbol']:14s}  {r['trade_id']:>9s}  "
            f"{r['entry']:>10.5f}  {r['sl']:>10.5f}  {r['tp']:>10.5f}  {snap:>4s}"
        )

    if parsed.dry_run:
        print("\n[DRY RUN] No files were written.")
    else:
        print(f"\n⚠️  tpsl_set=False for all recovered positions.")
        print("   Manually set SL/TP on Bitget, or restart the bot to activate S5 swing-trail.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    main()
