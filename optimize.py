"""
optimize.py — Claude-powered strategy parameter optimizer

Reads trade history, groups by strategy, sends to Claude Sonnet
with current config values, and prints parameter change suggestions.

Usage:
  python optimize.py           # analyze trades.csv (live)
  python optimize.py --paper   # analyze trades_paper.csv
  python optimize.py --min 5   # lower minimum trades threshold (default 10)
"""

import os, csv, sys, argparse
import anthropic
import config

# ── Config ────────────────────────────────────────────────────────── #

MODEL       = "claude-sonnet-4-6"
MIN_TRADES  = 10   # minimum closed trades per strategy before analyzing

# Current params shown to Claude so it knows the baseline
CURRENT_PARAMS = {
    "S1": {
        "RSI_LONG_THRESH":      70,
        "RSI_SHORT_THRESH":     30,
        "ADX_TREND_THRESHOLD":  25,
        "CONSOLIDATION_RANGE_PCT": "0.3%",
        "BREAKOUT_BUFFER_PCT":  "0.5%",
        "TAKE_PROFIT_PCT":      "3.3%",
        "STOP_LOSS_PCT":        "1.5%",
    },
    "S2": {
        "S2_RSI_LONG_THRESH":       70,
        "S2_BIG_CANDLE_BODY_PCT":   "20%",
        "S2_BIG_CANDLE_LOOKBACK":   30,
        "S2_CONSOL_RANGE_PCT":      "15%",
        "S2_MAX_ENTRY_BUFFER":      "4%",
        "S2_MIN_SR_CLEARANCE":      "15%",
        "S2_TRAILING_TRIGGER_PCT":  "10%",
        "S2_TRAILING_RANGE_PCT":    "10%",
    },
    "S3": {
        "S3_ADX_MIN":               30,
        "S3_STOCH_OVERSOLD":        30,
        "S3_STOCH_LOOKBACK":        8,
        "S3_MIN_RR":                2.0,
        "S3_MAX_ENTRY_BUFFER":      "4%",
        "S3_MIN_SR_CLEARANCE":      "15%",
        "S3_TRAILING_TRIGGER_PCT":  "10%",
        "S3_TRAILING_RANGE_PCT":    "10%",
        "S3_DAILY_GAIN_MIN":        "10%",
    },
    "S4": {
        "S4_RSI_PEAK_THRESH":       75,
        "S4_RSI_STILL_HOT_THRESH":  70,
        "S4_RSI_DIV_MIN_DROP":      5,
        "S4_BIG_CANDLE_BODY_PCT":   "20%",
        "S4_BIG_CANDLE_LOOKBACK":   30,
        "S4_ENTRY_BUFFER":          "1%",
        "S4_MAX_ENTRY_BUFFER":      "4%",
        "S4_MIN_SR_CLEARANCE":      "15%",
        "S4_TRAILING_TRIGGER_PCT":  "10%",
        "S4_TRAILING_RANGE_PCT":    "10%",
    },
    "S5": {
        "S5_HTF_BOS_LOOKBACK":   10,
        "S5_OB_LOOKBACK":        50,
        "S5_OB_MIN_IMPULSE":     "1%",
        "S5_CHOCH_LOOKBACK":     20,
        "S5_MAX_ENTRY_BUFFER":   "4%",
        "S5_SL_BUFFER_PCT":      "0.3%",
        "S5_MIN_SR_CLEARANCE":   "10%",
        "S5_MIN_RR":             2.0,
        "S5_SWING_LOOKBACK":     50,
        "S5_TRAIL_RANGE_PCT":    "5%",
    },
}

# Columns to include in the table sent to Claude, per strategy
STRATEGY_COLUMNS = {
    "S1": ["result", "pnl_pct", "exit_reason", "snap_rsi", "snap_adx",
           "snap_sentiment", "snap_box_range_pct"],
    "S2": ["result", "pnl_pct", "exit_reason", "snap_daily_rsi",
           "snap_sentiment", "snap_sr_clearance_pct", "snap_box_range_pct"],
    "S3": ["result", "pnl_pct", "exit_reason", "snap_adx",
           "snap_sentiment", "snap_sr_clearance_pct", "snap_rr"],
    "S4": ["result", "pnl_pct", "exit_reason", "snap_rsi_peak",
           "snap_spike_body_pct", "snap_rsi_div", "snap_sentiment",
           "snap_sr_clearance_pct"],
    "S5": ["result", "pnl_pct", "exit_reason", "snap_rr",
           "snap_s5_ob_low", "snap_s5_ob_high", "snap_s5_tp",
           "snap_sentiment", "snap_sr_clearance_pct"],
}

# ── Trade loader ──────────────────────────────────────────────────── #

def _pct(entry_open, entry_close, side):
    try:
        e, c = float(entry_open), float(entry_close)
        if not e:
            return None
        return round(((e - c) / e * 100) if side == "SHORT" else ((c - e) / e * 100), 1)
    except Exception:
        return None


