# Trade Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a slide-in chat panel to the dashboard that lets the user ask multi-turn questions about specific closed trades, with Claude having access to trade snapshot data and relevant strategy config rules.

**Architecture:** A new `claude_analyst.py` module handles context assembly and streaming Claude API calls with prompt caching. A new `POST /api/chat` SSE endpoint in `dashboard.py` consumes it. The dashboard's right column gains a chat panel that slides in when a trade row is clicked, replacing the trade history view while open.

**Tech Stack:** Python `anthropic` SDK (already installed), FastAPI `StreamingResponse`, browser `fetch` with SSE stream reader, existing dashboard CSS/JS patterns.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `claude_analyst.py` | Create | Context assembly + streaming Claude API call |
| `dashboard.py` | Modify | Add `POST /api/chat` streaming endpoint |
| `dashboard.html` | Modify | Chat panel UI + SSE stream consumer |
| `tests/test_claude_analyst.py` | Create | Unit tests for context assembly |

---

## Task 1: `claude_analyst.py` — context assembly

**Files:**
- Create: `claude_analyst.py`
- Create: `tests/test_claude_analyst.py`

- [ ] **Step 1: Write failing tests for `build_system_prompt`**

Create `tests/test_claude_analyst.py`:

```python
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import claude_analyst


TRADE_S3 = {
    "symbol": "ONTUSDT", "side": "LONG", "strategy": "S3",
    "entry": "0.07458", "sl": "0.07132", "tp": "0.08204",
    "exit_price": "0.07134", "result": "LOSS", "pnl": "-2.8391",
    "pnl_pct": "-43.44", "exit_reason": "SL",
    "snap_adx": "62.5", "snap_daily_rsi": "73.8",
    "snap_sentiment": "BULLISH", "snap_rsi": "",       # empty — should be skipped
    "snap_htf": "", "snap_coil": "",
    "snap_entry_trigger": "0.0738209", "snap_sl": "0.07131708", "snap_rr": "2.95",
    "snap_rsi_peak": "", "snap_spike_body_pct": "",
}

TRADE_UNKNOWN = {
    "symbol": "XYZUSDT", "side": "SHORT", "strategy": "UNKNOWN",
    "entry": "1.0", "sl": "1.05", "tp": "0.90",
    "exit_price": "1.03", "result": "LOSS", "pnl": "-1.5",
    "pnl_pct": "-15.0", "exit_reason": "SL",
}


def test_trade_block_present():
    prompt = claude_analyst.build_system_prompt(TRADE_S3)
    assert "ONTUSDT" in prompt
    assert "LONG" in prompt
    assert "0.07458" in prompt
    assert "-2.8391" in prompt
    assert "-43.44" in prompt


def test_snap_fields_non_null_only():
    prompt = claude_analyst.build_system_prompt(TRADE_S3)
    assert "ADX: 62.5" in prompt
    assert "Daily RSI: 73.8" in prompt
    # empty snap fields must not appear
    assert "snap_rsi" not in prompt
    assert "snap_htf" not in prompt


def test_strategy_config_included_for_s3():
    prompt = claude_analyst.build_system_prompt(TRADE_S3)
    # decision-relevant S3 constants must appear
    assert "S3_ADX_MIN" in prompt
    assert "S3_MIN_RR" in prompt
    assert "S3_USE_SWING_TRAIL" in prompt
    assert "S3_ENABLED" in prompt


def test_config_excludes_non_threshold_constants():
    prompt = claude_analyst.build_system_prompt(TRADE_S3)
    # leverage and trade size are not decision-relevant
    assert "S3_LEVERAGE" not in prompt
    assert "S3_TRADE_SIZE_PCT" not in prompt
    assert "S3_LTF_INTERVAL" not in prompt


def test_unknown_strategy_no_config_crash():
    # must not raise even when strategy has no config file
    prompt = claude_analyst.build_system_prompt(TRADE_UNKNOWN)
    assert "XYZUSDT" in prompt
    assert "Strategy config unavailable" in prompt
```

