"""Contrôle de la passerelle GC → XAUUSD sur une période.

Usage :
    python -m xauof.bridge.check --start 2025-10-01 --end 2026-10-01
→ horloges alignées semaine par semaine, basis par mois, roulements,
  graphique reports/basis_<début>_<fin>.png
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from xauof.config import ROOT, load_config  # noqa: E402
from xauof.data import gc_bars, store  # noqa: E402
from xauof.bridge import basis as bs  # noqa: E402

BG, FG, GRID, ACCENT, BLUE = "#0d1117", "#c9d1d9", "#21262d", "#f0b429", "#58a6ff"


def run(cfg: dict, start, end, out_dir: Path = ROOT / "reports") -> dict:
    b = cfg["bridge"]
    gc = gc_bars.load_m1(cfg, start, end)
    xau = store.load_range(store.xauusd_m1_dir(cfg), start, end, index_name="time", bars=True)
    df = bs.align(gc, xau, cfg["broker"]["point"])
    if df.empty:
        raise SystemExit("Aucune minute commune GC / XAUUSD : vérifie les deux téléchargements.")
    df["basis_ref"] = bs.rolling_basis(df, b["window"], b["min_obs"])
    df["bruit"] = bs.basis_noise(df, b["window"], b["min_obs"])

    print(f"=== Passerelle GC → XAUUSD : {len(df):,} minutes communes ===\n")
    clock = bs.weekly_clock_check(gc["close"], xau["close"])
    bad = clock[clock["decalage_min"] != 0]
    print(f"Horloges : {len(clock) - len(bad)}/{len(clock)} semaines alignées "
          f"(corrélation médiane {clock['correlation'].median():.2f})")
    if len(bad):
        print("  ⚠ semaines décalées (fuseau / changement d'heure à corriger) :")
        print(bad.to_string(index=False))

    monthly = df["basis"].groupby(df.index.tz_localize(None).to_period("M")).agg(["median", "std", "min", "max"]).round(2)
    print("\nBasis GC − XAUUSD par mois ($) :")
    print(monthly.to_string())

    r = bs.rolls(df)
    if len(r):
        print("\nRoulements de contrat :")
        print(r.round({"basis_avant": 2, "basis_apres": 2, "saut": 2}).to_string(index=False))

    ok = (df["bruit"] <= b["max_noise"]).mean()
    print(f"\nMinutes où le report de niveaux est fiable (bruit ≤ {b['max_noise']} $) : {ok:.1%}")
    err = (df["basis"] - df["basis_ref"]).abs().dropna()
    print(f"Erreur de report (|basis − médiane 60 min|) : médiane {err.median():.2f} $, 95 % < {err.quantile(.95):.2f} $")

    path = chart(df, Path(out_dir) / f"basis_{pd.Timestamp(start):%Y-%m-%d}_{pd.Timestamp(end):%Y-%m-%d}.png")
    print(f"\nGraphique : {path}")
    return dict(df=df, clock=clock, monthly=monthly, rolls=r)


def chart(df: pd.DataFrame, path: Path) -> Path:
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(15, 7), sharex=True, facecolor=BG,
                                  gridspec_kw={"height_ratios": [3, 1]})
    for a in (ax, ax2):
        a.set_facecolor(BG)
        a.tick_params(colors=FG, labelsize=8)
        a.grid(color=GRID, lw=0.6)
    hourly = df.resample("1h").median(numeric_only=True).dropna(subset=["basis"])
    ax.plot(hourly.index, hourly["basis"], color=BLUE, lw=0.8, label="basis GC − XAUUSD (médiane horaire)")
    ax.plot(hourly.index, hourly["basis_ref"], color=ACCENT, lw=1, label="référence glissante 60 min")
    ax.set_ylabel("$", color=FG)
    ax.legend(loc="upper right", fontsize=8, facecolor=BG, labelcolor=FG, edgecolor=GRID)
    ax.set_title("Écart future GC / spot XAUUSD", color=FG)
    ax2.plot(hourly.index, hourly["bruit"], color=FG, lw=0.7)
    ax2.set_ylabel("bruit ($)", color=FG)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fig.savefig(path, dpi=110, facecolor=BG, bbox_inches="tight")
    except OSError:
        path = path.with_name(f"{path.stem}_{pd.Timestamp.now():%H%M%S}{path.suffix}")
        fig.savefig(path, dpi=110, facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    return path


def main() -> None:
    p = argparse.ArgumentParser(description="Contrôle de la passerelle GC → XAUUSD")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True, help="date de fin exclue")
    args = p.parse_args()
    run(load_config(), args.start, args.end)


if __name__ == "__main__":
    main()
