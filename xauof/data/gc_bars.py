"""Bougies GC 1 minute (OHLC, volume, achats / ventes agressifs, contrat) construites
depuis les transactions et mises en cache par mois dans data/gc/m1/."""
from __future__ import annotations

import pandas as pd

from xauof.data import store
from xauof.indicators.orderflow import delta_bars


def m1_dir(cfg: dict):
    return store.data_dir(cfg) / "gc" / "m1"


def build_month(cfg: dict, month: pd.Timestamp) -> pd.DataFrame:
    src = store.month_file(store.gc_trades_dir(cfg), month)
    trades = pd.read_parquet(src)
    bars = delta_bars(trades, "1min")
    bars["instrument_id"] = trades["instrument_id"].resample("1min").last().reindex(bars.index)
    bars = bars.drop(columns="cvd")   # le CVD se recalcule sur la période voulue
    store.save(bars, store.month_file(m1_dir(cfg), month))
    return bars


def load_m1(cfg: dict, start, end) -> pd.DataFrame:
    """Bougies 1 min GC sur [start, end), (re)construites si le cache manque ou est périmé."""
    for c in store.month_chunks(start, end):
        src = store.month_file(store.gc_trades_dir(cfg), c.month)
        dst = store.month_file(m1_dir(cfg), c.month)
        if src.exists() and (not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime):
            print(f"  construction des bougies GC 1 min : {c.month:%Y-%m}")
            build_month(cfg, c.month)
    return store.load_range(m1_dir(cfg), start, end, bars=True)
