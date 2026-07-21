"""Indicateurs et briques communes aux stratégies "classiques" (à base
d'indicateurs plutôt que de structure SMC)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from smc.core import Setup, Zone, atr, calendar_blocked, htf_bias, \
    market_entry_blocked


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    """RSI (moyenne mobile simple des gains/pertes). Perte moyenne nulle
    (hausse continue) => RSI 100 ; gain moyen nul (baisse continue) => RSI 0."""
    delta = s.diff()
    up = delta.clip(lower=0).rolling(n).mean()
    down = (-delta.clip(upper=0)).rolling(n).mean()
    out = pd.Series(np.nan, index=s.index)
    valid = up.notna() & down.notna()
    both_zero = valid & (up == 0) & (down == 0)
    no_loss = valid & (down == 0) & (up > 0)
    normal = valid & (down > 0)
    out[normal] = 100 - 100 / (1 + up[normal] / down[normal])
    out[no_loss] = 100.0
    out[both_zero] = 50.0
    return out


def blocked(now: datetime, cfg: dict) -> bool:
    """Fenêtres interdites communes (calendrier + heures de marché)."""
    return bool(calendar_blocked(now, cfg.get("calendar")) or
                market_entry_blocked(now, cfg.get("market_hours")))


def htf_trend_ok(data: dict, cfg: dict, direction: str, require: bool) -> bool:
    """Si `require`, le biais H4 doit correspondre au sens du trade."""
    if not require:
        return True
    bias = htf_bias(data["htf"], k=cfg["strategy"].get("swing_k", 2))
    return bias == ("bullish" if direction == "long" else "bearish")


def signal_setup(pair: str, direction: str, entry: float, atr_val: float,
                 sb: dict, cfg: dict, now: datetime, zone_kind: str,
                 strategy_name: str, pip: float) -> Optional[Setup]:
    """Construit un Setup à entrée MARCHÉ avec SL en ATR et TP = R:R × risque."""
    long = direction == "long"
    sl_dist = sb.get("atr_sl_mult", 1.5) * atr_val
    if sl_dist <= 0:
        return None
    rr = sb.get("risk_reward", 2.0)
    sl = entry - sl_dist if long else entry + sl_dist
    tp = entry + rr * sl_dist if long else entry - rr * sl_dist
    if abs(entry - sl) < cfg["strategy"].get("min_risk_pips", 0.0) * pip:
        return None
    zone = Zone(zone_kind, "bullish" if long else "bearish",
                max(entry, sl), min(entry, sl), 0)
    return Setup(pair=pair, direction=direction, zone=zone, sweep=None,
                 entry=float(entry), sl=float(sl), tp=float(tp), rr=float(rr),
                 time=now, entry_is_limit=False, strategy=strategy_name,
                 max_score=0, comments=[f"{zone_kind} {direction}",
                                        f"SL {sb.get('atr_sl_mult',1.5)}×ATR, R:R {rr}"])
