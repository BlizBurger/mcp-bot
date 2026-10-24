"""« Bulles » : gros ordres agressifs du GC (vrai volume CME).

Un ordre agressif qui balaie plusieurs prix produit plusieurs transactions au même
horodatage : on les regroupe (même ts_event, même côté) en un seul ordre. Un ordre est
« gros » s'il dépasse un seuil fixé à partir de la séance PRÉCÉDENTE (pas d'anticipation).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def aggressive_orders(trades: pd.DataFrame) -> pd.DataFrame:
    """Ordres agressifs : index = horodatage, colonnes side (+1 achat / -1 vente), size,
    price (dernier prix touché), lo / hi (prix balayés). Transactions sans côté ignorées."""
    t = trades[trades["aggressor"] != 0]
    if t.empty:
        return pd.DataFrame(columns=["side", "size", "price", "lo", "hi"])
    g = t.assign(side=t["aggressor"]).groupby([t.index, "side"], sort=True)
    out = pd.DataFrame({"size": g["size"].sum(), "price": g["price"].last(),
                        "lo": g["price"].min(), "hi": g["price"].max()}).reset_index(level="side")
    out.index.name = "ts_event"
    return out


def threshold(previous_orders: pd.DataFrame, pct: float = 99.5, floor: int = 10) -> float:
    """Seuil « gros ordre » : centile `pct` des tailles de la séance précédente, au moins `floor`."""
    if previous_orders.empty:
        return float(floor)
    return float(max(floor, np.percentile(previous_orders["size"], pct)))


def big_orders(orders: pd.DataFrame, min_size: float) -> pd.DataFrame:
    return orders[orders["size"] >= min_size]
