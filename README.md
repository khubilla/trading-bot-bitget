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

## Dashboard

The live dashboard shows:

- **Header stats**: Balance, Open P/L, Total Value (balance + margin + unrealised P/L), Win Rate, Total P/L, Scanned Pairs
- **Active trades**: entry price, SL, TP, current P/L %, strategy badge, margin used
- **Trade history**: closed trades with PnL, result, and strategy tag
- **Pair scanner tabs**: S1 · S2 · S3 · S4 — each showing signals and setup status per pair
- **Candlestick chart** with RSI and MACD subcharts, entry/SL signal lines, synced scroll across panes

---

## File Structure

```
├── bot.py              # Main entry point (--paper flag for paper mode)
├── strategy.py         # S1 + S2 + S3 + S4 signal logic
├── trader.py           # Bitget order execution (live)
├── paper_trader.py     # Simulated order execution (paper)
├── scanner.py          # Pair scanner + market sentiment
├── dashboard.py        # Live web dashboard (FastAPI, --paper flag)
├── dashboard.html      # Dashboard frontend (served by dashboard.py)
├── backtest.py         # Backtesting engine
├── bitget_client.py    # Bitget REST API client
├── state.py            # Shared in-memory + on-disk state
│
├── config.py           # Credentials + paths (reads from .env / env vars)
├── config_s1.py        # Strategy 1 parameters
├── config_s2.py        # Strategy 2 parameters
├── config_s3.py        # Strategy 3 parameters
├── config_s4.py        # Strategy 4 parameters
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
| `pnl` / `result` | On close rows: realised PnL and WIN/LOSS |

---

## Requirements

- Python 3.10+
- Bitget account with Futures enabled and API key created
- `.env` file with valid API credentials
