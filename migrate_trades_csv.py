"""
DEPRECATED / UNUSED.

The regime-fields change was redesigned to write to a SEPARATE sidecar file
(see bot._regime_log_path → trades_regime.csv) instead of adding columns to
trades.csv. The trades.csv contract is therefore unchanged and no header
migration is needed. This file is retained only because it could not be
removed automatically; it is safe to delete.
"""
