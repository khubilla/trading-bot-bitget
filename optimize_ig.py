"""
optimize_ig.py — Claude-powered optimizer for the IG S5 / US30 bot

Reads ig_trades.csv, sends completed trades + current config_ig_s5 params
to Claude, and prints parameter change suggestions tuned for US30.

Usage:
  python optimize_ig.py           # analyze ig_trades.csv
  python optimize_ig.py --min 3   # lower minimum trades threshold (default 5)
"""

import os, csv, sys, argparse
import anthropic
import config_ig

MODEL      = "claude-sonnet-4-6"
MIN_TRADES = 5   # US30 session is limited to 3h/day — fewer trades than crypto

# ── Current params (read from config_ig_s5 at runtime) ─────── #

def _load_current_params() -> dict:
    import config_ig_s5 as c
    return {
        "HTF_BOS_LOOKBACK":  c.S5_HTF_BOS_LOOKBACK,
        "OB_LOOKBACK":       c.S5_OB_LOOKBACK,
        "OB_MIN_IMPULSE":    f"{c.S5_OB_MIN_IMPULSE * 100:.1f}%",
        "OB_MIN_RANGE_PCT":  f"{c.S5_OB_MIN_RANGE_PCT * 100:.1f}%",
        "CHOCH_LOOKBACK":    c.S5_CHOCH_LOOKBACK,
        "MAX_ENTRY_BUFFER":  f"{c.S5_MAX_ENTRY_BUFFER * 100:.0f}%",
        "SL_BUFFER_PCT":     f"{c.S5_SL_BUFFER_PCT * 100:.1f}%",
        "SWING_LOOKBACK":    c.S5_SWING_LOOKBACK,
        "MIN_RR":            c.S5_MIN_RR,
        "TRAIL_RANGE_PCT":   f"{c.S5_TRAIL_RANGE_PCT}%",
        "USE_CANDLE_STOPS":  c.S5_USE_CANDLE_STOPS,
    }


# ── Trade loader ────────────────────────────────────────────── #

def load_trades(csv_path: str) -> list[dict]:
    """
    Load completed trades from ig_trades.csv.
    Pairs S5_OPEN + S5_CLOSE rows; also handles S5_PARTIAL rows.
    Returns list of dicts with all snapshot + result fields.
    """
    if not os.path.exists(csv_path):
        return []

    with open(csv_path, newline="") as f:
        rows = list(csv.DictReader(f))

    opens   = {}   # trade_id → open row
    partial = {}   # trade_id → partial pnl (USD)
    trades  = []

    for r in rows:
        action   = r.get("action", "")
        trade_id = r.get("trade_id", "")
        if not action or not trade_id:
            continue

        if action in ("S5_OPEN",):
            opens[trade_id] = r

        elif action == "S5_PARTIAL":
            try:
                partial[trade_id] = partial.get(trade_id, 0) + float(r.get("pnl") or 0)
            except ValueError:
                pass

        elif action in ("S5_CLOSE", "S5_SL", "S5_TP"):
            if trade_id not in opens:
                continue
            o = opens.pop(trade_id)

            try:
                close_pnl   = float(r.get("pnl") or 0)
                partial_pnl = partial.pop(trade_id, 0)
                total_pnl   = close_pnl + partial_pnl
            except ValueError:
                total_pnl = None

            result = r.get("result") or o.get("result", "")
            if not result and total_pnl is not None:
                result = "WIN" if total_pnl > 0 else "LOSS"

            trade = {**o}
            trade["result"]      = result
            trade["total_pnl"]   = round(total_pnl, 2) if total_pnl is not None else ""
            trade["exit_reason"] = r.get("exit_reason", action)
            trade["close_date"]  = r.get("timestamp", "")[:10]
            trade["session_date"] = r.get("session_date") or o.get("session_date", "")
            trades.append(trade)

    return trades


# ── Formatting ──────────────────────────────────────────────── #

_COLS = [
    "result", "total_pnl", "exit_reason",
    "snap_rr", "snap_entry_trigger", "snap_sl",
    "snap_s5_ob_low", "snap_s5_ob_high", "snap_s5_tp",
]


def _fmt(val):
    if val is None or val == "":
        return "—"
    return str(val)


