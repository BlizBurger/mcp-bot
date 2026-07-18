"""Génération du dossier Backtest/ : une image par trade (bougies M15 avec
entrée/SL/TP), la courbe d'équité et le rapport HTML."""

from __future__ import annotations

import logging
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # rendu fichier, pas de fenêtre
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402
import pandas as pd  # noqa: E402

log = logging.getLogger("smc.charts")

_UP, _DOWN = "#1a9850", "#d73027"


def _plot_candles(ax, df: pd.DataFrame) -> None:
    for x, row in enumerate(df.itertuples()):
        color = _UP if row.close >= row.open else _DOWN
        ax.plot([x, x], [row.low, row.high], color=color, linewidth=0.8, zorder=1)
        body_low, body_high = sorted((row.open, row.close))
        ax.add_patch(Rectangle((x - 0.35, body_low), 0.7,
                               max(body_high - body_low, 1e-9),
                               facecolor=color, edgecolor=color, zorder=2))
    # étiquettes de temps clairsemées
    step = max(len(df) // 8, 1)
    ax.set_xticks(range(0, len(df), step))
    ax.set_xticklabels([df["time"].iloc[i].strftime("%d/%m %H:%M")
                        for i in range(0, len(df), step)],
                       rotation=30, ha="right", fontsize=7)
    ax.margins(x=0.02)
    ax.grid(True, alpha=0.2)


def render_trade_png(ltf: pd.DataFrame, trade: pd.Series, path: Path,
                     bars_before: int = 40, bars_after: int = 10) -> None:
    """Bougies M15 autour du trade, avec entrée / SL / TP et repères temporels."""
    t0 = trade["open_time"] - pd.Timedelta(minutes=15 * bars_before)
    t1 = trade["close_time"] + pd.Timedelta(minutes=15 * bars_after)
    win = ltf[(ltf["time"] >= t0) & (ltf["time"] <= t1)].reset_index(drop=True)
    if len(win) < 5:
        return

    fig, ax = plt.subplots(figsize=(12, 6))
    _plot_candles(ax, win)

    ax.axhline(trade["entry"], color="#2563eb", linewidth=1.2,
               label=f"Entrée {trade['entry']:.5f}")
    ax.axhline(trade["sl"], color=_DOWN, linewidth=1.2, linestyle="--",
               label=f"SL {trade['sl']:.5f}")
    ax.axhline(trade["tp"], color=_UP, linewidth=1.2, linestyle="--",
               label=f"TP {trade['tp']:.5f}")

    for ts, lbl, color in ((trade["open_time"], "ouverture", "#2563eb"),
                           (trade["close_time"], "clôture", "#555555")):
        pos = win.index[win["time"] == ts]
        if len(pos):
            ax.axvline(pos[0], color=color, alpha=0.35, linewidth=1.0)
            ax.annotate(lbl, (pos[0], ax.get_ylim()[1]), fontsize=7,
                        color=color, ha="center", va="bottom")

    res = trade["result_r"]
    score = f" | score {int(trade['score'])}/6" if trade.get("score") else ""
    ax.set_title(f"{trade['pair']} {trade['direction'].upper()} — "
                 f"{trade['zone_kind']}{' + sweep' if trade['swept'] else ''} — "
                 f"sortie {trade['exit_kind']} : {res:+.2f} R{score}",
                 fontsize=11,
                 color=_UP if res > 0 else _DOWN)
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def render_equity_png(trades: pd.DataFrame, path: Path) -> None:
    equity = trades["result_r"].cumsum()
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(trades["open_time"], equity, color="#2563eb", linewidth=1.5)
    ax.axhline(0, color="#999", linewidth=0.8, linestyle="--")
    ax.fill_between(trades["open_time"], equity, 0,
                    where=(equity >= 0), color="#2563eb", alpha=0.10)
    ax.fill_between(trades["open_time"], equity, 0,
                    where=(equity < 0), color=_DOWN, alpha=0.10)
    ax.set_title(f"Courbe d'équité — {len(trades)} trades — "
                 f"total {equity.iloc[-1]:+.1f} R", fontsize=12)
    ax.set_ylabel("R cumulés")
    ax.grid(True, alpha=0.25)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def render_backtest_folder(trades: pd.DataFrame, data: dict, report_html: str,
                           project_root: Path, stamp: str) -> Path:
    """Crée Backtest/<horodatage>/ : equity.png, report.html, trades.csv et
    un PNG par trade dans trades/."""
    out = project_root / "Backtest" / stamp
    (out / "trades").mkdir(parents=True, exist_ok=True)

    trades.to_csv(out / "trades.csv", index=False)
    (out / "report.html").write_text(report_html, encoding="utf-8")
    if trades.empty:
        log.info("Aucun trade : dossier %s créé avec le rapport seul", out)
        return out

    render_equity_png(trades, out / "equity.png")
    for i, trade in trades.iterrows():
        pair = trade["pair"]
        if pair not in data:
            continue
        tag = "win" if trade["result_r"] > 0 else "loss"
        name = (f"{i + 1:03d}_{pair}_{trade['open_time']:%Y%m%d_%H%M}"
                f"_{trade['direction']}_{tag}.png")
        try:
            render_trade_png(data[pair]["ltf"], trade, out / "trades" / name)
        except Exception:  # noqa: BLE001 — un graphique raté ne bloque pas le reste
            log.exception("Échec du graphique pour le trade %s %s",
                          pair, trade["open_time"])
    log.info("Dossier Backtest généré : %s (%d images de trades)",
             out, len(list((out / 'trades').glob('*.png'))))
    return out


def render_setup_png(ltf: pd.DataFrame, setup, path: Path,
                     bars: int = 60) -> bool:
    """Image d'un setup LIVE pour l'alerte Telegram : dernières `bars` bougies
    M15 + niveau de liquidité balayé + extrême du sweep + zone d'entrée +
    entrée/SL/TP. Retourne True si l'image a été générée."""
    win = ltf.tail(bars).reset_index(drop=True)
    if len(win) < 10:
        return False
    n = len(win)
    up = setup.direction == "long"

    fig, ax = plt.subplots(figsize=(11, 6))
    _plot_candles(ax, win)

    # Zone d'entrée (rectangle translucide sur toute la largeur)
    ztop, zbot = setup.zone.top, setup.zone.bottom
    ax.add_patch(Rectangle((-0.5, zbot), n, max(ztop - zbot, 1e-9),
                           facecolor="#2563eb", alpha=0.12, zorder=0))
    ax.axhspan(zbot, ztop, color="#2563eb", alpha=0.04, zorder=0)

    # Niveau de liquidité balayé + extrême du sweep
    if setup.sweep is not None:
        ax.axhline(setup.sweep.level, color="#9333ea", linewidth=1.1,
                   linestyle=":", zorder=3,
                   label=f"Liquidité balayée {setup.sweep.level:.5f}")
        ax.annotate("SWEEP", (n * 0.02, setup.sweep.extreme), fontsize=8,
                    color="#9333ea", va="center")
        ax.plot([0, n - 1], [setup.sweep.extreme, setup.sweep.extreme],
                color="#9333ea", linewidth=0.7, alpha=0.5, zorder=1)

    # Entrée / SL / TP
    ax.axhline(setup.entry, color="#2563eb", linewidth=1.3,
               label=f"Entrée {setup.entry:.5f}")
    ax.axhline(setup.sl, color=_DOWN, linewidth=1.3, linestyle="--",
               label=f"SL {setup.sl:.5f}")
    ax.axhline(setup.tp, color=_UP, linewidth=1.3, linestyle="--",
               label=f"TP {setup.tp:.5f}")

    score = f" · score {setup.score}/{setup.max_score}" if setup.score else ""
    ax.set_title(f"{setup.pair} — {'LONG ▲' if up else 'SHORT ▼'} — "
                 f"{setup.zone.kind}{score}",
                 fontsize=12, color=_UP if up else _DOWN)
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=100)
    plt.close(fig)
    return True
