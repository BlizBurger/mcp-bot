"""Tests critiques du moteur de stratégie (phase 4)."""
import copy

import pandas as pd
import pytest

from xauof.config import load_config
from xauof.news.calendar import Calendar
from xauof.strategy import context as ctx
from xauof.strategy.engine import Engine, Position, SimExecutor
from xauof.strategy.instrument import load_instrument, size_position
from xauof.strategy.risk import Account, spread_blocked
from xauof.strategy.scenarios import ScenarioTracker
from xauof.strategy.zones import Zone, ZoneBook, ZoneDetector

T0 = pd.Timestamp("2026-09-15 12:20", tz="UTC")          # 08:20 New York (EDT)


@pytest.fixture
def cfg(tmp_path):
    c = copy.deepcopy(load_config())
    c["data"]["dir"] = str(tmp_path / "data")
    c["costs"]["commission_per_lot_round_trip"] = 6.0      # chiffres des tests calculés avec 6 $/lot
    return c


def m5(rows, start=T0 - pd.Timedelta(minutes=5 * 20)):
    """rows : (open, high, low, close). Préfixe de 20 bougies calmes pour l'ATR (range 1 $)."""
    base = [(100.0, 100.5, 99.5, 100.0)] * 20
    idx = pd.date_range(start, periods=len(base) + len(rows), freq="5min")
    return pd.DataFrame(base + list(rows), columns=["open", "high", "low", "close"], index=idx)


def feed(detector, bars, spread=0.3):
    """Appelle le détecteur à chaque clôture, comme le moteur (données clôturées seulement)."""
    zones = []
    for i in range(1, len(bars) + 1):
        zones += detector.on_m5_close(bars.iloc[:i], spread)
    return zones


# --------------------------------------------------------------------------- zones

def test_fvg_activated_only_after_third_close(cfg):
    bars = m5([(100, 100.5, 99.8, 100.4), (100.4, 101.6, 100.4, 101.5), (101.5, 101.9, 100.9, 101.8)])
    det = ZoneDetector(cfg, T0, "t")
    seen_before = sum(len(det.on_m5_close(bars.iloc[:i], 0.3)) for i in range(1, len(bars)))
    assert seen_before == 0                                   # rien avant la 3e clôture
    z = [z for z in det.on_m5_close(bars, 0.3) if z.kind == "FVG"]
    assert len(z) == 1 and z[0].direction == 1
    assert (z[0].low, z[0].high) == (100.5, 100.9)
    assert z[0].active_from == bars.index[-1] + pd.Timedelta(minutes=5)


def test_fvg_size_bounds(cfg):
    det = ZoneDetector(cfg, T0, "t")
    tiny = m5([(100, 100.5, 99.8, 100.4), (100.4, 101.0, 100.4, 100.9), (100.9, 101.0, 100.55, 100.9)])
    z = [z for z in feed(det, tiny) if z.kind == "FVG"][0]
    assert z.state == "rejected" and "taille" in z.history[-1][2]   # 0.05 < max(2×0.3, 0.1 ATR)
    det = ZoneDetector(cfg, T0, "t")
    huge = m5([(100, 100.5, 99.8, 100.4), (100.4, 104.0, 100.4, 103.9), (103.9, 104.5, 102.0, 104.2)])
    z = [z for z in feed(det, huge) if z.kind == "FVG"][0]
    assert z.state == "rejected"                                    # 1.5 > 1 ATR


def _ob_bars(break_at_offset):
    """Pivot haut à 101 (2 bougies de chaque côté), bougie baissière, puis cassure impulsive."""
    rows = [(100.0, 100.4, 99.8, 100.2), (100.2, 100.6, 100.0, 100.4), (100.4, 101.0, 100.3, 100.6),  # pivot
            (100.6, 100.7, 100.2, 100.3), (100.3, 100.5, 99.9, 100.0)]                               # confirmation
    filler = [(100.0, 100.3, 99.8, 100.1)] * break_at_offset
    bear = [(100.2, 100.3, 99.7, 99.8)]
    brk = [(99.8, 101.9, 99.8, 101.8)]                                                              # corps 2.0
    return m5(rows + filler + bear + brk)


