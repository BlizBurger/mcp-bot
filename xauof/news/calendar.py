"""Calendrier économique : fenêtres d'interdiction autour des annonces USD importantes.

Deux sources, fusionnées (news.calendar_dirs) :
- calendar/history/ : historique officiel pour les backtests (versionné, voir history.py) ;
- data/calendar/    : imports live du flux Forex Factory (local, ignoré par git).

Format de chaque dossier :
  usd_events.csv : time_utc,currency,impact,kind,event[,source]
                   (kind ∈ NFP, CPI, FOMC, OTHER ; impact = High)
  coverage.csv   : start_utc,end_utc[,scope]   (périodes où la liste est COMPLÈTE)

Une journée hors couverture = calendrier indisponible = aucune entrée.

Usage live :
    python -m xauof.news.calendar --import-ff   # importe la semaine en cours
    python -m xauof.news.calendar --status      # couverture et prochaines annonces
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from xauof.config import ROOT, load_config

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NY = "America/New_York"
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


def _utc(x) -> pd.Timestamp:
    ts = pd.Timestamp(x)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


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
        ev = pd.read_csv(ev_path)
        ev["time_utc"] = pd.to_datetime(ev["time_utc"], utc=True)
        cov = pd.read_csv(cov_path)
        ranges = [(_utc(a), _utc(b)) for a, b in zip(cov["start_utc"], cov["end_utc"])]
        return cls(ev.set_index("time_utc").sort_index(), ranges)

    @classmethod
    def load_many(cls, directories) -> "Calendar":
        cals = [cls.load(Path(d)) for d in directories]
        frames = [c.events.reset_index() for c in cals if not c.events.empty]
        if not frames:
            return cls(cls.empty().events, [r for c in cals for r in c.coverage])
        ev = (pd.concat(frames).drop_duplicates(["time_utc", "currency", "event"])
              .set_index("time_utc").sort_index())
        return cls(ev, [r for c in cals for r in c.coverage])

    def covers(self, start: pd.Timestamp, end: pd.Timestamp) -> bool:
        """Vrai si [start, end] est entièrement couvert (périodes contiguës fusionnées)."""
        t = start
        for a, b in sorted(self.coverage):
            if a <= t < b:
                t = b
            if t >= end:
                return True
        return False

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

    def upcoming(self, t: pd.Timestamp, currencies, n: int = 10) -> pd.DataFrame:
        ev = self.relevant(currencies)
        return ev[ev.index >= t].head(n)


def calendar_dirs(cfg: dict) -> list[Path]:
    return [ROOT / d for d in cfg["news"]["calendar_dirs"]]


def load_calendar(cfg: dict) -> Calendar:
    return Calendar.load_many(calendar_dirs(cfg))


def parse_ff(raw: list[dict]) -> tuple[pd.DataFrame, tuple[pd.Timestamp, pd.Timestamp]]:
    """Flux Forex Factory → (annonces « High », couverture de la semaine dimanche → dimanche, New York).

    Le flux liste TOUTES les annonces de la semaine (tous impacts) : la semaine est donc
    complète pour les annonces « High »."""
    if not raw:
        raise ValueError("flux Forex Factory vide")
    times = [pd.Timestamp(r["date"]) for r in raw]
    first_ny = min(times).tz_convert(NY)
    sunday = (first_ny - pd.Timedelta(days=(first_ny.weekday() + 1) % 7)).normalize()
    start, end = sunday.tz_convert("UTC"), (sunday + pd.Timedelta(days=7)).tz_convert("UTC")
    if max(times).tz_convert("UTC") >= end:
        raise ValueError("flux Forex Factory sur plus d'une semaine : format inattendu")
    rows = [{"time_utc": pd.Timestamp(r["date"]).tz_convert("UTC"), "currency": r["country"],
             "impact": r["impact"], "kind": classify(r["title"]), "event": r["title"], "source": "Forex Factory"}
            for r in raw if r.get("impact") == "High"]
    cols = ["time_utc", "currency", "impact", "kind", "event", "source"]
    return pd.DataFrame(rows, columns=cols), (start, end)


def save_import(directory: Path, events: pd.DataFrame, coverage: tuple[pd.Timestamp, pd.Timestamp]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    ev_path, cov_path = directory / "usd_events.csv", directory / "coverage.csv"
    ev = events.copy()
    if ev_path.exists():
        old = pd.read_csv(ev_path)
        old["time_utc"] = pd.to_datetime(old["time_utc"], utc=True)
        # la semaine importée remplace ce qu'on avait sur cette semaine (horaires mis à jour)
        old = old[(old["time_utc"] < coverage[0]) | (old["time_utc"] >= coverage[1])]
        ev = pd.concat([old, ev])
    ev.sort_values("time_utc").to_csv(ev_path, index=False)
    cov = pd.DataFrame([{"start_utc": coverage[0], "end_utc": coverage[1], "scope": "Forex Factory (High)"}])
    if cov_path.exists():
        cov = pd.concat([pd.read_csv(cov_path), cov.astype(str)])
    cov.astype(str).drop_duplicates(["start_utc", "end_utc"]).to_csv(cov_path, index=False)


def import_ff_week(directory: Path) -> int:
    """Importe la semaine en cours depuis Forex Factory (réseau). Retourne le nombre d'annonces « High »."""
    import requests

    resp = requests.get(FF_URL, timeout=20)
    resp.raise_for_status()
    events, coverage = parse_ff(resp.json())
    save_import(directory, events, coverage)
    return len(events)


def ensure_fresh(cfg: dict, now: pd.Timestamp, horizon_hours: int = 24) -> tuple[Calendar, str]:
    """Live : garantit un calendrier couvrant [now, now + horizon]. Importe si besoin.
    En cas d'échec, le calendrier reste non couvrant et le moteur bloque les entrées."""
    cal = load_calendar(cfg)
    end = now + pd.Timedelta(hours=horizon_hours)
    if cal.covers(now, end):
        return cal, "à jour"
    try:
        n = import_ff_week(ROOT / cfg["news"]["live_dir"])
        cal = load_calendar(cfg)
        msg = f"{n} annonces importées"
    except Exception as e:  # réseau, format : on ne trade pas sans calendrier
        return cal, f"import impossible ({e}) : entrées bloquées"
    if not cal.covers(now, end):
        return cal, "semaine importée mais horizon non couvert (fin de semaine) : entrées bloquées"
    return cal, msg


def main() -> None:
    p = argparse.ArgumentParser(description="Calendrier économique")
    p.add_argument("--import-ff", action="store_true", help="importe la semaine en cours (Forex Factory)")
    p.add_argument("--status", action="store_true", help="couverture et prochaines annonces USD")
    args = p.parse_args()
    cfg = load_config()
    if args.import_ff:
        print(f"{import_ff_week(ROOT / cfg['news']['live_dir'])} annonces « High » importées")
    cal = load_calendar(cfg)
    print(f"{len(cal.events)} annonces ; couverture :")
    for a, b in sorted(cal.coverage):
        print(f"  {a:%Y-%m-%d %H:%M} → {b:%Y-%m-%d %H:%M} UTC")
    if args.status:
        now = pd.Timestamp.now(tz="UTC")
        print(f"Aujourd'hui couvert : {'oui' if cal.covers(now, now + pd.Timedelta(hours=12)) else 'NON'}")
        print(cal.upcoming(now, cfg["news"]["currencies"]).to_string())


if __name__ == "__main__":
    main()
