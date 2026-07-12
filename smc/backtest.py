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
from smc.core import in_window
from smc.strategies import STRATEGIES, get_strategy
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
    session: str      # killzone d'ouverture (london / newyork / autre)
    exit_kind: str    # tp / sl / breakeven / time
    score: int        # score de confluence du setup (0 si stratégie sans scoring)
    strategy: str     # stratégie qui a produit le trade
    amd: bool         # pattern AMD confirmé au moment du setup (bonus)
    amd_level: float  # compression la plus stricte où l'AMD se confirme (0 = aucun)


def _session_of(ts: datetime, cfg: dict) -> str:
    for name, win in cfg["sessions"]["killzones"].items():
        if in_window(ts, win):
            return name
    return "autre"


def simulate_pair(pair: str, htf: pd.DataFrame, ltf: pd.DataFrame,
                  cfg: dict, pip: float, daily_trades: dict | None = None,
                  d1: pd.DataFrame | None = None) -> list[Trade]:
    """Rejoue bougie par bougie une seule paire.

    `daily_trades=None` désactive le quota journalier : simulate_all applique
    alors la règle max_trades_per_day chronologiquement toutes paires
    confondues (comme en live), au lieu de servir les paires dans l'ordre de
    la boucle."""
    trades: list[Trade] = []
    max_per_day = cfg["backtest"]["max_trades_per_day"]
    spread_cfg = cfg["backtest"].get("spread_pips", {})
    spread = float(spread_cfg.get(pair, spread_cfg.get("default", 0.0))) * pip
    fill_timeout = cfg["backtest"].get("zone_fill_timeout_bars", 16)
    exits = cfg.get("exits", {})
    be_r = float(exits.get("breakeven_after_r", 0) or 0)
    max_bars = int(exits.get("max_holding_bars", 0) or 0)
    partial_r = float(exits.get("partial_at_r", 0) or 0)
    partial_frac = float(exits.get("partial_fraction", 0.5))
    tail = cfg["scanner"]["history_bars_ltf"]
    open_until: datetime | None = None  # pas de nouveau signal tant qu'un trade est ouvert

    for i in range(50, len(ltf) - 1):
        now = ltf["time"].iloc[i]
        if daily_trades is not None and \
                daily_trades.get(now.date(), 0) >= max_per_day:
            continue
        if open_until and now < open_until:
            continue

        ltf_slice = ltf.iloc[max(0, i + 1 - tail):i + 1].reset_index(drop=True)
        htf_slice = htf[htf["time"] <= now].tail(cfg["scanner"]["history_bars_htf"]) \
            .reset_index(drop=True)
        d1_slice = None
        if d1 is not None:
            d1_slice = d1[d1["time"] <= now].tail(
                cfg["scanner"].get("history_bars_d1", 60)).reset_index(drop=True)
        strategy_fn = get_strategy(cfg.get("strategy_name", "amd_asian"))
        setup = strategy_fn(pair, {"htf": htf_slice, "ltf": ltf_slice,
                                   "d1": d1_slice}, cfg, pip, now=now)
        if setup is None:
            continue
        is_long = setup.direction == "long"

        # --- Remplissage : ordre limite dans la zone, ou marché au signal ----
        if setup.entry_is_limit:
            sig_close = float(ltf["close"].iloc[i])
            fill_idx, entry = None, None
            if (is_long and sig_close <= setup.entry) or \
               (not is_long and sig_close >= setup.entry):
                fill_idx, entry = i, sig_close  # déjà au-delà du limite : rempli au marché
            else:
                for j in range(i + 1, min(i + 1 + fill_timeout, len(ltf))):
                    bar = ltf.iloc[j]
                    if (is_long and bar["low"] <= setup.entry) or \
                       (not is_long and bar["high"] >= setup.entry):
                        fill_idx, entry = j, setup.entry
                        break
            if fill_idx is None:
                continue  # jamais rempli : pas de trade
        else:
            fill_idx, entry = i, setup.entry

        # TP recalculé depuis le prix de remplissage réel (SL structurel inchangé)
        sl, rr = setup.sl, setup.rr
        risk = abs(entry - sl)
        if risk <= 0:
            continue
        tp = entry + rr * risk if is_long else entry - rr * risk
        open_time = ltf["time"].iloc[fill_idx]

        # --- Gestion : SL (prioritaire), TP, prise partielle, breakeven,
        # sortie au temps. `realized` = R déjà encaissés par la prise
        # partielle ; `remaining` = fraction de position encore ouverte.
        cur_sl = sl
        realized, remaining = 0.0, 1.0
        exit_price, result_r, close_time, exit_kind = None, None, None, None
        for j in range(fill_idx + 1, len(ltf)):
            bar = ltf.iloc[j]
            hit_sl = bar["low"] <= cur_sl if is_long else bar["high"] >= cur_sl
            hit_tp = bar["high"] >= tp if is_long else bar["low"] <= tp
            if hit_sl:  # SL prioritaire si les deux sont touchés (conservateur)
                exit_price, close_time = cur_sl, bar["time"]
                unit = (cur_sl - entry) / risk if is_long else (entry - cur_sl) / risk
                result_r = realized + remaining * unit
                exit_kind = "breakeven" if cur_sl != sl else "sl"
                break
            if hit_tp:
                exit_price, close_time, exit_kind = tp, bar["time"], "tp"
                result_r = realized + remaining * rr
                break
            if partial_r > 0 and remaining == 1.0:  # prise partielle à +N R
                reached = bar["high"] >= entry + partial_r * risk if is_long \
                    else bar["low"] <= entry - partial_r * risk
                if reached:
                    realized = partial_frac * partial_r
                    remaining = 1.0 - partial_frac
            if be_r > 0:  # trade en gain de be_r x R : SL remonté à l'entrée
                reached = bar["high"] >= entry + be_r * risk if is_long \
                    else bar["low"] <= entry - be_r * risk
                if reached:
                    cur_sl = max(cur_sl, entry) if is_long else min(cur_sl, entry)
            if max_bars and (j - fill_idx) >= max_bars:  # sortie au temps
                exit_price, close_time = float(bar["close"]), bar["time"]
                unit = (exit_price - entry) / risk if is_long \
                    else (entry - exit_price) / risk
                result_r = realized + remaining * unit
                exit_kind = "time"
                break
        if exit_price is None:
            continue  # trade encore ouvert en fin d'historique : ignoré

        # Coût du spread : payé une fois par aller-retour, exprimé en R
        spread_r = spread / risk
        result_r -= spread_r

        trades.append(Trade(pair=pair, direction=setup.direction,
                            zone_kind=setup.zone.kind, swept=setup.sweep is not None,
                            open_time=open_time, close_time=close_time,
                            entry=entry, sl=sl, tp=tp,
                            exit_price=exit_price, result_r=result_r,
                            spread_r=spread_r,
                            session=_session_of(open_time, cfg),
                            exit_kind=exit_kind,
                            score=setup.score, strategy=setup.strategy,
                            amd=setup.amd, amd_level=setup.amd_level))
        if daily_trades is not None:
            daily_trades[open_time.date()] = daily_trades.get(open_time.date(), 0) + 1
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