def test_order_block_after_confirmed_pivot(cfg):
    det = ZoneDetector(cfg, T0 - pd.Timedelta(hours=3), "t")
    bars = _ob_bars(1)
    obs = [z for z in feed(det, bars) if z.kind == "OB"]
    assert len(obs) == 1
    ob = obs[0]
    assert ob.direction == 1 and (ob.low, ob.high) == (99.7, 100.3)    # range complet de la bougie baissière
    assert ob.active_from == bars.index[-1] + pd.Timedelta(minutes=5)  # après la clôture de cassure
    assert ob.meta["pivot"] == 101.0


def test_no_order_block_on_unconfirmed_pivot(cfg):
    # la « cassure » arrive alors que le pivot n'a qu'une bougie à droite : pas encore confirmé
    rows = [(100.0, 100.4, 99.8, 100.2), (100.2, 100.6, 100.0, 100.4), (100.4, 101.0, 100.3, 100.6),
            (100.6, 100.7, 99.7, 99.8), (99.8, 101.9, 99.8, 101.8)]
    det = ZoneDetector(cfg, T0 - pd.Timedelta(hours=3), "t")
    assert not [z for z in feed(det, m5(rows)) if z.kind == "OB"]


def test_order_block_body_measured_with_atr_before_break(cfg):
    cfg["strategy"]["ob_body_atr"] = 3.0                        # corps 2.0 < 3 × ATR(≈1) → refus
    det = ZoneDetector(cfg, T0 - pd.Timedelta(hours=3), "t")
    assert not [z for z in feed(det, _ob_bars(1)) if z.kind == "OB"]


def _zone(zid, direction, lo, hi, t=T0):
    z = Zone(zid, "FVG", direction, lo, hi, t)
    z.set(t, "detected")
    return z


def test_dedup_contact_consumes_and_invalidation():
    book = ZoneBook()
    a, b, c = _zone("a", 1, 100, 101), _zone("b", 1, 100.5, 102), _zone("c", -1, 100.5, 102)
    for z in (a, b, c):
        book.add(z)
    assert (a.state, b.state, c.state) == ("eligible", "duplicate", "eligible")
    t1 = T0 + pd.Timedelta(minutes=1)
    assert book.contacts(t1, bid_low=101.5, ask_high=99) == []
    assert [z.id for z in book.contacts(t1, bid_low=100.9, ask_high=99)] == ["a"]
    assert book.contacts(t1 + pd.Timedelta(minutes=1), bid_low=100.2, ask_high=99) == []   # premier contact seul
    book.invalidate(t1, bid_close=99.9)
    assert a.state == "invalidated"
    assert [s for _, s, _ in a.history] == ["detected", "eligible", "touched", "invalidated"]


def test_zone_not_live_before_activation():
    book = ZoneBook()
    z = _zone("a", 1, 100, 101, t=T0 + pd.Timedelta(minutes=5))
    book.add(z)
    assert book.contacts(T0, bid_low=100.5, ask_high=0) == []


# --------------------------------------------------------------------------- scénarios

def _scen_bars(closes, start=T0):
    idx = pd.date_range(start, periods=len(closes), freq="5min")
    return pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes}, index=idx)


def test_value_return_within_three_bars(cfg):
    tr = ScenarioTracker(cfg, val=90, vah=110, session_start=T0)
    bars = _scen_bars([100, 112, 113, 108])
    for i in range(1, len(bars) + 1):
        tr.on_m5_close(bars.iloc[:i], pd.Series(dtype=float))
    setups = tr.active(bars.index[-1] + pd.Timedelta(minutes=5))
    assert [(s.name, s.direction) for s in setups] == [("value_return", -1)]


def test_value_return_too_late(cfg):
    tr = ScenarioTracker(cfg, val=90, vah=110, session_start=T0)
    bars = _scen_bars([100, 112, 113, 114, 115, 108])                  # 4 bougies dehors
    for i in range(1, len(bars) + 1):
        tr.on_m5_close(bars.iloc[:i], pd.Series(dtype=float))
    assert not [s for s in tr.active(bars.index[-1] + pd.Timedelta(minutes=5)) if s.name == "value_return"]


