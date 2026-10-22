import numpy as np
import pandas as pd
import pytest

from xauof.indicators import orderflow, sessions, smc, tpo, volume_profile as vp


def trades(rows, start="2026-09-15 12:20", step="1s"):
    """rows : liste de (price, size, aggressor)."""
    idx = pd.date_range(start, periods=len(rows), freq=step, tz="UTC")
    df = pd.DataFrame(rows, columns=["price", "size", "aggressor"], index=idx)
    df["instrument_id"] = 1
    return df


def bars(ohlc, start="2026-09-15 12:20", freq="5min"):
    idx = pd.date_range(start, periods=len(ohlc), freq=freq, tz="UTC")
    return pd.DataFrame(ohlc, columns=["open", "high", "low", "close"], index=idx)


# --- sessions ---------------------------------------------------------------

def test_trading_day_starts_18h_new_york():
    idx = pd.DatetimeIndex(["2026-09-14 21:59", "2026-09-14 22:00"], tz="UTC")  # 17:59 / 18:00 NY (EDT)
    assert list(sessions.trading_day(idx)) == [pd.Timestamp("2026-09-14"), pd.Timestamp("2026-09-15")]


def test_rth_mask():
    idx = pd.DatetimeIndex(["2026-09-15 12:19", "2026-09-15 12:20", "2026-09-15 17:30"], tz="UTC")
    assert list(sessions.rth_mask(idx, "08:20", "13:30")) == [False, True, False]


def test_front_contract_keeps_most_traded():
    df = trades([(1, 5, 1), (2, 1, 1), (3, 1, 1)])
    df["instrument_id"] = [7, 8, 8]
    assert list(sessions.front_contract(df)["instrument_id"]) == [7]


# --- volume profile ---------------------------------------------------------

def test_value_area_and_poc():
    prof = pd.Series([1, 2, 10, 4, 1, 1, 1], index=[1.0, 2, 3, 4, 5, 6, 7], dtype=float)
    poc, val, vah = vp.value_area(prof, 0.70)
    assert poc == 3.0
    assert (val, vah) == (3.0, 4.0)       # 10+4 = 14/20 = 70 % atteint
    assert vp.value_area(prof, 0.75)[1:] == (2.0, 4.0)   # il faut un niveau de plus : 2 (2) avant 5 (1)


def test_build_profile_bins_prices():
    prof = vp.build_profile([2400.04, 2400.06, 2400.94], [1, 2, 3], bin_size=1.0)
    assert prof.to_dict() == {2400.0: 3.0, 2401.0: 3.0}


def test_nodes_find_lvn_between_two_hvn():
    v = [1, 5, 20, 5, 1, 0, 1, 5, 20, 5, 1]
    hvn, lvn = vp.nodes(pd.Series(v, index=np.arange(len(v), dtype=float)), smooth=1)
    assert hvn == [2.0, 8.0] and lvn == [5.0]


# --- TPO --------------------------------------------------------------------

def test_tpo_profile_counts_and_singles():
    # A : 100-102, B : 101-102, C : 106-106 (saut → 103-105 jamais touchés)... on relie par D : 102-106
    rows = []
    for p in (100, 102):
        rows.append((p, 1, 0))
    df_a = trades(rows, start="2026-09-15 12:20", step="10min")
    df_b = trades([(101, 1, 0), (102, 1, 0)], start="2026-09-15 12:50", step="10min")
    df_c = trades([(102, 1, 0), (106, 1, 0)], start="2026-09-15 13:20", step="10min")
    df_d = trades([(101, 1, 0), (102, 1, 0)], start="2026-09-15 13:50", step="10min")
    prof = tpo.tpo_profile(pd.concat([df_a, df_b, df_c, df_d]), bin_size=1.0, period_min=30, ib_min=60)
    assert prof.counts.to_dict() == {100: 1, 101: 3, 102: 4, 103: 1, 104: 1, 105: 1, 106: 1}
    assert prof.poc == 102.0
    assert prof.letters[102.0] == "ABCD"
    assert prof.tails == [(100.0, 100.0), (103.0, 106.0)]   # 103-106 : C seul, jusqu'au plus haut
    assert (prof.ib_low, prof.ib_high) == (100.0, 102.0)
    assert not prof.poor_high and not prof.poor_low


def test_tpo_single_prints_in_the_middle():
    a = trades([(100, 1, 0), (101, 1, 0)], start="2026-09-15 12:20", step="10min")
    b = trades([(100, 1, 0), (105, 1, 0)], start="2026-09-15 12:50", step="10min")
    c = trades([(104, 1, 0), (105, 1, 0)], start="2026-09-15 13:20", step="10min")
    prof = tpo.tpo_profile(pd.concat([a, b, c]), bin_size=1.0)
    assert prof.single_prints == [(102.0, 103.0)]
    assert prof.poor_high and prof.poor_low     # 2 périodes sur chaque extrême


