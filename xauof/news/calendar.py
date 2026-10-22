"""Calendrier économique : fenêtres d'interdiction autour des annonces USD importantes.

Format attendu dans news.calendar_dir :
  usd_events.csv : time_utc,currency,impact,kind,event
                   (kind ∈ NFP, CPI, FOMC, OTHER ; impact = High)
  coverage.csv   : start_utc,end_utc   (périodes où le calendrier est COMPLET)

Une journée hors couverture = calendrier indisponible = aucune entrée (sauf
réglage explicite news.missing_calendar: ignore_backtest_only en backtest).

Import de la semaine en cours (live) depuis le flux public Forex Factory :
    python -m xauof.news.calendar --import-ff
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from xauof.config import ROOT, load_config

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
KIND_KEYWORDS = {
    "NFP": ("non-farm", "nonfarm"),
    "CPI": ("cpi",),
    "FOMC": ("fomc", "federal funds rate", "fed interest rate"),
}


def classify(title: str) -> str:
    t = title.lower()
    for kind, words in KIND_KEYWORDS.items():
        if any(w in t for w in words):
            return kind
    return "OTHER"


@dataclass
class Calendar:
    events: pd.DataFrame          # index time_utc ; colonnes currency, impact, kind, event
    coverage: list[tuple[pd.Timestamp, pd.Timestamp]]

    @classmethod
    def empty(cls) -> "Calendar":
        return cls(pd.DataFrame(columns=["currency", "impact", "kind", "event"],
                                index=pd.DatetimeIndex([], tz="UTC", name="time_utc")), [])

    @classmethod
    def load(cls, directory: Path) -> "Calendar":
        ev_path, cov_path = directory / "usd_events.csv", directory / "coverage.csv"
        if not ev_path.exists() or not cov_path.exists():
            return cls.empty()
        ev = pd.read_csv(ev_path, parse_dates=["time_utc"])
        ev["time_utc"] = pd.to_datetime(ev["time_utc"], utc=True)
        cov = pd.read_csv(cov_path)
        ranges = [(pd.Timestamp(a, tz="UTC") if pd.Timestamp(a).tzinfo is None else pd.Timestamp(a).tz_convert("UTC"),
                   pd.Timestamp(b, tz="UTC") if pd.Timestamp(b).tzinfo is None else pd.Timestamp(b).tz_convert("UTC"))
                  for a, b in zip(cov["start_utc"], cov["end_utc"])]
        return cls(ev.set_index("time_utc").sort_index(), ranges)

    def covers(self, start: pd.Timestamp, end: pd.Timestamp) -> bool:
        return any(a <= start and end <= b for a, b in self.coverage)

    def relevant(self, currencies) -> pd.DataFrame:
        return self.events[self.events["currency"].isin(currencies) & (self.events["impact"] == "High")]

    def blackout(self, t: pd.Timestamp, before_min: int, after_min: int, currencies) -> str | None:
        """Annonce dont la fenêtre [T-before, T+after] contient t, sinon None."""
        ev = self.relevant(currencies)
        lo, hi = t - pd.Timedelta(minutes=after_min), t + pd.Timedelta(minutes=before_min)
        hit = ev[(ev.index >= lo) & (ev.index <= hi)]
        return None if hit.empty else f"{hit['event'].iloc[0]} ({hit.index[0]:%Y-%m-%d %H:%M} UTC)"

    def flatten_due(self, t: pd.Timestamp, before_min: int, kinds, currencies) -> str | None:
        """Annonce majeure (NFP, CPI, FOMC) dans moins de before_min minutes, ou en cours de fenêtre."""
        ev = self.relevant(currencies)
        ev = ev[ev["kind"].isin(kinds)]
        hit = ev[(ev.index >= t - pd.Timedelta(minutes=before_min)) & (ev.index <= t + pd.Timedelta(minutes=before_min))]
        return None if hit.empty else f"{hit['event'].iloc[0]} ({hit.index[0]:%Y-%m-%d %H:%M} UTC)"


def import_ff_week(directory: Path) -> int:
    """Ajoute les annonces de la semaine (flux Forex Factory) et la couverture correspondante."""
    import requests

    raw = requests.get(FF_URL, timeout=20).json()
    rows = [{"time_utc": pd.Timestamp(r["date"]).tz_convert("UTC"), "currency": r["country"],
             "impact": r["impact"], "kind": classify(r["title"]), "event": r["title"]}
            for r in raw if r.get("impact") == "High"]
    times = [pd.Timestamp(r["date"]).tz_convert("UTC") for r in raw]
    week_start = min(times).normalize() - pd.Timedelta(days=min(times).weekday())
    directory.mkdir(parents=True, exist_ok=True)
    ev_path, cov_path = directory / "usd_events.csv", directory / "coverage.csv"
    new = pd.DataFrame(rows)
    if ev_path.exists():
        new = pd.concat([pd.read_csv(ev_path, parse_dates=["time_utc"]), new])
    new["time_utc"] = pd.to_datetime(new["time_utc"], utc=True)
    new.drop_duplicates(["time_utc", "currency", "event"]).sort_values("time_utc").to_csv(ev_path, index=False)
    cov = pd.DataFrame([{"start_utc": week_start, "end_utc": week_start + pd.Timedelta(days=7)}])
    if cov_path.exists():
        cov = pd.concat([pd.read_csv(cov_path), cov.astype(str)])
    cov.astype(str).drop_duplicates().to_csv(cov_path, index=False)
    return len(rows)


def main() -> None:
    p = argparse.ArgumentParser(description="Calendrier économique")
    p.add_argument("--import-ff", action="store_true", help="importe la semaine en cours (Forex Factory)")
    args = p.parse_args()
    directory = ROOT / load_config()["news"]["calendar_dir"]
    if args.import_ff:
        print(f"{import_ff_week(directory)} annonces à fort impact importées dans {directory}")
    cal = Calendar.load(directory)
    print(f"Couverture : {len(cal.coverage)} période(s) ; {len(cal.events)} annonces")


if __name__ == "__main__":
    main()
