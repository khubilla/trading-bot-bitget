"""Unit tests for ig_stream.py (no real Lightstreamer connection)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import ig_stream


def test_get_mark_price_returns_zero_when_empty():
    ig_stream._mark_cache.clear()
    assert ig_stream.get_mark_price("EPIC1") == 0.0


def test_get_mark_price_returns_cached_value():
    ig_stream._mark_cache["EPIC1"] = 1234.5
    assert ig_stream.get_mark_price("EPIC1") == 1234.5
    ig_stream._mark_cache.clear()


def test_is_connected_false_initially():
    ig_stream._connected = False
    assert ig_stream.is_connected() is False


def test_needs_reauth_false_initially():
    ig_stream._needs_reauth = False
    assert ig_stream.needs_reauth() is False