def test_breakout_needs_volume_and_deactivates(cfg):
    start = T0 - pd.Timedelta(minutes=5 * 42)
    closes = [100] * 42 + [111, 112, 113, 109]
    bars = _scen_bars(closes, start)
    for vol_pair, expected in ((50.0, None), (500.0, 1)):
        vols = pd.Series([100.0] * 42 + [vol_pair / 2] * 2 + [100.0, 100.0], index=bars.index)
        tr = ScenarioTracker(cfg, val=90, vah=110, session_start=start + pd.Timedelta(minutes=5 * 42))
        for i in range(1, 45):
            tr.on_m5_close(bars.iloc[:i], vols)
        assert (tr.breakout.direction if tr.breakout else None) == expected
    tr.on_m5_close(bars.iloc[:45], vols)
    tr.on_m5_close(bars, vols)                                          # clôture revenue dans la VA
    assert tr.breakout is None


# --------------------------------------------------------------------------- contexte

def _levels(**kw):
    base = dict(source_day=pd.Timestamp("2026-09-14"), contract=7, basis=30.0, poc_v=100.0, vah_v=110.0,
                val_v=90.0, poc_t=101.0, vah_t=111.0, val_t=89.0, hvn=[(104.0, 106.0)], lvn=[95.0],
                singles=[(112.5, 113.0)])
    base.update(kw)
    return ctx.SessionLevels(**base)


def test_score_one_point_per_family_poc_excluded():
    lv = _levels()
    z = _zone("z", 1, 94.9, 95.5)                       # LVN 95 dedans, VAL 90 loin
    sc = ctx.score(z, lv, None, tol=0.3)
    assert sc["families"] == {"rejet": True, "bornes_valeur": False, "ib": False} and sc["score"] == 1
    z2 = _zone("z2", 1, 109.8, 111.2)                   # VAH vol ET VAH TPO : une seule famille
    assert ctx.score(z2, lv, (80.0, 111.1), tol=0.3)["score"] == 2   # bornes + IB haut
    z3 = _zone("z3", 1, 99.8, 100.2)                    # POC dedans : 0 point
    assert ctx.score(z3, lv, None, tol=0.3)["score"] == 0


def test_target_takes_nearest_obstacle_and_hvn_edge():
    lv = _levels()
    assert ctx.target(1, 97.0, lv) == (100.0, "POC vol")
    assert ctx.target(1, 102.0, lv)[0] == 104.0           # bord proche de la HVN 104-106
    assert ctx.target(-1, 108.0, lv)[0] == 106.0          # vente : bord haut de la HVN
    assert ctx.target(1, 120.0, lv) is None               # rien au-dessus → trade refusé


def test_breakout_measured_move_target():
    lv = _levels()                                        # VA volume 90-110 : hauteur 20
    assert ctx.measured_move(1, lv, "volume") == (130.0, "mouvement mesuré")
    assert ctx.measured_move(-1, lv, "volume") == (70.0, "mouvement mesuré")
    assert ctx.target(1, 115.0, lv, [ctx.measured_move(1, lv, "volume")]) == (130.0, "mouvement mesuré")
    lv2 = _levels(hvn=[(120.0, 122.0)])                   # obstacle avant l'objectif : il gagne
    assert ctx.target(1, 115.0, lv2, [ctx.measured_move(1, lv2, "volume")])[0] == 120.0


# --------------------------------------------------------------------------- taille et coûts

def test_sizing_rounds_down_and_refuses_min_lot(cfg):
    inst = load_instrument(cfg)
    sz = size_position(inst, 1, 4200.0, 4196.0, 4210.0, 50.0)
    assert sz.lots == 0.12 and sz.risk_usd <= 50.0
    assert sz.rr_net == pytest.approx(119.28 / 49.92)
    big_sl = size_position(inst, 1, 4200.0, 4140.0, 4300.0, 50.0)      # 0,01 lot coûterait 60,16 $
    assert big_sl.refused and "lot minimum" in big_sl.refused
    assert size_position(inst, 1, 4200.0, 4205.0, 4210.0, 50.0).refused   # SL du mauvais côté


