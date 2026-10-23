"""Combien d'alertes A+ par semaine, et à quelles heures ? Rejoue l'historique minute par
minute avec EXACTEMENT la logique des alertes en direct (zones, qualify, dédoublonnage).

    python -m xauof.web.alert_stats                       # historique jusqu'au début du holdout
    python -m xauof.web.alert_stats --start 2025-06-01 --end 2026-01-01

Mesure aussi ce qu'auraient donné les plans entrée / SL / TP proposés avec les alertes
(règles figées dans ALERTES_PLAN.md). Le holdout (à partir de validation.holdout_start)
n'est pas touché par défaut.
"""
from __future__ import annotations

import argparse
import os
from collections import Counter

import pandas as pd

from xauof.assistant.snapshot import ny_time
from xauof.indicators.smc import atr as smc_atr
from xauof.strategy.zones import ZoneBook, ZoneDetector
from xauof.strategy.instrument import load_instrument
from xauof.web.setups import AlertCenter, qualify, trade_plan

NY = "America/New_York"


def replay_day(cfg: dict, xau_m1: pd.DataFrame, levels: dict, day: pd.Timestamp, calendar=None,
               inst=None) -> dict:
    """Alertes qu'aurait envoyées l'interface ce jour-là (xau_m1 : bougies depuis le warm-up),
    et le plan proposé à la première alerte de chaque zone, simulé jusqu'à sa sortie."""
    st, n, point = cfg["strategy"], cfg["news"], cfg["broker"]["point"]
    inst = inst or load_instrument(cfg)
    budget = cfg["risk"]["risk_per_trade_pct"] / 100 * cfg["account"]["initial_capital"]
    plans: dict = {}
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
        setups = qualify(book.zones, levels, float(bar["close"]), atr, st["near_atr"])
        for x in setups:
            x["plan"] = trade_plan(x["zone"], levels, atr, bar["spread"] * point, inst, budget)
            if x["zone"].id not in plans:
                plans[x["zone"].id] = {"t0": close_t, "sens": x["zone"].direction, "news": bool(blackout),
                                       "plan": x["plan"]}
        alerts.from_setups(setups, close_t, blackout)
        for a in alerts.feed[:len(alerts.sent) - before]:      # les plus récentes sont en tête
            a["t"] = close_t
    events = [{"t": a["t"], "etape": "contact" if a["niveau"] == "alerte" else "pret",
               "sens": "achat" if "ACHAT" in a["texte"] else "vente",
               "news": "fenêtre news" in a["texte"], "texte": a["texte"]} for a in reversed(alerts.feed)]
    t_flat = ny_time(day, st["flat_time"], NY)
    for p in plans.values():
        if "refus" not in p["plan"]:
            p.update(simulate_plan(p["plan"], p["sens"], xau_m1[xau_m1.index >= p["t0"]], t_end, t_flat, inst, point))
    return {"alertes": events, "plans": list(plans.values())}


def simulate_plan(plan: dict, d: int, bars: pd.DataFrame, entry_deadline: pd.Timestamp, t_flat: pd.Timestamp,
                  inst, point: float) -> dict:
    """Ordre limite valable jusqu'à `entry_deadline`, puis SL / TP / clôture à `t_flat`.
    Bougies bid + spread ; conventions prudentes : sur une même bougie le SL passe avant le TP,
    et le TP n'est pas compté sur la bougie d'entrée. Slippage sur SL et clôture forcée."""
    entry, sl, tp, slip = plan["entree"], plan["sl"], plan["tp"], inst.slippage
    fill = None
    for t, b in bars.iterrows():
        ask = lambda x: x + b["spread"] * point  # noqa: E731
        if fill is None:
            if t >= entry_deadline:
                return {"issue": "non rempli"}
            if d > 0 and ask(b["low"]) <= entry:
                fill = min(entry, ask(b["open"]))
            elif d < 0 and b["high"] >= entry:
                fill = max(entry, b["open"])
            else:
                continue
            fill_t, first = t, True
        else:
            first = False
        if t >= t_flat:
            exit_, issue = (b["open"] - slip, "clôture 13:30") if d > 0 else (ask(b["open"]) + slip, "clôture 13:30")
            break
        if (d > 0 and b["low"] <= sl) or (d < 0 and ask(b["high"]) >= sl):
            exit_, issue = sl - d * slip, "SL"
            break
        if not first and ((d > 0 and b["high"] >= tp) or (d < 0 and ask(b["low"]) <= tp)):
            exit_, issue = tp, "TP"
            break
    else:
        if fill is None:
            return {"issue": "non rempli"}
        last = bars.iloc[-1]
        exit_, issue = (last["close"] if d > 0 else last["close"] + last["spread"] * point), "fin des données"
    pnl = d * (exit_ - fill) * inst.value_per_price - inst.commission_rt
    return {"issue": issue, "entree_reelle": fill, "t_entree": fill_t, "sortie": exit_,
            "r": pnl / inst.loss_per_lot(entry, sl)}


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


