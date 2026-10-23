"""Historique XAUUSD M1 depuis MT5 (prix du broker, ex. FTMO). Windows uniquement.

MT5 date ses bougies en heure SERVEUR du broker : on les convertit en UTC avec
`broker.server_clock`. FTMO suit la convention « New York + 7 h » (UTC+2 l'hiver,
UTC+3 pendant l'heure d'été AMÉRICAINE) : vérifié par `python -m xauof.bridge.check`.
L'historique M1 disponible dépend du broker et du réglage « Max bars in chart ».

Usage :
    python -m xauof.data.xauusd --start 2025-10-01
    python -m xauof.data.xauusd --start 2025-10-01 --force   # retélécharge tout
"""
from __future__ import annotations

import argparse
import os

import pandas as pd

from xauof.config import ROOT, load_config
from xauof.data import store


def _parse_clock(spec: str) -> tuple[str, pd.Timedelta]:
    """"America/New_York+7" → (fuseau, +7 h) ; "Europe/Athens" → (fuseau, 0)."""
    zone, sign, hours = spec, 1, "0"
    for sep in ("+", "-"):
        head, _, tail = spec.rpartition(sep)
        if head and tail.replace(".", "", 1).isdigit():
            zone, sign, hours = head, (1 if sep == "+" else -1), tail
            break
    return zone, sign * pd.Timedelta(hours=float(hours))


def server_to_utc(server_time: pd.Series, spec: str) -> pd.Series:
    zone, offset = _parse_clock(spec)
    local = server_time - offset
    return local.dt.tz_localize(zone, ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC")


def utc_to_server(ts: pd.Timestamp, spec: str) -> pd.Timestamp:
    zone, offset = _parse_clock(spec)
    return ts.tz_convert(zone).tz_localize(None) + offset


def normalize_rates(rates, server_clock: str) -> pd.DataFrame:
    """Tableau MT5 (time, open, high, low, close, tick_volume, spread, ...) → DataFrame UTC."""
    df = pd.DataFrame(rates)
    if df.empty:
        return df
    server_time = pd.to_datetime(df["time"], unit="s")  # heure serveur, sans fuseau
    df.index = pd.DatetimeIndex(server_to_utc(server_time, server_clock), name="time")
    df = df[df.index.notna()]
    return df[["open", "high", "low", "close", "tick_volume", "spread"]].sort_index()


def fetch(cfg: dict, mt5, start, end, force: bool = False) -> None:
    symbol, clock = cfg["symbols"]["broker"], cfg["broker"]["server_clock"]
    directory = store.xauusd_m1_dir(cfg)
    for c in store.month_chunks(start, end):
        path = store.month_file(directory, c.month)
        if c.complete and path.exists() and not force:
            continue
        # les bornes demandées à MT5 sont aussi en heure serveur
        rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1,
                                     utc_to_server(c.start, clock).to_pydatetime(),
                                     utc_to_server(c.end, clock).to_pydatetime())
        df = normalize_rates(rates if rates is not None else [], clock)
        # MT5 inclut la bougie située exactement à la borne de fin : chaque fichier
        # mensuel ne garde que ses propres minutes [début, fin)
        df = df[(df.index >= c.start) & (df.index < c.end)] if not df.empty else df
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
    p.add_argument("--force", action="store_true", help="retélécharge aussi les mois déjà en cache")
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
        fetch(cfg, mt5, args.start, args.end or pd.Timestamp.now(tz="UTC"), args.force)
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
