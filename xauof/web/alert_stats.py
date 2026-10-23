"""Combien d'alertes A+ par semaine, et à quelles heures ? Rejoue l'historique minute par
minute avec EXACTEMENT la logique des alertes en direct (zones, qualify, dédoublonnage).

    python -m xauof.web.alert_stats                       # historique jusqu'au début du holdout
    python -m xauof.web.alert_stats --start 2025-06-01 --end 2026-01-01
    python -m xauof.web.alert_stats --holdout             # UNE seule fois : hypothèses H1 / H2

Mesure aussi ce qu'auraient donné les plans entrée / SL / TP proposés avec les alertes
(règles figées dans ALERTES_PLAN.md). Le holdout (à partir de validation.holdout_start)
n'est pas touché par défaut.

Hypothèses H1 « sens de la journée » et H2 « confirmation order flow », figées dans
HYPOTHESES_ALERTES.md : étape 1 sur l'historique, puis --holdout une seule fois.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter

import pandas as pd

from xauof.assistant.snapshot import ny_time
from xauof.data import store
from xauof.indicators.smc import atr as smc_atr
from xauof.strategy.zones import ZoneBook, ZoneDetector
from xauof.strategy.instrument import load_instrument
from xauof.web.setups import AlertCenter, plan_sl, plan_target, qualify, trade_plan

NY = "America/New_York"


def replay_day(cfg: dict, xau_m1: pd.DataFrame, levels: dict, day: pd.Timestamp, calendar=None,
               inst=None, gc_m1: pd.DataFrame | None = None) -> dict:
    """Alertes qu'aurait envoyées l'interface ce jour-là (xau_m1 : bougies depuis le warm-up),
    et le plan proposé à la première alerte de chaque zone, simulé jusqu'à sa sortie."""
    st, n, point = cfg["strategy"], cfg["news"], cfg["broker"]["point"]
    inst = inst or load_instrument(cfg)
    budget = cfg["risk"]["risk_per_trade_pct"] / 100 * cfg["account"]["initial_capital"]
    plans: dict = {}
    plans_sens: dict = {}                       # H1 « sens de la journée »
    plans_of: dict = {}                         # H2 « confirmation order flow »
    t_start, t_end = (ny_time(day, st[k], NY) for k in ("entry_start", "entry_end"))
    ib_end = t_start + pd.Timedelta(minutes=cfg["sessions"]["initial_balance_min"])
    ib_hi, ib_lo = float("-inf"), float("inf")
    tf = pd.Timedelta(st["zone_tf"])
    m5 = xau_m1[["open", "high", "low", "close"]].resample(tf).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    det, book, alerts = ZoneDetector(cfg, t_start, f"{day:%Y-%m-%d}"), ZoneBook(), AlertCenter()
    atr = float("nan")
    for s, bar in xau_m1.iterrows():
        close_t = s + pd.Timedelta(minutes=1)
        if t_start <= s < ib_end:
            ib_hi, ib_lo = max(ib_hi, bar["high"]), min(ib_lo, bar["low"])
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
            entry = {"t0": close_t, "sens": x["zone"].direction, "news": bool(blackout), "plan": x["plan"]}
            if x["zone"].id not in plans:
                plans[x["zone"].id] = entry
                plans_of[x["zone"].id] = {"t0": close_t, "sens": x["zone"].direction, "news": bool(blackout),
                                          "zone": x["zone"], "atr": atr, "spread": bar["spread"] * point}
            if x["zone"].id not in plans_sens and day_direction_ok(x["zone"].direction, float(bar["close"]),
                                                                   close_t, ib_end, ib_hi, ib_lo):
                plans_sens[x["zone"].id] = dict(entry)
        alerts.from_setups(setups, close_t, blackout)
        for a in alerts.feed[:len(alerts.sent) - before]:      # les plus récentes sont en tête
            a["t"] = close_t
    events = [{"t": a["t"], "etape": "contact" if a["niveau"] == "alerte" else "pret",
               "sens": "achat" if "ACHAT" in a["texte"] else "vente",
               "news": "fenêtre news" in a["texte"], "texte": a["texte"]} for a in reversed(alerts.feed)]
    t_flat = ny_time(day, st["flat_time"], NY)
    for p in [*plans.values(), *plans_sens.values()]:
        if "refus" not in p["plan"]:
            p.update(simulate_plan(p["plan"], p["sens"], xau_m1[xau_m1.index >= p["t0"]], t_end, t_flat, inst, point))
    delta = gc_m1["delta"] if gc_m1 is not None and "delta" in gc_m1 else pd.Series(dtype=float)
    for p in plans_of.values():
        z = p.pop("zone")
        p.update(simulate_confirm(z, p["atr"], p["spread"], xau_m1[xau_m1.index >= p["t0"]], delta, levels,
                                  t_end, t_flat, inst, point))
    return {"alertes": events, "plans": list(plans.values()), "plans_sens": list(plans_sens.values()),
            "plans_of": list(plans_of.values())}