- [ ] **Step 2: Run tests — verify they all fail**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
python -m pytest tests/test_claude_analyst.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError: No module named 'claude_analyst'`

- [ ] **Step 3: Create `claude_analyst.py` with `build_system_prompt`**

Create `claude_analyst.py`:

```python
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
    n = strategy.lstrip("S").lstrip("s")
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
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
python -m pytest tests/test_claude_analyst.py -v
```

Expected output:
```
tests/test_claude_analyst.py::test_trade_block_present PASSED
tests/test_claude_analyst.py::test_snap_fields_non_null_only PASSED
tests/test_claude_analyst.py::test_strategy_config_included_for_s3 PASSED
tests/test_claude_analyst.py::test_config_excludes_non_threshold_constants PASSED
tests/test_claude_analyst.py::test_unknown_strategy_no_config_crash PASSED
5 passed
```

- [ ] **Step 5: Commit**

```bash
git add claude_analyst.py tests/test_claude_analyst.py
git commit -m "feat(chat): add claude_analyst — context assembly + streaming"
```

---

## Task 2: `dashboard.py` — `POST /api/chat` streaming endpoint

**Files:**
- Modify: `dashboard.py`

- [ ] **Step 1: Write a failing test for the endpoint shape**

Add to `tests/test_ui.py` (file already exists — append these):

```python
def test_chat_endpoint_exists():
    """POST /api/chat must exist and return SSE content-type."""
    import claude_analyst as _ca
    # Patch stream_response to avoid real API call
    original = _ca.stream_response
    _ca.stream_response = lambda system, messages: iter(["Hello", " world"])
    try:
        resp = client.post("/api/chat", json={
            "trade": {
                "symbol": "ONTUSDT", "side": "LONG", "strategy": "S3",
                "entry": "0.07458", "sl": "0.07132", "tp": "0.08204",
                "exit_price": "0.07134", "result": "LOSS",
                "pnl": "-2.8391", "pnl_pct": "-43.44", "exit_reason": "SL",
            },
            "messages": [{"role": "user", "content": "Was this entry valid?"}],
        })
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        body = resp.text
        assert "data: Hello" in body
        assert "data: [DONE]" in body
    finally:
        _ca.stream_response = original


