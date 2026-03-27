# Bitget USDT-Futures MTF Bot

Automated crypto futures trading bot for Bitget USDT-margined perpetual futures. Runs four independent strategies simultaneously with a shared live dashboard. Supports both **live trading** and **paper trading** mode.

---

## Strategies

### Strategy 1 — MTF RSI Breakout
Multi-timeframe breakout with trend and momentum filters.

| Timeframe | Filter |
|-----------|--------|
| 1D | ADX > 25 (trending, not sideways) |
| 1H | Current high > previous high (bull) / low < prev low (bear) |
| 3m | RSI > 70 (long) or < 30 (short) throughout consolidation |
| 3m | Candle closes above/below box + 0.5% buffer |

**Risk:** 30x leverage · 25% of total portfolio · SL at box edge · TP at +3.3%

---

### Strategy 2 — Daily Momentum Coil Breakout
Pure daily-chart strategy targeting post-squeeze breakouts.

1. Big momentum candle (≥20% body) within last 30 daily candles
2. Daily RSI > 70 throughout consolidation
3. 1–5 tight daily candles coiling (max 15% range)
4. Current daily candle breaks above consolidation high (1% buffer)

**Entry window:** price must be within 1–4% above the breakout trigger — entry is skipped if already missed.

**Scale-in:** opens at 2.5% margin initially; adds remaining 2.5% after 1 hour if price is still within the entry window.

**Risk:** 10x leverage · 5% of total portfolio · SL at box low · 50% partial close at +10% · 10% trailing stop on remainder

---

### Strategy 3 — 15m Swing Pullback
Long-only pullback strategy on the 15m timeframe.

**Prerequisites (15m):**
- EMA10 > EMA20 > EMA50 > EMA200 (golden alignment)
- ADX > 30 (strong trend)

**Entry (15m):**
- Slow Stochastics (5,3) recently oversold (<30) — pullback confirmed
- First green candle after oversold = uptick signal
- Price closes above uptick high + 1% buffer
- MACD line > signal line (momentum turning up)

**Entry window:** price must be within 1–4% above the entry trigger.

**Risk:** 10x leverage · 5% of total portfolio · SL below pullback pivot low · 50% partial close at +10% · 10% trailing stop on remainder

---

### Strategy 4 — Post-Pump RSI Divergence Short
Short-only strategy targeting reversals after momentum spikes.

1. Big momentum spike (≥20% body) within last 30 daily candles
2. RSI peaked above 75 within last 10 candles (was overbought)
3. Previous candle RSI still ≥70 (setup not stale)
4. Optional: RSI bearish divergence (2nd push ≥5pts lower than 1st)
5. Entry: price breaches 1% below the previous day's low (intraday)

**Entry window:** price must be 1–4% below the previous day's low — entry is skipped if already too far.

**Scale-in:** opens at 2.5% margin initially; adds remaining 2.5% after 1 hour if price is still within the entry window.

**Risk:** 10x leverage · 5% of total portfolio · SL at −50% P/L · 50% partial close at −10% · 10% trailing stop on remainder

**Sentiment gate:** only fires when market is not BULLISH.

---

### Strategy 5 — SMC Order Block Pullback
Multi-timeframe Smart Money Concepts strategy. Long or short depending on market direction.

**Structure (top-down):**
| Timeframe | Check |
|-----------|-------|
| 1D | EMA10 > EMA20 > EMA50 (bullish bias) or reverse (bearish bias) |
| 1H | Break of Structure — close above most recent 1H swing high pivot (LONG) or below swing low pivot (SHORT) |
| 15m | Order Block — last opposing candle before a ≥1% impulse of 2+ candles; OB range must be ≥0.5% |
| 15m | Pullback touches OB zone |
| 15m | Change of Character (ChoCH) — close back through OB boundary confirms entry |

**Entry:** 0.5% beyond the OB boundary. When ChoCH fires but price hasn't yet crossed the trigger, the setup is queued as **PENDING** — the entry watcher thread catches the breakout within 3–7 seconds instead of waiting for the next 60s scan cycle.

**Exits (standard SMC):** 50% partial close at 1:1 R:R → SL moves to breakeven → after partial, SL trails to the previous completed 15m candle's low (LONG) or high (SHORT) each scan cycle (`S5_USE_CANDLE_STOPS`). Remaining 50% targets the nearest structural swing high/low on 15m. Fallback 5% trailing stop if no structural target is found.

**Risk:** 10x leverage · 5% of total portfolio · SL 0.3% beyond OB outer wick · minimum 2:1 R:R required

**Optional filters (`config_s5.py`):** `S5_SMC_FVG_FILTER` — require an unfilled Fair Value Gap above/below the OB for added confluence (off by default).

**Sentiment gate:** LONG only when BULLISH or NEUTRAL · SHORT only when BEARISH.