def fetch_data(cfg: dict, pairs: list[str], days: int) -> dict:
    """Télécharge une seule fois l'historique de toutes les paires."""
    env = load_env()
    client = MT5Client(env)
    client.connect()
    end = datetime.now()
    start = end - timedelta(days=days)
    data: dict = {}
    try:
        for pair in pairs:
            log.info("Chargement de l'historique %s (%d jours)...", pair, days)
            try:
                pip = client.pip_size(pair)
            except Exception:
                pip = pip_size_fallback(pair, cfg)
            try:
                data[pair] = {
                    "pip": pip,
                    "htf": client.get_rates_range(pair, cfg["timeframes"]["htf"],
                                                  start - timedelta(days=30), end),
                    "ltf": client.get_rates_range(pair, cfg["timeframes"]["ltf"],
                                                  start, end),
                    "d1": client.get_rates_range(pair, "D1",
                                                 start - timedelta(days=200), end),
                }
            except Exception as exc:  # noqa: BLE001 — symbole absent chez le broker
                log.warning("%s ignoré (indisponible chez le broker ?) : %s",
                            pair, exc)
    finally:
        client.shutdown()
    return data


def simulate_all(cfg: dict, data: dict) -> pd.DataFrame:
    """Simule chaque paire sans quota, puis applique la règle
    max_trades_per_day CHRONOLOGIQUEMENT toutes paires confondues — le
    premier setup du jour prend la place, comme en live."""
    all_trades: list[Trade] = []
    for n, (pair, d) in enumerate(data.items(), start=1):
        log.info("Simulation %s (%d/%d)...", pair, n, len(data))
        pair_trades = simulate_pair(pair, d["htf"], d["ltf"], cfg,
                                    d["pip"], daily_trades=None, d1=d["d1"])
        log.info("%s : %d signal(aux) avant quota journalier", pair, len(pair_trades))
        all_trades.extend(pair_trades)
    df = pd.DataFrame([t.__dict__ for t in all_trades])
    if df.empty:
        return df
    df = df.sort_values("open_time").reset_index(drop=True)
    max_per_day = cfg["backtest"]["max_trades_per_day"]
    if max_per_day:
        counts: dict = {}
        keep = []
        for idx, row in df.iterrows():
            day = row["open_time"].date()
            if counts.get(day, 0) < max_per_day:
                keep.append(idx)
                counts[day] = counts.get(day, 0) + 1
        df = df.loc[keep].reset_index(drop=True)
    return df


