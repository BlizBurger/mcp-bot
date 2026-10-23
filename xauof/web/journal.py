"""Journal de trading : positions clôturées importées de MT5 + tes annotations (setup, critères, notes).

Objectif : mesurer, avec la même rigueur que les backtests, quels setups gagnent VRAIMENT
chez toi (résultat en R quand le stop initial est connu).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from xauof.data import store

CRITERIA = [
    "Zone FVG/OB",
    "Niveau de la veille",
    "Sens du contexte",
    "Après l'IB (09:20)",
    "Hors fenêtre news",
    "Alerte A+ reçue",
]
USER_FIELDS = ("setup", "criteres", "note")


def journal_path(cfg: dict) -> Path:
    return store.data_dir(cfg) / "journal" / "trades.json"


def load(cfg: dict) -> list[dict]:
    p = journal_path(cfg)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def save(cfg: dict, trades: list[dict]) -> None:
    p = journal_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(trades, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _g(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def trades_from_deals(deals: Iterable, orders: Iterable, symbol: str, value_per_price: float,
                      to_utc) -> list[dict]:
    """Regroupe les transactions MT5 par position. `to_utc(sec)` convertit l'heure serveur MT5.

    deals : objets ou dicts MT5 (position_id, entry 0=IN 1=OUT, type 0=BUY 1=SELL, volume,
    price, profit, commission, swap, fee, time) ; orders : (position_id, sl, time_setup)."""
    by_pos: dict[int, list] = {}
    for d in deals:
        if _g(d, "symbol", symbol) != symbol or _g(d, "position_id", 0) == 0:
            continue
        by_pos.setdefault(int(_g(d, "position_id")), []).append(d)
    sl_by_pos: dict[int, float] = {}
    for o in sorted(orders, key=lambda o: _g(o, "time_setup", 0)):
        pid, sl = int(_g(o, "position_id", 0)), float(_g(o, "sl", 0.0) or 0.0)
        if pid and sl and pid not in sl_by_pos:
            sl_by_pos[pid] = sl                      # premier stop connu = stop initial
    out = []
    for pid, ds in by_pos.items():
        ins = [d for d in ds if _g(d, "entry") == 0]
        outs = [d for d in ds if _g(d, "entry") in (1, 3)]
        if not ins or not outs:
            continue                                 # position encore ouverte
        lots = sum(_g(d, "volume") for d in ins)
        entry = sum(_g(d, "price") * _g(d, "volume") for d in ins) / lots
        out_lots = sum(_g(d, "volume") for d in outs)
        if out_lots + 1e-9 < lots:
            continue                                 # partiellement fermée : on attend la fin
        exit_ = sum(_g(d, "price") * _g(d, "volume") for d in outs) / out_lots
        direction = 1 if _g(ins[0], "type") == 0 else -1
        pnl = sum(_g(d, "profit") + _g(d, "commission") + _g(d, "swap") + (_g(d, "fee", 0) or 0) for d in ds)
        sl = sl_by_pos.get(pid)
        risk = abs(entry - sl) * value_per_price * lots if sl else None
        out.append({
            "id": pid, "sens": "achat" if direction > 0 else "vente", "lots": round(lots, 2),
            "ouverture": to_utc(min(_g(d, "time") for d in ins)).isoformat(),
            "fermeture": to_utc(max(_g(d, "time") for d in outs)).isoformat(),
            "prix_entree": round(entry, 2), "prix_sortie": round(exit_, 2), "sl_initial": sl,
            "pnl": round(pnl, 2), "risque_usd": round(risk, 2) if risk else None,
            "r": round(pnl / risk, 3) if risk else None,
        })
    return sorted(out, key=lambda t: t["ouverture"])


def merge(existing: list[dict], fresh: list[dict]) -> list[dict]:
    """Ajoute les nouvelles positions, garde tes annotations sur celles déjà connues."""
    known = {t["id"]: t for t in existing}
    for t in fresh:
        if t["id"] in known:
            for k in USER_FIELDS:
                t[k] = known[t["id"]].get(k)
        else:
            t.update({"setup": "", "criteres": [], "note": ""})
        known[t["id"]] = t
    return sorted(known.values(), key=lambda t: t["ouverture"])


def annotate(trades: list[dict], trade_id: int, setup: Optional[str] = None,
             criteres: Optional[list] = None, note: Optional[str] = None) -> dict:
    t = next(t for t in trades if t["id"] == trade_id)
    if setup is not None:
        t["setup"] = setup
    if criteres is not None:
        t["criteres"] = [c for c in criteres if c in CRITERIA]
    if note is not None:
        t["note"] = note
    return t


def _group_stats(df: pd.DataFrame) -> dict:
    r = df["r"].dropna()
    return {"n": int(len(df)), "gagnants": float((df["pnl"] > 0).mean()) if len(df) else 0.0,
            "pnl": round(float(df["pnl"].sum()), 2), "r_moyen": round(float(r.mean()), 3) if len(r) else None,
            "n_avec_r": int(len(r))}


def stats(trades: list[dict]) -> dict:
    """Statistiques globales, par setup et par critère (et A+ = tous tes critères principaux)."""
    if not trades:
        return {"global": _group_stats(pd.DataFrame(columns=["pnl", "r"])), "par_setup": {}, "par_critere": {}}
    df = pd.DataFrame(trades)
    df["r"] = pd.to_numeric(df["r"], errors="coerce")
    res = {"global": _group_stats(df),
           "par_setup": {s or "(non renseigné)": _group_stats(g) for s, g in df.groupby(df["setup"].fillna(""))},
           "par_critere": {}}
    for c in CRITERIA:
        has = df["criteres"].apply(lambda xs, c=c: c in (xs or []))
        res["par_critere"][c] = {"avec": _group_stats(df[has]), "sans": _group_stats(df[~has])}
    main = {"Zone FVG/OB", "Niveau de la veille", "Sens du contexte"}
    aplus = df["criteres"].apply(lambda xs: main <= set(xs or []))
    res["a_plus"] = {"avec": _group_stats(df[aplus]), "sans": _group_stats(df[~aplus])}
    res["avertissement"] = ("Moins de 30 trades par groupe : différences non significatives."
                            if len(df) < 30 else "")
    return res