---

All strategies share a **market sentiment gate** — volume-weighted bull/bear ratio across all scanned pairs filters the allowed trade direction.

Trade sizes are always calculated as a percentage of **total portfolio equity** (available balance + locked margin + unrealized P/L), not just the free balance.

---

## Setup

**1. Clone and install dependencies**
```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

**2. Set your API credentials**

Create a `.env` file in the project root (it is gitignored):
```bash
BITGET_API_KEY=your_api_key
BITGET_API_SECRET=your_api_secret
BITGET_API_PASSPHRASE=your_passphrase
```

`config.py` auto-loads `.env` on startup so no extra tooling is needed. Environment variables take precedence if already set (e.g. on a server).

**3. Tune strategy parameters (optional)**

| File | Purpose |
|------|---------|
| `config_s1.py` | Strategy 1 — timeframes, RSI, ADX, risk params |
| `config_s2.py` | Strategy 2 — big candle detection, coil, trailing stop params |
| `config_s3.py` | Strategy 3 — EMA alignment, Stochastics, MACD, risk params |
| `config_s4.py` | Strategy 4 — spike detection, RSI divergence, entry/exit params |
| `config_s5.py` | Strategy 5 — SMC OB lookback, ChoCH window, R:R minimum, candle stops, risk params |

Each config has an `S*_ENABLED = True/False` switch to disable a strategy without touching any other code.

---

## Running

### Live trading

**Start the bot**
```bash
python bot.py
```

**Start the dashboard** (separate terminal)
```bash
python dashboard.py
```
Then open [http://localhost:8080](http://localhost:8080).

### Paper trading (simulated, no real orders)

**Start the paper bot**
```bash
python bot.py --paper
```

**Start the paper dashboard** (separate terminal)
```bash
python dashboard.py --paper
```
Then open [http://localhost:8081](http://localhost:8081).

Paper trading uses real market data from Bitget but simulates all order execution locally. State is persisted in `paper_state.json` and `state_paper.json`. Paper trades are logged to `trades_paper.csv`.

---

### Docker (VPS deployment)

Runs the bot + dashboard as persistent containers. State files are written to `./data/` on the host so they survive container restarts and rebuilds.

**1. Clone and configure**
```bash
git clone https://github.com/khubilla/trading-bot-bitget.git
cd trading-bot-bitget
mkdir data
cp .env.example .env   # fill in API keys
```

**2. Live trading**
```bash
docker compose up -d
```
Dashboard at [http://your-server-ip:8080](http://your-server-ip:8080).

**3. Paper trading**
```bash
docker compose --profile paper up -d
```
Dashboard at [http://your-server-ip:8081](http://your-server-ip:8081).

**4. Useful commands**
```bash
docker compose logs -f bot          # live bot logs
docker compose logs -f dashboard    # dashboard logs
docker compose pull && docker compose up -d --build   # update to latest
docker compose down                 # stop everything
```

---

## Dashboard

The live dashboard shows:

- **Header stats**: Balance, Open P/L, Total Value (balance + margin + unrealised P/L), Win Rate, Total P/L, Scanned Pairs
- **Active trades**: entry price, SL, TP, current P/L %, strategy badge, margin used
- **Trade history**: closed trades with PnL, result, and strategy tag
- **Pair scanner tabs**: S1 · S2 · S3 · S4 · S5 — each showing signals and setup status per pair; S5 shows OB zone, entry trigger, SL, and structural TP lines on the 15m chart
- **Candlestick chart** with RSI and MACD subcharts, entry/SL signal lines, synced scroll across panes

---

## Claude AI Integration

### Trade Approval Filter (`claude_filter.py`)

An optional Claude Haiku gate that runs before S2, S3, and S4 execute a trade. When enabled, it sends the current signal indicators + recent trade history to Claude and asks for an APPROVE or REJECT decision with a one-line reason.

**How it works:**
- Reads the last N closed trades from `trades.csv` / `trades_paper.csv`
- Sends signal indicators (RSI, sentiment, S/R clearance, entry/SL) + trade history table to Claude
- Claude looks for patterns: does this setup's indicator profile match past wins or losses?
- Returns APPROVE or REJECT with a brief reason logged to the dashboard and bot.log
- On any API error, defaults to APPROVE — trades are never blocked by infrastructure failures

**S1 is excluded** — it's a scalper with a 3.3% TP where LLM latency would matter.

**Enable in `config.py`:**
```python
CLAUDE_FILTER_ENABLED   = True
CLAUDE_FILTER_MODEL     = "claude-3-haiku-20240307"  # cheapest model
CLAUDE_FILTER_HISTORY_N = 30                          # last N trades as context
```

Add your API key to `.env`:
```
ANTHROPIC_API_KEY=sk-ant-...
```

> The filter is most useful after 20–30+ closed trades per strategy. With fewer trades Claude has no pattern to learn from and will approve everything.

**Cost:** ~$0.001 per signal at Haiku pricing (~$0.10–0.15/month).

---

### Strategy Optimizer (`optimize.py`)

A one-off analysis script that reads all closed trades, groups them by strategy, and asks Claude Sonnet to identify win/loss patterns and suggest specific config parameter changes.

**Run it:**
```bash
python optimize.py           # analyze trades.csv (live)
python optimize.py --paper   # analyze trades_paper.csv
python optimize.py --min 5   # lower minimum trades threshold (default 10)
```

**Sample output:**
```
🔍 Analyzing S2 — 34 trades | 21W / 13L
============================================================
1. KEY PATTERNS
• Winning trades had RSI ≥ 75 in 18/21 cases; losses averaged RSI 71.2
• All 7 losses with NEUTRAL sentiment vs 18/21 wins during BULLISH
• S/R clearance < 18% appeared in 9/13 losses

