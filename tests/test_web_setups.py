import pytest
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
    res = replay_day(cfg, src._bars[(src._bars.index >= a) & (src._bars.index < b)], src._levels, day)
    ev = res["alertes"]
    assert res["plans"] and all("issue" in p or "refus" in p["plan"] for p in res["plans"])
    assert ev, "la journée démo contient des setups A+"
    lo, hi = ny_time(day, cfg["strategy"]["entry_start"], ny), ny_time(day, cfg["strategy"]["entry_end"], ny)
    assert all(lo <= e["t"] <= hi for e in ev)
    assert all(e["t"] <= f["t"] for e, f in zip(ev, ev[1:]))
    keys = [(e["texte"].split(" sur ")[0].split("— ")[1], e["etape"]) for e in ev]
    assert len(keys) == len(set(keys))
    out = summarize(ev, [day])
    assert "par semaine" in out and "rentabilité" in out


def _inst():
    from xauof.config import load_config
    from xauof.strategy.instrument import load_instrument

    return load_instrument(load_config())


def test_plan_buy_targets_first_level_at_min_rr():
    p = su.trade_plan(zone(1, 89.8, 90.5), LV, atr=1.0, spread=0.3, inst=_inst(), budget_usd=50)
    assert p["entree"] == 90.5 and p["sl"] == 89.55
    assert p["tp"] == 94.9 and p["cible"] == "LVN"                # premier niveau à ≥ 1,5 R, 0,1 ATR avant
    assert p["rr"] >= su.PLAN_MIN_RR and 0 < p["risque_usd"] <= 50 and p["lots"] > 0


def test_plan_sell_accounts_for_spread_and_lists_obstacles():
    p = su.trade_plan(zone(-1, 109.6, 110.3), LV, atr=1.0, spread=0.3, inst=_inst(), budget_usd=50)
    assert p["entree"] == 109.6 and p["sl"] == 110.85               # au-dessus de la zone + spread + 0,25 ATR
    assert p["tp"] == 106.4 and p["cible"].startswith("HVN")       # bord haut du HVN 104-106, + spread + 0,1 ATR
    far = su.trade_plan(zone(-1, 109.6, 110.3), {**LV, "hvn": [], "lvn": [95.0, 108.5]}, 1.0, 0.3, _inst(), 50)
    assert far["cible"] == "POC TPO" and "LVN" in far["obstacles"]


def test_plan_refused_without_target_and_shown_in_alert(monkeypatch):
    monkeypatch.setenv("XAUOF_NO_TELEGRAM", "1")
    p = su.trade_plan(zone(1, 113.2, 113.8), LV, atr=1.0, spread=0.3, inst=_inst(), budget_usd=50)
    assert "RR insuffisant" in p["refus"]
    ac = su.AlertCenter()
    s = su.qualify([zone(1, 89.8, 90.5, zid="b")], LV, price=95.0, atr=1.0, near_atr=0.5)
    s[0]["plan"] = su.trade_plan(s[0]["zone"], LV, 1.0, 0.3, _inst(), 50)
    ac.from_setups(s, T, None)
    assert "SL 89.55" in ac.feed[0]["texte"] and "Non validé" in ac.feed[0]["texte"]


def _bars(rows, start="2026-10-23 14:00"):
    idx = pd.date_range(start, periods=len(rows), freq="1min", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "spread"], index=idx)


