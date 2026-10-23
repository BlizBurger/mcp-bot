"""Assistant manuel : niveaux, instantané de marché, compte (sans MT5 ni réseau)."""
import copy

import pandas as pd
import pytest

from synthetic import make_market
from xauof.assistant import levels as lv
from xauof.assistant import snapshot as sn
from xauof.assistant.update import fetch_session, previous_weekdays
from xauof.config import load_config
from xauof.data import store
from xauof.indicators.check import day_bounds
from xauof.news.calendar import Calendar
from xauof.strategy import run as R
from xauof.strategy.engine import Engine, SimExecutor
from xauof.strategy.instrument import load_instrument
from xauof.strategy.risk import Account

DAYS = ["2026-03-02", "2026-03-03", "2026-03-04"]


@pytest.fixture(scope="module")
def mkt(tmp_path_factory):
    cfg = copy.deepcopy(load_config())
    cfg["data"]["dir"] = str(tmp_path_factory.mktemp("as") / "data")
    cfg["news"]["missing_calendar"] = "ignore_backtest_only"
    make_market(cfg, DAYS, seed=2, kappa=0.03)
    return cfg, R.prepare(cfg, "2026-03-03", "2026-03-05")


def test_levels_match_engine_levels(mkt):
    cfg, data = mkt
    prev = pd.Timestamp("2026-03-02")
    a, b = day_bounds(prev)
    trades = store.load_range(store.gc_trades_dir(cfg), a, b)
    mine, _ = lv.compute_levels(cfg, trades, data["xau"], prev)
    ref, _ = R.levels_for(cfg, data, prev)
    assert mine == ref.as_dict()


def test_levels_roundtrip_and_lines(mkt, tmp_path):
    cfg, data = mkt
    ref, _ = R.levels_for(cfg, data, pd.Timestamp("2026-03-02"))
    lv.save_levels(cfg, "2026-03-03", ref.as_dict())
    back = lv.load_levels(cfg, "2026-03-03")
    assert back["poc_v"] == pytest.approx(ref.poc_v)
    prices = [p for _, p in lv.level_lines(back)]
    assert prices == sorted(prices, reverse=True)
    assert lv.load_levels(cfg, "2030-01-01") is None


def test_snapshot_zones_match_backtest_engine(mkt):
    cfg, data = mkt
    day = pd.Timestamp("2026-03-03")
    dd = R.day_data(cfg, data, day, None, "test")
    eng = Engine(cfg, "A", load_instrument(cfg), Account.from_config(cfg), Calendar.empty(), SimExecutor())
    eng.run_day(dd)
    now = sn.ny_time(day, "11:00", "America/New_York")
    snap = sn.snapshot(cfg, dd.xau, None, Calendar.empty(), now)
    key = lambda z: (z.kind, z.direction, round(z.low, 6), round(z.high, 6), z.active_from)  # noqa: E731
    engine_zones = {key(z) for z in eng.book.zones if z.active_from <= now}
    assert engine_zones and {key(z) for z in snap["zones"]} == engine_zones


def test_snapshot_ib_and_window(mkt):
    cfg, data = mkt
    day = pd.Timestamp("2026-03-03")
    xau = R.day_data(cfg, data, day, None, "").xau
    early = sn.snapshot(cfg, xau, None, Calendar.empty(), sn.ny_time(day, "08:50", "America/New_York"))
    assert early["ib"] and not early["ib"]["definitive"] and early["fenetre"].startswith("entrées autorisées")
    late = sn.snapshot(cfg, xau, None, Calendar.empty(), sn.ny_time(day, "13:20", "America/New_York"))
    assert late["ib"]["definitive"] and "plus d'entrées" in late["fenetre"]
    assert any("Niveaux de la veille absents" in a for a in late["alertes"])
    assert any("Calendrier news non couvert" in a for a in late["alertes"])


def test_account_status(mkt):
    cfg, _ = mkt
    a = sn.account_status(cfg, balance=9950, equity=9900, realized_today=-50)
    assert a["solde_debut_jour"] == 10000 and a["perte_jour"] == 100
    assert a["reste_arret_interne"] == 50 and a["reste_ftmo_jour"] == 400
    assert a["marge_plancher_ftmo"] == 900 and not a["stop_interne_atteint"]
    assert sn.account_status(cfg, 9900, 9850, -100)["stop_interne_atteint"]


def test_previous_weekdays_skip_weekend():
    assert [d.strftime("%a %d") for d in previous_weekdays(pd.Timestamp("2026-10-26"), 3)] == \
        ["Fri 23", "Thu 22", "Wed 21"]


def test_fetch_session_cost_guard(mkt):
    cfg, _ = mkt

    class Client:
        class metadata:
            @staticmethod
            def get_cost(**kw):
                return 5.0

    with pytest.raises(SystemExit, match="coût estimé"):
        fetch_session(cfg, Client(), pd.Timestamp("2026-10-23"))
