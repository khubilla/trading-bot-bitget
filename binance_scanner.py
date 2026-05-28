"""
binance_scanner.py — Pair Scanner + Volume-Weighted Market Sentiment (Binance)

Endpoint: GET /fapi/v1/ticker/24hr (no auth)

Key response fields per ticker:
  symbol              — e.g. "BTCUSDT"
  quoteVolume         — 24h volume in USDT (quote asset)
  priceChangePercent  — 24h price change as PERCENT string (e.g. "2.35" = +2.35%)
                        NOTE: this is ALREADY a percent, NOT a decimal fraction
                        like Bybit's price24hPcnt.
  lastPrice           — last price

Returns the same (qualified_pairs, SentimentResult) tuple as scanner.py so
binance_bot.py can be a structural clone of bot.py.
"""

import math
import logging
from dataclasses import dataclass

import binance_client as bc
from config_binance import (
    MIN_VOLUME_USDT, MAX_PRICE_USDT, SENTIMENT_THRESHOLD,
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


def _filter_by_liquidity(pairs: list[str], depth_map: dict[str, float]) -> list[str]:
    liquid, excluded = [], []
    for sym in pairs:
        if depth_map.get(sym, 0.0) >= MIN_OB_DEPTH_USDT:
            liquid.append(sym)
        else:
            excluded.append(sym)
    if excluded:
        logger.info(
            f"[Binance] Liquidity filter: removed {len(excluded)} illiquid pair(s): "
            + ", ".join(f"{s}(${depth_map.get(s, 0.0):.0f})" for s in excluded)
        )
    return liquid


def _fetch_book_depth(symbols: list[str]) -> dict[str, float]:
    """
    Top-of-book bid+ask USDT depth via /fapi/v1/ticker/bookTicker.
    Single call returns ALL symbols when no symbol param is provided.
    """
    try:
        rows = bc.get_public("/fapi/v1/ticker/bookTicker")
    except Exception as e:
        logger.warning(f"[Binance] bookTicker fetch failed: {e}")
        return {}
    if not isinstance(rows, list):
        return {}
    by_symbol = {r.get("symbol"): r for r in rows}
    out: dict[str, float] = {}
    for s in symbols:
        r = by_symbol.get(s)
        if not r:
            out[s] = 0.0
            continue
        try:
            bid_d = float(r.get("bidQty") or 0) * float(r.get("bidPrice") or 0)
            ask_d = float(r.get("askQty") or 0) * float(r.get("askPrice") or 0)
            out[s] = bid_d + ask_d
        except (ValueError, TypeError):
            out[s] = 0.0
    return out


def get_qualified_pairs_and_sentiment() -> tuple[list[str], SentimentResult]:
    """Single API call → qualified pairs + volume-weighted sentiment."""
    try:
        tickers = bc.get_public("/fapi/v1/ticker/24hr")
    except Exception as e:
        logger.error(f"[Binance] Scanner: ticker fetch failed: {e}")
        return [], SentimentResult("NEUTRAL", 0.5, 0, 0, 0, 0.0, 0.0)

    if not isinstance(tickers, list):
        return [], SentimentResult("NEUTRAL", 0.5, 0, 0, 0, 0.0, 0.0)

    qualified:    list[str] = []
    green_volume = 0.0
    red_volume   = 0.0
    green_count  = 0
    red_count    = 0
    btc_change   = 0.0

    for t in tickers:
        symbol = t.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue

        try:
            vol_usdt     = float(t.get("quoteVolume") or 0)
            last_pr      = float(t.get("lastPrice")   or 0)
            # Binance returns priceChangePercent as a percent string ("2.35" = +2.35%).
            # Bybit returned it as a decimal fraction (0.0235). NO multiplication here.
            price_change = float(t.get("priceChangePercent") or 0)
        except (ValueError, TypeError):
            continue

        if vol_usdt != 0 and vol_usdt < MIN_VOLUME_USDT:
            continue
        if MAX_PRICE_USDT != 0 and last_pr > MAX_PRICE_USDT:
            continue

        qualified.append(symbol)

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
    )

    logger.info(
        f"[Binance] Scanner: {len(qualified)} pairs | "
        f"Sentiment: {direction} ({bullish_w*100:.1f}% green by vol×magnitude) | "
        f"🟢 {green_count}  🔴 {red_count} | BTC={btc_change:+.1f}%"
    )
    if LIQUIDITY_CHECK_ENABLED:
        depth_map = _fetch_book_depth(qualified)
        qualified = _filter_by_liquidity(qualified, depth_map)
    return qualified, sentiment


def get_qualified_pairs() -> list[str]:
    """Backtest shim — matches scanner.get_qualified_pairs."""
    pairs, _ = get_qualified_pairs_and_sentiment()
    return pairs
