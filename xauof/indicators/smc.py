"""FVG et Order Blocks (bullish / bearish) sur des bougies OHLC.

FVG haussier : le plus bas de la bougie i+1 est au-dessus du plus haut de la
bougie i-1 → trou [high(i-1), low(i+1)] laissé par la bougie impulsive i.

Order Block haussier : dernière bougie baissière avant une bougie de
déplacement (corps ≥ disp_atr × ATR) qui clôture au-dessus d'elle.
Zone = [low, high] de l'OB. Symétrique pour le baissier.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class Zone:
    kind: str                      # "fvg" | "ob"
    direction: str                 # "bull" | "bear"
    low: float
    high: float
    formed_at: pd.Timestamp        # bougie qui confirme la zone
    touched_at: Optional[pd.Timestamp] = None   # premier retour du prix dans la zone
    invalidated_at: Optional[pd.Timestamp] = None  # clôture de l'autre côté

    def contains(self, price: float) -> bool:
        return self.low <= price <= self.high


def atr(bars: pd.DataFrame, period: int = 14) -> pd.Series:
    prev = bars["close"].shift(1)
    tr = pd.concat([bars["high"] - bars["low"], (bars["high"] - prev).abs(), (bars["low"] - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def find_fvgs(bars: pd.DataFrame, min_size: float = 0.0) -> list[Zone]:
    h, l, idx = bars["high"].to_numpy(), bars["low"].to_numpy(), bars.index
    zones = []
    for i in range(1, len(bars) - 1):
        if l[i + 1] - h[i - 1] > min_size:
            zones.append(Zone("fvg", "bull", float(h[i - 1]), float(l[i + 1]), idx[i + 1]))
        elif l[i - 1] - h[i + 1] > min_size:
            zones.append(Zone("fvg", "bear", float(h[i + 1]), float(l[i - 1]), idx[i + 1]))
    return zones


def find_order_blocks(bars: pd.DataFrame, disp_atr: float = 1.5, atr_period: int = 14) -> list[Zone]:
    o, h, l, c = (bars[k].to_numpy() for k in ("open", "high", "low", "close"))
    a, idx = atr(bars, atr_period).to_numpy(), bars.index
    zones = []
    for i in range(1, len(bars)):
        body = c[i] - o[i]
        if abs(body) < disp_atr * a[i - 1]:
            continue
        want_down = body > 0               # haussier : on cherche la dernière bougie baissière
        j = i - 1
        while j >= 0 and not ((c[j] < o[j]) if want_down else (c[j] > o[j])):
            j -= 1
        if j < 0:
            continue
        if want_down and c[i] > h[j]:
            zones.append(Zone("ob", "bull", float(l[j]), float(h[j]), idx[i]))
        elif not want_down and c[i] < l[j]:
            zones.append(Zone("ob", "bear", float(l[j]), float(h[j]), idx[i]))
    return zones


def track(zones: list[Zone], bars: pd.DataFrame) -> list[Zone]:
    """Renseigne touched_at (premier retour en zone) et invalidated_at (clôture au-delà)."""
    for z in zones:
        after = bars[bars.index > z.formed_at]
        touched = after[(after["low"] <= z.high) & (after["high"] >= z.low)]
        z.touched_at = touched.index[0] if len(touched) else None
        broken = after["close"] < z.low if z.direction == "bull" else after["close"] > z.high
        z.invalidated_at = after.index[np.argmax(broken.to_numpy())] if broken.any() else None
    return zones