def run_backtest(cfg: dict, pairs: list[str], days: int) -> pd.DataFrame:
    return simulate_all(cfg, fetch_data(cfg, pairs, days))


# ---------------------------------------------------------------------------
# Mode --compare : mesurer l'effet de chaque option isolément
# ---------------------------------------------------------------------------

def _cfg_with(cfg: dict, overrides: dict) -> dict:
    """Copie profonde de la config avec des remplacements {(section, clé): valeur}."""
    import copy
    out = copy.deepcopy(cfg)
    for (section, key), value in overrides.items():
        out[section][key] = value
    return out


_ALL_OFF = {
    ("strategy", "require_rejection_candle"): False,
    ("strategy", "require_d1_alignment"): False,
    ("strategy", "entry_at_zone_edge"): False,
    ("strategy", "min_sweep_depth_pips"): 0.0,
    ("calendar", "skip_dates"): [],
    ("calendar", "skip_friday_after"): "",
}

_VARIANTS: list[tuple[str, dict]] = [
    ("config actuelle (tout activé)", {}),
    ("sans bougie de rejet", {("strategy", "require_rejection_candle"): False}),
    ("sans alignement D1", {("strategy", "require_d1_alignment"): False}),
    ("sans ordre limite (entrée marché)", {("strategy", "entry_at_zone_edge"): False}),
    ("sans profondeur de sweep min", {("strategy", "min_sweep_depth_pips"): 0.0}),
    ("sans calendrier", {("calendar", "skip_dates"): [],
                         ("calendar", "skip_friday_after"): ""}),
    ("sans breakeven", {("exits", "breakeven_after_r"): 0}),
    ("sans sortie au temps", {("exits", "max_holding_bars"): 0}),
    ("aucun nouveau filtre (base)", dict(_ALL_OFF)),
]

# Paires épargnées par la variante "majors + crosses JPY" (hypothèse : les
# crosses CHF/CAD/AUD ont des spreads trop lourds pour la stratégie)
_JPY_MAJORS = {"EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD",
               "NZDUSD", "EURJPY", "GBPJPY", "AUDJPY", "CHFJPY", "EURGBP"}

# Hypothèses issues du rapport 365j/21 paires (sorties, zones, SL, paires)
_SWEEP_VARIANTS: list[tuple[str, dict, set | None]] = [
    ("config actuelle", {}, None),
    ("SL buffer 0.20%", {("sweep_bos", "sl_buffer_pct"): 0.20}, None),
    ("SL buffer 0.30%", {("sweep_bos", "sl_buffer_pct"): 0.30}, None),
    ("entrées sans OB", {("sweep_bos", "entry_zone_kinds"):
                         ["FVG", "IFVG", "Breaker"]}, None),
    ("sortie au temps à 48h", {("exits", "max_holding_bars"): 192}, None),
    ("sans breakeven", {("exits", "breakeven_after_r"): 0}, None),
    ("sans prise partielle", {("exits", "partial_at_r"): 0}, None),
    ("majors + crosses JPY", {}, _JPY_MAJORS),
]


