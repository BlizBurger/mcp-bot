"""Combien d'alertes A+ par semaine, et à quelles heures ? Rejoue l'historique minute par
minute avec EXACTEMENT la logique des alertes en direct (zones, qualify, dédoublonnage).

    python -m xauof.web.alert_stats                       # historique jusqu'au début du holdout
    python -m xauof.web.alert_stats --start 2025-06-01 --end 2026-01-01

Compte des alertes, pas des gains : rien ici ne dit si ces setups sont rentables.
"""
from __future__ import annotations

import argparse
import os
from collections import Counter

import pandas as pd

from xauof.assistant.snapshot import ny_time
from xauof.indicators.smc import atr as smc_atr
from xauof.strategy.zones import ZoneBook, ZoneDetector
from xauof.web.setups import AlertCenter, qualify

NY = "America/New_York"


def replay_day(cfg: dict, xau_m1: pd.DataFrame, levels: dict, day: pd.Timestamp, calendar=None) -> list[dict]:
    """Alertes qu'aurait envoyées l'interface ce jour-là (xau_m1 : bougies depuis le warm-up)."""
    st, n, point = cfg["strategy"], cfg["news"], cfg["broker"]["point"]
    t_start, t_end = (ny_time(day, st[k], NY) for k in ("entry_start", "entry_end"))
    tf = pd.Timedelta(st["zone_tf"])
    m5 = xau_m1[["open", "high", "low", "close"]].resample(tf).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    det, book, alerts = ZoneDetector(cfg, t_start, f"{day:%Y-%m-%d}"), ZoneBook(), AlertCenter()
    atr = float("nan")
    for s, bar in xau_m1.iterrows():
        close_t = s + pd.Timedelta(minutes=1)
        if s >= t_start:
            book.contacts(s, bar["low"], bar["high"] + bar["spread"] * point)
            book.invalidate(close_t, bar["close"])
        if close_t.value % tf.value == 0:
            closed = m5[m5.index + tf <= close_t]
            if len(closed) and closed.index[-1] + tf == close_t:
                for z in det.on_m5_close(closed, bar["spread"] * point):
                    book.add(z)
                if len(closed) >= st["atr_period"]:
                    atr = float(smc_atr(closed, st["atr_period"]).iloc[-1])
        if not (t_start <= close_t <= t_end):
            continue
        blackout = calendar.blackout(close_t, n["blackout_before_min"], n["blackout_after_min"],
                                     n["currencies"]) if calendar is not None else None
        before = len(alerts.sent)
        alerts.from_setups(qualify(book.zones, levels, float(bar["close"]), atr, st["near_atr"]), close_t, blackout)
        for a in alerts.feed[:len(alerts.sent) - before]:      # les plus récentes sont en tête
            a["t"] = close_t
    return [{"t": a["t"], "etape": "contact" if a["niveau"] == "alerte" else "pret",
             "sens": "achat" if "ACHAT" in a["texte"] else "vente",
             "news": "fenêtre news" in a["texte"], "texte": a["texte"]} for a in reversed(alerts.feed)]


def summarize(events: list[dict], days: list[pd.Timestamp]) -> str:
    if not days:
        return "Aucun jour de séance dans la période."
    weeks = pd.Series(1, index=pd.DatetimeIndex(days)).resample("W-FRI").size()
    weeks = weeks[weeks > 0]
    ev = pd.DataFrame(events)
    lines = [f"Période : {days[0]:%Y-%m-%d} → {days[-1]:%Y-%m-%d}  ({len(days)} séances, {len(weeks)} semaines)"]
    if ev.empty:
        return "\n".join(lines + ["Aucune alerte A+ sur la période."])
    contact = ev[ev["etape"] == "contact"]
    for label, part in (("« en préparation »", ev[ev["etape"] == "pret"]), ("« prix dans la zone »", contact)):
        per_week = part.groupby(part["t"].dt.tz_convert(NY).dt.tz_localize(None).dt.to_period("W-FRI")).size()
        per_week = per_week.reindex(weeks.index.to_period("W-FRI"), fill_value=0)
        lines.append(f"Alertes {label} : {len(part)} au total · {per_week.mean():.1f} par semaine en moyenne "
                     f"(médiane {per_week.median():.0f}, max {per_week.max()}) · "
                     f"semaines sans aucune : {(per_week == 0).mean():.0%}")
    if contact.empty:
        return "\n".join(lines)
    ny_t = contact["t"].dt.tz_convert(NY)
    paris_t = contact["t"].dt.tz_convert("Europe/Paris")
    lines.append("\nAlertes « prix dans la zone » par tranche horaire (New York → Paris) :")
    slots = ny_t.dt.floor("30min")
    for slot, cnt in sorted(Counter(slots.dt.strftime("%H:%M")).items()):
        paris = paris_t[slots.dt.strftime("%H:%M") == slot].dt.floor("30min").dt.strftime("%H:%M").mode().iloc[0]
        lines.append(f"  {slot} NY ({paris} Paris)  {cnt:4d}  {'█' * round(40 * cnt / len(contact))}")
    jours = ["lundi", "mardi", "mercredi", "jeudi", "vendredi"]
    by_dow = Counter(ny_t.dt.dayofweek)
    lines.append("\nPar jour : " + " · ".join(f"{jours[d]} {by_dow.get(d, 0)}" for d in range(5)))
    lines.append(f"Sens : {(contact['sens'] == 'achat').sum()} achats · {(contact['sens'] == 'vente').sum()} ventes")
    lines.append(f"Pendant une fenêtre news (à éviter) : {int(contact['news'].sum())}")
    lines.append("\nCe sont des COMPTES d'alertes : ils ne disent rien de la rentabilité de ces setups.")
    return "\n".join(lines)


def main() -> None:
    from xauof.config import load_config
    from xauof.news.calendar import load_calendar
    from xauof.strategy.run import day_data, levels_for, prepare

    cfg = load_config()
    p = argparse.ArgumentParser(description="Fréquence et horaires des alertes A+ sur l'historique")
    p.add_argument("--start", default="2024-10-01")
    p.add_argument("--end", default=str(cfg["validation"]["holdout_start"]), help="exclu (défaut : début du holdout)")
    args = p.parse_args()
    os.environ["XAUOF_NO_TELEGRAM"] = "1"
    start, end = pd.Timestamp(args.start), pd.Timestamp(args.end)
    data = prepare(cfg, start, end)
    cal = load_calendar(cfg)
    events, days, skipped = [], [], 0
    all_days = data["days"]
    for i, day in enumerate(all_days):
        if not (start <= day < end) or i == 0:
            continue
        levels, reason = levels_for(cfg, data, all_days[i - 1])
        if levels is None:
            skipped += 1
            continue
        events += replay_day(cfg, day_data(cfg, data, day, levels, reason).xau, levels.as_dict(), day, cal)
        days.append(day)
        print(f"\r{day:%Y-%m-%d}  alertes : {len(events)}", end="", flush=True)
    print("\n")
    print(summarize(events, days))
    if skipped:
        print(f"\n{skipped} séance(s) ignorée(s) : niveaux de la veille indisponibles (données GC manquantes).")


if __name__ == "__main__":
    main()
