"""Calendrier économique : historique officiel (backtests) et import live (sans réseau)."""
import copy

import pandas as pd

from xauof.config import load_config
from xauof.news import calendar as cal_mod
from xauof.news import history
from xauof.news.calendar import Calendar, parse_ff, save_import

U = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731


def test_history_times_follow_new_york_dst():
    ev = history.build_events().set_index("time_utc")
    nfp = ev[ev["kind"] == "NFP"].index
    assert U("2024-10-04 12:30") in nfp          # 08:30 EDT
    assert U("2025-01-10 13:30") in nfp          # 08:30 EST
    fomc = ev[ev["event"].str.contains("communiqué")].index
    assert U("2025-03-19 18:00") in fomc         # 14:00 EDT (heure d'été US déjà active)
    assert U("2024-12-18 19:00") in fomc         # 14:00 EST


def test_history_reflects_2025_shutdown_and_unknown_times():
    ev = history.build_events()
    nfp_days = set(ev.loc[ev["kind"] == "NFP", "time_utc"].dt.strftime("%Y-%m-%d"))
    assert "2025-11-20" in nfp_days and not any(d.startswith("2025-10") for d in nfp_days)
    dec5 = ev[ev["time_utc"].dt.strftime("%Y-%m-%d") == "2025-12-05"]
    assert len(dec5) == 2                        # horaire non publié : 08:30 ET 10:00 bloqués


def test_history_has_no_duplicates_and_matches_committed_csv():
    ev = history.build_events()
    assert not ev.duplicated(["time_utc", "event"]).any()
    committed = pd.read_csv(history.OUT_DIR / "usd_events.csv")
    assert len(committed) == len(ev)


def test_history_covers_backtest_and_holdout_periods():
    cal = Calendar.load(history.OUT_DIR)
    assert cal.covers(U("2024-10-07 08:00"), U("2026-07-01 00:00"))
    assert cal.covers(U("2026-07-01 00:00"), U("2026-09-30 20:00"))
    assert not cal.covers(U("2026-10-05 08:00"), U("2026-10-05 18:00"))
    assert cal.blackout(U("2026-07-02 12:20"), 15, 15, ["USD"])          # NFP du 2 juillet 2026


def _ff(day_offsets):
    base = pd.Timestamp("2026-10-18 10:15", tz="America/New_York")      # dimanche
    raw = [{"title": "IMF Meetings", "country": "All", "date": base.isoformat(), "impact": "Low"}]
    for k, title, impact in day_offsets:
        raw.append({"title": title, "country": "USD", "impact": impact,
                    "date": (base + pd.Timedelta(days=k, hours=-1, minutes=-45)).isoformat()})
    return raw


def test_parse_ff_week_coverage_and_filter():
    ev, (start, end) = parse_ff(_ff([(5, "Non-Farm Employment Change", "High"), (3, "ISM", "Medium")]))
    assert start == U("2026-10-18 04:00") and end == U("2026-10-25 04:00")   # dimanche → dimanche, New York
    assert list(ev["kind"]) == ["NFP"] and ev["time_utc"].iloc[0] == U("2026-10-23 12:30")


def test_load_many_merges_history_and_live(tmp_path):
    ev, cov = parse_ff(_ff([(5, "CPI m/m", "High")]))
    save_import(tmp_path / "live", ev, cov)
    cal = Calendar.load_many([history.OUT_DIR, tmp_path / "live"])
    assert cal.covers(U("2026-09-29 08:00"), U("2026-09-30 18:00"))
    assert cal.covers(U("2026-10-20 08:00"), U("2026-10-20 18:00"))
    assert not cal.covers(U("2026-10-05 08:00"), U("2026-10-05 18:00"))     # trou entre les deux
    assert cal.flatten_due(U("2026-10-23 12:20"), 15, ["CPI"], ["USD"])


def test_save_import_replaces_the_week(tmp_path):
    ev, cov = parse_ff(_ff([(5, "CPI m/m", "High")]))
    save_import(tmp_path, ev, cov)
    ev2, cov2 = parse_ff(_ff([(4, "CPI m/m", "High")]))                      # horaire déplacé
    save_import(tmp_path, ev2, cov2)
    cal = Calendar.load(tmp_path)
    assert list(cal.events.index) == [U("2026-10-22 12:30")]
    assert len(cal.coverage) == 1


def test_ensure_fresh_blocks_when_import_fails(tmp_path, monkeypatch):
    cfg = copy.deepcopy(load_config())
    cfg["news"]["calendar_dirs"] = [str(tmp_path / "live")]
    cfg["news"]["live_dir"] = str(tmp_path / "live")
    monkeypatch.setattr(cal_mod, "import_ff_week", lambda d: (_ for _ in ()).throw(OSError("réseau coupé")))
    cal, msg = cal_mod.ensure_fresh(cfg, U("2026-10-20 12:00"))
    assert "bloquées" in msg and not cal.covers(U("2026-10-20 12:00"), U("2026-10-20 13:00"))


def test_ensure_fresh_imports_when_needed(tmp_path, monkeypatch):
    cfg = copy.deepcopy(load_config())
    cfg["news"]["calendar_dirs"] = [str(tmp_path / "live")]
    cfg["news"]["live_dir"] = str(tmp_path / "live")

    def fake_import(directory):
        ev, cov = parse_ff(_ff([(5, "CPI m/m", "High")]))
        save_import(directory, ev, cov)
        return len(ev)

    monkeypatch.setattr(cal_mod, "import_ff_week", fake_import)
    cal, msg = cal_mod.ensure_fresh(cfg, U("2026-10-20 12:00"))
    assert msg == "1 annonces importées" and cal.covers(U("2026-10-20 12:00"), U("2026-10-21 12:00"))
    assert cal_mod.ensure_fresh(cfg, U("2026-10-20 13:00"))[1] == "à jour"


def test_backtest_period_is_covered_by_default_config():
    cal = cal_mod.load_calendar(load_config())
    assert cal.covers(U("2024-10-07 08:00"), U("2026-06-30 18:00"))
