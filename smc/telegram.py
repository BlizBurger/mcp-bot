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
_PHOTO_API = "https://api.telegram.org/bot{token}/sendPhoto"
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


def send_photo(token: str, chat_id: str, photo_path: str, caption: str) -> bool:
    """Envoie une image (le graphique du setup) avec l'alerte en légende.
    Ne lève jamais ; retry + backoff comme send_message. La légende Telegram
    est limitée à 1024 caractères — tronquée au besoin."""
    if not token or not chat_id:
        log.error("Telegram non configuré")
        return False
    caption = caption[:1024]
    for attempt in range(1, _RETRIES + 1):
        try:
            with open(photo_path, "rb") as fh:
                r = requests.post(
                    _PHOTO_API.format(token=token),
                    data={"chat_id": chat_id, "caption": caption,
                          "parse_mode": "HTML"},
                    files={"photo": fh}, timeout=30,
                )
            if r.ok:
                return True
            log.warning("Telegram photo HTTP %s : %s", r.status_code, r.text[:200])
        except (requests.RequestException, OSError) as exc:
            log.warning("Telegram photo tentative %d/%d : %s", attempt, _RETRIES, exc)
        if attempt < _RETRIES:
            time.sleep(_BACKOFF ** attempt)
    return False


def format_setup(setup: Setup, exits_cfg: dict | None = None,
                 sizing: dict | None = None) -> str:
    """Message d'alerte épuré, prêt à exécuter à la main.

    `sizing` (optionnel, calculé en live via MT5) : {lots, risk_amount,
    stop_pips, currency, risk_pct} — la taille de position à passer.
    """
    head = "🟢 NOUVEAU SIGNAL" if setup.direction == "long" else "🔴 NOUVEAU SIGNAL"
    dir_txt = setup.direction.upper()
    limit = " (LIMITE)" if setup.entry_is_limit else ""

    conf = ", ".join(setup.confluences) if setup.confluences else "aucune"
    details = f"🔍 Détails : {conf}\n" if setup.confluences else ""

    lines = [
        f"<b>{head} — {setup.pair} {dir_txt}</b>",
        "━━━━━━━━━━━━━━━",
        "",
        f"📍 Entrée : {setup.entry:.5f}{limit}",
        f"🛑 Stop Loss : {setup.sl:.5f}",
        f"🎯 Take Profit : {setup.tp:.5f}",
        f"⚖️ R:R : {setup.rr:.2f}",
        f"✨ Score confluence : {setup.score}/{setup.max_score}",
    ]
    if details:
        lines.append(details.rstrip("\n"))

    if sizing:
        cur = sizing.get("currency", "")
        lines += [
            "",
            f"💰 Taille position : {sizing['lots']:.2f} lots",
            f"⚠️ Risque : {sizing.get('risk_pct', 0):.1f}% "
            f"({sizing['risk_amount']:.2f}{cur})",
            f"📏 Distance stop : {sizing['stop_pips']:.1f} pips",
        ]

    # Plan de gestion compact (une ligne)
    plan_bits = []
    if exits_cfg:
        pr = float(exits_cfg.get("partial_at_r", 0) or 0)
        if pr > 0:
            frac = float(exits_cfg.get("partial_fraction", 0.5))
            plan_bits.append(f"{frac:.0%} à +{pr:.0f}R")
        if float(exits_cfg.get("breakeven_after_r", 0) or 0) > 0:
            plan_bits.append("puis SL au BE")
        mh = int(exits_cfg.get("max_holding_bars", 0) or 0)
        if mh > 0:
            plan_bits.append(f"coupe ~{mh // 96 or 1}j")
    if plan_bits:
        lines += ["", f"📋 Gestion : {', '.join(plan_bits)}"]

    lines += ["", "ℹ️ Alerte informative — exécution manuelle. "
              "SMC : pas d'edge prouvé."]
    return "\n".join(lines)
