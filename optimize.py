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
import config_s1
import config_s2
import config_s3
import config_s4
import config_s5
import config_s6
import config_s7

# ── Config ────────────────────────────────────────────────────────── #

MODEL       = "claude-sonnet-4-6"
MIN_TRADES  = 10   # minimum closed trades per strategy before analyzing

# Pull current params live from config modules — never hardcode
def _cfg(module, *names):
    return {n: getattr(module, n) for n in names if hasattr(module, n)}

CURRENT_PARAMS = {
    "S1": _cfg(config_s1,
        "RSI_LONG_THRESH", "RSI_SHORT_THRESH", "ADX_TREND_THRESHOLD",
        "CONSOLIDATION_RANGE_PCT", "BREAKOUT_BUFFER_PCT",
        "TAKE_PROFIT_PCT", "STOP_LOSS_PCT",
        "S1_SL_BUFFER_PCT", "S1_MIN_SR_CLEARANCE",
        "S1_USE_SWING_TRAIL", "S1_TRAIL_RANGE_PCT", "S1_SWING_LOOKBACK",
    ),
    "S2": _cfg(config_s2,
        "S2_RSI_LONG_THRESH",
        "S2_BIG_CANDLE_BODY_PCT", "S2_BIG_CANDLE_LOOKBACK",
        "S2_CONSOL_RANGE_PCT", "S2_CONSOL_CANDLES",
        "S2_MAX_ENTRY_BUFFER", "S2_MIN_SR_CLEARANCE",
        "S2_TRAILING_TRIGGER_PCT", "S2_TRAILING_RANGE_PCT",
        "S2_USE_SWING_TRAIL", "S2_SWING_LOOKBACK",
    ),
    "S3": _cfg(config_s3,
        "S3_ADX_MIN", "S3_ADX_MAX",
        "S3_STOCH_OVERSOLD", "S3_STOCH_LOOKBACK",
        "S3_MIN_RR", "S3_MIN_SR_CLEARANCE",
        "S3_MAX_ENTRY_BUFFER", "S3_ENTRY_BUFFER_PCT",
        "S3_TRAILING_TRIGGER_PCT", "S3_TRAILING_RANGE_PCT",
        "S3_USE_SWING_TRAIL", "S3_SWING_LOOKBACK",
        "S3_DAILY_GAIN_MIN",
    ),
    "S4": _cfg(config_s4,
        "S4_RSI_PEAK_THRESH", "S4_RSI_STILL_HOT_THRESH",
        "S4_RSI_DIV_MIN_DROP", "S4_RSI_PEAK_LOOKBACK",
        "S4_BIG_CANDLE_BODY_PCT", "S4_BIG_CANDLE_LOOKBACK",
        "S4_ENTRY_BUFFER", "S4_MAX_ENTRY_BUFFER",
        "S4_MIN_SR_CLEARANCE", "S4_LOW_LOOKBACK",
        "S4_TRAILING_TRIGGER_PCT", "S4_TRAILING_RANGE_PCT",
        "S4_USE_SWING_TRAIL", "S4_SWING_LOOKBACK",
    ),
    "S5": _cfg(config_s5,
        "S5_HTF_BOS_LOOKBACK", "S5_OB_LOOKBACK",
        "S5_OB_MIN_IMPULSE", "S5_OB_MIN_RANGE_PCT",
        "S5_CHOCH_LOOKBACK",
        "S5_MAX_ENTRY_BUFFER", "S5_SL_BUFFER_PCT",
        "S5_MIN_SR_CLEARANCE", "S5_MIN_RR",
        "S5_SWING_LOOKBACK", "S5_TRAIL_RANGE_PCT",
        "S5_USE_SWING_TRAIL", "S5_SMC_FVG_FILTER",
    ),
    "S6": _cfg(config_s6,
        "S6_OVERBOUGHT_RSI", "S6_MIN_DROP_PCT",
        "S6_MIN_RECOVERY_RATIO", "S6_SPIKE_LOOKBACK",
        "S6_SL_PCT", "S6_TRAILING_TRIGGER_PCT", "S6_TRAIL_RANGE_PCT",
    ),
    "S7": _cfg(config_s7,
        "S7_RSI_PEAK_THRESH", "S7_RSI_STILL_HOT_THRESH",
        "S7_RSI_DIV_MIN_DROP", "S7_RSI_PEAK_LOOKBACK",
        "S7_BIG_CANDLE_BODY_PCT", "S7_BIG_CANDLE_LOOKBACK",
        "S7_ENTRY_BUFFER", "S7_MAX_ENTRY_BUFFER",
        "S7_MIN_SR_CLEARANCE",
        "S7_BOX_CONFIRM_COUNT",
        "S7_TRAILING_TRIGGER_PCT", "S7_TRAILING_RANGE_PCT",
        "S7_USE_SWING_TRAIL", "S7_SWING_LOOKBACK",
    ),
}

