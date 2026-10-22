import copy

import numpy as np
import pandas as pd
import pytest

from xauof.bridge import basis as bs


def synthetic(n=3000, basis=30.0, clock_shift_min=0, roll_at=None, seed=0):
    """GC = marche aléatoire ; XAUUSD = GC − basis + bruit, horloge éventuellement décalée."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-09-14 12:00", periods=n, freq="1min", tz="UTC")
    gc_close = 4200 + np.cumsum(rng.normal(0, 0.5, n))
    contract = np.full(n, 1)
    b = np.full(n, basis)
    if roll_at is not None:
        contract[roll_at:] = 2
        b[roll_at:] = basis + 20        # nouveau contrat plus éloigné → basis plus grand
    gc = pd.DataFrame({"close": gc_close, "instrument_id": contract}, index=idx)
    xau_close = gc_close - b + rng.normal(0, 0.05, n)
    xau = pd.DataFrame({"close": xau_close - 0.15, "spread": np.full(n, 30)},
                       index=idx + pd.Timedelta(minutes=clock_shift_min))
    return gc, xau


def test_align_uses_mid_price():
    gc, xau = synthetic(n=50)
    df = bs.align(gc, xau, point=0.01)
    # bid = close − 0.15, spread 30 points = 0,30 $ → milieu = close
    assert df["basis"].mean() == pytest.approx(30.0, abs=0.05)


def test_rolling_basis_resets_on_roll():
    gc, xau = synthetic(n=600, roll_at=300)
    df = bs.align(gc, xau, point=0.01)
    ref = bs.rolling_basis(df, "60min", min_obs=20)
    assert ref.iloc[290] == pytest.approx(30, abs=0.1)
    assert np.isnan(ref.iloc[305])                     # pas assez d'historique sur le nouveau contrat
    assert ref.iloc[400] == pytest.approx(50, abs=0.1)  # jamais de mélange avec l'ancien contrat
    r = bs.rolls(df)
    assert len(r) == 1 and r["saut"].iloc[0] == pytest.approx(20, abs=0.2)


def test_clock_offset_detects_aligned_clocks():
    gc, xau = synthetic()
    corr = bs.clock_offset(gc["close"], xau["close"])
    assert max(corr, key=corr.get) == 0 and corr[0] > 0.9


def test_clock_offset_detects_one_hour_error():
    # XAUUSD horodaté 1 h trop tard (mauvais fuseau) : il faut le reculer de 60 min
    gc, xau = synthetic(clock_shift_min=60)
    corr = bs.clock_offset(gc["close"], xau["close"])
    assert max(corr, key=corr.get) == -60


def test_weekly_clock_check():
    gc, xau = synthetic(n=6000)
    wk = bs.weekly_clock_check(gc["close"], xau["close"])
    assert (wk["decalage_min"] == 0).all()


def test_level_mapping_round_trip():
    assert bs.gc_to_xau(4230.0, 30.0) == 4200.0
    assert bs.xau_to_gc(bs.gc_to_xau(4230.0, 30.0), 30.0) == 4230.0


def test_bridge_check_end_to_end(tmp_path):
    from xauof.bridge import check
    from xauof.config import load_config
    from xauof.data import store

    cfg = copy.deepcopy(load_config())
    cfg["data"]["dir"] = str(tmp_path / "data")
    rng = np.random.default_rng(3)
    # transactions GC (1 par 10 s) et bougies XAUUSD M1 sur 2 jours
    idx = pd.date_range("2026-09-14", "2026-09-16", freq="10s", tz="UTC", inclusive="left", name="ts_event")
    price = np.round((4200 + np.cumsum(rng.normal(0, 0.1, len(idx)))) / 0.1) * 0.1
    trades = pd.DataFrame({"price": price, "size": np.ones(len(idx), "int32"),
                           "aggressor": rng.choice([-1, 1], len(idx)).astype("int8"),
                           "instrument_id": np.full(len(idx), 7, "uint32")}, index=idx)
    store.save(trades, store.month_file(store.gc_trades_dir(cfg), pd.Timestamp("2026-09-01", tz="UTC")))
    m1 = trades["price"].resample("1min").last()
    xau = pd.DataFrame({"open": m1 - 30, "high": m1 - 30, "low": m1 - 30, "close": m1 - 30.15,
                        "tick_volume": 1, "spread": 30}, index=m1.index.rename("time"))
    store.save(xau, store.month_file(store.xauusd_m1_dir(cfg), pd.Timestamp("2026-09-01", tz="UTC")))

    res = check.run(cfg, "2026-09-14", "2026-09-16", out_dir=tmp_path)
    assert res["df"]["basis"].median() == pytest.approx(30, abs=0.01)
    assert (res["clock"]["decalage_min"] == 0).all()
