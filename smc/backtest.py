"""Backtest : rejoue la stratégie AMD sur l'historique MT5.

Lancement : python -m smc.backtest [--days 60] [--pairs EURUSD,USDJPY]

⚠️ Limites assumées (voir smc.WARNINGS) : simulation sur mèches M15, pas tick
par tick — spread et slippage réels non modélisés, si SL et TP sont touchés
dans la même bougie le trade est compté PERDANT (hypothèse conservatrice).
Les résultats réels seront probablement moins bons.

Utilise exactement les mêmes fonctions de détection que le scanner live
(smc.core.find_amd_setup) pour que « ce qui est backtesté » = « ce qui serait
alerté ».
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from smc.config import load_config, load_env, pip_size_fallback
from smc.core import find_amd_setup
from smc.logging_setup import log_warnings_banner, setup_logging
from smc.mt5_client import MT5Client
from smc.report import render_report

log = logging.getLogger("smc.backtest")


@dataclass
class Trade:
    pair: str
    direction: str
    zone_kind: str
    swept: bool
    open_time: datetime
    close_time: datetime
    entry: float
    sl: float
    tp: float
    exit_price: float
    result_r: float   # multiple de R après déduction du spread
    spread_r: float   # coût du spread exprimé en R (déjà déduit de result_r)


def simulate_pair(pair: str, htf: pd.DataFrame, ltf: pd.DataFrame,
                  cfg: dict, pip: float,
                  daily_trades: dict) -> list[Trade]:
    """Rejoue bougie par bougie. `daily_trades` (partagé entre paires) fait
    respecter la règle max_trades_per_day globale, comme en live."""
    trades: list[Trade] = []
    max_per_day = cfg["backtest"]["max_trades_per_day"]
    spread_cfg = cfg["backtest"].get("spread_pips", {})
    spread = float(spread_cfg.get(pair, spread_cfg.get("default", 0.0))) * pip
    tail = cfg["scanner"]["history_bars_ltf"]
    open_until: datetime | None = None  # pas de nouveau signal tant qu'un trade est ouvert

    for i in range(50, len(ltf) - 1):
        now = ltf["time"].iloc[i]
        day = now.date()
        if daily_trades.get(day, 0) >= max_per_day:
            continue
        if open_until and now < open_until:
            continue

        ltf_slice = ltf.iloc[max(0, i + 1 - tail):i + 1].reset_index(drop=True)
        htf_slice = htf[htf["time"] <= now].tail(cfg["scanner"]["history_bars_htf"]) \
            .reset_index(drop=True)
        setup = find_amd_setup(pair, htf_slice, ltf_slice, cfg, pip, now=now)
        if setup is None:
            continue

        # Simulation de la sortie sur les bougies suivantes (mèches M15)
        entry, sl, tp = setup.entry, setup.sl, setup.tp
        rr = setup.rr
        exit_price, result_r, close_time = None, None, None
        for j in range(i + 1, len(ltf)):
            bar = ltf.iloc[j]
            if setup.direction == "long":
                hit_sl, hit_tp = bar["low"] <= sl, bar["high"] >= tp
            else:
                hit_sl, hit_tp = bar["high"] >= sl, bar["low"] <= tp
            if hit_sl:  # SL prioritaire si les deux sont touchés (conservateur)
                exit_price, result_r, close_time = sl, -1.0, bar["time"]
                break
            if hit_tp:
                exit_price, result_r, close_time = tp, rr, bar["time"]
                break
        if exit_price is None:
            continue  # trade encore ouvert en fin d'historique : ignoré

        # Coût du spread : payé une fois par aller-retour, exprimé en R
        risk = abs(entry - sl)
        spread_r = spread / risk if risk > 0 else 0.0
        result_r -= spread_r

        trades.append(Trade(pair=pair, direction=setup.direction,
                            zone_kind=setup.zone.kind, swept=setup.sweep is not None,
                            open_time=now, close_time=close_time,
                            entry=entry, sl=sl, tp=tp,
                            exit_price=exit_price, result_r=result_r,
                            spread_r=spread_r))
        daily_trades[day] = daily_trades.get(day, 0) + 1
        open_until = close_time
    return trades


def compute_stats(trades_df: pd.DataFrame) -> dict:
    if trades_df.empty:
        return {"trades": 0}
    wins = trades_df[trades_df["result_r"] > 0]
    losses = trades_df[trades_df["result_r"] <= 0]
    gross_win = wins["result_r"].sum()
    gross_loss = abs(losses["result_r"].sum())
    return {
        "trades": len(trades_df),
        "win_rate": len(wins) / len(trades_df),
        "avg_r": trades_df["result_r"].mean(),
        "total_r": trades_df["result_r"].sum(),
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        "max_drawdown_r": float(
            (trades_df["result_r"].cumsum().cummax()
             - trades_df["result_r"].cumsum()).max()),
    }


def run_backtest(cfg: dict, pairs: list[str], days: int) -> pd.DataFrame:
    env = load_env()
    client = MT5Client(env)
    client.connect()
    end = datetime.now()
    start = end - timedelta(days=days)
    all_trades: list[Trade] = []
    daily_trades: dict = {}  # partagé : 1 trade/jour toutes paires confondues
    try:
        for pair in pairs:
            log.info("Backtest %s (%d jours)...", pair, days)
            try:
                pip = client.pip_size(pair)
            except Exception:
                pip = pip_size_fallback(pair, cfg)
            htf = client.get_rates_range(pair, cfg["timeframes"]["htf"],
                                         start - timedelta(days=30), end)
            ltf = client.get_rates_range(pair, cfg["timeframes"]["ltf"], start, end)
            trades = simulate_pair(pair, htf, ltf, cfg, pip, daily_trades)
            log.info("%s : %d trade(s)", pair, len(trades))
            all_trades.extend(trades)
    finally:
        client.shutdown()
    df = pd.DataFrame([t.__dict__ for t in all_trades])
    if not df.empty:
        df = df.sort_values("open_time").reset_index(drop=True)
    return df


def main() -> None:
    cfg = load_config()
    setup_logging(cfg["paths"]["logs"], "backtest")
    log_warnings_banner(log)

    parser = argparse.ArgumentParser(description="Backtest SMC/AMD sur historique MT5")
    parser.add_argument("--days", type=int, default=cfg["backtest"]["days"])
    parser.add_argument("--pairs", type=str, default=",".join(cfg["pairs"]))
    args = parser.parse_args()
    pairs = [p.strip().upper() for p in args.pairs.split(",") if p.strip()]

    trades_df = run_backtest(cfg, pairs, args.days)

    out_dir = Path(cfg["paths"]["reports"])
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    csv_path = out_dir / f"trades_{stamp}.csv"
    trades_df.to_csv(csv_path, index=False)

    stats = compute_stats(trades_df)
    per_pair = {p: compute_stats(trades_df[trades_df["pair"] == p])
                for p in pairs} if not trades_df.empty else {}
    html_path = out_dir / f"report_{stamp}.html"
    html_path.write_text(render_report(trades_df, stats, per_pair, args.days),
                         encoding="utf-8")
    # copie stable pour le dashboard
    (out_dir / "latest.html").write_text(
        render_report(trades_df, stats, per_pair, args.days), encoding="utf-8")

    log.info("Terminé : %d trades — rapport : %s (CSV : %s)",
             stats.get("trades", 0), html_path, csv_path)
    if stats.get("trades"):
        log.info("Win rate %.1f%% | R moyen %.2f | Profit factor %.2f | Total %.1fR",
                 stats["win_rate"] * 100, stats["avg_r"],
                 stats["profit_factor"], stats["total_r"])


if __name__ == "__main__":
    main()