def format_table(trades: list[dict]) -> str:
    header = " | ".join(f"{c:<22}" for c in ["date"] + _COLS)
    sep    = "-" * len(header)
    lines  = [header, sep]
    for t in trades:
        row = [t.get("close_date", t.get("session_date", ""))[:10]]
        row += [f"{_fmt(t.get(c)):<22}" for c in _COLS]
        lines.append(" | ".join(row))
    return "\n".join(lines)


# ── Prompt ──────────────────────────────────────────────────── #

def build_prompt(trades: list[dict], params: dict) -> str:
    wins    = sum(1 for t in trades if t.get("result") == "WIN")
    losses  = len(trades) - wins
    pnl_vals = [float(t["total_pnl"]) for t in trades
                if t.get("total_pnl") not in (None, "", "—")]
    avg_pnl = f"  Avg P/L: ${sum(pnl_vals)/len(pnl_vals):+.2f}" if pnl_vals else ""
    total_pnl = f"  Total P/L: ${sum(pnl_vals):+.2f}" if pnl_vals else ""

    exit_counts: dict[str, int] = {}
    for t in trades:
        k = t.get("exit_reason", "?")
        exit_counts[k] = exit_counts.get(k, 0) + 1
    exit_summary = ", ".join(f"{k}={v}" for k, v in sorted(exit_counts.items()))

    params_str = "\n".join(f"  {k} = {v}" for k, v in params.items())
    table      = format_table(trades)

    return f"""You are a trading strategy optimizer for an S5 (SMC Order Block) strategy running on US30 / Wall Street Cash (Dow Jones CFD) via IG.com.

Instrument context:
- US30 trades 09:30–12:30 ET (3-hour morning session only, then all positions force-closed)
- Average 15m candle range: ~30–50 points
- $1/point per contract; partial close (50%) at 1:1 R:R, remainder trailed via candle stops
- No overnight holds; session_end exits are neutral (neither win nor loss due to time limit)

Total trades: {len(trades)} | Wins: {wins} | Losses: {losses} | Win rate: {wins/len(trades)*100:.0f}%
{avg_pnl}{total_pnl}
Exit breakdown: {exit_summary}

Current parameters (from config_ig_s5.py):
{params_str}

Trade history (oldest to newest):
snap_rr = R:R ratio at entry | snap_entry_trigger = entry price | snap_sl = stop loss
snap_s5_ob_low/high = order block zone | snap_s5_tp = structural TP target
{table}

Analyze patterns in wins vs losses. Consider:
- OB quality (impulse size, OB zone width)
- R:R at entry vs actual outcomes
- Exit reasons (SL hits vs SESSION_END vs TP)
- Whether session_end exits suggest entries too late in the session

Suggest specific parameter changes to improve win rate and reduce premature SL exits.

Format your response as:
1. KEY PATTERNS (2-3 bullets: what separates wins from losses)
2. SUGGESTED CHANGES (param name → current → suggested, with reason in US30 point terms)
3. TRADES TO FILTER (indicator profile of trades that should be skipped)

Be specific and data-driven. Only suggest changes supported by the data."""


# ── Main ────────────────────────────────────────────────────── #

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--min", type=int, default=MIN_TRADES,
                        help=f"Min completed trades before analyzing (default {MIN_TRADES})")
    args = parser.parse_args()

    csv_path = config_ig.TRADE_LOG
    print(f"\n📊 Loading IG trades from: {csv_path}")
    trades = load_trades(csv_path)

    if not trades:
        print("❌ No completed trades found in", csv_path)
        sys.exit(1)

    wins = sum(1 for t in trades if t.get("result") == "WIN")
    print(f"   Found {len(trades)} closed trades | {wins}W / {len(trades)-wins}L\n")

    if len(trades) < args.min:
        print(f"⏭️  Only {len(trades)} trades (min {args.min}) — run more sessions first.")
        sys.exit(0)

    params = _load_current_params()
    prompt = build_prompt(trades, params)

    print("=" * 60)
    print(f"🔍 Analyzing S5 / US30 — {len(trades)} trades")
    print("=" * 60)

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    resp   = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    print(resp.content[0].text.strip())
    print()


if __name__ == "__main__":
    main()