# Columns to include in the table sent to Claude, per strategy
STRATEGY_COLUMNS = {
    "S1": ["result", "pnl_pct", "exit_reason",
           "snap_rsi", "snap_adx", "snap_htf", "snap_coil",
           "snap_box_range_pct", "snap_sentiment"],
    "S2": ["result", "pnl_pct", "exit_reason",
           "snap_daily_rsi", "snap_sentiment", "snap_sr_clearance_pct"],
    "S3": ["result", "pnl_pct", "exit_reason",
           "snap_adx", "snap_rr", "snap_sentiment", "snap_sr_clearance_pct"],
    "S4": ["result", "pnl_pct", "exit_reason",
           "snap_rsi_peak", "snap_spike_body_pct",
           "snap_rsi_div", "snap_rsi_div_str",
           "snap_sentiment", "snap_sr_clearance_pct"],
    "S5": ["result", "pnl_pct", "exit_reason",
           "snap_rr", "snap_s5_ob_low", "snap_s5_ob_high", "snap_s5_tp",
           "snap_sentiment", "snap_sr_clearance_pct"],
    "S6": ["result", "pnl_pct", "exit_reason",
           "snap_s6_peak", "snap_s6_drop_pct", "snap_s6_rsi_at_peak",
           "snap_sentiment"],
    "S7": ["result", "pnl_pct", "exit_reason",
           "snap_rsi", "snap_rsi_peak", "snap_spike_body_pct",
           "snap_rsi_div", "snap_box_top", "snap_box_low_initial",
           "snap_sentiment", "snap_sl", "snap_sr_clearance_pct"],
}

# ── Trade loader ──────────────────────────────────────────────────── #

def load_trades(csv_path: str) -> list[dict]:
    """
    Load and pair OPEN + CLOSE rows from trades CSV.
    Keys open trades by trade_id (not symbol) to correctly handle
    multiple simultaneous open positions on the same symbol.
    Returns list of completed trades with all snapshot fields + result/pnl_pct/exit_reason.
    """
    if not os.path.exists(csv_path):
        return []

    with open(csv_path, newline="") as f:
        rows = list(csv.DictReader(f))

    opens  = {}   # trade_id → open row
    trades = []

    for r in rows:
        action   = r.get("action", "")
        trade_id = r.get("trade_id", "")
        if not action or not trade_id:
            continue

        if "_LONG" in action or "_SHORT" in action:
            opens[trade_id] = r
        elif "_CLOSE" in action:
            if trade_id not in opens:
                continue
            o = opens.pop(trade_id)

            result      = r.get("result") or o.get("result", "")
            pnl_pct     = r.get("pnl_pct") or None
            exit_reason = r.get("exit_reason", "")

            trade = {**o}
            trade["result"]      = result
            trade["pnl_pct"]     = pnl_pct
            trade["exit_reason"] = exit_reason
            trade["close_ts"]    = r.get("timestamp", "")[:10]
            trades.append(trade)
        elif "_PARTIAL" in action:
            # Partial TPs don't close the trade — ignore for full-trade analysis
            pass

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
    all_cols = ["date", "symbol"] + cols
    widths = {c: max(len(c), 10) for c in all_cols}
    widths["date"]   = max(widths["date"],   10)
    widths["symbol"] = max(widths["symbol"], 12)
    for c in cols:
        widths[c] = max(widths[c], 14)

    def pad(val, col):
        return str(val).ljust(widths[col])

    header = " | ".join(pad(c, c) for c in all_cols)
    sep    = "-+-".join("-" * widths[c] for c in all_cols)
    lines  = [header, sep]
    for t in trades:
        date   = t.get("close_ts", t.get("timestamp", ""))[:10]
        symbol = t.get("symbol", "")
        row    = [pad(date, "date"), pad(symbol, "symbol")]
        row   += [pad(_fmt(t.get(c)), c) for c in cols]
        lines.append(" | ".join(row))
    return "\n".join(lines)


def build_prompt(strategy: str, trades: list[dict]) -> str:
    params     = CURRENT_PARAMS.get(strategy, {})
    params_str = "\n".join(f"  {k} = {v}" for k, v in params.items())
    table      = format_trade_table(trades, strategy)
    wins       = sum(1 for t in trades if t.get("result") == "WIN")
    losses     = len(trades) - wins
    avg_pnl    = ""
    pnl_vals   = [float(t["pnl_pct"]) for t in trades if t.get("pnl_pct") not in (None, "", "—")]
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
