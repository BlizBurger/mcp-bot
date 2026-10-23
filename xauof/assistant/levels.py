"""Niveaux du jour : profils volume / TPO de la séance New York précédente, en prix XAUUSD."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pandas as pd

from xauof.data import store
from xauof.indicators import sessions
from xauof.indicators.orderflow import delta_bars
from xauof.strategy import context as ctx


def assistant_dir(cfg: dict) -> Path:
    return store.data_dir(cfg) / "assistant"


def gc_minutes(trades: pd.DataFrame) -> pd.DataFrame:
    """Bougies GC 1 min (même format que data/gc/m1) à partir des transactions."""
    bars = delta_bars(trades, "1min").drop(columns="cvd")
    bars["instrument_id"] = trades["instrument_id"].resample("1min").last().reindex(bars.index)
    return bars


def compute_levels(cfg: dict, trades: pd.DataFrame, xau_m1: pd.DataFrame, session_day) -> tuple[Optional[dict], str]:
    """Profils de la séance `session_day` (08:20-13:30 New York) convertis en XAUUSD."""
    rth = sessions.day_trades(trades, session_day, tuple(cfg["sessions"]["rth"]))
    levels, reason = ctx.build_session_levels(cfg, rth, gc_minutes(trades), xau_m1, session_day)
    if levels is None:
        return None, reason
    return levels.as_dict(), ""


def save_levels(cfg: dict, for_day, levels: dict) -> Path:
    path = assistant_dir(cfg) / f"levels_{pd.Timestamp(for_day):%Y-%m-%d}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(levels, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_levels(cfg: dict, for_day) -> Optional[dict]:
    path = assistant_dir(cfg) / f"levels_{pd.Timestamp(for_day):%Y-%m-%d}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def level_lines(levels: dict) -> list[tuple[str, float]]:
    """Liste (nom, prix XAUUSD) pour l'affichage, triée par prix décroissant."""
    out = [("POC vol", levels["poc_v"]), ("VAH vol", levels["vah_v"]), ("VAL vol", levels["val_v"]),
           ("POC TPO", levels["poc_t"]), ("VAH TPO", levels["vah_t"]), ("VAL TPO", levels["val_t"])]
    out += [("LVN", v) for v in levels["lvn"]]
    out += [(f"HVN {lo:.2f}-{hi:.2f}", (lo + hi) / 2) for lo, hi in levels["hvn"]]
    out += [(f"single prints {lo:.2f}-{hi:.2f}", (lo + hi) / 2) for lo, hi in levels["singles"]]
    return sorted(out, key=lambda x: -x[1])
