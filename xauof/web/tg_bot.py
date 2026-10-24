"""Bot Telegram interactif (gratuit, par interrogation : aucun port ouvert, l'interface doit tourner).

- Sous chaque alerte A+ : « ✅ Je le prends » / « ❌ Je passe » → enregistré dans
  data/journal/alertes_decisions.json ; répondre à l'alerte (ou à la confirmation) ajoute une note.
- Commandes : /etat /setups /niveaux /semaine /pause /reprendre /aide.

Sécurité : seuls les messages venant de TELEGRAM_CHAT_ID sont traités, tout le reste est ignoré.
Lecture seule : aucune commande ne passe d'ordre.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from xauof.data import store
from xauof.web import journal as jr
from xauof.web.setups import plan_text, send_telegram

NY = "America/New_York"
HELP = ("Commandes :\n/etat — prix, contexte, risque du jour, trades, semaine\n/setups — setups A+ en cours et plans\n"
        "/niveaux — niveaux d'hier\n/semaine — bilan de la semaine et décisions sur les alertes\n"
        "/pause — plus d'alertes Telegram jusqu'à demain\n/reprendre — réactive les alertes\n\n"
        "Sous une alerte : ✅ / ❌ pour noter ta décision. Réponds à l'alerte pour ajouter une note.")


def decisions_path(cfg: dict) -> Path:
    return store.data_dir(cfg) / "journal" / "alertes_decisions.json"


class Decisions:
    """Décisions prises sur les alertes (une entrée par alerte), avec notes."""

    def __init__(self, cfg: dict):
        self.path = decisions_path(cfg)

    def load(self) -> list[dict]:
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else []

    def _save(self, items: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")

    def _get(self, items: list[dict], key: str, alert: Optional[dict]) -> dict:
        e = next((x for x in items if x["cle"] == key), None)
        if e is None:
            e = {"cle": key, "texte": (alert or {}).get("texte", ""), "t_alerte": (alert or {}).get("t"),
                 "decision": None, "t_decision": None, "notes": []}
            items.append(e)
        return e

    def decide(self, key: str, decision: str, alert: Optional[dict], now: pd.Timestamp) -> dict:
        items = self.load()
        e = self._get(items, key, alert)
        e["decision"], e["t_decision"] = decision, now.isoformat()
        self._save(items)
        return e

    def note(self, key: str, text: str, alert: Optional[dict], now: pd.Timestamp) -> dict:
        items = self.load()
        e = self._get(items, key, alert)
        e["notes"].append({"t": now.isoformat(), "texte": text})
        self._save(items)
        return e


def fmt_state(s: dict) -> str:
    if "erreur" in s:
        return f"⚠ {s['erreur']}"
    p, a = s["prix"], s.get("compte")
    lines = [f"🕒 {s['heure_ny']} NY · {s['heure_paris']} Paris — {s['fenetre']}",
             f"💰 Prix {p['bid']:.2f} (spread {p['spread_pts']:.0f} pts)",
             f"🧭 Contexte : {s.get('position_va') or '—'}"
             + (f" · niveau proche {s['niveau_proche'][0]} {s['niveau_proche'][1]:.2f}" if s.get("niveau_proche") else ""),
             f"📊 Pression 5 min {s['orderflow']['delta_5min']:+.0f}"
             + (f" · CVD {s['orderflow']['cvd']:+.0f}" if s["orderflow"]["cvd"] is not None else "")]
    if a:
        lines.append(f"🛡 Jour {-a['perte_jour']:+.2f} $ (reste {a['reste_arret_interne']:.0f} $ avant l'arrêt)"
                     f" · trades {a['trades_jour']}/{a['max_trades_jour']}"
                     + (f" · semaine {-a['perte_semaine']:+.2f} $" if a.get("perte_semaine") is not None else ""))
        if a["stop_interne_atteint"]:
            lines.append("⛔ STOP : " + ", ".join(a["stop_raisons"]))
    if s.get("news"):
        n = s["news"][0]
        lines.append(f"📰 Prochaine annonce dans {n['dans_min']} min : {n['annonce']}")
    lines.append(f"🎯 Setups A+ en cours : {len(s.get('setups', []))}")
    return "\n".join(lines)


def fmt_setups(s: dict) -> str:
    if "erreur" in s:
        return f"⚠ {s['erreur']}"
    if not s.get("setups"):
        return "Aucun setup A+ pour l'instant."
    out = []
    for x in s["setups"]:
        z = x["zone"]
        out.append(f"{'▲ ACHAT' if z['sens'] > 0 else '▼ VENTE'} {z['type']} {z['bas']:.2f}-{z['haut']:.2f} "
                   f"({x['etat']}) sur {', '.join(x['niveaux'])}{plan_text(x.get('plan'))}")
    return "\n\n".join(out)


def fmt_levels(s: dict) -> str:
    if not s.get("niveaux"):
        return "Niveaux d'hier absents : lance python -m xauof.assistant.update."
    return f"Niveaux d'hier ({s.get('niveaux_source')}) :\n" + "\n".join(
        f"{x['nom']} : {x['prix']:.2f} ({x['distance']:+.2f} $)" for x in s["niveaux"])


def fmt_week(cfg: dict, now: pd.Timestamp) -> str:
    monday = now.tz_convert(NY).normalize() - pd.Timedelta(days=now.tz_convert(NY).weekday())
    trades = [t for t in jr.load(cfg) if pd.Timestamp(t["ouverture"]) >= monday]
    dec = [d for d in Decisions(cfg).load() if d.get("t_alerte") and pd.Timestamp(d["t_alerte"]) >= monday]
    taken, skipped = sum(d["decision"] == "pris" for d in dec), sum(d["decision"] == "passé" for d in dec)
    r = sum(t["r"] or 0 for t in trades)
    lines = [f"Semaine du {monday:%d/%m} : {len(trades)} trade(s) · {sum(t['pnl'] for t in trades):+.2f} $ · {r:+.2f} R",
             f"Alertes : {taken} prise(s), {skipped} passée(s)"]
    if trades:
        lines.append(f"SL dès l'entrée : {sum(1 for t in trades if t['sl_initial'])}/{len(trades)}"
                     f" · setup noté : {sum(1 for t in trades if (t.get('setup') or '').strip())}/{len(trades)}")
    lines.append("(trades importés dans le journal de l'interface)")
    return "\n".join(lines)


class TelegramBot:
    def __init__(self, cfg: dict, hub, token: str, chat: str, send: Callable = send_telegram,
                 api: Optional[Callable] = None, clock: Callable = lambda: pd.Timestamp.now(tz="UTC")):
        self.cfg, self.hub, self.token, self.chat = cfg, hub, token, str(chat)
        self.send, self.clock = send, clock
        self.api = api or self._api
        self.decisions = Decisions(cfg)
        self.offset = None
        self.replies: dict = {}                       # id d'une confirmation du bot → clé de l'alerte
        self._stop = threading.Event()

    def _api(self, method: str, **params):
        import requests

        r = requests.post(f"https://api.telegram.org/bot{self.token}/{method}", json=params,
                          timeout=params.get("timeout", 0) + 10)
        return r.json()

    # --- traitement d'une mise à jour -------------------------------------------------------
    def handle(self, u: dict) -> None:
        if "callback_query" in u:
            q = u["callback_query"]
            if str(q["message"]["chat"]["id"]) != self.chat:
                return
            action, _, key = q.get("data", "").partition("|")
            alert = self.hub.alerts.messages.get(q["message"]["message_id"]) or {"texte": q["message"].get("text", "")}
            decision = {"take": "pris", "skip": "passé"}.get(action)
            if not decision:
                return
            self.decisions.decide(key, decision, alert, self.clock())
            self.api("answerCallbackQuery", callback_query_id=q["id"], text="Noté ✅")
            self.api("editMessageReplyMarkup", chat_id=self.chat, message_id=q["message"]["message_id"],
                     reply_markup={"inline_keyboard": []})
            msg = ("Noté : tu le prends. Checklist cochée ? SL et TP dans la fenêtre d'ordre, taille ≤ 0,5 %."
                   if decision == "pris" else "Noté : tu passes.")
            mid = self.send(msg + "\nRéponds à ce message pour ajouter une note (facultatif).",
                            reply_to=q["message"]["message_id"])
            if mid:
                self.replies[mid] = key
            return
        m = u.get("message") or {}
        if str(m.get("chat", {}).get("id")) != self.chat or not m.get("text"):
            return                                    # pas toi, ou pas du texte : ignoré
        text = m["text"].strip()
        if text.startswith("/"):
            self.send(self.command(text.split()[0].split("@")[0].lower()))
            return
        replied = (m.get("reply_to_message") or {}).get("message_id")
        key = self.replies.get(replied) or (self.hub.alerts.messages.get(replied) or {}).get("cle")
        if key:
            self.decisions.note(key, text, self.hub.alerts.messages.get(replied), self.clock())
            self.send("📝 Note ajoutée à l'alerte.", reply_to=m["message_id"])
        else:
            self.send("Je n'ai pas compris. " + HELP)

    def command(self, cmd: str) -> str:
        if cmd in ("/start", "/aide", "/help"):
            return HELP
        if cmd == "/etat":
            return fmt_state(self.hub.state())
        if cmd == "/setups":
            return fmt_setups(self.hub.state())
        if cmd == "/niveaux":
            return fmt_levels(self.hub.state())
        if cmd == "/semaine":
            return fmt_week(self.cfg, self.clock())
        if cmd == "/pause":
            nxt = (self.clock().tz_convert(NY).normalize() + pd.Timedelta(days=1)).tz_convert("UTC")
            self.hub.alerts.paused_until = nxt
            return "⏸ Alertes Telegram en pause jusqu'à demain (elles restent visibles dans l'interface). /reprendre pour les réactiver."
        if cmd == "/reprendre":
            self.hub.alerts.paused_until = None
            return "▶ Alertes Telegram réactivées."
        return "Commande inconnue. " + HELP

    # --- boucle d'écoute --------------------------------------------------------------------
    def run(self) -> None:
        try:                                          # ignore les messages envoyés pendant que l'interface était arrêtée
            last = self.api("getUpdates", offset=-1, timeout=0).get("result", [])
            self.offset = last[-1]["update_id"] + 1 if last else None
        except Exception:
            pass
        while not self._stop.is_set():
            try:
                res = self.api("getUpdates", offset=self.offset, timeout=25,
                               allowed_updates=["message", "callback_query"]).get("result", [])
                for u in res:
                    self.offset = u["update_id"] + 1
                    try:
                        self.handle(u)
                    except Exception:
                        pass
            except Exception:
                self._stop.wait(5)

    def start(self) -> None:
        threading.Thread(target=self.run, daemon=True, name="telegram-bot").start()

    def stop(self) -> None:
        self._stop.set()