# --------------------------------------------------------------------------- compte et filtres

def test_account_resets_at_prague_midnight_across_dst(cfg):
    a = Account.from_config(cfg)
    a.roll(pd.Timestamp("2026-07-01 21:59", tz="UTC"))
    a.balance = 9900
    a.roll(pd.Timestamp("2026-07-01 22:00", tz="UTC"))                  # minuit Prague (UTC+2)
    assert a.day_start_balance == 9900
    a.balance = 9850
    a.roll(pd.Timestamp("2026-12-01 22:30", tz="UTC"))                  # 23:30 Prague (UTC+1)
    a.roll(pd.Timestamp("2026-12-01 23:00", tz="UTC"))                  # minuit Prague
    assert a.day_start_balance == 9850


def test_account_breaches(cfg):
    a = Account.from_config(cfg)
    a.roll(T0)
    assert a.breach(9851) is None
    assert "journalier" in a.breach(9850)
    assert a.internal_floor == 9200 and a.ftmo_floor == 9000
    a.day_start_balance = 9300
    assert "plancher" in a.breach(9199)
    assert a.risk_budget(10500, 0.5) == 50 and a.risk_budget(9500, 0.5) == 47.5


def test_spread_filter():
    idx = pd.date_range(T0 - pd.Timedelta(minutes=60), periods=60, freq="1min")
    spreads = pd.Series(30.0, index=idx)
    assert spread_blocked(spreads, T0, 50, 60, 60, 2.0) is None
    assert "spread" in spread_blocked(spreads, T0, 61, 60, 60, 2.0)
    assert "insuffisant" in spread_blocked(spreads.iloc[5:], T0, 30, 60, 60, 2.0)


def test_calendar_windows_and_coverage():
    ev = pd.DataFrame({"currency": ["USD", "EUR"], "impact": ["High", "High"], "kind": ["NFP", "OTHER"],
                       "event": ["Non-Farm Employment Change", "ECB"]},
                      index=pd.DatetimeIndex(["2026-09-04 12:30", "2026-09-04 13:00"], tz="UTC", name="time_utc"))
    cal = Calendar(ev, [(pd.Timestamp("2026-08-31", tz="UTC"), pd.Timestamp("2026-09-07", tz="UTC"))])
    t = lambda s: pd.Timestamp(f"2026-09-04 {s}", tz="UTC")  # noqa: E731
    assert cal.blackout(t("12:14"), 15, 15, ["USD"]) is None
    assert cal.blackout(t("12:15"), 15, 15, ["USD"])
    assert cal.blackout(t("12:45"), 15, 15, ["USD"])
    assert cal.blackout(t("12:46"), 15, 15, ["USD"]) is None
    assert cal.blackout(t("13:00"), 15, 15, ["USD"]) is None          # annonce EUR ignorée
    assert cal.flatten_due(t("12:15"), 15, ["NFP", "CPI", "FOMC"], ["USD"])
    assert cal.covers(t("08:00"), t("17:30"))
    assert not cal.covers(pd.Timestamp("2026-09-08 08:00", tz="UTC"), pd.Timestamp("2026-09-08 17:30", tz="UTC"))
    assert not Calendar.empty().covers(t("08:00"), t("09:00"))


# --------------------------------------------------------------------------- gestion de position

def _engine(cfg, fail_stop=False, variant="A"):
    eng = Engine(cfg, variant, load_instrument(cfg), Account.from_config(cfg), Calendar.empty(), SimExecutor(fail_stop))
    eng.account.roll(T0)
    eng.t_flat = T0 + pd.Timedelta(hours=5, minutes=10)
    eng.cal_ok = False
    eng.book = ZoneBook()
    return eng


def _pos(direction=1, entry=4200.0, sl=4196.0, tp=4210.0, lots=0.12):
    return Position("z", "value_return", direction, lots, entry, sl, tp, T0, 49.92, 2.39, "POC vol")


