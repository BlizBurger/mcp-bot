"""Contrôle visuel d'une journée : bougies, volume profile, TPO, delta / CVD, FVG / OB, absorption.

Usage :
    python -m xauof.indicators.check --date 2026-09-15
→ affiche les niveaux clés et enregistre reports/check_2026-09-15.png
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from xauof.config import ROOT, load_config  # noqa: E402
from xauof.data import store  # noqa: E402
from xauof.indicators import orderflow, sessions, smc, tpo  # noqa: E402
from xauof.indicators import volume_profile as vp  # noqa: E402

BG, FG, GRID = "#0d1117", "#c9d1d9", "#21262d"
UP, DOWN, ACCENT, TPO_C = "#26a69a", "#ef5350", "#f0b429", "#58a6ff"


def day_bounds(day) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Journée CME en UTC : 18:00 New York la veille → 18:00 New York le jour même."""
    d = pd.Timestamp(day)
    start = (d - pd.Timedelta(days=1) + pd.Timedelta(hours=18)).tz_localize(sessions.NY)
    end = (d + pd.Timedelta(hours=18)).tz_localize(sessions.NY)
    return start.tz_convert("UTC"), end.tz_convert("UTC")


def analyse(cfg: dict, trades: pd.DataFrame, day) -> dict:
    p, of, sm = cfg["profile"], cfg["orderflow"], cfg["smc"]
    rth = tuple(cfg["sessions"]["rth"])
    full = sessions.day_trades(trades, day)
    reg = sessions.day_trades(trades, day, rth)
    if reg.empty:
        raise SystemExit(f"Aucune transaction en séance régulière le {day} (week-end ou jour férié ?)")

    vol = vp.volume_profile(reg, p["bin_size"], p["value_area_pct"], p["hvn_smooth"], p["lvn_ratio"])
    tp = tpo.tpo_profile(reg, p["bin_size"], cfg["sessions"]["tpo_period_min"],
                         cfg["sessions"]["initial_balance_min"], p["value_area_pct"])
    bars = orderflow.delta_bars(full, of["bar"])
    ab = of["absorption"]
    bars["absorption"] = orderflow.absorption(bars, ab["lookback"], ab["vol_mult"], ab["delta_frac"], ab["max_move_atr"])
    zones = smc.track(smc.find_fvgs(bars, sm["fvg_min_size"]) + smc.find_order_blocks(bars, sm["ob_displacement_atr"]), bars)
    return dict(full=full, reg=reg, vol=vol, tpo=tp, bars=bars, zones=zones, rth=rth)


def summary(day, a: dict) -> str:
    vol, tp, bars = a["vol"], a["tpo"], a["bars"]
    fmt = lambda xs: ", ".join(f"{x:.1f}" for x in xs) or "—"  # noqa: E731
    rngs = lambda rs: ", ".join(f"{lo:.1f}-{hi:.1f}" for lo, hi in rs) or "—"  # noqa: E731
    zones = a["zones"]
    count = lambda k, d: sum(z.kind == k and z.direction == d for z in zones)  # noqa: E731
    return "\n".join([
        f"=== GC {pd.Timestamp(day):%Y-%m-%d} — {len(a['full']):,} transactions ({len(a['reg']):,} en RTH) ===",
        f"Volume  : POC {vol.poc:.1f} | VA {vol.val:.1f} – {vol.vah:.1f}",
        f"TPO     : POC {tp.poc:.1f} | VA {tp.val:.1f} – {tp.vah:.1f} | IB {tp.ib_low:.1f} – {tp.ib_high:.1f}",
        f"HVN     : {fmt(vol.hvn)}",
        f"LVN     : {fmt(vol.lvn)}",
        f"Single prints : {rngs(tp.single_prints)} | queues : {rngs(tp.tails)}",
        f"Poor high : {'oui' if tp.poor_high else 'non'} | poor low : {'oui' if tp.poor_low else 'non'}",
        f"Delta du jour : {bars['delta'].sum():+,.0f} contrats",
        f"Absorptions : {(bars['absorption'] == 1).sum()} haussières, {(bars['absorption'] == -1).sum()} baissières",
        f"FVG : {count('fvg', 'bull')} bull / {count('fvg', 'bear')} bear | "
        f"OB : {count('ob', 'bull')} bull / {count('ob', 'bear')} bear",
    ])


def _style(ax):
    ax.set_facecolor(BG)
    ax.tick_params(colors=FG, labelsize=8)
    ax.grid(color=GRID, lw=0.6)
    for s in ax.spines.values():
        s.set_color(GRID)


