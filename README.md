# Multi-Exchange Algorithmic Trading Bot

Automated trading bot supporting **Bitget USDT-margined crypto futures** and **IG CFD (Wall Street Cash / US30)**. Runs multiple independent strategies with a unified live dashboard. Supports both **live trading** and **paper trading** modes.

---

## ⚠️ Risk Disclaimer

**Trading involves substantial risk of loss.** This software is provided for educational and informational purposes only and does NOT constitute financial advice.

- Past performance does not guarantee future results
- You may lose some or all of your invested capital
- The creator accepts **no liability** for any financial losses or damages arising from use of this software
- Use entirely at your own risk
- The creator is **not responsible** for any modified, redistributed, or tampered versions — only the original source is covered by this disclaimer
- Always verify you are running the original source from the [official repository](https://github.com/khubilla/trading-bot-bitget)

On first run, the bot will display this disclaimer and require you to type `I AGREE` before starting. This acceptance is stored locally and only shown once per installation.

---

## Exchanges

| Exchange | Instrument | Strategies |
|----------|-----------|-----------|
| Bitget | USDT-margined perpetual futures | S1 · S2 · S3 · S4 · S5 |
| IG | Wall Street Cash (US30 / Dow Jones) | S5 only |

---

## Strategies

### Strategy 1 — MTF RSI Breakout *(Bitget only)*

Multi-timeframe breakout with trend and momentum filters.

| Timeframe | Filter |
|-----------|--------|
| 1D | ADX > 25 (trending, not sideways) |
| 1H | Current high > previous high (bull) / low < prev low (bear) |
| 3m | RSI > 70 (long) or < 30 (short) throughout consolidation |
| 3m | Candle closes above/below box + 0.5% buffer |

**Risk:** 30x leverage · 25% of total portfolio · SL at box edge · TP at +3.3%

---

### Strategy 2 — Daily Momentum Coil Breakout *(Bitget only)*

Pure daily-chart strategy targeting post-squeeze breakouts.

1. Big momentum candle (≥20% body) within last 30 daily candles
2. Daily RSI > 70 throughout consolidation
3. 1–5 tight daily candles coiling (max 15% range)
4. Current daily candle breaks above consolidation high (1% buffer)

**Entry window:** price must be within 1–4% above the breakout trigger — entry is skipped if already missed.

**Scale-in:** opens at 2.5% margin initially; adds remaining 2.5% after 1 hour if price is still within the entry window.

**Risk:** 10x leverage · 5% of total portfolio · SL at box low · 50% partial close at +10% · 10% trailing stop on remainder

---

### Strategy 3 — 15m Swing Pullback *(Bitget only)*

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

### Strategy 4 — Post-Pump RSI Divergence Short *(Bitget only)*

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

### Strategy 5 — SMC Order Block Pullback *(Bitget + IG)*

Multi-timeframe Smart Money Concepts strategy. Long or short depending on market direction.

**Structure (top-down):**
| Timeframe | Check |
|-----------|-------|
| 1D | EMA10 > EMA20 > EMA50 (bullish bias) or reverse (bearish bias) |
| 1H | Break of Structure — close above most recent 1H swing high pivot (LONG) or below swing low pivot (SHORT) |
| 15m | Order Block — last opposing candle before a ≥1% impulse of 2+ candles; OB range must be ≥0.5% |
| 15m | Pullback touches OB zone — limit order placed immediately at `ob_high` (LONG) or `ob_low` (SHORT) |

**Entry:** GTC limit order placed at the OB boundary (`ob_high` for LONG, `ob_low` for SHORT) the moment the pullback touches the OB zone. The limit fills when price dips through the boundary and returns — providing structural confirmation at a materially better price than a post-ChoCH market order. The order is cancelled if the OB is invalidated (price closes below `ob_low` for LONG) or after 4 hours.

**Exits (standard SMC):** SL trails to the previous completed 15m candle's low (LONG) or high (SHORT) each scan cycle (`S5_USE_CANDLE_STOPS`). 50% partial close at 1:1 R:R → SL moves to breakeven. Remaining 50% targets the nearest structural swing high/low on 15m. Fallback 5% trailing stop if no structural target is found.

**Risk (Bitget):** 10x leverage · 5% of total portfolio · SL 0.3% beyond OB outer wick · minimum 2:1 R:R required

**Risk (IG):** 0.04 contracts (Wall Street Cash, min 0.02) · partial close 0.02 at 1:1 R:R · SL trails to prev 15m candle · $1/point per contract

**Optional filters (`config_s5.py`):** `S5_SMC_FVG_FILTER` — require an unfilled Fair Value Gap above/below the OB for added confluence (off by default).

**Sentiment gate:** LONG only when BULLISH or NEUTRAL · SHORT only when BEARISH.

---

All Bitget strategies share a **market sentiment gate** — volume-weighted bull/bear ratio across all scanned pairs filters the allowed trade direction.

Trade sizes are always calculated as a percentage of **total portfolio equity** (available balance + locked margin + unrealized P/L), not just the free balance.

---

## Bitget Setup

**1. Create a Bitget account**

Register with the referral link to get a fee discount:
👉 **[Sign up for Bitget](https://www.bitgetapps.com/referral/register?clacCode=PTQGU9EF&from=%2Fevents%2Freferral-all-program&source=events&utmSource=PremierInviter)**

Then in your account: enable **Futures trading** and create an **API key** with Futures read/trade permissions (no withdrawal permission needed).

**2. Install dependencies**
```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

**3. Configure credentials**

Add to your `.env` file:
```
BITGET_API_KEY=your_api_key
BITGET_API_SECRET=your_api_secret
BITGET_API_PASSPHRASE=your_passphrase

# Optional — protects the dashboard when deployed on a public server
# Generate with: openssl rand -hex 32
DASHBOARD_API_KEY=
```

**4. Tune strategy parameters (optional)**

| File | Purpose |
|------|---------|
| `config_s1.py` | Strategy 1 — timeframes, RSI, ADX, risk params |
| `config_s2.py` | Strategy 2 — big candle detection, coil, trailing stop params |
| `config_s3.py` | Strategy 3 — EMA alignment, Stochastics, MACD, risk params |
| `config_s4.py` | Strategy 4 — spike detection, RSI divergence, entry/exit params |
| `config_s5.py` | Strategy 5 — SMC OB lookback, OB invalidation buffer, R:R minimum, candle stops, risk params |

Each config has an `S*_ENABLED = True/False` switch to disable a strategy without touching any other code.

---

## IG Setup

**1. Create an IG account**

Register with the referral link:
👉 **[Sign up for IG](https://refer.ig.com/jonkevinh-2)**

Open a **CFD account** (not spread betting). A **demo account** is available for paper testing — recommended before going live.

> Wall Street Cash (US30) minimum contract size is **0.02**. The bot opens **0.04** contracts and partially closes 0.02 at 1:1 R:R, leaving 0.02 to trail.

**2. Get your API key**

IG API keys are separate from your account login — they must be generated from IG Labs:

1. Go to **[labs.ig.com](https://labs.ig.com)** and log in with your IG account
2. Click **My Applications** → **Create application**
3. Copy the generated **API Key**

**3. Configure credentials**

Add to your `.env` file:
```
IG_API_KEY=your_api_key_from_labs_ig_com
IG_USERNAME=your_ig_username
IG_PASSWORD=your_ig_password
IG_ACC_TYPE=LIVE   # or DEMO
IG_ACCOUNT_ID=     # optional — leave blank to auto-select
```

> `IG_USERNAME` is your IG account username (not email). Find it in the IG platform under My Account.

**4. Tune IG parameters (optional)**

| File | Purpose |
|------|---------|
| `config_ig.py` | Session hours (ET), contract sizing, poll interval, file paths |
| `config_ig_s5.py` | S5 strategy parameters — US30-tuned overrides (OB lookback, OB invalidation buffer, R:R minimum, candle stops) |

---

## Running

### Bitget — Live trading
```bash
python bot.py
```

### Bitget — Paper trading *(simulated, no real orders)*
```bash
python bot.py --paper
```

### IG — Live trading *(sessions: Mon–Fri 09:30–12:30 ET only)*
```bash
python ig_bot.py
```

The IG bot automatically skips weekends, waits for the session window, and force-closes any open position at 12:30 ET.

### IG — Paper trading
```bash
python ig_bot.py --paper
```

### Dashboard *(both bots, unified)*
```bash
python dashboard.py          # Bitget live  → http://localhost:8080
python dashboard.py --paper  # Bitget paper → http://localhost:8081
```

The dashboard has a **Bitget / IG tab switcher** at the top. The IG tab shows the current session status (active/closed), open position, and trade history. Both bots can run simultaneously — the dashboard reads from their respective state and log files.

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

> **Security:** If the dashboard is publicly accessible, set `DASHBOARD_API_KEY` in `.env` to protect it. The dashboard will prompt for the key on first load and store it in the browser.
> ```
> DASHBOARD_API_KEY=<output of: openssl rand -hex 32>
> ```

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

The unified dashboard has a **Bitget** tab and an **IG** tab.

**Bitget tab:**
- Header stats: Balance, Open P/L, Total Value, Win Rate, Total P/L, Scanned Pairs
- Active trades: entry price, SL, TP, current P/L %, strategy badge, margin used
- Trade history: closed trades with PnL, result, and strategy tag
- Pair scanner tabs: S1 · S2 · S3 · S4 · S5 — each showing signals and setup status per pair; S5 shows OB zone, entry trigger, SL, and structural TP lines on the 15m chart
- Candlestick chart with RSI and MACD subcharts, entry/SL signal lines
- Entry chart modal: click any trade to see the exact candle snapshot captured at entry, partial TP, and close events

**IG tab:**
- Session badge: **In Session** (green, 09:30–12:30 ET weekdays) or **Closed** (red)
- Bot status badge: Running / Stopped
- Current open position: side, entry, SL, TP1, final TP, contracts, OB zone, partial-close status
- Trade history: all closed trades with P/L and exit reason
- Trade lifecycle chart: click any closed trade to see a combined candle chart across all lifecycle events (entry → partial → close)

---

## Claude AI Integration

### Trade Approval Filter (`claude_filter.py`) *(Bitget only)*

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
CLAUDE_FILTER_MODEL     = "claude-3-haiku-20240307"
CLAUDE_FILTER_HISTORY_N = 30   # last N trades as context
```

Add to `.env`:
```
ANTHROPIC_API_KEY=sk-ant-...
```

> The filter is most useful after 20–30+ closed trades per strategy. With fewer trades Claude has no pattern to learn from and will approve everything.

**Cost:** ~$0.001 per signal at Haiku pricing (~$0.10–0.15/month).

---

### Strategy Optimizer (`optimize.py`) *(Bitget only)*

A one-off analysis script that reads all closed trades, groups them by strategy, and asks Claude Sonnet to identify win/loss patterns and suggest specific config parameter changes.

```bash
python optimize.py           # analyze trades.csv (live)
python optimize.py --paper   # analyze trades_paper.csv
python optimize.py --min 5   # lower minimum trades threshold (default 10)
```

**Sample output:**
```
Analyzing S2 — 34 trades | 21W / 13L
1. KEY PATTERNS
   Winning trades had RSI >= 75 in 18/21 cases; losses averaged RSI 71.2
   All 7 losses with NEUTRAL sentiment vs 18/21 wins during BULLISH

2. SUGGESTED CHANGES
   S2_RSI_LONG_THRESH: 70 → 75  (filters 8 losing trades, keeps 19 winners)
```

Claude suggests changes; you review and apply them manually. All five strategies (S1–S5) are covered once they have sufficient trade history.

**Cost:** ~$0.015 per run with Claude Sonnet 4.6.

---

## File Structure

```
├── bot.py              # Bitget main entry point (--paper flag for paper mode)
├── ig_bot.py           # IG main entry point — S5 on US30, session-gated
│
├── strategy.py         # S1–S5 signal logic (shared by Bitget + IG)
│
├── trader.py           # Bitget order execution (live)
├── paper_trader.py     # Simulated order execution (Bitget paper)
├── bitget_client.py    # Bitget REST API client
├── scanner.py          # Pair scanner + market sentiment (Bitget)
│
├── ig_client.py        # IG REST API client
├── snapshot.py         # Candle snapshot capture/restore at trade lifecycle events
├── optimize_ig.py      # Claude Sonnet IG strategy parameter optimizer
│
├── dashboard.py        # Live web dashboard (FastAPI) — Bitget + IG tab
├── dashboard.html      # Dashboard frontend
├── backtest.py         # Backtesting engine
├── state.py            # Shared in-memory + on-disk state (Bitget)
├── claude_filter.py    # Claude Haiku trade approval gate (S2/S3/S4)
├── optimize.py         # Claude Sonnet strategy parameter optimizer
│
├── config.py           # Bitget credentials + bot settings
├── config_ig.py        # IG credentials + session hours + sizing
├── config_ig_s5.py     # Strategy 5 IG-specific overrides (US30 tuning)
├── config_s1.py        # Strategy 1 parameters
├── config_s2.py        # Strategy 2 parameters
├── config_s3.py        # Strategy 3 parameters
├── config_s4.py        # Strategy 4 parameters
├── config_s5.py        # Strategy 5 parameters (shared: Bitget + IG)
│
├── .env                # Credentials for all exchanges (gitignored)
│
├── trades.csv          # Bitget live trade log
├── trades_paper.csv    # Bitget paper trade log
├── ig_trades.csv       # IG trade log (live + paper)
├── state.json          # Bitget live runtime state
├── state_paper.json    # Bitget paper runtime state
├── ig_state.json       # IG runtime state (current position + pending_order)
├── paper_state.json    # Bitget paper simulation state
└── bot.log / ig_bot.log
```

---

## Trade Log

### Bitget (`trades.csv` / `trades_paper.csv`)

| Column | Description |
|--------|-------------|
| `timestamp` | UTC ISO timestamp |
| `action` | `S1_LONG`, `S2_LONG`, `S*_CLOSE`, etc. |
| `symbol` | e.g. `BTCUSDT` |
| `side` | `LONG` or `SHORT` |
| `qty` | Position size |
| `entry` | Entry price |
| `sl` / `tp` | Stop-loss / take-profit price |
| `leverage` / `margin` | Risk sizing |
| `snap_rsi`, `snap_adx`, … | Indicator snapshot at entry |
| `snap_s5_ob_low`, `snap_s5_ob_high`, `snap_s5_tp` | S5 OB zone and structural TP |
| `result` / `pnl_pct` / `exit_reason` | On close: WIN/LOSS, P/L %, exit type |

### IG (`ig_trades.csv`)

| Column | Description |
|--------|-------------|
| `timestamp` | UTC ISO timestamp |
| `action` | `S5_LONG`, `S5_SHORT`, `S5_PARTIAL`, `S5_CLOSE` |
| `side` | `LONG` or `SHORT` |
| `qty` | Contracts |
| `entry` / `sl` / `tp` | Prices |
| `snap_entry_trigger`, `snap_sl`, `snap_rr` | Snapshot at entry |
| `snap_s5_ob_low`, `snap_s5_ob_high`, `snap_s5_tp` | OB zone and structural TP |
| `result` / `pnl` / `exit_reason` | On close: WIN/LOSS, USD P/L, exit type |
| `session_date` | Trading session date (ET) |
| `mode` | `LIVE` or `PAPER` |

---

## Requirements

- Python 3.10+
- **Bitget account** with Futures enabled → [Register here](https://www.bitgetapps.com/referral/register?clacCode=PTQGU9EF&from=%2Fevents%2Freferral-all-program&source=events&utmSource=PremierInviter)
- **IG account** (CFD) with API key from [labs.ig.com](https://labs.ig.com) → [Register here](https://refer.ig.com/jonkevinh-2)
- `.env` file with valid credentials for the exchange(s) you intend to use
- (Optional) Anthropic API key for Claude filter and optimizer
