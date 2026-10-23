"""Mise à jour du matin : niveaux de la séance GC précédente, reportés sur XAUUSD, + calendrier.

Usage (MT5 ouvert, clé Databento dans .env) :
    python -m xauof.assistant.update
    python -m xauof.assistant.update --day 2026-10-26     # niveaux pour une autre journée
Coût Databento : la fenêtre 08:20-13:30 d'une seule séance (quelques centimes), plafonné.
"""
from __future__ import annotations

import argparse

import pandas as pd

from xauof.config import ROOT, load_config, secret
from xauof.data.gc import normalize_trades
from xauof.assistant import levels as lv
from xauof.assistant.snapshot import ny_time
from xauof.news.calendar import ensure_fresh

MAX_COST_USD = 1.0


def previous_weekdays(day: pd.Timestamp, n: int = 5) -> list[pd.Timestamp]:
    out, d = [], pd.Timestamp(day)
    while len(out) < n:
        d -= pd.Timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return out


def fetch_session(cfg: dict, client, session_day: pd.Timestamp) -> pd.DataFrame:
    tz = cfg["strategy"]["timezone"]
    a, b = (ny_time(session_day, h, tz) for h in cfg["sessions"]["rth"])
    req = dict(dataset=cfg["symbols"]["dataset"], symbols=[cfg["symbols"]["futures"]], stype_in="continuous",
               schema=cfg["data"]["schema"], start=a, end=b)
    cost = client.metadata.get_cost(**req)
    if cost > MAX_COST_USD:
        raise SystemExit(f"STOP : coût estimé {cost:.2f} $ > {MAX_COST_USD:.2f} $ pour une séance")
    raw = client.timeseries.get_range(**req).to_df()
    return normalize_trades(raw) if len(raw) else raw


def main() -> None:
    import warnings

    import databento as db

    warnings.filterwarnings("ignore", message=".*inplace keyword.*")   # bruit interne databento / pandas

    from xauof.assistant import mt5_live

    p = argparse.ArgumentParser(description="Niveaux du jour pour l'assistant manuel")
    p.add_argument("--day", default=None, help="journée à préparer (défaut : aujourd'hui, New York)")
    args = p.parse_args()
    cfg = load_config()
    tz = cfg["strategy"]["timezone"]
    day = pd.Timestamp(args.day) if args.day else pd.Timestamp.now(tz=tz).normalize().tz_localize(None)

    client = db.Historical(secret("DATABENTO_API_KEY"))
    mt5 = mt5_live.connect()
    try:
        for prev in previous_weekdays(day):
            trades = fetch_session(cfg, client, prev)
            if len(trades) == 0:
                print(f"  {prev:%Y-%m-%d} : pas de séance GC (jour férié ?), on remonte d'un jour")
                continue
            a, b = (ny_time(prev, h, tz) for h in cfg["sessions"]["rth"])
            xau = mt5_live.xau_m1(mt5, cfg, a - pd.Timedelta(minutes=5), b + pd.Timedelta(minutes=5))
            levels, reason = lv.compute_levels(cfg, trades, xau, prev)
            if levels is None:
                raise SystemExit(f"Niveaux impossibles pour {prev:%Y-%m-%d} : {reason}")
            path = lv.save_levels(cfg, day, levels)
            print(f"Niveaux de la séance du {prev:%Y-%m-%d} (basis {levels['basis']:+.2f} $) pour le {day:%Y-%m-%d} :")
            for name, val in lv.level_lines(levels):
                print(f"  {val:10.2f}  {name}")
            print(f"Enregistré : {path}")
            break
        else:
            raise SystemExit("Aucune séance GC trouvée sur les 5 derniers jours ouvrés")
    finally:
        mt5.shutdown()
    _, msg = ensure_fresh(cfg, pd.Timestamp.now(tz="UTC"))
    print(f"Calendrier : {msg}")
    print(f"Tableau de bord : streamlit run {ROOT / 'xauof' / 'assistant' / 'app.py'}")


if __name__ == "__main__":
    main()
