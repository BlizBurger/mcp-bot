import importlib

import pytest

from xauof.config import load_config, secret

SUBPACKAGES = ["data", "indicators", "bridge", "strategy", "backtest", "live", "news", "web"]


def test_config_loads():
    cfg = load_config()
    assert cfg["symbols"]["broker"] == "XAUUSD"
    assert 0 < cfg["profile"]["value_area_pct"] < 1
    # les garde-fous internes doivent rester sous les limites FTMO 2-Step
    assert cfg["risk"]["daily_stop_usd"] < cfg["ftmo"]["daily_loss_limit_usd"]
    assert cfg["risk"]["total_floor_margin_usd"] > 0
    assert cfg["risk"]["risk_per_trade_pct"] * cfg["account"]["initial_capital"] / 100 == 100


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackages_import(name):
    importlib.import_module(f"xauof.{name}")


def test_missing_secret_raises(monkeypatch):
    monkeypatch.delenv("XAUOF_TEST_ABSENT", raising=False)
    with pytest.raises(RuntimeError, match="XAUOF_TEST_ABSENT"):
        secret("XAUOF_TEST_ABSENT")
