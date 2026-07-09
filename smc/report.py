"""Rapport de backtest HTML autonome (courbe d'équité en SVG inline, aucune
dépendance graphique)."""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from smc import WARNINGS

_CSS = """
body { font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 960px;
       color: #1a1a2e; }
h1 { font-size: 1.5rem; } h2 { font-size: 1.15rem; margin-top: 2rem; }
table { border-collapse: collapse; width: 100%; font-size: 0.85rem; }
th, td { border: 1px solid #ddd; padding: 4px 8px; text-align: right; }
th { background: #f0f0f5; } td:first-child, th:first-child { text-align: left; }
.win { color: #0a7d32; } .loss { color: #c0392b; }
.warn { background: #fff6e5; border: 1px solid #e6b800; border-radius: 6px;
        padding: 1rem; font-size: 0.85rem; }
.stats { display: flex; gap: 1rem; flex-wrap: wrap; }
.stat { background: #f0f0f5; border-radius: 8px; padding: 0.8rem 1.2rem; }
.stat b { display: block; font-size: 1.3rem; }
svg { background: #fafafa; border: 1px solid #eee; border-radius: 6px; }
"""


def _equity_svg(equity: list[float], width: int = 900, height: int = 260) -> str:
    """Courbe d'équité (en R cumulés) en SVG pur."""
    if len(equity) < 2:
        return "<p>Pas assez de trades pour tracer une courbe d'équité.</p>"
    lo, hi = min(equity + [0.0]), max(equity + [0.0])
    span = (hi - lo) or 1.0
    pad = 30
    def x(i): return pad + i * (width - 2 * pad) / (len(equity) - 1)
    def y(v): return height - pad - (v - lo) * (height - 2 * pad) / span
    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(equity))
    zero = y(0.0)
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
        f'aria-label="Courbe d\'équité en R cumulés">'
        f'<line x1="{pad}" y1="{zero:.1f}" x2="{width-pad}" y2="{zero:.1f}" '
        f'stroke="#bbb" stroke-dasharray="4 3"/>'
        f'<polyline points="{pts}" fill="none" stroke="#2563eb" stroke-width="2"/>'
        f'<text x="{pad}" y="{zero-6:.1f}" font-size="11" fill="#888">0 R</text>'
        f'<text x="{pad}" y="16" font-size="11" fill="#888">max {hi:.1f} R</text>'
        f"</svg>"
    )


def _grp_stats(df: pd.DataFrame) -> dict:
    """Mini-stats pour les tableaux de ventilation."""
    if df.empty:
        return {"n": 0}
    wins = df[df["result_r"] > 0]
    gross_win = wins["result_r"].sum()
    gross_loss = abs(df[df["result_r"] <= 0]["result_r"].sum())
    return {"n": len(df), "wr": len(wins) / len(df),
            "avg": df["result_r"].mean(), "tot": df["result_r"].sum(),
            "pf": (gross_win / gross_loss) if gross_loss > 0 else float("inf")}


def _breakdown_table(trades: pd.DataFrame, key, title: str) -> str:
    """Tableau de stats ventilé par `key` (nom de colonne ou fonction ligne->label)."""
    if trades.empty:
        return ""
    labels = trades[key] if isinstance(key, str) else trades.apply(key, axis=1)
    rows = []
    for label in labels.unique():
        s = _grp_stats(trades[labels == label])
        pf_txt = "∞" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
        rows.append(f"<tr><td>{label}</td><td>{s['n']}</td>"
                    f"<td>{s['wr']*100:.0f}%</td><td>{s['avg']:+.2f}</td>"
                    f"<td>{s['tot']:+.1f}</td><td>{pf_txt}</td></tr>")
    return (f"<h2>{title}</h2><table><tr><th></th><th>Trades</th><th>Win rate</th>"
            f"<th>R moyen</th><th>Total R</th><th>Profit factor</th></tr>"
            + "".join(rows) + "</table>")


_WEEKDAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def _duration_bucket(row) -> str:
    hours = (row["close_time"] - row["open_time"]).total_seconds() / 3600
    if hours < 1:
        return "< 1h"
    if hours < 4:
        return "1-4h"
    if hours < 24:
        return "4-24h"
    return "> 24h"


