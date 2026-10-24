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


# Plan proposé avec chaque setup (règles figées dans ALERTES_PLAN.md, mesurées par alert_stats)
PLAN_MIN_RR = 1.5          # TP = premier niveau d'hier à au moins 1,5 R
PLAN_SL_ATR = 0.25         # marge du SL au-delà de la zone, en ATR M5
PLAN_TP_ATR = 0.10         # TP placé un peu AVANT le niveau visé


def level_points(levels: dict) -> list[tuple[str, float]]:
    """Tous les prix de niveaux d'hier (HVN et single prints : leurs deux bords)."""
    pts = [("POC vol", levels["poc_v"]), ("VAH vol", levels["vah_v"]), ("VAL vol", levels["val_v"]),
           ("POC TPO", levels["poc_t"]), ("VAH TPO", levels["vah_t"]), ("VAL TPO", levels["val_t"])]
    pts += [("LVN", v) for v in levels["lvn"]]
    for name, key in (("HVN", "hvn"), ("single prints", "singles")):
        for a, b in levels[key]:
            pts += [(f"{name} {a:.2f}-{b:.2f}", a), (f"{name} {a:.2f}-{b:.2f}", b)]
    return pts


def plan_sl(zone, atr: float, spread: float) -> float:
    """SL au-delà du bord distal de la zone + PLAN_SL_ATR × ATR (+ spread pour une vente)."""
    return zone.low - PLAN_SL_ATR * atr if zone.direction > 0 else zone.high + spread + PLAN_SL_ATR * atr


def plan_target(d: int, entry: float, sl: float, levels: dict, atr: float, spread: float):
    """(TP, nom du niveau) : premier niveau d'hier à ≥ PLAN_MIN_RR depuis `entry`, ou None."""
    risk = abs(entry - sl)
    cands = []
    for name, p in level_points(levels):
        tp = p - PLAN_TP_ATR * atr if d > 0 else p + spread + PLAN_TP_ATR * atr
        if d * (tp - entry) >= PLAN_MIN_RR * risk:
            cands.append((abs(tp - entry), round(tp, 2), name))
    return min(cands)[1:] if cands else None


def trade_plan(zone, levels: dict, atr: float, spread: float, inst, budget_usd: float) -> dict:
    """Entrée limite au bord de la zone, SL au-delà (+ marge), TP avant le premier niveau d'hier
    à au moins PLAN_MIN_RR. Prix bid ; une vente se clôture à l'ask, d'où le spread côté vente."""
    from xauof.strategy.instrument import size_position

    d = zone.direction
    entry, sl = (zone.high if d > 0 else zone.low), plan_sl(zone, atr, spread)
    risk = abs(entry - sl)
    found = plan_target(d, entry, sl, levels, atr, spread)
    if not found:
        return {"refus": f"pas de niveau d'hier à {PLAN_MIN_RR} R ou plus (RR insuffisant)"}
    tp, target = found
    obstacles = sorted({n for n, p in level_points(levels) if 0 < d * (p - entry) < d * (tp - entry)
                        and abs(p - entry) > risk * 0.25 and n != target})
    size = size_position(inst, d, entry, sl, tp, budget_usd)
    if size.refused:
        return {"refus": size.refused}
    return {"entree": round(entry, 2), "sl": round(sl, 2), "tp": tp, "cible": target,
            "rr": round(abs(tp - entry) / risk, 2), "lots": size.lots, "risque_usd": round(size.risk_usd, 2),
            "obstacles": obstacles}


def plan_text(plan: Optional[dict]) -> str:
    if not plan:
        return ""
    if "refus" in plan:
        return f" — Plan : aucun ({plan['refus']})"
    obs = f" ; obstacles avant le TP : {', '.join(plan['obstacles'])}" if plan["obstacles"] else ""
    return (f"\nPlan : entrée {plan['entree']:.2f} (ordre limite) · SL {plan['sl']:.2f} · "
            f"TP {plan['tp']:.2f} (avant {plan['cible']}) · {plan['rr']:.1f} R · {plan['lots']:.2f} lot "
            f"≈ {plan['risque_usd']:.0f} $ de risque{obs}. Non validé : à toi de confirmer.")


@dataclass
class AlertCenter:
    """Alertes dédupliquées (une par zone et par étape), envoyées à Telegram si configuré,
    avec les boutons « je le prends / je passe ». /pause les coupe sur Telegram jusqu'au lendemain."""
    sent: set = field(default_factory=set)
    feed: list = field(default_factory=list)
    messages: dict = field(default_factory=dict)        # id du message Telegram → alerte
    paused_until: Optional[pd.Timestamp] = None

    def paused(self, t: pd.Timestamp) -> bool:
        return self.paused_until is not None and t < self.paused_until

    def push(self, key: str, t: pd.Timestamp, text: str, level: str = "info") -> bool:
        if key in self.sent:
            return False
        self.sent.add(key)
        self.feed.insert(0, {"t": t.isoformat(), "texte": text, "niveau": level})
        del self.feed[50:]
        if not self.paused(t):
            mid = send_telegram(text, buttons=[[("✅ Je le prends", f"take|{key}"), ("❌ Je passe", f"skip|{key}")]])
            if mid:
                self.messages[mid] = {"cle": key, "texte": text, "t": t.isoformat()}
        return True

    def from_setups(self, setups: list[dict], t: pd.Timestamp, blackout: Optional[str],
                    warning: Optional[str] = None) -> None:
        for s in setups:
            z = s["zone"]
            sens = "ACHAT" if z.direction > 0 else "VENTE"
            base = (f"{sens} — {z.kind} {z.low:.2f}-{z.high:.2f} sur {', '.join(s['niveaux'])} "
                    f"({s['contexte']})")
            if blackout:
                base += f" ⚠ fenêtre news : {blackout}"
            base += plan_text(s.get("plan"))
            if warning:                                   # limite atteinte : l'alerte part quand même, pour info
                base += f"\n⛔ {warning} — pour info seulement, pas de nouveau trade."
            if s["etat"] == "en attente du prix":
                self.push(f"{z.id}:pret", t, f"Setup A+ en préparation : {base}", "info")
            else:
                self.push(f"{z.id}:contact", t, f"Setup A+ : prix dans la zone. {base}", "alerte")


def telegram_credentials() -> tuple[Optional[str], Optional[str]]:
    """(jeton, chat id) depuis .env, ou (None, None) si absents ou désactivés (tests, démo)."""
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    from xauof.web.telegram import clean_chat_id

    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), clean_chat_id(os.getenv("TELEGRAM_CHAT_ID"))
    if not token or not chat or os.getenv("XAUOF_NO_TELEGRAM"):
        return None, None
    return token.strip(), chat


def send_telegram(text: str, buttons: Optional[list] = None, reply_to: Optional[int] = None) -> Optional[int]:
    """Envoie un message (boutons : lignes de (libellé, donnée)). Renvoie son id, ou None."""
    token, chat = telegram_credentials()
    if not token:
        return None
    body = {"chat_id": chat, "text": f"🟡 XAUUSD OF\n{text}"}
    if buttons:
        body["reply_markup"] = {"inline_keyboard": [[{"text": a, "callback_data": b} for a, b in row] for row in buttons]}
    if reply_to:
        body["reply_to_message_id"] = reply_to
    try:
        import requests

        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json=body, timeout=10)
        return r.json()["result"]["message_id"] if r.ok else None
    except Exception:
        return None
