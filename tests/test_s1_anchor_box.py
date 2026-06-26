import importlib


def test_anchor_box_config_defaults():
    for mod_name in ("config_s1", "config_bybit_s1", "config_binance_s1"):
        mod = importlib.import_module(mod_name)
        assert mod.S1_ANCHOR_BOX is True
        assert mod.S1_BOX_MAX_AGE == 10