def _bar(o, h, lo, c, spread=30):
    return pd.Series({"open": o, "high": h, "low": lo, "close": c, "spread": spread})


def test_sl_and_tp_in_same_bar_counts_sl(cfg):
    eng = _engine(cfg)
    eng.position = _pos()
    eng._manage(0, T0, _bar(4200, 4211, 4195, 4205))
    tr = eng.trades[-1]
    assert tr.exit_reason == "SL" and tr.exit == pytest.approx(4196.0 - 0.10)


def test_gap_beyond_sl_fills_at_open(cfg):
    eng = _engine(cfg)
    eng.position = _pos()
    eng._manage(0, T0, _bar(4194, 4195, 4193, 4194))
    assert eng.trades[-1].exit_reason.startswith("SL (ouverture") and eng.trades[-1].exit == pytest.approx(4193.9)


def test_short_exits_use_ask(cfg):
    eng = _engine(cfg)
    eng.position = _pos(direction=-1, entry=4200.0, sl=4204.0, tp=4190.0)
    eng._manage(0, T0, _bar(4199, 4203.8, 4198, 4199))             # ask haut = 4204.1 ≥ SL
    assert eng.trades[-1].exit_reason == "SL"
    eng.position = _pos(direction=-1, entry=4200.0, sl=4204.0, tp=4190.0)
    eng._manage(0, T0, _bar(4192, 4193, 4189.8, 4191))             # ask bas = 4190.1 > TP : pas de TP
    assert eng.position is not None


def test_forced_flat_at_1330(cfg):
    eng = _engine(cfg)
    eng.position = _pos()
    eng._manage(0, eng.t_flat, _bar(4202, 4203, 4201, 4202))
    assert "fin de séance" in eng.trades[-1].exit_reason and eng.trades[-1].exit == pytest.approx(4201.9)


def test_news_flatten_before_major_release(cfg):
    eng = _engine(cfg)
    ev = pd.DataFrame({"currency": ["USD"], "impact": ["High"], "kind": ["CPI"], "event": ["CPI m/m"]},
                      index=pd.DatetimeIndex([T0 + pd.Timedelta(minutes=30)], tz="UTC", name="time_utc"))
    eng.calendar, eng.cal_ok = Calendar(ev, [(T0 - pd.Timedelta(days=1), T0 + pd.Timedelta(days=1))]), True
    eng.position = _pos()
    eng._manage(0, T0 + pd.Timedelta(minutes=14), _bar(4201, 4202, 4200, 4201))
    assert eng.position is not None
    eng._manage(1, T0 + pd.Timedelta(minutes=15), _bar(4201, 4202, 4200, 4201))
    assert "avant annonce" in eng.trades[-1].exit_reason


def test_daily_stop_closes_and_locks(cfg):
    eng = _engine(cfg)
    eng.account.balance = eng.account.day_start_balance - 120         # déjà -120 $ aujourd'hui
    eng.position = _pos(sl=4150.0, lots=0.12)                          # SL lointain : l'arrêt journalier passe avant
    eng._manage(0, T0, _bar(4199, 4199.5, 4196, 4197))
    assert eng.position is None and eng.account.day_locked
    assert "journalier" in eng.trades[-1].exit_reason
    assert eng.account.daily_loss(eng.account.balance) >= 150 - 1e-6


# --------------------------------------------------------------------------- V2

def _v2_pos(direction=1):
    if direction > 0:
        p = _pos(direction=1, entry=4200.0, sl=4196.0, tp=4208.0, lots=0.12)
        p.partial_level = 4204.0
    else:
        p = _pos(direction=-1, entry=4200.0, sl=4204.0, tp=4192.0, lots=0.12)
        p.partial_level = 4196.0
    p.partial_lots, p.lots_initial = 0.06, 0.12
    return p


def _open(eng, pos):
    """Comme une vraie entrée : demi-commission d'ouverture débitée."""
    half = 6.0 * pos.lots / 2
    eng.account.balance -= half
    pos.commission = half
    eng.position = pos


