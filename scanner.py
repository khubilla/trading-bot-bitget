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
from config import (
    MIN_VOLUME_USDT, MAX_PRICE_USDT, PRODUCT_TYPE, SENTIMENT_THRESHOLD,
    LIQUIDITY_CHECK_ENABLED, MIN_OB_DEPTH_USDT,
)
import math

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

def _filter_by_liquidity(pairs: list[str], depth_map: dict[str, float]) -> list[str]:
    """
    Remove pairs whose top-of-book USDT depth is below MIN_OB_DEPTH_USDT.
    depth_map: symbol → bidSz×bidPr + askSz×askPr (from ticker loop).
    Conservative: symbols absent from depth_map treated as depth=0 (excluded).
    """
    liquid   = []
    excluded = []
    for sym in pairs:
        if depth_map.get(sym, 0.0) >= MIN_OB_DEPTH_USDT:
            liquid.append(sym)
        else:
            excluded.append(sym)
    if excluded:
        logger.info(
            f"Liquidity filter: removed {len(excluded)} illiquid pair(s): "
            + ", ".join(f"{s}(${depth_map.get(s, 0.0):.0f})" for s in excluded)
        )
    return liquid


def get_qualified_pairs_and_sentiment() -> tuple[list[str], SentimentResult]:
    """
    Single API call → qualified pairs + volume-weighted sentiment.
    Weight = volume × magnitude of price change, so a coin up 30%
    contributes 30× more than a coin up 1% at the same volume.
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
    btc_change   = 0.0          # ← NEW: for BTC veto
    depth_map   : dict[str, float] = {}

    for t in tickers:
        symbol = t.get("symbol", "")

        if not symbol.endswith("USDT"):
            continue
        if "_" in symbol:
            continue

        try:
            vol_usdt     = float(t.get("quoteVolume") or 0)
            openUtc      = float(t.get("openUtc") or t.get("open24h") or 0)
            lastPr       = float(t.get("lastPr") or 0)
            price_change = ((lastPr - openUtc) / openUtc * 100) if openUtc > 0 else 0.0
        except (ValueError, TypeError):
            continue

        if vol_usdt < MIN_VOLUME_USDT:
            continue

        if lastPr > MAX_PRICE_USDT:
            continue

        qualified.append(symbol)

        # Liquidity probe: top-of-book USDT depth from ticker fields
        try:
            bid_d = float(t.get("bidSz") or 0) * float(t.get("bidPr") or 0)
            ask_d = float(t.get("askSz") or 0) * float(t.get("askPr") or 0)
            depth_map[symbol] = bid_d + ask_d
        except (ValueError, TypeError):
            depth_map[symbol] = 0.0

        # ── NEW: capture BTC change for veto ─────────────────────
        if symbol == "BTCUSDT":
            btc_change = price_change

        # weight = volume × magnitude of change ────────────
        # Floor at 0.5% so near-zero movers don't get zeroed out
        # but still contribute less than strong movers
        # magnitude = max(abs(price_change), 0.5)
        # weight    = math.log(vol_usdt) * magnitude

        # if price_change > 0:
        #     green_volume += weight      # ← was: vol_usdt
        #     green_count  += 1
        # else:
        #     red_volume   += weight      # ← was: vol_usdt
        #     red_count    += 1

        # 1. Calculate how 'heavy' this move is
        # We use abs() so both big drops and big pumps get high weight
        magnitude = abs(price_change) 

        # 2. Apply a small noise filter (optional)
        if magnitude < 0.15: 
            weight = 0
        else:
            weight = math.sqrt(vol_usdt) * magnitude

        # 3. Sort into buckets based on the actual SIGN of the change
        if price_change > 0:
            green_volume += weight
            green_count  += 1
        else:
            red_volume   += weight
            red_count    += 1

    qualified.sort()

    total_volume = green_volume + red_volume
    bullish_w    = (green_volume / total_volume) if total_volume > 0 else 0.5

    # ── NEW: BTC veto — force BEARISH if BTC drops > 3% ──────────
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
        f"Scanner: {len(qualified)} pairs | "
        f"Sentiment: {direction} ({bullish_w*100:.1f}% green by vol×magnitude) | "
        f"🟢 {green_count}  🔴 {red_count} | "
        f"BTC={btc_change:+.1f}%"
    )
    if LIQUIDITY_CHECK_ENABLED:
        qualified = _filter_by_liquidity(qualified, depth_map)
    return qualified, sentiment


# Backtest shim
def get_qualified_pairs() -> list[str]:
    pairs, _ = get_qualified_pairs_and_sentiment()
    return pairs
