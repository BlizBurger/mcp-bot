"""Configuration et test des alertes Telegram.

    python -m xauof.web.telegram     # trouve ton TELEGRAM_CHAT_ID, puis envoie un message de test

Le jeton du bot (TELEGRAM_BOT_TOKEN) se met dans .env, jamais ailleurs : quiconque
l'a peut envoyer des messages au nom de ton bot.
"""
from __future__ import annotations

import os
import re

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


def token_diagnostic(token: str) -> list[str]:
    """Problèmes de forme du jeton, SANS jamais l'afficher."""
    problems = []
    if token != token.strip():
        problems.append("espaces avant ou après le jeton")
    if any(c in token for c in "\"' "):
        problems.append("guillemets ou espaces dans le jeton")
    t = token.strip().strip("\"'")
    if ":" not in t:
        problems.append("pas de « : » : ce n'est pas un jeton (identifiant du bot collé à la place ?)")
    else:
        num, key = t.split(":", 1)
        if not num.isdigit() or not 8 <= len(num) <= 11:
            problems.append(f"partie avant « : » incorrecte ({len(num)} caractères, attendu 8 à 11 chiffres)")
        if not re.fullmatch(r"[A-Za-z0-9_-]{35}", key):
            problems.append(f"partie après « : » incorrecte ({len(key)} caractères, attendu 35) : jeton coupé ?")
    return problems


def clean_chat_id(raw: str | None) -> str | None:
    """Garde seulement l'identifiant numérique (ex. « 6291755478  (Alex) » → « 6291755478 »)."""
    m = re.search(r"-?\d{5,}", raw or "")
    return m.group(0) if m else None


def main() -> None:
    import requests

    load_dotenv(ROOT / ".env", override=True)
    token, raw_chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    chat = clean_chat_id(raw_chat)
    if raw_chat and chat != raw_chat.strip():
        print(f"  ⚠ TELEGRAM_CHAT_ID contient du texte en trop : on utilise {chat}. "
              f"Corrige la ligne en « TELEGRAM_CHAT_ID={chat} ».")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN manquant dans .env (étape 1 : créer le bot avec @BotFather)")
    problems = token_diagnostic(token)
    print(f"Jeton lu dans .env : {len(token)} caractères, se termine par « …{token[-3:]} »")
    for p in problems:
        print(f"  ⚠ {p}")
    me = requests.get(API.format(token=token.strip(), method="getMe"), timeout=10).json()
    if not me.get("ok"):
        raise SystemExit(f"Jeton refusé par Telegram ({me.get('description')}). "
                         + ("Corrige les points ci-dessus." if problems else
                            "Le format est bon : le jeton a sans doute été régénéré. Reprends-le dans "
                            "@BotFather → /mybots → ton bot → API Token."))
    print(f"Bot reconnu : @{me['result']['username']}")
    if not chat:
        ids = chat_ids(requests.get(API.format(token=token.strip(), method="getUpdates"), timeout=10).json())
        if not ids:
            raise SystemExit("Aucun message reçu : ouvre ton bot dans Telegram, appuie sur Démarrer (ou envoie "
                             "« bonjour »), puis relance cette commande.")
        print("Mets cette ligne dans .env :")
        for cid, name in ids:
            print(f"  TELEGRAM_CHAT_ID={cid}      ({name})")
        return
    r = requests.post(API.format(token=token.strip(), method="sendMessage"), timeout=10,
                      json={"chat_id": chat, "text": "🟡 XAUUSD OF\nTest réussi : les alertes Setup A+ arriveront ici."}).json()
    if not r.get("ok"):
        raise SystemExit(f"Envoi refusé ({r.get('description')}) avec TELEGRAM_CHAT_ID={chat}. Vide la ligne "
                         "TELEGRAM_CHAT_ID dans .env, écris « bonjour » au bot, puis relance pour le retrouver.")
    print("Message de test envoyé : regarde ton Telegram ✅")


if __name__ == "__main__":
    main()
