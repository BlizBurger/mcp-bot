"""Lecture MT5 pour l'assistant (Windows, terminal ouvert). Aucune fonction d'ordre ici."""
from __future__ import annotations

import os

import pandas as pd
from dotenv import load_dotenv

from xauof.config import ROOT
from xauof.data.xauusd import mt5_time, normalize_rates


def connect():
    import MetaTrader5 as mt5

    load_dotenv(ROOT / ".env")
    kwargs = {k: v for k, v in {
        "path": os.getenv("MT5_PATH"),
        "login": int(os.getenv("MT5_LOGIN")) if os.getenv("MT5_LOGIN") else None,
        "password": os.getenv("MT5_PASSWORD"),
        "server": os.getenv("MT5_SERVER"),
    }.items() if v}
    if not mt5.initialize(**kwargs):
        raise RuntimeError(f"MT5 : connexion impossible ({mt5.last_error()}). Le terminal est-il ouvert ?")
    return mt5


def xau_m1(mt5, cfg: dict, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    clock, symbol = cfg["broker"]["server_clock"], cfg["symbols"]["broker"]
    mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1, mt5_time(start, clock), mt5_time(end, clock))
    df = normalize_rates(rates if rates is not None else [], clock)
    return df[(df.index >= start) & (df.index < end)] if not df.empty else df


def account(mt5, cfg: dict, now: pd.Timestamp) -> dict:
    """Solde, equity et résultat réalisé depuis minuit Europe/Prague (commissions et swaps compris)."""
    info = mt5.account_info()
    tz = cfg["risk"]["daily_reset_timezone"]
    midnight = now.tz_convert(tz).normalize().tz_convert("UTC")
    clock = cfg["broker"]["server_clock"]
    deals = mt5.history_deals_get(mt5_time(midnight, clock), mt5_time(now + pd.Timedelta(minutes=1), clock)) or []
    trades = [d for d in deals if d.type in (mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_SELL)]   # hors dépôts / retraits
    realized = sum(d.profit + d.commission + d.swap + getattr(d, "fee", 0.0) for d in trades)
    return {"balance": float(info.balance), "equity": float(info.equity), "realized_today": float(realized)}
