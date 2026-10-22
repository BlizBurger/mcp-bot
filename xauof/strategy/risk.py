"""Compte, limites de perte et filtres de marché.

Limites (toutes « pertes réalisées + flottantes + frais », vs solde à minuit Europe/Prague) :
- arrêt interne journalier : risk.daily_stop_usd (150) → clôture + verrouillage jusqu'au lendemain ;
- référence FTMO journalière : ftmo.daily_loss_limit_usd (500) ;
- plancher FTMO total : capital − ftmo.max_loss_usd (9 000), avec une marge interne
  (9 200 par défaut) → clôture + verrouillage définitif.
Une clôture au marché peut subir du slippage : ces seuils ne garantissent PAS une
perte maximale exacte.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


@dataclass
class Account:
    initial: float
    daily_stop: float
    ftmo_daily: float
    ftmo_max_loss: float
    floor_margin: float
    reset_tz: str
    balance: float = 0.0
    day: Optional[pd.Timestamp] = None           # date Prague en cours
    day_start_balance: float = 0.0
    day_locked: bool = False
    total_locked: bool = False
    lock_reason: str = ""
    events: list = field(default_factory=list)

    @classmethod
    def from_config(cls, cfg: dict) -> "Account":
        a = cls(initial=cfg["account"]["initial_capital"], daily_stop=cfg["risk"]["daily_stop_usd"],
                ftmo_daily=cfg["ftmo"]["daily_loss_limit_usd"], ftmo_max_loss=cfg["ftmo"]["max_loss_usd"],
                floor_margin=cfg["risk"]["total_floor_margin_usd"], reset_tz=cfg["risk"]["daily_reset_timezone"])
        a.balance = a.day_start_balance = a.initial
        return a

    @property
    def ftmo_floor(self) -> float:
        return self.initial - self.ftmo_max_loss

    @property
    def internal_floor(self) -> float:
        return self.ftmo_floor + self.floor_margin

    def roll(self, t: pd.Timestamp) -> None:
        """Nouveau jour à minuit Europe/Prague : solde de référence et levée du verrou journalier."""
        d = t.tz_convert(self.reset_tz).normalize()
        if self.day is None or d != self.day:
            self.day = d
            self.day_start_balance = self.balance
            if self.day_locked:
                self.events.append((t, "verrou journalier levé"))
            self.day_locked = False

    def daily_loss(self, equity: float) -> float:
        return self.day_start_balance - equity

    def remaining_daily(self, equity: float) -> float:
        """Budget avant l'arrêt interne journalier."""
        return self.daily_stop - self.daily_loss(equity)

    def remaining_total(self, equity: float) -> float:
        return equity - self.internal_floor

    def risk_budget(self, equity: float, pct: float) -> float:
        return pct / 100 * min(equity, self.initial)

    def breach(self, equity: float) -> Optional[str]:
        """Motif si l'equity franchit une limite interne, sinon None."""
        if equity <= self.internal_floor:
            return f"plancher interne atteint (equity {equity:.2f} <= {self.internal_floor:.2f})"
        if self.daily_loss(equity) >= self.daily_stop:
            return f"arrêt journalier interne (perte {self.daily_loss(equity):.2f} >= {self.daily_stop:.2f})"
        return None

    def lock(self, t: pd.Timestamp, reason: str, total: bool = False) -> None:
        if total:
            self.total_locked = True
        else:
            self.day_locked = True
        self.lock_reason = reason
        self.events.append((t, f"verrouillage {'total' if total else 'journalier'} : {reason}"))

    def ftmo_status(self, equity: float) -> dict:
        return {"perte_jour": round(self.daily_loss(equity), 2),
                "reste_ftmo_jour": round(self.ftmo_daily - self.daily_loss(equity), 2),
                "reste_ftmo_total": round(equity - self.ftmo_floor, 2)}


def spread_blocked(spreads: pd.Series, t: pd.Timestamp, current: float, window_min: int,
                   min_obs: int, mult: float) -> Optional[str]:
    """Spread courant > mult × médiane des `window_min` minutes précédentes → motif de blocage.
    Historique insuffisant → blocage aussi (pas d'entrée sans référence)."""
    w = spreads[(spreads.index >= t - pd.Timedelta(minutes=window_min)) & (spreads.index < t)]
    if len(w) < min_obs:
        return f"historique de spread insuffisant ({len(w)}/{min_obs} minutes)"
    med = float(w.median())
    if current > mult * med:
        return f"spread {current:.0f} pts > {mult:g} × médiane {med:.0f} pts"
    return None
