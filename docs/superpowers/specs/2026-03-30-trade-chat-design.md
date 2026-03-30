# Trade Chat — Design Spec
**Date:** 2026-03-30

## Overview

A slide-in chat panel in the dashboard that lets the user ask multi-turn questions about specific closed trades. Claude has access to the trade's snapshot data and the relevant strategy config rules to give grounded, verifiable answers.

---

## Scope

- Ask questions about a specific trade (entry validity, exit analysis, what could have filtered it)
- Multi-turn conversation per trade session
- User initiates — no auto-analysis on open
- Specific trades only (not strategy stats or live market questions)

Out of scope: strategy performance aggregation, live market advice, open trade commentary.

---

## Architecture

### New file: `claude_analyst.py`

Responsible for context assembly and the Claude API call. Keeps Anthropic SDK usage isolated (same pattern as `claude_filter.py`).

```python
def build_system_prompt(trade: dict) -> str
    """Returns system prompt string with trade data + trimmed strategy config."""

def stream_response(system: str, messages: list[dict]) -> Iterator[str]
    """Streams Claude response tokens. Uses prompt caching on system prompt."""
```

**Model:** `claude-haiku-4-5-20251001` (cheapest, fast)
**Max tokens:** 350 per response

**Prompt caching — concrete API call:**

```python
client.messages.stream(
    model="claude-haiku-4-5-20251001",
    max_tokens=350,
    system=[
        {
            "type": "text",
            "text": system_prompt,          # trade data + config rules
            "cache_control": {"type": "ephemeral"},  # ← cache this block
        }
    ],
    messages=messages[-6:],                 # sliding window: last 6 only
)
```

The `cache_control` block tells Anthropic to cache everything up to and including that system prompt prefix. On the first call it's written to cache (full price). All subsequent turns in the same thread that send the identical system prompt text hit the cache — billed at ~10% of normal input token cost. Cache lifetime is 5 minutes; a typical trade Q&A session stays well within that.

### Modified: `dashboard.py`

New endpoint:

```
POST /api/chat
Body: { "trade": {...}, "messages": [{"role": "user"|"assistant", "content": "..."}] }
Response: StreamingResponse (text/event-stream, SSE)
```

Calls `claude_analyst.stream_response()` and yields tokens as `data: <token>\n\n`. Sends `data: [DONE]\n\n` on completion.

### Modified: `dashboard.html`

- Trade history row click: opens chat panel instead of (or in addition to) chart overlay — needs a decision at implementation time based on how the existing chart overlay interacts
- Chat panel replaces right column content while open; `✕` restores trade history
- JS stores `messages[]` array in memory per trade session (cleared on panel close)
- Fetch consumes SSE stream, appends tokens to the active assistant bubble as they arrive

---

## Context Assembly (`build_system_prompt`)

**System prompt structure (~150 tokens for a typical S3 trade):**

```
You are a trading analyst for a Bitget futures bot. Be direct and concise.

TRADE:
Symbol: {symbol} | Side: {side} | Strategy: {strategy}
Entry: {entry} | SL: {sl} | TP: {tp}
Exit: {exit_price} | Result: {result} | PnL: {pnl} USDT ({pnl_pct}%)
Exit reason: {exit_reason}

INDICATORS AT ENTRY:
{non-null snap fields only, formatted as Key: Value}

STRATEGY RULES ({config_sN}.py — decision-relevant constants only):
{S{N}_MIN_* and S{N}_USE_* and S{N}_*_ENABLED constants only}
```

**Config trimming rules:**
- Include only constants matching: `*_MIN_*`, `*_MAX_*`, `*_USE_*`, `*_ENABLED`, `*_THRESHOLD*`, `*_BUFFER*`
- Skip: leverage, trade size, interval, symbol lists, non-threshold values

**Snap field trimming:** Skip any snap field where the CSV value is empty/null.

---

## Conversation History

- Full messages array sent on each turn (standard Claude multi-turn)
- **Sliding window:** cap at last 6 messages (3 user + 3 assistant) to control token growth
- History cleared when panel is closed

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| `ANTHROPIC_API_KEY` not set | Panel shows: "Claude API key not configured" |
| Stream error mid-response | Partial response stays; append "⚠ Connection lost" |
| `config_sN.py` not found | Trade data still sent; note "Strategy config unavailable" in system prompt |
| Non-S1–S5 strategy | Load no config; analysis based on trade data only |

---

## Files Changed

| File | Change |
|---|---|
| `claude_analyst.py` | New — context assembly + streaming API call |
| `dashboard.py` | Add `POST /api/chat` streaming endpoint |
| `dashboard.html` | Add chat panel UI + SSE stream consumer |

---

## Token Budget (per conversation)

| Item | Tokens | Charged |
|---|---|---|
| System prompt (first turn) | ~150 | Full price |
| System prompt (subsequent turns) | ~150 | ~10% (cached) |
| User message | ~20–50 | Full price |
| Assistant response | ~100–350 | Full price |
| Typical 3-turn thread | ~900 total | ~$0.0002 (Haiku) |
