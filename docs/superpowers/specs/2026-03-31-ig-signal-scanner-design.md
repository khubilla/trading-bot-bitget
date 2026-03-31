# IG Signal Scanner & Scan Log — Design Spec

**Date:** 2026-03-31
**Status:** Approved

---

## Overview

Add a per-instrument S5 signal checklist and a 20-entry scan log to the IG dashboard panel. The layout mirrors the Bitget pair scanner: instrument cards on top showing S5 check rows, position card and scan log side-by-side below.

The design is also forward-compatible with adding Gold as a second IG instrument.

---

## Layout (Option A — approved)

```
┌─────────────────────────────────────────────────────────┐
│  IG Bot header (P&L, trades, win rate, ET time, status) │
├─────────────────────────────────────────────────────────┤
│  Instrument Scanner                                     │
│  ┌────────────────────┐  ┌────────────────────┐        │
│  │ US30   PENDING     │  │ Gold   HOLD         │        │
│  │ Daily EMA  ✓       │  │ Daily EMA  —        │        │
│  │ 1H BOS     ✓       │  │ 1H BOS     —        │        │
│  │ 15m OB     ✓       │  │ 15m OB     —        │        │
│  │ Limit      Watching│  │ Limit      —        │        │
│  │ reason text · age  │  │ reason text · age   │        │
│  └────────────────────┘  └────────────────────┘        │
├──────────────────────────┬──────────────────────────────┤
│  Open Position           │  Scan Log (last 20)          │
│  (existing card)         │  10:24  US30  reason text…  │
│                          │  10:21  US30  reason text…  │
│                          │  10:18  Gold  reason text…  │
├──────────────────────────┴──────────────────────────────┤
│  Trade History (existing)                               │
└─────────────────────────────────────────────────────────┘
```

---

## Data Changes

### ig_state.json — two new top-level fields

**`scan_signals`** — dict keyed by instrument display name (e.g. `"US30"`, `"Gold"`). Written after every `evaluate_s5()` call. Overwritten each tick (not appended).

```json
{
  "scan_signals": {
    "US30": {
      "signal":        "PENDING_SHORT",
      "reason":        "Daily EMA bearish ✅ | 1H BOS confirmed ✅ | OB 44210–44380 ✅ · entry @ 44380",
      "ema_ok":        true,
      "bos_ok":        true,
      "ob_ok":         true,
      "ob_low":        44210.0,
      "ob_high":       44380.0,
      "entry_trigger": 44380.0,
      "sl":            44450.0,
      "tp":            44210.0,
      "updated_at":    "2026-03-31T10:24:00+00:00"
    }
  }
}
```

Boolean derivation rules (applied in ig_bot.py, not the dashboard):
- `ema_ok` — reason does NOT start with `"Daily EMA flat"` and NOT `"S5 disabled"`
- `bos_ok` — `"BOS ✅"` in reason OR signal is `PENDING_LONG` / `PENDING_SHORT`
- `ob_ok` — signal is `PENDING_LONG` / `PENDING_SHORT` OR `"OB ✅"` in reason

**`scan_log`** — list of last 20 entries, newest first. One entry appended per tick. Capped at 20 (drop oldest when over limit).

```json
{
  "scan_log": [
    { "ts": "10:24", "instrument": "US30", "message": "Daily EMA bearish ✅ | 1H BOS not confirmed (need close 45731 < swing low 44210)" },
    { "ts": "10:21", "instrument": "US30", "message": "PENDING_SHORT: OB zone 44210–44380 · entry @ 44380 · SL 44450 · TP 44210" }
  ]
}
```

`ts` format: `"HH:MM"` in ET timezone (matches the existing ET time display on the dashboard).

---

## ig_bot.py Changes

### New instance variables (initialised in `__init__`)

```python
self._scan_signals: dict = {}   # keyed by instrument display name
self._scan_log: list = []       # last 20 entries, newest first
```

### After every `evaluate_s5()` call

1. Derive `ema_ok`, `bos_ok`, `ob_ok` from signal + reason (rules above).
2. Write / overwrite `self._scan_signals[INSTRUMENT_DISPLAY_NAME]`.
3. Prepend a new entry to `self._scan_log`; truncate to 20 entries.
4. `_save_state()` is called at end of tick as usual — it will include both fields.

`INSTRUMENT_DISPLAY_NAME` is a module-level constant (e.g. `"US30"`). When Gold is added, its bot file defines its own constant.

### `_save_state()` — updated payload

```python
json.dump({
    "position":      self.position,
    "pending_order": self.pending_order,
    "scan_signals":  self._scan_signals,
    "scan_log":      self._scan_log,
}, f, indent=2)
```

### Startup: restore from existing state file

On `__init__` (or `_sync_live_position`), read `scan_signals` and `scan_log` from ig_state.json if it exists, so the dashboard shows data immediately after a bot restart.

---

## dashboard.py Changes

`/api/ig/state` already reads ig_state.json. Pass the new fields through with safe defaults:

```python
scan_signals = state.get("scan_signals", {})
scan_log     = state.get("scan_log", [])
```

Include both in the `JSONResponse`. No other changes to dashboard.py.

---

## dashboard.html Changes

### New "Instrument Scanner" section

Inserted above the existing position + trade history panels in `#ig-panel`.

Rendered by a new JS function `renderIGScanner(scan_signals)`:
- Iterates over entries in `scan_signals`
- Renders one card per instrument (same card structure as Bitget pair scanner)
- Cards use `data-sig` attribute for signal badge colouring (PENDING=blue, LONG=green, SHORT=rose, HOLD=muted)
- Check rows: Daily EMA / 1H BOS / 15m OB / Limit Order — tick (✓, green) or dash (—, muted) based on `ema_ok`, `bos_ok`, `ob_ok`, and `signal`
- Reason text truncated to 80 chars + age from `updated_at`

### Scan log panel

Replaces the right column of the existing 2-column grid (position stays left, scan log goes right):

```
[ Open Position ]   [ Scan Log (20) ]
```

Rendered by `renderIGScanLog(scan_log)`:
- Each row: `HH:MM  INSTRUMENT  message text`
- Instrument column coloured by name (US30=blue, Gold=amber)
- `overflow-y: auto; max-height: 260px`

### Instrument name mapping (JS constant)

```js
const IG_EPIC_LABELS = {
  "IX.D.DOW.IFD.IP": "US30",
  // Gold epic added here when ready
};
```

`scan_signals` is already keyed by display name (set in ig_bot.py), so this mapping is only needed if the dashboard ever needs to map back from epic to label for other purposes. The scanner cards use the key directly.

### Polling

`/api/ig/state` is already polled every 5 seconds. No polling changes needed.

---

## Backward Compatibility

- ig_state.json files without `scan_signals` / `scan_log` are handled by `.get(..., {})` / `.get(..., [])` in both ig_bot.py startup and dashboard.py.
- No existing fields are removed or renamed.

---

## Out of Scope

- Gold instrument wiring (separate task when Gold trading is set up)
- Trade chart / snapshot for IG trades
- Per-instrument session time tracking

---

## Files Changed

| File | Change |
|------|--------|
| `ig_bot.py` | Add `_scan_signals`, `_scan_log`; populate after `evaluate_s5()`; include in `_save_state()`; restore on startup |
| `dashboard.py` | Pass `scan_signals` + `scan_log` through `/api/ig/state` response |
| `dashboard.html` | Add `renderIGScanner()`, `renderIGScanLog()`; restructure IG panel grid |
| `docs/DEPENDENCIES.md` | Update §4.4 ig_state.json — add `scan_signals` and `scan_log` fields |
