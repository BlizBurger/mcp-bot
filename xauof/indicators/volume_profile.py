"""Volume profile : où le volume s'est échangé (prix accepté vs rejeté).

POC = niveau le plus échangé ; Value Area = zone contenant `va_pct` du volume
autour du POC ; HVN / LVN = pics / creux du profil.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


def to_bins(price, bin_size: float) -> np.ndarray:
    """Prix → niveau arrondi au multiple de bin_size (calcul en entiers, sans dérive float)."""
    return np.rint(np.asarray(price, dtype="float64") / bin_size).astype("int64")


def build_profile(prices, weights, bin_size: float) -> pd.Series:
    """Somme des poids par niveau de prix, triée du plus bas au plus haut (index = prix)."""
    s = pd.Series(np.asarray(weights, dtype="float64")).groupby(to_bins(prices, bin_size)).sum()
    s.index = np.round(s.index.to_numpy() * bin_size, 10)
    return s.sort_index()


def value_area(profile: pd.Series, va_pct: float = 0.70) -> tuple[float, float, float]:
    """(POC, VAL, VAH). On part du POC et on ajoute à chaque pas le niveau voisin
    (au-dessus ou en dessous) le plus fourni, jusqu'à couvrir va_pct du total."""
    v = profile.to_numpy()
    prices = profile.index.to_numpy()
    poc = int(np.argmax(v))
    lo = hi = poc
    acc, target = v[poc], va_pct * v.sum()
    while acc < target and (lo > 0 or hi < len(v) - 1):
        up = v[hi + 1] if hi < len(v) - 1 else -1.0
        down = v[lo - 1] if lo > 0 else -1.0
        if up >= down:
            hi += 1
            acc += up
        else:
            lo -= 1
            acc += down
    return float(prices[poc]), float(prices[lo]), float(prices[hi])


def _smooth(v: np.ndarray, window: int) -> np.ndarray:
    if window <= 1:
        return v
    return pd.Series(v).rolling(window, center=True, min_periods=1).mean().to_numpy()


def nodes(profile: pd.Series, smooth: int = 5, lvn_ratio: float = 0.5) -> tuple[list[float], list[float]]:
    """(HVN, LVN). HVN = pic local du profil lissé au-dessus de la médiane ;
    LVN = creux entre deux HVN, inférieur à lvn_ratio × le plus petit des deux pics."""
    v = _smooth(profile.to_numpy(), smooth)
    prices = profile.index.to_numpy()
    med = np.median(v)
    peaks = [i for i in range(1, len(v) - 1) if v[i] >= v[i - 1] and v[i] > v[i + 1] and v[i] > med]
    lvn = []
    for a, b in zip(peaks, peaks[1:]):
        j = a + int(np.argmin(v[a:b + 1]))
        if v[j] < lvn_ratio * min(v[a], v[b]):
            lvn.append(float(prices[j]))
    return [float(prices[i]) for i in peaks], lvn


def hvn_zones(profile: pd.Series, smooth: int = 5, frac: float = 0.7) -> list[tuple[float, float]]:
    """HVN sous forme de zones (bas, haut) : niveaux contigus autour de chaque pic
    du profil lissé dont le volume lissé reste >= frac × le pic."""
    v = _smooth(profile.to_numpy(), smooth)
    prices = profile.index.to_numpy()
    med = np.median(v)
    zones = []
    for i in range(1, len(v) - 1):
        if not (v[i] >= v[i - 1] and v[i] > v[i + 1] and v[i] > med):
            continue
        lo = hi = i
        while lo > 0 and v[lo - 1] >= frac * v[i]:
            lo -= 1
        while hi < len(v) - 1 and v[hi + 1] >= frac * v[i]:
            hi += 1
        zones.append((float(prices[lo]), float(prices[hi])))
    merged: list[tuple[float, float]] = []
    for z in sorted(zones):
        if merged and z[0] <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], z[1]))
        else:
            merged.append(z)
    return merged


@dataclass
class Profile:
    profile: pd.Series
    poc: float
    val: float
    vah: float
    hvn: list[float] = field(default_factory=list)
    lvn: list[float] = field(default_factory=list)


def volume_profile(trades: pd.DataFrame, bin_size: float, va_pct: float = 0.70,
                   smooth: int = 5, lvn_ratio: float = 0.5) -> Profile:
    prof = build_profile(trades["price"], trades["size"], bin_size)
    poc, val, vah = value_area(prof, va_pct)
    hvn, lvn = nodes(prof, smooth, lvn_ratio)
    return Profile(prof, poc, val, vah, hvn, lvn)
