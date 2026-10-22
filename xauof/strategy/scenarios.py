"""Les deux scénarios, évalués à chaque clôture M5 XAUUSD (bid) dans la séance.

A — retour vers la valeur : une clôture M5 hors de la Value Area précédente, puis,
    dans les 3 bougies suivantes, une clôture de retour à l'intérieur.
    Retour sous la VAH → ventes uniquement ; au-dessus de la VAL → achats uniquement.
B — cassure acceptée (approximation opérationnelle, pas une preuve d'acceptation) :
    deux clôtures M5 consécutives au-delà de la même borne, avec un volume GC cumulé
    >= médiane des volumes cumulés des 20 paires précédentes. Désactivée dès qu'une
    clôture M5 revient dans la Value Area.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class Setup:
    name: str                     # "value_return" | "breakout"
    direction: int
    since: pd.Timestamp
    until: Optional[pd.Timestamp]  # None = jusqu'à désactivation
    bound: float                  # borne de VA concernée
    info: str = ""


class ScenarioTracker:
    def __init__(self, cfg: dict, val: float, vah: float, session_start: pd.Timestamp):
        s = cfg["strategy"]
        self.val, self.vah = val, vah
        self.session_start = session_start
        self.reentry = s["value_return"]["reentry_within_m5"]
        self.valid = s["value_return"]["setup_valid_m5"]
        self.pairs = s["breakout"]["volume_pairs"]
        self.tf = pd.Timedelta(s["zone_tf"])
        self.value_return: Optional[Setup] = None
        self.breakout: Optional[Setup] = None
        self.log: list[tuple[pd.Timestamp, str]] = []

    def _side(self, close: float) -> int:
        return 1 if close > self.vah else (-1 if close < self.val else 0)

    def on_m5_close(self, m5: pd.DataFrame, gc_vol_m5: pd.Series) -> None:
        """m5 : bougies XAUUSD clôturées ; gc_vol_m5 : volume GC par bougie M5 (mêmes débuts)."""
        i = len(m5) - 1
        t_close = m5.index[i] + self.tf
        if m5.index[i] < self.session_start:
            return
        closes = m5["close"].to_numpy()
        side_now = self._side(closes[i])
        sess = m5.index >= self.session_start

        # A — retour vers la valeur : première clôture revenue à l'intérieur, la sortie
        # (série de clôtures du même côté) ayant commencé au plus `reentry` bougies avant.
        if side_now == 0 and i >= 1 and sess[i - 1] and self._side(closes[i - 1]) != 0:
            out_side = self._side(closes[i - 1])
            k0 = i - 1
            while k0 - 1 >= 0 and sess[k0 - 1] and self._side(closes[k0 - 1]) == out_side:
                k0 -= 1
            if i - k0 <= self.reentry:
                direction = -out_side                             # retour sous VAH → vente
                bound = self.vah if direction < 0 else self.val
                self.value_return = Setup("value_return", direction, t_close,
                                          t_close + self.valid * self.tf, bound,
                                          f"sortie à {m5.index[k0]:%H:%M}, retour à {m5.index[i]:%H:%M}")
                self.log.append((t_close, f"retour vers la valeur actif ({'vente' if direction < 0 else 'achat'})"))

        # B — cassure acceptée
        if self.breakout and side_now != self.breakout.direction:
            self.log.append((t_close, "cassure désactivée : clôture revenue dans la Value Area"))
            self.breakout = None
        if (self.breakout is None and side_now != 0 and i >= 1 and sess[i - 1]
                and self._side(closes[i - 1]) == side_now):
            vol = gc_vol_m5.reindex(m5.index[: i + 1])
            pair_now = vol.iloc[i] + vol.iloc[i - 1]
            prev = [vol.iloc[k] + vol.iloc[k - 1] for k in range(i - 2, i - 2 - 2 * self.pairs, -2) if k - 1 >= 0]
            if len(prev) < self.pairs or np.isnan(pair_now) or np.isnan(prev).any():
                self.log.append((t_close, "cassure non évaluée : historique de volume GC insuffisant"))
            elif pair_now >= float(np.median(prev)):
                bound = self.vah if side_now > 0 else self.val
                self.breakout = Setup("breakout", side_now, t_close, None, bound,
                                      f"volume paire {pair_now:.0f} >= médiane {np.median(prev):.0f}")
                self.log.append((t_close, f"cassure acceptée ({'achat' if side_now > 0 else 'vente'})"))

    def active(self, t: pd.Timestamp) -> list[Setup]:
        out = []
        if self.breakout:
            out.append(self.breakout)
        vr = self.value_return
        if vr and vr.since <= t < vr.until:
            out.append(vr)
        return out