# --- order flow -------------------------------------------------------------

def test_delta_bars_and_cvd():
    df = trades([(100, 5, 1), (101, 2, -1), (100, 1, 0), (99, 4, -1)], step="3min")
    b = orderflow.delta_bars(df, "5min")
    assert list(b["buy"]) == [5, 0] and list(b["sell"]) == [2, 4]
    assert list(b["delta"]) == [3, -4] and list(b["cvd"]) == [3, -1]
    assert list(b["volume"]) == [7, 5]


def test_footprint_diagonal_imbalance():
    # niveau 101 : 30 achats agressifs ; niveau 100 : 5 ventes → 30 ≥ 3 × 5
    df = trades([(101, 30, 1), (100, 5, -1), (100, 2, 1)])
    fp = orderflow.footprint(df, "5min", bin_size=1.0, ratio=3, min_volume=10)
    bar = fp.index.get_level_values("bar")[0]
    assert bool(fp.loc[(bar, 101.0), "buy_imb"])
    assert not bool(fp.loc[(bar, 100.0), "buy_imb"])
    assert fp.loc[(bar, 100.0), "delta"] == -3


def test_absorption_flags_selling_into_support():
    n = 25
    rows = [(100, 101, 99, 100.5)] * n + [(100, 100.4, 99.6, 100.1)]
    b = bars(rows)
    b["volume"] = [100] * n + [400]
    b["delta"] = [0] * n + [-200]          # vente agressive massive, le prix ne baisse pas
    sig = orderflow.absorption(b)
    assert sig.iloc[-1] == 1 and (sig.iloc[:-1] == 0).all()


def test_cvd_divergence_on_new_high():
    b = bars([(1, 1, 1, 1)] * 12 + [(1, 2, 1, 2)])
    b["cvd"] = [10] * 12 + [5]
    assert orderflow.cvd_divergence(b, lookback=12).iloc[-1] == -1


def test_large_prints():
    df = trades([(100, 1, 1)] * 99 + [(100, 50, 1)])
    assert list(orderflow.large_prints(df, min_size=20)["size"]) == [50]


# --- FVG / OB ---------------------------------------------------------------

def test_bull_and_bear_fvg():
    up = smc.find_fvgs(bars([(10, 11, 9, 10.5), (10.5, 14, 10.4, 13.8), (13.8, 15, 12, 14.5)]))
    assert [(z.direction, z.low, z.high) for z in up] == [("bull", 11.0, 12.0)]
    down = smc.find_fvgs(bars([(14, 15, 13, 13.5), (13.5, 13.6, 9, 9.2), (9.2, 11, 8, 8.5)]))
    assert [(z.direction, z.low, z.high) for z in down] == [("bear", 11.0, 13.0)]


def test_bull_order_block_and_tracking():
    rows = [(10, 10.5, 9.5, 10)] * 14 + [
        (10.2, 10.3, 9.6, 9.7),   # dernière bougie baissière = OB [9.6, 10.3]
        (9.7, 13.0, 9.7, 12.9),   # déplacement haussier, clôture > 10.3
        (12.9, 13.1, 12.0, 12.5),
        (12.5, 12.6, 10.0, 10.4), # retour dans la zone
        (10.4, 10.5, 9.0, 9.2),   # clôture sous la zone → invalidé
    ]
    b = bars(rows)
    obs = [z for z in smc.find_order_blocks(b, disp_atr=1.5) if z.direction == "bull"]
    assert len(obs) == 1 and (obs[0].low, obs[0].high) == (9.6, 10.3)
    smc.track(obs, b)
    assert obs[0].touched_at == b.index[17] and obs[0].invalidated_at == b.index[18]


# --- contrôle visuel de bout en bout -----------------------------------------

def test_check_runs_end_to_end(tmp_path):
    import copy

    from xauof.config import load_config
    from xauof.data import store
    from xauof.indicators import check

    cfg = copy.deepcopy(load_config())
    cfg["data"]["dir"] = str(tmp_path / "data")
    start, end = check.day_bounds("2026-09-15")
    rng = np.random.default_rng(1)
    n = 20_000
    idx = pd.DatetimeIndex(np.sort(rng.uniform(start.value, end.value, n)).astype("int64"), tz="UTC", name="ts_event")
    price = np.round((2650 + np.cumsum(rng.normal(0, 0.1, n))) / 0.1) * 0.1
    df = pd.DataFrame({"price": price, "size": rng.integers(1, 5, n).astype("int32"),
                       "aggressor": rng.choice([-1, 1], n).astype("int8"),
                       "instrument_id": np.full(n, 42, "uint32")}, index=idx)
    store.save(df, store.month_file(store.gc_trades_dir(cfg), pd.Timestamp("2026-09-01", tz="UTC")))

    path = check.run(cfg, "2026-09-15", out_dir=tmp_path)
    assert path.exists() and path.stat().st_size > 10_000
