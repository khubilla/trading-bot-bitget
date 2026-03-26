# Bitget USDT-Futures MTF Bot

Automated crypto futures trading bot for Bitget USDT-margined perpetual futures. Runs two independent strategies simultaneously with a shared live dashboard.

---

## Strategies

### Strategy 1 — MTF RSI Breakout
Multi-timeframe breakout with trend and momentum filters.

| Timeframe | Filter |
|-----------|--------|
| 1D | ADX > 25 (trending, not sideways) |
| 1H | Current high > previous high (bull) / low < prev low (bear) |
| 3m | RSI > 70 (long) or < 30 (short) throughout consolidation |
| 3m | Candle closes above/below box + 0.1% buffer |

Risk: 30x leverage, 25% margin per trade, SL at box edge, TP at +3.3%.

### Strategy 2 — Daily Momentum Coil Breakout
Pure daily-chart strategy targeting post-squeeze breakouts.

1. Big momentum candle (≥20% body) within last 30 daily candles
2. Daily RSI > 70 throughout consolidation
3. 1–5 tight daily candles coiling (max 15% range)
4. Current daily candle breaks above consolidation high

Risk: 10x leverage, 25% margin per trade, trailing stop after +10%.

Both strategies share a **market sentiment gate** — volume-weighted bull/bear ratio across all pairs filters allowed trade direction.

---

## Setup

**1. Clone and install dependencies**
```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

**2. Create your config file**
```bash
cp config_template.py config.py
```
Edit `config.py` and fill in your Bitget API credentials:
```python
API_KEY        = "your_api_key"
API_SECRET     = "your_api_secret"
API_PASSPHRASE = "your_passphrase"
DEMO_MODE      = True   # set False for live trading
```

**3. Tune strategy parameters (optional)**

| File | Purpose |
|------|---------|
| `config_s1.py` | Strategy 1 — timeframes, RSI, ADX, risk params |
| `config_s2.py` | Strategy 2 — big candle detection, coil, risk params |

---

## Running

**Start the bot**
```bash
python bot.py
```

**Start the dashboard** (separate terminal)
```bash
python dashboard.py
```
Then open [http://localhost:8080](http://localhost:8080).

---

## File Structure

```
├── bot.py              # Main entry point
├── strategy.py         # S1 + S2 signal logic
├── trader.py           # Bitget order execution
├── scanner.py          # Pair scanner + market sentiment
├── dashboard.py        # Live web dashboard (FastAPI)
├── backtest.py         # Backtesting engine
├── bitget_client.py    # Bitget REST API client
├── state.py            # Shared in-memory state
│
├── config_template.py  # Copy to config.py and add your keys
├── config_s1.py        # Strategy 1 parameters
├── config_s2.py        # Strategy 2 parameters
│
├── trades.csv          # Trade log
└── bot.log             # Runtime log
```

> `config.py` is gitignored — never commit your API credentials.

---

## Requirements

- Python 3.10+
- Bitget account with Futures enabled and API key created
- Set `DEMO_MODE = True` to paper trade before going live
