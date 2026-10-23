"""Contexte GC projeté sur XAUUSD : profils de la séance New York PRÉCÉDENTE,
Initial Balance de la séance courante, score de confluence et objectifs.

Séances utilisées (V1) :
- profils volume et TPO : séance New York précédente 08:20–13:30 (jamais le profil
  électronique 18:00–17:00, jamais la séance en cours) ;
- Initial Balance : 08:20–09:20 de la séance courante, utilisable à partir de 09:20.

Projection : un niveau GC de la séance précédente est converti en prix spot avec le
basis médian de CETTE séance (calculé sur le contrat GC qui a formé le profil). Les
niveaux vivent ensuite en « espace XAUUSD », indépendant du contrat GC : un
roulement entre deux séances ne décale donc pas les niveaux.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from xauof.bridge import basis as bs
from xauof.indicators import tpo
from xauof.indicators import volume_profile as vp


@dataclass
class SessionLevels:
    """Niveaux de la séance précédente, déjà convertis en prix XAUUSD."""
    source_day: pd.Timestamp
    contract: int
    basis: float
    poc_v: float
    vah_v: float
    val_v: float
    poc_t: float
    vah_t: float
    val_t: float
    hvn: list[tuple[float, float]] = field(default_factory=list)
    lvn: list[float] = field(default_factory=list)
    singles: list[tuple[float, float]] = field(default_factory=list)

    def value_area(self, source: str) -> tuple[float, float, float]:
        """(VAL, POC, VAH) selon la source choisie pour les scénarios."""
        if source == "tpo":
            return self.val_t, self.poc_t, self.vah_t
        return self.val_v, self.poc_v, self.vah_v

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "source_day"} | {"source_day": str(self.source_day.date())}


def build_session_levels(cfg: dict, rth_trades: pd.DataFrame, gc_m1: pd.DataFrame,
                         xau_m1: pd.DataFrame, day) -> tuple[Optional[SessionLevels], str]:
    """Profils de la séance `day` (fenêtre New York) convertis en XAUUSD. (None, motif) si impossible."""
    p, st = cfg["profile"], cfg["strategy"]
    if rth_trades.empty:
        return None, "aucune transaction GC sur la séance précédente"
    contract = int(rth_trades["instrument_id"].iloc[0])        # day_trades garde le contrat principal
    t0, t1 = rth_trades.index[0].floor("1min"), rth_trades.index[-1].ceil("1min")
    g = gc_m1[(gc_m1.index >= t0) & (gc_m1.index <= t1) & (gc_m1["instrument_id"] == contract)]
    x = xau_m1[(xau_m1.index >= t0) & (xau_m1.index <= t1)]
    aligned = bs.align(g, x, cfg["broker"]["point"])
    if len(aligned) < cfg["bridge"]["min_obs"]:
        return None, f"basis indisponible sur la séance précédente ({len(aligned)} minutes communes)"
    if aligned["basis"].std() > 3 * cfg["bridge"]["max_noise"]:
        return None, f"basis instable sur la séance précédente (écart-type {aligned['basis'].std():.2f} $)"
    b = float(aligned["basis"].median())

    vol = vp.volume_profile(rth_trades, p["bin_size"], p["value_area_pct"], p["hvn_smooth"], p["lvn_ratio"])
    hz = vp.hvn_zones(vol.profile, p["hvn_smooth"], st["data_guards"]["hvn_zone_frac"])
    tp = tpo.tpo_profile(rth_trades, p["bin_size"], cfg["sessions"]["tpo_period_min"],
                         cfg["sessions"]["initial_balance_min"], p["value_area_pct"])
    x_ = lambda v: float(v) - b  # noqa: E731
    return SessionLevels(
        source_day=pd.Timestamp(day), contract=contract, basis=b,
        poc_v=x_(vol.poc), vah_v=x_(vol.vah), val_v=x_(vol.val),
        poc_t=x_(tp.poc), vah_t=x_(tp.vah), val_t=x_(tp.val),
        hvn=[(x_(a), x_(c)) for a, c in hz], lvn=[x_(v) for v in vol.lvn],
        singles=[(x_(a), x_(c)) for a, c in tp.single_prints],
    ), ""


def ib_from_gc(gc_m1: pd.DataFrame, ib_start: pd.Timestamp, ib_end: pd.Timestamp) -> Optional[dict]:
    """IB GC (prix futures) de [ib_start, ib_end) ; None si pas de données."""
    w = gc_m1[(gc_m1.index >= ib_start) & (gc_m1.index < ib_end)]
    if w.empty:
        return None
    return {"high": float(w["high"].max()), "low": float(w["low"].min()),
            "contract": int(w["instrument_id"].iloc[-1])}


def near(zone, lo: float, hi: Optional[float], tol: float) -> bool:
    return zone.distance(lo, hi) <= tol


def score(zone, lv: SessionLevels, ib_xau: Optional[tuple[float, float]], tol: float) -> dict:
    """Score par famille (1 point max par famille). Le POC n'est pas compté."""
    rejet = [f"LVN {v:.2f}" for v in lv.lvn if near(zone, v, None, tol)] + \
            [f"single print {a:.2f}-{c:.2f}" for a, c in lv.singles if near(zone, a, c, tol)]
    bornes = [f"{n} {v:.2f}" for n, v in (("VAH vol", lv.vah_v), ("VAL vol", lv.val_v),
                                          ("VAH TPO", lv.vah_t), ("VAL TPO", lv.val_t)) if near(zone, v, None, tol)]
    ib = [] if ib_xau is None else [f"{n} {v:.2f}" for n, v in (("IB bas", ib_xau[0]), ("IB haut", ib_xau[1]))
                                     if near(zone, v, None, tol)]
    fam = {"rejet": rejet, "bornes_valeur": bornes, "ib": ib}
    return {"families": {k: bool(v) for k, v in fam.items()}, "details": fam,
            "score": sum(bool(v) for v in fam.values())}


