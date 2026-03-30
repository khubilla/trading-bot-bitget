"""
claude_analyst.py — Trade analysis context assembly + streaming Claude calls.

Keeps all Anthropic SDK usage isolated, same pattern as claude_filter.py.
"""

import os
import importlib
from typing import Iterator

_client = None

# Snap field display names (key = CSV column name, value = display label)
_SNAP_LABELS = {
    "snap_rsi":           "RSI",
    "snap_adx":           "ADX",
    "snap_htf":           "HTF bias",
    "snap_coil":          "Coil",
    "snap_box_range_pct": "Box range %",
    "snap_sentiment":     "Sentiment",
    "snap_daily_rsi":     "Daily RSI",
    "snap_entry_trigger": "Entry trigger",
    "snap_sl":            "Snap SL",
    "snap_rr":            "RR",
    "snap_rsi_peak":      "RSI peak",
    "snap_spike_body_pct":"Spike body %",
    "snap_rsi_div":       "RSI div",
    "snap_rsi_div_str":   "RSI div strength",
    "snap_s5_ob_low":     "OB low",
    "snap_s5_ob_high":    "OB high",
    "snap_s5_tp":         "S5 TP",
    "snap_sr_clearance_pct": "S/R clearance %",
}

# Config constant name patterns considered decision-relevant
_RELEVANT_PATTERNS = ("_MIN_", "_MAX_", "_USE_", "_ENABLED", "_THRESHOLD", "_BUFFER", "_ADX_", "_STOCH_OVERSOLD", "_MIN_RR", "_MIN_SR")


def _get_client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    return _client


def _load_strategy_config(strategy: str) -> str:
    """
    Load decision-relevant constants from config_sN.py.
    Returns formatted string or note if unavailable.
    Strategy is like 'S3', 'S2', etc.
    """
    n = strategy.upper().removeprefix("S")
    if not n.isdigit():
        return "Strategy config unavailable"
    module_name = f"config_s{n}"
    try:
        mod = importlib.import_module(module_name)
    except ModuleNotFoundError:
        return "Strategy config unavailable"

    lines = []
    for name in dir(mod):
        if name.startswith("_"):
            continue
        if any(pat in name for pat in _RELEVANT_PATTERNS):
            val = getattr(mod, name)
            lines.append(f"{name} = {val}")
    return "\n".join(lines) if lines else "Strategy config unavailable"


def _format_snap_fields(trade: dict) -> str:
    """Return non-empty snap fields as 'Label: value' lines."""
    lines = []
    for key, label in _SNAP_LABELS.items():
        val = trade.get(key, "")
        if val not in (None, "", "None"):
            lines.append(f"{label}: {val}")
    return "\n".join(lines) if lines else "(no indicator snapshot)"


def build_system_prompt(trade: dict) -> str:
    """
    Build the cacheable system prompt for a trade thread.
    Includes trade data, non-null snap fields, and trimmed strategy config.
    """
    strategy = trade.get("strategy", "UNKNOWN")
    config_block = _load_strategy_config(strategy)
    snap_block = _format_snap_fields(trade)

    return f"""You are a trading analyst for a Bitget futures bot. Be direct and concise.

TRADE:
Symbol: {trade.get('symbol')} | Side: {trade.get('side')} | Strategy: {strategy}
Entry: {trade.get('entry')} | SL: {trade.get('sl')} | TP: {trade.get('tp')}
Exit: {trade.get('exit_price')} | Result: {trade.get('result')} | PnL: {trade.get('pnl')} USDT ({trade.get('pnl_pct')}%)
Exit reason: {trade.get('exit_reason') or 'unknown'}

INDICATORS AT ENTRY:
{snap_block}

STRATEGY RULES ({strategy} config — decision-relevant constants only):
{config_block}"""


def stream_response(system: str, messages: list[dict]) -> Iterator[str]:
    """
    Stream Claude response tokens for a multi-turn trade conversation.
    Uses prompt caching on the system prompt to minimise costs on follow-up turns.
    Yields text token strings. Raises on API/config errors.
    """
    client = _get_client()
    with client.messages.stream(
        model="claude-haiku-4-5-20251001",
        max_tokens=350,
        system=[
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=messages[-6:],   # sliding window — last 6 messages only
    ) as stream:
        for text in stream.text_stream:
            yield text
