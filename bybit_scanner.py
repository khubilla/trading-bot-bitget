"""
bybit_scanner.py — Pair Scanner + Volume-Weighted Market Sentiment (Bybit)

Endpoint: GET /v5/market/tickers?category=linear

Key response fields per ticker:
  symbol         — e.g. "BTCUSDT"
  turnover24h    — 24h volume in USDT (Bybit's quote-volume equivalent)
  price24hPcnt   — 24h price change as decimal fraction (e.g. "0.0235" = +2.35%)
  lastPrice      — last price

Returns the same (qualified_pairs, SentimentResult) tuple as scanner.py so
bybit_bot.py can be a structural clone of bot.py.
"""

import math
import logging
from dataclasses import dataclass

import bybit_client as bc
from config_bybit import (
    MIN_VOLUME_USDT, MAX_PRICE_USDT, CATEGORY, SENTIMENT_THRESHOLD,
    LIQUIDITY_CHECK_ENABLED, MIN_OB_DEPTH_USDT,
)

logger = logging.getLogger(__name__)


@dataclass
class SentimentResult:
    direction:      str
    bullish_weight: float
    green_count:    int
    red_count:      int
    total_pairs:    int
    green_volume:   float
    red_volume:     float
    btc_change:     float = 0.0   # BTC 24h percent change at scan time (regime context)


def _filter_by_liquidity(pairs: list[str], depth_map: dict[str, float]) -> list[str]:
    liquid, excluded = [], []
    for sym in pairs:
        if depth_map.get(sym, 0.0) >= MIN_OB_DEPTH_USDT:
            liquid.append(sym)
        else:
            excluded.append(sym)
    if excluded:
        logger.info(
            f"[Bybit] Liquidity filter: removed {len(excluded)} illiquid pair(s): "
            + ", ".join(f"{s}(${depth_map.get(s, 0.0):.0f})" for s in excluded)
        )
    return liquid


def get_qualified_pairs_and_sentiment() -> tuple[list[str], SentimentResult]:
    """Single API call → qualified pairs + volume-weighted sentiment."""
    try:
        data = bc.get_public("/v5/market/tickers", params={"category": CATEGORY})
    except Exception as e:
        logger.error(f"[Bybit] Scanner: ticker fetch failed: {e}")
        return [], SentimentResult("NEUTRAL", 0.5, 0, 0, 0, 0.0, 0.0)

    tickers = (data.get("result") or {}).get("list") or []

    qualified:    list[str] = []
    green_volume = 0.0
    red_volume   = 0.0
    green_count  = 0
    red_count    = 0
    btc_change   = 0.0
    depth_map:    dict[str, float] = {}

    for t in tickers:
        symbol = t.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue

        try:
            vol_usdt     = float(t.get("turnover24h") or 0)
            last_pr      = float(t.get("lastPrice")   or 0)
            pcnt         = float(t.get("price24hPcnt") or 0)   # decimal fraction
            price_change = pcnt * 100
        except (ValueError, TypeError):
            continue

        if vol_usdt != 0 and vol_usdt < MIN_VOLUME_USDT:
            continue
        if MAX_PRICE_USDT != 0 and last_pr > MAX_PRICE_USDT:
            continue

        qualified.append(symbol)

        # Liquidity probe: top-of-book USDT depth
        try:
            bid_d = float(t.get("bid1Size") or 0) * float(t.get("bid1Price") or 0)
            ask_d = float(t.get("ask1Size") or 0) * float(t.get("ask1Price") or 0)
            depth_map[symbol] = bid_d + ask_d
        except (ValueError, TypeError):
            depth_map[symbol] = 0.0

        if symbol == "BTCUSDT":
            btc_change = price_change

        magnitude = abs(price_change)
        if magnitude < 0.15:
            weight = 0
        else:
            weight = math.sqrt(vol_usdt) * magnitude

        if price_change > 0:
            green_volume += weight
            green_count  += 1
        else:
            red_volume   += weight
            red_count    += 1

    qualified.sort()

    total_volume = green_volume + red_volume
    bullish_w    = (green_volume / total_volume) if total_volume > 0 else 0.5

    if btc_change < -3.0:
        direction = "BEARISH"
    elif bullish_w >= SENTIMENT_THRESHOLD:
        direction = "BULLISH"
    elif bullish_w <= (1 - SENTIMENT_THRESHOLD):
        direction = "BEARISH"
    else:
        direction = "NEUTRAL"

    sentiment = SentimentResult(
        direction      = direction,
        bullish_weight = round(bullish_w, 4),
        green_count    = green_count,
        red_count      = red_count,
        total_pairs    = len(qualified),
        green_volume   = round(green_volume, 0),
        red_volume     = round(red_volume, 0),
        btc_change     = round(btc_change, 2),
    )

    logger.info(
        f"[Bybit] Scanner: {len(qualified)} pairs | "
        f"Sentiment: {direction} ({bullish_w*100:.1f}% green by vol×magnitude) | "
        f"🟢 {green_count}  🔴 {red_count} | BTC={btc_change:+.1f}%"
    )
    if LIQUIDITY_CHECK_ENABLED:
        qualified = _filter_by_liquidity(qualified, depth_map)
    return qualified, sentiment


def get_qualified_pairs() -> list[str]:
    """Backtest shim — matches scanner.get_qualified_pairs."""
    pairs, _ = get_qualified_pairs_and_sentiment()
    return pairs
