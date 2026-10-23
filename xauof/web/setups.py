"""Setups « A+ » selon TES critères, et alertes (interface + Telegram).

Critères (choisis par toi, NON validés statistiquement) :
1. zone FVG / OB sur un niveau de la séance précédente (POC, VAH/VAL volume ou TPO,
   HVN, LVN, single prints), à moins de `near_atr` × ATR M5 ;
2. dans le sens du contexte : achat si le prix est au-dessus de la Value Area ou si
   la zone est sur la VAL ; vente si le prix est sous la VA ou si la zone est sur la VAH.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from xauof.config import ROOT


def _near(zone, lo: float, hi: Optional[float], tol: float) -> bool:
    return zone.distance(lo, hi) <= tol


def matching_levels(zone, levels: dict, tol: float) -> list[str]:
    hits = [f"{n} {v:.2f}" for n, v in (("POC vol", levels["poc_v"]), ("VAH vol", levels["vah_v"]),
                                       ("VAL vol", levels["val_v"]), ("POC TPO", levels["poc_t"]),
                                       ("VAH TPO", levels["vah_t"]), ("VAL TPO", levels["val_t"]))
            if _near(zone, v, None, tol)]
    hits += [f"LVN {v:.2f}" for v in levels["lvn"] if _near(zone, v, None, tol)]
    hits += [f"HVN {a:.2f}-{b:.2f}" for a, b in levels["hvn"] if _near(zone, a, b, tol)]
    hits += [f"single prints {a:.2f}-{b:.2f}" for a, b in levels["singles"] if _near(zone, a, b, tol)]
    return hits


def context_ok(zone, levels: dict, price: float, tol: float) -> tuple[bool, str]:
    vah, val = levels["vah_v"], levels["val_v"]
    if zone.direction > 0:
        if price > vah:
            return True, "achat au-dessus de la Value Area"
        if _near(zone, val, None, tol):
            return True, "achat sur la VAL"
        return False, "achat hors contexte (ni au-dessus de la VA, ni sur la VAL)"
    if price < val:
        return True, "vente sous la Value Area"
    if _near(zone, vah, None, tol):
        return True, "vente sur la VAH"
    return False, "vente hors contexte (ni sous la VA, ni sur la VAH)"


def qualify(zones: list, levels: Optional[dict], price: float, atr: float, near_atr: float) -> list[dict]:
    """Zones actives ou juste touchées qui remplissent les deux critères."""
    if not levels or not atr or atr != atr:
        return []
    tol = near_atr * atr
    out = []
    for z in zones:
        if z.state not in ("eligible", "touched"):
            continue
        lv_hits = matching_levels(z, levels, tol)
        ok, why = context_ok(z, levels, price, tol)
        if lv_hits and ok:
            out.append({"zone": z, "niveaux": lv_hits, "contexte": why,
                        "etat": "prix dans la zone" if z.state == "touched" else "en attente du prix"})
    return out


@dataclass
class AlertCenter:
    """Alertes dédupliquées (une par zone et par étape), envoyées à Telegram si configuré."""
    sent: set = field(default_factory=set)
    feed: list = field(default_factory=list)

    def push(self, key: str, t: pd.Timestamp, text: str, level: str = "info") -> bool:
        if key in self.sent:
            return False
        self.sent.add(key)
        self.feed.insert(0, {"t": t.isoformat(), "texte": text, "niveau": level})
        del self.feed[50:]
        send_telegram(text)
        return True

    def from_setups(self, setups: list[dict], t: pd.Timestamp, blackout: Optional[str]) -> None:
        for s in setups:
            z = s["zone"]
            sens = "ACHAT" if z.direction > 0 else "VENTE"
            base = (f"{sens} — {z.kind} {z.low:.2f}-{z.high:.2f} sur {', '.join(s['niveaux'])} "
                    f"({s['contexte']})")
            if blackout:
                base += f" ⚠ fenêtre news : {blackout}"
            if s["etat"] == "en attente du prix":
                self.push(f"{z.id}:pret", t, f"Setup A+ en préparation : {base}", "info")
            else:
                self.push(f"{z.id}:contact", t, f"Setup A+ : prix dans la zone. {base}", "alerte")


def send_telegram(text: str) -> bool:
    """Envoi Telegram si TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID sont dans .env (sinon rien)."""
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    from xauof.web.telegram import clean_chat_id

    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), clean_chat_id(os.getenv("TELEGRAM_CHAT_ID"))
    if not token or not chat or os.getenv("XAUOF_NO_TELEGRAM"):
        return False
    try:
        import requests

        r = requests.post(f"https://api.telegram.org/bot{token.strip()}/sendMessage",
                          json={"chat_id": chat, "text": f"🟡 XAUUSD OF\n{text}"}, timeout=10)
        return r.ok
    except Exception:
        return False