def chart(day, a: dict, path: Path) -> Path:
    bars, vol, tp = a["bars"], a["vol"], a["tpo"]
    local = bars.index.tz_convert(sessions.NY)
    x = range(len(bars))
    fig = plt.figure(figsize=(16, 9), facecolor=BG)
    gs = fig.add_gridspec(2, 3, width_ratios=[6, 1, 1], height_ratios=[3, 1], wspace=0.03, hspace=0.08)
    ax = fig.add_subplot(gs[0, 0])
    axv = fig.add_subplot(gs[0, 1], sharey=ax)
    axt = fig.add_subplot(gs[0, 2], sharey=ax)
    axd = fig.add_subplot(gs[1, 0], sharex=ax)
    for a_ in (ax, axv, axt, axd):
        _style(a_)

    for z in a["zones"]:
        i0 = bars.index.get_indexer([z.formed_at])[0]
        end = z.invalidated_at or z.touched_at
        i1 = bars.index.get_indexer([end])[0] if end is not None else len(bars) - 1
        color = UP if z.direction == "bull" else DOWN
        ax.fill_between([i0, max(i1, i0 + 1)], z.low, z.high, color=color,
                        alpha=0.18 if z.kind == "fvg" else 0.32, lw=0)

    in_rth = sessions.rth_mask(bars.index, *a["rth"])
    if in_rth.any():
        ax.axvspan(in_rth.argmax() - 0.5, len(in_rth) - in_rth[::-1].argmax() - 0.5, color=FG, alpha=0.04, label="séance RTH")

    colors = [UP if c >= o else DOWN for o, c in zip(bars["open"], bars["close"])]
    ax.vlines(x, bars["low"], bars["high"], color=colors, lw=0.8)
    ax.bar(x, (bars["close"] - bars["open"]).abs().clip(lower=0.05), bottom=bars[["open", "close"]].min(axis=1),
           color=colors, width=0.7)
    for level, style, label in ((vol.poc, "-", "POC vol"), (vol.vah, "--", "VAH/VAL vol"), (vol.val, "--", None),
                                (tp.poc, ":", "POC TPO")):
        ax.axhline(level, color=ACCENT if "vol" in (label or "vol") else TPO_C, ls=style, lw=1, label=label)
    ax.axhspan(tp.ib_low, tp.ib_high, color=TPO_C, alpha=0.06, label="Initial Balance")
    for lvl in vol.lvn:
        ax.axhline(lvl, color=FG, lw=0.5, alpha=0.5)
    for sign, color, marker in ((1, UP, "^"), (-1, DOWN, "v")):
        hit = bars["absorption"] == sign
        y = bars["low"][hit] - 0.5 if sign > 0 else bars["high"][hit] + 0.5
        ax.scatter([i for i, h in zip(x, hit) if h], y, color=color, marker=marker, s=50, zorder=5,
                   label="absorption " + ("haussière" if sign > 0 else "baissière"))
    ax.legend(loc="upper left", fontsize=7, facecolor=BG, labelcolor=FG, edgecolor=GRID)
    ax.set_title(f"GC — {pd.Timestamp(day):%Y-%m-%d} (heure de New York)", color=FG)
    plt.setp(ax.get_xticklabels(), visible=False)

    bs = vol.profile.index.to_series().diff().median() or 0.5
    axv.barh(vol.profile.index, vol.profile.values, height=bs * 0.9,
             color=[ACCENT if vol.val <= p <= vol.vah else GRID for p in vol.profile.index])
    axv.set_title("Volume", color=FG, fontsize=9)
    axt.barh(tp.counts.index, tp.counts.values, height=bs * 0.9,
             color=[TPO_C if tp.val <= p <= tp.vah else GRID for p in tp.counts.index])
    axt.set_title("TPO", color=FG, fontsize=9)
    for a_ in (axv, axt):
        plt.setp(a_.get_yticklabels(), visible=False)

    axd.bar(x, bars["delta"], color=[UP if d >= 0 else DOWN for d in bars["delta"]], width=0.7)
    axc = axd.twinx()
    axc.plot(list(x), bars["cvd"], color=ACCENT, lw=1.2)
    axc.tick_params(colors=FG, labelsize=8)
    axd.set_ylabel("delta", color=FG, fontsize=8)
    axc.set_ylabel("CVD", color=ACCENT, fontsize=8)
    ticks = [i for i, t in enumerate(local) if t.minute == 0 and t.hour % 2 == 0]
    axd.set_xticks(ticks)
    axd.set_xticklabels([local[i].strftime("%H:%M") for i in ticks])

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fig.savefig(path, dpi=110, facecolor=BG, bbox_inches="tight")
    except OSError:
        # Windows : fichier encore ouvert dans la visionneuse ou en cours de synchro OneDrive
        path = path.with_name(f"{path.stem}_{pd.Timestamp.now():%H%M%S}{path.suffix}")
        fig.savefig(path, dpi=110, facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    return path


def run(cfg: dict, day, out_dir: Path = ROOT / "reports") -> Path:
    start, end = day_bounds(day)
    trades = store.load_range(store.gc_trades_dir(cfg), start, end)
    a = analyse(cfg, trades, day)
    print(summary(day, a))
    path = chart(day, a, Path(out_dir) / f"check_{pd.Timestamp(day):%Y-%m-%d}.png")
    print(f"Graphique : {path}")
    return path


def main() -> None:
    p = argparse.ArgumentParser(description="Contrôle visuel des indicateurs sur une journée GC")
    p.add_argument("--date", required=True, help="journée CME, ex. 2026-09-15")
    run(load_config(), p.parse_args().date)


if __name__ == "__main__":
    main()