def measured_move(direction: int, lv: SessionLevels, source: str) -> tuple[float, str]:
    """Cassure acceptée : borne cassée ± hauteur de la Value Area précédente."""
    val, _, vah = lv.value_area(source)
    height = vah - val
    return (vah + height, "mouvement mesuré") if direction > 0 else (val - height, "mouvement mesuré")


def opposing_levels(direction: int, entry: float, lv: SessionLevels,
                    extra: Optional[list[tuple[float, str]]] = None) -> list[tuple[float, str]]:
    """Niveaux connus situés devant le prix dans le sens du trade (POC, VAH/VAL des deux
    profils, bord proche des HVN, plus `extra`), du plus proche au plus lointain."""
    cands = [(lv.poc_v, "POC vol"), (lv.poc_t, "POC TPO"), (lv.vah_v, "VAH vol"), (lv.val_v, "VAL vol"),
             (lv.vah_t, "VAH TPO"), (lv.val_t, "VAL TPO")] + list(extra or [])
    for lo, hi in lv.hvn:
        if lo <= entry <= hi:
            continue                                  # déjà dans la HVN
        cands.append((lo if direction > 0 else hi, f"HVN {lo:.2f}-{hi:.2f}"))
    ahead = [(lvl, name) for lvl, name in cands if direction * (lvl - entry) > 0]
    return sorted(ahead, key=lambda c: abs(c[0] - entry))


def target(direction: int, entry: float, lv: SessionLevels,
           extra: Optional[list[tuple[float, str]]] = None) -> Optional[tuple[float, str]]:
    """V1 : premier niveau opposant connu dans le sens du trade (aucun obstacle sauté)."""
    ahead = opposing_levels(direction, entry, lv, extra)
    return ahead[0] if ahead else None


def obstacle_before(direction: int, entry: float, limit: float, lv: SessionLevels) -> Optional[tuple[float, str]]:
    """V2 « espace libre » : premier niveau connu strictement devant l'entrée et au plus à `limit`."""
    ahead = [c for c in opposing_levels(direction, entry, lv) if direction * (c[0] - limit) <= 0]
    return ahead[0] if ahead else None


def unclassified_share(gc_m1: pd.DataFrame, t: pd.Timestamp, window: str = "60min") -> float:
    """Part du volume GC récent sans côté agresseur (le delta n'est fiable que si elle est faible)."""
    w = gc_m1[(gc_m1.index > t - pd.Timedelta(window)) & (gc_m1.index < t)]
    vol = w["volume"].sum()
    if vol <= 0:
        return float("nan")
    return float((vol - w["buy"].sum() - w["sell"].sum()) / vol)


def previous_session_day(available_days, day) -> Optional[pd.Timestamp]:
    prior = [d for d in available_days if d < pd.Timestamp(day)]
    return max(prior) if prior else None