def summarize_plans(plans: list[dict], n_weeks: int) -> str:
    """Ce qu'auraient donné les plans (entrée / SL / TP) proposés avec les alertes."""
    from xauof.strategy.run import bootstrap_ci

    if not plans:
        return "Aucun plan à mesurer."
    df = pd.DataFrame(plans)
    refused = df["plan"].apply(lambda p: "refus" in p)
    lines = [f"\nPLANS PROPOSÉS ({len(df)} zones A+) : {refused.sum()} sans plan (RR insuffisant ou lot minimum), "
             f"{(~refused).sum()} avec un plan"]
    sim = df[~refused]
    filled = sim[sim["issue"] != "non rempli"] if len(sim) else sim
    if filled.empty:
        return "\n".join(lines + ["Aucun ordre limite rempli."])
    lines.append(f"Ordres limites remplis : {len(filled)} sur {len(sim)} ({len(filled) / len(sim):.0%}) · "
                 f"{len(filled) / max(n_weeks, 1):.1f} trades par semaine")
    lines.append("Issues : " + " · ".join(f"{k} {v}" for k, v in filled["issue"].value_counts().items()))
    for label, part in (("tous", filled), ("hors fenêtre news", filled[~filled["news"]])):
        if part.empty:
            continue
        lo, hi = bootstrap_ci(part["r"])
        lines.append(f"R net moyen ({label}) : {part['r'].mean():+.3f} R par trade  [IC 95 % {lo:+.3f} ; {hi:+.3f}]  "
                     f"· {len(part)} trades · gagnants {(part['r'] > 0).mean():.0%} · total {part['r'].sum():+.1f} R")
    for sens, part in filled.groupby("sens"):
        lines.append(f"  {'achats' if sens > 0 else 'ventes'} : {len(part)} trades · {part['r'].mean():+.3f} R")
    lo, hi = bootstrap_ci(filled[~filled["news"]]["r"]) if (~filled["news"]).any() else (float("nan"),) * 2
    verdict = ("POSITIF sur l'historique (borne basse de l'IC > 0) : à confirmer sur le holdout avant d'y croire."
               if lo > 0 else
               "NON VALIDÉ : l'intervalle de confiance contient 0 ou est négatif. Les niveaux restent indicatifs."
               if hi > 0 else
               "NÉGATIF : suivre ces plans à l'aveugle aurait perdu de l'argent sur l'historique.")
    lines.append(f"Verdict (règle figée dans ALERTES_PLAN.md) : {verdict}")
    lines.append("Coûts estimés inclus (commission, slippage sur SL) ; ordre limite rempli au prix ou mieux.")
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
    events, plans, days, skipped = [], [], [], 0
    inst = load_instrument(cfg)
    all_days = data["days"]
    for i, day in enumerate(all_days):
        if not (start <= day < end) or i == 0:
            continue
        levels, reason = levels_for(cfg, data, all_days[i - 1])
        if levels is None:
            skipped += 1
            continue
        res = replay_day(cfg, day_data(cfg, data, day, levels, reason).xau, levels.as_dict(), day, cal, inst)
        events += res["alertes"]
        plans += res["plans"]
        days.append(day)
        print(f"\r{day:%Y-%m-%d}  alertes : {len(events)}", end="", flush=True)
    print("\n")
    print(summarize(events, days))
    weeks = pd.Series(1, index=pd.DatetimeIndex(days)).resample("W-FRI").size() if days else pd.Series()
    print(summarize_plans(plans, int((weeks > 0).sum())))
    if skipped:
        print(f"\n{skipped} séance(s) ignorée(s) : niveaux de la veille indisponibles (données GC manquantes).")


if __name__ == "__main__":
    main()
