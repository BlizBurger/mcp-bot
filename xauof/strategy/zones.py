"""Zones FVG / Order Block sur XAUUSD M5, détectées bougie par bougie (aucune donnée future).

Cycle de vie (machine à états, historique conservé dans Zone.history) :
  detected → eligible → touched → confirming → ordered → position → closed
  avec sorties possibles : rejected (taille), duplicate, consumed (contact sans
  trade), invalidated (clôture M1 au-delà du bord distal), expired (fin de fenêtre).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from xauof.indicators.smc import atr as smc_atr

FINAL = {"rejected", "duplicate", "consumed", "invalidated", "expired", "closed"}
LIVE = {"eligible"}                       # en attente d'un premier contact


@dataclass
class Zone:
    id: str
    kind: str                     # "FVG" | "OB"
    direction: int                # +1 haussière (achat), -1 baissière (vente)
    low: float
    high: float
    active_from: pd.Timestamp     # clôture de la bougie qui confirme la zone
    meta: dict = field(default_factory=dict)
    state: str = "detected"
    history: list = field(default_factory=list)
    contact_at: Optional[pd.Timestamp] = None

    def set(self, t: pd.Timestamp, state: str, reason: str = "") -> None:
        self.state = state
        self.history.append((t, state, reason))

    @property
    def distal(self) -> float:
        """Bord opposé à l'arrivée du prix : sous la zone pour un achat, au-dessus pour une vente."""
        return self.low if self.direction > 0 else self.high

    def overlaps(self, other: "Zone") -> bool:
        return self.low <= other.high and other.low <= self.high

    def distance(self, lo: float, hi: Optional[float] = None) -> float:
        """Distance entre la zone et un niveau (ou un intervalle) ; 0 s'ils se recouvrent."""
        hi = lo if hi is None else hi
        if hi < self.low:
            return self.low - hi
        if lo > self.high:
            return lo - self.high
        return 0.0


@dataclass
class Pivot:
    index: int
    price: float
    kind: str                     # "high" | "low"
    confirmed_index: int
    broken: bool = False


