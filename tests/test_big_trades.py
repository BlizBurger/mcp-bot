"""Bulles GC : regroupement des ordres agressifs, seuil sans anticipation, mesures."""
import numpy as np
import pandas as pd

from xauof.indicators import big_trades as bt
from xauof.strategy import big_trades_study as st

T = pd.Timestamp("2026-10-22 13:30", tz="UTC")


def trades(rows):
    """rows : (secondes, prix, taille, côté)."""
    idx = pd.DatetimeIndex([T + pd.Timedelta(seconds=s) for s, *_ in rows], name="ts_event")
    return pd.DataFrame({"price": [r[1] for r in rows], "size": [r[2] for r in rows],
                         "aggressor": [r[3] for r in rows], "instrument_id": 1}, index=idx)


def test_sweep_at_same_timestamp_is_one_order():
    o = bt.aggressive_orders(trades([(0, 100.0, 5, 1), (0, 100.1, 30, 1), (0, 100.2, 15, 1),
                                     (0, 99.9, 2, -1), (1, 100.0, 1, 0)]))
    buy = o[o["side"] == 1].iloc[0]
    assert len(o) == 2 and buy["size"] == 50 and buy["price"] == 100.2 and (buy["lo"], buy["hi"]) == (100.0, 100.2)


def test_threshold_uses_previous_session_with_floor():
    prev = pd.DataFrame({"size": [1] * 990 + [40] * 10})
    assert bt.threshold(prev) == 40.0
    assert bt.threshold(pd.DataFrame({"size": [1, 2, 3]})) == 10.0          # plancher
    assert bt.threshold(pd.DataFrame(columns=["size"])) == 10.0


def test_follow_through_is_signed_by_aggressor():
    ev = pd.DataFrame({"side": [1, -1], "price": [100.0, 100.0]}, index=pd.DatetimeIndex([T, T]))
    t_ns = np.array([T.value + k * 60_000_000_000 for k in range(0, 31)])
    last = 100.0 + 0.1 * np.arange(31)                                     # le prix monte de 0,10 $/min
    out = st.follow_through(ev, t_ns, last, int(t_ns[-1]))
    assert np.allclose(out[5], [0.5, -0.5]) and np.allclose(out[30], [3.0, -3.0])


def test_level_reaction_requires_moving_away_then_revisit():
    ev = pd.DataFrame({"side": [1], "price": [100.0]}, index=pd.DatetimeIndex([T]))
    secs = np.arange(0, 1200)
    t_ns = T.value + secs * 1_000_000_000
    price = np.full(len(secs), 100.0)
    price[10:400] = 101.5          # s'éloigne de 1,5 $
    price[400:410] = 100.0         # revient sur le niveau après 5 min
    price[410:] = 102.5            # rebondit de 2,5 $ dans le sens de la bulle acheteuse
    out = st.level_reactions(ev, t_ns, price, price)
    assert out[1.0] == [1] and out[2.0] == [1]
    price[410:] = 97.5             # casse au contraire
    out = st.level_reactions(ev, t_ns, price, price)
    assert out[1.0] == [0] and out[2.0] == [0]


def test_session_study_runs_and_reports():
    rng = np.random.default_rng(0)
    n = 4000
    secs = np.sort(rng.integers(0, 18000, n))
    price = 100 + np.cumsum(rng.normal(0, 0.05, n))
    size = np.where(rng.random(n) < 0.01, 60, rng.integers(1, 4, n))
    side = rng.choice([-1, 1], n)
    tr = trades(list(zip(secs.tolist(), price.tolist(), size.tolist(), side.tolist())))
    prev = bt.aggressive_orders(tr)
    res, orders, thr = st.session_study(tr, prev, seed=1)
    assert res["bulles"]["n"] > 0 and res["temoin"]["n"] == res["bulles"]["n"] and thr >= 10
    acc = {}
    st.merge(acc, res)
    assert "SUITE IMMÉDIATE" in st.report(acc, 1, [thr])
