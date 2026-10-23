"""Calendrier historique des annonces USD à fort impact (backtests), depuis les sources officielles.

Toutes les dates ci-dessous ont été relevées le 2026-10-23 :
- BLS (NFP, CPI, PPI, JOLTS) : noms des fichiers d'archives des communiqués
  (bls.gov/bls/news-release/<série>.htm), donc dates de publication RÉELLES.
  Les publications d'octobre 2025 ont été annulées pendant la paralysie budgétaire.
- BEA (PIB, revenus et dépenses personnels / PCE) : archive des communiqués
  (bea.gov/news/archive) ; septembre 2026 et les horaires de 2026 : calendrier
  BEA 2026 (bea.gov/news/schedule/full).
- Fed (FOMC) : federalreserve.gov/monetarypolicy/fomccalendars.htm (communiqué
  14:00, conférence de presse 14:30, minutes 14:00, heure de New York).

Champ couvert : NFP, CPI, PPI, JOLTS, PIB, PCE, FOMC (communiqué, conférence, minutes).
NON couverts (aucune source officielle d'historique accessible) : ventes au
détail (Census), ISM, inscriptions au chômage hebdomadaires, discours de la Fed.
En live, le flux Forex Factory couvre toutes les annonces « High ».

Usage :
    python -m xauof.news.history          # régénère calendar/history/*.csv
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from xauof.config import ROOT

NY = "America/New_York"
OUT_DIR = ROOT / "calendar" / "history"
COVERAGE = ("2024-10-01", "2026-10-01")       # [début, fin) en heure de New York

BLS = "BLS, archives des communiqués"
BEA_ARCH = "BEA, archive des communiqués"
BEA_2026 = "BEA, calendrier 2026"
FED = "Fed, calendrier FOMC"

# (date MMJJAAAA, mois de référence) — noms de fichiers des archives BLS
NFP = ["10042024", "11012024", "12062024", "01102025", "02072025", "03072025", "04042025", "05022025",
       "06062025", "07032025", "08012025", "09052025", "11202025", "12162025", "01092026", "02112026",
       "03062026", "04032026", "05082026", "06052026", "07022026", "08072026", "09042026"]
CPI = ["10102024", "11132024", "12112024", "01152025", "02122025", "03122025", "04102025", "05132025",
       "06112025", "07152025", "08122025", "09112025", "10242025", "12182025", "01132026", "02132026",
       "03112026", "04102026", "05122026", "06102026", "07142026", "08122026", "09112026"]
PPI = ["10112024", "11142024", "12122024", "01142025", "02132025", "03132025", "04112025", "05152025",
       "06122025", "07162025", "08142025", "09102025", "11252025", "01142026", "01302026", "02272026",
       "03182026", "04142026", "05132026", "06112026", "07152026", "08132026", "09102026"]
JOLTS = ["10292024", "12032024", "01072025", "02042025", "03112025", "04012025", "04292025", "06032025",
         "07012025", "07292025", "09032025", "09302025", "12092025", "01072026", "02052026", "03132026",
         "03312026", "05052026", "06022026", "06302026", "08042026", "09012026", "09292026"]

# BEA : (date, heure NY ou None si non publiée par la source, intitulé, source)
GDP = [("2024-10-30", "08:30", "PIB T3 2024 (avancé)", BEA_ARCH),
       ("2024-11-27", "08:30", "PIB T3 2024 (2e estimation)", BEA_ARCH),
       ("2024-12-19", "08:30", "PIB T3 2024 (3e estimation)", BEA_ARCH),
       ("2025-01-30", "08:30", "PIB T4 2024 (avancé)", BEA_ARCH),
       ("2025-02-27", "08:30", "PIB T4 2024 (2e estimation)", BEA_ARCH),
       ("2025-03-27", "08:30", "PIB T4 2024 (3e estimation)", BEA_ARCH),
       ("2025-04-30", "08:30", "PIB T1 2025 (avancé)", BEA_ARCH),
       ("2025-05-29", "08:30", "PIB T1 2025 (2e estimation)", BEA_ARCH),
       ("2025-06-26", "08:30", "PIB T1 2025 (3e estimation)", BEA_ARCH),
       ("2025-07-30", "08:30", "PIB T2 2025 (avancé)", BEA_ARCH),
       ("2025-08-28", "08:30", "PIB T2 2025 (2e estimation)", BEA_ARCH),
       ("2025-09-25", "08:30", "PIB T2 2025 (3e estimation)", BEA_ARCH),
       ("2025-12-23", None, "PIB T3 2025 (estimation initiale)", BEA_ARCH),
       ("2026-01-22", "08:30", "PIB T3 2025 (estimation mise à jour)", BEA_2026),
       ("2026-02-20", "08:30", "PIB T4 2025 (avancé)", BEA_2026),
       ("2026-03-13", "08:30", "PIB T4 2025 (2e estimation)", BEA_2026),
       ("2026-04-09", "08:30", "PIB T4 2025 (3e estimation)", BEA_2026),
       ("2026-04-30", "08:30", "PIB T1 2026 (avancé)", BEA_2026),
       ("2026-05-28", "08:30", "PIB T1 2026 (2e estimation)", BEA_2026),
       ("2026-06-25", "08:30", "PIB T1 2026 (3e estimation)", BEA_2026),
       ("2026-07-30", "08:30", "PIB T2 2026 (avancé)", BEA_2026),
       ("2026-08-26", "08:30", "PIB T2 2026 (2e estimation)", BEA_2026),
       ("2026-09-30", "08:30", "PIB T2 2026 (3e estimation)", BEA_2026)]
PCE = [("2024-10-31", "08:30", BEA_ARCH), ("2024-11-27", "08:30", BEA_ARCH), ("2024-12-20", "08:30", BEA_ARCH),
       ("2025-01-31", "08:30", BEA_ARCH), ("2025-02-28", "08:30", BEA_ARCH), ("2025-03-28", "08:30", BEA_ARCH),
       ("2025-04-30", "08:30", BEA_ARCH), ("2025-05-30", "08:30", BEA_ARCH), ("2025-06-27", "08:30", BEA_ARCH),
       ("2025-07-31", "08:30", BEA_ARCH), ("2025-08-29", "08:30", BEA_ARCH), ("2025-09-26", "08:30", BEA_ARCH),
       ("2025-12-05", None, BEA_ARCH), ("2025-12-23", None, BEA_ARCH),
       ("2026-01-22", "10:00", BEA_2026), ("2026-02-20", "08:30", BEA_2026), ("2026-03-13", "08:30", BEA_2026),
       ("2026-04-09", "08:30", BEA_2026), ("2026-04-30", "08:30", BEA_2026), ("2026-05-28", "08:30", BEA_2026),
       ("2026-06-25", "08:30", BEA_2026), ("2026-07-30", "08:30", BEA_2026), ("2026-08-26", "08:30", BEA_2026),
       ("2026-09-30", "08:30", BEA_2026)]

# FOMC : jour de décision (2e jour de réunion) et publication des minutes
FOMC_DECISIONS = ["2024-11-07", "2024-12-18", "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
                  "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10", "2026-01-28", "2026-03-18",
                  "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16"]
FOMC_MINUTES = ["2024-10-09", "2024-11-26", "2025-01-08", "2025-02-19", "2025-04-09", "2025-05-28",
                "2025-07-09", "2025-08-20", "2025-10-08", "2025-11-19", "2025-12-30", "2026-02-18",
                "2026-04-08", "2026-05-20", "2026-07-08", "2026-08-19"]

UNKNOWN_TIME_SLOTS = ("08:30", "10:00")       # horaire non publié : on bloque les deux créneaux usuels


def _utc(day: str, hhmm: str) -> pd.Timestamp:
    return (pd.Timestamp(day) + pd.Timedelta(hhmm + ":00")).tz_localize(NY).tz_convert("UTC")


def _mmddyyyy(code: str) -> str:
    return f"{code[4:]}-{code[:2]}-{code[2:4]}"


def build_events() -> pd.DataFrame:
    rows = []

    def add(day, hhmm, kind, event, source):
        rows.append({"time_utc": _utc(day, hhmm), "currency": "USD", "impact": "High", "kind": kind,
                     "event": event, "source": source})

    for codes, kind, name, hhmm in ((NFP, "NFP", "Non-Farm Payrolls (Employment Situation)", "08:30"),
                                    (CPI, "CPI", "CPI", "08:30"),
                                    (PPI, "OTHER", "PPI", "08:30"),
                                    (JOLTS, "OTHER", "JOLTS", "10:00")):
        for c in codes:
            add(_mmddyyyy(c), hhmm, kind, name, BLS)
    for day, hhmm, title, src in GDP:
        for slot in ([hhmm] if hhmm else UNKNOWN_TIME_SLOTS):
            add(day, slot, "OTHER", title + ("" if hhmm else " — horaire non publié, créneau bloqué par prudence"), src)
    for day, hhmm, src in PCE:
        for slot in ([hhmm] if hhmm else UNKNOWN_TIME_SLOTS):
            add(day, slot, "OTHER", "Revenus et dépenses personnels (PCE)"
                + ("" if hhmm else " — horaire non publié, créneau bloqué par prudence"), src)
    for day in FOMC_DECISIONS:
        add(day, "14:00", "FOMC", "FOMC : communiqué et décision de taux", FED)
        add(day, "14:30", "FOMC", "FOMC : conférence de presse", FED)
    for day in FOMC_MINUTES:
        add(day, "14:00", "FOMC", "FOMC : minutes", FED)
    df = pd.DataFrame(rows).sort_values("time_utc").reset_index(drop=True)
    start, end = (pd.Timestamp(x).tz_localize(NY).tz_convert("UTC") for x in COVERAGE)
    return df[(df["time_utc"] >= start) & (df["time_utc"] < end)]


def write(out_dir: Path = OUT_DIR) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ev = build_events()
    ev_path, cov_path = out_dir / "usd_events.csv", out_dir / "coverage.csv"
    ev.to_csv(ev_path, index=False)
    start, end = (pd.Timestamp(x).tz_localize(NY).tz_convert("UTC") for x in COVERAGE)
    pd.DataFrame([{"start_utc": start, "end_utc": end,
                   "scope": "NFP CPI PPI JOLTS PIB PCE FOMC (hors ventes au détail, ISM, inscriptions chômage)"}]
                 ).to_csv(cov_path, index=False)
    return ev_path, cov_path


def main() -> None:
    ev_path, _ = write()
    ev = pd.read_csv(ev_path)
    print(f"{len(ev)} annonces écrites dans {ev_path}")
    print(ev.groupby("kind").size().to_string())


if __name__ == "__main__":
    main()
