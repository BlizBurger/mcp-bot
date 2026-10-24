"""Exploration (PAS une stratégie) : les « bulles » GC ont-elles un effet mesurable ?

    python -m xauof.strategy.big_trades_study            # 2024-10 → veille du holdout

Sur la séance New York (08:20-13:30) de chaque jour, prix GC :

1. Suite immédiate : après une bulle, le prix continue-t-il dans le sens de l'agresseur
   (+1, +5, +15, +30 min) ? Mouvement signé en $ par once.
2. Niveau : quand le prix revient plus tard sur le prix d'une bulle (après s'en être
   éloigné d'au moins 1 $, et au moins 5 min après), rebondit-il dans le sens de la bulle
   (X $ dans son sens avant X $ contre) ?

Chaque fois comparé à un TÉMOIN : des ordres agressifs ordinaires (taille ≤ médiane), même
nombre, tirés au hasard (graine fixe). Seule la différence bulles − témoin compte.
Le holdout n'est jamais lu ici.
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from xauof.indicators import big_trades as bt
from xauof.indicators import sessions

HORIZONS_MIN = (1, 5, 15, 30)
BOUNCE_USD = (1.0, 2.0)
AWAY_USD, REVISIT_AFTER_MIN = 1.0, 5


def ns(index: pd.DatetimeIndex) -> np.ndarray:
    """Horodatages en nanosecondes, quelle que soit la résolution interne de pandas."""
    return pd.DatetimeIndex(index).as_unit("ns").asi8


def second_bars(trades: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Bougies d'une seconde (temps en ns, plus haut, plus bas, dernier prix)."""
    g = trades["price"].resample("1s")
    b = pd.DataFrame({"hi": g.max(), "lo": g.min(), "last": g.last()}).dropna()
    return ns(b.index), b["hi"].to_numpy(), b["lo"].to_numpy(), b["last"].to_numpy()


def follow_through(events: pd.DataFrame, t_ns: np.ndarray, last: np.ndarray, end_ns: int) -> dict:
    """{horizon: mouvements signés} : side × (prix à t + h − prix de l'ordre)."""
    out = {h: [] for h in HORIZONS_MIN}
    for t, e in zip(ns(events.index), events.itertuples()):
        for h in HORIZONS_MIN:
            target = t + h * 60_000_000_000
            if target > end_ns:
                continue
            i = np.searchsorted(t_ns, target, side="right") - 1
            if i >= 0:
                out[h].append(e.side * (last[i] - e.price))
    return out


def level_reactions(events: pd.DataFrame, t_ns: np.ndarray, hi: np.ndarray, lo: np.ndarray) -> dict:
    """{X: liste de 1 (rebond dans le sens de la bulle) / 0 (cassure)} au premier retour."""
    out = {x: [] for x in BOUNCE_USD}
    for t, e in zip(ns(events.index), events.itertuples()):
        lvl, s = e.price, e.side
        i0 = np.searchsorted(t_ns, t, side="right")
        away = np.flatnonzero((hi[i0:] >= lvl + AWAY_USD) | (lo[i0:] <= lvl - AWAY_USD))
        if not len(away):
            continue
        start = max(i0 + away[0] + 1, np.searchsorted(t_ns, t + REVISIT_AFTER_MIN * 60_000_000_000))
        touch = np.flatnonzero((lo[start:] <= lvl) & (hi[start:] >= lvl))
        if not len(touch):
            continue
        k = start + touch[0] + 1                       # réaction à partir de la seconde suivante
        for x in BOUNCE_USD:
            win = np.flatnonzero(hi[k:] >= lvl + x) if s > 0 else np.flatnonzero(lo[k:] <= lvl - x)
            lose = np.flatnonzero(lo[k:] <= lvl - x) if s > 0 else np.flatnonzero(hi[k:] >= lvl + x)
            w, l_ = (win[0] if len(win) else None), (lose[0] if len(lose) else None)
            if w is None and l_ is None:
                continue                               # non tranché avant la fin de séance
            out[x].append(1 if l_ is None or (w is not None and w < l_) else 0)   # même seconde : cassure
    return out


def session_study(trades: pd.DataFrame, prev_orders: pd.DataFrame, seed: int) -> tuple[dict, pd.DataFrame, float]:
    orders = bt.aggressive_orders(trades)
    thr = bt.threshold(prev_orders)
    big = bt.big_orders(orders, thr)
    normal = orders[orders["size"] <= orders["size"].median()] if len(orders) else orders
    ctrl = normal.sample(n=min(len(big), len(normal)), random_state=seed).sort_index() if len(big) else normal.iloc[:0]
    t_ns, hi, lo, last = second_bars(trades)
    end_ns = int(ns(trades.index)[-1])
    res = {}
    for name, ev in (("bulles", big), ("temoin", ctrl)):
        res[name] = {"suite": follow_through(ev, t_ns, last, end_ns), "niveau": level_reactions(ev, t_ns, hi, lo),
                     "n": len(ev)}
    return res, orders, thr


