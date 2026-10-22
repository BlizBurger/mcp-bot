"""TPO / Market Profile : où le marché a passé du temps.

Chaque période (30 min par défaut) « marque » tous les niveaux de prix qu'elle a
touchés entre son plus bas et son plus haut. Le nombre de périodes par niveau
donne le profil de temps.
"""
from __future__ import annotations

import string
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from xauof.indicators.volume_profile import to_bins, value_area

LETTERS = string.ascii_uppercase + string.ascii_lowercase


@dataclass
class TPOProfile:
    counts: pd.Series                 # nb de périodes par niveau (index = prix)
    letters: pd.Series                # lettres des périodes par niveau, ex. "ABD"
    poc: float
    val: float
    vah: float
    ib_low: float
    ib_high: float
    single_prints: list[tuple[float, float]] = field(default_factory=list)  # (bas, haut) au milieu du profil
    tails: list[tuple[float, float]] = field(default_factory=list)          # single prints aux extrêmes
    poor_high: bool = False
    poor_low: bool = False


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Plages contiguës de True : [(début, fin incluse), ...]."""
    runs, start = [], None
    for i, m in enumerate(mask):
        if m and start is None:
            start = i
        if not m and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(mask) - 1))
    return runs


def tpo_profile(trades: pd.DataFrame, bin_size: float, period_min: int = 30,
                ib_min: int = 60, va_pct: float = 0.70) -> TPOProfile:
    """Profil TPO d'une séance (transactions déjà limitées à la séance voulue)."""
    periods = trades["price"].resample(f"{period_min}min", origin="start").agg(["min", "max"]).dropna()
    lo_all, hi_all = to_bins(periods["min"].min(), bin_size), to_bins(periods["max"].max(), bin_size)
    levels = np.arange(lo_all, hi_all + 1)
    counts = np.zeros(len(levels), dtype=int)
    letters = [""] * len(levels)
    for k, (_, row) in enumerate(periods.iterrows()):
        a, b = to_bins(row["min"], bin_size) - lo_all, to_bins(row["max"], bin_size) - lo_all
        counts[a:b + 1] += 1
        for i in range(a, b + 1):
            letters[i] += LETTERS[k % len(LETTERS)]
    prices = np.round(levels * bin_size, 10)
    counts_s = pd.Series(counts, index=prices)
    poc, val, vah = value_area(counts_s.astype(float), va_pct)

    ib = trades[trades.index < trades.index[0] + pd.Timedelta(minutes=ib_min)]["price"]

    singles, tails = [], []
    for a, b in _runs(counts == 1):
        rng = (float(prices[a]), float(prices[b]))
        (tails if a == 0 or b == len(counts) - 1 else singles).append(rng)

    return TPOProfile(
        counts=counts_s, letters=pd.Series(letters, index=prices),
        poc=poc, val=val, vah=vah,
        ib_low=float(ib.min()), ib_high=float(ib.max()),
        single_prints=singles, tails=tails,
        # extrême « mal fini » : plusieurs périodes butent sur le même niveau, sans queue
        poor_high=bool(counts[-1] >= 2), poor_low=bool(counts[0] >= 2),
    )
