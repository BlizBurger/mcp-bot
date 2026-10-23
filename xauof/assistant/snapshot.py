"""Instantané de marché pour l'assistant manuel (fonctions pures, testables sans MT5).

Mêmes briques que le moteur : zones FVG/OB (détecteur M5 sans anticipation), premier
contact et invalidation sur M1, IB, filtre de spread, calendrier, limites FTMO.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from xauof.news.calendar import Calendar
from xauof.strategy.risk import spread_blocked
from xauof.strategy.zones import ZoneBook, ZoneDetector


def ny_time(day: pd.Timestamp, hhmm: str, tz: str) -> pd.Timestamp:
    return (pd.Timestamp(pd.Timestamp(day).date()) + pd.Timedelta(hhmm + ":00")).tz_localize(tz).tz_convert("UTC")


def zones_today(cfg: dict, xau_m1: pd.DataFrame, session_start: pd.Timestamp, now: pd.Timestamp,
                point: float) -> list:
    """Rejoue la séance bougie par bougie : zones détectées à chaque clôture M5, puis
    premier contact et invalidation sur les bougies M1 suivantes."""
    tf = pd.Timedelta(cfg["strategy"]["zone_tf"])
    bars = xau_m1[xau_m1.index + pd.Timedelta(minutes=1) <= now]
    m5 = bars[["open", "high", "low", "close"]].resample(tf).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    det = ZoneDetector(cfg, session_start, f"{session_start:%Y-%m-%d}")
    book = ZoneBook()
    for s, bar in bars.iterrows():
        close_t = s + pd.Timedelta(minutes=1)
        if s >= session_start:
            book.contacts(s, bar["low"], bar["high"] + bar["spread"] * point)
            book.invalidate(close_t, bar["close"])
        if close_t.value % tf.value == 0:
            closed = m5[m5.index + tf <= close_t]
            if len(closed) and closed.index[-1] + tf == close_t:
                for z in det.on_m5_close(closed, bar["spread"] * point):
                    book.add(z)
    return book.zones


def account_status(cfg: dict, balance: float, equity: float, realized_today: float) -> dict:
    """Limites du jour à partir des chiffres MT5 (réalisé du jour = depuis minuit Europe/Prague)."""
    day_start = balance - realized_today
    loss = day_start - equity
    floor = cfg["account"]["initial_capital"] - cfg["ftmo"]["max_loss_usd"]
    internal_floor = floor + cfg["risk"]["total_floor_margin_usd"]
    return {
        "solde": balance, "equity": equity, "solde_debut_jour": day_start, "perte_jour": loss,
        "reste_arret_interne": cfg["risk"]["daily_stop_usd"] - loss,
        "reste_ftmo_jour": cfg["ftmo"]["daily_loss_limit_usd"] - loss,
        "marge_plancher_ftmo": equity - floor, "marge_plancher_interne": equity - internal_floor,
        "stop_interne_atteint": loss >= cfg["risk"]["daily_stop_usd"] or equity <= internal_floor,
    }


def snapshot(cfg: dict, xau_m1: pd.DataFrame, levels: Optional[dict], calendar: Calendar,
             now: pd.Timestamp, account: Optional[dict] = None) -> dict:
    st, r, n = cfg["strategy"], cfg["risk"], cfg["news"]
    tz, point = st["timezone"], cfg["broker"]["point"]
    day = now.tz_convert(tz).normalize().tz_localize(None)
    t_start, t_end, t_flat = (ny_time(day, st[k], tz) for k in ("entry_start", "entry_end", "flat_time"))
    ib_end = t_start + pd.Timedelta(minutes=cfg["sessions"]["initial_balance_min"])
    out: dict = {"maintenant": now, "jour": day}

    closed = xau_m1[xau_m1.index + pd.Timedelta(minutes=1) <= now]
    if closed.empty:
        out["erreur"] = "aucune bougie XAUUSD"
        return out
    last = closed.iloc[-1]
    out["prix"] = {"bid": float(last["close"]), "ask": float(last["close"] + last["spread"] * point),
                   "spread_pts": float(last["spread"]), "bougie": closed.index[-1]}
    out["spread_bloque"] = spread_blocked(closed["spread"], now.floor("min"), float(last["spread"]),
                                          r["spread_filter_window_min"], r["spread_filter_min_obs"],
                                          r["spread_filter_mult"])

    ib_bars = closed[(closed.index >= t_start) & (closed.index < ib_end)]
    out["ib"] = None if ib_bars.empty else {"high": float(ib_bars["high"].max()), "low": float(ib_bars["low"].min()),
                                            "definitive": now >= ib_end}

    zones = zones_today(cfg, xau_m1, t_start, now, point) if now > t_start else []
    out["zones_actives"] = [z for z in zones if z.state == "eligible"]
    out["zones"] = zones

    out["niveaux"] = levels
    if levels:
        price = out["prix"]["bid"]
        lines = [(name, val) for name, val in [("POC vol", levels["poc_v"]), ("VAH vol", levels["vah_v"]),
                                               ("VAL vol", levels["val_v"]), ("POC TPO", levels["poc_t"])]]
        out["position_va"] = ("au-dessus de la VA" if price > levels["vah_v"] else
                              "sous la VA" if price < levels["val_v"] else "dans la VA")
        out["niveau_proche"] = min(lines, key=lambda x: abs(x[1] - price))

    cal_ok = calendar.covers(now, now + pd.Timedelta(hours=1))
    out["calendrier_ok"] = cal_ok
    out["news_blackout"] = calendar.blackout(now, n["blackout_before_min"], n["blackout_after_min"],
                                             n["currencies"]) if cal_ok else None
    out["news_cloture"] = calendar.flatten_due(now, n["flatten_before_min"], n["flatten_kinds"],
                                               n["currencies"]) if cal_ok else None
    out["prochaines_news"] = calendar.upcoming(now, n["currencies"], 5)

    local = now.tz_convert(tz)
    if now < t_start:
        out["fenetre"] = f"avant la séance (ouverture {st['entry_start']})"
    elif now <= t_end:
        out["fenetre"] = f"entrées autorisées jusqu'à {st['entry_end']} ({(t_end - now).seconds // 60} min)"
    elif now < t_flat:
        out["fenetre"] = f"plus d'entrées ; clôture à {st['flat_time']}"
    else:
        out["fenetre"] = "séance terminée"
    out["heure_ny"] = local.strftime("%H:%M")

    out["compte"] = account_status(cfg, **account) if account else None
    alerts = []
    if out["spread_bloque"]:
        alerts.append(f"Spread : {out['spread_bloque']}")
    if not cal_ok:
        alerts.append("Calendrier news non couvert : lancer python -m xauof.news.calendar --import-ff")
    if out["news_blackout"]:
        alerts.append(f"Fenêtre news : {out['news_blackout']}")
    if out["news_cloture"]:
        alerts.append(f"Clôturer avant annonce majeure : {out['news_cloture']}")
    if out["compte"] and out["compte"]["stop_interne_atteint"]:
        alerts.append("Limite interne atteinte : ne plus trader aujourd'hui")
    if levels is None:
        alerts.append("Niveaux de la veille absents : lancer python -m xauof.assistant.update")
    out["alertes"] = alerts
    return out