def merge(acc: dict, res: dict) -> None:
    for name, r in res.items():
        a = acc.setdefault(name, {"suite": {h: [] for h in HORIZONS_MIN}, "niveau": {x: [] for x in BOUNCE_USD}, "n": 0})
        a["n"] += r["n"]
        for h in HORIZONS_MIN:
            a["suite"][h] += r["suite"][h]
        for x in BOUNCE_USD:
            a["niveau"][x] += r["niveau"][x]


def report(acc: dict, n_days: int, thresholds: list[float]) -> str:
    from xauof.strategy.run import bootstrap_ci

    b, c = acc.get("bulles"), acc.get("temoin")
    if not b or not b["n"]:
        return "Aucune bulle trouvée."
    lines = [f"{n_days} séances · {b['n']} bulles ({b['n'] / n_days:.0f} par séance) · seuil médian "
             f"{np.median(thresholds):.0f} contrats (centile 99,5 de la séance précédente, minimum 10)", "",
             "1. SUITE IMMÉDIATE (mouvement en $ dans le sens de l'agresseur, prix GC)",
             "   horizon    bulles [IC 95 %]               témoin [IC 95 %]               écart"]
    for h in HORIZONS_MIN:
        vb, vc = np.asarray(b["suite"][h]), np.asarray(c["suite"][h])
        if not len(vb) or not len(vc):
            continue
        (lb, hb), (lc, hc) = bootstrap_ci(vb), bootstrap_ci(vc)
        lines.append(f"   {h:>3} min   {vb.mean():+.3f} [{lb:+.3f} ; {hb:+.3f}]   "
                     f"{vc.mean():+.3f} [{lc:+.3f} ; {hc:+.3f}]   {vb.mean() - vc.mean():+.3f}")
    lines += ["", f"2. RETOUR SUR LE NIVEAU (après ≥ {AWAY_USD:.0f} $ d'écart et ≥ {REVISIT_AFTER_MIN} min) : "
                  "part de rebonds dans le sens de la bulle",
              "   seuil      bulles [IC 95 %]            témoin [IC 95 %]            écart"]
    for x in BOUNCE_USD:
        vb, vc = np.asarray(b["niveau"][x], float), np.asarray(c["niveau"][x], float)
        if not len(vb) or not len(vc):
            continue
        (lb, hb), (lc, hc) = bootstrap_ci(vb), bootstrap_ci(vc)
        lines.append(f"   ±{x:.0f} $     {vb.mean():.1%} [{lb:.1%} ; {hb:.1%}] n={len(vb):<5} "
                     f"{vc.mean():.1%} [{lc:.1%} ; {hc:.1%}] n={len(vc):<5} {100 * (vb.mean() - vc.mean()):+.1f} pts")
    lines += ["", "Lecture : un rebond à 50 % = pile ou face. Coût d'un aller-retour XAUUSD ≈ 0,45 $ (spread + "
                  "commission) : une suite de quelques centimes ne paie pas les frais.",
              "Exploration seulement : une règle tirée de ces chiffres devra être figée puis testée sur le holdout."]
    return "\n".join(lines)


def main() -> None:
    from xauof.config import load_config
    from xauof.data import store
    from xauof.indicators.check import day_bounds

    cfg = load_config()
    p = argparse.ArgumentParser(description="Exploration des bulles GC (gros ordres agressifs)")
    p.add_argument("--start", default="2024-10-01")
    p.add_argument("--end", default=None, help="exclu ; jamais au-delà du début du holdout")
    args = p.parse_args()
    hold = pd.Timestamp(cfg["validation"]["holdout_start"])
    end = min(pd.Timestamp(args.end), hold) if args.end else hold
    rth = tuple(cfg["sessions"]["rth"])
    acc, thresholds, prev, n_days = {}, [], None, 0
    for day in pd.bdate_range(args.start, end - pd.Timedelta(days=1)):
        a, b = day_bounds(day)
        try:
            trades = sessions.day_trades(store.load_range(store.gc_trades_dir(cfg), a, b), day, rth)
        except FileNotFoundError:
            continue
        if trades.empty:
            continue
        if prev is not None:
            res, orders, thr = session_study(trades, prev, seed=int(day.strftime("%Y%m%d")))
            merge(acc, res)
            thresholds.append(thr)
            n_days += 1
        else:
            orders = bt.aggressive_orders(trades)
        prev = orders
        print(f"\r{day:%Y-%m-%d}  bulles : {acc.get('bulles', {}).get('n', 0)}", end="", flush=True)
    print("\n")
    print(report(acc, n_days, thresholds))


if __name__ == "__main__":
    main()