def test_simulate_plan_fill_tp_and_conservative_conventions():
    from xauof.web.alert_stats import simulate_plan

    inst, end, flat = _inst(), pd.Timestamp("2026-10-23 17:15", tz="UTC"), pd.Timestamp("2026-10-23 17:30", tz="UTC")
    plan = {"entree": 100.0, "sl": 99.0, "tp": 102.0}
    # achat : rempli quand l'ask (bid + 0,30) descend à 100 ; TP ignoré sur la bougie d'entrée
    r = simulate_plan(plan, 1, _bars([[101, 101, 100.5, 100.8, 30], [100.5, 102.5, 99.6, 101, 30],
                                      [101, 102.1, 100.9, 102, 30]]), end, flat, inst, 0.01)
    assert r["issue"] == "TP" and r["t_entree"] == pd.Timestamp("2026-10-23 14:01", tz="UTC")
    assert r["r"] == pytest.approx((2.0 * inst.value_per_price - inst.commission_rt) / inst.loss_per_lot(100, 99))
    # SL et TP sur la même bougie : le SL l'emporte
    r = simulate_plan(plan, 1, _bars([[100.2, 100.3, 99.6, 100, 30], [100, 102.5, 98.9, 100, 30]]), end, flat, inst, 0.01)
    assert r["issue"] == "SL" and r["r"] < -0.95
    # jamais rempli avant la limite d'entrée
    assert simulate_plan(plan, 1, _bars([[101, 101.5, 100.8, 101, 30]]), end, flat, inst, 0.01)["issue"] == "non rempli"
    # vente : rempli quand le bid monte à l'entrée, SL déclenché par l'ask
    sell = {"entree": 100.0, "sl": 101.0, "tp": 98.0}
    r = simulate_plan(sell, -1, _bars([[99.5, 100.1, 99.4, 99.9, 30], [99.9, 100.75, 99.8, 100.6, 30]]),
                      end, flat, inst, 0.01)
    assert r["issue"] == "SL"


def test_h1_day_direction_requires_finished_ib_and_breakout():
    from xauof.web.alert_stats import day_direction_ok

    end = pd.Timestamp("2026-10-23 13:20", tz="UTC")
    assert not day_direction_ok(1, 105.0, end - pd.Timedelta(minutes=1), end, 104.0, 100.0)   # IB pas finie
    assert day_direction_ok(1, 105.0, end, end, 104.0, 100.0)
    assert not day_direction_ok(1, 103.0, end, end, 104.0, 100.0)                            # dans l'IB
    assert day_direction_ok(-1, 99.0, end, end, 104.0, 100.0)
    assert not day_direction_ok(-1, 105.0, end, end, 104.0, 100.0)


def test_h2_confirmation_needs_candle_and_gc_delta_in_trade_direction():
    from xauof.web.alert_stats import simulate_confirm

    inst = _inst()
    end, flat = pd.Timestamp("2026-10-23 17:15", tz="UTC"), pd.Timestamp("2026-10-23 17:30", tz="UTC")
    lv = {**LV, "lvn": [95.0, 104.0]}
    z = zone(1, 99.0, 100.0)                        # achat ; SL = 99 - 0,25 = 98.75
    bars = _bars([[101, 101, 99.8, 99.9, 30],       # contact, bougie baissière
                  [99.9, 100.4, 99.7, 100.3, 30],   # haussière mais delta GC négatif : pas de signal
                  [100.3, 100.8, 100.2, 100.7, 30], # haussière + delta positif : signal
                  [100.7, 101.0, 100.6, 100.9, 30], # entrée à l'ouverture : 100.7 + 0.30 + 0.10
                  [100.9, 106.2, 100.8, 106.0, 30]])
    delta = pd.Series([-5.0, -3.0, 8.0, 2.0, 4.0], index=bars.index)
    r = simulate_confirm(z, 1.0, 0.3, bars, delta, lv, end, flat, inst, 0.01)
    assert r["entree_reelle"] == pytest.approx(101.1) and r["t_entree"] == bars.index[3]
    assert r["plan"]["tp"] == 105.9 and r["issue"] == "TP" and r["r"] > 1   # LVN 104 < 1,5 R : bord haut du HVN
    invalid = _bars([[101, 101, 99.8, 99.9, 30], [99.9, 99.9, 98.5, 98.6, 30]])
    assert simulate_confirm(z, 1.0, 0.3, invalid, pd.Series([1.0, 1.0], index=invalid.index), lv, end, flat,
                            inst, 0.01)["plan"]["refus"].startswith("zone invalidée")
