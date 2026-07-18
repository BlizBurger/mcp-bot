"""Envoie une alerte de DÉMONSTRATION sur Telegram, avec le graphique au look
MT5 et les vraies bougies M15 du broker.

Usage : python -m smc.test_alert [PAIR]   (défaut : EURUSD)

Construit un setup plausible autour du prix courant — c'est un EXEMPLE pour
visualiser le rendu de l'alerte, PAS un vrai signal (préfixe [TEST]).
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from smc.charts import render_setup_png
from smc.config import load_config, load_env
from smc.core import Setup, Sweep, Zone, position_size
from smc.mt5_client import MT5Client
from smc.telegram import format_setup, send_photo, send_message


def main() -> None:
    pair = (sys.argv[1] if len(sys.argv) > 1 else "EURUSD").upper()
    cfg, env = load_config(), load_env()
    client = MT5Client(env)
    client.connect()
    ltf = client.get_rates(pair, cfg["timeframes"]["ltf"], 120)
    pip = client.pip_size(pair)
    last = float(ltf["close"].iloc[-1])

    # setup SHORT d'exemple construit autour du prix courant
    entry = last + 6 * pip
    sl = entry + 11 * pip
    tp = entry - 22 * pip
    setup = Setup(
        pair=pair, direction="short",
        zone=Zone("FVG", "bearish", entry + 4 * pip, entry - 2 * pip, 0),
        sweep=Sweep(0, "high", entry + 9 * pip, entry + 13 * pip, "h4"),
        entry=entry, sl=sl, tp=tp, rr=2.0, time=ltf["time"].iloc[-1],
        entry_is_limit=True, score=2, max_score=7,
        confluences=["FVG", "Équilibre"], strategy="sweep_bos")

    sizing = None
    try:
        acc = cfg.get("account", {})
        pvl = client.pip_value_per_lot(pair, pip)
        sizing = position_size(acc.get("balance", 10000), acc.get("risk_pct", 1.0),
                               entry, sl, pip, pvl, **client.volume_specs(pair))
        if sizing:
            sizing["currency"] = acc.get("currency", "")
            sizing["risk_pct"] = acc.get("risk_pct", 1.0)
    except Exception as exc:  # noqa: BLE001
        print("sizing indisponible :", exc)

    caption = "[TEST — exemple, pas un vrai signal]\n" + \
        format_setup(setup, cfg.get("exits"), sizing)
    img = Path(cfg["paths"]["reports"]) / "alerts" / f"TEST_{pair}.png"
    img.parent.mkdir(parents=True, exist_ok=True)

    ok = render_setup_png(ltf, setup, img)
    if ok:
        sent = send_photo(env["telegram_token"], env["telegram_chat_id"],
                          str(img), caption)
    else:
        sent = send_message(env["telegram_token"], env["telegram_chat_id"], caption)
    client.shutdown()
    print("Alerte de test envoyée :", sent, "| image :", ok, "| fichier :", img)


if __name__ == "__main__":
    main()