class ZoneDetector:
    """Appelé à chaque clôture M5 avec toutes les bougies M5 clôturées jusque-là."""

    def __init__(self, cfg: dict, session_start: pd.Timestamp, day_tag: str):
        s = cfg["strategy"]
        self.s = s
        self.session_start = session_start
        self.day_tag = day_tag
        self.pivots: list[Pivot] = []
        self.counter = 0
        self.tf = pd.Timedelta(s["zone_tf"])

    def _new_id(self, kind: str, direction: int) -> str:
        self.counter += 1
        return f"{self.day_tag}-{kind}-{'bull' if direction > 0 else 'bear'}-{self.counter:03d}"

    def _confirm_pivots(self, m5: pd.DataFrame) -> None:
        L, R = self.s["pivot_left"], self.s["pivot_right"]
        i = len(m5) - 1
        j = i - R                                   # pivot candidat confirmé par cette clôture
        if j - L < 0:
            return
        h, lo = m5["high"].to_numpy(), m5["low"].to_numpy()
        if h[j] > h[j - L:j].max() and h[j] > h[j + 1:j + R + 1].max():
            self.pivots.append(Pivot(j, float(h[j]), "high", i))
        if lo[j] < lo[j - L:j].min() and lo[j] < lo[j + 1:j + R + 1].min():
            self.pivots.append(Pivot(j, float(lo[j]), "low", i))

    def on_m5_close(self, m5: pd.DataFrame, spread_price: float) -> list[Zone]:
        """m5 : bougies M5 clôturées (index = début de bougie). Retourne les zones nouvelles."""
        i = len(m5) - 1
        close_t = m5.index[i] + self.tf
        a = smc_atr(m5, self.s["atr_period"]).to_numpy(copy=True)
        a[: self.s["atr_period"] - 1] = np.nan        # ATR complet uniquement
        out: list[Zone] = []
        in_session = m5.index >= self.session_start
        o, h, lo, c = (m5[k].to_numpy() for k in ("open", "high", "low", "close"))

        # --- FVG : bougies i-2, i-1, i toutes clôturées et dans la séance ---
        if i >= 2 and in_session[i - 2] and not np.isnan(a[i]):
            min_size = max(self.s["fvg_min_spreads"] * spread_price, self.s["fvg_min_atr"] * a[i])
            max_size = self.s["fvg_max_atr"] * a[i]
            for direction, zlo, zhi in ((1, h[i - 2], lo[i]), (-1, h[i], lo[i - 2])):
                if zhi > zlo:
                    z = Zone(self._new_id("FVG", direction), "FVG", direction, float(zlo), float(zhi), close_t,
                             meta={"atr": float(a[i]), "size": float(zhi - zlo),
                                   "min_size": float(min_size), "max_size": float(max_size)})
                    z.set(close_t, "detected")
                    if not (min_size <= z.meta["size"] <= max_size):
                        z.set(close_t, "rejected", f"taille {z.meta['size']:.2f} hors [{min_size:.2f}, {max_size:.2f}]")
                    out.append(z)

        # --- Order Block : clôture qui casse un pivot confirmé AVANT cette bougie ---
        if i >= 1 and in_session[i] and not np.isnan(a[i - 1]):
            body = c[i] - o[i]
            for direction, kind in ((1, "high"), (-1, "low")):
                broken = [p for p in self.pivots if p.kind == kind and not p.broken and p.confirmed_index < i
                          and (c[i] > p.price if direction > 0 else c[i] < p.price)]
                if not broken:
                    continue
                for p in broken:                     # un pivot ne sert qu'une fois
                    p.broken = True
                if direction * body < self.s["ob_body_atr"] * a[i - 1]:
                    continue                         # cassure sans impulsion suffisante
                for j in range(i - 1, max(i - 1 - self.s["ob_lookback"], -1), -1):
                    opposite = (c[j] < o[j]) if direction > 0 else (c[j] > o[j])
                    if opposite:
                        if not in_session[j]:
                            break
                        z = Zone(self._new_id("OB", direction), "OB", direction, float(lo[j]), float(h[j]), close_t,
                                 meta={"atr_before": float(a[i - 1]), "body": float(abs(body)),
                                       "pivot": max(broken, key=lambda p: p.index).price,
                                       "ob_bar": str(m5.index[j]), "break_bar": str(m5.index[i])})
                        z.set(close_t, "detected")
                        out.append(z)
                        break
        self._confirm_pivots(m5)
        return out


class ZoneBook:
    """Zones de la séance : déduplication, premier contact, invalidation, expiration."""

    def __init__(self):
        self.zones: list[Zone] = []

    def add(self, zone: Zone) -> None:
        if zone.state == "detected":
            dup = next((z for z in self.zones if z.direction == zone.direction and z.state != "rejected"
                        and z.overlaps(zone)), None)
            if dup:
                zone.set(zone.active_from, "duplicate", f"chevauche {dup.id} (même idée)")
            else:
                zone.set(zone.active_from, "eligible")
        self.zones.append(zone)

    def live(self, t: pd.Timestamp) -> list[Zone]:
        return [z for z in self.zones if z.state in LIVE and z.active_from <= t]

    def contacts(self, bar_start: pd.Timestamp, bid_low: float, ask_high: float) -> list[Zone]:
        """Zones touchées pour la première fois par cette bougie M1 (achat : bid descend dans
        la zone ; vente : ask monte dans la zone). Le contact consomme la zone."""
        hit = []
        for z in self.live(bar_start):
            touched = bid_low <= z.high if z.direction > 0 else ask_high >= z.low
            if touched:
                z.contact_at = bar_start
                z.set(bar_start, "touched")
                hit.append(z)
        return hit

    def invalidate(self, t: pd.Timestamp, bid_close: float) -> list[Zone]:
        """Clôture M1 au-delà du bord distal : zone invalidée (même en attente de confirmation)."""
        out = []
        for z in self.zones:
            if z.state in LIVE | {"touched", "confirming"}:
                beyond = bid_close < z.low if z.direction > 0 else bid_close > z.high
                if beyond:
                    z.set(t, "invalidated", f"clôture M1 {bid_close:.2f} au-delà du bord distal {z.distal:.2f}")
                    out.append(z)
        return out

    def expire(self, t: pd.Timestamp, reason: str) -> None:
        for z in self.zones:
            if z.state not in FINAL and z.state not in {"position", "ordered"}:
                z.set(t, "expired", reason)