def load_trades(csv_path: str) -> list[dict]:
    """
    Load and pair OPEN + CLOSE rows from trades CSV.
    Returns list of completed trades with all snapshot fields + result/pnl_pct/exit_reason.
    """
    if not os.path.exists(csv_path):
        return []

    with open(csv_path, newline="") as f:
        rows = list(csv.DictReader(f))

    opens  = {}   # symbol → open row
    trades = []

    for r in rows:
        action = r.get("action", "")
        sym    = r.get("symbol", "")
        if not action or not sym:
            continue

        if "_CLOSE" in action:
            if sym not in opens:
                continue
            o = opens.pop(sym)

            # Use stored result/pnl_pct if available (new format)
            result     = r.get("result") or o.get("result", "")
            pnl_pct    = r.get("pnl_pct") or None
            exit_reason = r.get("exit_reason", "")

            # Fall back to computing from entry prices
            if not result:
                computed = _pct(o.get("entry"), r.get("entry"), o.get("side", "LONG"))
                result   = "WIN" if computed and computed > 0 else "LOSS"
                pnl_pct  = computed

            trade = {**o}
            trade["result"]      = result
            trade["pnl_pct"]     = pnl_pct
            trade["exit_reason"] = exit_reason
            trade["close_ts"]    = r.get("timestamp", "")[:10]
            trades.append(trade)
        else:
            opens[sym] = r

    return trades


def group_by_strategy(trades: list[dict]) -> dict[str, list[dict]]:
    groups = {}
    for t in trades:
        s = t.get("strategy", "")
        if s:
            groups.setdefault(s, []).append(t)
    return groups


# ── Formatting ────────────────────────────────────────────────────── #

def _fmt(val):
    if val is None or val == "":
        return "—"
    return str(val)


def format_trade_table(trades: list[dict], strategy: str) -> str:
    cols = STRATEGY_COLUMNS.get(strategy, ["result", "pnl_pct", "snap_sentiment"])
    header = " | ".join(f"{c:<22}" for c in ["date", "symbol"] + cols)
    sep    = "-" * len(header)
    lines  = [header, sep]
    for t in trades:
        row = [t.get("close_ts", t.get("timestamp", ""))[:10], f"{t.get('symbol',''):<10}"]
        row += [f"{_fmt(t.get(c)):<22}" for c in cols]
        lines.append(" | ".join(row))
    return "\n".join(lines)


def build_prompt(strategy: str, trades: list[dict]) -> str:
    params = CURRENT_PARAMS.get(strategy, {})
    params_str = "\n".join(f"  {k} = {v}" for k, v in params.items())
    table  = format_trade_table(trades, strategy)
    wins   = sum(1 for t in trades if t.get("result") == "WIN")
    losses = len(trades) - wins
    avg_pnl = ""
    pnl_vals = [float(t["pnl_pct"]) for t in trades if t.get("pnl_pct") not in (None, "", "—")]
    if pnl_vals:
        avg_pnl = f"  Avg P/L: {sum(pnl_vals)/len(pnl_vals):+.1f}%"

    return f"""You are a trading strategy optimizer. Analyze {strategy} trade history and suggest specific parameter improvements.

Strategy: {strategy}
Total trades: {len(trades)} | Wins: {wins} | Losses: {losses} | Win rate: {wins/len(trades)*100:.0f}%{avg_pnl}

Current parameters:
{params_str}

Trade history (oldest to newest):
{table}

Analyze the patterns in wins vs losses. Look for correlations between indicator values and outcomes.
Then suggest specific parameter changes that would have filtered out losing trades while keeping winning ones.

Format your response as:
1. KEY PATTERNS (2-3 bullet points on what separates wins from losses)
2. SUGGESTED CHANGES (specific param name → current value → suggested value, with reason)
3. TRADES TO FILTER (describe the indicator profile of trades that should be skipped)

Be specific and data-driven. Only suggest changes supported by the data."""


# ── Main ──────────────────────────────────────────────────────────── #

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--paper", action="store_true", help="Use paper trades CSV")
    parser.add_argument("--min",   type=int, default=MIN_TRADES,
                        help=f"Min trades per strategy to analyze (default {MIN_TRADES})")
    args = parser.parse_args()

    csv_path = config.TRADE_LOG
    if args.paper:
        csv_path = csv_path.replace("trades.csv", "trades_paper.csv")

    print(f"\n📊 Loading trades from: {csv_path}")
    trades = load_trades(csv_path)

    if not trades:
        print("❌ No completed trades found.")
        sys.exit(1)

    groups = group_by_strategy(trades)
    print(f"   Found {len(trades)} closed trades across strategies: {list(groups.keys())}\n")

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

    for strategy, strat_trades in sorted(groups.items()):
        if len(strat_trades) < args.min:
            print(f"⏭️  {strategy}: only {len(strat_trades)} trades (min {args.min}) — skipping\n")
            continue

        wins = sum(1 for t in strat_trades if t.get("result") == "WIN")
        print(f"{'='*60}")
        print(f"🔍 Analyzing {strategy} — {len(strat_trades)} trades | {wins}W / {len(strat_trades)-wins}L")
        print(f"{'='*60}")

        prompt = build_prompt(strategy, strat_trades)

        resp = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        print(resp.content[0].text.strip())
        print()


if __name__ == "__main__":
    main()
