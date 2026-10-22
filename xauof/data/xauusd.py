"""Historique XAUUSD M1 depuis MT5 (prix du broker, ex. FTMO). Windows uniquement.

MT5 date ses bougies en heure SERVEUR du broker : on les convertit en UTC avec
`broker.server_timezone` (à vérifier ; la phase 3 contrôlera l'alignement avec GC).
L'historique M1 disponible dépend du broker et du réglage « Max bars in chart ».

Usage :
    python -m xauof.data.xauusd --start 2025-10-01
"""
from __future__ import annotations

import argparse
import os

import pandas as pd

from xauof.config import ROOT, load_config
from xauof.data import store


def normalize_rates(rates, server_tz: str) -> pd.DataFrame:
    """Tableau MT5 (time, open, high, low, close, tick_volume, spread, ...) → DataFrame UTC."""
    df = pd.DataFrame(rates)
    if df.empty:
        return df
    server_time = pd.to_datetime(df["time"], unit="s")  # heure serveur, sans fuseau
    idx = server_time.dt.tz_localize(server_tz, ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC")
    df.index = pd.DatetimeIndex(idx, name="time")
    df = df[df.index.notna()]
    return df[["open", "high", "low", "close", "tick_volume", "spread"]].sort_index()


def fetch(cfg: dict, mt5, start, end) -> None:
    symbol, tz = cfg["symbols"]["broker"], cfg["broker"]["server_timezone"]
    directory = store.xauusd_m1_dir(cfg)
    for c in store.month_chunks(start, end):
        path = store.month_file(directory, c.month)
        if c.complete and path.exists():
            continue
        # les bornes demandées à MT5 sont aussi en heure serveur
        rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1,
                                     c.start.tz_convert(tz).tz_localize(None).to_pydatetime(),
                                     c.end.tz_convert(tz).tz_localize(None).to_pydatetime())
        df = normalize_rates(rates if rates is not None else [], tz)
        if df.empty:
            print(f"  {c.month:%Y-%m} : aucune bougie ({mt5.last_error()})")
            continue
        store.save(df, path)
        print(f"  {c.month:%Y-%m} : {len(df):,} bougies M1 enregistrées")


def main() -> None:
    cfg = load_config()
    p = argparse.ArgumentParser(description="Télécharge l'historique XAUUSD M1 depuis MT5")
    p.add_argument("--start", required=True)
    p.add_argument("--end", default=None, help="défaut : maintenant")
    args = p.parse_args()

    import MetaTrader5 as mt5
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    kwargs = {k: v for k, v in {
        "path": os.getenv("MT5_PATH"),
        "login": int(os.getenv("MT5_LOGIN")) if os.getenv("MT5_LOGIN") else None,
        "password": os.getenv("MT5_PASSWORD"),
        "server": os.getenv("MT5_SERVER"),
    }.items() if v}
    if not mt5.initialize(**kwargs):
        raise SystemExit(f"MT5 : connexion impossible ({mt5.last_error()}). Le terminal est-il ouvert ?")
    try:
        if not mt5.symbol_select(cfg["symbols"]["broker"], True):
            raise SystemExit(f"Symbole {cfg['symbols']['broker']} introuvable : corrige symbols.broker dans config.yaml")
        fetch(cfg, mt5, args.start, args.end or pd.Timestamp.now(tz="UTC"))
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