def simulate_confirm(zone, atr: float, spread: float, bars: pd.DataFrame, delta: pd.Series, levels: dict,
                     entry_deadline: pd.Timestamp, t_flat: pd.Timestamp, inst, point: float,
                     window: int = 10) -> dict:
    """H2 : contact, puis bougie M1 qui clôture dans le sens du trade avec un delta GC dans le même
    sens (au plus `window` bougies, contact inclus) ; entrée au marché à l'ouverture suivante."""
    d, slip = zone.direction, inst.slippage
    sl = plan_sl(zone, atr, spread)
    rows = list(bars.iterrows())
    contact = next((i for i, (t, b) in enumerate(rows) if t < entry_deadline and
                    ((d > 0 and b["low"] <= zone.high) or (d < 0 and b["high"] + b["spread"] * point >= zone.low))),
                   None)
    if contact is None:
        return {"plan": {"refus": "pas de contact"}}
    for i in range(contact, min(contact + window, len(rows))):
        t, b = rows[i]
        if (d > 0 and b["close"] < zone.low) or (d < 0 and b["close"] > zone.high):
            return {"plan": {"refus": "zone invalidée avant confirmation"}}
        dl = delta.get(t, float("nan"))
        if d * (b["close"] - b["open"]) > 0 and d * dl > 0:
            if i + 1 >= len(rows) or rows[i + 1][0] >= entry_deadline:
                return {"plan": {"refus": "confirmation trop tardive"}}
            t1, b1 = rows[i + 1]
            fill = b1["open"] + b1["spread"] * point + slip if d > 0 else b1["open"] - slip
            if d * (fill - sl) <= 0:
                return {"plan": {"refus": "entrée au-delà du SL"}}
            found = plan_target(d, fill, sl, levels, atr, spread)
            if not found:
                return {"plan": {"refus": "RR insuffisant depuis l'entrée"}}
            tp = found[0]
            out = manage(d, fill, sl, tp, bars.iloc[i + 1:], t_flat, inst, point)
            return {"plan": {"entree": fill, "sl": sl, "tp": tp}, "t_entree": t1, "entree_reelle": fill, **out,
                    "r": (d * (out["sortie"] - fill) * inst.value_per_price - inst.commission_rt)
                    / inst.loss_per_lot(fill, sl)}
    return {"plan": {"refus": "pas de confirmation"}}


def manage(d: int, fill: float, sl: float, tp: float, bars: pd.DataFrame, t_flat: pd.Timestamp, inst,
           point: float) -> dict:
    """Position ouverte sur la première bougie de `bars` : SL avant TP, TP ignoré sur cette
    bougie, clôture forcée à `t_flat`, slippage sur SL et clôture."""
    slip = inst.slippage
    for k, (t, b) in enumerate(bars.iterrows()):
        ask = lambda x: x + b["spread"] * point  # noqa: E731
        if t >= t_flat:
            return {"issue": "clôture 13:30", "sortie": b["open"] - slip if d > 0 else ask(b["open"]) + slip}
        if (d > 0 and b["low"] <= sl) or (d < 0 and ask(b["high"]) >= sl):
            return {"issue": "SL", "sortie": sl - d * slip}
        if k > 0 and ((d > 0 and b["high"] >= tp) or (d < 0 and ask(b["low"]) <= tp)):
            return {"issue": "TP", "sortie": tp}
    last = bars.iloc[-1]
    return {"issue": "fin des données", "sortie": last["close"] if d > 0 else last["close"] + last["spread"] * point}


def day_direction_ok(direction: int, close: float, t: pd.Timestamp, ib_end: pd.Timestamp,
                     ib_hi: float, ib_lo: float) -> bool:
    """Hypothèse « sens de la journée » : IB terminé, achat seulement si la dernière clôture M1
    est au-dessus du haut de l'IB, vente seulement si elle est sous le bas."""
    if t < ib_end or ib_hi < ib_lo:
        return False
    return close > ib_hi if direction > 0 else close < ib_lo


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
    by_dow = Counter(ny_t.dt.day_of_week)
    lines.append("\nPar jour : " + " · ".join(f"{jours[d]} {by_dow.get(d, 0)}" for d in range(5)))
    lines.append(f"Sens : {(contact['sens'] == 'achat').sum()} achats · {(contact['sens'] == 'vente').sum()} ventes")
    lines.append(f"Pendant une fenêtre news (à éviter) : {int(contact['news'].sum())}")
    lines.append("\nCe sont des COMPTES d'alertes : ils ne disent rien de la rentabilité de ces setups.")
    return "\n".join(lines)


