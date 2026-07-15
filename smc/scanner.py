"""Scanner live : scanne les paires toutes les 5 minutes, alerte sur Telegram.

AUCUNE exécution d'ordre — alertes uniquement, la décision reste manuelle.

Lancement : python -m smc.scanner

Robustesse :
  - chaque cycle et chaque paire sont isolés dans un try/except : une erreur
    est loggée puis le scan continue, le processus ne meurt jamais en silence ;
  - la connexion MT5 est vérifiée/rétablie à chaque accès aux données ;
  - les envois Telegram sont réessayés avec backoff ;
  - un fichier de statut JSON est écrit à chaque cycle pour le dashboard.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path

from smc import WARNINGS
from smc.config import load_config, load_env
from smc.core import correlation, htf_bias, news_blackout, position_size
from smc.strategies import get_strategy
from smc.db import alerts_sent_today, connect, insert_setup, setup_already_stored
from smc.logging_setup import log_warnings_banner, setup_logging
from smc.mt5_client import MT5Client, MT5Error
from smc.news import load_news
from smc.telegram import format_setup, send_message

log = logging.getLogger("smc.scanner")


def write_status(path: str, **fields) -> None:
    """Statut consommé par le dashboard. Ne doit jamais faire tomber le scan."""
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(
            {"updated_at": datetime.now().isoformat(), **fields},
            ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        log.warning("Impossible d'écrire le fichier de statut : %s", exc)


_weekend_reminder_date = None  # dernier jour où le rappel week-end a été envoyé


def maybe_weekend_close_reminder(cfg: dict, env: dict, server_now) -> None:
    """Le vendredi, ~30 min avant la clôture hebdo : rappel Telegram de fermer
    toute position ouverte (le bot n'exécute jamais d'ordre lui-même)."""
    global _weekend_reminder_date
    mh = cfg.get("market_hours", {}) or {}
    wc, fc = mh.get("week_close_friday"), mh.get("force_close_minutes_before")
    if not wc or not fc or server_now.weekday() != 4:
        return
    h, m = map(int, str(wc).split(":"))
    threshold = h * 60 + m - int(fc) - 10  # petite marge d'avance
    if (server_now.hour * 60 + server_now.minute) >= threshold and \
            _weekend_reminder_date != server_now.date():
        sent = send_message(
            env["telegram_token"], env["telegram_chat_id"],
            "⏰ <b>RAPPEL WEEK-END</b> : clôture du marché dans ~30-40 min "
            "(heure serveur). Ferme toute position encore ouverte — règle : "
            "aucune position gardée pendant le week-end, gagnante ou perdante.")
        if sent:
            _weekend_reminder_date = server_now.date()
            log.info("Rappel de clôture week-end envoyé")


def scan_once(client: MT5Client, cfg: dict, conn, env: dict) -> list[str]:
    """Un cycle complet de scan. Retourne les paires alertées."""
    pairs = cfg["pairs"]
    s = cfg["strategy"]
    news = load_news(cfg["news"]["file"])
    alerted: list[str] = []

    # Pré-charger H4/LTF de toutes les paires (aussi utilisé par le filtre corrélation)
    data: dict[str, dict] = {}
    need_d1 = bool(s.get("require_d1_alignment"))
    for pair in pairs:
        try:
            data[pair] = {
                "htf": client.get_rates(pair, cfg["timeframes"]["htf"],
                                        cfg["scanner"]["history_bars_htf"]),
                "ltf": client.get_rates(pair, cfg["timeframes"]["ltf"],
                                        cfg["scanner"]["history_bars_ltf"]),
                "d1": client.get_rates(pair, "D1",
                                       cfg["scanner"].get("history_bars_d1", 60))
                if need_d1 else None,
            }
        except MT5Error as exc:
            log.error("Données indisponibles pour %s : %s", pair, exc)

    # Rappel week-end basé sur l'heure SERVEUR (dernière bougie M15 reçue)
    for d in data.values():
        if d.get("ltf") is not None and len(d["ltf"]):
            maybe_weekend_close_reminder(cfg, env, d["ltf"]["time"].iloc[-1])
            break

    for pair, d in data.items():
        try:
            now = datetime.now()

            # Filtre news : blackout 30 min avant/après une High impact
            ev = news_blackout(news, pair, now, cfg["news"]["blackout_minutes"])
            if ev:
                log.info("%s ignoré : blackout news %s %s @ %s",
                         pair, ev.currency, ev.name, ev.time)
                continue

            pip = client.pip_size(pair)
            strategy_fn = get_strategy(cfg.get("strategy_name", "amd_asian"))
            setup = strategy_fn(pair, d, cfg, pip)
            if setup is None:
                continue

            # Filtre corrélation : paire fortement corrélée avec biais H4 opposé
            my_bias = "bullish" if setup.direction == "long" else "bearish"
            opposite = "bearish" if my_bias == "bullish" else "bullish"
            vetoed = False
            for other, od in data.items():
                if other == pair:
                    continue
                corr = correlation(d["ltf"]["close"], od["ltf"]["close"],
                                   s["correlation_window"])
                if abs(corr) > s["correlation_threshold"]:
                    other_bias = htf_bias(od["htf"], k=s.get("swing_k", 2))
                    expected = my_bias if corr > 0 else opposite
                    if other_bias != "neutral" and other_bias != expected:
                        log.info("%s ignoré : %s corrélé %.2f avec biais opposé (%s)",
                                 pair, other, corr, other_bias)
                        vetoed = True
                        break
            if vetoed:
                continue

            # Anti-doublon : un setup stocké max par paire et par jour
            if setup_already_stored(conn, pair, now.date()):
                continue

            # Taille de position (via MT5, précise au broker) pour l'alerte
            sizing = None
            try:
                acc = cfg.get("account", {})
                pvl = client.pip_value_per_lot(pair, pip)
                specs = client.volume_specs(pair)
                sizing = position_size(
                    acc.get("balance", 10000), acc.get("risk_pct", 1.0),
                    setup.entry, setup.sl, pip, pvl, **specs)
                if sizing:
                    sizing["currency"] = acc.get("currency", "")
                    sizing["risk_pct"] = acc.get("risk_pct", 1.0)
            except Exception as exc:  # noqa: BLE001 — l'alerte part même sans sizing
                log.warning("Taille de position non calculée pour %s : %s", pair, exc)

            # Règle 1 trade/jour : seul le 1er setup du jour part sur Telegram,
            # les suivants sont stockés en base (alerted=0) pour analyse.
            can_alert = alerts_sent_today(conn) < cfg["scanner"]["max_telegram_alerts_per_day"]
            sent = False
            if can_alert:
                sent = send_message(env["telegram_token"], env["telegram_chat_id"],
                                    format_setup(setup, cfg.get("exits"), sizing))
            insert_setup(conn, setup, alerted=sent)
            log.info("SETUP %s %s : entrée %.5f SL %.5f TP %.5f — %s",
                     pair, setup.direction, setup.entry, setup.sl, setup.tp,
                     "ALERTÉ" if sent else "stocké sans alerte (quota 1/jour atteint)")
            if sent:
                alerted.append(pair)
        except Exception:  # noqa: BLE001 — une paire ne doit pas tuer le cycle
            log.exception("Erreur pendant le scan de %s", pair)
    return alerted


def main() -> None:
    cfg = load_config()
    env = load_env()
    setup_logging(cfg["paths"]["logs"], "scanner")
    log_warnings_banner(log)
    log.info("Scanner SMC démarré — stratégie: %s — alertes uniquement, "
             "aucune exécution d'ordre.", cfg.get("strategy_name", "amd_asian"))

    conn = connect(cfg["paths"]["db"])
    client = MT5Client(env)
    client.connect()

    interval = cfg["scanner"]["interval_seconds"]
    try:
        while True:
            start = time.monotonic()
            try:
                alerted = scan_once(client, cfg, conn, env)
                write_status(cfg["paths"]["status"],
                             mt5_connected=client.is_connected(),
                             last_scan=datetime.now().isoformat(),
                             pairs=cfg["pairs"], alerted_this_cycle=alerted,
                             warnings=WARNINGS)
            except Exception:  # noqa: BLE001 — le scanner ne meurt jamais en silence
                log.exception("Erreur inattendue pendant le cycle de scan")
                write_status(cfg["paths"]["status"],
                             mt5_connected=client.is_connected(),
                             last_scan=datetime.now().isoformat(),
                             last_error=datetime.now().isoformat())
            elapsed = time.monotonic() - start
            time.sleep(max(1.0, interval - elapsed))
    except KeyboardInterrupt:
        log.info("Arrêt demandé (Ctrl+C)")
    finally:
        client.shutdown()
        conn.close()


if __name__ == "__main__":
    main()
