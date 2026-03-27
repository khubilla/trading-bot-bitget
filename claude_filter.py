"""
claude_filter.py — Claude trade approval filter

Called before open_long / open_short in S2, S3, S4.
Reads recent trade history, sends current signal indicators to Claude,
and returns approve / reject with a one-line reason.

Only runs when config.CLAUDE_FILTER_ENABLED = True.
On any API error, defaults to approved=True so trades are never blocked
by an infrastructure failure.
"""

import os
import csv
import config

_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    return _client


def _load_history(n: int) -> list[dict]:
    """Load last n rows from trades.csv."""
    path = config.TRADE_LOG
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    return rows[-n:]


def _pct(entry, close_price, side):
    """Rough P/L % given open entry, close price, and side."""
    try:
        e, c = float(entry), float(close_price)
        if not e:
            return None
        if side == "SHORT":
            return round((e - c) / e * 100, 1)
        return round((c - e) / e * 100, 1)
    except Exception:
        return None


def _format_history(rows: list[dict]) -> str:
    """
    Pair OPEN + CLOSE rows, compute P/L, format as a readable table.
    Shows at most 20 closed trades.
    """
    opens = {}
    lines = []

    for r in rows:
        action = r.get("action", "")
        sym = r.get("symbol", "")

        if "_CLOSE" in action:
            if sym in opens:
                o = opens.pop(sym)
                pnl = _pct(o.get("entry", 0), r.get("entry", 0), o.get("side", "LONG"))
                if pnl is None:
                    continue
                result = f"+{pnl}% WIN" if pnl > 0 else f"{pnl}% LOSS"
                strat = o.get("strategy", "?")
                rsi   = o.get("snap_daily_rsi") or o.get("snap_rsi") or "?"
                sent  = o.get("snap_sentiment", "?")
                ts    = o.get("timestamp", "")[:10]
                lines.append(
                    f"{ts}  {strat:<3} {sym:<14} {result:<12}  RSI={rsi:<6} sentiment={sent}"
                )
        elif action:
            # Any non-close action with a symbol is a potential open
            opens[sym] = r

    return "\n".join(lines[-20:]) if lines else "(no closed trades yet)"


def _build_prompt(strategy: str, symbol: str, signal_data: dict, history_str: str) -> str:
    sig_lines = "\n".join(f"  {k}: {v}" for k, v in signal_data.items())
    return f"""You are a crypto futures trading filter. Your job is to assess setup quality based on indicator patterns, NOT which coin is trading.

Recent closed trades (newest at bottom):
{history_str}

Current signal:
  Strategy: {strategy}
  Symbol: {symbol}
{sig_lines}

Rules:
- Focus on INDICATOR VALUES (RSI, sentiment, S/R clearance) — not the symbol name
- A coin that lost before can win again under different market conditions
- REJECT only if the current indicator profile closely matches losing setups (e.g. low RSI, wrong sentiment, tight S/R)
- APPROVE if the indicator profile matches winning setups or there is insufficient history to judge
- When in doubt, APPROVE — missing a good trade is worse than taking a borderline one

Reply with exactly one line starting with APPROVE or REJECT, followed by a dash and a brief reason (max 15 words).
Example: APPROVE - RSI 78 and bullish sentiment match winning trade profile.
Example: REJECT - RSI 71 with neutral sentiment matches recent losing setups."""


def claude_approve(strategy: str, symbol: str, signal_data: dict) -> dict:
    """
    Returns {"approved": bool, "reason": str}.

    On any error (API down, missing key, etc.) defaults to approved=True
    so trades are never blocked by infrastructure issues.
    """
    if not config.CLAUDE_FILTER_ENABLED:
        return {"approved": True, "reason": "filter disabled"}

    try:
        rows = _load_history(config.CLAUDE_FILTER_HISTORY_N)
        history_str = _format_history(rows)
        prompt = _build_prompt(strategy, symbol, signal_data, history_str)

        resp = _get_client().messages.create(
            model=config.CLAUDE_FILTER_MODEL,
            max_tokens=80,
            messages=[{"role": "user", "content": prompt}],
        )

        text = resp.content[0].text.strip()
        approved = text.upper().startswith("APPROVE")
        reason = text.split("-", 1)[-1].strip() if "-" in text else text
        return {"approved": approved, "reason": reason}

    except Exception as e:
        return {"approved": True, "reason": f"filter error (defaulting approve): {e}"}
