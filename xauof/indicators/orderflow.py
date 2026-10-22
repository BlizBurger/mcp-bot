"""Order flow : qui est agressif, et est-ce que ça fait bouger le prix ?

Entrée : transactions avec `price`, `size`, `aggressor` (+1 acheteur, -1 vendeur, 0 inconnu).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from xauof.indicators.volume_profile import to_bins


def delta_bars(trades: pd.DataFrame, freq: str = "5min") -> pd.DataFrame:
    """Bougies avec volume acheteur / vendeur agressif, delta et CVD."""
    buy = trades["size"].where(trades["aggressor"] > 0, 0)
    sell = trades["size"].where(trades["aggressor"] < 0, 0)
    g = pd.DataFrame({"price": trades["price"], "size": trades["size"], "buy": buy, "sell": sell}).resample(freq)
    bars = g["price"].ohlc()
    bars["volume"] = g["size"].sum()
    bars["buy"] = g["buy"].sum()
    bars["sell"] = g["sell"].sum()
    bars = bars.dropna(subset=["open"])
    bars["delta"] = bars["buy"] - bars["sell"]
    bars["cvd"] = bars["delta"].cumsum()
    return bars


def footprint(trades: pd.DataFrame, freq: str, bin_size: float,
              ratio: float = 3.0, min_volume: int = 10) -> pd.DataFrame:
    """Volume acheteur / vendeur par (bougie, niveau de prix) + déséquilibres diagonaux.

    Déséquilibre acheteur au niveau p : achats agressifs en p ≥ ratio × ventes
    agressives en p - 1 niveau (les acheteurs lèvent l'offre plus vite que les
    vendeurs ne frappent le bid juste en dessous). Symétrique côté vendeur.
    """
    df = pd.DataFrame({
        "bar": trades.index.floor(freq),
        "level": to_bins(trades["price"], bin_size),
        "buy": trades["size"].where(trades["aggressor"] > 0, 0),
        "sell": trades["size"].where(trades["aggressor"] < 0, 0),
    })
    fp = df.groupby(["bar", "level"])[["buy", "sell"]].sum()
    out = []
    for bar, g in fp.groupby(level="bar"):
        g = g.droplevel("bar").reindex(range(g.index.get_level_values("level").min(),
                                             g.index.get_level_values("level").max() + 1), fill_value=0)
        sell_below = g["sell"].shift(1, fill_value=0)   # ventes au niveau p-1
        buy_above = g["buy"].shift(-1, fill_value=0)    # achats au niveau p+1
        g["buy_imb"] = (g["buy"] >= min_volume) & (g["buy"] >= ratio * sell_below.clip(lower=1))
        g["sell_imb"] = (g["sell"] >= min_volume) & (g["sell"] >= ratio * buy_above.clip(lower=1))
        g["price"] = np.round(g.index.to_numpy() * bin_size, 10)
        g["bar"] = bar
        out.append(g.reset_index(drop=True))
    res = pd.concat(out, ignore_index=True) if out else pd.DataFrame(
        columns=["buy", "sell", "buy_imb", "sell_imb", "price", "bar"])
    res["delta"] = res["buy"] - res["sell"]
    return res.set_index(["bar", "price"])


def absorption(bars: pd.DataFrame, lookback: int = 20, vol_mult: float = 1.5,
               delta_frac: float = 0.15, max_move_atr: float = 0.3) -> pd.Series:
    """Absorption : gros volume, agresseurs nettement d'un côté, mais le prix n'avance pas.

    +1 = ventes absorbées (des acheteurs passifs tiennent → haussier)
    -1 = achats absorbés (des vendeurs passifs tiennent → baissier)
     0 = rien
    """
    vol_ok = bars["volume"] >= vol_mult * bars["volume"].rolling(lookback, min_periods=5).mean().shift(1)
    side = np.sign(bars["delta"])
    one_sided = bars["delta"].abs() >= delta_frac * bars["volume"]
    rng = (bars["high"] - bars["low"]).rolling(lookback, min_periods=5).mean().shift(1)
    move = (bars["close"] - bars["open"]) * side          # avancée dans le sens des agresseurs
    stalled = move <= max_move_atr * rng
    hit = vol_ok & one_sided & stalled
    return (-side).where(hit, 0).astype(int)


def large_prints(trades: pd.DataFrame, min_size: int | None = None, quantile: float = 0.999) -> pd.DataFrame:
    """Transactions de taille anormale (seuil fixe, ou quantile de la période)."""
    threshold = min_size if min_size is not None else trades["size"].quantile(quantile)
    return trades[trades["size"] >= threshold]


def cvd_divergence(bars: pd.DataFrame, lookback: int = 12) -> pd.Series:
    """-1 : le prix fait un plus haut sur `lookback` bougies mais pas le CVD (achat essoufflé).
    +1 : plus bas du prix sans plus bas du CVD (vente essoufflée)."""
    hh = bars["high"] >= bars["high"].rolling(lookback).max()
    cvd_hh = bars["cvd"] >= bars["cvd"].rolling(lookback).max()
    ll = bars["low"] <= bars["low"].rolling(lookback).min()
    cvd_ll = bars["cvd"] <= bars["cvd"].rolling(lookback).min()
    return pd.Series(np.where(hh & ~cvd_hh, -1, np.where(ll & ~cvd_ll, 1, 0)), index=bars.index)