def run_compare_strategies(cfg: dict, pairs: list[str], days: int) -> pd.DataFrame:
    """Backtest chaque stratégie (et les paliers de score pour sweep_bos) sur
    la même période. ⚠️ Exploration in-sample, risque d'overfitting."""
    import copy
    data = fetch_data(cfg, pairs, days)
    variants: list[tuple[str, dict]] = []
    for name in STRATEGIES:
        base = copy.deepcopy(cfg)
        base["strategy_name"] = name
        variants.append((name, base))
        if name == "sweep_bos":
            for min_score in (2, 3):
                v = copy.deepcopy(base)
                v["sweep_bos"]["min_score"] = min_score
                variants.append((f"{name} (score >= {min_score})", v))
    rows = []
    for label, vcfg in variants:
        df = simulate_all(vcfg, data)
        s = compute_stats(df)
        rows.append({
            "strategie": label,
            "trades": s.get("trades", 0),
            "win_rate_%": round(s["win_rate"] * 100, 1) if s.get("trades") else None,
            "r_moyen": round(s["avg_r"], 2) if s.get("trades") else None,
            "total_r": round(s["total_r"], 1) if s.get("trades") else None,
            "profit_factor": round(s["profit_factor"], 2)
            if s.get("trades") and s["profit_factor"] != float("inf") else None,
            "drawdown_r": round(s["max_drawdown_r"], 1) if s.get("trades") else None,
        })
        log.info("%-28s : %3d trades | WR %s%% | total %s R | PF %s | DD %s R",
                 label, rows[-1]["trades"], rows[-1]["win_rate_%"],
                 rows[-1]["total_r"], rows[-1]["profit_factor"],
                 rows[-1]["drawdown_r"])
    return pd.DataFrame(rows)


def _fmt_seg(s: dict) -> str:
    if not s.get("trades"):
        return "0 trades"
    pf = s["profit_factor"]
    pf_txt = "∞" if pf == float("inf") else f"{pf:.2f}"
    return (f"{s['trades']:3d} trades | WR {s['win_rate']*100:4.1f}% | "
            f"total {s['total_r']:+6.1f} R | PF {pf_txt}")


def run_compare(cfg: dict, pairs: list[str], days: int,
                split: float = 0.0) -> pd.DataFrame:
    """Rejoue la même période avec des variantes ciblées.

    `split` (ex. 0.75) découpe la période : les variantes sont jugées sur les
    premiers 75% (in-sample) ET vérifiées sur les derniers 25% jamais utilisés
    pour choisir (out-of-sample). Une variante qui ne tient que sur
    l'in-sample est de l'overfitting — à jeter.
    """
    data = fetch_data(cfg, pairs, days)
    variants = _SWEEP_VARIANTS if cfg.get("strategy_name") == "sweep_bos" \
        else [(n, o, None) for n, o in _VARIANTS]
    cutoff = datetime.now() - timedelta(days=days * (1 - split)) if split else None

    rows = []
    for name, overrides, pair_filter in variants:
        subset = {p: d for p, d in data.items()
                  if pair_filter is None or p in pair_filter}
        df = simulate_all(_cfg_with(cfg, overrides), subset)
        if cutoff is not None and not df.empty:
            df_is = df[df["open_time"] < cutoff]
            df_oos = df[df["open_time"] >= cutoff]
            s_is, s_oos = compute_stats(df_is), compute_stats(df_oos)
            log.info("%-24s | IS : %s || OOS : %s",
                     name, _fmt_seg(s_is), _fmt_seg(s_oos))
            rows.append({"variante": name,
                         "is_trades": s_is.get("trades", 0),
                         "is_total_r": round(s_is.get("total_r", 0), 1) if s_is.get("trades") else None,
                         "is_pf": round(s_is["profit_factor"], 2)
                         if s_is.get("trades") and s_is["profit_factor"] != float("inf") else None,
                         "oos_trades": s_oos.get("trades", 0),
                         "oos_total_r": round(s_oos.get("total_r", 0), 1) if s_oos.get("trades") else None,
                         "oos_pf": round(s_oos["profit_factor"], 2)
                         if s_oos.get("trades") and s_oos["profit_factor"] != float("inf") else None})
        else:
            s = compute_stats(df)
            log.info("%-24s : %s", name, _fmt_seg(s))
            rows.append({"variante": name,
                         "trades": s.get("trades", 0),
                         "total_r": round(s.get("total_r", 0), 1) if s.get("trades") else None,
                         "profit_factor": round(s["profit_factor"], 2)
                         if s.get("trades") and s["profit_factor"] != float("inf") else None})
    return pd.DataFrame(rows)


