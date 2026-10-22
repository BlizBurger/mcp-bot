"""Tests d'intégration du moteur sur un marché synthétique (aucune donnée réelle nécessaire)."""
import copy

import pandas as pd
import pytest

from synthetic import make_market
from xauof.config import load_config
from xauof.news.calendar import Calendar
from xauof.strategy import run as R
from xauof.strategy.engine import Engine, SimExecutor
from xauof.strategy.instrument import load_instrument
from xauof.strategy.risk import Account

DAYS = ["2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06", "2026-03-09",
        "2026-03-10", "2026-03-11", "2026-03-12", "2026-03-13"]
START, END = "2026-03-03", "2026-03-14"


@pytest.fixture(scope="module")
def market(tmp_path_factory):
    cfg = copy.deepcopy(load_config())
    cfg["data"]["dir"] = str(tmp_path_factory.mktemp("mkt") / "data")
    cfg["news"]["missing_calendar"] = "ignore_backtest_only"
    cfg["strategy"]["min_rr_net"] = 0.3          # TEST : assoupli pour obtenir des trades à vérifier
    make_market(cfg, DAYS, seed=2, kappa=0.03)
    return cfg, R.prepare(cfg, START, END)


def run(cfg, data, variant="A", executor=None, calendar=None):
    eng = Engine(cfg, variant, load_instrument(cfg), Account.from_config(cfg),
                 calendar or Calendar.empty(), executor or SimExecutor())
    for day in [d for d in data["days"] if pd.Timestamp(START) <= d < pd.Timestamp(END)]:
        prev = max(d for d in data["days"] if d < day)
        levels, reason = R.levels_for(cfg, data, prev)
        eng.run_day(R.day_data(cfg, data, day, levels, reason))
    return eng


def entries(eng):
    return [r for r in eng.journal if r["type"] == "entree"]


def test_invariants_on_trades(market):
    cfg, data = market
    eng = run(cfg, data, "A")
    tr = eng.trades
    assert len(tr) >= 2, "le marché de test doit produire des trades à vérifier"
    tz = cfg["strategy"]["timezone"]
    per_day = pd.Series([t.opened_at.tz_convert(tz).date() for t in tr]).value_counts()
    assert per_day.max() <= cfg["risk"]["max_entries_per_day"]
    assert len({t.zone_id for t in tr}) == len(tr)                       # une tentative par zone
    for a, b in zip(tr, tr[1:]):
        assert b.opened_at >= a.closed_at                                # une position à la fois
    xau = data["xau"]
    conf = {r["zone_id"]: pd.Timestamp(r["t"]) for r in eng.journal if r["type"] == "confirmation"}
    for t in tr:
        local = t.opened_at.tz_convert(tz)
        assert "08:20" <= local.strftime("%H:%M") <= "13:15"
        assert t.closed_at.tz_convert(tz).strftime("%H:%M") <= "13:30"
        assert t.opened_at == conf[t.zone_id]                            # ouverture de la bougie suivante
        bar = xau.loc[t.opened_at]
        expected = bar["open"] + bar["spread"] * 0.01 + 0.10 if t.direction > 0 else bar["open"] - 0.10
        assert t.entry == pytest.approx(expected)                        # ask pour un achat, bid pour une vente
        assert t.risk_usd <= 50.0 + 1e-9
        assert t.direction * (t.tp - t.entry) > 0 and t.direction * (t.entry - t.sl) > 0


def test_no_lookahead_truncated_data_gives_same_decisions(market):
    """Couper les données à T ne doit changer aucune décision prise avant T."""
    cfg, data = market
    full = run(cfg, data, "A")
    cut = pd.Timestamp("2026-03-11 16:00", tz="UTC")
    trunc = {k: (v[v.index < cut] if isinstance(v, pd.DataFrame) else v) for k, v in data.items()}
    part = run(cfg, trunc, "A")
    keep = {"contact_eligible", "contact_refuse", "confirmation", "entree", "entree_refusee", "refus", "ib"}
    sel = lambda eng: [r for r in eng.journal if r["type"] in keep and pd.Timestamp(r["t"]) < cut]  # noqa: E731
    a, b = sel(full), sel(part)
    assert len(a) > 10 and a == b