def plan_results(plans: list[dict]) -> pd.DataFrame:
    """Trades remplis (une ligne par plan exécuté)."""
    df = pd.DataFrame(plans)
    if df.empty:
        return df
    sim = df[~df["plan"].apply(lambda p: "refus" in p)]
    return sim[sim["issue"] != "non rempli"] if len(sim) else sim


def verdict_base(lo: float, hi: float, mean: float) -> str:
    return ("POSITIF sur l'historique (borne basse de l'IC > 0) : à confirmer sur le holdout avant d'y croire."
            if lo > 0 else
            "NON VALIDÉ : l'intervalle de confiance contient 0 ou est négatif. Les niveaux restent indicatifs."
            if hi > 0 else
            "NÉGATIF : suivre ces plans à l'aveugle aurait perdu de l'argent sur l'historique.")


def verdict_etape1(lo: float, hi: float, mean: float) -> str:
    return ("PASSE l'étape 1 (R moyen > 0) : le test sur le holdout est autorisé, une seule fois."
            if mean > 0 else "ÉCHOUE à l'étape 1 (R moyen ≤ 0) : hypothèse abandonnée, pas de holdout.")


def verdict_holdout(lo: float, hi: float, mean: float) -> str:
    return ("CONFIRMÉ sur le holdout (borne basse de l'IC > 0)." if lo > 0 else
            "ENCOURAGEANT mais NON PROUVÉ (R moyen > 0, IC qui contient 0) : à suivre en réel via le journal."
            if mean > 0 else "REJETÉ sur le holdout (R moyen ≤ 0) : hypothèse abandonnée.")


