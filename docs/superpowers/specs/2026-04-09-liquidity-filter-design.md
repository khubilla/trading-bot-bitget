# Liquidity Filter — Design Spec

**Date:** 2026-04-09  
**Status:** Approved  
**Scope:** Bitget bot only (`bot.py` + `scanner.py`); IG bot unaffected

---

## Background

A trade on ARIAUSDT was liquidated at 100% loss because the pair was illiquid at the time of entry. The existing `MIN_VOLUME_USDT = 5_000_000` filter in `scanner.py` checks 24h quote volume but does not verify that the order book has sufficient depth near the current price. A pair can pass the volume filter yet have a dangerously thin order book.

---

## Goal

Exclude illiquid pairs from `qualified_pairs` at scan time so they never reach any strategy evaluation. Applies to all strategies (S1–S6) transparently.

---

## Design

### Approach: Last-funnel filter in `scanner.py` using existing ticker data

The `/api/v2/mix/market/tickers` call already returns `bidPr`, `askPr`, `bidSz`, `askSz` per pair. We use these to compute top-of-book USDT depth:

```
ob_depth = bidSz × bidPr + askSz × askPr
```

This is computed during the existing ticker loop at zero extra API cost. Pairs below `MIN_OB_DEPTH_USDT` are excluded as the final funnel step, after volume and price filters.

### Why top-of-book instead of multi-level depth

A batch order book API does not exist on Bitget — multi-level depth would require N per-symbol API calls per scan cycle. For a pair illiquid enough to cause a liquidation, the top-of-book `bidSz`/`askSz` will be visibly thin and is a reliable signal. Top-of-book from the existing bulk ticker call adds zero latency and zero extra API calls.

---

## Changes

### `config.py`

Add two new parameters near `MIN_VOLUME_USDT`:

```python
LIQUIDITY_CHECK_ENABLED = True
MIN_OB_DEPTH_USDT       = 50_000   # bidSz×bidPr + askSz×askPr threshold
```

- `LIQUIDITY_CHECK_ENABLED`: master on/off switch; set to `False` to disable without removing code.
- `MIN_OB_DEPTH_USDT`: fixed USDT floor. Tunable via config once we observe which pairs get filtered. Start at $50,000.

### `scanner.py`

**During the existing ticker loop** (after a pair passes `MIN_VOLUME_USDT` and `MAX_PRICE_USDT` checks):

```python
ob_depth = float(t.get("bidSz") or 0) * float(t.get("bidPr") or 0) \
          + float(t.get("askSz") or 0) * float(t.get("askPr") or 0)
depth_map[symbol] = ob_depth
```

**New private helper** called at the end of `get_qualified_pairs_and_sentiment()`:

```python
def _filter_by_liquidity(pairs: list[str], depth_map: dict[str, float]) -> list[str]:
```

- Iterates `pairs`, keeps those where `depth_map.get(sym, 0) >= MIN_OB_DEPTH_USDT`
- Logs one summary line: how many pairs were removed and which symbols
- Returns the filtered list

**In `get_qualified_pairs_and_sentiment()`**, after building `qualified`:

```python
if LIQUIDITY_CHECK_ENABLED:
    qualified = _filter_by_liquidity(qualified, depth_map)
```

---

## What does NOT change

- `bot.py` — no changes; receives shorter `qualified` list transparently
- `trader.py` — no changes
- `ig_bot.py` — no changes; uses `ig_client.py`, unrelated exchange
- All strategy files — no changes
- `state.json`, CSV columns, dashboard — no changes
- `paper_trader.py` — no changes; paper mode still uses the same `qualified` list (filtering applies in paper mode too, which is correct)

---

## Error handling

If `bidSz`, `bidPr`, `askSz`, or `askPr` are missing or non-numeric for a pair, `ob_depth` defaults to `0.0`, causing the pair to be excluded. This is conservative: if the exchange doesn't report top-of-book data, treat the pair as illiquid.

**Implementation guard:** `bidSz`/`askSz` are not currently read anywhere in this codebase. During first implementation, log the raw ticker fields for one sample pair and confirm these keys are present before relying on them. If they are absent from the `/api/v2/mix/market/tickers` response, the fallback is to call `/api/v2/mix/market/merge-depth` with `limit=1` per qualified pair (one extra API call per pair, but a much smaller N after the volume/price funnel).

---

## Tuning guidance

- Start with `MIN_OB_DEPTH_USDT = 50_000` and observe logs for which pairs get filtered
- If legitimate high-quality pairs are being filtered, lower the threshold
- If illiquid pairs are still slipping through, raise it
- Different pairs have different depth profiles; a fixed floor is intentionally simple and can be revisited

---

## Dependency check summary

- **Change type:** New last-funnel filter in `scanner.py`; new config params
- **Shared files touched:** None
- **Data contracts affected:** None (no state.json, CSV, or return value changes)
- **Both bots affected?** No — Bitget only
- **Safe to proceed?** Yes
