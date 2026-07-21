"""Stratégies classiques à base d'indicateurs, toutes comparables sur le même
moteur (entrée marché, SL en ATR, R:R paramétrable) :

  - ema_rsi   : pullback RSI dans le sens de la tendance EMA (momentum)
  - donchian  : cassure de canal type Turtle (suivi de tendance, edge documenté)
  - bollinger : retour à la moyenne depuis une bande (contre-tendance)
  - ema_cross : croisement de moyennes (référence de base du suivi de tendance)

Chaque stratégie accepte un filtre de tendance H4 optionnel (`htf_filter`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from smc.core import Setup, atr
from smc.strategies.indicators import (
    blocked, ema, htf_trend_ok, rsi, signal_setup,
)


def _prep(data: dict, cfg: dict, block: str, min_bars: int,
          now: Optional[datetime]):
    """Garde commune : renvoie (df, sb, now, atr_val) ou None si non tradable."""
    df = data["ltf"]
    if len(df) < min_bars:
        return None
    now = now or df["time"].iloc[-1]
    if blocked(now, cfg):
        return None
    a = atr(df, 14).iloc[-1]
    if pd.isna(a) or a <= 0:
        return None
    return df, cfg.get(block, {}), now, float(a)


def find_ema_rsi(pair, data, cfg, pip, now=None) -> Optional[Setup]:
    prep = _prep(data, cfg, "ema_rsi", 60, now)
    if prep is None:
        return None
    df, sb, now, a = prep
    ef = ema(df["close"], sb.get("ema_fast", 21))
    es = ema(df["close"], sb.get("ema_slow", 50))
    r = rsi(df["close"], sb.get("rsi_period", 14))
    up = ef.iloc[-1] > es.iloc[-1]
    down = ef.iloc[-1] < es.iloc[-1]
    buy, sell = sb.get("rsi_buy", 50), sb.get("rsi_sell", 50)
    entry = float(df["close"].iloc[-1])
    req = sb.get("htf_filter", False)
    # pullback : le RSI repasse au-dessus (long) / en-dessous (short) du seuil
    if up and r.iloc[-2] < buy <= r.iloc[-1] and htf_trend_ok(data, cfg, "long", req):
        return signal_setup(pair, "long", entry, a, sb, cfg, now, "EMA+RSI", "ema_rsi", pip)
    if down and r.iloc[-2] > sell >= r.iloc[-1] and htf_trend_ok(data, cfg, "short", req):
        return signal_setup(pair, "short", entry, a, sb, cfg, now, "EMA+RSI", "ema_rsi", pip)
    return None


def find_donchian(pair, data, cfg, pip, now=None) -> Optional[Setup]:
    n = cfg.get("donchian", {}).get("channel", 20)
    prep = _prep(data, cfg, "donchian", n + 20, now)
    if prep is None:
        return None
    df, sb, now, a = prep
    hi = df["high"].iloc[-n - 1:-1].max()
    lo = df["low"].iloc[-n - 1:-1].min()
    c, pc = float(df["close"].iloc[-1]), float(df["close"].iloc[-2])
    req = sb.get("htf_filter", False)
    if pc <= hi < c and htf_trend_ok(data, cfg, "long", req):
        return signal_setup(pair, "long", c, a, sb, cfg, now, "Breakout", "donchian", pip)
    if pc >= lo > c and htf_trend_ok(data, cfg, "short", req):
        return signal_setup(pair, "short", c, a, sb, cfg, now, "Breakout", "donchian", pip)
    return None


def find_bollinger(pair, data, cfg, pip, now=None) -> Optional[Setup]:
    n = cfg.get("bollinger", {}).get("period", 20)
    prep = _prep(data, cfg, "bollinger", n + 20, now)
    if prep is None:
        return None
    df, sb, now, a = prep
    k = sb.get("std", 2.0)
    mid = df["close"].rolling(n).mean()
    sd = df["close"].rolling(n).std()
    upper, lower = mid + k * sd, mid - k * sd
    c, pc = float(df["close"].iloc[-1]), float(df["close"].iloc[-2])
    req = sb.get("htf_filter", False)
    # retour dans la bande après en être sorti (mean reversion)
    if pc < lower.iloc[-2] and c > lower.iloc[-1] and htf_trend_ok(data, cfg, "long", req):
        return signal_setup(pair, "long", c, a, sb, cfg, now, "MeanRev", "bollinger", pip)
    if pc > upper.iloc[-2] and c < upper.iloc[-1] and htf_trend_ok(data, cfg, "short", req):
        return signal_setup(pair, "short", c, a, sb, cfg, now, "MeanRev", "bollinger", pip)
    return None


def find_ema_cross(pair, data, cfg, pip, now=None) -> Optional[Setup]:
    prep = _prep(data, cfg, "ema_cross", 60, now)
    if prep is None:
        return None
    df, sb, now, a = prep
    ef = ema(df["close"], sb.get("ema_fast", 20))
    es = ema(df["close"], sb.get("ema_slow", 50))
    c = float(df["close"].iloc[-1])
    req = sb.get("htf_filter", False)
    if ef.iloc[-2] <= es.iloc[-2] and ef.iloc[-1] > es.iloc[-1] and htf_trend_ok(data, cfg, "long", req):
        return signal_setup(pair, "long", c, a, sb, cfg, now, "EMAx", "ema_cross", pip)
    if ef.iloc[-2] >= es.iloc[-2] and ef.iloc[-1] < es.iloc[-1] and htf_trend_ok(data, cfg, "short", req):
        return signal_setup(pair, "short", c, a, sb, cfg, now, "EMAx", "ema_cross", pip)
    return None
