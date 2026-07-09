"""Envoi d'alertes Telegram avec retry + backoff exponentiel.

Le scanner ALERTE seulement — aucune exécution d'ordre, jamais.
"""

from __future__ import annotations

import logging
import time

import requests

from smc.core import Setup

log = logging.getLogger("smc.telegram")

_API = "https://api.telegram.org/bot{token}/sendMessage"
_RETRIES = 4
_BACKOFF = 2  # 2s, 4s, 8s, 16s


def send_message(token: str, chat_id: str, text: str) -> bool:
    """True si envoyé. Ne lève jamais : les erreurs réseau sont loggées et
    réessayées, puis abandonnées (le scanner ne doit pas crasher pour ça)."""
    if not token or not chat_id:
        log.error("Telegram non configuré (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
        return False
    for attempt in range(1, _RETRIES + 1):
        try:
            r = requests.post(
                _API.format(token=token),
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                timeout=15,
            )
            if r.ok:
                return True
            log.warning("Telegram HTTP %s : %s", r.status_code, r.text[:200])
        except requests.RequestException as exc:
            log.warning("Telegram tentative %d/%d échouée : %s", attempt, _RETRIES, exc)
        if attempt < _RETRIES:
            time.sleep(_BACKOFF ** attempt)
    log.error("Alerte Telegram abandonnée après %d tentatives", _RETRIES)
    return False


def format_setup(setup: Setup) -> str:
    """Message d'alerte lisible sur mobile."""
    arrow = "🟢 LONG" if setup.direction == "long" else "🔴 SHORT"
    sweep_line = (
        f"Sweep {setup.sweep.side} @ {setup.sweep.level:.5f} ({setup.sweep.level_kind})"
        if setup.sweep else "Pas de sweep (zone seule)"
    )
    return (
        f"<b>{arrow} {setup.pair}</b> — setup AMD détecté\n"
        f"Zone : {setup.zone.kind} [{setup.zone.bottom:.5f} ; {setup.zone.top:.5f}]\n"
        f"{sweep_line}\n"
        f"Entrée ≈ {setup.entry:.5f}\n"
        f"SL : {setup.sl:.5f}\n"
        f"TP : {setup.tp:.5f} (R:R {setup.rr:.1f})\n"
        f"{setup.time:%Y-%m-%d %H:%M} (heure serveur)\n\n"
        f"⚠️ Alerte informative — décision et exécution manuelles uniquement.\n"
        f"⚠️ SMC = méthode sans edge statistiquement prouvé."
    )
