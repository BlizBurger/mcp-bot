"""Découpage du temps CME : journée de trading, séance régulière (RTH), contrat actif."""
from __future__ import annotations

import pandas as pd

NY = "America/New_York"


def trading_day(index: pd.DatetimeIndex) -> pd.Index:
    """Journée CME : commence à 18:00 heure de New York la veille, finit à 17:00."""
    return (index.tz_convert(NY) + pd.Timedelta(hours=6)).normalize().tz_localize(None)


def rth_mask(index: pd.DatetimeIndex, start: str = "08:20", end: str = "13:30"):
    t = index.tz_convert(NY).time
    s, e = pd.Timestamp(start).time(), pd.Timestamp(end).time()
    return (t >= s) & (t < e)


def front_contract(trades: pd.DataFrame) -> pd.DataFrame:
    """Garde le contrat le plus échangé : évite de mélanger deux échéances le jour du roulement."""
    if trades.empty:
        return trades
    main = trades.groupby("instrument_id")["size"].sum().idxmax()
    return trades[trades["instrument_id"] == main]


def day_trades(trades: pd.DataFrame, day, rth: tuple[str, str] | None = None) -> pd.DataFrame:
    """Transactions d'une journée CME (contrat actif), optionnellement limitées à la RTH."""
    sel = trades[trading_day(trades.index) == pd.Timestamp(day)]
    if rth:
        sel = sel[rth_mask(sel.index, *rth)]
    return front_contract(sel)
