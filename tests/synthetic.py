"""Marché synthétique cohérent (GC transactions + bougies 1 min, XAUUSD M1) pour tester le moteur."""
from __future__ import annotations

import numpy as np
import pandas as pd

from xauof.data import store
from xauof.data.gc_bars import build_month


def make_market(cfg: dict, days: list[str], basis: float = 30.0, spread_pts: int = 30, seed: int = 0,
                trades_per_min: int = 20, vol: float = 0.35, kappa: float = 0.0, mu: float = 4200.0) -> None:
    """Écrit dans cfg['data']['dir'] des transactions GC, les bougies GC 1 min et XAUUSD M1."""
    rng = np.random.default_rng(seed)
    all_trades, xau_rows = [], []
    price = 4200.0
    for day in days:
        d = pd.Timestamp(day)
        start = (d - pd.Timedelta(days=1) + pd.Timedelta(hours=18)).tz_localize("America/New_York").tz_convert("UTC")
        end = (d + pd.Timedelta(hours=17)).tz_localize("America/New_York").tz_convert("UTC")
        minutes = pd.date_range(start, end, freq="1min", inclusive="left")
        shocks = rng.normal(0, vol, len(minutes))
        # quelques impulsions pour créer des FVG / OB
        for k in rng.choice(len(minutes), 25, replace=False):
            shocks[k:k + 3] += rng.choice([-1, 1]) * rng.uniform(1.5, 3.0)
        path = np.empty(len(minutes))
        for k, e in enumerate(shocks):              # retour à la moyenne (kappa = 0 : marche aléatoire)
            price = price + kappa * (mu - price) + e
            path[k] = price
        n = trades_per_min
        ts = (minutes.repeat(n) + pd.to_timedelta(np.tile(np.linspace(0, 59, n), len(minutes)), unit="s"))
        px = np.round((np.repeat(path, n) + rng.normal(0, 0.15, len(ts))) / 0.1) * 0.1
        aggr = np.sign(np.r_[0, np.diff(px)] + rng.normal(0, 0.02, len(ts))).astype("int8")
        all_trades.append(pd.DataFrame({"price": px, "size": rng.integers(1, 6, len(ts)).astype("int32"),
                                        "aggressor": aggr, "instrument_id": np.full(len(ts), 7, "uint32")},
                                       index=pd.DatetimeIndex(ts, name="ts_event")))
        g = pd.Series(px, index=ts).resample("1min").ohlc()
        x = g - basis
        x["spread"] = spread_pts
        x["tick_volume"] = n
        xau_rows.append(x)
    trades = pd.concat(all_trades).sort_index()
    xau = pd.concat(xau_rows).sort_index()
    xau.index.name = "time"
    for m, grp in trades.groupby(trades.index.tz_localize(None).to_period("M")):
        month = pd.Timestamp(m.start_time, tz="UTC")
        store.save(grp, store.month_file(store.gc_trades_dir(cfg), month))
        build_month(cfg, month)
    for m, grp in xau.groupby(xau.index.tz_localize(None).to_period("M")):
        store.save(grp, store.month_file(store.xauusd_m1_dir(cfg), pd.Timestamp(m.start_time, tz="UTC")))
