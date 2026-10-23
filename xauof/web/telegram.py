"""Configuration et test des alertes Telegram.

    python -m xauof.web.telegram     # trouve ton TELEGRAM_CHAT_ID, puis envoie un message de test

Le jeton du bot (TELEGRAM_BOT_TOKEN) se met dans .env, jamais ailleurs : quiconque
l'a peut envoyer des messages au nom de ton bot.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

from xauof.config import ROOT

API = "https://api.telegram.org/bot{token}/{method}"


def chat_ids(updates: dict) -> list[tuple[int, str]]:
    """(chat_id, nom) des conversations qui ont écrit au bot, sans doublon."""
    seen, out = set(), []
    for u in updates.get("result", []):
        chat = (u.get("message") or u.get("edited_message") or {}).get("chat")
        if chat and chat["id"] not in seen:
            seen.add(chat["id"])
            out.append((chat["id"], chat.get("first_name") or chat.get("title") or chat.get("username") or "?"))
    return out


def main() -> None:
    import requests

    load_dotenv(ROOT / ".env", override=True)
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN manquant dans .env (étape 1 : créer le bot avec @BotFather)")
    me = requests.get(API.format(token=token, method="getMe"), timeout=10).json()
    if not me.get("ok"):
        raise SystemExit(f"Jeton refusé par Telegram ({me.get('description')}) : vérifie TELEGRAM_BOT_TOKEN")
    print(f"Bot reconnu : @{me['result']['username']}")
    if not chat:
        ids = chat_ids(requests.get(API.format(token=token, method="getUpdates"), timeout=10).json())
        if not ids:
            raise SystemExit("Aucun message reçu : ouvre ton bot dans Telegram, appuie sur Démarrer (ou envoie "
                             "« bonjour »), puis relance cette commande.")
        print("Mets cette ligne dans .env :")
        for cid, name in ids:
            print(f"  TELEGRAM_CHAT_ID={cid}      ({name})")
        return
    r = requests.post(API.format(token=token, method="sendMessage"), timeout=10,
                      json={"chat_id": chat, "text": "🟡 XAUUSD OF\nTest réussi : les alertes Setup A+ arriveront ici."}).json()
    if not r.get("ok"):
        raise SystemExit(f"Envoi refusé ({r.get('description')}) : vérifie TELEGRAM_CHAT_ID")
    print("Message de test envoyé : regarde ton Telegram ✅")


if __name__ == "__main__":
    main()
