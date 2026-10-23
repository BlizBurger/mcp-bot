import pandas as pd

from xauof.strategy.zones import Zone
from xauof.web import setups as su

T = pd.Timestamp("2026-10-23 14:00", tz="UTC")
LV = {"poc_v": 100.0, "vah_v": 110.0, "val_v": 90.0, "poc_t": 101.0, "vah_t": 111.0, "val_t": 89.0,
      "hvn": [(104.0, 106.0)], "lvn": [95.0], "singles": [(113.0, 114.0)]}


def zone(direction, lo, hi, state="eligible", zid="z"):
    z = Zone(zid, "FVG", direction, lo, hi, T)
    z.state = state
    return z


def test_buy_on_val_inside_value_is_a_plus():
    s = su.qualify([zone(1, 89.8, 90.5)], LV, price=95.0, atr=1.0, near_atr=0.5)
    assert len(s) == 1 and s[0]["contexte"] == "achat sur la VAL"
    assert any(h.startswith("VAL vol") for h in s[0]["niveaux"])


def test_buy_on_poc_inside_value_is_out_of_context():
    assert su.qualify([zone(1, 99.8, 100.4)], LV, price=103.0, atr=1.0, near_atr=0.5) == []


def test_buy_above_value_on_single_prints():
    s = su.qualify([zone(1, 113.2, 113.8)], LV, price=116.0, atr=1.0, near_atr=0.5)
    assert s and s[0]["contexte"] == "achat au-dessus de la Value Area"


def test_zone_without_level_is_ignored_and_states_filtered():
    assert su.qualify([zone(-1, 97.0, 97.5)], LV, price=85.0, atr=1.0, near_atr=0.5) == []
    assert su.qualify([zone(1, 89.8, 90.5, state="consumed")], LV, price=95.0, atr=1.0, near_atr=0.5) == []
    assert su.qualify([zone(1, 89.8, 90.5)], None, price=95.0, atr=1.0, near_atr=0.5) == []


def test_alert_center_dedupes_and_escalates(monkeypatch):
    monkeypatch.setenv("XAUOF_NO_TELEGRAM", "1")
    ac = su.AlertCenter()
    s = su.qualify([zone(1, 89.8, 90.5, zid="a")], LV, price=95.0, atr=1.0, near_atr=0.5)
    ac.from_setups(s, T, None)
    ac.from_setups(s, T, None)
    assert len(ac.feed) == 1 and "en préparation" in ac.feed[0]["texte"]
    s2 = su.qualify([zone(1, 89.8, 90.5, state="touched", zid="a")], LV, price=90.2, atr=1.0, near_atr=0.5)
    ac.from_setups(s2, T, "CPI (12:30 UTC)")
    assert ac.feed[0]["niveau"] == "alerte" and "fenêtre news" in ac.feed[0]["texte"]


def test_replay_day_matches_live_alert_logic(monkeypatch):
    """Rejeu historique : alertes datées dans la fenêtre d'entrée, une seule par zone et par étape."""
    from xauof.assistant.snapshot import ny_time
    from xauof.config import load_config
    from xauof.web.alert_stats import replay_day, summarize
    from xauof.web.sources import DemoSource

    monkeypatch.setenv("XAUOF_NO_TELEGRAM", "1")
    cfg = load_config()
    src, day, ny = DemoSource(cfg, speed=0.0), pd.Timestamp("2026-10-23"), "America/New_York"
    a, b = ny_time(day, cfg["strategy"]["warmup_start"], ny), ny_time(day, "13:35", ny)
    ev = replay_day(cfg, src._bars[(src._bars.index >= a) & (src._bars.index < b)], src._levels, day)
    assert ev, "la journée démo contient des setups A+"
    lo, hi = ny_time(day, cfg["strategy"]["entry_start"], ny), ny_time(day, cfg["strategy"]["entry_end"], ny)
    assert all(lo <= e["t"] <= hi for e in ev)
    assert all(e["t"] <= f["t"] for e, f in zip(ev, ev[1:]))
    keys = [(e["texte"].split(" sur ")[0].split("— ")[1], e["etape"]) for e in ev]
    assert len(keys) == len(set(keys))
    out = summarize(ev, [day])
    assert "par semaine" in out and "rentabilité" in out
