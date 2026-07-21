"""Registre des stratégies disponibles.

Chaque stratégie expose la même interface :
    find_setup(pair, data, cfg, pip, now=None) -> Optional[Setup]
où `data` est un dict {"htf": DataFrame H4, "ltf": DataFrame M15,
"d1": DataFrame Daily ou None}.
"""

from smc.strategies.amd_asian import find_setup as _amd
from smc.strategies.sweep_bos import find_setup as _sweep_bos
from smc.strategies.classic import (
    find_bollinger, find_donchian, find_ema_cross, find_ema_rsi,
)

STRATEGIES = {
    "amd_asian": _amd,
    "sweep_bos": _sweep_bos,
    "ema_rsi": find_ema_rsi,
    "donchian": find_donchian,
    "bollinger": find_bollinger,
    "ema_cross": find_ema_cross,
}


def get_strategy(name: str):
    if name not in STRATEGIES:
        raise KeyError(f"Stratégie inconnue : {name!r} — choix : {sorted(STRATEGIES)}")
    return STRATEGIES[name]
