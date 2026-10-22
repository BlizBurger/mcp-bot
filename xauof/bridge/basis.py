"""Passerelle GC → XAUUSD.

basis = prix GC − prix XAUUSD (même minute). Le future cote plus cher que le
spot (coût de portage) : quelques dizaines de dollars, qui fondent jusqu'à
l'échéance puis sautent au roulement de contrat.

Un niveau GC (POC, LVN, zone d'absorption...) se reporte sur le graphique du
broker par : niveau_xau = niveau_gc − basis.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def align(gc_m1: pd.DataFrame, xau_m1: pd.DataFrame, point: float) -> pd.DataFrame:
    """Minutes communes : clôture GC, milieu bid/ask XAUUSD, basis, contrat GC.

    MT5 enregistre les bougies au bid ; le spread (en points) donne l'ask.
    """
    xau_mid = xau_m1["close"] + xau_m1["spread"] * point / 2
    df = pd.concat({"gc": gc_m1["close"], "xau": xau_mid, "contract": gc_m1["instrument_id"]},
                   axis=1, join="inner").dropna()
    df["basis"] = df["gc"] - df["xau"]
    return df


def rolling_basis(df: pd.DataFrame, window: str = "60min", min_obs: int = 20) -> pd.Series:
    """Médiane glissante du basis sur le passé uniquement (aucune information future),
    remise à zéro à chaque changement de contrat GC."""
    out = df.groupby("contract", group_keys=False)["basis"].apply(
        lambda s: s.rolling(window, min_periods=min_obs).median())
    return out.reindex(df.index)


def basis_noise(df: pd.DataFrame, window: str = "60min", min_obs: int = 20) -> pd.Series:
    """Écart-type glissant du basis : s'il grimpe (news, broker qui décroche), on ne
    fait plus confiance au report de niveaux."""
    out = df.groupby("contract", group_keys=False)["basis"].apply(
        lambda s: s.rolling(window, min_periods=min_obs).std())
    return out.reindex(df.index)


def rolls(df: pd.DataFrame) -> pd.DataFrame:
    """Changements de contrat GC avec le saut de basis associé."""
    changed = df["contract"].ne(df["contract"].shift()) & df["contract"].shift().notna()
    rows = []
    for t in df.index[changed]:
        before = df.loc[:t, "basis"].iloc[-61:-1].median()
        after = df.loc[t:, "basis"].iloc[:60].median()
        rows.append({"time": t, "basis_avant": before, "basis_apres": after, "saut": after - before})
    return pd.DataFrame(rows)


def clock_offset(gc_close: pd.Series, xau_close: pd.Series,
                 offsets_min=(-180, -120, -60, 0, 60, 120, 180)) -> dict[int, float]:
    """Corrélation des rendements 1 min GC / XAUUSD pour chaque décalage d'horloge testé.

    Le bon décalage est celui qui maximise la corrélation ; 0 = horloges alignées.
    Un décalage de ±60 min trahit une erreur de fuseau ou de changement d'heure.
    """
    gc_ret = gc_close.diff()
    res = {}
    for k in offsets_min:
        shifted = xau_close.copy()
        shifted.index = shifted.index + pd.Timedelta(minutes=k)
        both = pd.concat([gc_ret, shifted.diff()], axis=1, join="inner").dropna()
        res[k] = float(both.iloc[:, 0].corr(both.iloc[:, 1])) if len(both) > 30 else np.nan
    return res


def weekly_clock_check(gc_close: pd.Series, xau_close: pd.Series) -> pd.DataFrame:
    """Décalage d'horloge le plus probable semaine par semaine (repère les changements d'heure)."""
    rows = []
    for week, g in gc_close.groupby(gc_close.index.tz_localize(None).to_period("W")):
        x = xau_close[(xau_close.index >= g.index[0] - pd.Timedelta(hours=4)) &
                      (xau_close.index <= g.index[-1] + pd.Timedelta(hours=4))]
        corr = clock_offset(g, x)
        valid = {k: v for k, v in corr.items() if not np.isnan(v)}
        if not valid:
            continue
        best = max(valid, key=valid.get)
        rows.append({"semaine": str(week.start_time.date()), "decalage_min": best,
                     "correlation": round(valid[best], 3)})
    return pd.DataFrame(rows)


def gc_to_xau(level_gc: float, basis: float) -> float:
    return level_gc - basis


def xau_to_gc(level_xau: float, basis: float) -> float:
    return level_xau + basis
