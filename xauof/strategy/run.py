"""Backtest du moteur de stratégie sur une période, pour une variante.

Usage :
    python -m xauof.strategy.run --start 2025-10-06 --end 2026-07-01 --variant A
    python -m xauof.strategy.run ... --variant C
La période réservée (validation.holdout_start et après) est refusée sans --holdout.
Sorties : reports/strategy_<variante>_<début>_<fin>/ (journal.jsonl, trades.csv, summary.txt)
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

import pandas as pd

from xauof.bridge import basis as bs
from xauof.config import ROOT, load_config
from xauof.data import gc_bars, store
from xauof.indicators import sessions
from xauof.indicators.check import day_bounds
from xauof.news.calendar import Calendar
from xauof.strategy import context as ctx
from xauof.strategy.engine import DayData, Engine, SimExecutor
from xauof.strategy.instrument import load_instrument
from xauof.strategy.risk import Account


def ny(day: pd.Timestamp, hhmm: str, tz: str) -> pd.Timestamp:
    return (pd.Timestamp(day.date()) + pd.Timedelta(hhmm + ":00")).tz_localize(tz).tz_convert("UTC")


def session_days(xau_m1: pd.DataFrame, cfg: dict) -> list[pd.Timestamp]:
    """Journées (date New York) qui ont des bougies XAUUSD dans la fenêtre 08:20–13:30."""
    st = cfg["strategy"]
    local = xau_m1.index.tz_convert(st["timezone"])
    mask = sessions.rth_mask(xau_m1.index, st["entry_start"], st["flat_time"])
    return sorted(pd.Timestamp(d) for d in set(local[mask].normalize().tz_localize(None)))


def prepare(cfg: dict, start, end) -> dict:
    """Charge les données (avec 10 jours d'historique avant `start` pour la séance précédente)."""
    lo, hi = pd.Timestamp(start) - pd.Timedelta(days=10), pd.Timestamp(end) + pd.Timedelta(days=1)
    gc = gc_bars.load_m1(cfg, lo, hi)
    xau = store.load_range(store.xauusd_m1_dir(cfg), lo, hi, index_name="time")
    al = bs.align(gc, xau, cfg["broker"]["point"])
    al["basis_ref"] = bs.rolling_basis(al, cfg["bridge"]["window"], cfg["bridge"]["min_obs"])
    al["bruit"] = bs.basis_noise(al, cfg["bridge"]["window"], cfg["bridge"]["min_obs"])
    return {"gc": gc, "xau": xau, "basis": al, "days": session_days(xau, cfg)}


def aggressor_check(gc_m1: pd.DataFrame, start, end) -> float:
    """Part du volume GC de la période sans côté agresseur (buy + sell < volume)."""
    w = gc_m1[(gc_m1.index >= pd.Timestamp(start, tz="UTC")) & (gc_m1.index < pd.Timestamp(end, tz="UTC"))]
    vol = w["volume"].sum()
    return float("nan") if vol <= 0 else float((vol - w["buy"].sum() - w["sell"].sum()) / vol)


def levels_for(cfg: dict, data: dict, prev_day: pd.Timestamp):
    rth = tuple(cfg["sessions"]["rth"])
    a, b = day_bounds(prev_day)
    try:
        trades = store.load_range(store.gc_trades_dir(cfg), a, b)
    except FileNotFoundError:
        return None, f"transactions GC absentes pour {prev_day:%Y-%m-%d}"
    reg = sessions.day_trades(trades, prev_day, rth)
    return ctx.build_session_levels(cfg, reg, data["gc"], data["xau"], prev_day)


def day_data(cfg: dict, data: dict, day: pd.Timestamp, levels, reason: str) -> DayData:
    st = cfg["strategy"]
    t0 = ny(day, st["warmup_start"], st["timezone"])
    t1 = ny(day, st["flat_time"], st["timezone"]) + pd.Timedelta(minutes=5)
    sl = lambda df: df[(df.index >= t0) & (df.index < t1)]  # noqa: E731
    return DayData(day, sl(data["xau"]), sl(data["gc"]), sl(data["basis"]), levels, reason)


def run(cfg: dict, start, end, variant: str, holdout: bool = False, out_dir: Path | None = None,
        data: dict | None = None) -> dict:
    hs = pd.Timestamp(cfg["validation"]["holdout_start"])
    if pd.Timestamp(end) > hs and not holdout:
        raise SystemExit(f"La période demandée empiète sur la période réservée (à partir du {hs:%Y-%m-%d}). "
                         "Ne l'utiliser qu'une fois les règles figées, avec --holdout.")
    data = data or prepare(cfg, start, end)
    share = aggressor_check(data["gc"], start, end)
    limit = cfg["strategy"]["data_guards"]["max_unclassified_share"]
    print(f"Contrôle source GC : {share:.2%} du volume sans côté agresseur (limite {limit:.0%})")
    if variant.startswith("C") and not share <= limit:
        raise SystemExit(f"Variante {variant} désactivée : la source GC ne classe pas assez de transactions "
                         f"acheteur/vendeur ({share:.2%} non classées > {limit:.0%}). Le delta ne peut pas "
                         "être calculé honnêtement à partir de simples OHLCV.")
    inst = load_instrument(cfg)
    cal = Calendar.load(ROOT / cfg["news"]["calendar_dir"])
    eng = Engine(cfg, variant, inst, Account.from_config(cfg), cal, SimExecutor())
    days = [d for d in data["days"] if pd.Timestamp(start) <= d < pd.Timestamp(end)]
    for day in days:
        prev = ctx.previous_session_day(data["days"], day)
        levels, reason = (None, "aucune séance précédente") if prev is None else levels_for(cfg, data, prev)
        eng.run_day(day_data(cfg, data, day, levels, reason))
        if eng.account.total_locked:
            eng.log(day, "arret", motif=eng.account.lock_reason)
            break
    res = {"engine": eng, "days": days, "instrument": inst, "calendar_ok_days": sum(
        1 for r in eng.journal if r["type"] == "calendrier_absent")}
    if out_dir is not None:
        write_outputs(res, out_dir, cfg, variant, start, end)
    return res


def summarize(eng: Engine, cfg: dict) -> str:
    rows = []
    tr = pd.DataFrame([t.__dict__ for t in eng.trades])
    lines = [f"Variante {eng.variant} — {len(tr)} trades"]
    if not tr.empty:
        tr["r"] = tr["pnl"] / tr["risk_usd"]
        for name, g in [("TOUS", tr)] + list(tr.groupby("scenario")):
            eq = g["pnl"].cumsum()
            rows.append(f"  {name:<13} n={len(g):>4}  gagnants={(g['pnl'] > 0).mean():6.1%}  "
                        f"R moyen={g['r'].mean():+.3f}  PnL={g['pnl'].sum():+9.2f} $  "
                        f"DD max={(eq.cummax() - eq).max():8.2f} $")
        lines += rows
        lines.append("  sorties : " + ", ".join(f"{k} {v}" for k, v in Counter(tr["exit_reason"]).most_common()))
    refus = Counter()
    generic = lambda m: re.sub(r"[-+]?\d[\d.,:\- ]*\d|\d", "#", m.split(" (")[0])  # noqa: E731
    for r in eng.journal:
        if r["type"] in ("contact_refuse", "entree_refusee"):
            for m in r["motifs"]:
                refus[generic(m)] += 1
        elif r["type"] == "refus":
            refus[generic(r["motif"])] += 1
    lines.append("Refus les plus fréquents :")
    lines += [f"  {n:>6}  {m}" for m, n in refus.most_common(12)]
    lines.append(f"Solde final : {eng.account.balance:.2f} $ ; verrouillages : "
                 f"{sum('verrouillage' in e[1] for e in eng.account.events)}")
    return "\n".join(lines)


def write_outputs(res: dict, out_dir: Path, cfg: dict, variant: str, start, end) -> None:
    eng = res["engine"]
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "journal.jsonl", "w", encoding="utf-8") as f:
        for r in eng.journal:
            f.write(json.dumps(r, default=str, ensure_ascii=False) + "\n")
    pd.DataFrame([t.__dict__ for t in eng.trades]).to_csv(out_dir / "trades.csv", index=False)
    warn = []
    if not res["instrument"].verified:
        warn.append("⚠ spécifications XAUUSD NON vérifiées (lancer python -m xauof.data.symbol_spec)")
    if res["calendar_ok_days"]:
        mode = cfg["news"]["missing_calendar"]
        warn.append(f"⚠ calendrier absent sur {res['calendar_ok_days']} jour(s) — mode {mode}"
                    + (" : RÉSULTATS SANS FILTRE NEWS, NON REPRÉSENTATIFS" if mode == "ignore_backtest_only" else
                       " : aucune entrée ces jours-là"))
    text = "\n".join([f"Période {start} → {end} ({len(res['days'])} séances)"] + warn + [summarize(eng, cfg)])
    (out_dir / "summary.txt").write_text(text, encoding="utf-8")
    print(text)
    print(f"\nJournal et trades : {out_dir}")


def main() -> None:
    p = argparse.ArgumentParser(description="Backtest du moteur de stratégie (phase 4)")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True, help="date de fin exclue")
    p.add_argument("--variant", default="A", choices=["A", "B", "C", "C_ABS"])
    p.add_argument("--holdout", action="store_true", help="autorise la période réservée (règles figées uniquement)")
    args = p.parse_args()
    cfg = load_config()
    out = ROOT / "reports" / f"strategy_{args.variant}_{args.start}_{args.end}"
    run(cfg, args.start, args.end, args.variant, args.holdout, out)


if __name__ == "__main__":
    main()
