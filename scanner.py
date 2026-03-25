"""
scanner.py — Pair Scanner + Volume-Weighted Market Sentiment (Bitget)

Endpoint: GET /api/v2/mix/market/tickers?productType=USDT-FUTURES

Key response fields per ticker:
  symbol        — e.g. "BTCUSDT"
  quoteVolume   — 24h volume in USDT
  change24h     — 24h price change % (e.g. "2.35" means +2.35%)
  lastPr        — last price

Sentiment formula
─────────────────
  bullish_weight = Σ(volume of green coins) / Σ(all volumes)

  bullish_weight ≥ SENTIMENT_THRESHOLD       → BULLISH  (longs only)
  bullish_weight ≤ (1 - SENTIMENT_THRESHOLD) → BEARISH  (shorts only)
  otherwise                                  → NEUTRAL  (no trades)
"""

import logging
from dataclasses import dataclass
import bitget_client as bc
from config import MIN_VOLUME_USDT, PRODUCT_TYPE, SENTIMENT_THRESHOLD

logger = logging.getLogger(__name__)


@dataclass
class SentimentResult:
    direction:      str    # "BULLISH" | "BEARISH" | "NEUTRAL"
    bullish_weight: float  # 0.0 – 1.0
    green_count:    int
    red_count:      int
    total_pairs:    int
    green_volume:   float
    red_volume:     float


def get_qualified_pairs_and_sentiment() -> tuple[list[str], SentimentResult]:
    """
    Single API call → qualified pairs + volume-weighted sentiment.
    """
    try:
        data = bc.get_public(
            "/api/v2/mix/market/tickers",
            params={"productType": PRODUCT_TYPE}
        )
    except Exception as e:
        logger.error(f"Scanner: ticker fetch failed: {e}")
        return [], SentimentResult("NEUTRAL", 0.5, 0, 0, 0, 0.0, 0.0)

    tickers = data.get("data", [])

    qualified    : list[str] = []
    green_volume = 0.0
    red_volume   = 0.0
    green_count  = 0
    red_count    = 0

    for t in tickers:
        symbol = t.get("symbol", "")

        # Skip non-USDT and delivery contracts (contain underscore date suffixes)
        if not symbol.endswith("USDT"):
            continue
        if "_" in symbol:
            continue

        try:
            vol_usdt     = float(t.get("quoteVolume") or 0)
            change_str   = t.get("change24h") or "0"
            price_change = float(change_str)
            lastPr        = float(t.get("lastPr") or 0)
        except (ValueError, TypeError):
            continue

        # if lastPr > 200:
        #     continue

        if vol_usdt < MIN_VOLUME_USDT:
            continue

        qualified.append(symbol)

        if price_change > 0:
            green_volume += vol_usdt
            green_count  += 1
        else:
            red_volume += vol_usdt
            red_count  += 1

    qualified.sort()

    total_volume = green_volume + red_volume
    bullish_w    = (green_volume / total_volume) if total_volume > 0 else 0.5

    if bullish_w >= SENTIMENT_THRESHOLD:
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
        f"Scanner: {len(qualified)} pairs | "
        f"Sentiment: {direction} ({bullish_w*100:.1f}% green by vol) | "
        f"🟢 {green_count}  🔴 {red_count}"
    )
    return qualified, sentiment


# Backtest shim
def get_qualified_pairs() -> list[str]:
    pairs, _ = get_qualified_pairs_and_sentiment()
    return pairs
