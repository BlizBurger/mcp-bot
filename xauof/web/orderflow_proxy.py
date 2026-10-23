"""Order flow ESTIMÉ à partir des ticks XAUUSD du broker (gratuit, sans vrai volume).

Règle du tick sur le milieu bid/ask : +1 quand le prix monte, −1 quand il baisse ; un
tick sans changement reprend le signe précédent. Ce n'est PAS le vrai delta
acheteurs / vendeurs (il faudrait le flux GC du CME, ~199 $/mois) : c'est une
approximation de la pression, à lire comme telle.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def tick_signs(mid: np.ndarray) -> np.ndarray:
    d = np.sign(np.diff(mid, prepend=mid[0] if len(mid) else 0.0))
    s = pd.Series(d).replace(0, np.nan).ffill().fillna(0)
    return s.to_numpy()


def proxy_bars(ticks: pd.DataFrame, freq: str = "1min", cvd_from: pd.Timestamp | None = None) -> pd.DataFrame:
    """ticks : index UTC, colonnes bid, ask. Retourne par minute : ticks, delta, cvd (estimés)."""
    if ticks.empty:
        return pd.DataFrame(columns=["ticks", "delta", "cvd"])
    mid = ((ticks["bid"] + ticks["ask"]) / 2).to_numpy()
    sign = pd.Series(tick_signs(mid), index=ticks.index)
    g = sign.resample(freq)
    out = pd.DataFrame({"ticks": g.count(), "delta": g.sum()})
    out = out[out["ticks"] > 0]
    start = out.index >= cvd_from if cvd_from is not None else np.ones(len(out), bool)
    out["cvd"] = out["delta"].where(start, 0).cumsum().where(start)
    return out