def test_chat_endpoint_missing_api_key(monkeypatch):
    """Missing ANTHROPIC_API_KEY returns 200 SSE with error message."""
    import claude_analyst as _ca
    def _raise(system, messages):
        raise Exception("No API key")
        yield  # make it a generator
    original = _ca.stream_response
    _ca.stream_response = _raise
    try:
        resp = client.post("/api/chat", json={
            "trade": {"symbol": "X", "strategy": "S3"},
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 200
        assert "error" in resp.text.lower() or "⚠" in resp.text
    finally:
        _ca.stream_response = original
```

- [ ] **Step 2: Run new tests — verify they fail**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
python -m pytest tests/test_ui.py::test_chat_endpoint_exists tests/test_ui.py::test_chat_endpoint_missing_api_key -v 2>&1 | tail -10
```

Expected: both FAIL (endpoint doesn't exist yet).

- [ ] **Step 3: Add the endpoint to `dashboard.py`**

Add these two imports at the top of `dashboard.py` alongside the existing imports:

```python
from fastapi import Request
from fastapi.responses import StreamingResponse
```

Then add the endpoint after the existing `/api/state` endpoint (around line 161):

```python
@app.post("/api/chat")
async def chat(request: Request):
    """Stream a Claude trade analysis response via SSE."""
    import claude_analyst
    body      = await request.json()
    trade     = body.get("trade", {})
    messages  = body.get("messages", [])

    def generate():
        try:
            system = claude_analyst.build_system_prompt(trade)
            for token in claude_analyst.stream_response(system, messages):
                # SSE format: each token on its own data line
                yield f"data: {token}\n\n"
        except Exception as e:
            yield f"data: ⚠ Error: {e}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
python -m pytest tests/test_ui.py::test_chat_endpoint_exists tests/test_ui.py::test_chat_endpoint_missing_api_key -v
```

Expected: both PASS.

- [ ] **Step 5: Run full test suite — verify nothing broken**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
python -m pytest tests/ -v 2>&1 | tail -15
```

Expected: all previously passing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add dashboard.py
git commit -m "feat(chat): add POST /api/chat SSE streaming endpoint"
```

---

## Task 3: `dashboard.html` — chat panel UI + SSE consumer

**Files:**
- Modify: `dashboard.html`

This task has no automated tests — verify manually by opening the dashboard and clicking a trade row.

- [ ] **Step 1: Add chat panel CSS**

In `dashboard.html`, find the block ending with `.hist-pnl.neg { color: var(--rose); }` (around line 438) and add after it:

```css
/* ── Trade Chat Panel ───────────────────────────────── */
.chat-panel { display:flex; flex-direction:column; height:100%; }
.chat-header { display:flex; align-items:center; justify-content:space-between;
               padding:8px 12px; border-bottom:1px solid var(--border);
               background:var(--surface); flex-shrink:0; }
.chat-header-left { display:flex; align-items:center; gap:8px; }
.chat-trade-sym { color:var(--fg); font-weight:600; font-size:12px; }
.chat-trade-meta { color:var(--muted); font-size:9px; }
.chat-close-btn { color:var(--muted); cursor:pointer; font-size:14px; line-height:1;
                  background:none; border:none; padding:0; }
.chat-close-btn:hover { color:var(--fg); }
.chat-strip { display:flex; gap:14px; padding:5px 12px;
              border-bottom:1px solid var(--border); flex-shrink:0; flex-wrap:wrap; }
.chat-strip-item { display:flex; flex-direction:column; gap:1px; }
.chat-strip-key { color:var(--muted); font-size:7px; text-transform:uppercase; letter-spacing:.5px; }
.chat-strip-val { color:var(--fg-dim); font-size:10px; }
.chat-messages { flex:1; overflow-y:auto; padding:12px; display:flex;
                 flex-direction:column; gap:10px; min-height:0; }
.chat-msg { display:flex; gap:6px; align-items:flex-start; }
.chat-msg.user { justify-content:flex-end; }
.chat-msg.user .chat-bubble { background:#1f3a5f; border-radius:8px 8px 2px 8px; max-width:88%; }
.chat-msg.assistant .chat-bubble { background:transparent; max-width:94%; }
.chat-bubble { color:var(--fg-dim); font-size:10px; line-height:1.6; padding:6px 10px; }
.chat-assistant-icon { color:var(--emerald); font-size:9px; margin-top:3px; flex-shrink:0; }
.chat-cursor { display:inline-block; width:7px; height:11px; background:var(--emerald);
               animation:blink-cursor .9s step-end infinite; vertical-align:text-bottom; }
@keyframes blink-cursor { 0%,100%{opacity:1} 50%{opacity:0} }
.chat-input-row { display:flex; gap:6px; padding:8px 12px;
                  border-top:1px solid var(--border); flex-shrink:0; }
.chat-input { flex:1; background:var(--surface); border:1px solid var(--border);
              border-radius:4px; padding:5px 8px; color:var(--fg); font-family:inherit;
              font-size:10px; outline:none; resize:none; }
.chat-input:focus { border-color:var(--blue); }
.chat-send-btn { background:var(--emerald); border:none; border-radius:4px; color:#000;
                 padding:4px 10px; font-size:10px; cursor:pointer; font-family:inherit;
                 font-weight:600; flex-shrink:0; }
.chat-send-btn:disabled { opacity:.4; cursor:default; }
```

- [ ] **Step 2: Add chat panel HTML in the right column**

Find in `dashboard.html`:
```html
    <div class="col-right">
      <div class="panel" style="flex:1;">
        <div class="panel-header">
          <span class="panel-title">Trade History</span>
          <span class="panel-badge" id="hist-count">0</span>
        </div>
        <div class="panel-body" id="trade-history"></div>
      </div>
    </div>
```

Replace with:
```html
    <div class="col-right" id="col-right">
      <!-- Trade History (default view) -->
      <div class="panel" style="flex:1;" id="history-panel">
        <div class="panel-header">
          <span class="panel-title">Trade History</span>
          <span class="panel-badge" id="hist-count">0</span>
        </div>
        <div class="panel-body" id="trade-history"></div>
      </div>
      <!-- Chat Panel (shown when trade clicked) -->
      <div class="panel" style="flex:1;display:none;" id="chat-panel-container">
        <div class="chat-panel">
          <div class="chat-header">
            <div class="chat-header-left">
              <span class="chat-trade-sym" id="chat-sym">—</span>
              <span class="chat-trade-meta" id="chat-meta"></span>
            </div>
            <button class="chat-close-btn" onclick="closeChat()">✕</button>
          </div>
          <div class="chat-strip" id="chat-strip"></div>
          <div class="chat-messages" id="chat-messages"></div>
          <div class="chat-input-row">
            <textarea class="chat-input" id="chat-input" rows="1"
              placeholder="Ask about this trade…"
              onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendChat();}"></textarea>
            <button class="chat-send-btn" id="chat-send" onclick="sendChat()">↵</button>
          </div>
        </div>
      </div>
    </div>
```

- [ ] **Step 3: Add chat JS — state, open/close, render**

Find the line `// ── Polling ────────────────────────────────────────────` in `dashboard.html` and add the following block immediately before it:

```javascript
// ── Trade Chat ────────────────────────────────────────
let _chatTrade    = null;    // trade object currently in chat
let _chatMessages = [];      // [{role, content}] conversation history
let _chatStreaming = false;   // true while SSE stream is active

function openChat(trade) {
  _chatTrade    = trade;
  _chatMessages = [];
  _chatStreaming = false;

  // Header
  const sym = (trade.symbol || '').replace('USDT', '');
  const res = trade.result || '—';
  const resClass = res === 'WIN' ? 'pos' : res === 'LOSS' ? 'neg' : '';
  $('chat-sym').textContent = sym;
  $('chat-meta').innerHTML =
    `<span class="hist-pnl ${resClass}" style="font-size:9px;">${res}</span>` +
    ` <span style="color:var(--muted);font-size:9px;">${trade.strategy || ''} · ${trade.side || ''} · ${trade.pnl_pct != null ? trade.pnl_pct + '%' : ''}</span>`;

  // Summary strip
  const stripItems = [
    ['ENTRY',  trade.entry],
    ['EXIT',   trade.exit_price],
    ['SL',     trade.sl],
    ['RESULT', trade.result],
    ['PNL',    trade.pnl != null ? trade.pnl + ' USDT' : null],
  ].filter(([,v]) => v != null && v !== '');
  $('chat-strip').innerHTML = stripItems.map(([k,v]) =>
    `<div class="chat-strip-item">
       <span class="chat-strip-key">${k}</span>
       <span class="chat-strip-val">${v}</span>
     </div>`
  ).join('');

  $('chat-messages').innerHTML = '';
  $('chat-input').value = '';

  // Show chat panel, hide history panel
  $('history-panel').style.display = 'none';
  $('chat-panel-container').style.display = '';
  $('chat-input').focus();
}

function closeChat() {
  _chatTrade    = null;
  _chatMessages = [];
  _chatStreaming = false;
  $('chat-panel-container').style.display = 'none';
  $('history-panel').style.display = '';
}

function _appendMessage(role, content) {
  const msgs = $('chat-messages');
  const div = document.createElement('div');
  div.className = `chat-msg ${role}`;
  if (role === 'assistant') {
    div.innerHTML =
      `<span class="chat-assistant-icon">◆</span>
       <div class="chat-bubble" id="chat-bubble-${_chatMessages.length}"></div>`;
  } else {
    div.innerHTML = `<div class="chat-bubble">${content}</div>`;
  }
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
  return div;
}

async function sendChat() {
  if (_chatStreaming || !_chatTrade) return;
  const input = $('chat-input');
  const text  = input.value.trim();
  if (!text) return;

  input.value = '';
  $('chat-send').disabled = true;
  _chatStreaming = true;

  // Add user message to history and DOM
  _chatMessages.push({ role: 'user', content: text });
  _appendMessage('user', text);

  // Placeholder assistant bubble with cursor
  const assistantDiv = _appendMessage('assistant', '');
  const bubbleId = `chat-bubble-${_chatMessages.length - 1}`;
  // The bubble was appended before we pushed to _chatMessages, use DOM query
  const bubble = assistantDiv.querySelector('.chat-bubble');
  bubble.innerHTML = '<span class="chat-cursor"></span>';

  let accumulated = '';

  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ trade: _chatTrade, messages: _chatMessages }),
    });

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const chunk = decoder.decode(value, { stream: true });
      for (const line of chunk.split('\n')) {
        if (!line.startsWith('data: ')) continue;
        const token = line.slice(6);
        if (token === '[DONE]') break;
        accumulated += token;
        bubble.textContent = accumulated;
        $('chat-messages').scrollTop = $('chat-messages').scrollHeight;
      }
    }
  } catch (e) {
    accumulated += ` ⚠ Connection lost`;
    bubble.textContent = accumulated;
  }

  // Remove cursor, save assistant reply to history
  _chatStreaming = false;
  $('chat-send').disabled = false;
  _chatMessages.push({ role: 'assistant', content: accumulated });
  $('chat-input').focus();
}
```

- [ ] **Step 4: Wire trade history rows to open chat**

Find this block in `dashboard.html` (around line 1388):
```javascript
    $('trade-history').innerHTML = window._histTrades.map((t, i) => {
      const pnl = t.pnl || 0;
      const pnlClass = pnl >= 0 ? 'pos' : 'neg';
      const res = t.result || '—';
      const sym = (t.symbol || '').replace('USDT','');
      const canChart = !!(t.entry && t.open_at && t.closed_at);
      return `
        <div class="hist-row" ${canChart ? `style="cursor:pointer" onclick="openTradeChart(window._histTrades[${i}])"` : ''}>
          <span class="hist-sym">${sym}</span>
          <span class="side-tag ${(t.side||'').toLowerCase()}" style="font-size:8px;">${t.side||'?'}</span>
          <span class="hist-badge ${res}">${res}</span>
          <span class="hist-pnl ${pnlClass}">${fmtUSD(pnl)}</span>
          <span class="hist-time">${relTime(t.closed_at)}</span>
          ${canChart ? '<span style="font-size:9px;color:var(--muted);margin-left:auto">↗</span>' : ''}
        </div>`;
    }).join('');
```

Replace with:
```javascript
    $('trade-history').innerHTML = window._histTrades.map((t, i) => {
      const pnl = t.pnl || 0;
      const pnlClass = pnl >= 0 ? 'pos' : 'neg';
      const res = t.result || '—';
      const sym = (t.symbol || '').replace('USDT','');
      const canChart = !!(t.entry && t.open_at && t.closed_at);
      return `
        <div class="hist-row" style="cursor:pointer"
             onclick="openChat(window._histTrades[${i}])"
             ondblclick="${canChart ? `openTradeChart(window._histTrades[${i}])` : ''}">
          <span class="hist-sym">${sym}</span>
          <span class="side-tag ${(t.side||'').toLowerCase()}" style="font-size:8px;">${t.side||'?'}</span>
          <span class="hist-badge ${res}">${res}</span>
          <span class="hist-pnl ${pnlClass}">${fmtUSD(pnl)}</span>
          <span class="hist-time">${relTime(t.closed_at)}</span>
          <span style="font-size:9px;color:var(--muted);margin-left:auto">💬${canChart ? ' ↗' : ''}</span>
        </div>`;
    }).join('');
```

Single click opens chat. Double click on chartable trades still opens the chart.

- [ ] **Step 5: Manual verification**

```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
python dashboard.py
```

Open `http://localhost:8080` in the browser. Verify:
1. Trade history rows show `💬` icon
2. Clicking a trade row opens the chat panel in the right column
3. Trade header shows symbol, result, strategy, side, PnL%
4. Summary strip shows entry/exit/SL/result/PnL
5. Typing a message and pressing Enter (or ↵ button) fires the request
6. Response streams in token-by-token with blinking cursor
7. Follow-up messages work (multi-turn)
8. ✕ button restores the trade history panel
9. Double-clicking a chartable trade still opens the chart overlay

- [ ] **Step 6: Commit**

```bash
git add dashboard.html
git commit -m "feat(chat): add trade chat panel UI with SSE stream consumer"
```

---

## Task 4: Wire `ANTHROPIC_API_KEY` error to panel message

The panel should show a clear message if the API key is missing, not a raw error string.

**Files:**
- Modify: `dashboard.html` (the `sendChat` function)

- [ ] **Step 1: Update error display in `sendChat`**

In the `sendChat` function added in Task 3, find:

```javascript
  } catch (e) {
    accumulated += ` ⚠ Connection lost`;
    bubble.textContent = accumulated;
  }
```

Replace with:

```javascript
  } catch (e) {
    const msg = String(e).includes('API key') || accumulated === ''
      ? '⚠ Claude API key not configured. Set ANTHROPIC_API_KEY and restart dashboard.'
      : accumulated + ' ⚠ Connection lost';
    bubble.textContent = msg;
  }
```

- [ ] **Step 2: Manual verification**

With `ANTHROPIC_API_KEY` unset:
```bash
cd /Users/kevin/Downloads/bitget_mtf_bot
ANTHROPIC_API_KEY="" python dashboard.py
```

Open dashboard, click a trade, send a message. Expected: panel shows the "API key not configured" message instead of crashing or showing a blank bubble.

- [ ] **Step 3: Commit**

```bash
git add dashboard.html
git commit -m "feat(chat): friendly API key error message in chat panel"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| Slide-in panel on trade click | Task 3 Step 2+4 |
| Multi-turn conversation | Task 3 Step 3 (`_chatMessages` array) |
| User initiates (no auto-analysis) | Task 3 Step 3 (blank panel on open) |
| Trade data in context | Task 1 Step 3 (`build_system_prompt`) |
| Strategy config in context | Task 1 Step 3 (`_load_strategy_config`) |
| Trim snap fields (non-null only) | Task 1 Step 3 (`_format_snap_fields`) |
| Trim config (decision-relevant only) | Task 1 Step 3 (`_RELEVANT_PATTERNS`) |
| Prompt caching | Task 1 Step 3 (`cache_control: ephemeral`) |
| Sliding window (last 6 messages) | Task 1 Step 3 (`messages[-6:]`) |
| Haiku model, max 350 tokens | Task 1 Step 3 |
| SSE streaming endpoint | Task 2 Step 3 |
| `✕` closes panel, restores history | Task 3 Step 3 (`closeChat`) |
| ANTHROPIC_API_KEY missing → message | Task 2 Step 3 + Task 4 |
| Stream error → partial + warning | Task 3 Step 3 (catch block) |
| Config file not found → graceful | Task 1 Step 3 (`ModuleNotFoundError`) |
| Unknown strategy → no crash | Task 1 test + implementation |

**Placeholder scan:** None found.

**Type consistency:** `build_system_prompt(trade: dict) -> str` and `stream_response(system: str, messages: list[dict]) -> Iterator[str]` used consistently across Task 1 (implementation), Task 2 (endpoint), and Task 3 (N/A — frontend only).