2. SUGGESTED CHANGES
• S2_RSI_LONG_THRESH: 70 → 75  (filters 8 losing trades, keeps 19 winners)
• S2_MIN_SR_CLEARANCE: 0.15 → 0.18  (removes 9 losses, loses 2 winners)

3. TRADES TO FILTER
Skip S2 when RSI < 75 AND sentiment is NEUTRAL — 6 consecutive losses, 0 wins.
```

Claude suggests changes; you review and apply them manually to the config files. All five strategies (S1–S5) are covered once they have sufficient trade history.

**Cost:** ~$0.015 per run with Claude Sonnet 4.6.

---

## File Structure

```
├── bot.py              # Main entry point (--paper flag for paper mode)
├── strategy.py         # S1 + S2 + S3 + S4 + S5 signal logic
├── trader.py           # Bitget order execution (live)
├── paper_trader.py     # Simulated order execution (paper)
├── scanner.py          # Pair scanner + market sentiment
├── dashboard.py        # Live web dashboard (FastAPI, --paper flag)
├── dashboard.html      # Dashboard frontend (served by dashboard.py)
├── backtest.py         # Backtesting engine
├── bitget_client.py    # Bitget REST API client
├── state.py            # Shared in-memory + on-disk state
├── claude_filter.py    # Claude Haiku trade approval gate (S2/S3/S4)
├── optimize.py         # Claude Sonnet strategy parameter optimizer
│
├── config.py           # Credentials + paths + Claude filter settings
├── config_s1.py        # Strategy 1 parameters
├── config_s2.py        # Strategy 2 parameters
├── config_s3.py        # Strategy 3 parameters
├── config_s4.py        # Strategy 4 parameters
├── config_s5.py        # Strategy 5 parameters
│
├── .env                # Local credentials (gitignored, never commit)
├── trades.csv          # Live trade log
├── trades_paper.csv    # Paper trade log
├── state.json          # Live bot runtime state
├── state_paper.json    # Paper bot runtime state
├── paper_state.json    # Paper trader simulation state (balance, positions)
└── bot.log             # Runtime log
```

---

## Trade Log (`trades.csv` / `trades_paper.csv`)

Each trade open and close is appended as a row. Columns:

| Column | Description |
|--------|-------------|
| `timestamp` | UTC ISO timestamp |
| `action` | `S1_LONG`, `S2_LONG`, `S3_LONG`, `S4_SHORT`, `S*_CLOSE`, etc. |
| `symbol` | e.g. `BTCUSDT` |
| `side` | `LONG` or `SHORT` |
| `qty` | Position size |
| `entry` | Entry price |
| `sl` / `tp` | Stop-loss / take-profit price |
| `leverage` / `margin` | Risk sizing |
| `strategy` | Strategy tag |
| `snap_rsi`, `snap_adx`, … | Indicator snapshot at entry |
| `snap_rsi_peak`, `snap_spike_body_pct`, `snap_rsi_div` | S4-specific snapshot fields |
| `snap_s5_ob_low`, `snap_s5_ob_high`, `snap_s5_tp` | S5-specific: OB zone and structural TP at entry |
| `snap_sr_clearance_pct` | S/R clearance % at entry (S2/S3/S4/S5) |
| `result` / `pnl_pct` / `exit_reason` | On close rows: WIN/LOSS, P/L %, and exit type (SL/TP/TRAIL_STOP/PARTIAL_TP) |

---

## Requirements

- Python 3.10+
- Bitget account with Futures enabled and API key created (Register here -> https://www.bitgetapps.com/referral/register?clacCode=PTQGU9EF&from=%2Fevents%2Freferral-all-program&source=events&utmSource=PremierInviter)
- `.env` file with valid API credentials
- (Optional) Anthropic API key for Claude filter and optimizer