def summarize_plans(plans: list[dict], n_weeks: int, title: str = "PLANS PROPOSÉS",
                    verdict=verdict_base, rule: str = "ALERTES_PLAN.md") -> str:
    """Ce qu'auraient donné les plans (entrée / SL / TP) proposés avec les alertes."""
    from xauof.strategy.run import bootstrap_ci

    if not plans:
        return "Aucun plan à mesurer."
    df = pd.DataFrame(plans)
    refused = df["plan"].apply(lambda p: "refus" in p)
    lines = [f"\n{title} ({len(df)} zones A+) : {refused.sum()} sans trade (RR insuffisant, pas de contact ou de confirmation…), "
             f"{(~refused).sum()} avec un plan"]
    sim = df[~refused]
    filled = sim[sim["issue"] != "non rempli"] if len(sim) else sim
    if filled.empty:
        return "\n".join(lines + ["Aucun ordre limite rempli."])
    lines.append(f"Trades exécutés : {len(filled)} sur {len(sim)} ({len(filled) / len(sim):.0%}) · "
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
    clean = filled[~filled["news"]]["r"]
    lo, hi = bootstrap_ci(clean) if len(clean) else (float("nan"),) * 2
    lines.append(f"Verdict (règle figée dans {rule}) : {verdict(lo, hi, clean.mean() if len(clean) else float('nan'))}")
    lines.append("Coûts estimés inclus (commission, slippage sur SL et ordres au marché).")
    return "\n".join(lines)


HYPOTHESES = {"H1": ("plans_sens", "H1 « SENS DE LA JOURNÉE »"), "H2": ("plans_of", "H2 « CONFIRMATION ORDER FLOW »")}


def ci(values, level: float = 0.95, n: int = 5000, seed: int = 0) -> tuple[float, float]:
    """Intervalle de confiance bootstrap de la moyenne (graine fixe)."""
    import numpy as np

    v = np.asarray(values, dtype=float)
    if len(v) < 2:
        return float("nan"), float("nan")
    means = np.random.default_rng(seed).choice(v, size=(n, len(v)), replace=True).mean(axis=1)
    a = (1 - level) / 2 * 100
    return float(np.percentile(means, a)), float(np.percentile(means, 100 - a))


def verdict_holdout_975(values) -> tuple[str, list]:
    lo, hi = ci(values, 0.975)
    mean = float(pd.Series(values, dtype=float).mean()) if len(values) else float("nan")
    return verdict_holdout(lo, hi, mean), [lo, hi]


def main() -> None:
    from xauof.config import load_config
    from xauof.news.calendar import load_calendar
    from xauof.strategy.run import day_data, levels_for, prepare

    cfg = load_config()
    p = argparse.ArgumentParser(description="Alertes A+ : fréquence, plans, et hypothèses H1 / H2 (HYPOTHESES_ALERTES.md)")
    p.add_argument("--start", default="2024-10-01")
    p.add_argument("--end", default=None, help="exclu (défaut : début du holdout, ou aujourd'hui avec --holdout)")
    p.add_argument("--holdout", action="store_true", help="test UNIQUE des hypothèses ayant passé l'étape 1")
    args = p.parse_args()
    os.environ["XAUOF_NO_TELEGRAM"] = "1"
    hold = pd.Timestamp(cfg["validation"]["holdout_start"])
    vdir = store.data_dir(cfg) / "validation"
    etape1, etape2 = vdir / "hypotheses_etape1.json", vdir / "hypotheses_holdout.json"
    retenues = list(HYPOTHESES)
    if args.holdout:
        if etape2.exists():
            raise SystemExit(f"Holdout déjà utilisé, relance refusée. Résultat enregistré : {etape2}")
        if not etape1.exists():
            raise SystemExit("Holdout refusé : lance d'abord l'étape 1 (python -m xauof.web.alert_stats).")
        e1 = json.loads(etape1.read_text(encoding="utf-8"))
        retenues = [h for h in HYPOTHESES if e1[h]["passe"]]
        if not retenues:
            raise SystemExit("Holdout refusé : aucune hypothèse n'a passé l'étape 1.")
        start = hold
        end = pd.Timestamp(args.end) if args.end else pd.Timestamp.now(tz=NY).normalize().tz_localize(None)
    else:
        start = pd.Timestamp(args.start)
        end = min(pd.Timestamp(args.end), hold) if args.end else hold        # jamais dans le holdout ici
    data = prepare(cfg, start, end)
    cal = load_calendar(cfg)
    events, days, skipped = [], [], 0
    sets = {"plans": [], "plans_sens": [], "plans_of": []}
    inst = load_instrument(cfg)
    all_days = data["days"]
    for i, day in enumerate(all_days):
        if not (start <= day < end) or i == 0:
            continue
        levels, reason = levels_for(cfg, data, all_days[i - 1])
        if levels is None:
            skipped += 1
            continue
        dd = day_data(cfg, data, day, levels, reason)
        res = replay_day(cfg, dd.xau, levels.as_dict(), day, cal, inst, dd.gc)
        events += res["alertes"]
        for k in sets:
            sets[k] += res[k]
        days.append(day)
        print(f"\r{day:%Y-%m-%d}  alertes : {len(events)}", end="", flush=True)
    print("\n")
    if args.holdout:
        expected = len(pd.bdate_range(start, end - pd.Timedelta(days=1)))
        if len(days) < 0.85 * expected:
            raise SystemExit(f"Holdout NON consommé : seulement {len(days)} séances exploitables sur ~{expected} "
                             f"jours ouvrés. Télécharge d'abord les données XAUUSD et GC de {start:%Y-%m-%d} à "
                             f"{end:%Y-%m-%d}, puis relance.")
    weeks = pd.Series(1, index=pd.DatetimeIndex(days)).resample("W-FRI").size() if days else pd.Series()
    n_weeks = int((weeks > 0).sum())
    if skipped:
        print(f"{skipped} séance(s) ignorée(s) : niveaux de la veille indisponibles (données GC manquantes).\n")
    if not args.holdout:
        print(summarize(events, days))
        print(summarize_plans(sets["plans"], n_weeks))
    record = {"periode": [f"{days[0]:%Y-%m-%d}", f"{days[-1]:%Y-%m-%d}"] if days else [], "seances": len(days)}
    for h in retenues:
        key, title = HYPOTHESES[h]
        res = plan_results(sets[key])
        clean = res[~res["news"]]["r"] if len(res) else pd.Series(dtype=float)
        mean = float(clean.mean()) if len(clean) else float("nan")
        if args.holdout:
            verdict, interval = verdict_holdout_975(clean)
            rule = lambda lo, hi, m, v=verdict: v  # noqa: E731
        else:
            interval = list(ci(clean))
            verdict = verdict_etape1(*interval, mean)
            rule = verdict_etape1
        print(summarize_plans(sets[key], n_weeks, f"{title} — {'HOLDOUT' if args.holdout else 'ÉTAPE 1'}", rule,
                              "HYPOTHESES_ALERTES.md"))
        if args.holdout:
            print(f"IC 97,5 % (correction 2 hypothèses) : [{interval[0]:+.3f} ; {interval[1]:+.3f}]")
        record[h] = {"trades": int(len(clean)), "r_moyen": mean, "ic": interval, "verdict": verdict,
                     "passe": bool(mean > 0)}
    if args.end is None and days:                         # seule l'exécution complète est enregistrée
        vdir.mkdir(parents=True, exist_ok=True)
        (etape2 if args.holdout else etape1).write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nRésultat enregistré : {etape2 if args.holdout else etape1}")


if __name__ == "__main__":
    main()
