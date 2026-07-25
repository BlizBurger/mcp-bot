"""Validation walk-forward multi-fenêtres branchée sur le vrai moteur.

Remplace le split unique 75/25 par : plusieurs fenêtres train→test glissantes,
un intervalle de confiance bootstrap sur l'espérance OOS, une vérification de
consistance inter-fenêtres, et un bloc "vault" scellé jamais utilisé pour
choisir quoi que ce soit.

Usage :
  python -m smc.backtest_walkforward --train-months 6 --test-months 2 \
      --step-months 2 --vault-months 3 --years 4

Le vault n'est évalué QUE si tu passes --vault-strategy NOM (une seule fois,
sur une stratégie déjà choisie d'après le comparatif walk-forward).
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from smc.backtest import simulate_all
from smc.config import load_config, load_env, pip_size_fallback
from smc.logging_setup import log_warnings_banner, setup_logging
from smc.mt5_client import MT5Client
from smc.strategies import STRATEGIES
from smc.walkforward import (
    TradeResult, WindowResult, build_windows, bootstrap_expectancy_ci,
    summarize_walkforward,
)

log = logging.getLogger("smc.walkforward")

# Warmup (chauffe) à préfixer avant chaque fenêtre pour que les indicateurs
# et la structure aient assez d'historique. Généreux (jours calendaires).
_WARMUP = {"ltf": timedelta(days=20), "htf": timedelta(days=60),
           "d1": timedelta(days=280)}

# Grilles de paramètres — testées UNIQUEMENT sur le train de chaque fenêtre.
# Clés = (section_config, clé). Petites grilles exprès (comparaisons multiples).
_PARAM_GRIDS: dict[str, list[dict] | None] = {
    "sweep_bos": [
        {("sweep_bos", "min_wick_ratio"): 0.5, ("sweep_bos", "sl_buffer_pct"): 0.15,
         ("sweep_bos", "min_rr"): 1.5},
        {("sweep_bos", "min_wick_ratio"): 0.6, ("sweep_bos", "sl_buffer_pct"): 0.20,
         ("sweep_bos", "min_rr"): 2.0},
        {("sweep_bos", "min_wick_ratio"): 0.6, ("sweep_bos", "sl_buffer_pct"): 0.25,
         ("sweep_bos", "min_rr"): 2.5},
    ],
    "donchian": [
        {("donchian", "channel"): 20, ("donchian", "atr_sl_mult"): 1.5,
         ("donchian", "risk_reward"): 2.0},
        {("donchian", "channel"): 55, ("donchian", "atr_sl_mult"): 2.0,
         ("donchian", "risk_reward"): 2.0},
    ],
    "ema_rsi": None, "bollinger": None, "ema_cross": None, "amd_asian": None,
}


# --------------------------------------------------------------------------
# Chargement des données (une seule fois sur toute la plage)
# --------------------------------------------------------------------------

def fetch_full(cfg: dict, pairs: list[str], years: int
               ) -> tuple[dict, datetime, datetime]:
    """Charge H4/M15/D1 de toutes les paires par NOMBRE DE BOUGIES depuis
    maintenant (copy_rates_from_pos, fiable) plutôt que par plage de dates
    (copy_rates_range renvoie "Invalid params" si on demande plus loin que
    l'historique disponible). Retourne (données, plage réelle)."""
    # bougies/jour approx (forex ~24h/5j) — on sur-demande, MT5 rend le dispo
    ltf_count = min(int(years * 365 * 100) + 500, 250_000)  # M15
    htf_count = int(years * 365 * 7) + 300                  # H4
    d1_count = int(years * 365) + 400                       # D1
    env = load_env()
    client = MT5Client(env)
    client.connect()
    full: dict = {}
    real_start, real_end = None, None
    try:
        for pair in pairs:
            log.info("Chargement %s (~%d ans)...", pair, years)
            try:
                pip = client.pip_size(pair)
            except Exception:
                pip = pip_size_fallback(pair, cfg)
            try:
                htf = client.get_rates_max(pair, cfg["timeframes"]["htf"], htf_count)
                ltf = client.get_rates_max(pair, cfg["timeframes"]["ltf"], ltf_count)
                d1 = client.get_rates_max(pair, "D1", d1_count)
            except Exception as exc:  # noqa: BLE001 — paire absente/insuffisante
                log.warning("%s ignoré : %s", pair, exc)
                continue
            full[pair] = {"pip": pip, "htf": htf, "ltf": ltf, "d1": d1}
            first, last = ltf["time"].iloc[0], ltf["time"].iloc[-1]
            real_start = first if real_start is None else min(real_start, first)
            real_end = last if real_end is None else max(real_end, last)
            log.info("  %s : %d bougies M15 (%s → %s)", pair, len(ltf),
                     first.date(), last.date())
    finally:
        client.shutdown()
    if real_start is None:
        real_start = real_end = datetime.now()
    return full, real_start, real_end


def make_data_loader(full: dict):
    """Renvoie loader(start, end) qui découpe les données pré-chargées, en
    préfixant un warmup pour les indicateurs. Le résultat porte les bornes de
    la fenêtre pour que la stratégie ne compte que les trades ouverts dedans."""
    def loader(start: datetime, end: datetime) -> dict:
        sliced = {}
        for pair, d in full.items():
            sub = {}
            for tf, warm in (("htf", _WARMUP["htf"]), ("ltf", _WARMUP["ltf"]),
                             ("d1", _WARMUP["d1"])):
                df = d[tf]
                m = (df["time"] >= start - warm) & (df["time"] <= end)
                sub[tf] = df[m].reset_index(drop=True)
            if len(sub["ltf"]) < 60 or len(sub["htf"]) < 30:
                continue
            sliced[pair] = {"pip": d["pip"], **sub}
        return {"pairs": sliced, "start": start, "end": end}
    return loader


def make_strategy_fn(name: str, base_cfg: dict):
    """Renvoie strategy_fn(slice, params) -> list[TradeResult], qui rejoue la
    vraie stratégie via simulate_all et ne garde que les trades OUVERTS dans la
    fenêtre (pas ceux issus du warmup)."""
    def fn(slice_obj: dict, params: dict) -> list[TradeResult]:
        if not slice_obj["pairs"]:
            return []
        vcfg = copy.deepcopy(base_cfg)
        vcfg["strategy_name"] = name
        for (section, key), value in (params or {}).items():
            vcfg.setdefault(section, {})[key] = value
        df = simulate_all(vcfg, slice_obj["pairs"])
        if df.empty:
            return []
        s, e = slice_obj["start"], slice_obj["end"]
        df = df[(df["open_time"] >= s) & (df["open_time"] <= e)]
        return [TradeResult(entry_time=r.open_time, exit_time=r.close_time,
                            r_multiple=float(r.result_r), symbol=r.pair,
                            strategy=name)
                for r in df.itertuples()]
    return fn


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def simulate_full(cfg: dict, full: dict, name: str, params: dict) -> list[TradeResult]:
    """Simule une stratégie UNE SEULE FOIS sur tout l'historique et renvoie
    TOUS ses trades (répartis ensuite dans les fenêtres par date). C'est ce qui
    rend le walk-forward praticable : au lieu de re-simuler par fenêtre, on
    simule une fois puis on découpe par timestamp."""
    vcfg = copy.deepcopy(cfg)
    vcfg["strategy_name"] = name
    for (section, key), value in (params or {}).items():
        vcfg.setdefault(section, {})[key] = value
    df = simulate_all(vcfg, full)
    if df.empty:
        return []
    return [TradeResult(entry_time=r.open_time, exit_time=r.close_time,
                        r_multiple=float(r.result_r), symbol=r.pair, strategy=name)
            for r in df.itertuples()]


def _in(trades: list[TradeResult], a, b) -> list[TradeResult]:
    return [t for t in trades if a <= t.entry_time <= b]


def walk_forward_from_trades(name: str, combos: list[tuple[dict, list[TradeResult]]],
                             windows: list) -> list:
    """Walk-forward par découpage temporel des trades pré-simulés. Si plusieurs
    combos de paramètres, le meilleur est choisi sur le TRAIN de chaque fenêtre
    (jamais sur le test), puis évalué sur le test."""
    import statistics
    results = []
    for w in windows:
        best_idx = 0
        if len(combos) > 1:
            best_score = float("-inf")
            for i, (_, trades) in enumerate(combos):
                tr = _in(trades, w.train_start, w.train_end)
                if not tr:
                    continue
                score = statistics.mean(t.r_multiple for t in tr)
                if score > best_score:
                    best_score, best_idx = score, i
        test_trades = _in(combos[best_idx][1], w.test_start, w.test_end)
        results.append(WindowResult(window=w, trades=test_trades,
                                    params_used=combos[best_idx][0]))
    return results


def run(cfg: dict, pairs: list[str], years: int, train_m: int, test_m: int,
        step_m: int, vault_m: int, strat_names: list[str],
        vault_strategy: str | None) -> dict:
    full, real_start, real_end = fetch_full(cfg, pairs, years)
    if not full:
        return {"erreur": "aucune donnée chargée depuis MT5"}
    real_start = pd.Timestamp(real_start).to_pydatetime()
    real_end = pd.Timestamp(real_end).to_pydatetime()

    windows, (vault_start, vault_end) = build_windows(
        real_start, real_end, train_months=train_m, test_months=test_m,
        step_months=step_m, vault_months=vault_m)
    log.info("Plage réelle : %s → %s | %d fenêtres | vault %s → %s",
             real_start.date(), real_end.date(), len(windows),
             vault_start.date(), vault_end.date())
    if len(windows) < 8:
        log.warning("⚠️ Seulement %d fenêtres : historique un peu court pour une "
                    "conclusion solide (viser 10-15).", len(windows))

    names = [n for n in strat_names if n in STRATEGIES]
    # Pré-simulation : chaque stratégie (et chaque combo de sa grille) UNE fois
    combos_by_strat: dict[str, list] = {}
    for name in names:
        grid = _PARAM_GRIDS.get(name) or [None]
        combos = []
        for gi, params in enumerate(grid):
            log.info("Simulation complète %s (combo %d/%d) sur %s → %s...",
                     name, gi + 1, len(grid), real_start.date(), real_end.date())
            combos.append((params or {}, simulate_full(cfg, full, name, params)))
        combos_by_strat[name] = combos

    # Walk-forward (découpage temporel) + résumé par stratégie
    summaries = {}
    for name, combos in combos_by_strat.items():
        wr = walk_forward_from_trades(name, combos, windows)
        summaries[name] = summarize_walkforward(wr, name)

    proven = [n for n, s in summaries.items() if s.get("edge_statistiquement_prouve")]
    warning = None
    if len(names) > 5 and len(proven) <= 1:
        warning = (f"⚠️ {len(names)} stratégies comparées : avec autant de tests, "
                   "l'une peut ressortir par pur hasard. Seul le VAULT tranche.")

    report = {
        "genere_le": datetime.now().isoformat(),
        "plage_donnees": f"{real_start.date()} → {real_end.date()}",
        "n_fenetres": len(windows),
        "decoupage": f"train {train_m}m / test {test_m}m / step {step_m}m / vault {vault_m}m",
        "vault": f"{vault_start.date()} → {vault_end.date()} (scellé)",
        "comparatif": summaries,
        "strategies_avec_edge_prouve": proven,
        "avertissement_comparaisons_multiples": warning,
    }

    # Vault : UNIQUEMENT si demandé, sur une seule stratégie (combo 0 = params
    # de base ; le walk-forward sert à décider, pas le vault)
    if vault_strategy:
        if vault_strategy not in combos_by_strat:
            report["vault_result"] = {"erreur": f"{vault_strategy} non testée"}
        else:
            log.warning("Évaluation VAULT sur %s — une seule fois, verdict final.",
                        vault_strategy)
            vtr = _in(combos_by_strat[vault_strategy][0][1], vault_start, vault_end)
            if not vtr:
                report["vault_result"] = {"strategy": vault_strategy,
                                          "verdict": "Aucun trade sur le vault"}
            else:
                mean, lo, hi = bootstrap_expectancy_ci(vtr)
                wr = sum(1 for t in vtr if t.r_multiple > 0) / len(vtr) * 100
                report["vault_result"] = {
                    "strategy": vault_strategy, "n_trades_vault": len(vtr),
                    "win_rate_vault": round(wr, 1),
                    "expectancy_vault": round(mean, 3),
                    "IC_95%": (round(lo, 3) if lo == lo else "N/A",
                               round(hi, 3) if hi == hi else "N/A"),
                    "verdict": ("VALIDÉ sur données jamais vues — passable en démo réelle"
                                if lo == lo and lo > 0 else
                                "NON VALIDÉ — l'edge ne se confirme pas sur le vault.")}
    return report


def _print_report(report: dict) -> None:
    print("\n===== COMPARATIF WALK-FORWARD (multi-fenêtres, out-of-sample) =====")
    print(f"Plage : {report.get('plage_donnees')} | {report.get('n_fenetres')} "
          f"fenêtres | {report.get('decoupage')}")
    print(f"Vault scellé : {report.get('vault')}\n")
    for name, s in report.get("comparatif", {}).items():
        print(f"--- {name} ---")
        for k, v in s.items():
            print(f"  {k}: {v}")
        print()
    if report.get("avertissement_comparaisons_multiples"):
        print(report["avertissement_comparaisons_multiples"], "\n")
    if "vault_result" in report:
        print("===== VAULT (verdict final, jamais touché avant) =====")
        for k, v in report["vault_result"].items():
            print(f"  {k}: {v}")


def main() -> None:
    cfg = load_config()
    setup_logging(cfg["paths"]["logs"], "walkforward")
    log_warnings_banner(log)

    p = argparse.ArgumentParser(description="Validation walk-forward multi-fenêtres")
    p.add_argument("--walkforward", action="store_true", help="(implicite)")
    p.add_argument("--years", type=int, default=4, help="profondeur d'historique visée")
    p.add_argument("--train-months", type=int, default=6)
    p.add_argument("--test-months", type=int, default=2)
    p.add_argument("--step-months", type=int, default=2)
    p.add_argument("--vault-months", type=int, default=3)
    p.add_argument("--pairs", type=str, default=",".join(cfg["pairs"]))
    p.add_argument("--strategies", type=str,
                   default="sweep_bos,donchian,ema_rsi,bollinger,ema_cross,amd_asian")
    p.add_argument("--vault-strategy", type=str, default=None,
                   help="évalue le vault sur CETTE stratégie (une seule fois)")
    args = p.parse_args()

    pairs = [x.strip().upper() for x in args.pairs.split(",") if x.strip()]
    strats = [x.strip() for x in args.strategies.split(",") if x.strip()]

    report = run(cfg, pairs, args.years, args.train_months, args.test_months,
                 args.step_months, args.vault_months, strats, args.vault_strategy)
    _print_report(report)

    out = Path(cfg["paths"]["reports"]).parent / "Backtest" / \
        f"walkforward_{datetime.now():%Y%m%d_%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "rapport.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    log.info("Rapport walk-forward écrit : %s", out / "rapport.json")
    log.warning("⚠️ Ne retenir une stratégie que si edge_statistiquement_prouve "
                "== True ET consistance positive. Vault = verdict final unique.")


if __name__ == "__main__":
    main()