def _fmt_stats(stats: dict) -> str:
    if not stats.get("trades"):
        return "<p><b>Aucun trade généré sur la période.</b></p>"
    pf = stats["profit_factor"]
    return f"""
<div class="stats">
  <div class="stat"><b>{stats['trades']}</b>trades</div>
  <div class="stat"><b>{stats['win_rate']*100:.1f}%</b>win rate</div>
  <div class="stat"><b>{stats['avg_r']:+.2f}</b>R moyen</div>
  <div class="stat"><b>{stats['total_r']:+.1f} R</b>total</div>
  <div class="stat"><b>{'∞' if pf == float('inf') else f'{pf:.2f}'}</b>profit factor</div>
  <div class="stat"><b>{stats['max_drawdown_r']:.1f} R</b>drawdown max</div>
</div>"""


def render_report(trades: pd.DataFrame, stats: dict, per_pair: dict,
                  days: int) -> str:
    warn_items = "".join(f"<li>{w}</li>" for w in WARNINGS)

    breakdowns = ""
    if trades.empty:
        equity_html = "<p>Aucun trade.</p>"
        trades_rows = ""
        pair_rows = ""
    else:
        if "session" in trades.columns:
            breakdowns += _breakdown_table(trades, "session", "Stats par killzone")
        breakdowns += _breakdown_table(trades, "zone_kind", "Stats par type de zone")
        breakdowns += _breakdown_table(trades, "direction", "Stats par sens")
        breakdowns += _breakdown_table(
            trades, lambda r: _WEEKDAYS[r["open_time"].weekday()],
            "Stats par jour de la semaine")
        breakdowns += _breakdown_table(trades, _duration_bucket,
                                       "Stats par durée de trade")
        if "exit_kind" in trades.columns:
            breakdowns += _breakdown_table(trades, "exit_kind",
                                           "Stats par type de sortie")
        equity_html = _equity_svg(trades["result_r"].cumsum().tolist())
        trades_rows = "".join(
            f"<tr><td>{t.pair}</td><td>{t.direction}</td><td>{t.zone_kind}"
            f"{' + sweep' if t.swept else ''}</td>"
            f"<td>{t.open_time:%Y-%m-%d %H:%M}</td><td>{t.close_time:%m-%d %H:%M}</td>"
            f"<td>{t.entry:.5f}</td><td>{t.sl:.5f}</td><td>{t.tp:.5f}</td>"
            f"<td>{getattr(t, 'exit_kind', '')}</td>"
            f"<td class=\"{'win' if t.result_r > 0 else 'loss'}\">{t.result_r:+.1f} R</td></tr>"
            for t in trades.itertuples())
        pair_cells = []
        for p, s in per_pair.items():
            if s.get("trades"):
                pf = s["profit_factor"]
                pf_txt = "∞" if pf == float("inf") else f"{pf:.2f}"
                cells = (f"<td>{s['win_rate']*100:.0f}%</td><td>{s['avg_r']:+.2f}</td>"
                         f"<td>{s['total_r']:+.1f}</td><td>{pf_txt}</td>")
            else:
                cells = "<td>—</td><td>—</td><td>—</td><td>—</td>"
            pair_cells.append(
                f"<tr><td>{p}</td><td>{s.get('trades', 0)}</td>{cells}</tr>")
        pair_rows = "".join(pair_cells)

    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<title>Backtest SMC/AMD — {datetime.now():%Y-%m-%d %H:%M}</title>
<style>{_CSS}</style></head><body>
<h1>Backtest SMC/AMD — {days} jours — généré le {datetime.now():%Y-%m-%d %H:%M}</h1>

<div class="warn"><b>⚠️ Limites de ce backtest (à lire avant d'interpréter) :</b>
<ul>{warn_items}</ul></div>

<h2>Statistiques globales (en multiples de R)</h2>
<p style="font-size:0.85rem;color:#666">Un spread fixe par paire (configurable
dans <code>config.yaml</code>) est déduit de chaque trade — approximation
grossière, le spread réel varie. Le slippage n'est pas modélisé.</p>
{_fmt_stats(stats)}

<h2>Courbe d'équité (R cumulés)</h2>
{equity_html}

<h2>Stats par paire</h2>
<table><tr><th>Paire</th><th>Trades</th><th>Win rate</th><th>R moyen</th>
<th>Total R</th><th>Profit factor</th></tr>{pair_rows}</table>

{breakdowns}

<h2>Trades ({stats.get('trades', 0)})</h2>
<table><tr><th>Paire</th><th>Sens</th><th>Zone</th><th>Ouverture</th>
<th>Clôture</th><th>Entrée</th><th>SL</th><th>TP</th><th>Sortie</th>
<th>Résultat</th></tr>
{trades_rows}</table>
</body></html>"""