def test_stop_placement_failure_closes_and_locks_day(market):
    cfg, data = market
    eng = run(cfg, data, "A", executor=SimExecutor(fail_stop=True))
    fails = [r for r in eng.journal if r["type"] == "echec_sl"]
    assert fails and all(t.exit_reason == "échec du placement du SL" for t in eng.trades)
    days = pd.Series([t.opened_at.tz_convert("Europe/Prague").date() for t in eng.trades])
    assert days.value_counts().max() == 1                                 # journée verrouillée ensuite


def test_missing_calendar_blocks_entries(market):
    cfg, data = market
    strict = copy.deepcopy(cfg)
    strict["news"]["missing_calendar"] = "block"
    eng = run(strict, data, "A")
    assert eng.trades == []
    assert any("calendrier économique indisponible" in m for r in eng.journal
               if r["type"] == "contact_refuse" for m in r["motifs"])


def test_news_blackout_blocks_entries(market):
    cfg, data = market
    base = run(cfg, data, "A")
    t = base.trades[0].opened_at
    ev = pd.DataFrame({"currency": ["USD"], "impact": ["High"], "kind": ["OTHER"], "event": ["ISM"]},
                      index=pd.DatetimeIndex([t + pd.Timedelta(minutes=10)], name="time_utc"))
    cal = Calendar(ev, [(pd.Timestamp("2026-02-01", tz="UTC"), pd.Timestamp("2026-04-01", tz="UTC"))])
    eng = run(cfg, data, "A", calendar=cal)
    assert t not in [x.opened_at for x in eng.trades]


def test_unstable_basis_blocks_entries(market):
    cfg, data = market
    noisy = dict(data)
    noisy["basis"] = data["basis"].assign(bruit=5.0)
    eng = run(cfg, noisy, "A")
    assert eng.trades == []
    assert any("basis instable" in m for r in eng.journal if r["type"] == "contact_refuse" for m in r["motifs"])


def test_delta_variant_refuses_without_aggressor_side(market):
    cfg, data = market
    blind = dict(data)
    blind["gc"] = data["gc"].assign(buy=0, sell=0, delta=0)
    eng = run(cfg, blind, "C")
    assert eng.trades == []
    assert all("delta" in r["motif"] for r in eng.journal if r["type"] == "refus" and "delta" in r["motif"])


def test_holdout_is_protected(market):
    cfg, data = market
    with pytest.raises(SystemExit, match="réservée"):
        R.run(cfg, "2026-06-01", "2026-07-15", "A", data=data)


def test_aggressor_check_reports_unclassified_volume(market):
    cfg, data = market
    assert R.aggressor_check(data["gc"], START, END) < 0.05
    blind = data["gc"].assign(buy=0, sell=0)
    assert R.aggressor_check(blind, START, END) == pytest.approx(1.0)


def test_journal_records_data_availability(market):
    cfg, data = market
    eng = run(cfg, data, "A")
    rec = next(r for r in eng.journal if r["type"] == "entree")
    assert pd.Timestamp(rec["donnees"]["derniere_gc"]) < pd.Timestamp(rec["donnees"]["decision"])
    assert {"lots", "sl", "tp", "rr_net", "reste_jour", "reste_total", "spread_pts"} <= rec.keys()


def test_funnel_report_runs(market, tmp_path):
    from xauof.strategy import funnel

    cfg, data = market
    eng = run(cfg, data, "A")
    text = funnel.funnel(eng.journal)
    assert "Zones détectées" in text and "Premiers contacts" in text
    assert not [r for r in eng.journal if r["type"] == "zone" and r["state"] == "eligible"]
    motifs = [m for r in eng.journal if r["type"] == "contact_refuse" for m in r["motifs"]]
    assert not any(m == "aucun scénario compatible (sens ou retest de la borne cassée)" for m in motifs)
