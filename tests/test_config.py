import importlib

import pytest

from xauof.config import load_config, secret

SUBPACKAGES = ["data", "indicators", "bridge", "strategy", "backtest", "live", "news", "web"]


def test_config_loads():
    cfg = load_config()
    assert cfg["symbols"]["broker"] == "XAUUSD"
    assert 0 < cfg["profile"]["value_area_pct"] < 1
    # les garde-fous doivent rester sous les limites FTMO 2-Step (5 % / 10 %)
    assert cfg["risk"]["max_daily_loss_pct"] < 5
    assert cfg["risk"]["max_total_loss_pct"] < 10


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackages_import(name):
    importlib.import_module(f"xauof.{name}")


def test_missing_secret_raises(monkeypatch):
    monkeypatch.delenv("XAUOF_TEST_ABSENT", raising=False)
    with pytest.raises(RuntimeError, match="XAUOF_TEST_ABSENT"):
        secret("XAUOF_TEST_ABSENT")