def test_v2_partial_then_target(cfg):
    eng = _engine(cfg, variant="V2_DF")
    _open(eng, _v2_pos())
    eng._manage(0, T0, _bar(4201, 4204.5, 4201, 4204))
    assert eng.position.partial_done and eng.position.sl == 4200.0 and eng.position.lots == 0.06
    eng._manage(1, T0, _bar(4204, 4208.2, 4202, 4208))
    tr = eng.trades[-1]
    assert tr.exit_reason == "TP (après partiel)"
    assert tr.pnl == pytest.approx(0.06 * 4 * 100 + 0.06 * 8 * 100 - 6.0 * 0.12)
    assert eng.account.balance == pytest.approx(10000 + tr.pnl)


def test_v2_breakeven_in_same_bar_as_partial_is_conservative(cfg):
    eng = _engine(cfg, variant="V2_DF")
    _open(eng, _v2_pos())
    eng._manage(0, T0, _bar(4201, 4205, 4199.5, 4203))
    tr = eng.trades[-1]
    assert tr.exit_reason == "break-even (même bougie que le partiel)"
    assert tr.pnl == pytest.approx(0.06 * 4 * 100 + 0.06 * (-0.10) * 100 - 0.72)


def test_v2_stop_before_partial_in_same_bar(cfg):
    eng = _engine(cfg, variant="V2_DF")
    _open(eng, _v2_pos())
    eng._manage(0, T0, _bar(4201, 4205, 4195.5, 4197))
    tr = eng.trades[-1]
    assert tr.exit_reason == "SL" and not tr.partial_done
    assert tr.pnl == pytest.approx(0.12 * -4.1 * 100 - 0.72)


def test_v2_short_partial_uses_ask(cfg):
    eng = _engine(cfg, variant="V2_DF")
    eng.position = _v2_pos(direction=-1)
    eng._manage(0, T0, _bar(4199, 4199.5, 4196.0, 4197))          # ask bas = 4196.3 > 4196 : pas de partiel
    assert not eng.position.partial_done
    eng._manage(1, T0, _bar(4197, 4197.5, 4195.6, 4196))          # ask bas = 4195.9 ≤ 4196 : partiel
    assert eng.position.partial_done and eng.position.sl == 4200.0


def test_v2_without_breakeven_keeps_stop(cfg):
    cfg["strategy"]["v2"]["breakeven_after_partial"] = False
    eng = _engine(cfg, variant="V2_DF")
    eng.position = _v2_pos()
    eng._manage(0, T0, _bar(4201, 4204.5, 4201, 4204))
    assert eng.position.partial_done and eng.position.sl == 4196.0


def test_obstacle_before_space_filter():
    lv = _levels()                                        # POC 100, HVN 104-106, VAH 110 / 111
    assert ctx.obstacle_before(1, 98.0, 101.0, lv) == (100.0, "POC vol")
    assert ctx.obstacle_before(1, 101.5, 103.5, lv) is None   # POC TPO 101 derrière, HVN à 104 au-delà
    assert ctx.obstacle_before(-1, 108.0, 105.5, lv) == (106.0, "HVN 104.00-106.00")



# --------------------------------------------------------------------------- V3

def _v3_day(cfg, path):
    """Séance XAUUSD construite à la main : path = [(heure NY 'HH:MM', open, high, low, close), ...]
    (une entrée par minute à partir de 04:00 ; les minutes absentes répètent la précédente)."""
    from xauof.strategy.engine import DayData

    day = pd.Timestamp("2026-09-15")
    t0 = (day + pd.Timedelta("04:00:00")).tz_localize("America/New_York")
    minutes = pd.date_range(t0, t0 + pd.Timedelta("9h40min"), freq="1min", inclusive="left")
    spec = {pd.Timestamp(f"2026-09-15 {hm}").tz_localize("America/New_York"): v for hm, *v in path}
    rows, last = [], (105.0, 105.2, 104.8, 105.0)
    for m in minutes:
        last = tuple(spec.get(m, (last[3], last[3] + 0.2, last[3] - 0.2, last[3])))
        rows.append(last)
    xau = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=minutes.tz_convert("UTC"))
    xau["spread"] = 30
    empty_gc = pd.DataFrame(columns=["open", "high", "low", "close", "volume", "buy", "sell", "delta",
                                     "instrument_id"], index=pd.DatetimeIndex([], tz="UTC"))
    empty_b = pd.DataFrame(columns=["basis", "basis_ref", "bruit", "contract"], index=pd.DatetimeIndex([], tz="UTC"))
    return DayData(day, xau, empty_gc, empty_b, None, "test")


