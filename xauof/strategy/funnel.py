"""Entonnoir de décision d'un backtest : où et pourquoi les zones sont abandonnées.

Usage :
    python -m xauof.strategy.funnel reports/strategy_A_2025-10-06_2026-07-01
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import pandas as pd


def load(path: Path) -> list[dict]:
    path = path / "journal.jsonl" if path.is_dir() else path
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def funnel(journal: list[dict], tz: str = "America/New_York") -> str:
    zones = [r for r in journal if r["type"] == "zone"]
    contacts = [r for r in journal if r["type"] in ("contact_eligible", "contact_refuse")]
    scen = [r for r in journal if r["type"] == "scenario"]
    days = {r["zone_id"][:10] for r in zones} | {r["t"][:10] for r in journal if r["type"] == "ib"}
    out = [f"Séances avec données : {len(days)}", "", "1. Zones détectées (état final)"]
    states = Counter((r["kind"], r["state"]) for r in zones)
    for (kind, st), n in sorted(states.items()):
        out.append(f"   {kind:<4} {st:<12} {n:>6}")

    out += ["", "2. Scénarios activés"]
    kinds = Counter(r["message"].split(" (")[0] for r in scen)
    for k, n in kinds.most_common():
        out.append(f"   {n:>6}  {k}")
    by_day = pd.Series([r["t"][:10] for r in scen if "actif" in r["message"] or "acceptée" in r["message"]])
    out.append(f"   séances avec au moins une activation : {by_day.nunique()} / {len(days)}")

    out += ["", f"3. Premiers contacts : {len(contacts)}"]
    elig = sum(r["type"] == "contact_eligible" for r in contacts)
    out.append(f"   éligibles (en attente de confirmation) : {elig}")
    first = Counter(r["motifs"][0].split(" (")[0] for r in contacts if r["type"] == "contact_refuse")
    for m, n in first.most_common(10):
        out.append(f"   {n:>6}  {m}")
    hours = Counter(pd.Timestamp(r["t"]).tz_convert(tz).hour for r in contacts)
    out.append("   contacts par heure (New York) : " + ", ".join(f"{h}h:{n}" for h, n in sorted(hours.items())))

    conf = sum(r["type"] == "confirmation" for r in journal)
    ent = [r for r in journal if r["type"] == "entree"]
    ref = Counter(m.split(" (")[0] for r in journal if r["type"] == "entree_refusee" for m in r["motifs"])
    out += ["", f"4. Confirmations : {conf} → entrées : {len(ent)}"]
    for m, n in ref.most_common(8):
        out.append(f"   refus d'entrée : {n:>4}  {m}")
    return "\n".join(out)


def main() -> None:
    p = argparse.ArgumentParser(description="Entonnoir de décision d'un backtest")
    p.add_argument("path", help="dossier reports/strategy_... ou fichier journal.jsonl")
    print(funnel(load(Path(p.parse_args().path))))


if __name__ == "__main__":
    main()