def main() -> None:
    cfg = load_config()
    setup_logging(cfg["paths"]["logs"], "backtest")
    log_warnings_banner(log)

    parser = argparse.ArgumentParser(description="Backtest SMC/AMD sur historique MT5")
    parser.add_argument("--days", type=int, default=cfg["backtest"]["days"])
    parser.add_argument("--pairs", type=str, default=",".join(cfg["pairs"]))
    parser.add_argument("--compare", action="store_true",
                        help="rejoue la période en retirant chaque option une à "
                             "une pour mesurer son effet (diagnostic in-sample)")
    parser.add_argument("--compare-strategies", action="store_true",
                        help="backtest chaque stratégie sur la même période")
    parser.add_argument("--strategy", type=str, default=None,
                        help=f"stratégie à utiliser ({', '.join(STRATEGIES)}) ; "
                             "défaut : strategy_name du config.yaml")
    parser.add_argument("--split", type=float, default=0.0,
                        help="fraction in-sample (ex. 0.75) : les stats sont "
                             "aussi calculées sur la fin de période jamais "
                             "utilisée pour choisir (out-of-sample)")
    args = parser.parse_args()
    pairs = [p.strip().upper() for p in args.pairs.split(",") if p.strip()]
    if args.strategy:
        cfg["strategy_name"] = args.strategy
        get_strategy(args.strategy)  # valide le nom tout de suite

    if args.compare_strategies:
        result = run_compare_strategies(cfg, pairs, args.days)
        out_dir = Path(cfg["paths"]["reports"])
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"strategies_{datetime.now():%Y%m%d_%H%M%S}.csv"
        result.to_csv(path, index=False)
        log.info("Comparatif stratégies écrit : %s", path)
        log.warning("⚠️ Comparaison in-sample — valider en démo avant d'y croire.")
        return

    if args.compare:
        result = run_compare(cfg, pairs, args.days, split=args.split)
        out_dir = Path(cfg["paths"]["reports"])
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"compare_{datetime.now():%Y%m%d_%H%M%S}.csv"
        result.to_csv(path, index=False)
        log.info("Comparatif écrit : %s", path)
        log.warning("⚠️ Comparer des variantes sur le même passé = exploration "
                    "in-sample. Ne garder que ce qui a une justification, et "
                    "valider en démo avant d'y croire.")
        return

    data = fetch_data(cfg, pairs, args.days)
    trades_df = simulate_all(cfg, data)

    out_dir = Path(cfg["paths"]["reports"])
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    csv_path = out_dir / f"trades_{stamp}.csv"
    trades_df.to_csv(csv_path, index=False)

    stats = compute_stats(trades_df)
    per_pair = {p: compute_stats(trades_df[trades_df["pair"] == p])
                for p in pairs} if not trades_df.empty else {}
    html = render_report(trades_df, stats, per_pair, args.days)
    html_path = out_dir / f"report_{stamp}.html"
    html_path.write_text(html, encoding="utf-8")
    # copie stable pour le dashboard
    (out_dir / "latest.html").write_text(html, encoding="utf-8")

    # Dossier Backtest/ : images des trades + courbe d'équité + rapport
    if cfg["backtest"].get("charts", True):
        try:
            from smc.charts import render_backtest_folder
            render_backtest_folder(trades_df, data, html,
                                   Path(cfg["paths"]["reports"]).parent, stamp)
        except ImportError:
            log.warning("matplotlib manquant — images non générées "
                        "(pip install matplotlib)")

    log.info("Terminé : %d trades — rapport : %s (CSV : %s)",
             stats.get("trades", 0), html_path, csv_path)
    if stats.get("trades"):
        log.info("Win rate %.1f%% | R moyen %.2f | Profit factor %.2f | Total %.1fR",
                 stats["win_rate"] * 100, stats["avg_r"],
                 stats["profit_factor"], stats["total_r"])


if __name__ == "__main__":
    main()