def _v3_engine(cfg):
    cfg["news"]["missing_calendar"] = "ignore_backtest_only"
    return Engine(cfg, "V3", load_instrument(cfg), Account.from_config(cfg), Calendar.empty(), SimExecutor())


def test_v3_failed_ib_break_short_partial_then_target(cfg):
    path = [("08:20", 105, 110, 100, 105)]                                   # IB 100 - 110
    path += [(f"09:{m:02d}", 110.5, 112, 110.4, 111) for m in range(20, 25)]   # M5 09:20 clôture 111 : cassure
    path += [(f"09:{m:02d}", 110, 111.5, 108.8, 109) for m in range(25, 30)]   # M5 09:25 clôture 109 : échec
    path += [("09:30", 109, 109.1, 107, 107.5), ("09:35", 107, 107.2, 104.5, 105)]   # partiel au milieu (105)
    path += [("09:50", 101, 101.2, 99.5, 100)]                                 # objectif : bas de l'IB (100)
    eng = _v3_engine(cfg)
    eng.run_day(_v3_day(cfg, path))
    assert [r["type"] for r in eng.journal if r["type"].startswith("v3_")] == ["v3_cassure"]
    entry = next(r for r in eng.journal if r["type"] == "entree")
    assert entry["direction"] == -1 and entry["entree"] == pytest.approx(109 - 0.10)
    assert entry["sl"] > 112 and entry["tp"] == 100 and entry["partiel_niveau"] == 105
    tr = eng.trades[-1]
    assert tr.partial_done and tr.exit_reason == "TP (après partiel)"
    assert pd.Timestamp(entry["t"]) == pd.Timestamp("2026-09-15 09:30", tz="America/New_York")


def test_v3_accepted_break_gives_no_trade(cfg):
    path = [("08:20", 105, 110, 100, 105)]
    path += [(f"09:{m:02d}", 110.5, 112, 110.4, 111) for m in range(20, 60)]  # reste dehors 40 min
    eng = _v3_engine(cfg)
    eng.run_day(_v3_day(cfg, path))
    assert any(r["type"] == "v3_cassure_acceptee" for r in eng.journal)
    assert not eng.trades and not any(r["type"] == "entree" for r in eng.journal)


def test_v3_refuses_when_ib_middle_is_too_close(cfg):
    path = [("08:20", 105, 110, 100, 105)]
    path += [(f"09:{m:02d}", 110.5, 118, 110.4, 111) for m in range(20, 25)]   # extrême 118 : stop lointain
    path += [(f"09:{m:02d}", 110, 111.5, 108.8, 109) for m in range(25, 30)]
    eng = _v3_engine(cfg)
    eng.run_day(_v3_day(cfg, path))
    ref = next(r for r in eng.journal if r["type"] == "entree_refusee")
    assert any(m.startswith("milieu de l'IB") for m in ref["motifs"])
    assert not eng.trades


def test_v3_one_signal_per_side(cfg):
    path = [("08:20", 105, 110, 100, 105)]
    for start in (20, 40):                                                     # deux cassures ratées par le haut
        path += [(f"09:{m:02d}", 110.5, 112, 110.4, 111) for m in range(start, start + 5)]
        path += [(f"09:{m:02d}", 110, 111.5, 108.8, 109) for m in range(start + 5, start + 10)]
    eng = _v3_engine(cfg)
    eng.run_day(_v3_day(cfg, path))
    assert sum(r["type"] == "confirmation" for r in eng.journal) == 1
