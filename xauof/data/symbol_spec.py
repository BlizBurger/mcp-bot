"""Lit les spécifications réelles de XAUUSD dans MT5 et les enregistre dans data/xauusd/spec.json.

Usage (MT5 ouvert et connecté) :
    python -m xauof.data.symbol_spec
"""
from __future__ import annotations

import json

from xauof.config import load_config
from xauof.data import store


def main() -> None:
    import MetaTrader5 as mt5

    cfg = load_config()
    if not mt5.initialize():
        raise SystemExit(f"MT5 : connexion impossible ({mt5.last_error()})")
    try:
        info = mt5.symbol_info(cfg["symbols"]["broker"])
        if info is None:
            raise SystemExit(f"Symbole {cfg['symbols']['broker']} introuvable")
        spec = {
            "contract_size": info.trade_contract_size,
            "tick_size": info.trade_tick_size,
            "tick_value": info.trade_tick_value,
            "volume_min": info.volume_min,
            "volume_step": info.volume_step,
            "volume_max": info.volume_max,
            "profit_currency": info.currency_profit,
            "digits": info.digits,
        }
    finally:
        mt5.shutdown()
    path = store.data_dir(cfg) / "xauusd" / "spec.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    print(json.dumps(spec, indent=2))
    print(f"Enregistré : {path}")


if __name__ == "__main__":
    main()
